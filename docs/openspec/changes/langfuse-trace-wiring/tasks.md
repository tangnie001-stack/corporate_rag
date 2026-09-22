# langfuse-trace-wiring —— 执行档索引

> **本文件不承载任务清单。** 本变更跨子系统且需分阶段交付，按 `docs/agents/dev-flow.md` 的 ⑤ 环节规定（`writing-plans` 与 openspec tasks **二选一，不可都写**），执行档按阶段拆为实施计划，落在 `docs/superpowers/plans/`：
>
> | 阶段 | 计划文件（⑤ 环节产出） | 范围 | 完成标准（DoD） |
> |---|---|---|---|
> | **P1** | `docs/superpowers/plans/2026-09-22-langfuse-trace-wiring-p1-instrument.md` | 接线与开关：死代码与新模块的分界 → `_run_generation` 落 trace 根（trace id 走 `langfuse_observation_id`）→ `agent_model` 闭包加 generation 与字段回填 → `eval_ragas` 每问根 → `main.py` lifespan 接开关与关停 flush → **开关保持 `false`** 先验证零行为变化 → 再置 `true` 实跑核对 | ① 关闭态：一轮对话的行为与接线前逐项一致（SSE 事件序列、落库、引用），`pytest` 全绿；② 开启态：dev 真实一轮对话的**四方 id 对齐**（响应头 / 日志行 / SSE done / Langfuse UI），主 agent generation 带 model / input / output / usage / 首 token 时间 |
> | **P2** | `docs/superpowers/plans/2026-09-22-langfuse-trace-wiring-p2-retention.md` | trace 保留与清理：清理 CLI（保留期参数、`--dry-run`、幂等）+ 定时落地形态选定与接入 | ① `--dry-run` 输出待删条数与标识且不删任何数据；② 真删只影响早于保留期的 trace，保留期内零改动；③ 无超期数据时重复执行正常结束；④ 定时落地方案在 dev 与 prod 各自可运行 |
> | **P3** | `docs/superpowers/plans/2026-09-22-langfuse-trace-wiring-p3-cleanup-and-docs.md` | 收尾：删 `LangfuseTracer` / `@traced` / `current_tracer` / `stream_answer` / 三个 `TRACE_*` 事件（`log_events.py` 与 `log_event_specs.py` 两处同名）→ `estimate_usage` 迁宿主并同步两个 import 点 → 文档同步（`api_contract` / `logging-rules` / `data-flow` / `cookbook` / `src/cli/README.md`）→ 落 ADR-0011 复查条件②的复评记录 | ① 全仓对已删符号零引用（含 tests）；② 质量门禁全绿（`pytest` / `ruff` / `pyright` / `check_docs` / `check_adr`）；③ 文档不再出现"UI 里看不到数据是正常的"这类已过时叙述；④ 复评记录可被独立读到 |
>
> **分阶段的原因**：① 跨 ≥2 个独立模块（`src/infra/llm` + `src/services` + `src/agents/graph` + `src/cli` + `src/main.py` + `src/core`）；② 硬顺序依赖（清理与删死代码必须在接线验证之后，否则删掉的正是唯一可用的观测依据）；③ **各阶段验收标准不同**（P1 看 trace 是否产出与 id 是否对齐，P2 看清理是否幂等且不误删，P3 看是否还有残留引用与文档一致性）；④ 与在途 change `turn-provenance-observability` 重叠，已声明**本变更先行**。

## §1 前置事实（本变更定稿时的实测结论）

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

## §2 实施期修正记录

执行阶段若发现与设计不符的事实，逐条登记于此（格式：日期 / 落点 / 事实 / 处置 / 是否回改 `design.md`）。**不要直接改写 `design.md` 的既有决策叙述** —— 决策的修订走正常流程并在此留索引。

| 日期 | 落点 | 事实 | 处置 | 回改 design |
|---|---|---|---|---|
| — | — | — | — | — |

## §3 未决项（承接 `design.md` 的 Open Questions）

| # | 未决项 | 何时必须收敛 |
|---|---|---|
| 1 | 辅助 LLM（`rewrite_query` / `parse_temporal` / 分类）与 fork 子代理的 generation 是否纳入 | P1 实跑后：若"缺这两块就看不明白一轮对话"则提前；否则维持 Non-Goal |
| 2 | 清理的定时落地形态（宿主 cron / systemd timer / compose 定时服务），dev 与 prod 是否同方案 | P2 开工前 |
| 3 | `estimate_usage` 的最终宿主（并入 `src/infra/llm/token_usage.py` 或保留 `rag/stream.py` 作薄模块） | P3 开工前 |
| 4 | ADR-0011 复查条件②的复评记录形式（写在 change design 内 vs 追加新 ADR） | P3 收尾前 |
