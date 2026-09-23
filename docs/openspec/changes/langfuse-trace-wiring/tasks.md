# langfuse-trace-wiring —— 执行档索引

> **本文件不承载任务清单。** 按 `docs/agents/dev-flow.md` 的 ⑤ 环节规定（`writing-plans` 与 openspec tasks **二选一，不可都写**），本 change 的执行档由**一份实施计划**承载，另有一个**计划外的部署动作**。

| 执行档 | 载体 | 范围 | 完成标准（DoD） |
|---|---|---|---|
| **实施计划（唯一一份）** | `docs/superpowers/plans/2026-09-22-langfuse-trace-wiring.md`（⑤ 环节产出） | 测试重定（D14）→ 接线与开关（**服务侧 + CLI 侧两处**，D5；CLI 另需 flush，D6）→ **入站 trace id 校验**（D3）→ generation 字段回填（**`capture_input=False`**，D8）→ 清理 CLI 的**代码**（含运行期护栏，D7）→ 死代码清理 + **`token-usage-model` delta**（D9）→ **`settings.py` 内置默认值清理**（D19）→ 文档同步（含 `glossary.md` 术语统一 / `code-map.md` / `cookbook.md:315`）→ **新 ADR（D16）+ ADR-0011 复评记录（D10）**。全部是普通代码任务 | ① 测试重定先完成，**"pytest 全绿"才可被当作证据**（D14，须在全局关停 tracing 的前提下取得）；② **关闭态**：一轮对话行为与接线前逐项一致（SSE 事件序列、落库、引用）；③ **开启态**：dev 真实一轮对话的**四方 id 对齐**（响应头 / 日志行 / SSE done / Langfuse UI），主 agent generation 带 model / input / output / usage / 首 token 时间；④ **四条未验证断言已逐条验**：取消后 trace 仍在 / id 字符集被服务端接受 / 辅助 LLM 与 fork 自动嵌套 / 级联与 UI；⑤ **非法 `X-Trace-ID` 被拒且四方仍对齐**（D3）；⑥ **trace 输入里不含内部运行时对象**（`ctx` / `manager` / `graph` / 事件队列，D8）；⑦ 清理 CLI 的 `--dry-run`、保留期**下界拒绝**、**未确认不删**、**超上限中止**、**审计输出**、保留期内零改动、重复执行安全（D7）；⑧ CLI 的 trace 在 `--gate` / 异常退出路径下**也能落库**（D6 的 flush）；⑨ 已删符号全仓零引用（含 tests），**`token-usage-model` 主规格已由 delta 修正**，质量门禁全绿（`pytest` / `ruff` / `pyright` / `check_docs` / `check_adr`）；⑩ 文档不再出现"UI 里看不到数据是正常的"这类叙述；**`glossary.md` 术语统一为「trace 保留期」并改条目名、`trace_id` 条目补写"同时是 Langfuse trace id + 不可信入站输入"**；D16 新 ADR 与 D10 复评记录可被独立读到；⑪ **配置四面一致**（`settings.py` 内置默认 / `.env` / `.env.template` / `.env.example`），内置 key 类默认为空串、HOST 不再指向不存在的服务名（D19） |
| **计划外的部署动作** | 不落计划文件（部署与数据操作，按 ⑥「不适合走 SDD 的情形」执行） | ① **级联删除小规模验证**（不通过则不许接定时任务，D15）；② 清理任务**定时接入**（宿主 cron / systemd timer / compose 定时服务，开工前定）；③ **prod 侧落地**：同步 prod 机上的 `.env`（改模板 ≠ 改到机器）+ 确认 `langfuse-web` 在跑（D17）；④ **dev 侧开关的启用与回滚**是手工动作且 `.env` 跨工作区共享（D18）—— 先记录原值再改 | ① 删少量超期 trace 后 `observations` / `scores` / `dataset_run_items` **无孤儿**，UI 正常（D15）；若 API 不级联，清理 CLI 已按 D15 的约定显式删附属记录；② 定时任务在 dev 与 prod 各自可运行；③ 真删只影响早于保留期的 trace，保留期内零改动；④ prod 侧"后端不在跑"的情形已按故障隔离验收过；⑤ `.env` 原值已记录、改动可精确回退。**注意**：这五条仍是本 change 的 DoD（ADR-0011 复查条件③要求清理与接线一并落地），不可挪到 change 之外 |

