## Context

Phase A（归档于 `9c02a9e`）已把上下文预算收口为「集中绝对预算 + 统一分词器计数 + 轮内度量」。本 change 落地其 **L2 摘要层**（ADR-0017 决策 1），使更旧的历史由「丢弃」变为「摘要保留」。

现状（已核实，含 10 项定点核查 + 一轮独立架构复审）：

- 跨轮历史：Redis `chat_history:{session_id}`（List，TTL `REDIS_TTL`），**只含 user/assistant**；MySQL `conversation_history` 供回放。
- **`_truncate_history` 只返回新列表、从不删 Redis**（`agent_node.py:48-57`）⇒ 被裁的原文仍在 Redis。这是本方案成立的前提。
- 内层 `create_agent` 循环（≤ `MAX_AGENT_ITERATIONS`）的中间消息**从不落库**。
- `_truncate_history` 的**切分口径**：先按 `HISTORY_MAX_TURNS`(=10) 轮取近段，再按 `HISTORY_TOKEN_BUDGET` 从最旧逐条弹出；**当 `len(history) <= max_turns*2` 时近段 = 全量**（`agent_node.py:46-49`）。
- seed 点：`agent_node._split_initial_messages`；**在本轮 assistant 写 Redis 之前**，且 **regen 轮不重进 seed**（`:197-203` 走 `state.messages` 分支 ⇒ regen 复用首轮已注入的消息）。
- `agent_node` **是图内节点**：无 `ChatManager`、无 Redis 句柄；`build_graph` 签名不含 `chat_manager`（`workflow.py:46-55`），`AgentLoopBundle` 也不带（`:114-118`）。
- 既有插入位置逻辑：`agent_node.py:148-153`；但技能注入的**前缀扫描只遍历 `state._history`**（`:84`），摘要若存独立键则扫不到。
- 会话锁：`chat_lock:{sid}`（`chat_lock.py`，TTL `SESSION_LOCK_TTL=180`）；**`release_lock` 在 `finally`**（`turn_runner.py:168`）。启动期只清 `chat_lock:*`（`main.py:83`）。
- 回合收尾三分支（`turn_runner.py`）：**else（正常，`:146-163`）写 Redis assistant**；cancel（`:110-128`）与 exception（`:129-145`）**只写 MySQL、不写 Redis**。
- 后台姿势先例：`asyncio.create_task`（`turn_runner.py:281/344`）；`task_registry` 是看板、非执行器。
- prompt 闸门：`kind: task` 只需 `id`/`kind`/`content` 且 id 全局唯一；违反会让 `main.py` 的 `validate_all()` 抛错 ⇒ **应用启动失败**。
- `get_classify_llm()` 默认 `enable_thinking: False`；`parse_temporal` 是「异步 + `temperature=0` + except 降级」的现成范式。
- 实测：深思考让输出 token 涨 **2.7 倍**。
- `format_node` 只从**答案文本**抽 `[n]`（`nodes.py:77-104`）。
- `ChatManager` 双路径：每个方法先 `_ensure_redis_async()` 再分内存/Redis 分支。

## Goals / Non-Goals

**Goals:**

- 更旧的历史越界后不再不可见：以**结构化摘要**跨轮复用。
- 生成**不增加用户可见延迟**；失败可降级、可观测、**可自愈**（不产生永久禁用该会话的状态）。
- 摘要有**可断言的四条不变量**，且**摘要段自身受预算约束**（不破坏 Phase A 主规格）。
- 不引入 DB 迁移；不动 `middleware.py`；不破坏 Phase A 的两条不变量与 `[n]` 溯源。

**Non-Goals:** L1 轮内瘦身、checkpointer 统一（B2）、长期记忆（C）、工具结果外置（D）、摘要落 MySQL/前端可见、阈值标定、`middleware.py` 拆分。

## Decisions

### D1 两段式落点：**回合正常完成后异步生成** + **seed 点只读注入**

