## Why

Phase A（已归档，`9c02a9e`）修好了上下文预算的**口径与度量**，但**只度量、不压缩**：超出预算的历史仍被**直接丢弃**。实测历史预算为 `HISTORY_TOKEN_BUDGET = 16384` token（经验值），长会话必然越界——一旦越界，早期的语义（用户的目标、已达成的决定、未解的约束）就**不再进入本轮上下文**，只剩「最近若干轮」。这是多轮对话「语义漂移」的直接成因。

调研（`docs/context-memory-research.md` §十 L2）与 ADR-0017 都指向同一结论：**让大预算成立的是摘要层，不是把历史填满**。

本仓的结构前提（已核实，决定了落点）：
- 跨轮历史只存 **user/assistant**（Redis `chat_history:{session_id}` 的 List，TTL 7 天）；内层 `create_agent` 循环的中间消息**从不落库**，跨轮不存在。
- **`_truncate_history` 只返回新列表、从不删 Redis**——被"裁掉"的原文**仍在 Redis**，这是本方案成立的关键前提（异步摘要可随时读全量）。
- seed 点在**本轮 assistant 写 Redis 之前**，且 regen 轮**不重进 seed**（走 `state.messages` 分支）⇒ **摘要生成不能放在 seed 点**。
- `session_id` 在 seed 点可得（`AgentState.session_id` 与 `RequestContext.session_id` 两处）。
- 唯一可能超长的工具结果**已在源头截断**（`WEB_BODY_LIMIT=2000`、`DELEGATE_RESULT_LIMIT=1000`），且当前轮检索证据受 Phase A 的 `[n]` 溯源不变量保护。

## What Changes

- 新增**跨轮历史摘要**，**两段式**：
  - **生成（回合正常完成后，异步不阻塞）**：仅在**回合正常完成**的收尾路径、且 assistant 已写入历史之后，用**与裁剪同一口径算出的「将被丢弃段」**（非全量历史）判断触阈 → **best-effort 抢带 TTL 的独立摘要锁** → `asyncio.create_task` 异步生成/更新（输入为**发起时冻结的快照**）→ 写回会话级存储。取消/异常回合不触发；失败只记降级信号，不重试（下轮触阈自然重试，因边界不变而幂等）。
  - **注入（seed 点，只读）**：seed 点读取由 `stream_chat` **预取并经 `AgentState` 传入**的摘要值，与保留的最近若干轮原文按 `[system 段, 摘要段, 保留尾部]` 组装；无摘要时行为与今天一致。
- 新增**摘要持久化**：Redis 键 `chat_summary:{session_id}`（TTL 与既有历史一致、写入续期），承载**摘要正文**与**覆盖边界（按消息条数）**；`ChatManager` 需**同时补内存降级分支**（否则无 Redis 环境下摘要静默失效）。
- 新增**摘要锁**：独立键 `chat_summary_lock:{session_id}`，**带 TTL** 并**纳入启动期残留锁清理**；**不复用** `chat_lock`；摘要调用套超时保险丝。
- **摘要段计入预算**：「摘要段 + 保留尾部」的聚合 token 量 SHALL ≤ `HISTORY_TOKEN_BUDGET`，超限先缩摘要、再裁尾部（同步给 `context-budget` 主规格带 MODIFIED delta）。
- 新增 `SUMMARY_MODEL` 配置项与 `get_summary_llm()`：**关闭思考**（实测深思考让输出 token 涨 2.7 倍）+ **显式输出上限**（否则"拒绝截断"不变量无从判定）+ `temperature=0`。
- 四条硬不变量：① 摘要 **小于**被丢弃段；② 就地更新**严格不增长**；③ **拒绝截断摘要**（按模型返回的结束原因判定）；④ 标记识别上次摘要，**不做摘要之摘要**。
- **注入侧语义**：摘要段作为**独立历史消息**注入（不进 system 段），带可辨识标签 + 「仅供对话背景、事实性结论仍须以本轮检索为准；不确定时请用户复述」声明；编号**仅窄幅剥离**（形如 `[数字]`），**不因编号丢弃整篇摘要**。
- 新增阈值常量 `HISTORY_SUMMARY_TRIGGER_TOKENS = 12288`（**经验值**，作用于「将被丢弃段」）。

