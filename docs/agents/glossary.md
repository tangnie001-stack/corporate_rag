# 领域词汇表

> 项目规范术语。每个概念只有一个标准叫法，文档/代码/对话统一用此表词汇，避免歧义。

## 核心标识符

| 术语 | 定义 | 常见错误 |
|------|------|---------|
| `kb_id` | 知识库唯一标识，UUID 字符串；`""` 表示"不检索"（未绑定 KB，纯对话） | ❌ 传 `kb_name` |
| `doc_id` | 文档唯一标识，UUID | ❌ 传 MySQL 自增 ID |
| `session_id` | 会话标识，用于关联对话历史 | ❌ 传空字符串 |
| `chunk_id` | 向量库中的分块 ID，格式 `"{doc_id}:{index}"` | — |
| `trace_id` | 请求追踪 ID，格式 `trace_<uuid>` | — |

## 响应与追踪

- **响应信封**：统一响应包装 `{"code", "message", "data"}`。成功响应由各 handler 显式 `return ResponseModel(data=...)` 产生（`src/api/schema.py` 的 `ResponseModel`）；错误响应由 `src/main.py` 异常处理器（AppError / HTTPException / 兜底）产出。业务层只 `raise` 异常或返回 `ResponseModel`，不 `return JSONResponse`
- **SSE 事件流**：聊天流式输出的事件序列，`status → token → citation → done`

## 日志规范

- **分层前缀**：日志 message 以 `[层名]` 开头标识事件归属，完整前缀集（8 前缀开放登记制）见 docs/agents/logging-rules.md「前缀主表」
- **retrieval_signal**：检索行为信号日志前缀（独立于 `[层]` 前缀的已知例外），类型集与行格式由 `src/core/log_events.py` 的 `Signal` 枚举与 helper 定义；保留原因与两条 grep 模式见 docs/agents/logging-rules.md「已知例外」
- **五级级别语义**：debug/info/warning/error/exception 各语义见 docs/agents/logging-rules.md「级别语义」
- EventSpec 注册表：src/core/log_event_specs.py 中 name/prefix/level/fields 的事件数据表（经 src/core/log_events.py re-export），helper 渲染/路由级别的事实源；Event 枚举与注册表 import 期一致性校验
- token 安全字符集：`^[A-Za-z0-9_./:@-]+$`，命中则日志字段裸写，否则引号 + JSON 转义
- replay drift 对照：replay_trace CLI 用当前配置重放，事件行记录的"当时参数"并排对比并标注差异

## RAG 流水线

- **chunk**：文档切分后的最小检索单元，由 `src/chunking/` 分块策略产生
- **retrieval**：检索，从 ChromaDB 按 kb 召回相关 chunk
- **rerank**：精排，对召回结果重排，产出 `contexts` 进入 LLM
- **contexts**：精排后拼入 LLM prompt 的上下文片段
- **kb**：知识库（knowledge base），文档与向量的隔离单位
- **dedup（按 doc_id 去重）**：`src/rag/retrieval.py::_dedup_by_doc_id` 对召回结果按文档分组，每文档至多保留前 N 条，提升上下文多样性；N 取 `RETRIEVAL_MAX_PER_DOC`（`src/config/settings.py`，环境变量可覆盖，默认 1 与旧行为一致）
- **RETRIEVAL_MAX_PER_DOC**：检索去重上限配置项，含义见「dedup」；为 A/B 实验变量（N=1 vs N=2/3，结论待真实 KB 评估后写入 change 记录）

## Agent 状态图（LangGraph）

- **AgentState**：节点间共享的状态对象，字段名以 `LangGraphNode.*` 常量作为 key（`src/agents/graph/state.py`）
- **LangGraphNode.\***：节点名常量，字段生产-消费矩阵的 key
- **LangGraphEvent.\*** / **LangGraphKey.\***：SSE 流解析用的事件类型 / 事件 dict key

## 对话行为

- **abstention**（拒答）：检索无达标 context 时直接返回拒答文案，不回 LLM
- **clarification**（追问）：分类器发现缺失实体时向用户发起追问（`CLARIFICATION_ENABLED` 开关控制）

## Web 搜索兜底

- **kind**：引用来源类型，取值 `kb`（知识库）/ `web`（网络搜索），默认 `kb`；承载于 `SSEInteractionTexts.CITATION_KIND_KB` / `CITATION_KIND_WEB`，贯穿 `RAGContext.kind` 与 citation 事件
- **search_web**：联网搜索工具，KB 检索不达标时经 Tavily 兜底检索网页，结果与 retrieve_kb 共用 `tool_contexts` 编号（kind=web 区分来源）；受 `WEB_SEARCH_ENABLED` 开关与 `WEB_SEARCH_PER_TURN_LIMIT` 限次控制
- **web_search**：SSE 状态阶段（`SSEStatusEvent.stage` 取值），联网搜索开始/完成状态提示（"正在联网搜索..." / "联网搜索完成，正在分析..."）

## 技能委派（主从委派）

| 术语 | 定义 | 常见错误 |
|------|------|---------|
| `skill` | 磁盘声明式能力文件（`skills/<name>/SKILL.md`，frontmatter 声明元数据 + 正文按 context 存方法论/子代理 prompt）；skill 是内容/配置而非代码 | ❌ 与开发期 `.claude/skills/` 工具链 skill 混为一谈 |
| `SkillRecord` | skill 文件解析后的运行时对象（name/description/context/inline_prompt/agent_prompt 等），由 `SkillLoader` 产出（`src/agents/skills/models.py`） | ❌ 直接用 SKILL.md 原文当结构体 |
| inline 执行 | skill `context=inline`：方法论注入主 agent 上下文，主 agent 自己执行 | — |
| fork 执行 | skill `context=fork`：`SkillExecutor` 生成零工具子代理独立深度分析，结果纯文本回主 agent | — |
| `delegate_task` | 主 agent 委派工具 `delegate_task(task, skill)`，按命中 skill 的 context 分发 inline/fork；unknown 返回"skill 不存在 + 可用列表" | 引用会指向子代理产出（实际引用仍只指向主 agent 自身检索来源） |

## 推理思考文本

- **reasoning_content**：模型的流式思考文本（chain-of-thought）。DashScope 等第三方把思考增量放在 `delta.reasoning_content`，OpenRouter 等用 `delta.reasoning`；`ChatQwenWithReasoning` 统一累积到 `AIMessageChunk.additional_kwargs["reasoning_content"]`，供上层读取与展示

## 评估指标（RAGAS）

| 指标 | 含义 |
|------|------|
| `faithfulness` | 忠实度：答案是否忠于检索上下文 |
| `answer_relevancy` | 答案相关性：是否切题 |
| `context_precision` | 上下文精确率：召回的 chunk 有多相关 |
| `context_recall` | 上下文召回率：相关信息有多少被召回 |

## 基础设施

- **ChromaDB**：向量数据库，按 kb 分 collection
- **MinIO**：文档对象存储
- **LiteLLM**：LLM 代理，`LLM_BASE_URL` 指向（默认 `http://litellm-proxy:4000`）
- **DashScope**：通义千问系列模型的提供商（Embedding / LLM / Rerank）

## 如何更新

- 新增概念/术语（新模块、新事件类型、新指标、新配置项）且会被多处使用时，登记进对应分区。
- 同一概念已有标准叫法时，沿用本表词汇，不另造新词。
- 文档、代码、命名中的术语一律与本表一致；发现不一致时以本表为准并修正别处。