## §1 为什么是「一份计划 + 一个计划外动作」

判据取自 `dev-flow.md`「判据二」的**收紧版** —— 只在「存在可独立结束的边界」时拆，满足任一：**A 执行器不同 / B 可独立交付 / C 体量或接手**。

- **接线 / 开关 / 清理 CLI 代码 / 死代码 / 文档** 五段全是普通代码任务，**执行器相同**（走 SDD），彼此的先后在单份计划内用前置行即可表达 → 命中「**不构成拆分理由**」里的**硬顺序依赖**与**任务证据形式不同**两条 → **不拆**。
- **只有「定时接入 + 真删验证」命中判据 A**：它是纯部署/配置 + 含破坏性删数据，落在 ⑥「不适合走 SDD 的情形」第 1、2 条 —— worktree 隔离与结尾 merge 对「改宿主 cron + 真删 langfuse 库数据」没有意义，且破坏性操作是 SDD 的停止条件 → **必须换执行器，故单独成档**（此处即"计划外动作"）。
- **反例对照**：`langfuse-v2-downgrade`（60 任务不拆）是纯部署/配置，按 ⑥ 本就不该走 SDD；`postgres-storage-consolidation`（拆 P1–P4）含数据搬迁、存量全量重写与**删卷**，四段里三段命中 A → 拆得对。

## §2 前置事实（本变更定稿时的实测结论）

均为本次在 `corporate-rag-app` 容器内、打真实 v2 服务端所得，探针数据已按 id 精确清理（库回到 `traces=1 / observations=0`）。

| 项 | 结论 | 影响的设计决策 |
|---|---|---|
| `langfuse.callback.CallbackHandler` | **import 即失败**：SDK 2.60.10 依赖 `langchain.schema.agent` / `langchain.callbacks.base`，langchain 1.x 已删除这些路径 | D1 路线选型（CallbackHandler 不可用） |
| `langfuse.decorators.observe` | 同步 / 异步 / **异步生成器** 三种形态均落库成功 | D1、D2 |
| trace id 自定义 | 根调用传 `langfuse_observation_id="trace_custom"` → trace id 即该值 | D3 |
| `configure(enabled=False)` | 50 次观察仅 1 行 SDK 警告；单次 `@observe` 约 **0.21 ms** | D5（不做条件装饰） |
| `update_current_observation` | 支持 model / input / output / usage / completion_start_time | D4 |
| `LANGFUSE_ENABLE` 现状 | `settings.py:307` 代码默认已是 `true`；现状 `false` 来自 `.env` / `.env.template` 覆盖。全仓仅 `prompt_manager` 读它，而 `PROMPT_NAMES = {}` → 不发网络请求；启动校验不碰 Langfuse | D5（翻开关是空操作、无启动期依赖） |
| `_run_generation` 调用点 | 全仓唯一（`api/chat.py:205` 的 `answer_builder` 闭包），且本身在后台 task 内 | D2（根落点） |
| `estimate_usage` 消费者 | 两个活消费者（`agent_node.py:215`、`fork_stream.py:327`），第三个在待删的 `stream_answer` 内 | D9（连带项） |
| Langfuse 库现状 | dev 库 11 MB、`traces=1`（降级验证时的 smoke）、`observations=0` | D7（保留期取值） |

## §3 实施期修正记录

