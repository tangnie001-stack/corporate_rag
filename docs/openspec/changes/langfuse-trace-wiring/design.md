## Context

Langfuse 自托管后端已按 ADR-0011 降到 v2.95.11（复用既有 PostgreSQL 的独立 database，web 仅回环可达），但**应用侧从未接线**：

- `@traced`（`src/infra/llm/langfuse_tracing.py:34`）全仓**零应用点**
- `LangfuseTracer` 只在 `src/services/agent_service.py:765` 建了一个从不使用的实例
- `current_tracer` 的唯一读取点在 `src/rag/stream.py::stream_answer`，而该函数**零调用方**
- `LANGFUSE_ENABLE` 只被 `prompt_manager` 读取，而远端 prompt 名单已按 ADR-0010 出列为空 → 实际零网络

结论：即使打开开关也不产出任何 trace。ADR-0011 把这条列为**另案不解决**，并在复查条件②③写明接线时的两项义务。

**关键实测结论**（本次在 `corporate-rag-app` 容器内、打真实 v2 服务端跑通，探针数据已清理）：

| 项 | 结果 |
|---|---|
| `langfuse.callback.CallbackHandler` | ❌ **import 即失败** —— SDK 2.60.10 依赖 `langchain.schema.agent` / `langchain.callbacks.base`，langchain 1.x 已删除这些路径 |
| `langfuse.decorators.observe` | ✅ 同步 / 异步 / **异步生成器**三种函数形态均落库成功 |
| trace id 自定义 | ✅ 根调用传 `langfuse_observation_id="trace_custom"` → trace id 即该值 |
| `langfuse_context.configure(enabled=False)` | ✅ 关闭后 50 次观察仅产出 **1 行** SDK 警告；单次 `@observe` 约 **0.21 ms** |
| `langfuse_context.update_current_observation(...)` | ✅ 支持 model / input / output / usage / completion_start_time 等字段 |

**运行时拓扑**（决定 trace 根落点的关键）：

```
HTTP 请求 → trace_id middleware → current_trace_id
 │
 ├─► api/chat.py::_stream_rag_response()   ← async gen（SSE 响应体，不跑 LLM）
 │     ├─ agent_service.stream_chat()      ← 只取历史 + 建 ctx
 │     ├─ asyncio.create_task(_run_with_finalize(...))     ★ 后台任务
 │     │     └─ answer_builder() → _run_generation(...)    ★ 全仓唯一调用点
 │     │            └─ graph.astream_events(...)
 │     │                 ├─ agent_model → model.astream()          [主 agent]
 │     │                 ├─ tools → retrieve_kb / rewrite_query / parse_temporal
 │     │                 └─ delegate_task → fork 子代理
 │     └─ async for event in subscription: yield to_sse(event)
 │
 └─► cli/eval_ragas.py   每问 set current_trace_id=eval_<hex> → graph.ainvoke()   ★ 独立入口
```

## Goals / Non-Goals

**Goals：**

- 一轮对话产出**一个** trace，id 与请求 `trace_id` 逐字一致（日志 / 响应头 / SSE done / Langfuse 四方对齐）
- 主 agent 每次 LLM 推理产出一条带 model / input / output / usage / 首 token 时间的 generation
- CLI 评测链路按问产出 trace，兑现 `src/cli/README.md:135` 的既有承诺
- `LANGFUSE_ENABLE` 成为应用侧 trace 总开关，默认 `true`、可关闭
- 落地 trace 保留/清理（ADR-0011 复查条件③）
- 清掉 LangfuseTracer 那一整套死代码

**Non-Goals（明确不做，避免范围蔓延）：**

- **不引 ClickHouse / worker / S3**，不动服务端版本与部署形态（ADR-0011 复评结论即"维持 v2"）
- **不接线辅助 LLM**（`rewrite_query` / `parse_temporal` / query 分类）与 **fork 子代理的 LLM generation** —— 它们的嵌套会自动成立，但不追加显式 generation；留作 P2
- **不接线文档入库链路的实体抽取**（`document_entity_extractor`）—— 不在对话 task 内，属另一条链路
- **不让 RAGAS judge LLM 进生产 trace** —— 评估脚本的裁判模型与业务链路无关
- **不削减既有日志** —— trace 与日志是两条并行的观测线，受众不同（可 grep vs 可视化），本变更不移除任何日志
- **不改 trace 内容记录范围** —— 见 D8

