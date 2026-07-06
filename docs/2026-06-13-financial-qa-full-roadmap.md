# 金融文档智能问答助手 — 全周期路线图

> 基于 RAG（Retrieval-Augmented Generation）的企业级财务文档问答系统。
> MVP 验证核心假设 → 增强补全能力 → 生产化工程。
> 本文档与 `openspec/changes/financial-qa-mvp/` 提案形成对照。
> 补充决策见 `openspec/changes/financial-qa-mvp/design.md`（技术设计）和 `openspec/changes/financial-qa-mvp/specs/`（规格）。

---

## 一、核心假设（验证目标）

| ID | 假设 | 验证方式 | 成功标准 |
|----|------|----------|----------|
| H1 | PDF/DOCX/TXT 文档可被可靠解析、分块、入库 | 上传测试集文档，检查分块质量 | 文档无遗漏，chunk 内容完整可读 |
| H2 | 语义检索能在财报场景下召回相关片段 | 构造 20+ QA 对，计算 Recall@K | Recall@5 ≥ 0.85 |
| H3 | Qwen-max + 引用机制能有效控制幻觉 | 人工评估 50 条回答，标注"幻觉率" | 幻觉率 < 5% |
| H4 | 分块策略对数字类问答友好（不切断关键数据） | 分块质量报告（chunk overlap, 表格完整性） | 无表格被切断，数字上下文完整 |
| H5 | 单机 Docker 部署下响应速度可接受 | 端到端测试 20 个问题记录耗时 | P95 响应 < 8s |

---

## 二、系统架构（三期共用）

```
┌──────────────────────────────────────────────────────────────────────┐
│                         docker-compose.yml                          │
│                                                                      │
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐  │
│  │  mysql:8.0       │  │  redis:7-alpine  │  │  chroma (内嵌)   │  │
│  │  端口: 3306      │  │  端口: 6379      │  │  → app容器内     │  │
│  │  数据卷: mysql_d  │  │  数据卷: redis_d  │  │  数据卷: chroma_ │  │
│  │  healthcheck ✅   │  │  healthcheck ✅   │  │  持久化          │  │
│  └────────┬─────────┘  └────────┬─────────┘  └────────┬─────────┘  │
│           │                     │                     │             │
│           └──────────┬──────────┴─────────────────────┘             │
│                      │                                              │
│  ┌───────────────────┴──────────────────────────────────────────┐   │
│  │  financial-qa-app (Python 3.11 + FastAPI + Nginx + Tailwind)                 │   │
│  │                                                              │   │
│  │  ┌────────────┐  ┌────────────┐  ┌────────────┐            │   │
│  │  │ Document   │  │ Vector     │  │ RAG Chain  │            │   │
│  │  │ Loader     │─▶│ Store      │─▶│            │            │   │
│  │  │ (解析分块)  │  │ (ChromaDB) │  │ (检索+生成) │            │   │
│  │  └────────────┘  └────────────┘  └─────┬──────┘            │   │
│  │                                        │                    │   │
│  │  ┌────────────┐  ┌────────────┐  ┌────┴──────┐            │   │
│  │  │ Chat       │  │ MySQL DB  │  │ Model     │            │   │
│  │  │ Manager    │◀─┤ (元数据)   │  │ Factory   │            │   │
│  │  │ (Redis)    │  └────────────┘  │ (LLM/Emed │            │   │
│  │  └────────────┘                  │ /Rerank)  │            │   │
│  │                                  └───────────┘            │   │
│  │                                                              │   │
│  │  端口: 8000 (FastAPI) / 80 (Nginx) / 3000 (Langfuse)          │   │
│  │  环境变量: DASHSCOPE_API_KEY, DB_URL, REDIS_URL, LANGFUSE_*    │   │
│  └──────────────────────────────────────────────────────────────┘   │
│                                                                      │
│  网络: app-network                                                   │
│  外部依赖: DashScope API (LLM, Embedding, Rerank)                   │
└──────────────────────────────────────────────────────────────────────┘
```

