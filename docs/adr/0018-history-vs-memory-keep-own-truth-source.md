# ADR-0018：对话历史与 Agent 记忆分轴；短期记忆真源不迁 LangGraph checkpointer

- **Status**：Accepted
- **Date**：2026-10-08
- **Deciders**：用户（决策：以参考项目实证为准，不做 checkpointer 迁移）；Claude（勘察与综合）
- **Supersedes**：ADR-0017 的「决策 3 记忆底座」（局部）
- **关系**：沿用 ADR-0017 的四层阶梯、per-model 窗口口径与「决策 4 记忆边界」（长期记忆限 episodic + user-semantic，事实仍走 RAG）；记忆底座沿用 ADR-0004 的单一 PostgreSQL（`pgvector`）；L2 摘要的已落地实现见 change `cross-turn-history-summary`（归档于 `docs/openspec/changes/archive/2026-10-08-cross-turn-history-summary/`）。

## 背景与问题

ADR-0017 决策 3 定的是：「短期 = LangGraph `checkpointer`（thread）；长期 = `PostgresStore` + `pgvector`」，并据此**确立依赖 `langgraph-checkpoint-postgres`**；Phase B 原计划在此落地。

2026-10-08 在 B2 开工前做了一次**接入调研**（参考项目源码实读 + 本地 LangGraph 源码实读 + 本仓现状核实），得到三类反证。

**① 参考项目普及度极低（20 个本地参考项目，实读源码）**

- 真正把 checkpointer 用于**生产会话持久化**的只有 **1 个**（`fastapi-langgraph-agent-production-ready-template`）。
- `financial_rag-main` 名义上装了 PostgresSaver，但自建的 saver 缺原生接口（无 `aget_tuple/aput`），被 `isinstance(BaseCheckpointSaver)` 校验挡下、**运行期降级为 `MemorySaver`**；它另写的 `RedisCheckpointer` 也**未被 `compile` 使用**（其 README 自陈），两套都是死代码。
- 其余 15 个（LangBot / dify / ragflow / Qwen-Agent / deepseek-harness / codex / claude-code / WeKnora / CowAgent …）**依赖清单里根本没有 langgraph**。
- **没有一个项目做过「Redis 存短期记忆 → 换成 checkpointer」的迁移**，因此没有可抄的先例。

**② 当初的两条理由，在 B2 开工前已经失效**

- 理由之一是「消除多套窗口口径打架」。现状核实：`MEMORY_WINDOW`(=6) 在 `src/` 内**零消费者**（死配置，仅 `tests/chat/test_chat_manager.py:7` 一句过时 docstring 仍声称"配置生效"）；`MODEL_CONTEXT_WINDOW_TOKENS`(=32768) 仅被 `src/rag/prompt.py:185,189` 的**段占比告警**使用，不参与任何截断。**真正在跑的窗口只有一个**（`HISTORY_MAX_TURNS=10` + `HISTORY_TOKEN_BUDGET=16384`）——该病已由 Phase A 与 B1 治好。
- 理由之二是「白拿 `resume` / `interrupt` / `time-travel`」。**这三个能力目前没有任何产品需求背书**；四家被细看的项目里，人机介入分别用**内存 waiter + Redis pub/sub**（WeKnora，明确不跨重启）与**一张 DB 表的 `pending` 状态**（LangBot）解决，**都不是靠框架状态机**。

**③ 接入本身有三个结构性冲突（本地 LangGraph 源码实读）**

- **内外双图**：`create_agent` 的子图 checkpointer 继承依赖"作为 subgraph 节点注册"（`langgraph/pregel/_algo.py:737-740`），而我们把它的产物**当普通函数** `inner.ainvoke(...)` 调用（`src/agents/graph/agent_node.py:293`），**不走继承路径**；两边都挂则同一 `thread_id` 存在两份 checkpoint，真源归属需另行裁决。
- **消息累积 vs 每轮重建**：挂 checkpointer 后 `messages` 跨 invoke 累积（`add_messages` 语义），而我们从 Redis 每轮重建历史且无 message id ⇒ 会被补 UUID 导致**重复累积**。
- **无 TTL、`prune` 未实现**：Postgres checkpointer 没有 TTL/GC，`prune` / `copy_thread` / `delete_for_runs` 在 `PostgresSaver` 未覆盖；删除会话必须自己接 `delete_thread`。

