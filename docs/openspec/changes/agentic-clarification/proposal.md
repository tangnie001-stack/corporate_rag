# agentic-clarification

## Why

当前澄清链路是"classify 预判缺失实体 → 发澄清事件 → **turn 结束** → 用户重发组合消息 → **新 turn 重跑全流程**"（`src/services/agent_service.py` 的 clarify 分支 + 前端 `chat.html:912` 关流重开）。交互体验差：澄清=问答重置，且用户多次补齐信息会连环问。

对照 deepseek-harness 的 `ask_user_question` 工具：澄清是 agent 推理中途的**工具调用**，阻塞等用户回答后**同一 turn 继续**。本 change 把澄清升级为 agent 自主的工具式交互，并补齐 agent 循环所需的工程化（abort 语义、结构化追踪）。

## What Changes

- **ask_user 工具（核心）**：新增 `ask_user` 工具替代 classify 澄清分支。模型推理中需要补充信息时自主调用；问题通过 SSE 推给前端；用户答案经 `POST /api/chat/clarify-answer` 回传，作为工具结果回喂，**同一 turn 继续**。
- **选项来源（KB 注入）**：ask_user 工具按 `dimension`（company/period/metric）查询 `aggregate_kb_entities`（`src/infra/search/query_router.py:124`）填充真实候选 options，无候选 fallback `SUGGESTIONS_MAP`；模型只负责措辞，不生成候选（防编造）。
- **前端 composer 化（**BREAKING**）**：澄清时**输入区被问题表单接管**（隐藏 textarea），结构化选项（单选 radio/多选 checkbox，label+description）+ 自定义文本；提交改为 `POST /clarify-answer`，**不再关流重开**，同一 EventSource 继续收 token。
- **事件契约（**BREAKING**）**：新增 `SSEAskUserEvent`（携带 questions/options/multi_select，对齐 dsh schema）与 `SSEAbstentionEvent`（abstention 标识 + 转人工标记）；`SSEClarificationEvent` **退役**（classify 删除后无预判来源，前端单路 composer 渲染）。流程状态事件改按事件类型发（模型思考/工具检索），`SSE_STATUS` 节点名映射删除。
- **引用编号（**BREAKING**）**：`retrieve_kb` 多次调用采用**全局递增编号**——每次调用 offset = 当前 `tool_contexts` 长度，返回 `[offset+1..offset+N]` 并追加 RAGContext；`format_node` 提取逻辑不变，多轮检索编号不冲突。
- **传输保持 SSE**：不引入 WebSocket。澄清问题走 SSE 事件，答案走 HTTP POST，均为单向/请求-响应，SSE 足够。
- **abort 语义**：用户关闭页面/跳转 → 请求取消 → agent 立即停止。per-request 注入式 abort 信号绑定浏览器断连；`llm.astream()` 异步流（取消可传播，修复线程池停不掉的问题）；ask_user 挂起 Future 绑定信号并清理注册表。双路事件合并（graph task + SSE task）用 **queue 哨兵收尾 + Task B finally 联动**（set abort + cancel task_a + gather）。
- **并发防护**：per-session 锁（Redis SETNX 带 TTL）拒绝同一 session 的并发 `/chat/stream` 请求（409），前端禁输入之外的后端兜底；finally 释放防死锁。
- **历史窗口**：agent 循环历史注入采用**最近 N 轮 + token 双上限**（默认 10 轮 + context 30% 阈值从最旧截断），最近 1 轮完整注入保澄清/追问上下文；滚动摘要列为后续增强。
- **转人工工具（阶段二）**：`escalate_to_human` 工具（fire-and-forget，创建工单+返回单号）；工单状态流转/分配/通知/控制台属产品层，不在工具内。阶段一只保留 abstention 时的前端"转人工咨询"入口。
- **答案反馈**：每条回答 👍/👎 + 可选原因，`POST /api/feedback` 落库，对接现有 RAGAS eval。
- **工程化补充**：全流程节点日志可查（每个图节点进出日志 + trace_id 还原执行链）、工具调用日志（iteration/tool/args/result/latency）、护栏命中告警（迭代上限/ask_user 超时/abort，含已耗 token）、循环内 token 用量聚合。**审计日志（澄清/转人工长期留存的独立记录）不在本 change 范围**，后续单独变更。
- **历史写入时机**：user 消息（原 query + 澄清答案）**到达即写** Redis；assistant 消息**完成时写完整、abort 时有 token 写 interrupted 部分**（Redis+MySQL），不产生孤儿消息。
- **删除 classify 节点与固定流水线（**BREAKING**）**：图简化为 `kb_router → agent 循环 → format`。闲聊/事实/澄清/转人工全由 agent 决定；classify 的三级路由、`skip_retrieval` 标记、确定性 abstention 分支一并删除（纯 agent 成本与保留闸门相当，且消除两条澄清源冲突）。
- **深度思考开关**：前端输入区"深度思考"开关（默认不选中）→ `/chat/stream?deep_thinking=` → agent 主 LLM `enable_thinking`（经 per-call `extra_body`，已验证 langchain 动态透传）。qwen3.7-flash 混合思考模式默认开启，显式传 `false` 关闭；思考过程走 `reasoning_content`（langchain 丢弃，前端不展示思考文本）。qwen3.7-flash 不支持 `reasoning_effort` 档位，仅开关。

