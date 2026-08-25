# agentic-clarification — Design

## Context

corporate_rag 当前澄清链路：classify 预判缺失实体 → `SSEClarificationEvent` → 前端 `chat.html:912` `source.close()` 关流 → `submitClarification` 调 `startSSE(组合消息)` 重开新 turn。交互=问答重置。

对照 deepseek-harness：澄清是 `ask_user_question` 工具（`packages/interaction/tool-ask-user/`），模型推理中途自主调用，工具阻塞等答案，答案回喂**同一 turn 继续**；前端 composer 接管输入区，结构化选项提交。

本 change 把澄清升级为 agent 工具式交互，**删除 classify 节点与固定流水线**（图简化为 `kb_router → agent 循环 → format`），并补齐 agent 循环工程化（abort、并发防护、历史窗口、可观测性）。技术栈：LangGraph 1.2.9（`bind_tools`/`ToolNode`/`tools_condition` 已确认可用）、FastAPI StreamingResponse、Redis 历史、loguru + trace_id + Langfuse。

## Goals / Non-Goals

**Goals:**
- ask_user 工具化澄清，同一 turn 继续
- 删除 classify 节点与固定流水线，图简化为 `kb_router → agent 循环 → format`
- KB 注入真实候选选项（防模型编造）
- 前端 composer 化（输入区接管、不关流、POST 提交）
- 传输保持 SSE（不引入 WebSocket）
- 关闭页面即停止（abort 语义，注入式信号）；并发防护；历史窗口
- 阶段二 escalate_to_human 工具；答案反馈；agent 循环可观测性

**Non-Goals:**
- 后台续跑（断连后继续执行）——RAG 回答是秒级~1 分钟，收益不抵架构复杂度；执行/传输分层已为将来留缝
- 最终答案人工审核节点（interrupt gate）——内部问答不需要阻塞式审批；对外披露场景再引入
- 工具操作审批（dsh user-approval）——检索是只读操作，无危险动作
- 完整 session 事件日志（dsh append-only log + OTel 管道）——本 change 不实现；其"会话续接保真度"价值列为后续探索（见 Open Questions），不在此承诺方案
- WebSocket 传输

## Decisions

### D1. ask_user 工具 + 挂起注册表 + 双路事件合并（哨兵收尾）

工具执行流程：
```
ask_user 工具（rag_tools.py）:
  1. 生成 questions（模型措辞 + KB 注入 options）
  2. 把问题推入 per-request 澄清通道（asyncio.Queue）
  3. 在挂起注册表（session_id → Future）登记
  4. await Future（绑定 abort 信号 + ASK_USER_TIMEOUT）
  5. 答案/超时/取消 → 返回 ToolMessage
agent_service 的 SSE 生成器（双任务）:
  Task A: 跑 graph.astream_events → 事件推入 queue
  Task B: drain queue → yield SSE 事件（token/status/citation/ask_user）
```

**收尾机制（哨兵 + finally 联动）**：
- queue 用哨兵对象收尾：Task A 异常时放 `ErrorMarker`、正常结束时放 `EndMarker`；Task B 遇到哨兵停止（ErrorMarker → 输出 `SSEErrorEvent` 后 break）
- Task B 的 `finally`：set abort 信号 + `task_a.cancel()` + `await asyncio.gather(task_a, return_exceptions=True)`——无论 Task B 因何退出（客户端断连被取消/异常/自然结束），graph 执行都被终止，ask_user Future 被唤醒
- 工具阻塞时 `astream_events` 无新事件，但 Task B 在 `await queue.get()` 保持 SSE 连接存活。

替代方案：把澄清事件绕开 queue 直接在 api 层转发——不可行，工具与 SSE 生成器不同协程，需共享通道。

### D2. KB 注入 options（而非模型生成）

模型传 `dimension`，工具 handler 调 `aggregate_kb_entities`（`query_router.py:124`）取真实候选填充 options，fallback `SUGGESTIONS_MAP`。
理由：财务场景选项=知识库实体，模型生成可能编造不存在的公司/期间 → 用户选中后检索空 → abstention 死循环。dsh 通用 harness 无此约束。