执行阶段若发现与设计不符的事实，逐条登记于此（格式：日期 / 落点 / 事实 / 处置 / 是否回改 `design.md`）。**不要直接改写 `design.md` 的既有决策叙述** —— 决策的修订走正常流程并在此留索引。

| 日期 | 落点 | 事实 | 处置 | 回改 design |
|---|---|---|---|---|
| 2026-09-23 | `src/agents/graph/agent_node.py`（`@observe`） | design D8 写 `capture_output=True`；实现改为 **`False`** —— SDK 的 output 回落会在 `_extract_text` 返回空串时把**节点返回的 state dict** 序列化进 trace | 改为显式 `update_current_observation(output=...)` 写入原文（T5 fix round） | 否 |
| 2026-09-23 | `src/cli/purge_langfuse_traces.py`、`src/infra/llm/langfuse_purge.py` | `DELETE /api/public/traces[/{id}]` 在 v2.95.11 上一律 **405**（该能力 **v3 才有**）；v2 里真正 enterprise-gated 的是 Data Retention | 立 ADR-0013，把保留期清理的**删除后端改为直连 Langfuse PG 的 SQL** | 否（索引指向 `docs/adr/0013-trace-retention-purge-via-langfuse-sql.md`） |
| 2026-09-23 | `src/infra/llm/tracing.py`（`TRACE_ID_PATTERN`） | 入站 trace id 白名单的**最终字符集**（关闭 §4 第 7 条）：实测服务端实际接受范围**宽于**白名单（点号 / 冒号也落库、**恰好 120 字符**也落库） | 白名单维持 `^[A-Za-z0-9_-]{1,120}$` 不变 | 否 |
| 2026-09-23 | `src/infra/llm/tracing.py`（SDK 重试） | 后端不可达实测（停 `langfuse-web` 3 秒）：SSE 正常走完、0 error 事件、对话零受损；SDK 用 `backoff.expo` 重试（`logger=None`）**吸收**故障并在恢复后补投成功（trace 确实落库） | 无需改动 | 否 |

## §4 未决项（承接 `design.md` 的 Open Questions）

| # | 未决项 | 何时必须收敛 |
|---|---|---|
| 1 | 辅助 LLM（`rewrite_query` / `parse_temporal` / 分类）与 fork 子代理的 generation 是否纳入 | 实施计划首轮实跑后：若"缺这两块就看不明白一轮对话"则提前；否则维持 Non-Goal |
| 2 | 清理的定时落地形态（宿主 cron / systemd timer / compose 定时服务），dev 与 prod 是否同方案 | 计划外部署动作开工前 |
| 3 | ~~`estimate_usage` 的最终宿主~~ → **已定：并入 `src/infra/llm/token_usage.py`，删除 `rag/stream.py`**（D9） | 已收敛 |
| 4 | ADR-0011 复查条件②的复评记录形式（写在 change design 内 vs 追加新 ADR）—— 注意与 D16 的新 ADR **是两件事**，不要合并 | 实施计划收尾前 |
| 5 | `LANGFUSE_ENABLE` 是否拆成两个开关（prompt 远端 / trace 产出）—— 见 D13，当前不拆 | 日后把 prompt 名单加回 `PROMPT_NAMES` 时**必须**重评 |
| 6 | `turn-provenance-observability/tasks.md` 4.1 的行号 `:158-167` 是错的（应为 `:173-200`） | 该 change 重启时顺手改正（属对方工件，本变更不擅自改） |
| 7 | ~~入站 trace id 白名单的**最终字符集** —— 须与服务端实际接受的字符集对齐后再钉死（客户端层结论不足）~~ → **已定：白名单维持 `^[A-Za-z0-9_-]{1,120}$`**（实测服务端接受范围宽于此，点号/冒号与恰好 120 字符均落库；见 §3） | 已收敛 |
| 8 | 是否给 `src/cli/check_docs.py` 补 `docs/openspec/specs/` 的扫描范围 —— 主规格的失效引用目前**没有机械闸门** | **不在本变更内**，登记为遗留 |
| 9 | 是否给 `LANGFUSE_*` 加"缺配置即拒绝启动"的 fail-fast —— D19 只把默认值改空串，没加校验（加了会改启动失败语义，属部署面） | **不在本变更内**，登记为遗留 |

