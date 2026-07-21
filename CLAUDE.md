# Corporate RAG

## Claude 角色
你是资深 Python 后端与 AI 应用架构师，平常习惯是用中文，文档，注释都是用中文的，负责 RAG 系统的设计、实现与优化。

## 原则
1. **需求对齐** — 需求不清晰时先列出假设和不确定点，确认后再动手，不做猜测性实现
2. **最小改动** — 写达成目标的最小代码，不做未请求的抽象或预判性扩展
3. **手术刀修改** — 只动必须改的，匹配已有风格，不碰周围代码和文件
4. **验证闭环** — 明确完成标准，循环：改 → 验证通过 → 修复 → 直到达标

## 技术栈
Python 3.11+ / FastAPI / ChromaDB / LangChain / DashScope / MySQL 8.0 / Redis 7 / Langfuse / Nginx

## 代码目录结构（修改代码前必读）

```
src/
├── api/                  # 纯路由层：只做请求→调用 service→返回，不写业务逻辑
│   ├── model/            #   请求/响应 Pydantic 模型
│   │   ├── request.py
│   │   └── response.py
│   ├── auth.py           #   登录/校验/登出/匿名
│   ├── chat.py           #   流式 RAG 问答 SSE
│   ├── dependencies.py   #   依赖注入（get_app_service）
│   ├── documents.py      #   文档上传/列表/状态/分块预览/删除
│   ├── health.py         #   健康检查 + 前端配置
│   ├── kb_eval.py        #   评估结果查询
│   ├── knowledge_base.py #   知识库 CRUD
│   ├── llm_test.py       #   LLM 连通性测试
│   ├── sessions.py       #   会话列表/消息/删除
│   └── sse_utils.py      #   SSE 格式化函数（纯工具，仅依赖 json）
├── services/             # 业务服务层
│   ├── app_service.py    #   统一编排入口（组合 KB/Document/Chat 三个子 service）
│   ├── chat_service.py   #   问答服务
│   ├── document_service.py # 文档处理服务
│   └── kb_service.py     #   知识库服务
├── rag/                  # RAG 流水线
│   ├── chain.py          #   RAGChain 主类（编排检索→精排→生成，含 Langfuse Tracing）
│   ├── context.py        #   RAGContext 数据类
│   ├── retrieval.py      #   检索 + 查询改写（纯函数）
│   ├── prompt.py         #   Prompt 构建（纯函数）
│   └── stream.py         #   流式生成（纯函数）
├── chat/                 # 对话管理
│   ├── manager.py        #   ChatManager（Redis/InMemory 会话 CRUD）
│   └── persistence.py    #   MySQL 持久化
├── core/                 # 基础设施核心
│   └── logging.py        #   Loguru 日志配置
├── config/               # 配置与常量
│   ├── __init__.py       #   配置导出
│   ├── settings.py       #   环境变量 + 可调参数
│   ├── prompts.py        #   LLM 提示词模板
│   ├── queries.py        #   SQL 语句
│   └── response_codes.py #   异常码枚举
├── eval/                 # 评估
│   └── chunk_scorer.py   #   分块质量评估（结构完整性/SBR/粒度CV）
├── parsers/              # 文档解析器
│   ├── router.py         #   解析器路由（按扩展名分发）
│   ├── base.py           #   解析器基类
│   ├── pymupdf_parser.py #   PDF 解析
│   ├── docx_parser.py    #   DOCX 解析
│   └── txt_parser.py     #   TXT 解析
├── middleware/            # 中间件
│   ├── auth.py           #   认证中间件
│   ├── response_processor.py # 统一响应包装（code/message/data）
│   └── trace_id.py       #   TraceID 注入
├── infra/                # 基础设施
│   ├── db/               #   数据库
│   │   ├── mysql_db.py   #     MySQL CRUD（异步 aiomysql）
│   │   ├── vector_store.py #   ChromaDB 向量存储
│   │   └── file_store.py #     MinIO 文件存储
│   ├── chunking/         #   分块
│   │   ├── router.py     #     策略检测与路由
│   │   ├── enhancer.py   #     分块增强（去重/修复）
│   │   ├── validator.py  #     分块质量校验
│   │   └── strategies/   #     分块策略
│   ├── auth/             #   用户认证
│   │   └── user_auth.py
│   ├── llm/              #   LLM 基础设施
│   │   ├── langfuse_tracing.py # Langfuse Tracing 封装
│   │   ├── prompt_manager.py   # Prompt 模板管理
│   │   └── trace_context.py    # 异步上下文 trace 传递
│   ├── search/           #   混合检索
│   │   ├── bm25_index.py #     BM25 索引
│   │   └── query_router.py #   查询路由
│   ├── desensitize.py    #   脱敏工具
│   ├── errors.py         #   异常定义（BusinessError/AuthError/SystemError）
│   └── redis_client.py   #   Redis 客户端
├── cli/                  # CLI 工具
│   ├── eval_ragas.py     #   RAGAS 评估入口
│   ├── eval_ragas_generate.py # 测试集生成
│   ├── check_retrieval.py    # 检索质量检查
│   └── compare_retrieval.py  # 检索策略对比
├── models.py             # LLM/Embedding/Rerank 工厂
└── main.py               # FastAPI 应用入口 + 异常处理器

tests/                 # 与 src/ 模块一一对应：api / chat / config / eval / infra / middleware / parsers
```