- **生成**：仅在 `turn_runner` 收尾的 **else（正常完成）分支**、且 `add_message_async(assistant)` **成功之后**触发——此刻本轮 assistant 已落 Redis、历史完整。cancel / exception 分支 **SHALL NOT** 触发（那两条路径不写 Redis assistant，历史末尾会是"未作答的 user 问"，摘要会把悬空问题当史实）。
- **注入**：seed 点**只读**（见 D7 的值通道）。
- **评估过并否决的更简方案**：把生成触发也放到 seed 点（异步）——它能删掉收尾挂点及其分支判定，但**代价是把存储依赖带进图**：seed 生成需要一个能写 Redis 的句柄，于是必须把 `ChatManager`（或写句柄）注入图节点/Bundle，使图节点依赖存储层并加重 `agent_node` 单测负担。而本方案的 seed 只需**一个字符串值**（D7）。故维持两段式。
- **接受的代价**：**触阈那一轮**更旧段仍被裁掉、当轮不可见；原文仍在 Redis，摘要自**下一轮**起补偿。

### D2 摘要持久化用 Redis 新键 + 内存降级分支；不引入 DB 迁移、不落 MySQL

- Redis 键 `chat_summary:{session_id}`，TTL 与 `REDIS_TTL` 一致；承载**摘要正文** + **覆盖边界（消息条数）**。写入路径 SHALL 续期 TTL（与 `chat_history` 一致）。
- `ChatManager` 新增 get/set/clear 三个 async 方法，**各自都要写内存分支与 Redis 分支**（新增 `_memory_summaries`），否则无 Redis 环境摘要静默失效。
- `clear_history_async` **SHALL** 一并清除摘要与摘要锁。
- **不落 MySQL**：`GET /sessions/messages` 回放仍是**完整原文**。**已知取舍**：模型上下文与用户回放视图分叉——回放保持全文**更利于审计**。兜底：让位声明要求模型在不确定时请用户复述（D7），按需回读原文属 Phase D。

### D3 触发判据落在**将被丢弃的那一段**，而不是全量历史

- 判据：用**与 `_truncate_history` 相同的切分口径**算出「保留尾部之外、将被丢弃的那一段」，对该段求 token；**该段为空或未超 `HISTORY_SUMMARY_TRIGGER_TOKENS`(=12288) 则不生成**。
- **为什么不能对全量历史判据**：当 `len(history) <= HISTORY_MAX_TURNS*2`(=20 条) 时近段 = 全量 ⇒ 被替换区间为空 ⇒ 不变量①「摘要 < 被替换区间」**永不成立** ⇒ 每轮白调一次 LLM 且摘要永不落地。中文长答案下 20 条超 12288 token 属常态。
- 覆盖边界按**消息条数**（与 Redis List 索引对应、切点精确可复现）。
- 保留尾部沿用 `HISTORY_MAX_TURNS`(=10) 轮原文（与 Phase A 轮数粗筛同口径）。

### D4 后台执行：独立锁（带 TTL + 启动清理）+ best-effort + 冻结快照 + 强引用

- **锁**：独立键 `chat_summary_lock:{session_id}`，`SETNX` **必须带 TTL**（=`摘要调用超时 + 余量`）；并把 `chat_summary_lock:*` **纳入启动期残留清理**（与 `chat_lock:*` 同处）。**不这样做**：进程被杀会留下无 TTL 的锁 ⇒ 该会话 best-effort 永远拿不到锁 ⇒ **永久不再生成摘要**，与"下轮自然重试"的目标直接矛盾。
- **超时**：摘要 LLM 调用 SHALL 套 `asyncio.wait_for`，超时即按失败降级（防长尾占用）。
- **best-effort**：拿不到锁直接跳过，不等待、不排队。
- **SHALL NOT 复用 `chat_lock`**：它承载"轮次互斥"语义，且即使在某些时点仍被本任务持有，复用会让"摘要"与"用户轮次"互相阻塞；语义与生命周期都不同。
- **冻结快照**：spawn 时**读取并冻结**消息列表，作为参数传给后台任务；任务内**不再读 Redis**（只写结果）。否则 fire-and-forget 期间下一轮 user 消息已写入，边界会吞入"未作答的 user 问"。
- **强引用**：后台任务须被模块级容器持有至完成（防长 LLM 调用期间被 GC）。
- **失败不重试**：只记降级信号；边界不变 ⇒ 后续触阈重算为幂等。

