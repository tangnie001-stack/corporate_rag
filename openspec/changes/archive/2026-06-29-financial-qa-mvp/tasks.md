## 1. 项目骨架与 Docker 生态

- [ ] 1.1 创建项目目录结构（src/, tests/, deploy/, test_docs/）
- [ ] 1.2 编写 pyproject.toml（项目元数据、依赖：gradio, chromadb, langchain-*等、工具配置）
- [ ] 1.3 编写 src/config.py（全部配置从环境变量读取，含重试参数、chunk 参数、模型参数）
- [ ] 1.4 编写 .env.template（列出所有环境变量及说明）
- [ ] 1.5 编写 deploy/mysql/init/001_schema.sql（knowledge_base, document, conversation_history 三张表）
- [ ] 1.6 编写 Dockerfile（多阶段构建，Python 3.11-slim）
- [ ] 1.7 编写 docker-compose.yml（mysql:8.0 + redis:7-alpine + app 三服务，healthcheck，命名卷）
- [ ] 1.8 编写 wait-for-it.sh（或等效的启动等待脚本）
- [ ] 1.9 编写 .gitignore（环境变量、chroma_persist/、mysql_data/、redis_data/、__pycache__/）
- [ ] 1.10 验证：`docker-compose up --build` 后所有服务健康运行，表自动创建

## 2. 文档处理流水线 ✅

- [x] 2.1 实现 src/parsers/base.py（ChunkData 数据类、ParseResult 数据类、BaseParser 抽象基类）
- [x] 2.2 实现 src/parsers/pymupdf_parser.py（PyMuPDFParser：逐页提取文本 + 表格，扫描件检测）
- [x] 2.3 实现 src/parsers/docx_parser.py（DocxParser：python-docx 逐段提取）
- [x] 2.4 实现 src/parsers/txt_parser.py（TxtParser：chardet 编码检测 + UTF-8/GBK 回退）
- [x] 2.5 实现 src/parsers/router.py（DocRouter：根据文件类型路由到对应 parser）
- [x] 2.6 实现 src/document_loader.py（兼容入口，调用 DocRouter，封装分块逻辑）
- [x] 2.7 实现表格完整性检测（check_chunks.py 中标记疑似被切断的表格行）
- [x] 2.8 实现 src/check_chunks.py（CLI 质量报告：总数/平均长度/分布/表格切断数）
- [x] 2.9 实现 src/mysql_db.py（MySQL CRUD 封装：连接重试+指数退避，建表，知识库/文档操作）
- [x] 2.10 验证：上传测试 PDF → 分块 → 运行 check_chunks.py 查看质量报告

## 3. 向量存储与检索

- [ ] 3.1 实现 src/models.py（DashScope LLM/Embedding/Rerank 工厂函数，含 retry + 指数退避）
- [ ] 3.2 实现 src/vector_store.py（ChromaDB PersistentClient 封装：增/删/查/集合管理，kb_ prefix）
- [ ] 3.3 实现 src/check_retrieval.py（检索测试 CLI：输入 query 打印召回结果）
- [ ] 3.4 验证：上传文档后运行检索测试，确认语义召回正常工作

## 4. RAG 问答链路

- [ ] 4.1 实现 src/chat_manager.py（Redis 对话历史 + InMemory 降级，窗口记忆保留最近 N 轮）
- [ ] 4.2 实现 src/rag_chain.py（检索 → 重排序 → 含约束的 Prompt → Qwen-max 流式生成 → 引用提取）
- [ ] 4.3 实现引用来源生成逻辑（source + page + content snippet，Markdown 格式）
- [ ] 4.4 验证：端到端问答 "这个财年营收多少？" → 答案 + 可追溯到原文位置的引用

## 5. Gradio UI 界面

- [ ] 5.1 实现 src/app.py（gr.Blocks 两栏布局：知识库管理 + 文件上传 + 对话 + 引用展示）
- [ ] 5.2 实现空状态引导（首次使用欢迎文案 + 知识库为空提示）
- [ ] 5.3 实现异步上传 + 进度提示（threading 后台处理，UI 展示处理状态）
- [ ] 5.4 实现知识库切换时对话历史清空逻辑
- [ ] 5.5 验证：投资人演示全流程走通（上传 → 提问 → 流式答案 → 引用展示）

## 6. 评估与收尾

- [ ] 6.1 基于测试文档（厦门灿坤年报 + 茅台年报）构造 20+ QA 测试对
- [ ] 6.2 对比测试不同 chunk_size（512/768/1024）的 RAGAS 指标
- [ ] 6.3 实现 src/eval_ragas.py（加载 QA 对 → 生成答案 → 计算指标 → 保存 CSV）
- [ ] 6.4 编写 README.md（项目说明、启动指南、架构图、使用说明）
- [ ] 6.5 编写演示脚本 + 录制演示视频
- [ ] 6.6 验证：所有核心假设达标，RAGAS 报告存档，演示流程流畅
