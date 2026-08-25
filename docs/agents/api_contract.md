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

事件流（按推送顺序，不含追问路径）：

```json
event: status
data: {"stage": "classify", "message": "正在分析查询类型..."}

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

event: model_info
data: {"model": "模型名", "is_fallback": false}

event: done
data: {}

event: error
data: {"error": "错误消息"}
```

追问路径（当 classify 检测到缺失实体时）：

```json
event: status
data: {"stage": "classify", "message": "正在分析查询类型..."}

event: clarification
data: {"type": "entity_completion", "question": "请问您想查询哪一年的数据？", "missing_entities": [{"type": "year", "question": "请问您想查询哪一年的数据？"}], "suggestions": ["2023年", "2024年", "其他"]}

event: done
data: {}
```

| 事件 | 触发条件 | 说明 |
|------|---------|------|
| `status` | 节点开始 | 四阶段状态（classify / retrieving / reranking / generating） |
| `token` | LLM 生成中 | LLM 生成文本片段，前端逐段追加 |
| `citation` | rerank 节点完成 | 引用来源，按 source+page 去重 |
| **`clarification`** | **classify 检测到缺失实体；检索空 abstention 引导（KB 有候选时）** | **追问事件，前端展示追问气泡 + 快捷选项** |
| `model_info` | generate 节点完成 | 实际使用的模型名和 fallback 状态 |
| `done` | 流结束 | 流结束标记（追问路径也在 clarification 后立即发送） |
| `error` | 异常 | 异常时推送，无 retry 机制 |

### `clarification` 事件详情

```python
# 数据结构（src/utils/sse.py: SSEClarificationEvent）
{
  "type": "entity_completion" | "intent_clarification" | "no_data_guidance",
  "question": str,              # 追问文本，如 "请问您想查询哪一年的数据？"
  "missing_entities": [dict],   # [{"type": "year", "question": "请问您想查询哪一年的数据？"}]
  "suggestions": [str],         # 快捷选项，如 ["2023年", "2024年", "其他"]
  "questions": [dict]           # 批量追问列表 [{"type", "question", "suggestions"}, ...]；空则省略，兼容旧前端
}
```

`suggestions` 根据缺失实体类型从 `SUGGESTIONS_MAP` 映射：

| 实体类型 | 快捷选项 |
|---------|---------|
| `year` | `["2023年", "2024年", "其他"]` |
| `quarter` | `["一季度", "二季度", "三季度", "四季度"]` |
| `month` | `["1月", "12月", "其他"]` |
| `company` | `["腾讯", "阿里巴巴", "其他"]` |
| `metric` | `["营收", "利润", "毛利率", "其他"]` |
| `default` | `["请补充说明", "其他"]` |

### 追问流程关键约束

- 追问后用户回答**必须复用同一 `session_id`**，graph 通过 `_history` 获取上轮实体信息
- 追问期间前端不应显示"回答结束"或"done"相关提示
- 追问路径不产生 `token`、`citation` 事件

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

## 5. 接口层：LangGraph 图内部

### 5.1 节点定义与输出字段

节点名称和输出字段常量定义在 `src/config/const.py` 的 `LangGraphNode` 类中。

| 节点 | 节点名 (NAME) | 输出字段 | 说明 |
|------|--------------|---------|------|
| **kb_router** | `"kb_router"` | `_resolved_kb_ids: list[str] \| None` | KB 路由穿透/智能匹配 |
| **classify** | `"classify"` | `intent`, `extracted_entities`, `missing_entities`, `classification_confidence` | 三层意图路由 |
| **rewrite** | `"rewrite"` | `rewritten_queries: list[str]`, `rewritten_query: str` | 查询改写（仅 medium/complex） |
| **retrieve** | `"retrieve"` | `retrieval_results: list[ChunkResult]` | 混合检索（Dense + BM25） |
| **rerank** | `"rerank"` | `contexts: list[RAGContext]` | DashScope Reranker 精排 |
| **generate** | `"generate"` | `answer: str`, `model_used: str` | LLM 流式生成 |
| **format** | `"format"` | `citations: list[dict]` | 去重引用列表 |

### 5.2 classify_node 三层路由架构

`make_classify_node(llm)` 创建的 classify 节点内部使用 `QueryRouter` 的三层架构：