### 核心设计原则

| 原则 | 说明 |
|------|------|
| **最小外部依赖** | 仅 DashScope API 是外部调用，其余全部容器化 |
| **一键部署** | `docker-compose up --build` 即完成启动 |
| **数据持久化** | MySQL / Redis / ChromaDB 均挂载命名卷 |
| **优雅启动** | 容器依赖 healthcheck，服务按序就绪 |
| **模块解耦** | 每个模块类实例化，不使用全局变量，Phase 2/3 可独立升级 |
| **日志规范** | loguru 统一日志，Docker 输出 stdout + 轮转文件 |
| **重试 + 指数退避** | 所有外部调用（MySQL/Redis/DashScope）统一 retry 策略，初始间隔 1-2s，2x 退避 |
| **前端流式遮掩延迟** | LLM 流式输出 + 异步上传进度提示，减少用户感知等待 |

---

## 三、数据模型

### MySQL Schema

```sql
CREATE TABLE knowledge_base (
    id          VARCHAR(36)  PRIMARY KEY,
    name        VARCHAR(255) NOT NULL UNIQUE,
    description TEXT,
    created_at  DATETIME     DEFAULT CURRENT_TIMESTAMP,
    updated_at  DATETIME     DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

CREATE TABLE document (
    id          VARCHAR(36)  PRIMARY KEY,
    kb_id       VARCHAR(36)  NOT NULL,
    filename    VARCHAR(255) NOT NULL,
    file_type   VARCHAR(10)  NOT NULL,         -- pdf / docx / txt
    file_size   INT          NOT NULL DEFAULT 0, -- bytes
    chunk_count INT          NOT NULL DEFAULT 0,
    status      VARCHAR(20)  NOT NULL DEFAULT 'pending',  -- pending/processing/ready/failed
    error_msg   TEXT,
    created_at  DATETIME     DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (kb_id) REFERENCES knowledge_base(id) ON DELETE CASCADE
);

CREATE TABLE conversation_history (
    id          INT          AUTO_INCREMENT PRIMARY KEY,
    session_id  VARCHAR(36)  NOT NULL,
    kb_id       VARCHAR(36)  NOT NULL,
    role        ENUM('user','assistant') NOT NULL,
    content     TEXT         NOT NULL,
    sources     JSON,                          -- 引用的文档片段
    created_at  DATETIME     DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_session (session_id, created_at)
);
```

### ChromaDB Schema

```
Collection: kb_{uuid}
  ┊─ Document: chunk_text
  ┊─ Metadata: {
  │   source: "xx.pdf",
  │   page: 12,
  │   chunk_index: 3,
  │   chunk_total: 45,
  │   doc_id: "<document.id>"
  │ }
  ┊─ ID: "{doc_id}:{chunk_index}"
  ┊─ Collection name prefix: kb_（避免特殊字符冲突，方便调试）
```

---

## 四、文档处理流水线

### Document Router（文档路由）

解析器采用 **Router + Parser 接口**模式，MVP 用 PyMuPDF，Phase 2 无缝接入 MinerU 兜底。

```
                    ┌──────────────┐
                    │  DocRouter   │
                    │   (路由入口)  │
                    └──────┬───────┘
                           │
              ┌────────────┼────────────┐
              │            │            │
        ┌─────┴─────┐ ┌───┴────┐ ┌────┴────┐
        │ PDF Router │ │ DOCX   │ │ TXT     │
        └─────┬─────┘ └────────┘ └─────────┘
              │
      ┌───────┼───────┐
      │       │       │
  ┌───┴───┐ ┌─┴──┐   (预留)
  │PyMuPDF│ │MinerU│
  │(MVP)  │ │Ph2  │
  └───────┘ └─────┘
```

详细接口设计见 `docs/superpowers/specs/2026-06-14-document-router-design.md`。

### MVP 分块流程

