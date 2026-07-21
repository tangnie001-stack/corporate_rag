## ADDED Requirements

### Requirement: API 统一 POST 方法
除 `/api/health`（GET）和 `/api/chat/stream`（GET, SSE）外，所有 API 端点 MUST 使用 POST 方法。路径参数 MUST 移入请求 body，每个端点使用独立的 Pydantic RequestBody 类。

#### Scenario: 知识库列表改为 POST
- **WHEN** 前端调用 `POST /api/kbs/list` 且 body 为 `{}`
- **THEN** 后端返回知识库列表，状态码 200

#### Scenario: 知识库删除改为 POST
- **WHEN** 前端调用 `POST /api/kbs/delete` 且 body 为 `{"kb_id": "uuid"}`
- **THEN** 后端删除指定知识库并返回成功，状态码 200

#### Scenario: 知识库删除参数校验
- **WHEN** 前端调用 `POST /api/kbs/delete` 且 body 缺少 `kb_id`
- **THEN** 后端返回 422 校验错误

#### Scenario: 文档列表改为 POST
- **WHEN** 前端调用 `POST /api/kbs/documents/list` 且 body 为 `{"kb_id": "uuid"}`
- **THEN** 后端返回指定知识库的文档列表，状态码 200

#### Scenario: 文档状态改为 POST
- **WHEN** 前端调用 `POST /api/kbs/documents/status` 且 body 为 `{"kb_id": "uuid", "doc_id": "uuid"}`
- **THEN** 后端返回文档处理状态

#### Scenario: 分块预览改为 POST
- **WHEN** 前端调用 `POST /api/kbs/documents/chunks` 且 body 为 `{"kb_id": "uuid", "doc_id": "uuid", "page": 1, "page_size": 50}`
- **THEN** 后端返回分页的分块预览数据

#### Scenario: 上传文档 kb_id 放入 FormData
- **WHEN** 前端调用 `POST /api/kbs/documents/upload` 且 FormData 包含 `kb_id` 和 `file` 字段
- **THEN** 后端接受上传并返回 202

#### Scenario: 会话列表改为 POST
- **WHEN** 前端调用 `POST /api/sessions/list`
- **THEN** 后端返回会话列表

#### Scenario: 会话消息改为 POST
- **WHEN** 前端调用 `POST /api/sessions/messages` 且 body 为 `{"session_id": "sid"}`
- **THEN** 后端返回指定会话的消息历史

#### Scenario: 会话删除改为 POST
- **WHEN** 前端调用 `POST /api/sessions/delete` 且 body 为 `{"session_id": "sid"}`
- **THEN** 后端删除指定会话

#### Scenario: 认证 verify 改为 POST
- **WHEN** 前端调用 `POST /api/auth/verify`（token 仍在 Cookie 中）
- **THEN** 后端验证 Cookie 并返回 `{"valid": true/false, "user_id": "..."}`

#### Scenario: 认证 anonymous 改为 POST
- **WHEN** 前端调用 `POST /api/auth/anonymous`
- **THEN** 后端返回 `{"user_id": "..."}` 并设置 Cookie

#### Scenario: Login 改为 JSON body
- **WHEN** 前端调用 `POST /api/auth/login` 且 Content-Type 为 `application/json`，body 为 `{"account": "xxx", "password": "yyy"}`
- **THEN** 后端验证并返回 `{"token": "...", "user_id": "..."}`

#### Scenario: 健康检查保留 GET
- **WHEN** 调用 `GET /api/health`
- **THEN** 返回 `{"status": "ok"}`，不走响应包装

#### Scenario: SSE 流保留 GET
- **WHEN** 前端建立 `EventSource('/api/chat/stream?session_id=...&kb_id=...&query=...')`
- **THEN** 后端返回 SSE 事件流
