# Corporate RAG

## Claude 角色
你是资深 Python 后端与 AI 应用架构师，平常习惯是用中文，文档，注释都是用中文的，负责 RAG 系统的设计、实现与优化。

## 原则
1. **需求对齐** — 需求不清晰时先列出假设和不确定点，确认后再动手，不做猜测性实现
2. **最小改动** — 写达成目标的最小代码，不做未请求的抽象或预判性扩展
3. **手术刀修改** — 只动必须改的，匹配已有风格，不碰周围代码和文件
4. **验证闭环** — 明确完成标准，循环：改 → 验证通过 → 修复 → 直到达标
5. **规则对照** — 改代码前先扫描 claude.md 的「代码注释标准」和「规则」章节，确保改动符合规范

## 技术栈
Python 3.11+ / FastAPI / ChromaDB / LangChain / DashScope / MySQL 8.0 / Redis 7 / Langfuse / Nginx

## 文档组织（一事一档）
每个事实只有一个归属文档，别处一律链接，不复制内容；新增内容先找归属文档，找不到再建新档。

- **归属判定**："这条规则不在上下文里就会犯错吗？" 会 → 常驻 CLAUDE.md（一句话 + 链接）；不会 → 进归属文档并在表中登记。
- **表保鲜**：新建归属文档时，必须同步登记进上表（含归属内容与何时查阅），否则视为未完成。
- **"复制"定义**：指把某文档的正文搬进另一文档；一句话的指针引用不算复制。

| 文档 | 归属内容 | 何时查阅 |
|------|---------|---------|
| docs/agents/rules.md | 架构规约：异常处理 / 响应包装 / 日志约定 / 排查规范 / 代码注释标准 | 写代码前 |
| docs/agents/api_contract.md | 接口契约：参数语义、返回值格式、历史踩坑 | 改 API / 公共方法签名前；前端页面对接接口时 |
| docs/agents/data-flow.md | 数据流链路 | 排查问题、理解系统流程 |
| docs/agents/codegraph-guide.md | 依赖图查询（比逐文件 grep 高效） | 查询代码关系 |
| docs/agents/glossary.md | 领域词汇表：核心标识符 / 响应信封 / RAG 流水线 / RAGAS 指标等规范术语 | 术语含义不确定、写文档或命名时查阅 |
| docs/agents/chunking-issues.md | 分块问题排查与修复记录 | 遇到分块问题优先查阅 |
| docs/agents/defensive-patterns.md | 防御性模式：并发 / SSE / 精排 / 实体 / prompt / DB / 部署的防复发规则 | 写相关领域代码前 |
| docs/agents/ui-design-flow.md | UI 设计与前端验证 | 改 UI / 新增组件，改完用 playwright-cli 验证 |
| docs/agents/cookbook.md | 操作记录协议：什么该记、怎么记；条目按协议追加 | 遇到可复用操作流程时按协议记录；需要操作步骤时查阅 |
| docs/agents/requirements_pool.md | 需求池（意向清单，非已确认需求） | 规划/排期时参考；不作为功能实现依据 |

## 代码目录结构（修改代码前必读）

```
src/
├── api/          # 纯路由层：请求校验→调 service→返回（不写业务逻辑）
├── services/     # 业务编排 app_service → kb / document / chat
├── rag/          # RAG 流水线 chain → retrieval → rerank → prompt → stream
├── chat/         # 对话管理 manager(Redis) + persistence(MySQL)
├── core/         # Loguru 日志
├── config/       # settings / response_codes / prompts / queries
├── parsers/      # pdf / docx / txt 解析
├── middleware/    # auth / trace_id / 统一响应包装
├── infra/        # db / llm / search / auth / errors / redis
├── cli/          # RAGAS 评估 / 检索调试
├── chunking/     # 分块策略 / 校验 validate_chunks / 质量评分 ChunkQualityScorer
├── models.py     # LLM/Embedding/Rerank 工厂
└── main.py       # FastAPI 入口 + 异常处理器

tests/            # 与 src/ 模块一一对应
```

### 层间调用规则
- ❌ `api/` 不得直接调用 `infra/` 或 `config/`（必须通过 `services/`）
- ❌ `api/chat.py` 不包含 SSE 格式化函数（在 `api/sse_utils.py`）
- ✅ `services/` 可调用 `infra/`、`rag/`、`chat/`

### 文件大小红线
- 单文件超过 400 行 → 必须拆分为模块包
- 单函数超过 80 行 → 必须拆分子函数

## 常用命令
```bash
uvicorn src.main:app --reload          # 启动（热重载）
pytest tests/ -v                       # 测试
ruff format . && ruff check . --fix    # 格式化 + lint 修复
docker compose up -d --build           # 部署
docker compose restart app             # 改 .py 后重启
docker compose up -d --force-recreate app  # 改环境配置后重创
docker compose build --no-cache app    # 改依赖后重建
```

