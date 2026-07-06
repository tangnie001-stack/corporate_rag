# Phase 2 Step 0 — 基础设施重构任务清单

## 任务总览

| ID | 任务 | 预估 | 前置 |
|----|------|------|------|
| T1 | 更新依赖：pyproject.toml（LangChain 1.x + FastAPI + Langfuse） | 15min | - |
| T2 | 升级 models.py：community → langchain-dashscope | 10min | T1 |
| T3 | 新增 settings.py：Langfuse 配置项 | 10min | T1 |
| T4 | 新增 src/api/ 目录：FastAPI 应用框架 + 路由 | 4h | T2 |
| T5 | 修改 rag_chain.py：CallbackHandler + @observe() | 30min | T3 |
| T6 | 新增 docker-compose 服务：postgres + langfuse | 30min | - |
| T7 | 调整 docker-compose app 服务：uvicorn + 端口 | 15min | T4 |
| T8 | 新增 nginx/ 目录：Dockerfile + nginx.conf | 1h | T6, T7 |
| T9 | 新增 .env 模板：加入 Langfuse / FastAPI 配置 | 15min | T3 |
| T10 | 存档 Gradio app.py 至 old/ | 10min | - |
| T11 | 更新 README：新架构说明 + 启动步骤 | 30min | T4-T9 |
| T12 | 前端开发：HTML 页面（KB 管理 + 聊天页） | 2-3d | T4 |
| T13 | 更新路线图 docs/roadmap.md | 15min | - |

## T1 — 更新 pyproject.toml

- 升级 langchain-core 到 `>=1.0.0,<2.0.0`
- 升级 langchain-openai 到 `>=1.0.0,<2.0.0`
- 升级 langchain-text-splitters 到 `>=1.0.0,<2.0.0`
- 升级 langchain-community 到 `>=0.3.0,<1.0.0`
- 新增 `langchain-dashscope>=0.1.0,<1.0.0`
- 新增 `langfuse>=4.0.0,<5.0.0`
- 新增 `fastapi>=0.115.0,<1.0.0`
- 新增 `uvicorn[standard]>=0.30.0,<1.0.0`

## T2 — 升级 models.py

- 改 2 行 import：community → langchain-dashscope
- `DashScopeEmbeddings` 和 `DashScopeRerank` 从 `langchain_dashscope` 导入
- 确认构造函数参数兼容

## T3 — 新增 settings.py 配置

```python
LANGFUSE_SECRET_KEY: str = os.getenv("LANGFUSE_SECRET_KEY", "")
LANGFUSE_PUBLIC_KEY: str = os.getenv("LANGFUSE_PUBLIC_KEY", "")
LANGFUSE_HOST: str = os.getenv("LANGFUSE_HOST", "http://langfuse:3000")
LANGFUSE_ENABLE: bool = os.getenv("LANGFUSE_ENABLE", "true").lower() == "true"
```

## T4 — 新增 FastAPI 框架

创建 `src/api/` 目录结构：

```
src/api/main.py           — FastAPI app, 生命周期, CORS
src/api/routes/__init__.py — router 聚合
src/api/routes/chat.py    — /api/chat/stream SSE
src/api/routes/knowledge_base.py — KB CRUD
src/api/routes/documents.py — 文档上传/列表
src/api/routes/health.py  — /api/health
```

### main.py 要点

- `from fastapi import FastAPI`
- `from fastapi.middleware.cors import CORSMiddleware`
- `app = FastAPI(title="Financial QA API")`
- 导入 routes 子模块
- 挂载 `app.add_middleware(CORSMiddleware, ...)`（允许 Nginx 源）

### chat.py 要点

- `GET /api/chat/stream` 接收 `session_id`, `kb_id`, `query`
- 调 `app_service.chat()` 或直接调 `rag_chain.chat_with_citations()`
- 用 `StreamingResponse` 包装为 SSE 格式
- `sse-starlette` 可选，原生 `StreamingResponse` 也行

### knowledge_base.py 要点

- `GET /api/kbs` → `app_service.list_knowledge_bases()`
- `POST /api/kbs` → `app_service.create_knowledge_base()`
- `DELETE /api/kbs/{kb_id}` → `app_service.delete_knowledge_base()`

### documents.py 要点

- `GET /api/kbs/{kb_id}/documents` → `app_service.get_documents()`
- `POST /api/kbs/{kb_id}/documents/upload` → `UploadFile` → `app_service.upload_and_process()`

## T5 — 修改 rag_chain.py

- `__init__` 中初始化 `CallbackHandler`
- `chat_with_citations()` 加 `@observe(name="chat_with_citations")`
- `_rerank_results()` 加 `@observe(name="rerank_results")`
- `_stream_answer()` 传入 `config={"callbacks": [...]}`
- 注意：`@observe()` 需要在调用 `CallbackHandler` 方法前导入

## T6 — 新增 Docker 服务

docker-compose.yml 新增 postgres + langfuse 两个服务，见 design.md 第 7 节。

## T7 — 调整 app 服务

- 端口从 `7860:7860` 改为 `8000:8000`
- command 从 `python -m src.app` 改为 `uvicorn src.api.main:app --host 0.0.0.0 --port 8000`
- 移除 Gradio 依赖（pyproject.toml 中保留但标记为可选？还是保留）

## T8 — 新增 nginx/

### nginx.conf

```nginx
server {
    listen 80;
    server_name localhost;

    # 前端静态文件
    location / {
        root /usr/share/nginx/html;
        index index.html;
        try_files $uri $uri/ /index.html;
    }

    # API 反向代理
    location /api/ {
        proxy_pass http://app:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection 'upgrade';
        proxy_set_header Host $host;
        proxy_cache_bypass $http_upgrade;
        # SSE 需要禁用缓冲
        proxy_buffering off;
        proxy_cache off;
    }
}
```

### Dockerfile

```dockerfile
FROM nginx:alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY html/ /usr/share/nginx/html/
```

## T9 — .env 模板

```bash
# FastAPI
FASTAPI_HOST=0.0.0.0
FASTAPI_PORT=8000

# Langfuse（自托管）
LANGFUSE_SECRET_KEY=sk-lf-xxx
LANGFUSE_PUBLIC_KEY=pk-lf-xxx
LANGFUSE_HOST=http://langfuse:3000
LANGFUSE_ENABLE=true

# Langfuse 数据库
LANGFUSE_POSTGRES_PASS=langfuse_pass

# NEXTAUTH（Langfuse UI 认证）
NEXTAUTH_SECRET=your-secret-here
LANGFUSE_SALT=your-salt-here
```

## T10 — 存档 Gradio

- 将 `src/app.py` 复制到 `old/src/app.py`
- 确认 `old/` 中已有其他历史版本

## T11 — 更新 README

- 启动步骤：改为 `docker compose up -d --build` + 访问 `http://localhost`
- Langfuse 首次启动配置步骤
- API 文档地址：`http://localhost/api/docs`

## T12 — 前端开发

- 使用 ui-ux-pro-max skill 生成 HTML/CSS/JS
- 两个页面：KB 管理（`/`）和聊天（`/chat`）
- KB 管理页：知识库列表、创建/删除、文件列表、上传
- 聊天页：KB 选择器、SSE 流式对话、引用展示
- Admin 模板或 Tailwind CSS 做基底

## T13 — 更新路线图

- 将 Phase 2 中"LangSmith Trace 接入"改为"Langfuse Tracing"
- 在 Phase 2 中新增 Step 0 说明
- 补充前端改造计划
