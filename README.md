# Corporate RAG MVP

**金融文档智能问答助手** — 基于 RAG（Retrieval-Augmented Generation）的财报问答系统。
上传 PDF/DOCX/TXT 格式的财报文档，即可用自然语言提问，
系统从文档中检索相关片段，由大语言模型生成带引用来源的回答。

## 系统架构

```
用户 → Nginx (:80) → /api/* → FastAPI (:8000) → MySQL, Redis, ChromaDB
                   → /* → 静态 HTML/CSS/JS 前端

Langfuse (:3000) → PostgreSQL (Tracing 存储)
```

## 功能特性

- 支持 PDF / DOCX / TXT 三种文档格式
- 知识库管理：创建、选择、删除多个知识库
- 语义检索：ChromaDB 向量数据库 + DashScope Embedding
- 重排序优化：DashScope gte-rerank-v2 精排检索结果
- 流式输出：Qwen-max 生成回答逐 token 显示
- 引用溯源：每个回答附带来源文档和页码
- 对话历史：Redis 缓存最近多轮对话（自动降级到内存）
- Docker 一键部署

## 技术栈

| 层 | 技术 |
|-----|--------|
| 前端 | Nginx + HTML/CSS/JS (取代 Gradio) |
| API 层 | FastAPI + Uvicorn |
| 后端 | Python 3.11, LangChain 1.x |
| LLM | DashScope Qwen-max, text-embedding-v3, gte-rerank-v2 |
| 向量库 | ChromaDB 0.5+ |
| 元数据库 | MySQL 8.0 |
| 缓存 | Redis 7 |
| Tracing | Langfuse (自托管) + PostgreSQL |
| 文档解析 | PyMuPDF (PDF), python-docx (DOCX) |
| 评价 | RAGAS (faithfulness, answer_relevancy, context_recall, context_precision) |
| 部署 | Docker Compose |

## 快速启动

### 前置条件

- Docker & Docker Compose
- DashScope API Key（阿里云百炼平台）

### 启动步骤

```bash
# 1. 克隆项目
git clone <repo-url>
cd financial-qa-mvp

# 2. 配置环境变量
cp .env.template .env
# 编辑 .env，填入 DASHSCOPE_API_KEY

# 3. 启动所有服务
docker compose up --build -d

# 4. 访问 Web 界面
# 前端界面： http://localhost （知识库管理 + 对话问答）
#   登录测试：账号 admin / 密码 admin123（首次输入自动注册）
# API 文档：  http://localhost/api/docs
# Langfuse：  http://localhost:3000
#   Langfuse 登录：账号 admin@corprag.local / 密码 admin123456

# 5. （首次）初始化 Langfuse
# 访问 http://localhost:3000 → 注册首个用户 → 创建项目 → Settings → API Keys
# 将 LANGFUSE_SECRET_KEY / LANGFUSE_PUBLIC_KEY 填入 .env
# 重启 app：docker compose restart app
```

### 使用流程

1. 创建知识库（如 "2024年年报"）
2. 上传财报文档（PDF / DOCX / TXT）
3. 在对话框中输入问题
4. 查看 AI 回答和引用来源

## 部署指南

### 1. Docker Compose 完整部署（推荐）

一键启动所有 7 个服务容器：

```bash
docker compose up -d --build
```

| 服务 | 容器名 | 端口 | 说明 |
|------|--------|------|------|
| Nginx | `financial-qa-nginx` | `:80` | 反向代理 + 静态文件服务 |
| FastAPI | `financial-qa-app` | `:8000` | REST API + SSE 流式 |
| MySQL | `financial-qa-mysql` | `:3306` | 文档/知识库元数据 |
| Redis | `financial-qa-redis` | `:6379` | 对话缓存 |
| PostgreSQL | `financial-qa-postgres` | `:5432` | Langfuse 存储 |
| Langfuse | `financial-qa-langfuse` | `:3000` | LLM Tracing 面板 |

启动后访问：

- **前端页面** → http://localhost （Nginx → Tailwind CSS CDN）
- **API 文档** → http://localhost/api/docs （Swagger UI）
- **Langfuse** → http://localhost:3000

### 2. 本地开发模式（热重载）

在不重启 Docker 的情况下修改代码即时生效：

```bash
# 启动依赖服务（不需要 app 和 nginx）
docker compose up -d mysql redis postgres langfuse

# 本地运行 FastAPI（--reload 热重载）
uvicorn src.main:app --host 0.0.0.0 --port 8000 --reload

# 在另一个终端启动静态文件服务（预览前端）
python3 -m http.server 8080 --directory nginx/html/
```

此时前端访问 `http://localhost:8080`，API 访问 `http://localhost:8000`。

> 如需让前端通过 Nginx 访问（`:80/api/*` → `:8000`），单独启动 Nginx：
> ```bash
> docker compose up -d --build nginx
> ```

### 3. 纯前端预览（无需后端）

只想看页面样式效果，不需要 API：

```bash
python3 -m http.server 8080 --directory nginx/html/
# 浏览器打开 http://localhost:8080
```

### 4. 单独管理各服务

```bash
# 构建并启动所有服务
docker compose up -d --build

# 查看所有服务状态
docker compose ps

# 查看应用日志
docker compose logs -f app
docker compose logs -f nginx

# 只重启某个服务
docker compose restart app

# 停止所有服务
docker compose down

# 停止并清除所有数据（慎用）
docker compose down -v

# 查看容器网络
docker network inspect financial-qa_app-network
```