### D3. 传输保持 SSE，不用 WebSocket

dsh 用 WebSocket 仅做**下行**事件流（`websocket-downlink.ts`，上行仍 HTTP），因为它是编辑器式 UI 需要服务器主动推送 + 双向 RPC。我们的交互全是请求-响应/单向：
- 下行（token/status/clarification/done）：SSE 覆盖
- 上行（提问、澄清答案、反馈）：HTTP GET/POST 覆盖
澄清问题走 SSE 事件、答案走 POST，无需双向通道。

### D4. abort 语义：per-request 注入式信号

```
api/chat.py: 创建 asyncio.Event（abort 信号）
  检测断连：StreamingResponse 任务被取消时 set；或 request.is_disconnected()
agent 循环: 每步边界检查信号（LLM 前/工具前/ask_user await 前）
LLM: 用 llm.astream()（异步），取消可传播——修复现状"generate_node 同步跑线程池、取消杀不掉线程"的缺陷
ask_user: Future 绑定信号，断连立即 resolve(cancelled) + 清理注册表
```
信号是**外部注入**而非硬编码进 graph——将来做后台续跑时，信号来源从"浏览器断连"换成"用户显式取消"即可，执行层不变。

### D5. 前端 composer 化（chat.html）

- `ask_user` 事件监听器（SSEAskUserEvent）：**不 `source.close()`**（现状 `clarification` 监听器在 912 行关流，删除），state=CLARIFYING，隐藏 textarea，渲染表单（radio/checkbox + custom）
- `submitClarification`：改为 `fetch POST /api/chat/clarify-answer {session_id, answers}`，不调 `startSSE`
- `done`：原流正常关闭，恢复输入区
- 断连处理：CLARIFYING 期间 `error`/`onerror` 移除表单恢复输入（EventSource 自动重连会重放 query——断连时前端显式 `source.close()` 放弃重连，提示重问）

### D6. escalate_to_human（阶段二，fire-and-forget）

与 ask_user 同族工具基建但**不阻塞**：创建工单（reason/context_summary/attempted_actions/session_id）→ 返回工单号 → agent 收尾。工单状态流转/分配/通知/控制台是产品层，工具只创建。护栏：per-turn 上限 1 次 + system prompt 约束。

### D7. 工程化：结构化追踪 + 护栏告警 + token 聚合

沿用 loguru 键值对格式（trace_id 已在行内）：
- 每 step：`iteration= tool= args_summary= result_summary= latency_ms= tokens=`
- 护栏命中（迭代上限/ask_user 超时/超限/abort）→ warn 级，含 query 与轨迹
- turn 结束：循环总 token 用量 + 调用次数
Langfuse 已有 generation 级追踪，结构化日志补足"无 Langfuse 时可还原循环序列"。

### D8. 引用编号全局递增

`retrieve_kb` 每次调用时 `offset = len(state.tool_contexts)`，返回文本编号 `[offset+1 .. offset+N]` 并追加 RAGContext 进 `tool_contexts`；工具通过 `InjectedState` 读写 state。`format_node` 逻辑不变（提取 `[n]` → 校验 `1<=n<=len(tool_contexts)` → 映射）。模型每次看到工具返回的真实编号，跨轮引用不冲突；跨 turn 的旧编号因 `tool_contexts` 每请求重建而自然失效（被 valid_numbers 过滤）。

### D9. 历史窗口（最近 N 轮 + token 双上限）

agent 循环历史注入策略：保留最近 N 轮（默认 10）user/assistant 对；历史消息总 token 超过 context 窗口 30% 时从最旧截断；最近 1 轮完整注入（保澄清/追问上下文）。**截断位置：初始注入前**（agent 节点入口对 `_history` 截断，再转 LangChain 消息）；**循环内 messages 不截断**（每轮工具结果必须保留，否则推理链断裂；有 `recursion_limit` 兜底）。澄清答案（已作为 user 消息到达即写 Redis）在窗口内自然参与。替代方案滚动摘要（dsh compaction）列为后续增强，与会话续接保真度探索关联。

### D10. 并发防护（per-session 锁）

