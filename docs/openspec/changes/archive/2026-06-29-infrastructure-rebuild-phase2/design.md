# Phase 2 Step 0 — 基础设施重构设计

## 1. 新架构总览

```
                              Nginx (:80)
                              │
                    ┌─────────┴─────────┐
                    │                   │
               /api/*               / (静态文件)
                    │                   │
            ┌───────▼───────┐   ┌───────▼───────┐
            │  FastAPI      │   │  HTML/CSS/JS  │
            │  (:8000)      │   │  前端          │
            └───────┬───────┘   └───────────────┘
                    │
    ┌───────────────┼───────────────────┐
    │               │                   │
    ▼               ▼                   ▼
  MySQL           Redis            ChromaDB
  (:3306)         (:6379)          (内嵌)
```

### 新增容器

```
nginx       → 反向代理 /api/* → FastAPI
              静态文件服务 /   → HTML/CSS/JS
postgres    → Langfuse 的数据库
langfuse    → Langfuse 服务端 (:3000)
```

## 2. API 路由设计

### 2.1 知识库管理

```
GET    /api/kbs                          → list_knowledge_bases()
POST   /api/kbs                          → create_knowledge_base()   body: {name, description?}
DELETE /api/kbs/{kb_id}                  → delete_knowledge_base()
```

### 2.2 文档管理

```
GET    /api/kbs/{kb_id}/documents        → get_documents()
POST   /api/kbs/{kb_id}/documents/upload → upload_and_process()      multipart/form-data
```

### 2.3 聊天

```
GET    /api/chat/stream?                 → chat_with_citations() → SSE
         kb_id=xxx
         &session_id=xxx
         &query=xxx
```

### 2.4 系统

```
GET    /api/health                       → health check
GET    /docs                             → FastAPI OpenAPI 文档
```

## 3. SSE 流式输出格式

```
GET /api/chat/stream?session_id=abc&kb_id=xyz&query=去年净利润多少

响应 (text/event-stream):

event: token
data: {"token": "根据"}

event: token
data: {"token": "年报"}

...

event: citation
data: {"source": "2024年年报.pdf", "page": 15, "snippet": "净利润为..."}

event: done
data: {}
```

### 前端消费

```javascript
const evtSource = new EventSource(
  `/api/chat/stream?session_id=${sid}&kb_id=${kbId}&query=${encodeURIComponent(query)}`
);

evtSource.addEventListener("token", (e) => {
  const { token } = JSON.parse(e.data);
  // 追加到当前对话气泡
});

evtSource.addEventListener("citation", (e) => {
  const citation = JSON.parse(e.data);
  // 追加到引用区域
});

evtSource.addEventListener("done", () => {
  evtSource.close();
});
```

## 4. 文件结构变更

```
src/
  api/                          ← 新增
    __init__.py
    main.py                     ← FastAPI app 创建 + 生命周期事件
    routes/
      __init__.py
      chat.py                   ← /api/chat/stream SSE
      knowledge_base.py         ← /api/kbs CRUD
      documents.py              ← /api/kbs/{kb_id}/documents
      health.py                 ← /api/health

  app.py                        ← 保留后移至 old/（归档用）
  app_service.py                ← 不动

nginx/                          ← 新增
  Dockerfile
  nginx.conf
  html/                         ← 前端静态文件
    index.html
    kb.html
    css/
    js/
```

## 5. LangChain 1.x 升级变更

### pyproject.toml

```toml
# 升级
langchain-core>=1.0.0,<2.0.0
langchain-openai>=1.0.0,<2.0.0
langchain-community>=0.3.0,<1.0.0    # 升到 0.4.2，无 1.x
langchain-text-splitters>=1.0.0,<2.0.0

# 新增
langchain-dashscope>=0.1.0,<1.0.0
langfuse>=4.0.0,<5.0.0
fastapi>=0.115.0,<1.0.0
uvicorn[standard]>=0.30.0,<1.0.0
```

### src/models.py（改动 2 行）