## §5 定稿期勘误（本轮审阅发现，已就地修正）

| 项 | 原写法 | 实际 | 处置 |
|---|---|---|---|
| `agent_node` 温度分档段行号 | `:158-167` | **`:173-200`**（158 是 `async def agent_model`，173 是 `turn_start`，184 起是 `if state.kb_id` 的两个分支） | proposal / design 已改；错源在 `turn-provenance-observability/tasks.md` 4.1，见 §4 第 6 条 |
| `tests/infra/llm/test_langfuse.py` 的 skip 条件 | 视为"条件需随开关语义重定" | **是必须修的雷**：代码默认 `true` + 无 `.env` → 不 skip → 真发网络 | 升格为 design D14 + DoD 第①条 |
| retention 删除 | 视为"能力已确认可用" | 只确认了**方法存在**，未验证级联与孤儿 | 升格为 design D15 + 计划外动作第①步（**前置**） |
| spec 的取消 scenario | 已写入但未实测 | **纸面断言** | 保留契约，列为首个验证项（Risks 首条 + DoD 第④条） |

## §6 独立架构评审的处置（2026-09-22，结论 `Request changes` → 已全部收进本 change）

由**未参与撰写本提案**的独立只读子代理完成（已核对：评审期间工作区零写入）。无 Blocker，7 条 Important，全部采纳。

| # | 发现 | 处置 |
|---|---|---|
| F1 | 不可信请求头直接成为 Langfuse trace 主键（`X-Trace-ID` 零校验；`client.trace(id=)` 是 upsert → 非法的静默丢失、任意的可注入、泄露的可被覆盖） | → **D3 补入站白名单**（不合法则服务端重生成，四方对齐不受影响）+ spec 新 scenario + 实现落 `src/middleware/trace_id.py` |
| F2 | 破坏性清理缺运行期护栏（保留期无下界、无确认、无上限、无审计、无环境约束） | → **D7 补五条护栏** + spec 四条新 scenario + DoD 第⑦条 |
| F3 | 测试默认会连真实后端，**范围远大于一个文件**（`settings.py` 内置真实 key 默认值 → 所有走 `_run_generation` / `agent_model` 的既有用例都发网络） | → **D14 扩为全局 autouse fixture**，"pytest 全绿"须在关停前提下取得；DoD 第①条 |
| F4 | 删 `stream_answer` 与主规格 `token-usage-model:15` 冲突，「Modified Capabilities: 无」不准确 | → proposal 登记 **Modified Capabilities** + 新增 delta `specs/token-usage-model/spec.md`；顺手修同文件已陈旧的 `generate_node`；D9 补同步说明 |
| F5 | `@observe` 默认 `capture_input=True` 会把 `ctx / manager / graph / 事件队列` 等内部对象与整段历史写进 trace，且 `update_current_observation(input=)` 只覆盖最终值、救不了 | → **D8 改写**（原"数据面无新增暴露"的论证被推翻）+ spec 新 requirement 措辞 + 新 scenario；DoD 第⑥条 |
| F6 | CLI 没有 flush 收口（`--gate` 提前 `sys.exit` / 异常退出 → trace 时有时无，DoD 项不可靠） | → **D6 扩到 CLI 侧**（含 `finally` / `atexit`）；DoD 第⑧条 |
| F7 | worktree 的 `.env` 是指向主工作区的软链 → 在该处"改 `.env`"会改到另一个会话正在用的配置；且 `.env` 不受版本控制，启用不可复现、回滚不完整 | → **新增 D18** + 计划外动作第④条（先记录原值再改）+ Migration 分开写"代码交付"与"手工动作" |