## Nginx 路由说明

```
http://localhost/              → nginx/html/ 静态文件
http://localhost/api/*         → 反向代理到 app:8000
http://localhost/api/docs      → FastAPI Swagger 文档
http://localhost/openapi.json  → FastAPI OpenAPI 规范
```

Nginx 已预配 SSE 支持（`proxy_buffering off`），确保流式问答不卡顿。

---

## 项目结构

```
├── src/
│   ├── api/                    # FastAPI REST API
│   │   ├── main.py             # FastAPI 应用入口
│   │   └── routes/             # API 路由
│   ├── app_service.py         # 业务逻辑层
│   ├── rag_chain.py           # RAG 问答链路 (Langfuse Tracing)
│   ├── chat_manager.py        # 对话缓存管理
│   ├── models.py              # LLM/Embedding/Rerank 工厂
│   ├── vector_store.py        # ChromaDB 向量存储
│   ├── mysql_db.py            # MySQL CRUD
│   ├── document_loader.py     # 文档加载入口
│   ├── eval_ragas.py          # RAGAS 评估脚本
│   ├── config/
│   │   ├── settings.py        # 环境配置
│   │   ├── prompts.py         # LLM 提示词
│   │   ├── queries.py         # SQL 语句
│   │   └── qa_pairs.py        # RAGAS 测试 QA 对
│   ├── parsers/
│   │   ├── router.py          # 文档路由
│   │   ├── base.py            # 解析器基类
│   │   ├── pymupdf_parser.py  # PDF 解析
│   │   ├── docx_parser.py     # DOCX 解析
│   │   └── txt_parser.py      # TXT 解析
    │   └── cli/
    │       └── check_retrieval.py # 检索质量检测
├── nginx/                     # Nginx 反向代理
│   ├── Dockerfile
│   ├── nginx.conf
│   └── html/                  # 前端静态文件
├── tests/                     # 单元测试
├── deploy/                    # Docker 部署配置
├── docker-compose.yml         # Docker 编排 (7 个服务)
├── Dockerfile                 # 容器构建
├── old/                       # 历史版本存档
└── outputs/                   # RAGAS 评估 CSV 输出
```

## 配置说明

通过 `.env` 文件配置所有参数，主要配置项：

| 配置项 | 说明 | 默认值 |
|---------|------|--------|
| DASHSCOPE_API_KEY | DashScope API Key | （必填） |
| LLM_MODEL | 大语言模型 | qwen-max |
| EMBEDDING_MODEL | 向量化模型 | text-embedding-v3 |
| CHUNK_SIZE | 分块大小 | 512 |
| TOP_K_RETRIEVAL | 检索召回数 | 8 |
| TOP_K_RERANK | 重排序保留数 | 5 |
| MEMORY_WINDOW | 对话窗口大小 | 6 |

## RAGAS 评估

```bash

# 运行评估（需先创建知识库并上传测试文档）
python src/eval_ragas.py

# Benchmark 对比不同 chunk_size
python src/eval_ragas.py --chunk-size 512
python src/eval_ragas.py --chunk-size 768
python src/eval_ragas.py --chunk-size 1024
```

## 已知限制

- 扫描件 PDF 暂不支持（无 OCR 能力）
- 表格/数字可能因分块被切断（MVP 只检测不保护）
- RAGAS 评估使用 Qwen-max 作为 judge LLM，存在 self-bias 风险
- 当前只支持单文档格式知识库（不支持多格式混合检索优化）

## 许可证

MIT

## 运行
### 创建虚拟环境
python -m venv .venv

### 激活虚拟环境
source .venv/bin/activate

### 安装项目依赖
pip install -e .

### 本地开发（启动依赖服务）
docker compose up -d mysql redis postgres langfuse

### 运行 API 服务（热重载）
uvicorn src.main:app --host 0.0.0.0 --port 8000 --reload

### 运行测试
pytest tests/ -v

### 结构图谱
codegraph init

### 工作流程
大需求流程：explore → grill-me → opsx:propose → brainstorming → grill-me → writing-plans → subagents → playwright-cli


## 大模型写代码的坑
### 技术细节上面幻觉非常多，一定要详细问，并且强制要求网上搜相应的文章进行结合后再回答
### 前后端搭配写代码时，一定要求大模型生成接口契约，否则生成的代码，前后端接口字段对不上
### 链接数据库，中间件等的方法，一定注意异步同步，大模型都是默认同步的， 其他情况下一定要询问同步/异步的问题，还有接口统一用post方式
### 大模型的知识盲区，一定要多问，多问，多问， 不会直接给你最好的方案，只会贴合你当前代码的给你方案，导致没有架构性，前瞻性，只是为了解决当前的问题。
### 大模型对工程化，对可用性，对性能的理解较弱，做项目的时候一定要自己关注这块
目前具体工程化的问题有，链路统一的traceid，同步/异步调用，线程池的使用，上下文contextvars的使用，添加日志系统给大模型查日志，添加异常统一管理，添加metrics,span完善监控，工程化的框架需要形成rule。代码的治理很麻烦，包装的方法要通过rule指定使用。
### 领域深化不够，不能针对领域特殊的case做出应对，必须要挑出来，单独处理。
文档分块，非正常格式的文档，需要做兼容。 文档表格跨页