## Capabilities

### New Capabilities
- `history-summarization`: 跨轮历史摘要的**异步生成**（触发、best-effort 并发守卫、结构化产出与四条不变量、失败降级）与**只读注入**（持久化、读取、标签与让位声明、编号剥离）。

### Modified Capabilities
- `clarification-interaction`: 「历史注入窗口」由「最近 N 轮 + 绝对 token 预算」扩展为「最近 N 轮 + 绝对预算 + **更旧段摘要**」，并明确摘要段的注入位置与「摘要不可用时回退纯裁剪」。
- `context-budget`: 「历史裁剪结果 SHALL 落在预算内」扩展为「**注入总量（摘要段 + 保留尾部）** SHALL 落在预算内」，防止摘要段因不在裁剪路径上而绕过预算口径。

## Impact

- **代码**：`src/services/agent_service.py`（**预取摘要并经 `launch_context` 传值**）、`src/agents/graph/state.py`（新增摘要值字段）、`src/agents/graph/agent_node.py`（seed 点**只读注入**：构造独立消息、复用既有插入位置）、`src/services/turn_runner.py`（**回合正常完成分支**挂异步生成）、`src/chat/manager.py`（摘要 + 摘要锁的读写，**含内存降级分支**）、`src/services/chat_lock.py` 或新模块（带 TTL 的摘要锁）、`src/main.py`（启动期残留锁清理纳入 `chat_summary_lock:*`）、新增摘要模块、`src/models.py`（`get_summary_llm()`）、`src/config/settings.py`（`SUMMARY_MODEL`）、`src/config/const.py`（触阈值）、`src/config/prompts/templates/`（`kind: task` 模板）。
- **依赖**：无新增第三方依赖。
- **行为**：跨轮历史的更旧段由「丢弃」变为「摘要保留」（**自触阈后的下一轮起生效**）；每会话每次触阈**多一次后台 LLM 调用**（关思考 + 有输出上限 + `temperature=0`）。属预期行为变化，非 API 破坏。
- **测试**：触阈/未触阈、异步生成写回、锁争用跳过、四条不变量各自失败即不采用、降级回退、内存降级路径、编号剥离、注入顺序与让位声明。
- **文档**：`docs/agents/glossary.md`（跨轮摘要 / 摘要覆盖边界）、`docs/agents/logging-rules.md`（两个 `[session]` 事件）、`docs/agents/code-map.md`（新模块落点）。
- **已知取舍（明写，非缺陷）**：**模型上下文与用户回放视图分叉**——摘要**不落 MySQL**，前端 `GET /sessions/messages` 回放的仍是**完整原文**（这反而是审计友好的：用户看到的是真实发生的对话）。

## 明确不做（本变更范围外）

- **L1 轮内消息级瘦身**：作用面几乎为零——跨轮历史**不含工具消息**；当前轮检索证据受 Phase A 的 `[n]` 溯源不变量保护；唯一可能超长的工具结果已在源头截断（`WEB_BODY_LIMIT=2000` / `DELEGATE_RESULT_LIMIT=1000`）。
- **摘要落 MySQL / 前端可见**（对齐既有 `SKILL_INJECTION_PREFIX` 的"落库+过滤"约定）→ 见「已知取舍」；前端任何改动须走 `docs/agents/ui-design-flow.md`。
- **短期记忆真源迁到 LangGraph checkpointer**（含 `langgraph-checkpoint-postgres`）→ 另开 B2。
- **长期记忆 Store**（限 episodic + user-semantic）→ Phase C。
- **工具结果外置/可回读**→ Phase D。
- **阈值标定**：按用户裁定**沿用经验值**（本变更不设标定任务）。
- **`middleware.py` 拆分**（已 393/400 行）：按用户裁定**后置**；本变更的设计 SHALL 避免向其新增代码。
