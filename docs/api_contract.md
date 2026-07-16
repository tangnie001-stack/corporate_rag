# 接口契约

> 记录模块间接口的参数语义、返回值格式和历史踩坑记录。
> 修改任一模块的公共方法签名时，同步更新此文件。

---

## 1. 核心标识符

| 标识符 | 格式 | 说明 | 常见错误 |
|--------|------|------|----------|
| `kb_id` | UUID 字符串（如 `53890512-f252-45bf-9485-25b4253cb4f1`）或空字符串 `""` | 知识库唯一标识。`""` 表示"搜索所有知识库" | ❌ 传了 `kb_name`（"2024年报"） |
| `doc_id` | UUID 字符串 | 文档唯一标识 | ❌ 传了 MySQL 自增 ID |
| `session_id` | 任意字符串 | 会话标识，用于关联对话历史 | ❌ 传了空字符串 |
| `chunk_id` | `"{doc_id}:{index}"` 格式 | 向量库中的文档分块 ID | - |

---

## 2. 接口层：FastAPI Routes ↔ AppService

路由统一挂载在 `/api` 前缀下。
除 `/api/health` 和 `/api/chat/stream` 外，所有端点使用 POST 方法。
请求头 `X-Trace-ID` 可选传，响应头含 `X-Trace-ID`。

### 2.1 知识库

#### 2.1.1 `POST /api/kbs/list → list[dict]`

列出所有知识库。Body: `{}`

```json
{"code": "SUCCESS", "message": "操作成功", "data": [
  {"id": "uuid", "name": "库名称", "doc_count": 0}
]}
```

#### 2.1.2 `POST /api/kbs → 201, CreateKBResponse`

创建知识库（名称重复时返回已有库）。

Body:
```json
{"name": "库名称", "description": "可选描述"}
```

Response:
```json
{"code": "SUCCESS", "message": "操作成功", "data": {"id": "uuid", "created": true}}
```

#### 2.1.3 `POST /api/kbs/delete → 200 | 404`

删除知识库及其向量数据。Body: `{"kb_id": "uuid"}`

Success:
```json
{"code": "SUCCESS", "message": "操作成功", "data": {"success": true, "message": "知识库已删除"}}
```

404:
```json
{"code": "NOT_FOUND", "message": "知识库不存在", "data": null}
```

### 2.2 文档管理

#### 2.2.1 `POST /api/kbs/documents/list → list[dict]`

列出知识库中的所有文档。Body: `{"kb_id": "uuid"}`

```json
{"code": "SUCCESS", "message": "操作成功", "data": [
  {
    "id": "uuid",
    "filename": "name.pdf",
    "file_type": "pdf",
    "file_size": 1234,
    "status": "ready",
    "chunk_count": 10,
    "error_msg": null,
    "created_at": "2026-07-03T12:00:00"
  }
]}
```

| 键 | 类型 | 说明 |
|----|------|------|
| `id` | str | 文档 UUID |
| `filename` | str | 原始文件名 |
| `file_type` | str | `pdf` / `docx` / `txt` |
| `file_size` | int | 字节数 |
| `status` | str | 当前处理状态 |
| `chunk_count` | int | 入库分块数 |
| `error_msg` | str\|null | 失败原因 |
| `created_at` | str | 上传时间 |
| `eval_score` | float\|null | 分块质量综合评分（0-1，需开启 `CHUNK_EVAL_ENABLED`） |
| `eval_passed` | bool\|null | 质量是否达标（阈值 ≥ 0.70） |
| `eval_detail` | dict\|null | 评估详情 JSON，含 structure_integrity / sbr / granularity_cv 三个模块的分数和断裂明细 |

#### 2.2.2 `POST /api/kbs/documents/upload → 202`

异步上传文档。立即返回，后台处理。

Content-Type: `multipart/form-data`
Fields: `file`（PDF/DOCX/TXT，最大 10MB）, `kb_id`（uuid）

Response:
```json
{"code": "SUCCESS", "message": "操作成功", "data": {"doc_id": "uuid", "status": "processing", "filename": "name.pdf"}}
```

| 错误 | 状态码 |
|------|--------|
| 文件超过 10MB | 413 |
| 不支持的文件类型 | 400 |

后台处理流程：**extracting → completed / failed**

#### 2.2.3 `POST /api/kbs/documents/status → dict`

轮询文档处理进度。Body: `{"kb_id": "uuid", "doc_id": "uuid"}`

```json
{"code": "SUCCESS", "message": "操作成功", "data": {"status": "ready", "chunk_count": 42, "progress": 100, "error": ""}}
```

