## Context

构建一个面向财务年报/财报场景的 RAG 智能问答系统 MVP。用户上传 PDF/DOCX/TXT 文档，系统自动解析、分块、向量化存储，用户通过自然语言问答获取答案并附引用来源。

现有 `old/` 目录包含一个功能相似的旧系统，但存在多处架构缺陷：import-time 全局初始化导致启动崩溃、无重试连接、bare except 吞异常、全局 SESSION_ID 多用户串话、模块耦合严重。MVP 在 `src/` 下完全重写，复用 old 的模块划分思路但修复所有已知问题。

部署环境为单机 Docker Compose（MySQL + Redis + App），Python 后端 + Gradio 前端 + DashScope 外部 API。

## Goals / Non-Goals

**Goals:**
- 实现可演示的端到端 RAG 问答流程：上传 → 解析 → 分块 → 向量化 → 检索 → 生成 → 引用
- Gradio UI 包含知识库管理、文件上传、对话交互、引用展示
- Docker Compose 一键部署，MySQL/Redis/ChromaDB 数据持久化
- 文档处理支持 PDF/DOCX/TXT，含扫描件检测和编码检测
- 异步上传处理 + 进度提示，首次使用空状态引导
- 6 个 Iteration 串行开发，业余时间 ~6.5 天

**Non-Goals:**
- 扫描件 OCR 识别（Phase 2 MinerU）
- BM25 混合检索（Phase 2）
- 意图路由/闲聊分类（Phase 2）
- 多知识库跨库对比（Phase 2）
- UI 分块质量看板（Phase 2）
- 生产级可观测性/限流/熔断（Phase 3）
- 单元测试覆盖率 ≥ 70%（Phase 2 Harness）

## Decisions

### 1. 项目结构

```
├── src/
│   ├── __init__.py
│   ├── app.py                    # Gradio 入口
│   ├── config.py                 # 全局配置（全部从环境变量读取）
│   ├── models.py                 # 模型工厂（LLM/Embedding/Rerank）
│   ├── document_loader.py        # 兼容入口，调用 DocRouter
│   ├── parsers/
│   │   ├── __init__.py
│   │   ├── base.py               # ChunkData, ParseResult, BaseParser 接口
│   │   ├── router.py             # DocRouter 路由逻辑
│   │   ├── pymupdf_parser.py     # PyMuPDF 解析器
│   │   ├── docx_parser.py        # python-docx 解析器
│   │   └── txt_parser.py         # 纯文本解析器（含编码检测）
│   ├── vector_store.py           # ChromaDB 封装（PersistentClient 内嵌）
│   ├── mysql_db.py               # MySQL CRUD（连接池 + 重试）
│   ├── rag_chain.py              # RAG 核心链
│   ├── chat_manager.py           # 会话管理（Redis + InMemory 降级）
│   ├── cli/
│   │   ├── check_chunks.py       # 分块质量 CLI 报告
│   │   └── check_retrieval.py    # 检索质量 CLI 测试
│   └── eval_ragas.py             # RAGAS 评估
├── old/                          # 参考代码，保留不动
├── test_docs/                    # 测试文档
├── docs/                         # 文档
├── deploy/
│   └── mysql/
│       └── init/
│           └── 001_schema.sql    # DDL
├── docker-compose.yml
├── Dockerfile
├── .env.template
├── .gitignore
├── pyproject.toml
└── README.md
```

### 2. 模块初始化策略（修复 P0 问题）

**决策**：所有模块采用**类实例化 + 显式 init 方法**，模块级不创建实例。

```python
# ✅ 正确做法
class MySQLDB:
    async def init(self):
        # 延迟初始化，支持重试
        ...

# 使用处
mysql = MySQLDB()  # 只创建对象，不连接
await mysql.init()     # 显式初始化
```

**原因**：避免 import-time 连接数据库/Redis 导致启动崩溃，支持 container 依赖未就绪时重试。

### 3. 重试策略（所有外部调用）

**决策**：对 MySQL 连接、Redis 连接、DashScope API 调用统一加 retry + 指数退避。

| 调用 | 重试次数 | 初始间隔 | 退避 |
|------|---------|---------|------|
| MySQL 连接 | 5 次 | 2s | 2x |
| Redis 连接 | 3 次 | 1s | 2x |
| DashScope Embedding/LLM | 3 次 | 1s | 2x |

### 4. 文档处理管线

