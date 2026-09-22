## Why

Langfuse 已经跑在 dev/prod（v2.95.11，复用既有 PostgreSQL 实例），但**应用侧从未接线**：`@traced` 全仓零应用点、`LangfuseTracer` 只在 `agent_service.py:765` 建了一个从不使用的实例、`current_tracer` 的唯一读取点在 `rag/stream.py::stream_answer`，而该函数零调用方。结果就是「库里没有数据」—— 且这不是配置问题：即使把 `LANGFUSE_ENABLE` 打开也不产出任何 trace。

同时，日志 / 响应头 `X-Trace-ID` / SSE `done` 事件里那个 `trace_<uuid>` 与 Langfuse 的 trace id **设计上同源**（`LangfuseTracer.start_trace` 原本以 `id=current_trace_id.get()` 传入），但这条线今天断着：在日志里看到一个 trace_id，去 Langfuse 里找不到它。`src/cli/README.md:135` 已经写下「用 CSV 里的 trace_id 即可在日志 / Langfuse 中回溯」这句承诺，当前不成立。

**为什么是现在**：ADR-0011 把「接线 tracing」列为**另案不解决**，并在复查条件里写明两条硬约束 —— ②「Langfuse 首次被真正接入请求路径 → 重新评估降级决策」、③「接线那次变更必须一并落地 trace 保留/清理」。本变更就是执行那条另案。

## What Changes

- **应用侧 tracing 接线**：采用 Langfuse v2 SDK 的 `@observe` 装饰器 + `langfuse_context`（`langfuse.callback.CallbackHandler` 路线**已实测不可用** —— SDK 2.60.10 依赖 `langchain.schema.agent` / `langchain.callbacks.base`，langchain 1.x 已删除这些路径，import 即 `ModuleNotFoundError`）。
- **一轮生成 = 一个 trace**：trace 根落在后台生成任务内，trace id 与请求 `trace_id` 逐字一致（依赖 SDK 的 `langfuse_observation_id` 入参，已实测 trace id 等于该值）。日志 / 响应头 / SSE done / Langfuse 四方对齐。
- **主 agent 每轮 LLM 调用 = 一条 generation**：记录 model、input messages、output、token usage（取流聚合后的真实计数，缺失才回落估算）、首 token 时间。
- **CLI 评测链路同步接线**：`eval_ragas` 每问一个独立 trace，id 用既有的 `eval_<hex>`，兑现 `src/cli/README.md` 那句承诺。
- **开关真正生效**：`LANGFUSE_ENABLE` 接到 SDK 的启用开关；`.env` 与 `.env.template` 的取值由 `false` 改为 `true`，**开关能力保留**（关闭时不产出 trace、不影响对话）。三点说明：① `settings.py` 的代码默认**本来就是 `true`**，现状 false 是 `.env` 覆盖所致；② **当前把开关翻成 true 是行为上的空操作**（全仓仅 `prompt_manager` 读它，而远端 prompt 名单已按 ADR-0010 出列为空 → 直接返回本地正文、不发起网络请求；启动期模板校验也不碰 Langfuse）；③ 开关接线**必须同时覆盖 CLI 路径** —— `eval_ragas` 不经过 `main.py` 的 lifespan，只在 lifespan 里调 `configure(enabled=...)` 会让 CLI 侧失控。
- **开关的双重语义（已知耦合，显式登记）**：本变更后 `LANGFUSE_ENABLE` 同时管两件事 —— `prompt_manager` 的远端读取与 trace 产出。风险是「日后把 prompt 名单加回时，打开 trace 会连带打开 prompt 远端读取」。本次**不拆开关**（最小改动），但把该耦合写进 `glossary.md` 与 `design.md`。
- **测试策略重定**：`tests/infra/llm/test_langfuse.py` 现以 `not LANGFUSE_ENABLE` 作 `skipif`，而代码默认是 `true` → 在无 `.env` 的环境（CI / 新 clone / worktree）**不会 skip，会构造真实客户端发网络**，违反「测试 mock 外部依赖，不发起真实网络调用」。本变更重写该文件时一并改为替身 / mock，**不再以开关决定是否运行**。
- **retention 删除的级联须先验证再落地**（ADR-0011 复查条件③的执行前提）：目前只确认 `client.api.trace.delete_multiple` **方法存在**，未验证删完库是否干净（`observations` / `scores` / `dataset_run_items` 有无孤儿）与 Langfuse UI 是否正常。**真删之前先做一次小规模验证**。
- **本变更自身的取舍写入 ADR**：D8「trace 记录 prompt / 回答原文 + 保留 30 天」命中 `docs/adr/README.md` 的判定（存在多个合理候选 / 是取舍 / 后人可能重新争论）→ 需一条新 ADR，与 ADR-0011 复查条件②的复评记录**分开记**。
- **prod 侧落地**：改 `.env.template` ≠ 改到 prod 机上的 `.env`（须人工同步）；且须先确认 prod 的 `langfuse-web` 确实在跑（ADR-0011 记 prod 从未部署过 v3，降级后亦未验证）—— 否则打开开关就是往一个不存在的后端发。
- **trace 保留/清理机制**（ADR-0011 复查条件③）：Langfuse v2 OSS 无 Data Retention（属企业版），须自建清理，否则接线后库无界增长。清理能力已确认可用（`client.api.trace.delete` / `delete_multiple`）。
- **ADR-0011 复查条件②的复评结论落地**：本变更使 Langfuse 首次真正进入请求路径，须显式记录复评结论（预期为「维持 v2」，因为接线不引 ClickHouse、不把 Langfuse 放进对话成败的关键路径）。
- **清理死代码**：`LangfuseTracer` 类、`@traced` 装饰器、`current_tracer` ContextVar、`rag/stream.py::stream_answer`、以及 `Event.TRACE_READY` / `TRACE_INIT_FAILED` / `TRACE_SKIP` 三个仅被 `LangfuseTracer` 使用的事件。
- **连带处理**：`rag/stream.py` 在移除 `stream_answer` 后只剩 `estimate_usage`，而该函数仍有两个活消费者（`agent_node.py:215`、`fork_stream.py:327`）—— 需为它定一个合理的宿主归属。