```python
# 改前
from langchain_community.embeddings import DashScopeEmbeddings
from langchain_community.document_compressors.dashscope_rerank import DashScopeRerank

# 改后
from langchain_dashscope import DashScopeEmbeddings
from langchain_dashscope import DashScopeRerank
```

### src/config/settings.py（新增 4 行）

```python
# Langfuse
LANGFUSE_SECRET_KEY: str = os.getenv("LANGFUSE_SECRET_KEY", "")
LANGFUSE_PUBLIC_KEY: str = os.getenv("LANGFUSE_PUBLIC_KEY", "")
LANGFUSE_HOST: str = os.getenv("LANGFUSE_HOST", "http://langfuse:3000")
LANGFUSE_ENABLE: bool = os.getenv("LANGFUSE_ENABLE", "true").lower() == "true"
```

## 6. Langfuse Tracing

### rag_chain.py 改动

```python
class RAGChain:
    def __init__(self, ...):
        ...
        # Langfuse CallbackHandler
        self._langfuse_handler = None
        if LANGFUSE_ENABLE:
            try:
                from langfuse.callback import CallbackHandler
                self._langfuse_handler = CallbackHandler(
                    secret_key=LANGFUSE_SECRET_KEY,
                    public_key=LANGFUSE_PUBLIC_KEY,
                    host=LANGFUSE_HOST,
                )
                logger.info("Langfuse tracing enabled")
            except Exception as e:
                logger.warning("Langfuse init failed: {}", e)

    @observe(name="chat_with_citations")
    def chat_with_citations(self, ...):
        ...

    @observe(name="rerank_results")
    def _rerank_results(self, ...):
        ...

    def _stream_answer(self, messages):
        config = {"callbacks": [self._langfuse_handler]} if self._langfuse_handler else None
        stream = self.llm.stream(messages, config=config)
        ...
```

## 7. Docker Compose 变更

### 新增服务

```yaml
postgres:
  image: postgres:15-alpine
  container_name: financial-qa-postgres
  environment:
    POSTGRES_DB: langfuse
    POSTGRES_USER: langfuse
    POSTGRES_PASSWORD: ${LANGFUSE_POSTGRES_PASS:-langfuse_pass}
  volumes:
    - postgres_data:/var/lib/postgresql/data
  healthcheck:
    test: ["CMD-SHELL", "pg_isready -U langfuse"]
  networks:
    - app-network

langfuse:
  image: langfuse/langfuse:2
  container_name: financial-qa-langfuse
  ports:
    - "3000:3000"
  environment:
    DATABASE_URL: postgresql://langfuse:${LANGFUSE_POSTGRES_PASS:-langfuse_pass}@postgres:5432/langfuse
    NEXTAUTH_SECRET: ${NEXTAUTH_SECRET:-changeme}
    SALT: ${LANGFUSE_SALT:-changeme}
  depends_on:
    postgres:
      condition: service_healthy
  networks:
    - app-network

nginx:
  build: ./nginx
  container_name: financial-qa-nginx
  ports:
    - "80:80"
  depends_on:
    - app
  networks:
    - app-network
```

### 现有 app 服务调整

```yaml
app:
  build: .
  container_name: financial-qa-app
  ports:
    - "8000:8000"      # 改端口
  command: uvicorn src.api.main:app --host 0.0.0.0 --port 8000
  # 其余不变
```

## 8. 部署与首次启动流程

```bash
# 1. 启动所有服务
docker compose up -d --build

# 2. 首次启动 Langfuse：打开浏览器 http://localhost:3000
#    注册第一个用户 → 创建 Project → Settings → API Keys

# 3. 将生成的 Key 写入 .env
echo "LANGFUSE_SECRET_KEY=sk-lf-..." >> .env
echo "LANGFUSE_PUBLIC_KEY=pk-lf-..." >> .env
echo "LANGFUSE_HOST=http://langfuse:3000" >> .env

# 4. 重启 app 加载 Key
docker compose restart app

# 5. 访问 http://localhost → 前端页面
#    API 文档 http://localhost/api/docs
```
