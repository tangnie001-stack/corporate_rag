# Iter 1 — 项目骨架与 Docker 生态 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 搭建完整项目目录结构、配置文件、Docker 编排，使所有容器健康运行，MySQL 表自动创建，Redis 可连接。

**Architecture:** 6 个文件组：项目骨架 → 配置层 → MySQL DDL + gitignore → Docker 基础设施 → Docker Compose → 集成验证。全部是新文件，无旧代码修改。

**Tech Stack:** Python 3.11, Docker Compose, MySQL 8.0, Redis 7-alpine, loguru, chromadb, gradio

---

## 文件结构 Iter 1 创建清单

```
/ (project root)
├── src/
│   └── __init__.py
├── deploy/
│   └── mysql/
│       └── init/
│           └── 001_schema.sql
├── tests/
│   └── __init__.py
├── test_docs/
│   └── .gitkeep
├── pyproject.toml
├── Dockerfile
├── docker-compose.yml
├── .env.template
├── .gitignore
└── .dockerignore
```

### Task 1: 项目目录结构与 pyproject.toml

**Files:**
- Create: `pyproject.toml`
- Create: `src/__init__.py`
- Create: `tests/__init__.py`
- Create: `test_docs/.gitkeep`

- [ ] **Step 1: Create all directories**

```bash
mkdir -p src/parsers deploy/mysql/init tests test_docs
```

- [ ] **Step 2: Write `pyproject.toml`**

```toml
[project]
name = "financial-qa-mvp"
version = "0.1.0"
description = "Financial Document QA Assistant - MVP"
requires-python = ">=3.11"
dependencies = [
    "gradio>=5.0,<6.0",
    "chromadb>=0.5.0,<1.0.0",
    "langchain-openai>=0.2.0,<1.0.0",
    "langchain-community>=0.3.0,<1.0.0",
    "langchain-core>=0.3.0,<1.0.0",
    "pymupdf>=1.24.0,<2.0.0",
    "python-docx>=1.1.0,<2.0.0",
    "python-dotenv>=1.0.0,<2.0.0",
    "loguru>=0.7.0,<1.0.0",
    "pymysql>=1.1.0,<2.0.0",
    "chardet>=5.0.0,<6.0.0",
    "mysql-connector-python>=8.0.0,<9.0.0",
    "redis>=5.0.0,<6.0.0",
    "ragas>=0.2.0,<1.0.0",
    "datasets>=2.0.0,<3.0.0",
]

[tool.setuptools.packages.find]
where = ["src"]
```

- [ ] **Step 3: Write `src/__init__.py` and `tests/__init__.py`** (empty files)

```bash
echo "" > src/__init__.py
echo "" > tests/__init__.py
echo "" > test_docs/.gitkeep
```

- [ ] **Step 4: Verify basic Python import**

```bash
pip install -e ".[dev]" 2>&1 | tail -5
python -c "import sys; sys.path.insert(0, 'src'); print('OK')"
# 预期: OK（此时 config 等模块还不存在，只验证 src 可导入）
```

### Task 2: src/config.py + .env.template

**Files:**
- Create: `src/config.py`
- Create: `.env.template`

- [ ] **Step 1: Write `src/config.py`**

