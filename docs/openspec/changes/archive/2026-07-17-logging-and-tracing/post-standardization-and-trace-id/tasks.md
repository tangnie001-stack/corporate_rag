## 1. 基础设施 — Trace Context

- [ ] 1.1 新建 `src/infra/llm/trace_context.py`，定义 `current_trace_id` 和 `current_user_id` 两个 contextvar

## 2. Trace ID 后端实现

- [ ] 2.1 新建 `src/middleware/trace_id.py`，实现 TraceID 中间件（X-Trace-ID → ?trace_id → uuid4 三级兜底）
- [ ] 2.2 修改 `src/api/main.py`，注册 TraceID 中间件（CORS 之后，ResponseEnvelope 之前）
- [ ] 2.3 修改 `src/infra/llm/langfuse_tracing.py`，`start_trace()` 读取 `current_trace_id.get()` 作为外部 trace_id
- [ ] 2.4 修改 `src/middleware/auth.py`，识别用户后同步设置 `current_user_id.set(uid)`
- [ ] 2.5 配置 loguru filter 自动从 `current_trace_id` 读取 trace_id 注入日志格式

## 3. POST 改造 — 后端路由

- [ ] 3.1 重构 `src/api/routes/auth.py`：login 改为 JSON body；verify/anonymous 改为 POST
- [ ] 3.2 重构 `src/api/routes/knowledge_base.py`：list/delete 拆分路径，kb_id 移入 body，新增 Pydantic models
- [ ] 3.3 重构 `src/api/routes/documents.py`：4 个路由去掉路径参数，参数移入 body，kb_id 加入 upload FormData，新增 Pydantic models
- [ ] 3.4 重构 `src/api/routes/sessions.py`：list/messages/delete 拆分路径，sid 移入 body，新增 Pydantic models

## 4. 前端改造

- [ ] 4.1 更新 `nginx/html/js/api.js` + `nginx/html/index.html` + `nginx/html/chat.html` 中的 API 调用：生成 trace_id 注入 X-Trace-ID；所有调用按新路径/方法/body 更新；含 4 处直接 fetch（verify×2、status×1、chunks×1）
- [ ] 4.2 修改 `nginx/html/js/chat.js`：SSE URL 追加 `&trace_id=xxx`
- [ ] 4.3 修改 `nginx/html/login.html`：login fetch 改为 `Content-Type: application/json`
- [ ] 4.4 前端 JS 版本号 bump：`api.js?v=10→11`，`chat.js?v=1→2`

## 5. 测试更新

- [ ] 5.1 更新 `tests/api/test_knowledge_base.py`：3 个测试的方法/路径/body（list、delete exists、delete not found）
- [ ] 5.2 更新 `tests/api/test_documents.py`：2 个测试的方法/路径/body（list、upload）

## 6. 接口契约与文档

- [ ] 6.1 更新 `docs/api-contract.md`：所有端点方法、路径、参数、body 按新规修订
- [ ] 6.2 更新 `docs/api_contract.md`：同步更新接口定义
- [ ] 6.3 更新 `src/api/README.md`：同步端点描述
- [ ] 6.4 更新 `src/chat_manager.py`：`cleanup_session` docstring 中 DELETE URL 注释

## 7. 验证

- [ ] 7.1 运行 `pytest tests/ -v` 确保全部通过
- [ ] 7.2 运行 `ruff check .` 确保无 lint 错误
- [ ] 7.3 清除遗留 `print()`、TODO 和调试代码