| 状态值 | 含义 |
|--------|------|
| `processing` | 正在处理 |
| `ready` | 处理完成 |
| `failed` | 处理失败 |
| `not_found` | 文档不存在 |

#### 2.2.4 `POST /api/kbs/documents/chunks → dict`

预览已处理文档的分块内容。Body: `{"kb_id": "uuid", "doc_id": "uuid", "page": 1, "page_size": 50}`

Response:
```json
{"code": "SUCCESS", "message": "操作成功", "data": {"items": [...], "total": 42, "page": 1, "page_size": 50}}
```

### 2.3 SSE 流式问答

#### 2.3.1 `GET /api/chat/stream?session_id={sid}&kb_id={kb_id}&query={question}&trace_id={traceId}`

Content-Type: `text/event-stream`

| 参数 | 说明 |
|------|------|
| `session_id` | 会话 ID |
| `kb_id` | 知识库 UUID（空字符串跨库搜索） |
| `query` | 用户问题 |
| `trace_id` | 链路追踪 ID（可选） |

事件流（按推送顺序）：

```json
event: status
data: {"stage": "retrieving", "message": "正在检索相关文档..."}

event: status
data: {"stage": "reranking", "message": "已找到 N 个候选，正在精排..."}

event: status
data: {"stage": "generating", "message": "正在生成回答..."}

event: token
data: {"token": "回答文本片段"}

event: citation
data: {"source": "文件名.pdf", "page": 15, "snippet": "内容摘要...", "score": 0.95, "highlighted_snippet": "<mark>高亮</mark>内容"}

event: done
data: {}

event: error
data: {"error": "错误消息"}
```

| 事件 | 说明 |
|------|------|
| `status` | 三阶段状态（retrieving / reranking / generating） |
| `token` | LLM 生成文本片段，前端逐段追加 |
| `citation` | 引用来源，按 source+page 去重 |
| `done` | 流结束标记 |
| `error` | 异常时推送，无 retry 机制 |

### 2.4 会话管理

#### 2.4.1 `POST /api/sessions/list → list[dict]`

列出最近 50 个会话。始终返回 200。Body: `{}`

```json
{"code": "SUCCESS", "message": "操作成功", "data": [
  {
    "id": "session_id",
    "title": "首条消息前20字",
    "kb_id": "uuid",
    "kb_name": "库名称",
    "message_count": 3,
    "created_at": "2026-07-03T12:00:00",
    "updated_at": "2026-07-03T12:05:00"
  }
]}
```

#### 2.4.2 `POST /api/sessions/messages → list[dict] | 404`

获取会话消息历史。Body: `{"session_id": "sid"}`