```python
import os
from dotenv import load_dotenv

load_dotenv()


# ------ DashScope API ------
DASHSCOPE_API_KEY: str = os.getenv("DASHSCOPE_API_KEY", "")
DASHSCOPE_BASE_URL: str = os.getenv("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")

# ------ 模型选择 ------
LLM_MODEL: str = os.getenv("LLM_MODEL", "qwen-max")
EMBEDDING_MODEL: str = os.getenv("EMBEDDING_MODEL", "text-embedding-v3")
RERANK_MODEL: str = os.getenv("RERANK_MODEL", "gte-rerank-v2")
LLM_TEMPERATURE: float = float(os.getenv("LLM_TEMPERATURE", "0.1"))

# ------ MySQL ------
MYSQL_HOST: str = os.getenv("MYSQL_HOST", "localhost")
MYSQL_PORT: int = int(os.getenv("MYSQL_PORT", "3306"))
MYSQL_USER: str = os.getenv("MYSQL_USER", "root")
MYSQL_PASSWORD: str = os.getenv("MYSQL_PASSWORD", "financial_qa_pass")
MYSQL_DATABASE: str = os.getenv("MYSQL_DATABASE", "financial_qa")

# ------ Redis ------
REDIS_HOST: str = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT: int = int(os.getenv("REDIS_PORT", "6379"))
REDIS_DB: int = int(os.getenv("REDIS_DB", "0"))
REDIS_URL: str = f"redis://{REDIS_HOST}:{REDIS_PORT}/{REDIS_DB}"

# ------ ChromaDB ------
CHROMA_PERSIST_DIR: str = os.getenv("CHROMA_PERSIST_DIR", "./data/chroma_persist")
CHROMA_COLLECTION_PREFIX: str = os.getenv("CHROMA_COLLECTION_PREFIX", "kb_")

# ------ 文档处理 ------
CHUNK_SIZE: int = int(os.getenv("CHUNK_SIZE", "512"))
CHUNK_OVERLAP: int = int(os.getenv("CHUNK_OVERLAP", "64"))
TOP_K_RETRIEVAL: int = int(os.getenv("TOP_K_RETRIEVAL", "8"))
TOP_K_RERANK: int = int(os.getenv("TOP_K_RERANK", "5"))

# ------ 对话 ------
MEMORY_WINDOW: int = int(os.getenv("MEMORY_WINDOW", "6"))
REDIS_TTL: int = int(os.getenv("REDIS_TTL", "604800"))

# ------ 重试策略 ------
RETRY_MAX_ATTEMPTS: int = int(os.getenv("RETRY_MAX_ATTEMPTS", "3"))
RETRY_INITIAL_INTERVAL: float = float(os.getenv("RETRY_INITIAL_INTERVAL", "1.0"))
RETRY_BACKOFF_FACTOR: float = float(os.getenv("RETRY_BACKOFF_FACTOR", "2.0"))

# ------ 文件上传 ------
MAX_FILE_SIZE: int = int(os.getenv("MAX_FILE_SIZE", str(50 * 1024 * 1024)))
```

- [ ] **Step 2: Write `.env.template`**

```bash
# DashScope API
DASHSCOPE_API_KEY=sk-your-api-key-here
DASHSCOPE_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1

# 模型选择
LLM_MODEL=qwen-max
EMBEDDING_MODEL=text-embedding-v3
RERANK_MODEL=gte-rerank-v2
LLM_TEMPERATURE=0.1

# MySQL（开发环境用 localhost，Docker 用 mysql）
MYSQL_HOST=localhost
MYSQL_PORT=3306
MYSQL_USER=root
MYSQL_PASSWORD=financial_qa_pass
MYSQL_DATABASE=financial_qa

# Redis（开发环境用 localhost，Docker 用 redis）
REDIS_HOST=localhost
REDIS_PORT=6379
REDIS_DB=0

# ChromaDB 持久化目录
CHROMA_PERSIST_DIR=./data/chroma_persist
CHROMA_COLLECTION_PREFIX=kb_

# 文档处理
CHUNK_SIZE=512
CHUNK_OVERLAP=64
TOP_K_RETRIEVAL=8
TOP_K_RERANK=5

# 对话
MEMORY_WINDOW=6
REDIS_TTL=604800

# 重试
RETRY_MAX_ATTEMPTS=3
RETRY_INITIAL_INTERVAL=1.0
RETRY_BACKOFF_FACTOR=2.0

# 上传限制（默认 50MB）
MAX_FILE_SIZE=52428800
```

### Task 3: MySQL DDL + .gitignore

**Files:**
- Create: `deploy/mysql/init/001_schema.sql`
- Create: `.gitignore`

- [ ] **Step 1: Write `deploy/mysql/init/001_schema.sql`**

```sql
CREATE TABLE IF NOT EXISTS knowledge_base (
    id          VARCHAR(36)  PRIMARY KEY,
    name        VARCHAR(255) NOT NULL UNIQUE,
    description TEXT,
    created_at  DATETIME     DEFAULT CURRENT_TIMESTAMP,
    updated_at  DATETIME     DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS document (
    id          VARCHAR(36)  PRIMARY KEY,
    kb_id       VARCHAR(36)  NOT NULL,
    filename    VARCHAR(255) NOT NULL,
    file_type   VARCHAR(10)  NOT NULL,
    file_size   INT          NOT NULL DEFAULT 0,
    chunk_count INT          NOT NULL DEFAULT 0,
    status      VARCHAR(20)  NOT NULL DEFAULT 'pending',
    error_msg   TEXT,
    created_at  DATETIME     DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (kb_id) REFERENCES knowledge_base(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS conversation_history (
    id          INT          AUTO_INCREMENT PRIMARY KEY,
    session_id  VARCHAR(36)  NOT NULL,
    kb_id       VARCHAR(36)  NOT NULL,
    role        ENUM('user','assistant') NOT NULL,
    content     TEXT         NOT NULL,
    sources     JSON,
    created_at  DATETIME     DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_session (session_id, created_at),
    FOREIGN KEY (kb_id) REFERENCES knowledge_base(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

- [ ] **Step 2: Write `.gitignore`**

```
# Python
__pycache__/
*.py[cod]
*.egg-info/
dist/
.eggs/
.venv/
venv/