## Capabilities

### New Capabilities

- `clarification-interaction`: ask_user 工具化澄清（agent 自主触发、KB 注入 options、结构化答案契约、同 turn 继续）
- `composer-ui`: 前端澄清交互 composer 化（输入区接管、radio/checkbox+custom、POST 提交不重开流、SSE 保持连接）
- `request-abort`: 请求生命周期 abort 语义（断连即停、注入式信号、ask_user 绑定、孤儿消息处理）
- `human-escalation`: 转人工工具（阶段二 escalate_to_human + 工单创建；阶段一 abstention 前端入口）
- `answer-feedback`: 答案反馈采集（👍/👎 + 原因，落库对接 eval）
- `agent-loop-observability`: agent 循环可观测性（全流程节点日志可查、工具调用日志、护栏告警、token 聚合）
- `chat-thinking-toggle`: 前端"深度思考"开关 → `deep_thinking` 参数 → agent LLM `enable_thinking`（per-call extra_body；思考过程不展示）

### Modified Capabilities

- `retrieval-quality`: 固定流水线删除后，abstention 触发路径全部变为"模型看到空工具结果后决策"（可转人工/可 ask_user/可 abstain）；不再有"确定性 abstention 分支"

## Impact

- **Graph**：`workflow.py` 删 classify/rewrite/retrieve/rerank/generate 节点（检索与生成并入 agent 循环），`kb_router → agent 循环（retrieve_kb + ask_user [+ escalate]）→ format`
- **State**：`state.py` 加 `tool_contexts`/`_agent_iterations`/`_ask_count`/abort 信号/挂起澄清字段；删 classify 相关字段与常量
- **Nodes**：`nodes.py` 删 `make_classify_node`/`make_rewrite_node`/`make_retrieve_node`/`make_rerank_node`/`make_generate_node`；新增 `make_agent_node`（model↔tools 循环，护栏计数存 AgentState）
- **Tools**：新增 `src/agents/tools/rag_tools.py`（`retrieve_kb` 全局递增编号、`ask_user`）、阶段二 `escalate_to_human`
- **Service**：`agent_service.py` 双路事件合并（queue 哨兵收尾 + Task B finally 联动）、abort 信号接线、per-session 并发锁、历史窗口注入
- **API**：新增 `POST /api/chat/clarify-answer`（user 消息到达即写；超时后提交返回 404）、`POST /api/feedback`；`sse.py` 新增 `SSEAskUserEvent`/`SSEAbstentionEvent`、退役 `SSEClarificationEvent`
- **Frontend**：`chat.html` composer 化改造（输入区接管、不关流、POST 提交）
- **Config**：`prompts.py` 工具描述与 agent system prompt；`const.py`/`settings.py` `MAX_AGENT_ITERATIONS`/`ASK_USER_TIMEOUT`/`MAX_ASK_PER_TURN`/`HISTORY_MAX_TURNS`/`HISTORY_TOKEN_RATIO`/会话锁 TTL
- **Logging**：`src/core/logging.py` agent 循环结构化日志格式
- **Tests**：分层测试——工具单测（编号/选项/超时/上限）、双路合并注入式单测（哨兵/取消）、SSE 集成测试（httpx 同流续答）、abort 集成测试（断开即停）
- **关系**：**归档 `query-rewrite-and-graph-simplification`**（0/31 未合入），其 rewrite/classify/grader/batch-clarification 任务作废，rerank 去阈值与 RRF 多查询融合吸收进本 change 的 retrieve_kb 工具
