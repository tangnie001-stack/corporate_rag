# langfuse-trace-wiring —— 执行档索引

> **本文件不承载任务清单。** 按 `docs/agents/dev-flow.md` 的 ⑤ 环节规定（`writing-plans` 与 openspec tasks **二选一，不可都写**），本 change 的执行档由**一份实施计划**承载，另有一个**计划外的部署动作**。

| 执行档 | 载体 | 范围 | 完成标准（DoD） |
|---|---|---|---|
| **实施计划（唯一一份）** | `docs/superpowers/plans/2026-09-22-langfuse-trace-wiring.md`（⑤ 环节产出） | 接线与开关（**服务侧 + CLI 侧两处**，D5）→ 测试重定（D14）→ 清理 CLI 的**代码** → 死代码清理 → 文档同步（含 `glossary.md` / `code-map.md` / `cookbook.md:315`）→ **新 ADR（D16）+ ADR-0011 复评记录（D10）**。全部是普通代码任务 | ① 测试重定先完成，**"pytest 全绿"才可被当作证据**（D14）；② **关闭态**：一轮对话行为与接线前逐项一致（SSE 事件序列、落库、引用）；③ **开启态**：dev 真实一轮对话的**四方 id 对齐**（响应头 / 日志行 / SSE done / Langfuse UI），主 agent generation 带 model / input / output / usage / 首 token 时间；④ **两件未实测的事已验**：取消一轮生成后 trace 仍在；后端停掉后对话仍正常完成（故障隔离）；⑤ 清理 CLI 的 `--dry-run` 正确、保留期内零改动、无超期数据时重复执行安全；⑥ 已删符号全仓零引用（含 tests），质量门禁全绿（`pytest` / `ruff` / `pyright` / `check_docs` / `check_adr`）；⑦ 文档不再出现"UI 里看不到数据是正常的"这类叙述，`glossary.md` 的「`trace 保留窗口`」条目已改写，D16 新 ADR 与 D10 复评记录可被独立读到 |
| **计划外的部署动作** | 不落计划文件（部署与数据操作，按 ⑥「不适合走 SDD 的情形」执行） | ① **级联删除小规模验证**（不通过则不许接定时任务，D15）；② 清理任务**定时接入**（宿主 cron / systemd timer / compose 定时服务，开工前定）；③ **prod 侧落地**：同步 prod 机上的 `.env`（改模板 ≠ 改到机器）+ 确认 `langfuse-web` 在跑（D17） | ① 删少量超期 trace 后 `observations` / `scores` / `dataset_run_items` **无孤儿**，UI 正常（D15）；② 定时任务在 dev 与 prod 各自可运行；③ 真删只影响早于保留期的 trace，保留期内零改动；④ prod 侧"后端不在跑"的情形已按故障隔离验收过。**注意**：这四条仍是本 change 的 DoD（ADR-0011 复查条件③要求清理与接线一并落地），不可挪到 change 之外 |

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
| — | — | — | — | — |

## §4 未决项（承接 `design.md` 的 Open Questions）

| # | 未决项 | 何时必须收敛 |
|---|---|---|
| 1 | 辅助 LLM（`rewrite_query` / `parse_temporal` / 分类）与 fork 子代理的 generation 是否纳入 | 实施计划首轮实跑后：若"缺这两块就看不明白一轮对话"则提前；否则维持 Non-Goal |
| 2 | 清理的定时落地形态（宿主 cron / systemd timer / compose 定时服务），dev 与 prod 是否同方案 | 计划外部署动作开工前 |
| 3 | `estimate_usage` 的最终宿主（并入 `src/infra/llm/token_usage.py` 或保留 `rag/stream.py` 作薄模块） | 死代码清理那一步开工前 |
| 4 | ADR-0011 复查条件②的复评记录形式（写在 change design 内 vs 追加新 ADR）—— 注意与 D16 的新 ADR **是两件事**，不要合并 | 实施计划收尾前 |
| 5 | `LANGFUSE_ENABLE` 是否拆成两个开关（prompt 远端 / trace 产出）—— 见 D13，当前不拆 | 日后把 prompt 名单加回 `PROMPT_NAMES` 时**必须**重评 |
| 6 | `turn-provenance-observability/tasks.md` 4.1 的行号 `:158-167` 是错的（应为 `:173-200`） | 该 change 重启时顺手改正（属对方工件，本变更不擅自改） |

## §5 定稿期勘误（本轮审阅发现，已就地修正）

| 项 | 原写法 | 实际 | 处置 |
|---|---|---|---|
| `agent_node` 温度分档段行号 | `:158-167` | **`:173-200`**（158 是 `async def agent_model`，173 是 `turn_start`，184 起是 `if state.kb_id` 的两个分支） | proposal / design 已改；错源在 `turn-provenance-observability/tasks.md` 4.1，见 §4 第 6 条 |
| `tests/infra/llm/test_langfuse.py` 的 skip 条件 | 视为"条件需随开关语义重定" | **是必须修的雷**：代码默认 `true` + 无 `.env` → 不 skip → 真发网络 | 升格为 design D14 + DoD 第①条 |
| retention 删除 | 视为"能力已确认可用" | 只确认了**方法存在**，未验证级联与孤儿 | 升格为 design D15 + 计划外动作第①步（**前置**） |
| spec 的取消 scenario | 已写入但未实测 | **纸面断言** | 保留契约，列为首个验证项（Risks 首条 + DoD 第④条） |