### D5 摘要产出：关思考 + 输出上限 + `kind: task` 模板

- 新增 `SUMMARY_MODEL`（`settings.py`，默认沿用 `LLM_MODEL`）与 `get_summary_llm()`（`models.py`）：**`enable_thinking: False`**（依据实测 2.7 倍输出 token）+ `temperature=0` + **显式 `max_tokens`**（不设则"拒绝截断"不变量无从判定）。
- 调用姿势照 `parse_temporal`：`await llm.ainvoke(...)` + `try/except` 降级。
- 模板放 `src/config/prompts/templates/`，**`kind: task`**（勿用 `kind: section`——非法 section 值会让应用启动失败）；结构化段 `用户目标 / 已达成的决定 / 未解约束 / 关键事实与数字 / 下一步`；正文要求"不得输出方括号编号"与"在旧摘要基础上增量更新、不得比上一版更长"。
- **不做摘要之摘要**：输入含既有摘要时以标记识别，要求增量更新。

### D6 四条不变量 + **摘要段的预算归属**

1. **更小**：`count_tokens(新摘要) < count_tokens(被替换段)`。
2. **严格不增长**：`count_tokens(新) <= count_tokens(旧)`——防"摘要逐轮膨胀吃回预算"；代价是可能过早折叠仍相关内容，但原文仍在 Redis，非不可逆。
3. **拒绝截断摘要**：判据 = 摘要模型返回的 `finish_reason`（`length` 即视为失败）；前提是 D5 已设 `max_tokens`。
4. **不做摘要之摘要**：见 D5。

**预算归属（Phase A 主规格的兼容）**：`_truncate_history` 只对尾部计数，而摘要段是裁剪**之后**追加的 ⇒ 若不加约束，"历史 ≤ 预算"会被静默破坏。故 SHALL 对**「摘要段 + 尾部」的聚合**设上限 `HISTORY_TOKEN_BUDGET`；超限时**先缩摘要、仍超再裁尾部**。该约束 SHALL 同步进 `context-budget` 主规格（本 change 带 MODIFIED delta）。

token 量一律经统一计数入口 `src/infra/llm/token_count.py`。

### D7 注入：**值通道**（`stream_chat` 预取 → `AgentState` 字段）+ 标签 + 让位声明 + 窄正则剥离

- **读取通道（本 change 必须补的接口）**：`agent_node` 是图内节点、无存储句柄，故摘要 SHALL 由 `stream_chat`（已 async、已持 `chat_manager`）**预取**，经 `launch_context` → `AgentState` 新增字段（如 `_summary`）传入；seed 点**只读该字段**构造独立 `HumanMessage`，并复用 `agent_node.py:148-153` 的插入位置。
- **不复用**技能注入的扫描机制（它只遍历 `state._history`）。
- 摘要段带可辨识标签 + 「仅供对话背景；**事实性结论仍须以本轮检索结果为准**」（依据 `retrieval-judgment` 主规格禁止凭记忆作答），并**要求模型在不确定时请用户复述**；**不并入 system 段**。
- **编号处理**：只剥离/拦截形如 `\[\d{1,2}\]` 的编号，**SHALL NOT 因含编号而丢弃整篇摘要**（"阻断"会与触阈重生成形成反复空转）；主防线是模板侧禁止 + 让位声明。依据：`format_node` 只从**答案**抽 `[n]`，摘要编号风险低于初版描述。

### D8 失败降级：全链路 try/except + `degraded` 事件 + 保留既有行为