**④ 另有一处概念混淆被同时发现**

「用户历史对话」与「Agent 记忆」被当作同一条轴（`docs/agents/requirements_pool.md` 的 A-07 甚至以"当前记忆提取…是同步 inline 调 LLM"描述现状）。两者是**两条不同的轴**，失败代价不同，合并会同时污染溯源与审计。

## 候选方案

| 方案 | 做法 | 代价 | 收益 |
|---|---|---|---|
| A：按原计划迁 checkpointer | 加 `langgraph-checkpoint-postgres`，外层图 + 内层 agent 都挂 | 新依赖一套（含 `psycopg`/`psycopg-pool`/`orjson`，与现有 asyncpg 并存）+ 4 张表 + 运维前置（`setup()` 建表、`CREATE INDEX CONCURRENTLY` 需 autocommit）+ 需先裁掉三个结构性冲突 + 删除会话接线 | 拿到框架原生 `resume`/`interrupt`/`time-travel`；真源与图状态合一 |
| B：保留应用自有真源，按需自建能力（**选定**） | 短期真源留在应用自有存储；长期记忆自建；`resume`/HITL 需要时用"普通表 + 游标 / 交互请求表"实现 | 放弃框架原生的图状态机恢复；将来若真需要，要自己写表与游标 | 不新增依赖与运维面；真源可读可审计；历史与记忆职责清晰；与四家参考上生产的选择一致 |
| C：只给外层图挂 checkpointer（中间态） | 仅 `builder.compile(checkpointer=...)`，`messages` 每轮仍重建并先清 | 同时存在两处状态（正是 0017 想消除的）；拿不到细粒度 `resume` | 破坏面小、可回滚 |

## 决策

**选 B。** 具体五条：

1. **分轴治理**：**对话历史**（transcript，本次会话发生过什么，不可丢，供上下文组装与回放审计）与 **Agent 记忆**（memory，跨会话习得的知识，可错可遗忘可重建）是**两条独立的轴**，分开存储、分开治理，**不得合并为同一层**。
2. **短期记忆真源留在应用自有存储**（当前 Redis `chat_history:{session_id}` + PostgreSQL `conversation_history` 审计/回放），**不迁** LangGraph checkpointer。若将来确需"跨重启续会话"，走 **普通关系表 + 游标（`seq`）+ 每轮重建**的路子，不引入框架状态机。
3. **长期记忆底座自建**，**不用** LangGraph `PostgresStore`。形态参考 WeKnora（PostgreSQL 多表 + 类型化 + 双时态 supersede + 容量归档）或 CowAgent（可读文件为真源 + 派生索引），二者都以"**真源可读、可审计**"为前提。
4. **记忆边界不变**：沿用 ADR-0017 决策 4 —— 长期记忆**只承载 episodic + user-semantic**（用户是谁 / 偏好 / 规则 / 经历）；**事实与知识类问题仍强制走 RAG 取证**。
5. **`resume` / 人机介入按需自建**：人机介入用**一张交互请求表 + `pending` 状态**（LangBot 形态），不做图状态机级恢复；在出现明确产品需求与容量证据之前**不实现**。

**依赖口径**：`langgraph-checkpoint-postgres`、`psycopg`、`psycopg-pool`、`orjson` **均不加入依赖**（当前宿主与容器三者皆无，且项目 DB 走 asyncpg）。

## 理由

一句话：**ADR-0017 决策 3 要解决的那个问题（窗口口径打架）在 B2 开工前已经被 A 与 B1 解决了，而它换来的能力没有任何需求背书；同时它要引入的三样东西（新依赖、4 张表、以及必须先裁掉的三个结构性冲突）在参考项目里没有任何可抄的先例。**

