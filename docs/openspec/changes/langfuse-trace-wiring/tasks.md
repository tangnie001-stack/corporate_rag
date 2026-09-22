# langfuse-trace-wiring —— 执行档索引

> **本文件不承载任务清单。** 按 `docs/agents/dev-flow.md` 的 ⑤ 环节规定（`writing-plans` 与 openspec tasks **二选一，不可都写**），本 change 的执行档由**一份实施计划**承载，另有一个**计划外的部署动作**。

| 执行档 | 载体 | 范围 | 完成标准（DoD） |
|---|---|---|---|
| **实施计划（唯一一份）** | `docs/superpowers/plans/2026-09-22-langfuse-trace-wiring.md`（⑤ 环节产出） | 接线与开关 → 清理 CLI 的**代码** → 死代码清理 → 文档同步与 ADR-0011 复评记录。全部是普通代码任务 | ① **关闭态**：一轮对话的行为与接线前逐项一致（SSE 事件序列、落库、引用），`pytest` 全绿；② **开启态**：dev 真实一轮对话的**四方 id 对齐**（响应头 / 日志行 / SSE done / Langfuse UI），主 agent generation 带 model / input / output / usage / 首 token 时间；③ 清理 CLI 的 `--dry-run` 正确、保留期内零改动、无超期数据时重复执行安全；④ 已删符号全仓零引用（含 tests），质量门禁全绿（`pytest` / `ruff` / `pyright` / `check_docs` / `check_adr`）；⑤ 文档不再出现"UI 里看不到数据是正常的"这类已过时叙述，复评记录可被独立读到 |
| **计划外的部署动作** | 不落计划文件（部署与数据操作，按 ⑥「不适合走 SDD 的情形」执行） | ① 清理任务**定时接入**（宿主 cron / systemd timer / compose 定时服务，开工前定）；② **真删验证**（对运行中的 langfuse 库实际删除超期 trace） | ① 定时任务在 dev 与 prod 各自可运行；② 真删只影响早于保留期的 trace，保留期内零改动。**注意**：这两条仍是本 change 的 DoD（ADR-0011 复查条件③要求清理与接线一并落地），不可挪到 change 之外 |

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
| 4 | ADR-0011 复查条件②的复评记录形式（写在 change design 内 vs 追加新 ADR） | 实施计划收尾前 |