Success:
```json
{"code": "SUCCESS", "message": "操作成功", "data": [
  {
    "role": "user",
    "content": "问题文本",
    "sources": null,
    "created_at": "2026-07-03T12:00:00"
  },
  {
    "role": "assistant",
    "content": "回答文本",
    "sources": ["文件名.pdf (第3页)", "文件2.docx (第5页)"],
    "created_at": "2026-07-03T12:00:05"
  }
]}

404:
```json
{"code": "NOT_FOUND", "message": "会话不存在", "data": null}
```

#### 2.4.3 `POST /api/sessions/delete → 200 | 404`

删除会话及其所有消息。Body: `{"session_id": "sid"}`

Success:
```json
{"code": "SUCCESS", "message": "操作成功", "data": {"success": true}}
```

404:
```json
{"code": "NOT_FOUND", "message": "会话不存在", "data": null}
```

### 2.5 评估报告

#### 2.5.1 `POST /api/kbs/eval/latest → dict | null`

获取知识库最新的 RAGAS 评估报告。Body: `{"kb_id": "uuid"}`

```json
{"code": "SUCCESS", "message": "操作成功", "data": {
  "eval_date": "2026-07-12T18:00:00",
  "faithfulness": 0.92,
  "answer_relevancy": 0.88,
  "context_precision": 0.85,
  "context_recall": 0.87,
  "overall_score": 0.89,
  "passed": true,
  "qa_count": 22,
  "run_type": "manual"
}}
```

无评估记录时返回 `{"code": "SUCCESS", "data": null}`。

### 2.6 健康检查

#### 2.5.1 `POST /api/config → AppConfigResponse`

获取前端配置（如上传文件大小限制）。Body: `{}`

```json
{"code": "SUCCESS", "message": "操作成功", "data": {"max_upload_size": 10485760}}
```

| 键 | 类型 | 说明 |
|----|------|------|
| `max_upload_size` | int | 单文件上传上限（字节），默认 10MB，可通过 `MAX_FILE_SIZE` 环境变量覆盖 |

#### 2.5.2 `GET /api/health → dict`

```json
{"status": "ok"}
```

---

## 3. 接口层：AppService ↔ MySQLDB

### 3.1 `MySQLDB.get_all_kb() → list[tuple[str, str]]`

| 元组位置 | 列名 | 类型 |
|----------|------|------|
| `[0]` | `kb_id` | UUID 字符串 |
| `[1]` | `kb_name` | VARCHAR(255) |

⚠️ 返回顺序按 `created_at DESC`。调用方不要假设按名称排序。

### 3.2 `MySQLDB.get_documents(kb_id) → list[dict]`

| 键 | 类型 | 说明 |
|----|------|------|
| `id` | str | 文档 UUID（`doc_id`） |
| `filename` | str | 原始文件名 |
| `file_type` | str | 扩展名（`pdf` / `docx` / `txt`） |
| `file_size` | int | 文件大小（字节） |
| `status` | str | `pending` / `parsing` / `chunking` / `indexing` / `ready` / `failed` |
| `chunk_count` | int | 分块数 |
| `error_msg` | str \| None | 处理失败时的错误信息 |
| `meta_info` | str \| None | JSON 字符串，含 `eval` 评估数据 |

### 3.3 `MySQLDB.delete_kb(kb_id) → bool`

CASCADE 级联删除：知识库 → 文档 → 对话历史。

⚠️ 调用方必须同时调用 `VectorStore.delete_collection()` 清理向量数据，
MySQLDB 不感知 ChromaDB。

### 3.4 `MySQLDB.update_document_status(doc_id, status, chunk_count=0, error_msg="") → None`

更新文档处理状态。由 `_process_document` 后台任务调用。

### 3.5 `MySQLDB.update_document_meta_info(doc_id, meta_info) → None`

更新文档的 `meta_info` JSON 列（存储分块评估结果）。由 `_process_document_task` 在分块质量评估后调用。

| 参数 | 类型 | 说明 |
|------|------|------|
| `meta_info` | dict | 写入 JSON 列的字典，评估结果放在 `{"eval": {...}}` 下 |

### 3.6 `MySQLDB.insert_eval_report(report) → None`

插入一条 RAGAS 评估报告。首次调用时自动建表（幂等）。

| 参数 | 类型 | 说明 |
|------|------|------|
| `report.kb_id` | str | 知识库 UUID |
| `report.run_type` | str | `manual` / `sampling` / `ci_gate` |
| `report.qa_count` | int | QA 对数量 |
| `report.faithfulness` | float | 忠实度 |
| `report.answer_relevancy` | float | 答案相关性 |
| `report.context_precision` | float | 上下文精确率 |
| `report.context_recall` | float | 上下文召回率 |
| `report.overall_score` | float | 加权综合分（0.3×faith + 0.3×recall + 0.2×precision + 0.2×relevancy） |
| `report.passed` | bool | 是否达标（≥ 0.70） |
| `report.report_path` | str\|null | CSV 报告路径 |
| `report.detail_json` | list\|null | 逐条 QA 得分 `[{"q_index":0, "faithfulness":0.95}, ...]` |

### 3.7 `MySQLDB.get_latest_eval_report(kb_id) → dict | None`

获取知识库最新的 RAGAS 评估报告。按 `eval_date DESC LIMIT 1` 查询。

---

## 4. 接口层：AppService ↔ VectorStore

### 4.1 Collection 命名规则

```
name = f"kb_{kb_id.replace('-', '')}"
示例：kb_53890512f25245bf948525b4253cb4f1
```

⚠️ 调用方不应直接构造 collection 名称，始终通过 `get_or_create_collection()`。

### 4.2 VectorStore 初始化

使用 `PersistentClient`（内嵌模式），持久化路径通过 `CHROMA_PERSIST_DIR` 配置。

### 4.3 `VectorStore.add_chunks(kb_id, chunks, doc_id) → int`

| 参数 | 类型 | 说明 |
|------|------|------|
| `chunks` | `list[ChunkData]` | 解析器产出的分块数据（Parent-Child 格式） |
| `doc_id` | `str` | 文档 UUID |

每个 chunk 的 ChromaDB ID = `f"{doc_id}:{index}"`，用于后续按文档删除。

### 4.4 `VectorStore.similarity_search(kb_id, query, k=5) → list[dict]`

| 返回键 | 类型 | 说明 |
|--------|------|------|
| `id` | str | ChromaDB chunk ID（`doc_id:index`） |
| `content` | str | 分块原文 |
| `metadata` | dict | 含 `source`、`page`、`doc_id`、`chunk_index`、`chunk_total` |
| `distance` | float | 余弦距离，越小越相似 |

**已知限制：** `k` 最大 100。

### 4.5 `VectorStore.similarity_search_all(query, k) → list[dict]`

与 `similarity_search` 返回格式相同。遍历所有 `kb_*` collection，
合并后按 `distance` 升序排列取 top-k。

### 4.6 `VectorStore.delete_collection(kb_id) → bool`

删除整个知识库的 collection（包括所有向量数据）。

### 4.7 `VectorStore.get_chunks_by_doc_id(doc_id, kb_id) → list[dict]`

按文档 ID 查询所有分块。由分块预览端点调用。

---

## 5. 接口层：RAGChain 内部

### 5.1 RAGChain 拆分后的三个方法

Phase 3 将旧 `chat_with_citations()` 拆分为三个独立方法：

```python
async def search(query, kb_id) → list[dict]
```

检索阶段。集成 Hybrid Search（Dense + BM25 并行 + RRF 融合）+ 查询改写 + 意图路由。

| 参数 | 说明 |
|------|------|
| `kb_id == ""` | 调用 `similarity_search_all` 全量搜索 |
| `kb_id != ""` | 调用 `similarity_search` 限定知识库 |

```python
def rerank(query, results) → list[RAGContext]
```

精排阶段。调用 DashScope Reranker 对候选项重新排序。

```python
def stream_answer(query, contexts, history) → Generator[str]
```

生成阶段。逐 token 流式生成回答文本。

### 5.2 `RAGContext` 数据类

| 字段 | 类型 | 说明 |
|------|------|------|
| `content` | str | 分块原文 |
| `source` | str | 文件名（用户上传时的原始名称） |
| `page` | int | 所在页码（TXT 固定为 1） |
| `doc_id` | str | 文档 UUID |
| `chunk_id` | str | ChromaDB chunk ID |
| `score` | float | Reranker 相关性分数（越高越相关） |

---

## 6. 数据流全貌

```
用户提问 "苹果营收" (query)
  → GET /api/chat/stream?session_id=xxx&kb_id=yyy&query=苹果营收&trace_id=zzz
    → AppService 调用 RAGChain.search()
      → 查询改写 → 意图路由 → Hybrid Search (Dense + BM25 + RRF)
    → RAGChain.rerank()
      → DashScope Reranker 精排
    → RAGChain.stream_answer()
      → LLM 流式生成
    → SSE 事件流推送至前端:
        event: status (retrieving)
        event: status (reranking)
        event: status (generating)
        event: token (逐片推送)
        event: citation (去重)
        event: done
    → 后台持久化对话到 MySQL（含重试）
      → chat_manager.save_session_async()
      → chat_manager.save_messages_async()