## Decisions

### D1 SDK 路线：用 `@observe` 装饰器，不用 CallbackHandler，不用自研封装

**选 SDK 原语（`langfuse.decorators.observe` + `langfuse_context`）。**

- **否决 CallbackHandler**：已实测在 langchain 1.x 下不可 import。要走出路必须升 langfuse SDK ≥3.x，而 v3/v4 SDK 要求服务端 ≥3.63 / v4 —— 与 ADR-0011 的 v2 降级直接冲突，且 v3+ 自托管要 ClickHouse。**这条路在"只用 v2"的约束下是死的。**
- **否决继续用自研 `LangfuseTracer`**：它是"包一层低层 client API + 手写 `start_trace` / `start_generation`"，正是 Langfuse 官方标注的旧式手动路径；且它正是当前接线断裂的载体（要靠 `@traced` 触发，而 `@traced` 零应用点）。
- `@observe` 的收益：官方支持的接入方式、自动处理 observation 起止与异常、嵌套靠 contextvar 自动成立、同步/异步/异步生成器均已实测可用。

### D2 trace 根落在 `_run_generation`（后台生成任务内）

**选 `src/services/agent_service.py::_run_generation`。**

- 它是全仓**唯一**的生成入口（`api/chat.py:205` 的 `answer_builder` 闭包），且**本身就在后台 task 内** → 根与全部下游 observation 处于同一 context，**不存在跨 task 的 contextvar 继承问题**。
- **否决 `_stream_rag_response`（HTTP 侧 SSE 生成器）**：生成在另一个 task 里跑，根会先于子树结束；跨 task 的父子关联依赖 `create_task` 的上下文拷贝，脆弱且语义错位。
- **否决 `AgentService.stream_chat`**：该函数不跑生成（只取历史 + 建 `RequestContext`），根会开了就关。
- CLI 侧在 `eval_ragas` 的每问循环内另开一条根（它不走 `_run_generation`）。

### D3 trace id 对齐用 `langfuse_observation_id` 入参

根 `@observe` 调用时传 `langfuse_observation_id=<current_trace_id>`。SDK 会把根 observation 的 id 直接用作 trace id（已实测）。这保住了"同一个 id 贯穿日志与 Langfuse"这一设计意图，且不新增任何 id 生成逻辑。

> 注意：该 id 必须作为**调用时的关键字参数**传入（SDK 从 kwargs 取用后不会传给被装饰函数体）。

### D4 主 agent generation 用"装饰节点闭包 + 函数内回填字段"

`agent_model` 是 `make_agent_model_node` 内部的闭包，且模型调用有两个分支（未绑 KB 传 temperature、绑 KB 不传）。做法：

- 给闭包加 `@observe(name=..., as_type="generation")`
- 在函数内（**两个分支合流之后**）用 `langfuse_context.update_current_observation(...)` 回填 model / input / output / usage / `completion_start_time`

**理由**：改动面最小（一个装饰器 + 一次 update），不触碰两个 `astream` 分支的调用形态。

**代价（显式接受）**：节点内的日志与迭代计数逻辑会被计入该 generation 的时长。**替代方案**是把两个分支的调用抽成独立 helper 再装饰 —— 时长更纯，但会改动 `agent_node` 结构，并与在途 change 的落点冲突更重（见 D12）。

现成红利：函数内已有 `first_chunk_ms`（首个 chunk 到达时刻），可直接作为 `completion_start_time` 的数据来源，让 Langfuse UI 直接呈现首 token 延迟。

### D5 开关：`langfuse_context.configure(enabled=LANGFUSE_ENABLE)` 于 lifespan 启动期

- `settings.py:307` 的代码默认**本来就是 `true`**；现状 false 来自 `.env:53` / `.env.template:87` 的覆盖。本变更把这两处改为 `true`，**保留开关**。
- **翻开关本身是空操作**（已核实）：全仓只有 `prompt_manager` 读它，而 `PROMPT_NAMES = {}` → `_resolve` 在名单为空时直接返回本地正文、不发网络请求；启动期模板校验（`config/prompts/validation.py`）的对象是打进镜像的本地文件，**不碰 Langfuse**。因此**不会引入启动期对 `langfuse-web` 的依赖**。
- 关闭时 SDK 会打**一行**警告（不刷屏），`@observe` 单次约 0.21 ms —— 对本轮秒级生成可忽略，故**不做条件装饰**（避免多一处分叉）。