### 层间调用规则
- ❌ `api/` 不得直接调用 `infra/` 或 `config/`（必须通过 `services/`）
- ❌ `api/chat.py` 不包含 SSE 格式化函数（在 `api/sse_utils.py`）
- ✅ `services/` 可调用 `infra/`、`rag/`、`chat/`
- ✅ `rag/chain.py` 编排 retrieval / prompt / stream，不包含实现细节
- ✅ `chat/manager.py` 不包含 MySQL 持久化逻辑（在 `chat/persistence.py`）

### 文件大小红线
- 单文件超过 400 行 → 必须拆分为模块包
- 单函数超过 80 行 → 必须拆分子函数

## 数据流
文档上传 → parsers/router 解析 → infra/chunking 分块 → infra/db/vector_store 入库
用户提问 → rag/chain 检索/重排序/生成 → api SSE 推送前端
session/消息 → chat/manager(Redis) 写 + chat/persistence(MySQL) 落盘 → api/sessions 读

## 依赖图
查询代码关系时参考 @docs/agents/codegraph-guide.md（比逐文件 grep 高效）。

## 常用命令
```bash
uvicorn src.main:app --reload  # 启动（热重载）
pytest tests/ -v           # 测试
ruff format . && ruff check . --fix  # 格式化
python -m src.cli.check_retrieval   # 检查检索
python -m src.cli.eval_ragas            # RAGAS 评估
docker compose up -d --build        # 部署
```

## TraceID
trace_id 的格式 `trace_<uuid>`，生成优先级：请求头 `X-Trace-ID` → 查询参数 `trace_id` → 自动生成。所有响应头均返回 `X-Trace-ID`（含 401/500）。

容器日志内 `/data/logs/`，按天轮转，trace_id 在日志行第三个 `|` 分隔段：
```bash
docker exec corporate-rag-app grep '<trace_id>' /data/logs/app_*.log
```

## 验证
改完代码后自检以下清单：
1. `pytest tests/ -v` 全部通过
2. `ruff check .` 无错误
3. 无遗留 `print()`、TODO 或调试代码
4. 改前端时用 playwright-cli 验证交互
5. **代码位置检查**：新增/修改的代码放在正确的目录了吗？
6. **层次检查**：api/ 里是否只做了参数校验和路由转发，没有写业务逻辑？
7. **import 检查**：有没有违反层间调用规则的 import（如 api/ 里 import infra/）？
8. **文件大小检查**：单文件是否超过 400 行？是否需要拆分？
9. **测试对应检查**：如果增加了新模块，是否更新了 tests/ 对应目录的测试？

## 设计流程
改 UI 或新增组件时参考 @docs/agents/ui-design-flow.md。

## 规则
- 架构规约（异常处理 / 响应包装 / 日志约定 / 排查规范）详见 @docs/agents/rules.md
- API 路由 handler 必须标注请求体和返回类型（请求用 Pydantic BaseModel，返回也用 Pydantic BaseModel 描述 data 结构，SSE 标注 StreamingResponse）
- git 操作由你手动执行，不会自动 commit/push
- API Key 和 Token 通过 `.env` 加载，日志中脱敏；连接串不记录到日志
- 测试 mock 外部依赖，不发起真实网络调用
- 需求池文档在docs/requirements_pool.md
- **代码风格**：不用三元表达式（`a if cond else b`），写完整的 if/else 结构，保持可读性


## 代码注释标准

所有函数必须写 docstring，详细标准见 @docs/agents/rules.md 的"代码注释标准"章节。

## 经验总结
分块问题排查和修复记录详见 @docs/agents/chunking-issues.md，遇到分块相关问题时优先查阅。