```
源文档 (PDF/DOCX/TXT)
        │
 DocRouter.parse()
        │
 ├── PDF → PyMuPDFParser  → 逐页提文本
 │         └── 扫描件检测：文本提取率 < 200字/页 → 报错"暂不支持"
 ├── DOCX → python-docx   → 逐段提文本
 └── TXT → 逐段提取
        └── chardet 编码检测 → UTF-8/GBK 回退
        │
        ▼
 RecursiveCharacterTextSplitter (chunk_size=待定, overlap=64)
        │  separator: ["\n\n", "\n", "。", "；", " ", ""]
        │  chunk_size 通过 RAGAS benchmark 对比 512/768/1024 确定
        ▼
 表格完整性检测（检测层面，标记被切断的表格行，不保护）
        │
        ▼
 ParseResult → ChromaDB 入库 + MySQL metadata 记录
```

> **表格保护决策**：MVP 只做到检测层面（check_chunks.py 标记疑似切断的表格片段），
> Phase 2 引入 MinerU 后实现完整表格保护。

**分块质量指标（check_chunks.py 输出）：**
- 总文档数 / 总 chunk 数
- 平均 chunk 长度（字符数）
- chunk 长度分布（P10 / P50 / P90）
- overlap 实际比例
- 被切断的表格数（0 为目标值）
- 示例输出（前 5 个 chunk 的预览）

---

## 五、项目规范

### 环境配置

MVP 所需环境变量：

```bash
# DashScope API
DASHSCOPE_API_KEY=sk-xxx
DASHSCOPE_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1

# 模型选择
LLM_MODEL=qwen-max
EMBEDDING_MODEL=text-embedding-v3
RERANK_MODEL=gte-rerank-v2
LLM_TEMPERATURE=0.1

# MySQL
MYSQL_HOST=mysql
MYSQL_PORT=3306
MYSQL_USER=root
MYSQL_PASSWORD=financial_qa_pass
MYSQL_DATABASE=financial_qa

# Redis
REDIS_HOST=redis
REDIS_PORT=6379
REDIS_DB=0

# ChromaDB（持久化目录，容器内路径）
CHROMA_PERSIST_DIR=/data/chroma

# 应用
LOG_LEVEL=INFO
```

`.env` 文件位于项目根目录，仅供本地开发使用。Docker 环境中环境变量通过 `docker-compose.yml` 的 `environment:` 或 `env_file:` 传入。**`.env` 不提交到 git。**

### .gitignore 规范

```
# Python
__pycache__/
*.py[cod]
*.egg-info/
dist/
.eggs/

# Environment
.env
.env.local

# IDE
.vscode/
.idea/

# Project local data（Docker 数据卷残留 + 本地开发数据）
data/chroma_persist/
chroma_data/
mysql_data/
redis_data/

# OS
.DS_Store
Thumbs.db
```

### 日志

沿用旧项目的 **loguru**，统一日志格式：

```python
from loguru import logger
logger.add("logs/app_{time:YYYY-MM-DD}.log", rotation="10 MB", retention="7 days")
```

Docker 环境下日志输出到 stdout（docker logs），同时保留文件日志供排查。

---

## 六、开发迭代计划

### Phase 1 — MVP（当前周期）

目标：验证核心技术可行性，提供可演示的端到端体验。