### D6 关停时 flush 缓冲事件

SDK 默认批量上报（`flush_at` / `flush_interval`）。lifespan 现只有 start / yield / stop，**须在关停路径调用 `langfuse_context.flush()`**，否则最后一批缓冲事件随进程消失。这是"重启后 trace 缺尾巴"这类隐性故障的唯一防线。

### D7 保留/清理：独立 CLI + dry-run，保留期 30 天

- Langfuse v2 OSS **无 Data Retention**（属企业版）→ 必须自建，这也是 ADR-0011 复查条件③的硬约束。
- 形态：`src/cli/` 下新增清理入口，用 SDK 的 `client.api.trace.delete_multiple`（已确认可用）；**保留期 30 天**，可用参数覆盖；**支持 dry-run**（只输出将删数量与标识）。
- 定时落地形态（宿主 cron / systemd timer / compose 定时服务）留待执行阶段决定，**不写进 capability 的 requirement**（避免把部署细节固化成规格）。
- 保留期选 30 天的理由：能覆盖"发版后回看上一版对比"这类典型排查，库增长仍在可控量级（当前库仅 11 MB、traces=1）。

### D8 trace 内容记录范围：沿用 SDK 默认（记录原文），不引入新开关

- SDK 默认 `capture_input=True` / `capture_output=True`。**不关闭**：trace 的主要价值就是看原文，关掉后只剩 model/usage/耗时，等于花一套链路买半个能力。
- **不新增 `LANGFUSE_CAPTURE_CONTENT`**：最小改动原则。日志层的 `LLM_LOG_CONTENT` 面向的是**运维日志流**（防内容刷屏，默认关），与 trace 是不同用途的 sink，强行合并会让两边语义都变浑。
- 数据面并无新增暴露：prompt 正文、检索上下文、用户问答**本来就在**同实例的 PostgreSQL（对话历史 / 向量库）与镜像内的 YAML 中；Langfuse web 亦仅回环可达。
- **真正的风险是保留期错配**：trace 会活过对话记录被删之后。故 spec 要求把"trace 记录原文 + 保留 30 天"作为**已知事实写入文档**，不让人从代码反推。
- 若日后有合规要求，Langfuse client 支持 `mask` 回调，可在不改业务代码的前提下按需脱敏 —— 本变更不预先实现。

### D9 死代码清理与 `estimate_usage` 归属

删除：`LangfuseTracer` 类、`@traced` 装饰器、`current_tracer` ContextVar、`src/rag/stream.py::stream_answer`、`Event.TRACE_READY` / `TRACE_INIT_FAILED` / `TRACE_SKIP`（**`log_events.py` 与 `log_event_specs.py` 两处同名登记**）。

连带项：`stream.py` 移除 `stream_answer` 后只剩 `estimate_usage`，而它仍有**两个活消费者**（`src/agents/graph/agent_node.py:215`、`src/agents/skills/fork_stream.py:327`）。归属方案在执行阶段定（候选：并入 `src/infra/llm/token_usage.py`，或保留 `rag/stream.py` 作薄模块），**无论哪种都要同步两个 import 点**。

### D10 ADR-0011 复查条件②的复评记录

本变更使 Langfuse **首次真正进入请求路径**，触发 ADR-0011 复查条件②。复评结论为**维持 v2 降级**，依据：接线不引 ClickHouse、不新增存储、Langfuse 仍在旁路（D3 的 id 对齐与 D5 的故障隔离保证对话不因 tracing 失败）。记录形式（change design 内记录 / 追加新 ADR）见 Open Questions。

### D11 覆盖边界由"嵌套自动成立"兜底

未显式接线的辅助 LLM（`rewrite_query` / `parse_temporal` / 分类）在物理上位于同一 task 的同一 context 内，**若日后给它们加装饰器会自动挂到正确的 trace 之下**，无需改动根。这让 P2 的扩展成本接近零，也是把 Non-Goals 划在这里的底气。