**评审顺带纠正的三处事实错**（已改）：
- `proposal.md:3` 原写「Langfuse 已经跑在 **dev/prod**」与 D17 / ADR-0011「prod 从未部署、未运行验证」矛盾 → 改为"dev 已跑、prod 未验证"
- 「`_run_generation` **全仓**唯一调用点」措辞不严（测试有约 20 处直调）→ 改为"**生产侧**唯一调用点"
- `.env.example` 的 `LANGFUSE_ENABLE` 为空串（等效 false），只改 `.env.template` 会造成三处漂移 → 配置项扩为三个文件一致化

**评审的「未能验证」清单** → 未验证断言由 1 条扩为 4 条（写进 design 的 Risks），并在 DoD 第④条逐条验。另新增两条 open question：入站白名单的最终字符集（须与服务端实际接受的字符集对齐）、是否给 `check_docs` 补 openspec specs 的扫描范围（**不在本变更内**，登记为遗留）。

## §7 grilling 轮的处置（2026-09-22，7 条决定全部采纳）

按 `grilling` 的方式把设计树 frontier 逐条摆出、逐条收敛。其中 **5 条改变了设计或规格**（Q1/Q2/Q3/Q5/Q6），2 条是登记性约束（Q4/Q7）。

| # | 问题 | 决定 | 落在哪 |
|---|---|---|---|
| Q1 | 入站 id 校验放哪一层 + 非法时"静默换"还是"400 拒" | **middleware、`set()` 之前；静默重生成、不返回 400** | D3（含依据）+ spec 新 scenario「校验发生在 id 生效之前」 |
| Q2 | `settings.py` 内置 `LANGFUSE_*` 默认值要不要本次清 | **本次一并清**（key 类改空串、HOST 与模板对齐） | **新增 D19** + proposal 的代码/配置两处 |
| Q3 | 清理 CLI 的三个具体阈值 | **下界 1 天 / 上限 1000 条 / 必须 `--yes`（不用交互输入）** | D7 + spec requirement 写死数值 |
| Q4 | worktree 的 `.env` 保持软链还是改独立副本 | **保持软链**，只加"改前记录原值"的纪律 | D18（去掉原先悬置的"或独立副本"） |
| Q5 | 术语：「保留期」还是「保留窗口」；`trace_id` 要不要点明双重身份 | **统一「保留期」**（与 `--retention-days` 同源）；`trace_id` 条目补"同时是 Langfuse trace id + 不可信入站输入" | proposal 文档清单 + DoD ⑩。**落地时才改 `glossary.md`** —— 它描述现状，现在写未来态会违"只写当前状态" |
| Q6 | `eval_ragas` 的"每问一条 trace"怎么落 | **抽"处理单问"的小函数并 `@observe`**，循环里传 `langfuse_observation_id` | D2 补粒度与做法（含"v2 无上下文管理器"的依据） |
| Q7 | 进程级默认 `trace_id` 会不会让非请求上下文的 trace 撞成一条 | **本次不改代码，登记约束**：新增根的前提是处于有 per-request id 的上下文 | **新增 D20** |

**本轮查证的事实**（子代理实读代码所得，非推测）：

- `middleware/trace_id.py:33` 回写响应头用的是**局部变量**，不重读 contextvar；`api/chat.py:193/248` 的 SSE `done` **是重读**的 → 这是 Q1 那条硬约束的来源
- Langfuse v2 客户端对 trace id **零校验**（`TraceBody.id` 无 pattern/max_length），且 ingestion 异常被 `client.py:1511-1512` **吞掉只记日志** → 非法 id 的后果是**静默丢 trace**
- RAGAS 裁判打分在生成循环**返回之后**的 `run_evaluation`（`:243`），逐问生成是**全串行 for**（无 gather）
- `settings.py` / `.env` / `.env.template` 的四个 `LANGFUSE_*` **三方全不一致**；内置 HOST 指向仓库内**不存在的服务名**
