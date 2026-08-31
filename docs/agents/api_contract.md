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
| `deep_thinking` | 深度思考开关（可选，默认 `false`）：`true` 时 agent 主 LLM 以思考模式调用（`enable_thinking=true`）；`false` 显式关闭。来自 `chat-thinking-toggle` capability |
| `trace_id` | 链路追踪 ID（可选） |

> **落库时机（streaming-decouple M1）**：请求开始（per-session Redis 锁获取后）即同步写 user 消息到 MySQL（`svc.save_user_async`），session 创建幂等；端点方法**保持 GET**（GET→POST 迁移推迟到 streaming-decouple M3.4，届时与前端 fetchStream 一同落地，避免 M1 破坏 EventSource）。Redis 的 user 写入仍保留在 `agent_service.stream_chat` 内（发生在 `get_history_async()` 之后，避免当前 query 作为历史进 prompt）。

事件流（按推送顺序，不含追问路径）：

```json
event: status
data: {"stage": "agent", "message": "正在思考..."}

event: status
data: {"stage": "retrieve", "message": "正在检索相关文档..."}

event: status
data: {"stage": "retrieve", "message": "检索完成，正在分析..."}

event: status
data: {"stage": "web_search", "message": "正在联网搜索..."}

event: status
data: {"stage": "web_search", "message": "联网搜索完成，正在分析..."}

event: token
data: {"token": "回答文本片段"}

event: citation
data: {"source": "文件名.pdf", "page": 15, "snippet": "内容摘要...", "score": 0.95, "highlighted_snippet": "<mark>高亮</mark>内容", "kind": "kb"}

event: model_info
data: {"model": "模型名", "is_fallback": false}

event: done
data: {"trace_id": "trace_xxx"}

event: error
data: {"error": "错误消息"}
```

追问路径（~~当 classify 检测到缺失实体时~~ ⚠️ 已退役，agent 化后由 `ask_user` 事件 + `POST /chat/clarify-answer` 接管，见下文 2.3.2 与 ask_user 事件详情）：

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
| `status` | agent 循环按事件类型接线 | stage 取值：`agent`（on_chat_model_start "正在思考..."）、`retrieve`（on_tool_start/end "正在检索相关文档..." / "检索完成，正在分析..."）、`web_search`（on_tool_start/end "正在联网搜索..." / "联网搜索完成，正在分析..."，KB 不达标时走 search_web 兜底才出现） |
| `token` | LLM 生成中 | LLM 生成文本片段，前端逐段追加 |
| **`reasoning`** | **agent 节点 LLM 流式输出思考增量（enable_thinking=true 且模型返回 reasoning_content，经 ChatQwenWithReasoning 提取）** | **思考过程增量（data: {"delta": "..."}），前端累积渲染 Think 折叠行；每轮 LLM 调用一个，默认收起；收到正文 token/状态/ask_user/abstention/done 时定型** |
| `citation` | format 节点完成 | 引用来源，按 source+page 去重；data 含 `kind`（`kb` 知识库 / `web` 网络搜索，默认 `kb`），前端按来源类型区分展示 |
| **`clarification`** | ~~classify 检测到缺失实体~~ | **已退役**：classify 已删，无预判来源，不再生产，前端已由 `ask_user` 接管 |
| **`ask_user`** | **ask_user 工具被调用（agent 需要用户补充信息）** | **问题卡片事件，前端 composer 接管输入区；提交答案后同流续答** |
| **`abstention`** | **模型输出命中拒答标记（如"未在文档中找到"）** | **abstention 标识 + 转人工提示文案，前端展示转人工入口；判定只看 answer 文案，与是否触发检索无关** |
| `model_info` | agent 循环末次 LLM 调用完成（on_chat_model_end 捕获） | 实际使用的模型名和 fallback 状态 |
| `done` | 流结束 | 流结束标记；携带 `trace_id`（当前请求全链路追踪 ID），前端记录后随答案反馈回传 |
| `error` | 异常 | 异常时推送，无 retry 机制 |

#### 2.3.2 `POST /api/chat/clarify-answer → 200 | 404`

提交 ask_user 问题答案，解析挂起的澄清 Future，使 agent 继续执行。