**决策**：Parser 接口 + Router 模式，MVP 只实现 PyMuPDF/python-docx/TXT。

```
Upload → DocRouter.resolve()
  → PDF: PyMuPDFParser.parse()
    → 扫描件检测（文本提取率 < 200字/页 → 报错）
    → 按页提取文本 + page.find_tables() 表格数据
  → DOCX: DocxParser.parse() → python-docx 逐段
  → TXT: TxtParser.parse() → chardet 编码检测 → UTF-8/GBK 回退
  → RecursiveCharacterTextSplitter(chunk_size=TBD, overlap=64)
  → 表格完整性标记（check_chunks 检测用）
  → ParseResult → ChromaDB 入库 + MySQL metadata
```

### 5. 异步上传 + 进度提示

**决策**：文件上传后使用 Python `threading` 在后台线程处理，Gradio 前端轮询状态。

```python
# 伪代码
upload_status = {}  # {task_id: {"status": "processing", "progress": 0}}

def upload_file(kb_name, file):
    task_id = str(uuid.uuid4())
    upload_status[task_id] = {"status": "processing", "progress": 0}
    thread = threading.Thread(target=process_file, args=(task_id, kb_name, file))
    thread.start()
    return task_id

def get_upload_progress(task_id):
    return upload_status.get(task_id, {"status": "unknown"})
```

### 6. Redis 降级策略

**决策**：Redis 不可用时报错但不影响主流程，退化为 InMemory 对话历史。

```python
class ChatManager:
    def __init__(self):
        self.memory = {}  # InMemory fallback
        self.redis = None
        try:
            self.redis = redis.from_url(REDIS_URL)
        except Exception:
            logger.warning("Redis unavailable, using in-memory fallback")
```

### 7. Prompt 约束

**决策**：System prompt 增加四条硬约束，提示词统一放在 `src/config/prompts.py` 中管理。

```
你是一个专业金融文档分析师。请严格遵循以下规则：

1. 仅根据提供的文档内容回答，不要计算文档中没有直接给出的比率或汇总数据
2. 回答中必须标注数据对应的年份/报告期
3. 如果文档中找不到相关信息，明确说明"未在文档中找到相关数据"
4. 回答语言与用户提问语言一致
```

**实现偏离说明（与初始设计相比）：**
- 角色从"专业财务分析师"调整为"专业金融文档分析师"——更聚焦"文档分析"而非"财务分析"，避免 LLM 产生不应有的推算行为
- 新增第 4 条"回答语言与用户提问语言一致"——防止多语言场景下模型用英文回答中文问题
- 第 1 条措辞从"除非检索结果中直接包含该数据"改为"不要计算文档中没有直接给出的比率或汇总数据"——从例外句式改为绝对禁止，更清晰

### 8. Chunk Size 调优策略

**决策**：MVP 以 chunk_size=512 为起点，通过 RAGAS benchmark 对比 512/768/1024 三组数据，以 Recall@5 为指标确定最终值。

### 9. 引用格式

**决策**：答案尾部追加 markdown 引用块。

```
---

**引用来源：**
1. **年报2023.pdf** → 第12页
   > 相关原文片段（前200字...）
2. **年报2023.pdf** → 第15页
   > 相关原文片段（前200字...）
```

### 10. 知识库 Collection 命名

**决策**：ChromaDB collection 命名使用 `kb_{uuid4_hex}` 格式，避免特殊字符冲突，方便调试识别。

## Risks / Trade-offs

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| DashScope API 延迟不稳定 | 中 | 高 | 前端流式输出遮掩延迟；retry + 指数退避 |
| 财报 PDF 格式复杂（扫描件、加密） | 中 | 中 | MVP 检测并明确提示"不支持"；Phase 2 MinerU |
| Embedding 维度高导致检索慢 | 低 | 中 | ChromaDB 本地检索延迟可控 |
| 幻觉率高于预期 | 中 | 高 | 严格的 system prompt + 引用约束 + RAGAS 评估把关 |
| Gradio 上传超时（大文件） | 中 | 中 | 异步处理 + 进度提示，避免请求超时 |
| TXT 编码检测误判 | 低 | 低 | chardet + UTF-8/GBK 回退，覆盖 95% 场景 |
| RAGAS 评估 bias (Qwen judge Qwen) | 中 | 中 | 优先使用基于 ground truth 的指标，记录 bias 风险 |
