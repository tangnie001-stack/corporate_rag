## Why

Phase 1 MVP 已跑通，核心 RAG 链路验证完成。进入 Phase 2 增强系统期，有三个紧迫问题：

1. **Gradio 界面无法满足演示需求** — 界面简陋，知识库管理和聊天挤在一个页面，拿出去给客户看第一印象差
2. **LangChain 0.3.x 面临版本过期** — LangChain 1.x 已发布至 1.4.8，API 变动大，现在不升后面代码积累越多成本越高
3. **缺少可观测性** — 功能修缮期需要 tracing 辅助调试，定位问题全靠打 log 效率低

## What Changes

- **FastAPI 替换 Gradio** — 后端架构重构，从 Gradio Blocks 切换到 FastAPI + SSE 流式输出，为前后端分离奠定基础
- **Nginx + 静态前端** — 新增 Nginx 容器做反向代理和静态文件服务，前端用纯 HTML/CSS/JS 实现
- **LangChain 全部升级到 1.x 线** — core/openai/text-splitters/community 四件套 + 新增 langchain-dashscope 独立包
- **Langfuse Tracing 接入** — 自托管可观测性，CallbackHandler + @observe() 覆盖全链路

## Capabilities

### New Capabilities

- `api-layer`: FastAPI REST API 层，替换 Gradio 事件处理。路由包括 KB CRUD、文档上传/列表、SSE 流式聊天
- `nginx-proxy`: Nginx 反向代理 + 静态文件服务，实现生产级前端服务架构
- `llm-tracing`: Langfuse 自托管可观测性，tracing 覆盖 RAG 全链路（检索→重排序→生成）
- `html-frontend`: 知识库管理独立页面 + 聊天页面，现代化视觉设计，EventSource 流式接收

### Modified Capabilities

- `rag-generation`（从 Phase 1 继承）: 升级到 LangChain 1.x，`models.py` 改用 langchain-dashscope 包
- `infrastructure`（从 Phase 1 继承）: Docker Compose 增加 nginx/postgres/langfuse 服务
- `chat-interface`（从 Phase 1 继承）: 从 Gradio Blocks 迁移到 HTML 前端 + FastAPI 后端

### Removed Capabilities

- Gradio 5.x UI（`chat-interface` 的实现方式从 Gradio 改为 HTML 前端，Gradio 代码移至 `old/`）

## Scope

### In Scope（Phase 2 Step 0）

- FastAPI REST API 层（全部路由）
- Nginx 反向代理 + 前端文件服务
- LangChain 0.3 → 1.x 升级（所有包）
- DashScope 模型从 langchain-community 迁移到 langchain-dashscope
- Langfuse Tracing（CallbackHandler + @observe() + Docker）
- HTML 前端基础版（KB 管理页 + 聊天页，含 SSE 流式）

### Out of Scope（Phase 2 Step 1+ 或 Phase 3）

- BM25 混合检索（Phase 2 Step 1）
- 意图路由（小 LLM 分类，Phase 2 Step 1）
- 测试覆盖率 ≥ 70%（Phase 2 Step 1，Harness Engineering）
- CI/CD GitHub Actions（Phase 2 Step 1）
- 多轮对话上下文融合（Phase 2 Step 2）
- Chunk 详情查看（Phase 3）

## Risks

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| LangChain 1.x API 兼容性 | 中 | 中 | langchain-community 无 1.x 版，需确认 0.4.2 兼容 langchain-core 1.x；DashScope 有独立包 |
| SSE 流式在前端实现复杂 | 低 | 中 | 标准 EventSource API，后端 ~20 行，前端 ~30-50 行 JS |
| Langfuse 首次启动需手动配 Key | 高 | 低 | 写进 README 启动步骤，一次性的 |
| Gradio 删除后 CLI 工具受影响 | 低 | 高 | CLI 直接调业务层，不经过 app.py，不受影响 |
| 前端质量靠 LLM 生成不稳定 | 中 | 中 | 用成熟 Admin 模板做基底，LLM 做填充和调整 |