# Environment
.env
.env.local

# IDE
.vscode/
.idea/

# Docker volume data
data/chroma_persist/
chroma_data/
mysql_data/
redis_data/

# Logs
logs/
*.log

# OS
.DS_Store
Thumbs.db
```

### Task 4: Docker 基础设施（Dockerfile + .dockerignore）

**Files:**
- Create: `Dockerfile`
- Create: `.dockerignore`

- [ ] **Step 1: Write `Dockerfile`**

```dockerfile
# ---- Build stage ----
FROM python:3.11-slim AS builder

WORKDIR /build
COPY pyproject.toml .
RUN pip install --no-cache-dir -e . && \
    pip show financial-qa-mvp | grep Location | cut -d' ' -f2 > /site-packages-path.txt

# ---- Runtime stage ----
FROM python:3.11-slim

WORKDIR /app

# Install runtime dependencies only
COPY pyproject.toml .
RUN pip install --no-cache-dir -e .

# Copy source code
COPY src/ src/
COPY deploy/ deploy/

# Volume mount points
VOLUME ["/data/chroma", "/data/logs"]

EXPOSE 7860
CMD ["python", "-m", "src.app"]
```

- [ ] **Step 2: Write `.dockerignore`**

```
__pycache__/
*.py[cod]
.env
.env.local
.git/
.gitignore
data/chroma_persist/
mysql_data/
redis_data/
logs/
test_docs/
old/
docs/
*.md
```

### Task 5: docker-compose.yml + wait-for-it.sh

**Files:**
- Create: `docker-compose.yml`
- Create: `deploy/wait-for-it.sh`

- [ ] **Step 1: Write `docker-compose.yml`**

```yaml
version: "3.8"

services:
  mysql:
    image: mysql:8.0
    container_name: financial-qa-mysql
    restart: unless-stopped
    environment:
      MYSQL_ROOT_PASSWORD: ${MYSQL_PASSWORD:-financial_qa_pass}
      MYSQL_DATABASE: ${MYSQL_DATABASE:-financial_qa}
    ports:
      - "3306:3306"
    volumes:
      - mysql_data:/var/lib/mysql
      - ./deploy/mysql/init:/docker-entrypoint-initdb.d
    healthcheck:
      test: ["CMD", "mysqladmin", "ping", "-h", "localhost", "-u", "root", "-p${MYSQL_PASSWORD:-financial_qa_pass}"]
      interval: 5s
      timeout: 3s
      retries: 15
      start_period: 30s
    networks:
      - app-network

  redis:
    image: redis:7-alpine
    container_name: financial-qa-redis
    restart: unless-stopped
    ports:
      - "6379:6379"
    volumes:
      - redis_data:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 10
    networks:
      - app-network

  app:
    build: .
    container_name: financial-qa-app
    restart: unless-stopped
    ports:
      - "7860:7860"
    env_file:
      - .env
    environment:
      MYSQL_HOST: mysql
      REDIS_HOST: redis
      CHROMA_PERSIST_DIR: /data/chroma
    volumes:
      - chroma_data:/data/chroma
      - app_logs:/data/logs
    depends_on:
      mysql:
        condition: service_healthy
      redis:
        condition: service_started
    networks:
      - app-network

volumes:
  mysql_data:
    name: financial_qa_mysql_data
  redis_data:
    name: financial_qa_redis_data
  chroma_data:
    name: financial_qa_chroma_data
  app_logs:
    name: financial_qa_app_logs

networks:
  app-network:
    name: financial_qa_network
```

- [ ] **Step 2: Write `deploy/wait-for-it.sh`**

```bash
#!/bin/bash
# wait-for-it.sh — wait for a TCP host:port to be available
# Usage: ./wait-for-it.sh host:port [-t timeout] [-- command args]

HOST=$(echo "$1" | cut -d: -f1)
PORT=$(echo "$1" | cut -d: -f2)
shift
TIMEOUT=30

while [[ "$#" -gt 0 ]]; do
    case "$1" in
        -t) TIMEOUT="$2"; shift 2 ;;
        --) shift; break ;;
        *) break ;;
    esac
done