支撑它的最关键两条证据：

- **四家被细看项目的真源都不是框架快照**：WeKnora = PG 普通表（源码注释原话 `DB is treated as the single source of truth — there is no Redis/in-memory cache layer above this function.`）；deepseek-harness = 内存事件日志 + 每会话 JSONL（并**主动删掉**已实现的 SQLite provider，避免双格式测试矩阵，同时白纸黑字记下"失去 DB/WAL"这一代价）；CowAgent = SQLite `messages` 表 + Markdown 文件；LangBot = DB 表 `transcript`，且把"**没有明确容量证据前，不新增额外数据库组件**"写成了架构决策。
- **要拿到"续会话"并不需要 checkpointer**：四家全部以"从真源重建"实现重启可续；需要跨重启存活的只有**人机交互请求**，LangBot 用一张表的 `pending` 状态就解决了。

## 后果

**正面**：

- 不新增依赖与运维面（无 `psycopg` 系、无 checkpointer 建表、无 `setup()` 前置）。
- 真源保持**可读、可审计、可离线检查**（Redis 键 + PostgreSQL 行），与 ADR-0004 的单 PostgreSQL 收敛方向一致。
- 绕开三个结构性冲突（内外双图 / 消息累积 / 无 TTL）——它们不是实现细节，而是必须先在架构上裁决的问题。
- 历史与记忆的职责被显式分开，避免"记忆污染溯源"与"把压缩产物升格成真源"两类隐性事故。

**负面 / 接受的代价**：

- **放弃 LangGraph 原生的 `resume` / `interrupt` / `time-travel`**；将来若确需，代价是自己实现"表 + 游标"并自证正确性（届时按复查触发条件重开）。
- **继承 ADR-0017 已声明的代价**（不再新引入）：L2 摘要额外 LLM 调用的成本与延迟、摘要的有损性。
- 本 ADR **接手** ADR-0017「后果」中「引入新依赖 `langgraph-checkpoint-postgres`」与「复查触发条件」中「checkpointer / Store API 语义变化（含该包废弃或破坏性升级）」两条 —— **随本决策一并失效**（依赖不引入，故无该风险面）。

**不解决的问题**（明确列出，避免后人误以为这条 ADR 管了它）：

- **不实现**任何记忆能力：本 ADR 只定"底座不用什么、真源放哪"，Phase C 的形态、提取、检索、遗忘参数均另议。
- **不解决** `chat_history:{session_id}` **无裁剪**（当前只受 TTL 约束、实测单会话已 60 条）——这是一个独立的小缺口，另开小变更处理。
- **不修正** `requirements_pool` 中对记忆系统现状的错误描述（A-07 假设已有记忆层）——属该文档自身维护。
- **不解决**「触顶 → 空回答」缺陷（归 `agent-round-budget`，需求池 F-37）。
- **不实现** per-model 窗口注册表与离线探针（ADR-0017 从 Phase A 移入 B 的那项）——B1 的阈值是绝对量、未依赖窗口数值，故其必要性同样待需求驱动。

## 复查触发条件

- 出现**明确的产品需求**：跨重启续会话、细粒度人机介入（如在工具调用中途暂停等待人工确认）、或回放重跑（time-travel）。
- 出现**容量证据**表明"每轮从真源重建"成为瓶颈（如历史条数/会话规模达到需要更强持久化的量级）。
- 需要跨进程/多副本承载**同一会话**（当前部署形态是单 worker、进程内状态；该前提变化会同时影响本决策与 ADR-0017 的 L1/L2 设计）。
- 出现"记忆被用来回答事实"导致**溯源失效**的实例（记忆边界失守）。
- LangGraph 若提供**应用自有真源与图状态共存**的一等支持（例如无需双写即可让 graph state 以外部存储为真源），可重开权衡。