```json
{"session_id": "sid", "answers": [{"id": "q1", "selected": ["2024年"], "custom": ""}]}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `session_id` | str | 会话 ID，定位挂起的 ask_user Future |
| `answers` | list | 用户答案列表，每条含 id/selected（可含 custom），原样写入 Future 作为 ask_user 返回值 |

Success:
```json
{"code": "SUCCESS", "message": "操作成功", "data": true}
```

**404 语义**：该澄清问题已超时或不存在（查无 Future 或 Future 已结束），
detail 为 `CLARIFY_ANSWER_NOT_FOUND_TEXT`（"澄清问题已过期或不存在"）。`pop` 保证单次消费。

> ⚠️ `POST /chat/clarify-answer` 与 SSE 是独立 HTTP 请求，contextvar 不跨请求，
> 答案经进程级 `pending_asks` 注册表（session_id → asyncio.Future）送达。
> 解析成功后，答案文本还会作为 user 消息写入 Redis 对话历史（chat_manager.add_message_async），
> 与 `stream_chat` 入口写入原始 query 的轨道并存，保证跨 turn 上下文不丢。

### `clarification` 事件详情

> ⚠️ **已退役**：classify 已删（agent 化改造），无预判来源，不再生产；
> 类定义与序列化逻辑已从 `src/utils/sse.py` 删除。以下内容仅供历史追溯。

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

### `ask_user` 事件详情

```json
event: ask_user
data: {"type": "ask_user", "questions": [{"id": "q1", "question": "您想查询哪一年的数据？", "options": ["2024年", "2023年", "其他"], "multi_select": false}]}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `type` | str | 固定 `"ask_user"`，前端据此路由到追问卡片 |
| `questions` | list | 问题卡片列表；每条含 `id`（答案回显）/`question`（问题文本）/`options`（候选）/`multi_select`（是否多选）。`options` 来源随 `ASK_USER_MODE_DSH`：**默认 `true`（dsh 模式）系统不注入 KB dimension 候选，直接用模型自带的 options（可为空 = 纯文本问题）**；`false`（dual 模式）模型自带优先，无 options 时按 dimension 从 KB 聚合真实候选注入（防编造），KB 无候选时兜底 `SUGGESTIONS_MAP` |

> 提交答案走 `POST /api/chat/clarify-answer`，提交后 SSE 保持连接、同流续答；
> 单 turn 最多 `MAX_ASK_PER_TURN`（2）次调用，超时（`ASK_USER_TIMEOUT` 120s）返回超时文案继续。

### `abstention` 事件详情

```json
event: abstention
data: {"type": "abstention", "message": "未在文档中找到相关数据，可尝试转人工咨询"}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `type` | str | 固定 `"abstention"`，前端据此路由到转人工提示 |
| `message` | str | 转人工提示文案（`SSEAbstentionEvent` 默认值） |

> 触发：agent 迭代结束后检索上下文为空或答案命中拒答标记（`_is_abstention`）；
> 文案固定为转人工引导，与拒答检测（`SSEInteractionTexts.ABSTENTION_MARKERS`）配套。

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
    "status": "complete",
    "created_at": "2026-07-03T12:00:00"
  },
  {
    "role": "assistant",
    "content": "回答文本",
    "sources": ["文件名.pdf (第3页)", "文件2.docx (第5页)"],
    "status": "interrupted",
    "created_at": "2026-07-03T12:00:05"
  }
]}
```

`status` 取值 `complete` / `interrupted`，标识消息是否完整生成（前端据此标记被中断的回答）。

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

#### 2.4.4 `GET /api/sessions/events?session_id={sid}&after_seq={seq}`

SSE 断点续接：前端刷新页面后重放进行中生成的未消费事件，再 tail 新事件直到终态。

| 参数 | 说明 |
|------|------|
| `session_id` | 会话 ID |
| `after_seq` | 起始 seq（已消费的最大 seq，默认 0 = 从头回放） |

Content-Type: `text/event-stream`。事件格式与实时流（2.3.1）一致：
先回放缓冲中 `seq > after_seq` 的事件，随后 tail 新事件直到 `done` / `error`
终态；tail 期间无新事件超过 180s（`_subscribe_buffer` 默认 `max_idle`）
推送续传超时 `error` 事件后结束。缓冲不存在时立即返回单个 `done` 事件。