echo "Waiting for $HOST:$PORT (timeout: ${TIMEOUT}s)..."
for i in $(seq 1 "$TIMEOUT"); do
    nc -z "$HOST" "$PORT" 2>/dev/null && break
    sleep 1
done

if nc -z "$HOST" "$PORT" 2>/dev/null; then
    echo "$HOST:$PORT is available"
    exec "$@"
else
    echo "Timeout waiting for $HOST:$PORT"
    exit 1
fi
```

```bash
chmod +x deploy/wait-for-it.sh
```

### 执行记录与计划差异（2026-06-16）

实际执行中与原始计划的差异，供后续 Iter 参考：

| # | 差异点 | 计划 | 实际 | 原因 |
|---|--------|------|------|------|
| 1 | **MySQL 版本** | 5.7 | 8.0 | 5.7 已不维护，Docker Hub 拉取失败 |
| 2 | **docker-compose.yml** | 含 `version: "3.8"` | 已移除 | Docker 提示该属性已废弃 |
| 3 | **Dockerfile 构建** | `pip install -e .` | `pip install .` + 前置 `mkdir -p src` | editable 模式需要 `src/` 目录存在；构建时先装依赖再拷源码 |
| 4 | **README.md** | 未提及 | 创建了最小版本 | pyproject.toml 引用 `readme = "README.md"`，缺少则构建失败 |
| 5 | **.dockerignore** | `*.md` 全部排除 | `!README.md` 例外 | 同上，README.md 需要进入构建上下文 |
| 6 | **src/app.py** | 计划 Iter 5 创建 | 创建了最小 stub | Docker CMD 为 `python -m src.app`，无 app.py 容器闪退 |
| 7 | **镜像加速** | 未涉及 | 配置 daemon.json (docker.m.daocloud.io 等) | 国内 Docker Hub 拉取慢/失败 |
| 8 | **wait-for-it.sh** | 用于启动等待 | 已创建但未实际使用 | docker-compose 的 depends_on healthcheck 已满足需求 |

### Task 6: 集成验证

**Files:** （无创建，仅运行命令）

- [ ] **Step 1: 复制 .env.template 为 .env 并填入真实 API Key**

```bash
cp .env.template .env
# 编辑 .env 填入 DASHSCOPE_API_KEY
```

- [ ] **Step 2: 构建并启动所有容器**

```bash
docker-compose up --build -d
# 首次构建会拉取 MySQL 和 Redis 镜像、构建 app 镜像
# 预期输出: Creating network ...  Creating volume ... 等
```

- [ ] **Step 3: 验证所有容器健康运行**

```bash
docker-compose ps
# 预期:
#   Name                   State   Ports
#   financial-qa-mysql     Up      3306/tcp
#   financial-qa-redis     Up      6379/tcp
#   financial-qa-app       Up      0.0.0.0:7860->7860/tcp

docker-compose logs app --tail=20
# 预期: app 启动日志，无 Exception 或 ConnectionError
```

- [ ] **Step 4: 验证 MySQL 表自动创建**

```bash
docker exec financial-qa-mysql mysql -uroot -pfinancial_qa_pass financial_qa -e "SHOW TABLES;"
# 预期:
# +-------------------------+
# | Tables_in_financial_qa  |
# +-------------------------+
# | knowledge_base          |
# | document                |
# | conversation_history    |
# +-------------------------+
```

- [ ] **Step 5: 验证 Redis 可连接**

```bash
docker exec financial-qa-redis redis-cli ping
# 预期: PONG
```

- [ ] **Step 6: 验证 config.py 在容器内可导入**

```bash
docker-compose exec app python -c "from src.config import DASHSCOPE_API_KEY, MYSQL_HOST, REDIS_HOST; print(f'MySQL: {MYSQL_HOST}, Redis: {REDIS_HOST}, Key set: {bool(DASHSCOPE_API_KEY)}')"
# 预期: MySQL: mysql, Redis: redis, Key set: True/False
```

- [ ] **Step 7: Iter 1 完成——提交代码**

```bash
git add -A
git status  # 确认只有 Iter 1 相关文件
git commit -m "feat: add project skeleton, Docker orchestration, and infrastructure config

- Project directory structure (src/, tests/, deploy/, test_docs/)
- pyproject.toml with all dependencies
- src/config.py with 21 env-driven configuration items
- .env.template for environment setup
- MySQL 8.0 schema (knowledge_base, document, conversation_history)
- Dockerfile (multi-stage, Python 3.11-slim)
- docker-compose.yml (mysql + redis + app with healthchecks)
- .gitignore and .dockerignore
- wait-for-it.sh for startup ordering"
```
