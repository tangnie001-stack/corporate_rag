## Why

财务年报、财报等专业文档的解读是投融资场景中的高频需求。现有通用问答工具对专业文档的领域知识理解不足，且回答缺乏可追溯来源，可信度低。本项目旨在构建一个基于 RAG 的企业文档智能问答助手 MVP，快速验证"上传财报文档 → 自然语言问答 → 附引用来源"这一核心流程的技术可行性和用户价值。

## What Changes

- 新增基于 RAG 的智能问答系统，支持上传 PDF/DOCX/TXT 文档并提问
- 新增 Gradio 5.x Web 界面，包含知识库管理、文件上传、对话交互、引用展示
- 新增 Docker 容器化部署方案（MySQL + Redis + App），实现一键启动
- 新增文档解析流水线（DocRouter + PyMuPDF/python-docx），支持编码检测和扫描件检测
- 新增 ChromaDB 内嵌向量存储 + 语义检索
- 新增 DashScope 模型集成（LLM: qwen-max, Embedding: text-embedding-v3, Rerank: gte-rerank-v2）
- 新增 RAGAS 评估流水线 + 分块质量 CLI 检查工具
- 重写架构：Python src/ 目录，模块类实例化、懒加载、无全局变量陷阱

## Capabilities

### New Capabilities

- `document-ingestion`: 文档上传、格式校验(PDF/DOCX/TXT)、编码检测(TXT)、扫描件检测(PDF)、PyMuPDF/python-docx 解析、RecursiveCharacterTextSplitter 分块、表格完整性检测
- `vector-retrieval`: ChromaDB PersistentClient 内嵌存储、text-embedding-v3 向量化、语义检索(余弦距离 + HNSW)、检索质量 CLI 检查
- `rag-generation`: Qwen-max LLM 调用(generator 流式输出)、gte-rerank-v2 重排序、带财务约束的 system prompt(拒绝计算/时间对齐)、引用来源生成、chat_manager(Redis + InMemory降级)
- `chat-interface`: Gradio 5.x Blocks 布局、知识库CRUD + 文件上传、对话交互 + 流式打字机效果、引用来源展示(答案尾部追加)、空状态引导、异步上传进度提示
- `infrastructure`: Docker Compose 编排(mysql:8.0 + redis:7-alpine + app)、healthcheck 优雅启动、多阶段 Dockerfile、.env 环境变量管理、命名卷持久化
- `evaluation`: RAGAS 评估流水线(faithfulness/answer_relevancy/context_recall/context_precision)、分块质量 CLI 报告(check_chunks.py)、检索测试 CLI(check_retrieval.py)

### Modified Capabilities

<!-- No existing specs to modify — this is the initial capability set. -->

## Impact

- 新增 `src/` 目录作为主代码目录，`old/` 保留不动作为参考
- 新增外部依赖：DashScope API (LLM, Embedding, Rerank)，ChatOpenAI/ChromaDB/Gradio/RAGAS Python 包
- 新增 Docker 依赖：MySQL 8.0, Redis 7-alpine
- Python 3.11+，依赖通过 pyproject.toml 或 requirements.txt 管理
- `.env` 文件管理敏感配置，不提交 git