- 读摘要 / 生成 / 校验 / 写回 任一失败：精确捕获 + 记降级事件（含原因）+ **保留既有纯裁剪行为**；**绝不外抛**、绝不中断对话、绝不静默产出损坏历史；写回失败也 **SHALL NOT 遗漏释放摘要锁**。

## Risks / Trade-offs

- **[触阈当轮老段不可见]** → 原文仍在 Redis，摘要下轮补偿；触阈是渐进水位。
- **[锁泄漏导致永久跳过]** → D4：TTL + 启动清理（这是"可自愈"的硬要求，不是优化）。
- **[边界吞入未作答的下一轮 user 问]** → D4 冻结快照。
- **[触发空转（进而不落地）]** → D3 判据落在"将被丢弃段"。
- **[摘要段撑破预算]** → D6 聚合上限 + `context-budget` delta。
- **[取消/异常轮产生错误摘要]** → D1 只在正常完成分支触发。
- **[摘要丢信息 / 用户引用被摘要掉的内容]** → 结构化模板 + 让位声明要求模型请用户复述。
- **[摘要段被当指令 / prompt 注入面]** → D7 标签 + 让位声明 + 不并入 system 段。
- **[进程被杀丢任务]** → 幂等，下轮重算（单 worker 前提）。
- **[视图分叉]** → D2 已知取舍。

## Migration Plan

纯配置 + 代码，**无 DB 迁移**。三步，每步独立提交 + 全量回归：

1. **摘要模块 + 持久化 + 锁 + 单测**（不接入链路）：触发判据（将被丢弃段）、结构化产出与四条不变量、聚合预算上限、读写往返（含内存降级）、锁 TTL 与启动清理、超时、快照冻结、编号窄剥离、失败不重试。
2. **接入两段式**：`stream_chat` 预取摘要 → `AgentState` 新字段；seed 点只读注入 + 重建顺序；`turn_runner` else 分支挂异步生成；`clear_history_async` 一并清摘要与锁。
3. **文档与回归**：glossary / logging-rules / code-map + `context-budget` delta 的同步；中文多轮回归（对比"丢弃"与"摘要保留"）。

**回滚**：回退提交（无迁移；Redis 摘要键与锁键为新增键，残留无害）。

## 已闭问题（评审后定论）

1. **`get_summary_llm()` 的输出上限** → **`SUMMARY_MAX_TOKENS = 2048`**，模板目标「**不超过 600 字**」。
   依据（**本地参考项目实读**）：`deepseek-harness` 摘要输出上限 `maxTokens: 8192`（`packages/compaction/compaction-basic/src/config.ts:91`）；`WeKnora` 摘要提示要求 "aim under 500 words"（`internal/agent/compaction/prompts.go:40`）且把 `finish_reason=length` 视为失败；`ragflow` 摘要预算 `MaxChars:1200 / MaxLines:24`（`internal/harness/core/middlewares/summarization/compactor.go:131-137`）。三者落在 1200 字符 ~ 500 词 ~ 8192 token 区间。
   本项目不能照搬 8192：**「摘要段 + 尾部」聚合 ≤ `HISTORY_TOKEN_BUDGET`=16384**，尾部要占大头。取 2048 —— 远小于触阈段（>12288，满足「更小」）、给尾部留余量、且使「拒绝截断」有可判定的判据（超过 2048 即为模型跑飞）。
2. **摘要覆盖边界与「保留尾部起点」重叠** → 边界由**保留尾部起点唯一确定**，无需 `max(...)` 取岔；"将被丢弃段" = 保留尾部起点之前的全部消息。
3. **让位声明是否足够兜住"用户引用了已被摘要掉的内容"** → 采用「让位声明 + **不确定时请用户复述**」（已写入 `history-summarization` 规格）；「按需回读原文的工具」列入 Phase D 候选，登记 `docs/agents/requirements_pool.md`。

## Open Questions

1. `SUMMARY_MAX_TOKENS = 2048` 与「≤ 600 字」目标在真实中文会话下的**实际产出分布**需上线后按观测校准（**经验值，不阻塞实施**）。