```json
event: token
data: {"token": "回答文本片段"}

event: done
data: {"trace_id": "trace_xxx"}
```

会话不存在或无权访问返回 404（`SESSION_NOT_FOUND`），与 2.4.2/2.4.3 一致。

#### 2.4.5 `GET /api/sessions/task-status?session_id={sid}`

查询会话生成任务状态，供前端判断是否在途生成、是否已完成。

| 参数 | 说明 |
|------|------|
| `session_id` | 会话 ID |

Success:
```json
{"code": "SUCCESS", "message": "操作成功", "data": {"status": "generating", "buffer_seq": 3}}
```

| 键 | 类型 | 说明 |
|----|------|------|
| `status` | str | `generating`（缓冲存在且无终态，生成中）/ `completed`（缓冲有 done/error 终态，或 MySQL 已有 assistant 消息）/ `idle`（无缓冲且无 assistant 消息） |
| `buffer_seq` | int\|null | 当前缓冲最大事件序号，仅缓冲存在时返回 |

判定顺序：先查进程内缓冲（`streaming_manager`），无缓冲时回退查 MySQL 消息
（`svc.get_messages` 是否存在 `role=assistant`）。会话不存在或无权访问返回 404
（`SESSION_NOT_FOUND`），与 2.4.2/2.4.3 一致。

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

### 2.7 答案反馈

#### 2.7.1 `POST /api/feedback → 200`

保存用户对单条答案的评分与可选评论。Body:

```json
{"session_id": "sid", "message_index": 2, "rating": "positive", "comment": "回答准确", "trace_id": "trace_xxx"}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `session_id` | str | 会话 ID |
| `message_index` | int | 会话内消息序号（前端消息数组索引，从 0 起），非数据库 ID |
| `rating` | str | `positive` / `negative`，非法值返回 422 |
| `comment` | str | 用户评论，可选，默认空串 |
| `trace_id` | str | 全链路追踪 ID（前端从 SSE `done` 事件记录并回传，用于还原该答案的生成链路），可选，默认空串 |

Success:
```json
{"code": "SUCCESS", "message": "操作成功", "data": true}
```

⚠️ 落库失败只记日志不报错（容错），前端始终收到成功响应。
⚠️ `message_index` 是前端消息数组的序号语义，存到 `feedback` 表原样保留，
不解析到 `conversation_history` 行。同一 (session_id, message_index) 可重复反馈（追加记录）。

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

节点名称和输出字段常量定义在 `src/agents/graph/state.py` 的 `LangGraphNode` 类中。

| 节点 | 节点名 (NAME) | 输出字段 | 说明 |
|------|--------------|---------|------|
| **kb_router** | `"kb_router"` | `_resolved_kb_ids: list[str] \| None` | KB 路由穿透/智能匹配 |
| **agent** | `"agent"` | `messages`, `_agent_iterations` | agent 模型节点：bind_tools 调 LLM，可发起工具调用（retrieve_kb / ask_user / search_web） |
| **agent_tools** | `"agent_tools"` | `messages`（ToolMessage 追加） | ToolNode 执行工具，错误回喂；工具集：`retrieve_kb`（KB 混合检索）/ `search_web`（Tavily 联网搜索兜底，KB 不达标时补充知识库外事实）/ `ask_user`（澄清追问） |
| **agent_finalize** | `"agent_finalize"` | `answer`, `tool_contexts` | 循环结束提取末次 AIMessage content → `answer`，读入 `tool_contexts` |
| **format** | `"format"` | `citations: list[dict]` | 去重引用列表 |

### 5.2 agent 循环（model ↔ tools 条件循环）

图结构为 `kb_router → agent → (agent_tools | agent_finalize) → format`：

```
kb_router → agent（LLM + bind_tools）
              │ 有 tool_calls 且未超限
              ▼
         agent_tools（ToolNode 执行 retrieve_kb / ask_user）
              │ 工具结果回填 messages
              ▼
            agent（下一轮 LLM）
              │ 无 tool_calls / 达迭代上限
              ▼
         agent_finalize（提取 answer + tool_contexts）
              ▼
            format（引用去重）
