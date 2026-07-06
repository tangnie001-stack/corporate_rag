# Corporate RAG API — Contract

## Base URL

Production: `http://localhost/api/`
Development: `http://localhost:8000/`
OpenAPI Docs: `http://localhost/api/docs`

## 通用说明

- 除 `/api/health` 和 `/api/chat/stream` 外，所有端点使用 POST 方法
- 请求头 `X-Trace-ID` 可选传，后端自动处理
- 响应头含 `X-Trace-ID`，可用于请求链路追踪

## Knowledge Bases

### List all KBs

`POST /api/kbs/list`
Content-Type: `application/json`
Body: `{}`

Response 200:
```json
{"code": "SUCCESS", "message": "操作成功", "data": [
  {"id": "uuid", "name": "库名称"}
]}
```

### Create KB

`POST /api/kbs`
Content-Type: `application/json`

Body:
```json
{"name": "库名称", "description": "可选描述"}
```

Response 201:
```json
{"code": "SUCCESS", "message": "操作成功", "data": {"id": "uuid", "created": true || false}}
```

### Delete KB

`POST /api/kbs/delete`
Content-Type: `application/json`
Body: `{"kb_id": "uuid"}`

Response 200:
```json
{"code": "SUCCESS", "message": "操作成功", "data": {"success": true, "message": "知识库已删除"}}
```

Response 404:
```json
{"code": "NOT_FOUND", "message": "知识库不存在", "data": null}
```

## Documents

### List documents

`POST /api/kbs/documents/list`
Content-Type: `application/json`
Body: `{"kb_id": "uuid"}`

Response 200:
```json
{"code": "SUCCESS", "message": "操作成功", "data": [
  {"id": "uuid", "filename": "name.pdf", "type": "pdf", "size": 1234, "status": "ready", "chunk_count": 10}
]}
```

### Upload document (async)

`POST /api/kbs/documents/upload`
Content-Type: `multipart/form-data`

Fields: `file` (PDF/DOCX/TXT), `kb_id` (uuid)

Response 202:
```json
{"code": "SUCCESS", "message": "操作成功", "data": {"doc_id": "uuid", "status": "processing", "filename": "name.pdf"}}
```

Response 413:
```json
{"code": "FILE_TOO_LARGE", "message": "File too large (max 10MB)", "data": null}
```

### Document processing status

`POST /api/kbs/documents/status`
Content-Type: `application/json`
Body: `{"kb_id": "uuid", "doc_id": "uuid"}`

Response 200:
```json
{"code": "SUCCESS", "message": "操作成功", "data": {"status": "ready", "chunk_count": 42, "progress": 100, "error": ""}}
```

Status values: `processing` / `ready` / `failed` / `not_found`

### Document chunk preview

`POST /api/kbs/documents/chunks`
Content-Type: `application/json`
Body: `{"kb_id": "uuid", "doc_id": "uuid", "page": 1, "page_size": 50}`

Response 200:
```json
{"code": "SUCCESS", "message": "操作成功", "data": {"items": [...], "total": 42, "page": 1, "page_size": 50}}
```

## Chat (SSE Streaming)

### Stream chat response

`GET /api/chat/stream?session_id={sid}&kb_id={kb_id}&query={question}&trace_id={traceId}`

Content-Type: `text/event-stream`

Events:

```
event: status
data: {"stage": "retrieving", "message": "正在检索相关文档..."}

event: token
data: {"token": "回答文本片段"}

event: citation
data: {"source": "文件名.pdf", "page": 15, "snippet": "内容摘要..."}

event: done
data: {}

event: error
data: {"error": "错误消息"}
```

## Health

### Health check

`GET /api/health`

Response 200:
```json
{"status": "ok"}
```