```
Iter 1: 项目骨架与 Docker 生态
  ├── 项目目录结构（src/, tests/, 根配置）
  ├── pyproject.toml（项目元数据、依赖、工具配置）
  ├── docker-compose.yml（定义 mysql / redis / app 三个服务）
  ├── Dockerfile（多阶段构建 + wait-for-it.sh）
  ├── src/config.py + .env.template
  ├── deploy/mysql/init/001_schema.sql（DDL 文件，挂载到 MySQL 容器 entrypoint）
  └── .gitignore
  └── 验证: docker-compose up --build 后所有服务健康运行，表自动创建

Iter 2: 文档处理流水线
  ├── src/parsers/base.py（ChunkData, ParseResult, BaseParser 接口）
  ├── src/parsers/pymupdf_parser.py（PyMuPDF 解析器）
  ├── src/parsers/docx_parser.py（python-docx 解析器）
  ├── src/parsers/txt_parser.py（纯文本解析器）
  ├── src/parsers/router.py（DocRouter 路由逻辑）
  ├── src/document_loader.py（兼容入口，调用 DocRouter）
  ├── 分块策略（RecursiveCharacterTextSplitter + 表格完整性检测）
  ├── src/check_chunks.py（分块质量报告）
  ├── src/mysql_db.py（MySQL CRUD 封装）
  └── 验证: 上传财报 PDF → 分块 → 查看质量报告

Iter 3: 向量存储与检索 ✅
  ├── src/models.py（DashScope Embedding/LLM/Rerank 工厂 + retry）
  ├── src/vector_store.py（ChromaDB 封装：增/删/查/集合管理，list_collections 已适配 ChromaDB 0.6）
  ├── src/check_retrieval.py（检索测试脚本）
  ├── 补充：Redis 密码认证（docker-compose + config + .env.template）
  └── 验证: 53/53 测试通过，端到端 parse→vectorize→search 闭环 ✅

Iter 4: RAG 问答链路
  ├── src/models.py（追加 Qwen-max LLM + gte-rerank-v2 客户端）
  ├── src/rag_chain.py（检索 → 重排序 → Prompt → 流式生成）
  ├── src/chat_manager.py（Redis 对话历史, 保留最近 N 轮）
  ├── 引用来源生成（source + page + content snippet）
  └── 验证: 端到端问答 + 引用可追溯到原文位置

Iter 5: Gradio UI 界面
  ├── src/app.py（知识库选择/创建/删除 + 文件上传 + 对话 + 引用展示）
  ├── 异步上传 + 进度提示（threading 后台处理，避免 Gradio 超时）
  ├── 首次使用空状态引导（欢迎文案 + 操作指引）
  ├── 知识库切换时清空对话历史
  └── 验证: 投资人演示全流程走通

Iter 6: 评估与收尾
  ├── src/eval_ragas.py（RAGAS: faithfulness, answer_relevancy, context_recall, context_precision）
  ├── 对比测试不同 chunk_size（512/768/1024），以 Recall@5 决定最终值
  ├── 20+ 示例 QA 对覆盖核心场景（基于测试文档：厦门灿坤 2019 年报）
  ├── README.md
  └── 演示脚本 + 录屏
  └── 验证: 指标达标, 演示流程流畅
```

**MVP 交付清单：**

| 类别 | 交付物 |
|------|--------|
| 代码 | 完整 Python 项目（模块化、类实例化、无全局变量陷阱） |
| 部署 | docker-compose.yml + Dockerfile + wait-for-it.sh |
| 文档 | README（启动、使用、架构说明） |
| 评估 | RAGAS 报告 + 人工评估纪要 |
| 演示 | 录屏 / 现场演示流程 |

---

### Phase 2 — 增强系统

目标：追平旧系统能力，新增智能路由和生产就绪的 Harness。

#### Phase 2 迭代计划 — 执行状态