```

- 迭代上限 `MAX_AGENT_ITERATIONS`（`src/config/const.py`），超限强制收尾
- 工具不能写 state：检索上下文累积到 `RequestContext.tool_contexts`（contextvar），由 `agent_finalize` 读入 `state.tool_contexts`
- per-request 对象（澄清通道 queue / abort 信号 / ask_count）经 contextvar（`current_request_ctx`）传递，并发 session 天然隔离
- 终止条件：`route_agent` 判断末条消息无 `tool_calls` 或达迭代上限 → `agent_finalize`

### 5.3 AgentState 关键字段

`AgentState`（`src/agents/graph/state.py`）为 agent 循环状态：

| 字段 | 类型 | 说明 |
|------|------|------|
| `session_id` / `kb_id` / `query` | str | 输入：会话 / 知识库 / 用户问题 |
| `messages` | `list[BaseMessage]` | 模型可见消息（`add_messages` 追加语义） |
| `tool_contexts` | `list[RAGContext]` | retrieve_kb 累积检索上下文（引用溯源） |
| `_agent_iterations` | int | 循环迭代计数（护栏） |
| `_max_agent_iterations` | int | 迭代上限（默认 `MAX_AGENT_ITERATIONS`） |
| `answer` | str | LLM 生成的完整回答 |
| `citations` | list[dict] | 去重引用列表 |
| `_resolved_kb_ids` | list[str] \| None | kb_router 路由结果（None = 未路由/降级） |
| `_history` | list[ChatMessage] | 对话历史（初始注入数据源，agent 节点入口截断） |

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

### 6.1 正常回答路径（agent 循环）

```
用户提问 "2024年公司营收多少" (query)
  → GET /api/chat/stream?session_id=xxx&kb_id=yyy&query=...&trace_id=zzz
    → kb_router: 穿透/跨库路由 → _resolved_kb_ids
    → agent 循环:
        1. agent: LLM 思考 → 调用 retrieve_kb
        2. agent_tools: 检索（hybrid Dense + BM25 + RRF 融合 → rerank 精排 → format_context）
        3. agent: 基于检索上下文生成回答（含引用编号 [n]）
           （检索不达标时可在循环内调用 search_web 联网兜底，产出 kind=web 引用）
           （信息不足时可在循环内调用 ask_user 追问，见 6.2）
    → agent_finalize: 提取 answer + tool_contexts
    → format_node: 去重引用列表
    → SSE 事件流推送至前端:
        event: status (agent, "正在思考...")
        event: status (retrieve, "正在检索相关文档...")
        event: status (retrieve, "检索完成，正在分析...")
        event: token (逐片推送)
        event: citation (去重)
        event: model_info (模型名 + fallback 状态)
        event: done
    → 后台任务收尾（_run_with_finalize）落库 assistant 到 MySQL（best-effort，失败仅记日志）
      → chat_manager.save_assistant_async()（仅写 assistant 消息，status=complete/interrupted；session + user 消息已在请求开始时写入，见上文「落库时机」）
```

### 6.2 澄清路径（ask_user 工具）

```
用户提问 "营收多少"（缺关键信息，agent 判断需澄清）
  → GET /api/chat/stream?session_id=xxx&kb_id=yyy&query=营收多少&trace_id=zzz
    → kb_router → agent 循环:
        1. agent: LLM 判断信息不足 → 调用 ask_user
        2. agent_tools: ask_user 推送问题 → SSEAskUserEvent，挂起等待（ASK_USER_TIMEOUT）
        3. 前端 composer 渲染问题表单（输入区接管），用户提交 POST /api/chat/clarify-answer
        4. 答案 resolve 挂起 Future → 作为工具结果回喂 → 同一 turn 继续
    → agent: 基于答案 + 检索上下文生成回答
    → SSE 事件流:
        event: status (agent)
        event: ask_user (问题卡片；options 来源随 ASK_USER_MODE_DSH：默认 true 用模型自带，
              不做 KB dimension 注入；false 时由 KB 实体注入真实候选)
        event: status (retrieve, 用户回答后继续检索)
        event: token / citation / model_info / done
```

- 用户答案到达即写 Redis 历史（`POST /api/chat/clarify-answer`，见 2.3.2）
- 超时/断连：ask_user Future 以超时/取消 resolve，agent 收尾；用户超时后提交返回 404
- 澄清答案在模型上下文是 ToolMessage（同 turn），在历史是 user 消息（跨 turn 上下文），两轨并存

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