同一 session 已有活跃 `/chat/stream` 时拒绝新请求。实现：Redis `SETNX chat_lock:{session_id}` 带 TTL（默认 30s，覆盖单次最长生成），请求结束 finally 释放；获取失败返回 409 "当前会话正在处理中"。前端禁输入挡常见情况，锁是后端兜底（双 tab/异常重放）。

### D11. 历史写入时机

- **user 消息**（原 query、澄清答案）：**到达即写** Redis——用户真实输入，abort 也保留（与 request-abort spec 一致）
- **assistant 消息**：**完成时写完整**、**abort 时有 token 写 interrupted 部分**（Redis+MySQL）
- 澄清答案在模型上下文是 ToolMessage（同 turn），在历史是 user 消息（跨 turn 上下文）——两轨并存，不冲突

### D12. 护栏计数状态存放（修正：节点走 AgentState，工具走 contextvar）

迭代上限（`MAX_AGENT_ITERATIONS`）：存 AgentState（`_agent_iterations`），由 agent 节点/条件边检查自增——节点可写 state，天然 per-request 隔离。ask 上限（`MAX_ASK_PER_TURN`）：**不能存 AgentState**——工具无法写 state（LangGraph 工具返回值只成为 ToolMessage，state 更新必须由节点返回值生效），检查与自增必须在 ask_user 工具内完成，故存 `RequestContext.ask_count`（contextvar）。同时设 LangGraph `recursion_limit` 作硬背折。不采用工具闭包计数（图在 AgentService 初始化编译一次、工具跨请求共享，并发 session 会串计数）。

### D13. per-request 对象传递：contextvar + InjectedState 分工

ask_user 工具需要的 per-request 对象（澄清通道 queue、abort 信号、挂起注册表）不在 graph state 内（state 需可序列化），`InjectedState` 只给 state 数据。工具闭包在 `AgentService` 初始化构建、跨请求共享，塞闭包会并发串号。

分工：**contextvar 传 per-request 对象，InjectedState 传 state 数据**——
- `agent_service.stream_chat` 每次请求设置 contextvars（`current_clarify_channel`/`current_abort_signal`/`current_registry`），graph 在同一 asyncio task 执行，工具调用时自动可见，并发 session 天然隔离
- 关键约束：ask_user **必须是 async 工具**（LangGraph 把 sync 工具丢线程池，线程池丢失 contextvar）
- 计数/`tool_contexts`/kb_ids 等数据经 `langgraph.prebuilt.InjectedState` 读写（已验证此版本可用）

### D14. ask_user 超时后提交处理

`ASK_USER_TIMEOUT` 触发后：Future 以超时 resolve → agent 收尾 → turn 结束 → SSE done。用户此时才提交：
- 后端 `POST /clarify-answer` 注册表查无该 session 挂起 Future → 返回 **404**（"该澄清问题已超时或不存在"），不写历史
- 前端 `CLARIFYING` 状态收到 done → 关闭 composer、恢复输入、显示"该问题已超时，请重新提问"
- 前端 submitClarification 的 fetch 非 2xx → 关闭 composer + 提示重问（覆盖 POST 先于 done 到达的竞态）

### D15. abstention 判定与前端标识

abstention 是模型决策（token 流），前端无法识别。后端在 agent 循环结束时综合判定：`state.tool_contexts` 为空 **或** 最终答案匹配 `ABSTENTION_MARKERS`（复用现有 `nodes.py` 逻辑）→ 发 `SSEAbstentionEvent`（携带"可转人工"标记）。前端收到 → abstention 样式 + "转人工咨询"入口（阶段一纯 UI + 会话标记，阶段二由 escalate 工具路径接管）。

### D16. answer 提取

`make_agent_node` 循环结束（模型不再调工具）后，取 messages 最后一条 AIMessage，将 content 规范化（str 或 content blocks → 拼接文本）写入 `state.answer`；`model_used`/`is_fallback` 由 `agent_service` 的 astream_events 改从 `on_chat_model_end` 捕获（agent 节点最后一次 LLM 调用），替代 Generate 节点捕获。citations 逻辑不变（format_node 读 state.answer + tool_contexts）。

### D17. AgentState 与 LangGraph messages 融合