```
Step 0: 基础设施重构（已完成 ✅）
  ├── FastAPI 替换 Gradio 后端（SSE 流式 / KB CRUD / 文档上传）✅
  ├── Nginx 反向代理 + 静态文件服务 ✅
  ├── LangChain 0.3 → 1.x 全线升级（含 community → langchain-dashscope）✅
  ├── Langfuse Tracing 接入（自托管，CallbackHandler + @observe）✅
  └── HTML 前端（KB 管理页 + 聊天页，基于 FastAPI API + Tailwind CSS CDN）✅

Step 0b: HTML 前端开发（已合并到 Step 0，已完成 ✅）
  ├── 基于 FastAPI API 的 Tailwind CSS 前端（IBM Plex Sans + 蓝/靛蓝配色）✅
  ├── 知识库管理页：卡片网格 + 侧边栏 + 模态框 CRUD + 文档上传 + 骨架屏 ✅
  ├── 聊天页：KB 选择 / SSE 流式 Markdown 对话 / 引用展示 / 快捷提问 ✅
  └── 设计系统：ui-ux-pro-max 生成，tailwind-design-system 实现 ✅

Step 1: Harness Engineering（质量护栏体系）

```
┌─────────────────────────────────────────────────────────┐
│                    Harness Engineering                    │
│                                                         │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  │
│  │ 单元测试      │  │ 集成测试      │  │ 回归测试      │  │
│  │ pytest       │  │ Docker 环境  │  │ 每次提交触发  │  │
│  │ ─ 每个模块   │  │ ─ 全链路    │  │ ─ GitHub CI  │  │
│  │   独立测试   │  │ ─ DB/Redis  │  │ ─ RAGAS基准  │  │
│  └──────────────┘  └──────────────┘  └──────────────┘  │
│                                                         │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  │
│  │ Prompt 测试   │  │ 分块质量 CI  │  │ 可观测性     │  │
│  │ ─ 输入模板   │  │ ─ 每次修改  │  │ ─ 请求追踪  │  │
│  │ ─ 输出断言   │  │   分块策略  │  │ ─ 性能监控  │  │
│  │ ─ 回归基线   │  │   自动验证  │  │ ─ 错误报告  │  │
│  └──────────────┘  └──────────────┘  └──────────────┘  │
│                                                         │
│ 工具: pytest, pytest-cov, GitHub Actions, Langfuse     │
└─────────────────────────────────────────────────────────┘
```

#### Phase 2 功能待办

| 模块 | 功能 | 优先级 |
|------|------|--------|
| **检索增强** | BM25 混合检索（带缓存，避免每次重建） | P0 |
| **检索增强** | Hybrid 权重可配置（语义:关键词 = ?:?） | P1 |
| **检索增强** | 多路召回融合（多 Query 扩展） | P2 |
| **意图路由** | 新增小 LLM（如 Qwen-turbo）做意图分类（闲聊 vs 查文档 vs 分析） | P0 |
| **对话增强** | 多轮对话上下文融合（当前问题 + 历史重新检索） | P1 |
| **对话增强** | 会话管理 UI（历史会话列表、继续对话） | P1 |
| **分块优化** | 语义分块（Semantic Chunker，按语义边界切分） | P2 |
| **分块优化** | 小文档摘要块（Document Summary Index） | P2 |
| **引用增强** | 引用高亮（在原文中标注答案位置） | P1 |
| **引用增强** | 多文档交叉引用 | P2 |
| **质量评估** | RAGAS 自动化流水线（每次优化后跑基准） | P0 |
| **Harness** | 单元测试覆盖率 ≥ 70% | P0 |
| **Harness** | CI/CD GitHub Actions 流水线 | P0 |
| **Harness** | Prompt 回归测试套件 | P1 |
| **Harness** | 分块策略变更自动验证 | P1 |
| **可观测性** | Langfuse Tracing（自托管） | P0 |

---

### Phase 3 — 生产工程化

目标：企业级可用性、可靠性、可观测性。

#### 架构升级

```
┌──────────────────────────────────────────────────────────┐
│                    生产架构（Phase 3）                     │
│                                                          │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐              │
│  │  Nginx   │──│ FastAPI │──│ 业务层   │              │
│  │  反向代理 │  │  多副本  │  │          │              │
│  └──────────┘  └──────────┘  └────┬─────┘              │
│                                    │                    │
│  ┌──────────┐  ┌──────────┐  ┌────┴─────┐              │
│  │ 熔断器   │  │ 降级策略  │  │ 限流     │              │
│  │ 断路器   │  │ 兜底文案  │  │ 速率限制 │              │
│  └──────────┘  └──────────┘  └──────────┘              │
│                                                          │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐              │
│  │ Prom     │  │ Grafana  │  │ Loki     │              │
│  │ 指标     │  │ 监控面板 │  │ 日志聚合 │              │
│  └──────────┘  └──────────┘  └──────────┘              │
└──────────────────────────────────────────────────────────┘
```

#### Phase 3 待办

| 类别 | 项目 | 说明 |
|------|------|------|
| **部署** | Docker 多阶段优化 | 镜像瘦身（目标 < 500MB） |
| **部署** | docker-compose 生产配置 | 资源限制、重启策略、日志轮转 |
| **部署** | HTTPS + Nginx 反向代理 | SSL 终结、静态资源缓存 |
| **可靠性** | 熔断器（Circuit Breaker） | DashScope API 调用异常时自动熔断，渐进恢复 |
| **可靠性** | 服务降级 | Redis 不可用 → 退化为内存；MySQL 不可用 → 只读模式 |
| **可靠性** | 限流 | 用户级 QPS 限制，防止 API 费用失控 |
| **可靠性** | 重试 + 指数退避 | 外部调用自动重试，抖动避让 |
| **可观测性** | Prometheus 指标 | 请求量/延迟/错误率/Token 消耗 |
| **可观测性** | Grafana 面板 | 系统健康度大盘 |
| **可观测性** | Loki 日志聚合 | 集中式日志查询 |
| **可观测性** | 告警规则 | 错误率突增/延迟超标/DashScope 配额预警 |
| **安全** | API Key 轮换 | 定期更换 + 多 Key 备用 |
| **安全** | 文档权限控制 | 知识库级别的访问控制 |
| **安全** | 请求审计日志 | 记录所有查询操作 |
| **评估** | 长效 RAGAS 监控 | 每次更新后自动评估质量变化 |
| **评估** | A/B 测试框架 | 多策略对比（分块/检索/提示词） |

---

## 七、模块依赖关系

```
src/config.py ──◄ 所有模块
    │
    ├── src/models.py
    │    ├── src/vector_store.py（ChromaDB 使用内置 embedding 或 DashScopeEmbedding）
    │    ├── src/rag_chain.py
    │    └── src/eval_ragas.py
    │
    ├── src/parsers/              ← Document Router + Parser 实现
    │    ├── src/parsers/router.py
    │    ├── src/parsers/base.py
    │    ├── src/parsers/pymupdf_parser.py
    │    ├── src/parsers/docx_parser.py
    │    └── src/parsers/txt_parser.py
    │    │
    │    └── src/document_loader.py（兼容入口，调用 DocRouter）
    │         ├── src/app.py
    │         ├── src/check_chunks.py
    │         └── src/check_retrieval.py
    │
    ├── src/mysql_db.py
    │    ├── src/app.py
    │    └── src/eval_ragas.py
    │
    └── src/chat_manager.py
         └── src/rag_chain.py