```

```
文档上传
  → POST /api/kbs/documents/upload (multipart)
    → 返回 202 {doc_id, status: "parsing"}
    → 后台 asyncio.create_task(_process_document):
      1. parsing — 调用 parser 提取文本
      2. chunking — ParentChildChunker 分层切分 + 质量校验
      3. indexing — ChromaDB PersistentClient.add_chunks()
      4. ready — 更新 MySQL 状态
    → 前端轮询 POST /api/kbs/documents/status 直至 ready/failed
```

---

## 7. 接口事故档案

| 日期 | 问题 | 根因 | 修复 |
|------|------|------|------|
| 2026-06-25 | chat 时传了 `kb_name` 而非 `kb_id` | `handle_chat` 拿到 dropdown value，`create_kb` 返回的是 name，调用方混用 | 修复参数传递链路，统一使用 `kb_id` |
| 2026-06-25 | 容器重启后 embedding 维度不匹配 | ChromaDB `get_collection()` 不返回创建时设置的 `embedding_function` | 改用 `get_or_create_collection(name, embedding_function=...)` |
| 2026-06-25 | 重置数据工具 hanging | `reset_data.py` 创建新 `AppService()` 时触发了 DashScope 网络调用 | 改用 `docker exec` 子进程模式 |
| 2026-07-03 | ChromaDB 服务器未启动导致文件上传一直卡在 processing | docker-compose 缺少 chroma 服务，HttpClient 无法连接 | 改为 PersistentClient 内嵌模式，用 volume 缓存 ONNX 模型 |
| 2026-07-03 | text-embedding-v3 免费额度用尽导致 embedding 失败 | .env 配置为 v3 模型，免费额度过期 | 切回 text-embedding-v2 |