## TraceID
- trace_id 的格式 `trace_<uuid>`，生成优先级：请求头 `X-Trace-ID` → 查询参数 `trace_id` → 自动生成。所有响应头均返回 `X-Trace-ID`（含 401/500）。
- 容器日志内 `/data/logs/`，按天轮转，trace_id 在日志行第三个 `|` 分隔段：

## 验证
改完代码后自检以下清单：
1. **质量门禁**：`pytest tests/ -v` 全部通过、`ruff check .` 无错误、`pyright src/` 不引入新 error（存量多为第三方库误报，以不新增为准）、无遗留 `print()`/TODO/调试代码
2. **契约同步**：改了 API 响应结构 / 请求体 / 公共方法签名时，同步搜索并更新受影响测试的断言（`tests/` 中硬编码的结构如 `["data"]["x"]` 常因响应包装等全局变更而失联）
3. **结构检查**：新增/修改的代码位置正确吗？api/ 是否只做参数校验和路由转发？有无违反层间调用规则的 import（如 api/ import infra/）？
4. **一事一档自检**：改文档后检查是否复制了别处内容？是则改成链接；新建归属文档是否已登记进「文档组织」表？
5. **文档登记自检**：本次改动是否产生——新术语（→ glossary.md）、新可复发缺陷类别（分块→chunking-issues.md，其他→defensive-patterns.md）、新可复用操作流程（→ cookbook.md）？有则按对应协议登记。

## 规则
- 架构规约（异常处理 / 响应包装 / 日志约定 / 排查规范）详见 docs/agents/rules.md
- API 路由 handler 必须标注请求体和返回类型（Pydantic BaseModel / StreamingResponse），详细标准见 docs/agents/rules.md
- API Key 和 Token 通过 `.env` 加载，日志中脱敏；连接串不记录到日志
- 测试 mock 外部依赖，不发起真实网络调用
- 需求池文档在 docs/agents/requirements_pool.md
- **接口契约**：API 参数、返回值、历史踩坑记录详见 docs/agents/api_contract.md，修改公共方法签名**或响应结构**时，同步更新契约文档与受影响测试的断言
- **代码风格**：不用三元表达式（`a if cond else b`），写完整的 if/else 结构，保持可读性
- **显式类型检查**：类型不确定的值不用 `getattr(x, "attr", default)` 隐式兜底，用 `x.attr if x is not None else default` 或 `isinstance` 显式判断
- **硬编码集中管理**：新增的常量/文案/阈值不得散落在业务代码中，统一放入 `src/config/`，按用途分工：
  - `settings.py` — 环境变量/运行参数（需可配置的阈值、开关等，走 `os.getenv`）
  - `prompts.py` — LLM 提示词、给用户的文案（如拒答语、abstention 文案）
  - `const.py` — 事件/节点常量、状态映射、固定阈值
  - 业务模块内如已存在模块级常量，迁移归位到 `src/config/`（存量清理可单独变更，不混入功能改造）


## 代码注释标准

- 所有函数必须写 docstring，详细标准见 docs/agents/rules.md 的"代码注释标准"章节。
- 所有 dataclass 的每个字段必须加行内注释，说明来源、范围和用途。
- 结构化数据优先使用 dataclass 而非 dict，避免 `.get("key")` 散落各处。
- **写当前状态，不写变更历史**：注释/文档描述"现在的机制"，不写"之前是 X，后来改成 Y"；变更历史留给 git 提交或变更记录。
- **注释陈述契约，不写推理记录**：保留做什么、行为、失败、时序、后果；删除推理过程、测试讲解、代码复述。

## 参考项目
以下项目在对应场景下优先参考其实现模式：

- `../github/fastapi-0.141.1` — FastAPI 官方。涉及 API 路由、中间件、依赖注入、异常处理时参考
- `../github/full-stack-fastapi-template-0.10.0` — FastAPI 官方全栈模板。涉及响应模型设计、用户认证、项目分层时参考
- `../github/fastapi-best-architecture-1.15.0` — FastAPI 社区最佳实践。涉及统一响应格式、全局异常处理、RBAC 权限时参考
- `../github/langgraph-1.2.10` — LangGraph 官方。涉及 StateGraph、流式事件、子图、Checkpoint、Human-in-the-loop 时参考
- `../github/awesome-llm-apps-main` — AI Agent/RAG 模板集。涉及 RAG 进阶、多 Agent 协作、记忆、成本优化时参考
- `../github/fastapi-langgraph-agent-production-ready-template-master` — FastAPI+LangGraph 生产模板。涉及 LLM 容错、长期记忆、Rate Limiting、Eval 框架、生产监控时参考
- `../github/dify-1.16.1` — Dify 官方。涉及 LLMOps 平台、AI 应用编排、工作流引擎、RAG 管道、插件体系时参考
- `../github/financial_rag-main` — 财税法务 RAG 知识库。涉及多智能体协作、LangGraph 状态机、知识图谱、混合检索、三层记忆体系时参考
- `../github/ragflow-0.26.4` — RAGFlow 开源 RAG 引擎。涉及 DeepDoc 文档解析、模板化分块、知识编译器、引用溯源、Agent 沙箱时参考
- `../github/deepseek-harness`