```
用户 query
    │
    ├── L0: 问候/长度检测
    │   "你好/谢谢/≤2字符" → 直接返回 simple，不调用下游
    │
    ├── L1: EntityExtractor（正则实体提取，0 LLM 成本）
    │   支持实体类型: year / quarter / month / metric / money / percentage / company
    │   输出: list[ExtractedEntity]
    │   源码: src/infra/search/entity_extractor.py
    │
    ├── L2: ComplexityScorer（关键词加权评分）
    │   关键词按 LOW(1) / MEDIUM(2) / HIGH(3) / VERY_HIGH(4) 加权
    │   实体数量 +0.5/个，多条件关键词 +2
    │   输出: float 分数（仅作为 LLM hint，不做判决）
    │   源码: src/infra/search/complexity_scorer.py
    │
    └── L3: LLM Classifier（一次 LLM 调用）
       输入: query + entities + score + history
       温度: CLASSIFIER_TEMPERATURE（默认 0.1）
       输出 JSON:
       {
         "route": "simple|medium|complex",
         "missing_entities": [{"type": "year", "question": "请问您想查询哪一年的数据？"}],
         "confidence": 0.92
       }
       源码: src/infra/search/query_router.py
```

`route_by_intent` 条件边根据 state 决定下一步：
- `missing_entities` 非空 → `"clarify"` → **END**（不走 rewrite/retrieve/generate）
- `route == "simple"` → `LangGraphNode.Retrieve.NAME`
- `route == "medium"` → `LangGraphNode.Rewrite.NAME`
- `route == "complex"` → `LangGraphNode.Rewrite.NAME`

### 5.3 AgentState 新增字段（意图理解）

自 Phase 4 意图路由升级后，`AgentState` 新增以下字段：

| 字段 | 类型 | 默认值 | 来源 | 说明 |
|------|------|--------|------|------|
| `extracted_entities` | `list[dict]` | `[]` | EntityExtractor L1 | 正则提取的实体列表 |
| `missing_entities` | `list[dict]` | `[]` | LLM L3 | LLM 标记的缺失实体（如 `[{"type": "year", "question": "请问您想查询哪一年的数据？"}]` |
| `classification_confidence` | `float` | `0.0` | LLM L3 | LLM 置信度（LLM 输出 key="confidence"） |

### 5.4 `RAGContext` 数据类

| 字段 | 类型 | 说明 |
|------|------|------|
| `content` | str | 分块原文 |
| `source` | str | 文件名（用户上传时的原始名称） |
| `page` | int | 所在页码（TXT 固定为 1） |
| `doc_id` | str | 文档 UUID |
| `chunk_id` | str | ChromaDB chunk ID |
| `score` | float | Reranker 相关性分数（越高越相关） |
| `entities` | dict | 业务实体（来自 chunk.metadata，如 `{"company": "xx", "report_period": "2024"}`） |

`to_prompt_text()` 渲染喂给生成模型的上下文文本，格式：

```
来源: {source} (第{page}页)
{实体标签}: {值} {实体标签}: {值}   ← 仅当实体非空时渲染，按 ENTITY_RENDER_ORDER 顺序
内容: {content}
```

实体锚点（如文件名/期间）随上下文一并喂给生成模型，帮助对齐事实；生产 prompt 与 RAGAS 评估的 NLI 上下文共用此格式（见 `src/rag/context.py`），评估时保证与线上生成看到完全一致的上下文。

---

## 6. 数据流全貌

### 6.1 正常回答路径（无追问）

```
用户提问 "2024年公司营收多少" (query)
  → GET /api/chat/stream?session_id=xxx&kb_id=yyy&query=...&trace_id=zzz
    → kb_router: 穿透/跨库路由 → _resolved_kb_ids
    → classify_node: QueryRouter 三层
        L0: 问候拦截（跳过）
        L1: EntityExtractor → [{year:2024, metric:营收}]
        L2: ComplexityScorer → score=2.5 (medium)
        L3: LLM → route=medium, missing=[], confidence=0.95
    → route_by_intent: "medium" → rewrite
    → rewrite_node: 查询改写（输出 rewritten_queries 多查询列表）
    → retrieve_node: Hybrid Search (Dense + BM25 + RRF)
    → rerank_node: DashScope Reranker 精排（retrieve → rerank 直连，grader 已删除）
    → generate_node: LLM 流式生成
    → format_node: 去重引用列表
    → SSE 事件流推送至前端:
        event: status (classify)
        event: status (retrieving)
        event: status (reranking)
        event: status (generating)
        event: token (逐片推送)
        event: citation (去重)
        event: model_info (模型名 + fallback 状态)
        event: done
    → 后台持久化对话到 MySQL（含重试）
      → chat_manager.save_session_async()
      → chat_manager.save_messages_async()
```