```

**MVP 重点改进（对比旧系统）：**

| 旧问题 | MVP 方案 |
|--------|----------|
| 全局 `SESSION_ID` 多用户串话 | 每个请求创建独立 session ID，Gradio 上下文隔离 |
| `models.py` Rerank 用错模型名 | 统一模型工厂，模型名从 config 读取 |
| `rag_chain.py` 直接 `ChatOpenAI()` | 全部通过 `models.py` 工厂创建 |
| `bare except: pass` | 最小化 try-catch，异常明确日志 |
| BM25 每次重建 | Phase 2 加缓存；MVP 纯语义检索 + Rerank |
| 模块级全局 init → 一挂全挂 | 类实例化 + 显式 init + MySQL/Redis 重试 |
| Redis 连接失败致命 | try/except 捕获 + InMemory 降级（预埋） |
| 无扫描件检测 | PyMuPDF 文本提取率 < 200字/页 → 明确报错 |
| 无编码检测 | chardet 检测 + UTF-8/GBK 回退 |
| 无上传进度反馈 | threading 异步处理 + UI 进度提示 |
| 引用展示被注释 | 答案尾部追加 Markdown 引用块 |

---

## 八、风险登记

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| DashScope API 延迟不稳定 | 中 | 高 | retry + 指数退避；前端流式输出遮掩延迟 |
| 财报 PDF 格式复杂（扫描件、加密） | 中 | 中 | MVP 检测扫描件并明确报错"暂不支持" |
| 表格/数字被错误分块切断 | 中 | 高 | check_chunks.py 标记被切断的表格行；Phase 2 MinerU |
| Embedding 维度高导致检索慢 | 低 | 中 | ChromaDB 本地检索延迟可控 |
| Docker 镜像体积过大 | 低 | 中 | 多阶段构建、alpine 基础镜像 |
| 幻觉率高于预期 | 中 | 高 | 严格的 system prompt（拒绝计算 + 强制时间对齐）+ 引用约束 + RAGAS 评估 |
| Gradio 上传大文件超时 | 中 | 中 | 异步 threading 处理 + UI 进度提示，避免请求超时 |
| TXT 编码检测误判 | 低 | 低 | chardet + UTF-8/GBK 回退，覆盖 95% 场景 |
| RAGAS 评估 bias（Qwen judge Qwen） | 中 | 低 | 优先使用基于 ground truth 的指标；记录 bias 风险 |

---

## 九、旧系统对比

| 维度 | 旧系统 (old/) | 新系统 MVP |
|------|--------------|------------|
| 部署方式 | pip install + 手动配置 | docker-compose up --build |
| 会话管理 | 全局变量，多用户冲突 | 每请求独立 session |
| 异常处理 | bare except，吞异常 | 显式异常分类 + 日志 |
| 模块初始化 | import 时全局创建 | 类实例化 + 懒加载 |
| Redis 依赖 | 不可用即崩溃 | 不可用时降级为无记忆 |
| BM25 | 每次查询重建 | Phase 2 实现，MVP 不做 |
| 分块验证 | 无 | check_chunks.py + 质量面板 |
| 测试 | tests.py（不完整） | Phase 2 Harness Engineering |

---

> **文件说明**: 本文档生成于 2026-06-13，为全景路线图。2026-06-15 经 grill 盘问后补充了以下决策：
> - 扫描件检测 + 明确报错 / TXT 编码检测 / 异步上传进度 / 空状态引导
> - 表格保护降级为检测层面 / chunk_size 通过 RAGAS benchmark 决定
> - Prompt 增加"拒绝计算"和"强制时间对齐"约束
> - 所有外部调用加 retry + 指数退避 / Redis 降级预埋
> - 分块质量看板改为 CLI 版本 / 引用展示改为方案 A（答案尾部追加）
> - 完整决策记录见 `openspec/changes/financial-qa-mvp/decision-log.md`
>
> 2026-06-26 Phase 2 Step 0 执行完成：
> - T1-T12 全部完成并提交，对应 git log 574681c..0c5bd3b
> - 前端方案最终采用 Tailwind CSS CDN（v3 Play CDN）+ IBM Plex Sans 字体
> - 设计系统由 ui-ux-pro-max 生成，tailwind-design-system 指导实现
> - 知识库管理页：卡片网格 + 侧边栏 + 模态框 CRUD + 文档上传 + 骨架屏
> - 聊天页：SSE 流式 Markdown + 引用展示 + 快捷提问 + 空状态引导
> - 下一步：Harness Engineering（测试覆盖率 + CI/CD + 检索增强）
>
> 2026-06-26 Phase 2 Step 0 规划决策：
> - 监控平台选型：LangSmith → Langfuse（自托管），因私有化部署和国内网络需求
> - 后端框架：Gradio → FastAPI + Nginx，实现前后端分离
> - LangChain 全线升级 0.3 → 1.x（含 community → langchain-dashscope）
> - 前端方案：HTML/CSS/JS（LLM 生成），基于 FastAPI API
> - 分阶段：Tracing Phase 1 先上，Eval/Prompt Mgmt 稳定后
> - 会话管理：前端 UUID，多人访问不受影响
> - 完整提案见 `openspec/changes/infrastructure-rebuild-phase2/`