## Capabilities

### New Capabilities

- `llm-tracing`: 应用侧 trace 产出的契约 —— 一轮生成一个 trace、trace id 与日志 trace_id 同源、observation 树的最小形状（根 + 主 agent generation）、开关语义（关闭不产出且不影响对话）、trace 保留与清理机制。

### Modified Capabilities

（无 —— 本变更不改变既有 capability 的 requirement：`observability-backend` 管的是部署面（容器 / 资源 / 可达性 / 凭据 / profile），本变更复用该后端、不改变其任何要求；`observability-logging` 管日志格式与事件注册机制，删除三个死事件是清理而非要求变更。）

## Impact

- **代码**：
  - `src/infra/llm/langfuse_tracing.py` — 由自研 `LangfuseTracer` 封装改为 SDK 原语接线（或整体替换）
  - `src/services/agent_service.py` — `_run_generation`（trace 根落点；全仓唯一调用点，且本身就在后台 task 内）
  - `src/agents/graph/agent_node.py` — 主 agent LLM 调用点的 generation（温度分档段与两个 `model.astream` 分支）
  - `src/main.py` — lifespan 接线（启用开关；关停时 flush 缓冲事件）
  - `src/cli/eval_ragas.py` — 每问一个 trace 根
  - `src/cli/` — 新增 trace 清理 CLI
  - `src/rag/stream.py`、`src/infra/llm/trace_context.py` — 删死代码、迁 `estimate_usage`
  - `src/core/log_events.py` + `src/core/log_event_specs.py` — 删三个 `TRACE_*` 事件（**两处同名登记**）
- **配置**：`.env`、`.env.template` 的 `LANGFUSE_ENABLE` → `true`
- **依赖**：无新增（`langfuse` 与 `langchain` 均已在 `pyproject.toml`）
- **文档**：`docs/agents/glossary.md`（**`:207` 的「`trace 保留窗口`」条目现写"另案，尚未落地…当前 tracing 未接线"，本变更直接使其过时**，须改写；并登记开关的双重语义）、`docs/agents/code-map.md`（**本变更新增一个 CLI 模块、移除 `src/infra/llm/` 内一整套结构**，而它是「改动代码前定位文件」的唯一归属）、`docs/agents/api_contract.md`（如 trace 关联字段对外可见）、`docs/agents/logging-rules.md`（事件增减）、`docs/agents/data-flow.md`（接线后的链路）、`docs/agents/cookbook.md`（**`:315` 一段既过时、又把读者指向将被删除的 `LangfuseTracer`**）、`src/cli/README.md`（承诺兑现）、`docs/adr/`（**新 ADR**：trace 记原文 + 保留 30 天；**另记** ADR-0011 复查条件②的复评结论）
- **在途 change 的次序约束**：`turn-provenance-observability`（0/36）与本变更**落点重叠**（`agent_node.agent_model` 温度分档段 `:173-200`、两个 `astream` 分支 `:184-203`；`agent_service._run_generation`），两者**不可并行**。已定：**本变更先行**，`turn-provenance-observability` 后置，重启时按本变更落地后的实际代码复核其行号与做法。**注**：该 change 的 `tasks.md` 4.1 沿用了错误的行号 `:158-167`（那是函数头与迭代日志，非温度分档段），重启时一并改正。
- **测试**：`tests/infra/llm/test_langfuse.py`（**须整体重写** —— 它测的是将被删除的 `LangfuseTracer`，且 skip 条件本身是雷，见 What Changes）、新增接线用例（用替身 / mock，**不发真实网络**）、受影响的 `_run_generation` / `eval_ragas` 相关用例