### 6.2 追问路径（缺失实体）

```
用户提问 "营收多少"（缺年份，无历史上下文）
  → GET /api/chat/stream?session_id=xxx&kb_id=yyy&query=营收多少&trace_id=zzz
    → kb_router → classify_node: QueryRouter 三层
        L0: 问候拦截（跳过）
        L1: EntityExtractor → [{metric:营收}]
        L2: ComplexityScorer → score=1.5
        L3: LLM → route=medium,
                    missing=[{type:"year", question:"请问您想查询哪一年的数据？"}],
                    confidence=0.85
    → route_by_intent: missing_entities 非空 → "clarify" → END
    → 图不执行 rewrite/retrieve/generate，直接结束
    → agent_service.stream_chat 在 classify CHAIN_END 捕获 missing_entities
    → 循环结束后发送 SSEClarificationEvent
    → SSE 事件流:
        event: status (classify)
        event: clarification (追问事件 + 快捷选项)
        event: done

用户补充信息 "2024年"（同 session_id）
  → GET /api/chat/stream?session_id=xxx&kb_id=yyy&query=2024年&trace_id=zzz
    → _history 包含上轮对话
    → classify_node: LLM 从 history 推断 year=2024，metric=营收
        missing=[], route=medium
    → 正常路径（同 6.1）
```

### 6.3 文档上传路径
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

---

## 8. CLI 接口：eval_ragas 命令行

### 8.1 `python -m src.cli.eval_ragas`

RAGAS 评估与测试集生成命令行。

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `--kb-id` | str | 是* | 知识库 UUID（与 `--list-kbs` 互斥） |
| `--generate` | flag | 否 | 生成测试集模式（从文档自动生成 QA 对） |
| `--size` | int | 否 | 生成测试集的 QA 对数（默认: `RAGAS_TEST_SIZE`） |
| `--model` | str | 否 | 生成测试集用的 LLM 模型名 |
| `--testset-version` | int | 否 | 指定测试集版本号（默认取最新版本） |
| `--gate` | flag | 否 | 评估后检查质量门禁指标 |
| `--output` | str | 否 | CSV 输出路径（默认: `data/reports/ragas_eval_<timestamp>.csv`） |
| `--session-id` | str | 否 | 评估用的会话 ID（默认: `ragas_eval_session`） |
| `--list-kbs` | flag | 否 | 列出所有可用知识库后退出 |

> `*` `--kb-id` 除 `--list-kbs` 模式外为必填。

使用示例：
```bash
# 生成测试集
python -m src.cli.eval_ragas --kb-id <uuid> --generate --size 20

# 评估（最新测试集）
python -m src.cli.eval_ragas --kb-id <uuid>

# 评估（指定测试集版本）
python -m src.cli.eval_ragas --kb-id <uuid> --testset-version 4

# 评估 + 质量门禁
python -m src.cli.eval_ragas --kb-id <uuid> --gate

# 列出知识库
python -m src.cli.eval_ragas --list-kbs
```

### 8.2 测试集 JSON 格式

位置：`data/ragas/testset_{kb_id}_v<N>.json`

```json
{
  "metadata": {
    "kb_id": "知识库UUID",
    "kb_name": "知识库名称",
    "doc_names": ["文档名.pdf"],
    "version": 1,
    "generated_at": "ISO 8601",
    "llm_model": "模型名",
    "testset_size": 20,
    "ragas_version": "x.y.z",
    "doc_ids": ["文档UUID"]
  },
  "samples": [
    {
      "user_input": "问题",
      "reference": "参考答案",
      "reference_contexts": ["上下文1", "上下文2"],
      "synthesizer_name": "合成器名"
    }
  ]
}
```

### 8.3 质量门禁阈值

| 指标 | 阈值 |
|------|------|
| faithfulness | 0.85 |
| answer_relevancy | 0.85 |
| context_precision | 0.80 |
| context_recall | 0.70 |

综合加权分 = 0.3×faithfulness + 0.3×context_recall + 0.2×context_precision + 0.2×answer_relevancy
综合分 ≥ 0.70 视为通过。