### D12 与 `turn-provenance-observability` 的次序

两者落点重叠且**不可并行**：

| 落点 | turn-provenance | 本变更 |
|---|---|---|
| `agent_node.agent_model`（温度分档段 `:158-167` + 两个 `astream` 分支 `:184-203`） | 提取单一真源 `temperature` 变量 + 扩 `model turn` 字段 | 装饰闭包 + 回填 generation 字段 |
| `agent_service._run_generation` | 发两条来源声明 status + 双 sink 写入 | 加 trace 根 |
| `log_events.py` / `log_event_specs.py` | 两处登记 5 个新事件 | 两处删除 3 个死事件 |

**已定：本变更先行**，`turn-provenance-observability` 后置，重启时按其落地后的实际代码复核行号与做法。

## Risks / Trade-offs

- **[Langfuse 进入请求路径带来的延迟]** → SDK 异步批量上报（实测 `@observe` 单次 0.21 ms）；根落在生成 task 内不影响 SSE 首字节；D5 的开关可在异常时一键关闭。
- **[tracing 故障影响对话]** → D5 的故障隔离要求写入 spec（后端不可达时对话仍正常完成，仅日志暴露）；SDK 自带异常吞掉与日志记录。
- **[trace 无界增长]** → D7 的清理机制（ADR-0011 复查条件③的硬要求，不可省）。
- **[v2 SDK 已 EOL，未来升级路径]** → 已知并接受（ADR-0011 已记录"升级须经 v3.29.0 中转、v2 期间 trace 不自动上行"）；本变更使 v2 期间真正积累了 trace，**升级时会有历史丢弃**，需在文档中登记。
- **[D4 的 generation 时长含非 LLM 逻辑]** → 显式接受并记录；若时长失真影响判断，再改为抽 helper（D4 的替代方案）。
- **[关停丢尾巴]** → D6 的 flush。
- **[trace 保留期与聊天记录保留期错配]** → D8：登记为已知事实，不引入开关。
- **[文档闸门误报]** → `src/cli/check_docs.py` 会校验文档里的 `src/**` 路径与反引号符号；删文件/改名后须同步更新引用，否则 pre-commit 拦截（这是预期行为，不是故障）。
- **[删 `TRACE_*` 事件可能影响既有断言]** → `tests/core/test_log_events.py` 断言两处登记一致，删除时须同步；执行阶段以 `pytest` 全绿为准。

## Migration Plan

1. **接线（不改变开关状态）**：先以 `LANGFUSE_ENABLE=false` 在本地把链路接完，确认关闭态下对话行为零变化 —— 这是一个"代码已接、行为未变"的可交付中间态。
2. **打开开关**：把 `.env` / `.env.template` 改为 `true`，在 dev 上跑一轮真实对话，核对四方 id 对齐（响应头 / 日志 / SSE done / Langfuse UI）。
3. **补清理机制**：接上 CLI 与定时落地，跑一次 dry-run 与一次真删。
4. **清死代码**：确认无引用后删 `LangfuseTracer` 一整套与三个事件。
5. **文档与 ADR**：同步 `docs/agents/` 与 `src/cli/README.md`，落 D10 的复评记录。

**回滚**：把 `LANGFUSE_ENABLE` 置回 `false` 即恢复"零 trace 产出"（接线代码保留但空转）；如需完全回退，`git revert` 本变更的提交即可，Langfuse 侧无 schema 变更、无需数据回滚。

## Open Questions

1. **辅助 LLM 与 fork 子代理是否纳入 P1** —— 当前划为 Non-Goals（D11）；若排查时发现"缺了这两块就看不明白一轮对话"，再提前。
2. **清理的定时落地形态** —— 宿主 cron、systemd timer、还是 compose 定时服务；prod（2 台机）与 dev（WSL）是否需要不同方案。
3. **`estimate_usage` 的最终宿主** —— 并入 `src/infra/llm/token_usage.py`，还是保留 `rag/stream.py` 作薄模块。
4. **D10 复评记录的形式** —— 写在 change design 内即可，还是按"推翻/确认既有 ADR"的惯例追加一条新 ADR（本仓 ADR 规则刚要求反向指针对称，本情形属"确认"而非"推翻"，倾向不追加）。