`AgentState` 新增 `messages: Annotated[list[BaseMessage], add_messages]` 字段（默认 []），循环内 model 输出 + ToolMessage 追加。初始注入：agent 节点第一轮前把 system prompt + `_history`（ChatMessage → LangChain 消息，复用 `build_prompt` 转换逻辑）+ 当前 query 组装为初始 messages。`_history` 保留为"初始注入数据源"（classify/rewrite/generate 删除后仅此消费方）。ToolNode 用默认 `messages_key="messages"`。

### D18. 状态事件按事件类型发

agent 化后无节点，`SSE_STATUS` 节点名映射失效。状态事件改按事件类型发（复用 `SSEStatusEvent` 结构，前端 status-tag 渲染不变）：
- `on_chat_model_start`（agent 节点）→ "正在思考..."
- `on_tool_start`（retrieve_kb）→ "正在检索相关文档..."
- `on_tool_start`（ask_user）→ 不发状态（composer 接管输入区）
- `on_tool_end`（retrieve_kb）→ "检索完成，正在分析..."
- 删除 `SSE_STATUS` 节点名映射表

### D19. query-rewrite-and-graph-simplification 处置

该 change 为 in-progress、0/31 完成、未合入。本 change 删除 classify/rewrite/retrieve/rerank/generate 节点后其大部分能力作废：**归档 `query-rewrite-and-graph-simplification`**，把仍有效部分吸收进本 change——rerank 去阈值逻辑并入 `retrieve_kb` 工具；RRF 多查询融合并入 `retrieve_kb`（若工具支持多查询参数）；rewrite/classify/grader/batch-clarification 任务作废。

## Risks / Trade-offs

- **ask_user 挂起泄漏** → Future 双绑定（abort 信号 + 超时），注册表 finally 清理
- **EventSource 自动重连重放 query** → 前端断连时显式 close（不做自动重连）+ per-session 并发锁（D10）拒绝重放期重复请求；user 消息按 D11 到达即写，重复写入由锁与前端共同兜住
- **模型过度 ask_user / escalate** → `MAX_ASK_PER_TURN` + 去重 + escalate 单次上限 + system prompt 约束
- **should-call 错误（该检索不检索）** → tool 描述写明触发条件；后续可加"无引用且事实性问题→强制补检索"兜底（grader 闭环）
- **同 turn 继续与历史持久化语义分裂**（答案=ToolMessage vs 历史显示=user 消息）→ 按 D11 两轨并存：澄清答案在模型上下文是 ToolMessage、在历史是 user 消息（到达即写），不冲突
- **断连时异步持久化任务**（`api/chat.py:189` create_task）→ 按 D11：assistant 消息在完成/abort 时同步落库（部分答案 interrupted），不依赖流结束后 task

## Migration Plan

1. 阶段一核心：rag_tools.py（retrieve_kb + ask_user）→ state.py → nodes.py（make_agent_node）→ workflow.py（删 classify 澄清分支）→ agent_service.py（双路合并 + abort）
2. 交互基建：挂起注册表 + `POST /api/chat/clarify-answer`
3. 前端 composer 化（chat.html）
4. 反馈接口 `POST /api/feedback`
5. 阶段二：escalate_to_human + 工单落库
6. 与 `query-rewrite-and-graph-simplification` 协调：该 change 的 batch-clarification 任务标记为被本 change 取代（触发/提交语义变更）

## Open Questions

- 反馈落库表结构与 message_index 语义需与现有会话表对齐
- **话题漂移后的会话续接保真度（后续探索，不预先承诺方案）**：A→B→A 场景下，dsh 靠 session log（工具调用结果在模型上下文，续接保真度最高）而我们只有文本历史（续接=重新检索+历史上下文）。后续需探索两条路线并对比取舍：(a) **session log**（dsh 式 append-only 事件日志，token 级可重放、工具结果进上下文、resume/fork，但事件建模+存储+投影是完整子系统）；(b) **工具结果持久化**（把 agent 循环每轮检索证据存 Redis，回到原话题时注入上下文，轻一个量级但只覆盖保真度的一部分）。本 change 不实现，只记录为后续探索项。
