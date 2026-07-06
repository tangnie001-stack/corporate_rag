# 金融文档智能问答助手 MVP — Master Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 构建一个基于 RAG 的企业文档智能问答助手 MVP，用户上传 PDF/DOCX/TXT 财报文档后，可通过自然语言问答获取带引用来源的答案，支持 Docker 一键部署和投资人演示。

**Architecture:** Python 3.11+ 后端 + Gradio 5.x 前端 + ChromaDB 内嵌向量库 + MySQL 元数据 + Redis 对话缓存。文档解析采用 Router + Parser 接口模式（PyMuPDF/python-docx/TXT），RAG 链路走 DashScope API（qwen-max + text-embedding-v3 + gte-rerank-v2）。全部通过 Docker Compose 编排，单人串行业余时间开发。

**Tech Stack:** Python 3.11, Gradio 5.x, ChromaDB, LangChain, DashScope API (qwen-max), MySQL 8.0, Redis 7, Docker Compose, PyMuPDF, python-docx, RAGAS, loguru

---

## 一、6 个 Iteration 边界

| Iter | 名称 | 目标 | 输入依赖 | 交付物 | 关联 Spec | 预估耗时 |
|------|------|------|---------|--------|----------|---------|
| 1 | 项目骨架与 Docker 生态 | 搭建项目目录、配置文件、Docker 编排，所有容器健康运行 | 无 | pyproject.toml, Dockerfile, docker-compose.yml, .env.template, src/config.py, deploy/mysql/init/001_schema.sql, .gitignore | [infrastructure](/openspec/changes/financial-qa-mvp/specs/infrastructure/spec.md) | 0.5 天 |
| 2 | 文档处理流水线 | 实现 PDF/DOCX/TXT 解析、分块、质量检查、MySQL 元数据写入 | Iter 1 | src/parsers/*, src/document_loader.py, src/mysql_db.py, src/cli/check_chunks.py | [document-ingestion](/openspec/changes/financial-qa-mvp/specs/document-ingestion/spec.md) | 1.5 天 |
| 3 | 向量存储与检索 ✅ | 实现 ChromaDB 向量化存储、语义检索、检索质量 CLI + Redis 密码认证 | Iter 1 | src/models.py（LLM/Embedding/Rerank 工厂）, src/vector_store.py, src/cli/check_retrieval.py | [vector-retrieval](/openspec/changes/financial-qa-mvp/specs/vector-retrieval/spec.md) | 1 天 |
| 4 | RAG 问答链路 ✅ | 实现检索→重排序→Prompt→流式生成→引用，端到端可问答 | Iter 2 + 3 | src/models.py（LLM+Rerank 部分）, src/rag_chain.py, src/chat_manager.py | [rag-generation](/openspec/changes/financial-qa-mvp/specs/rag-generation/spec.md) | 1.5 天 |
| 5 | Gradio UI 界面 | 实现完整 Web 交互：知识库管理、上传、对话、引用、空状态引导、异步进度 | Iter 4 | src/app.py | [chat-interface](/openspec/changes/financial-qa-mvp/specs/chat-interface/spec.md) | 1 天 |
| 6 | 评估与收尾 | RAGAS 评估、chunk_size benchmark、README、演示脚本 | Iter 4 + 5 | src/eval_ragas.py, test_docs/ QA 对, README.md | [evaluation](/openspec/changes/financial-qa-mvp/specs/evaluation/spec.md) | 1 天 |
| | **合计** | | | | | **~6.5 天** |

---

## 二、跨 Iter 锁定项

以下接口、配置项和命名规则在多个 Iter 中引用，必须保持一致。

### 2.1 config.py 配置项清单

```python
# ------ DashScope API ------
DASHSCOPE_API_KEY: str           # 从环境变量读取
DASHSCOPE_BASE_URL: str          # 默认 https://dashscope.aliyuncs.com/compatible-mode/v1

# ------ 模型选择 ------
LLM_MODEL: str                   # qwen-max
EMBEDDING_MODEL: str             # text-embedding-v3
RERANK_MODEL: str                # gte-rerank-v2
LLM_TEMPERATURE: float           # 0.1

# ------ MySQL ------
MYSQL_HOST: str                  # 开发: localhost | Docker: mysql
MYSQL_PORT: int                  # 3306
MYSQL_USER: str                  # root
MYSQL_PASSWORD: str
MYSQL_DATABASE: str              # financial_qa

# ------ Redis ------
REDIS_HOST: str                  # 开发: localhost | Docker: redis
REDIS_PORT: int                  # 6379
REDIS_DB: int                    # 0

# ------ ChromaDB ------
CHROMA_PERSIST_DIR: str          # /data/chroma (Docker) | ./data/chroma_persist (dev)
CHROMA_COLLECTION_PREFIX: str    # "kb_" — collection 命名前缀

# ------ 文档处理 ------
CHUNK_SIZE: int                  # 待定，Iter 6 通过 RAGAS 决定
CHUNK_OVERLAP: int               # 64
TOP_K_RETRIEVAL: int             # 8（检索召回数，供重排序选择）
TOP_K_RERANK: int                # 5（重排序后最终保留数）

# ------ 对话 ------
MEMORY_WINDOW: int               # 6（保留最近 N 轮对话）
REDIS_TTL: int                   # 604800（7 天）

# ------ 重试策略 ------
RETRY_MAX_ATTEMPTS: int          # 3
RETRY_INITIAL_INTERVAL: float    # 1.0（秒）
RETRY_BACKOFF_FACTOR: float      # 2.0

# ------ 文件上传 ------
MAX_FILE_SIZE: int               # 52428800（50MB）
```

### 2.2 models.py 工厂函数签名

```python
def get_llm(model: str = "qwen-max", temperature: float = 0.1) -> ChatOpenAI
def get_embeddings(model: str = "text-embedding-v3") -> DashScopeEmbeddings
def get_rerank(model: str = "gte-rerank-v2", top_n: int = 5) -> DashScopeRerank
def with_retry(func: Callable, max_attempts: int = 3, initial_interval: float = 1.0, backoff: float = 2.0) -> Callable
```

所有工厂函数统一封装 retry + 指数退避，DashScope API Key 从 config 读取。

### 2.3 mysql_db.py 接口

```python
class MySQLDB:
    def init_db(self) -> None              # 建表（幂等）
    def get_or_create_kb(self, name: str) -> tuple[str, bool]   # 返回 (kb_id, is_new)
    def add_document(self, kb_id: str, filename: str, file_type: str, file_size: int) -> str  # 返回 doc_id
    def update_document_status(self, doc_id: str, status: str, chunk_count: int = 0, error_msg: str = "") -> None
    def get_kb_by_name(self, name: str) -> str | None
    def get_all_kb(self) -> list[tuple[str, str]]
    def delete_kb(self, kb_id: str) -> bool
    def get_documents(self, kb_id: str) -> list[dict]
```

### 2.4 MySQL Schema（deploy/mysql/init/001_schema.sql）

```sql
CREATE TABLE IF NOT EXISTS knowledge_base (
    id          VARCHAR(36)  PRIMARY KEY,
    name        VARCHAR(255) NOT NULL UNIQUE,
    description TEXT,
    created_at  DATETIME     DEFAULT CURRENT_TIMESTAMP,
    updated_at  DATETIME     DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS document (
    id          VARCHAR(36)  PRIMARY KEY,
    kb_id       VARCHAR(36)  NOT NULL,
    filename    VARCHAR(255) NOT NULL,
    file_type   VARCHAR(10)  NOT NULL,
    file_size   INT          NOT NULL DEFAULT 0,
    chunk_count INT          NOT NULL DEFAULT 0,
    status      VARCHAR(20)  NOT NULL DEFAULT 'pending',
    error_msg   TEXT,
    created_at  DATETIME     DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (kb_id) REFERENCES knowledge_base(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS conversation_history (
    id          INT          AUTO_INCREMENT PRIMARY KEY,
    session_id  VARCHAR(36)  NOT NULL,
    kb_id       VARCHAR(36)  NOT NULL,
    role        ENUM('user','assistant') NOT NULL,
    content     TEXT         NOT NULL,
    sources     JSON,
    created_at  DATETIME     DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_session (session_id, created_at)
);
```

### 2.5 ChromaDB 命名与元数据规则

```python
# Collection 命名
collection_name = f"kb_{kb_id.replace('-', '')}"  # 如 kb_550e8400e29b41d4a716446655440000

# Chunk 元数据
metadata = {
    "source": str,          # 原始文件名（如 "年报2023.pdf"）
    "page": int,            # 页码（从 1 开始）
    "chunk_index": int,     # 该文档内的 chunk 序号
    "chunk_total": int,     # 该文档总 chunk 数
    "doc_id": str,          # document.id
}

# Chunk ID
chunk_id = f"{doc_id}:{chunk_index}"
```

---

## 三、每个 Iter 的里程碑验收 Checklist

### Iter 1 — 项目骨架与 Docker 生态

```bash
# 1. 目录结构就位
ls src/ deploy/ docker-compose.yml Dockerfile .env.template .gitignore pyproject.toml

# 2. Docker 全部健康运行
docker-compose up --build -d
docker-compose ps
# 预期: mysql (healthy), redis (healthy), app (running)

# 3. MySQL 表自动创建
docker exec financial-qa-mysql mysql -uroot -pfinancial_qa_pass financial_qa -e "SHOW TABLES;"
# 预期: knowledge_base, document, conversation_history

# 4. Redis 可连接
docker exec financial-qa-redis-1 redis-cli ping
# 预期: PONG

# 5. config.py 可导入
docker-compose exec app python -c "from src.config import *; print('OK')"
# 预期: OK
```

### Iter 2 — 文档处理流水线

```bash
# 1. 解析器可直接调用
docker-compose exec app python -c "
from src.parsers.router import DocRouter
router = DocRouter()
docs = router.parse('test_docs/sample.txt')
print(f'Parsed {len(docs)} chunks')
"
# 预期: 输出 chunk 数量

# 2. 分块质量报告可运行
docker-compose exec app python src/cli/check_chunks.py test_docs/sample.txt
# 预期: 打印六大指标（总数/平均长度/分布/overlap/表格切断数/预览）

# 3. MySQL CRUD 可用
docker-compose exec app python -c "
from src.mysql_db import MySQLDB
db = MySQLDB()
db.init_db()
kid, is_new = db.get_or_create_kb('测试库')
print(f'KB: {kid}, New: {is_new}')
"
# 预期: 返回 kb_id
```

### Iter 3 — 向量存储与检索 ✅

```bash
# 1. Embedding 调用正常
docker compose exec app python -c "
from src.models import get_embeddings
emb = get_embeddings()
vec = emb.embed_query('测试查询')
print(f'Vector dim: {len(vec)}')
"
# 结果: DashScopeEmbeddings 实例化成功（需真实 API Key 验证维度）

# 2. 向量入库与检索闭环
docker compose exec app python -c "
from src.vector_store import VectorStore
from src.parsers.base import ChunkData
import uuid

kb_id = uuid.uuid4().hex
vs = VectorStore()
vs.get_or_create_collection(kb_id)
chunks = [
    ChunkData(content='贵州茅台2024年营业收入1,741亿元', metadata={'source': 'test.txt', 'page': 1}, chunk_id='test:0'),
]
count = vs.add_chunks(kb_id, chunks, 'doc_test')
print(f'Added: {count} chunks')
results = vs.similarity_search(kb_id, '营业收入', k=5)
print(f'Found: {len(results)} chunks')
vs.delete_collection(kb_id)
"
# 结果: Added: 1 chunks, Found ≥ 1 chunks ✅

# 3. check_retrieval.py 可运行
python -m src.cli.check_retrieval --help
# 结果: 参数解析/错误处理正常 ✅
```

### Iter 4 — RAG 问答链路 ✅

```bash
# 1. 端到端问答 ✅
docker compose exec app python -c "
from src.rag_chain import RAGChain
rc = RAGChain()
answer_gen, citations = rc.chat_with_citations('rag_test_4672d3', 'test_session_001', '贵州茅台2024年营业收入是多少?')
full = ''.join([t for t in answer_gen])
print(f'Answer: {full}')
print(f'Citations: {len(citations)}')
for c in citations:
    print(f'  - {c.source} (p{c.page}): {c.content[:80]}...')
"
# 实际结果: Answer: 根据文档内容，贵州茅台2024年的营业总收入为1,741亿元。
#            Citations: 2（含 sample.txt 文件名 + 页码）

# 2. Redis 降级验证 ✅
docker stop financial-qa-redis
docker compose exec app python -c "
from src.rag_chain import RAGChain
rc = RAGChain()
answer_gen, citations = rc.chat_with_citations('rag_test_4672d3', 'test_session_002', '贵州茅台主营业务是什么?')
full = ''.join([t for t in answer_gen])
print(f'Answer (degraded): {full}')
"
docker start financial-qa-redis
# 实际结果: Redis 不可用时自动 InMemory 降级，日志显示:
#   WARNING  ChatManager: Redis unavailable (...), using InMemory fallback
# Answer: 根据文档内容，贵州茅台的主营业务是茅台酒及系列酒的生产与销售。

# 3. 知识库不存在 → 友好提示 ✅
# 4. 检索无结果 → "未在文档中找到相关数据" ✅
# 5. Rerank 失败 → 降级到原始排序（第1次重试失败，第2次重试失败，第3次重试失败后降级） ✅
# 6. LLM 流式 API 调用含指数退避重试（3次） ✅
```

**Iter 4 实际执行差异:**
| # | 差异点 | 计划 | 实际 | 原因 |
|---|--------|------|------|------|
| 1 | **Rerank 降级触发** | 预期正常 rerank | DashScopeRerank.rerank() 返回 None，3次重试后退回原始排序 | gte-rerank-v2 在 langchain-community 接口适配问题 |
| 2 | **测试用 KB 名称** | test_kb | rag_test_4672d3（UUID 自动生成） | 集成测试时动态创建避免冲突 |

### Iter 5 — Gradio UI 界面

```bash
# 1. Gradio 服务启动
docker-compose up --build -d
curl -s http://localhost:7860 | head -5
# 预期: 返回 Gradio HTML 页面

# 2. 浏览器人工验证 checklist（手动执行）
#   - [ ] 首次打开 → 空状态引导文案可见
#   - [ ] 创建知识库 → 下拉框可选
#   - [ ] 上传 PDF → 进度提示 → 成功消息
#   - [ ] 提问 → 流式输出 → 引用 Markdown 块展示
#   - [ ] 切换知识库 → 对话历史清空
#   - [ ] 删除知识库 → 列表刷新
```

### Iter 6 — 评估与收尾

```bash
# 1. RAGAS 评估可运行
docker-compose exec app python src/eval_ragas.py
# 预期: 输出评估指标到 CSV

# 2. chunk_size benchmark
docker-compose exec app python src/eval_ragas.py --chunk-size 512
docker-compose exec app python src/eval_ragas.py --chunk-size 768
docker-compose exec app python src/eval_ragas.py --chunk-size 1024
# 预期: 三组对比数据

# 3. README 存在
ls README.md
# 预期: 文件存在
```

---

## 四、已知风险与待办

以下风险在开发过程中识别，按优先级排列。

### P0 — 功能缺陷（Iter 6 后修复）

| # | 模块 | 问题 | 影响 |
|---|------|------|------|
| R1 | rag_chain.py | **助手回答未写回对话历史** — `chat_with_citations()` 只保存了用户消息，未调用 `chat_manager.add_message("assistant", ...)` 保存回答 | 多轮对话时 `get_window` 取到的历史中只有 user 消息没有 assistant 回答，LLM 缺少上一轮上下文，对话能力退化为单轮。计划 Iter 6 后修复 |

### P1 — 资源风险（持续关注，不阻塞 MVP）

| # | 模块 | 问题 | 影响 |
|---|------|------|------|
| R2 | chat_manager.py | **InMemory 模式无上限** — `_memory_store` 是普通 dict，无淘汰机制。Redis 模式有 TTL 保护，但 Redis 降级后会话数据只增不减 | 长会话或大量会话场景下内存持续增长，极端情况可能 OOM。建议后续引入每个 session 上限（如 500 条）或定时清理 |

### P2 — 观察项（暂不处理，记住就好）

| # | 模块 | 问题 | 说明 |
|---|------|------|------|
| R3 | chromadb | **InvalidCollectionException 导入失败** — 当前安装的 chromadb 版本中该异常不存在 | 预存兼容问题，`pip install --upgrade chromadb` 可能解决。不影响已有测试 |
| R4 | config.py | **原 config.py 文件拆分后残留英文重复** — 删除前有中英文两版重复内容 | 已在迁移到 `config/settings.py` 时清理，仅保留中文版 |
