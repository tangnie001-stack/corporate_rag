# API 路由层

提供 RAG 系统的 HTTP 接口，基于 FastAPI 实现，支持 SSE 流式推送。

## 文件说明

| 文件 | 职责 |
|---|---|
| `main.py` | FastAPI 应用入口、CORS、中间件注册、生命周期管理 |
| `routes/__init__.py` | 路由注册枢纽 |
| `routes/health.py` | 健康检查端点 `GET /api/health` |
| `routes/knowledge_base.py` | 知识库 CRUD：列表 `POST /api/kbs/list`、创建 `POST /api/kbs`、删除 `POST /api/kbs/delete` |
| `routes/documents.py` | 文档管理：列表 `POST /api/kbs/documents/list`、上传 `POST .../upload`、状态 `POST .../status`、分块 `POST .../chunks` |
| `routes/chat.py` | 流式 RAG 问答 `GET /api/chat/stream`，推送 status/token/citation/done 事件 |
| `routes/sessions.py` | 会话管理：列表 `POST /api/sessions/list`、消息 `POST .../messages`、删除 `POST .../delete` |

## 数据流

```
请求 → FastAPI router → AppService（编排层）→ infra（DB/LLM/Vector）→ SSE 响应
```
