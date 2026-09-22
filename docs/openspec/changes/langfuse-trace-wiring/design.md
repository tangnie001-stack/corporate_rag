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

**但该 id 的来源今天不可信，必须先加校验**。`src/middleware/trace_id.py:18-25` 直接取 `X-Trace-ID` 头（或 `?trace_id`），**零字符集与长度校验**。接线前它只是日志关联串，最坏是日志难看；接线后它成为 Langfuse 的**trace 主键**，而 `client.trace(id=...)` 是 **upsert** —— 等于把"持久化观测数据的主键"交给调用方：

- 非法字符/超长 → Langfuse 拒绝 → 该 trace **静默丢失**（而对话照常，没人会发现）
- 任意 id 可注入 → 观测库可被污染，且保留 30 天
- 若 id 外泄（截图、共享日志、CSV）→ 可被重放并**覆盖**同 id 的 trace

**做法**：入站值先过白名单（建议 `^[A-Za-z0-9_-]{1,120}$`），不合法则**丢弃并服务端重新生成**（与缺失时的行为一致）。**四方对齐不受影响** —— 重生成的值会回写响应头、进日志、进 SSE `done`，仍是同一个 id 贯穿。备选是"一律服务端生成、彻底忽略入站值"，但那会破坏现有的"由调用方指定 trace_id 做端到端串联"能力（CLI 评测与联调都依赖它），故不选。

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
- **开关接线必须覆盖两条入口**：`main.py` 的 lifespan（服务侧）**与** `cli/eval_ragas.py`（CLI 侧）。`eval_ragas` 不经 lifespan，只在 lifespan 里 `configure(enabled=...)` 会让 CLI 侧完全脱离开关控制 —— 该路径的 trace 产出将无法关闭。两处都调 `configure`（幂等）。

### D6 关停时 flush 缓冲事件（服务侧 **与 CLI 侧**都要）

SDK 默认批量上报（`flush_at` / `flush_interval`）。lifespan 现只有 start / yield / stop，**须在关停路径调用 `langfuse_context.flush()`**，否则最后一批缓冲事件随进程消失。这是"重启后 trace 缺尾巴"这类隐性故障的唯一防线。

**CLI 侧同样要 flush，且这里不只是"缺尾巴"**：`eval_ragas` 不经 lifespan，而 `--gate` 路径会提前 `sys.exit`、异常路径也会直接退出 —— 缓冲未发送时 **CLI trace 时有时无**，于是 DoD 里"CSV 的 trace_id 可在 Langfuse 查到"退化为**间歇性成立**，无法作为验收证据。做法：CLI 在生成段结束后 **以及 `finally` / `atexit`** 调用 `langfuse_context.flush()`。

### D7 保留/清理：独立 CLI + dry-run，保留期 30 天，**并带运行期护栏**

- Langfuse v2 OSS **无 Data Retention**（属企业版）→ 必须自建，这也是 ADR-0011 复查条件③的硬约束。
- 形态：`src/cli/` 下新增清理入口，用 SDK 的 `client.api.trace.delete_multiple`（已确认可用）；**保留期 30 天**，可用参数覆盖；**支持 dry-run**（只输出将删数量与标识）。
- 定时落地形态（宿主 cron / systemd timer / compose 定时服务）留待执行阶段决定，**不写进 capability 的 requirement**（避免把部署细节固化成规格）。
- 保留期选 30 天的理由：能覆盖"发版后回看上一版对比"这类典型排查，库增长仍在可控量级（当前库仅 11 MB、traces=1）。
- **本入口持有破坏性权限，必须有护栏**（原先只写了 dry-run 与可覆盖保留期，不足）：
  - **保留期下界**：低于下界（建议 ≥1 天）直接拒绝 —— 防一条 `--retention-days 0` 删光运行库
  - **显式确认**：非 dry-run 须 `--yes`（或交互确认），不接受"默认就删"
  - **单次删除上限**：超过上限即中止并提示分批 —— 防误配把库删空而来不及发现
  - **审计**：打印并将被删 trace 标识落日志 / 文件，事后可复盘"删了什么"
  - **执行环境约束**：仅允许在指定机器 / 环境变量下执行，防 dev 的配置误连 prod 库

### D8 trace 内容记录范围：记原文，但**只记该记的**（`capture_input` 必须显式关闭）

- trace 要能完整回放，所以**原文要记** —— `capture_output=True` 保留（SDK 默认）。
- **但 `capture_input` 必须显式关掉。** SDK 默认 `capture_input=True`，会把被装饰函数的**全部入参**序列化：根的入参是 `_run_generation(session_id, kb_id, query, history, deep_thinking, ctx, manager, graph, partial_holder, abort_signal, direct_skill)`，节点的入参是整个 `state`。序列化器会把 `RequestContext`、`asyncio.Event`、`StreamingRunManager`、编译后的 graph 等**内部运行时对象**照单全收（不报错，但全写进 trace）→ payload 与存储成本虚高、真正的输入被噪声淹没。
- **`update_current_observation(input=...)` 救不了这一条**：它只覆盖**最终值**，创建期的序列化已经发生。唯一有效做法是装饰时 `capture_input=False`，再**显式写入**该记的输入（query + 消息列表）。
- **不新增 `LANGFUSE_CAPTURE_CONTENT` 开关**：最小改动原则。日志层的 `LLM_LOG_CONTENT` 面向的是**运维日志流**（防内容刷屏，默认关），与 trace 是不同用途的 sink，强行合并会让两边语义都变浑。
- **数据面（把上一句说准）**：prompt 正文、检索上下文、用户问答**本来就在**同实例的 PostgreSQL（对话历史 / 向量库）与镜像内的 YAML 中，Langfuse web 亦仅回环可达 —— 所以记录原文**不引入新的数据类别**。但本变更确实**新增了一个持久的原文副本**（此前 trace 为空），且它与对话记录的**保留期不一致**（30 天 vs 对话记录的删除时机）→ trace 会活过对话被删之后。这一取舍由 **D16 的 ADR** 正式记下，并在 spec 与文档中作为已知事实登记。
- 若日后有合规要求，Langfuse client 支持 `mask` 回调，可在不改业务代码的前提下按需脱敏 —— 本变更不预先实现。

### D9 死代码清理与 `estimate_usage` 归属

删除：`LangfuseTracer` 类、`@traced` 装饰器、`current_tracer` ContextVar、`src/rag/stream.py::stream_answer`、`Event.TRACE_READY` / `TRACE_INIT_FAILED` / `TRACE_SKIP`（**`log_events.py` 与 `log_event_specs.py` 两处同名登记**）。

连带项：`stream.py` 移除 `stream_answer` 后只剩 `estimate_usage`，而它仍有**两个活消费者**（`src/agents/graph/agent_node.py:215`、`src/agents/skills/fork_stream.py:327`）。**宿主定为并入 `src/infra/llm/token_usage.py`**（该文件正是 `TokenUsage` 的定义处，`estimate_usage` 与它是同一职责），并入后删除 `rag/stream.py`；同步两个 import 点。备选"保留 `rag/stream.py` 作薄模块"否决 —— 那会让一个已死的模块名长期承载唯一存活的函数，是纯历史包袱。

**连带同步（原先漏了）**：删 `stream_answer` 会与**主规格冲突** —— `docs/openspec/specs/token-usage-model/spec.md:15` 明确要求「`stream_answer()` SHALL map them to `TokenUsage(...)`」。故本变更须以 delta 修改 `token-usage-model`（见 proposal 的 Modified Capabilities），把 usage 映射的载体改为现路径，并顺手修掉同文件里已陈旧的 `generate_node` 场景（该节点全仓已不存在）。`TokenUsage` 自身的 docstring 现写「用于 `end_generation` 的参数传递」，`end_generation` 属于待删的 `LangfuseTracer` —— 一并改掉。

> **为什么先前会漏**：`src/cli/check_docs.py` 只校验 `docs/agents/*.md`，**不扫 `docs/openspec/specs/`**。本次靠人工系统性扫描补上（扫描结果：主 specs 中只有这一处受影响）。给闸门补 openspec specs 的扫描范围**不在本变更内**，已登记为遗留。

### D10 ADR-0011 复查条件②的复评记录

本变更使 Langfuse **首次真正进入请求路径**，触发 ADR-0011 复查条件②。复评结论为**维持 v2 降级**，依据：接线不引 ClickHouse、不新增存储、Langfuse 仍在旁路（D3 的 id 对齐与 D5 的故障隔离保证对话不因 tracing 失败）。记录形式（change design 内记录 / 追加新 ADR）见 Open Questions。

### D11 覆盖边界由"嵌套自动成立"兜底

未显式接线的辅助 LLM（`rewrite_query` / `parse_temporal` / 分类）在物理上位于同一 task 的同一 context 内，**若日后给它们加装饰器会自动挂到正确的 trace 之下**，无需改动根。这让 P2 的扩展成本接近零，也是把 Non-Goals 划在这里的底气。

### D12 与 `turn-provenance-observability` 的次序

两者落点重叠且**不可并行**：

| 落点 | turn-provenance | 本变更 |
|---|---|---|
| `agent_node.agent_model`（温度分档段 `:173-200` + 两个 `astream` 分支 `:184-203`） | 提取单一真源 `temperature` 变量 + 扩 `model turn` 字段 | 装饰闭包 + 回填 generation 字段 |
| `agent_service._run_generation` | 发两条来源声明 status + 双 sink 写入 | 加 trace 根 |
| `log_events.py` / `log_event_specs.py` | 两处登记 5 个新事件 | 两处删除 3 个死事件 |

**已定：本变更先行**，`turn-provenance-observability` 后置，重启时按其落地后的实际代码复核行号与做法。

> **行号勘误**：`turn-provenance-observability/tasks.md` 的 4.1 写「温度分档处（`:158-167`）」—— 该范围实为 `async def agent_model` 的函数头与迭代日志；温度分档段实际是 `:173-200`。本变更已按实际值书写，对方重启时需同步改正（两处同错源于同一次取证）。

### D13 开关的双重语义：一个开关同时管 prompt 远端与 trace 产出（显式登记，不拆）

`LANGFUSE_ENABLE` 在今天管 `prompt_manager` 的远端读取（见 ADR-0010）；本变更后**它同时管 trace 产出**。后果：**一旦日后把 prompt 名单加回 `PROMPT_NAMES`，打开 trace 会连带打开 prompt 远端读取** —— 改本地文案不生效而日志不会提示（正是 ADR-0010 当初出列的理由）。

- **本次不拆开关**（最小改动原则；当前名单为空，耦合无实际影响）。但这是**已知耦合**，须写进 `glossary.md`（随开关一起描述）与本文，避免后人踩时才发现。
- 备选（留待真需要时再做）：拆成 `LANGFUSE_PROMPT_REMOTE` 与 `LANGFUSE_TRACE` 两个开关。**注意**：拆开关属配置契约变更，会影响 `.env` / `.env.template` / 部署文档，不宜顺带做。

### D14 测试策略：**全局关停 tracing**，不用开关决定是否运行

**问题比"一个文件"大得多。** 三件事叠加：

1. `tests/infra/llm/test_langfuse.py:19-21` 用 `pytest.mark.skipif(not LANGFUSE_ENABLE)`，而 `settings.py:307` 的代码默认是 `true` → **在没有 `.env` 的环境（CI / 新 clone / worktree）不会 skip**；
2. `src/config/settings.py:298-307` 里 `LANGFUSE_SECRET_KEY` / `PUBLIC_KEY` **内置了真实默认值**、host 默认 `http://langfuse:3000` → 一旦构造客户端就会真的尝试上报；
3. 接线上后受影响的**不只是那个文件**：凡走 `_run_generation` / `agent_model` 的既有用例（`tests/services/` 一大批）都会构造真实客户端发网络 —— 违反「测试 mock 外部依赖，不发起真实网络调用」。

**做法**：

- **加 autouse 的会话级 fixture 全局关停 tracing**（`langfuse_context.configure(enabled=False)`，或 monkeypatch `LANGFUSE_ENABLE=false`）—— 覆盖全部测试，而不是逐个文件打补丁。
- **明确写入 DoD**：「`pytest` 全绿」必须在 **tracing 被全局关停**的前提下取得，否则这个证据不成立。
- `test_langfuse.py` 按新契约重写（它测的 `LangfuseTracer` 将被删除，且**已经漂移** —— 断言了当前类不存在的 `tracer._initialized`，只是被 skip 掩盖）：断言接线后的契约（根开启、id 传递、generation 字段回填），用替身 / mock，**不依赖 `LANGFUSE_ENABLE` 决定是否运行**。
- 「开关关闭时不产出」这类行为，用**显式 monkeypatch 开关值**来测，而不是靠环境变量碰运气。

### D15 retention 的级联删除须先小规模验证，并预先定好级联失败时的契约

已确认的只有「`client.api.trace.delete_multiple` **方法存在**」。**未确认**：删掉 trace 后 `observations` / `scores` / `dataset_run_items` 是否级联清理、有无孤儿残留、Langfuse UI 打开是否正常。直接上手定时全量删，风险是把库删成"半数表有孤儿"的状态。

**做法**：在计划外部署动作里**第一步先做一次小规模验证** —— 挑少量超期 trace → 删除 → 查上述各表的行数变化与孤儿 → 打开对应 trace 页面确认 UI 不报错。**验证通过才接定时任务**。结论登记进 `tasks.md` 的「实施期修正记录」。

**并且现在就把两种结果都定好，不留到执行期现编**：

| 验证结果 | 契约怎么走 |
|---|---|
| **API 级联**（无孤儿） | 维持 spec 的「SHALL NOT 留下孤儿」；CLI 只调 `delete_multiple` |
| **API 不级联**（有孤儿） | 清理 CLI **必须显式删除附属记录**（`observations` / `scores` 等按 trace_id 清理），spec 的「不留孤儿」要求不变，责任落到实现 |

> 不允许的第三条路：把 spec 改成"允许孤儿"来迁就 API。孤儿会让 Langfuse 的查询与 UI 处于未定义状态，且事后无法区分"数据本来就少"与"清理删坏了"。

### D16 本变更自身的取舍单独写一条 ADR

`docs/adr/README.md` 的「什么时候写 ADR」里，D8 命中三条：**存在多个合理候选**（记原文 / 复用 `LLM_LOG_CONTENT` / 新增独立开关）、**是取舍**（用"内容留存 + 保留期错配风险"换"链路可完整回放"）、**后人可能重新争论**（合规要求出现时必然重问）。

因此除 ADR-0011 复评记录外，**另立一条新 ADR** 记 D8 的取舍（含被否决的两个候选与理由）。与 D10 的复评记录分开：一个是"确认旧决策"，一个是"新决策"。

### D17 prod 侧落地：改模板 ≠ 改到机器，且须先确认后端在跑

两件事都必须在 change 内显式处理，否则"prod 接线"只是纸面：

1. `.env.template` 改了**不代表** prod 机上的 `.env` 改了 —— 该文件在部署机上手工维护，须列为部署动作（与 D7 的定时接入同批）。
2. **须先确认 prod 的 `langfuse-web` 确实在跑**：ADR-0011 记「prod 从未部署过 v3」，降级后「只做静态校验、未运行验证」。若 prod 没有该服务，打开开关就是往一个不存在的 host 发 —— 此时**只剩故障隔离在兜底**（对话不受影响，但 trace 全丢且只有日志能看出来）。验收时须实测一次"后端不可达时对话正常"。

### D18 `.env` 不受版本控制，且在多工作区间共享 —— 开关的启用与回滚是**手工动作**

`LANGFUSE_ENABLE` 的真实取值来自 `.env`，而 `.env` 被 `.gitignore` 忽略：**改 `.env.template` 不会改到任何一台机器上的 `.env`**（prod 如此，dev 亦然）。在 worktree 里还有一层：worktree 的 `.env` 是指向**主工作区** `.env` 的软链（建 worktree 时按 cookbook 建立），所以在 worktree 里"改 `.env`"实际改的是主工作区那份 —— 另一个会话可能正在使用它。

**做法**（写进 Migration 与回滚，不留成隐性知识）：

- 开关的**启用**与**回滚**都是**手工动作**，且要**先记录当前值**再改（便于精确回退）
- 在 worktree 里做这一步前，先确认主工作区是否有会话在用（本仓平时就有并行会话）
- `.env.template` / `.env.example` 的同步**是代码交付**（进 git），`.env` 的改动**不是** —— 两者的验收标准不同，Migration 里分开写
- 若要彻底避免跨工作区影响：在 worktree 放一份独立的 `.env`（代价是两份要各自维护）

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
- **[★ 未验证断言：不止一条]** → 下列都是**纸面契约、未实测**，实施计划须逐条验，**不允许留一条没验过的 scenario**：
  1. **取消路径** —— 「生成被取消时 trace 不被丢失」。取消走的是 `abort_signal` 置位后 `_run_generation` 自抛 `CancelledError`（**不是** `task.cancel()`）；`@observe` 的 `except Exception` 不捕获 `BaseException`，但 `finally` 会 end observation → **从代码看契约大概率成立**，仍需实测（造一次取消 → 查 Langfuse）
  2. **id 字符集** —— `trace_<uuid>` / `eval_<hex>` 能否被 v2 **服务端**接受（客户端层已验，服务端未验）。与 D3 的入站校验同源：若某些字符被拒，白名单要与之对齐
  3. **嵌套自动成立** —— D11 断言「未装饰的辅助 LLM / fork 子代理会自动挂到正确 trace 之下」，这是"免费兜底"的底气，但**没跑过图验证**
  4. **级联与 UI** —— `delete_multiple` 的级联行为与清理后 UI 正常性（D15 已列为部署动作的前置）
- **[★ 测试基线的雷]** → D14：tracing 一旦接线，**所有**走 `_run_generation` / `agent_model` 的既有用例都会真发网络（且 `settings.py` 内置真实 key 默认值）。必须先加**全局关停 fixture**，否则"pytest 全绿"本身不可信 —— 而它是 ①–④ 之外所有验收的前提。
- **[入站 id 成为持久主键]** → D3 的白名单是**新增的安全边界**；若不实现，非法字符会导致 trace 静默丢失、任意 id 可注入。验收须包含"非法 `X-Trace-ID` 被拒并用服务端生成值跑通四方对齐"。

## Migration Plan

1. **接线（不改变开关状态）**：先以 `LANGFUSE_ENABLE=false` 在本地把链路接完，确认关闭态下对话行为零变化 —— 这是一个"代码已接、行为未变"的可交付中间态。**开关接线要同时覆盖 `main.py` lifespan 与 `cli/eval_ragas.py`**（D5）。
2. **重定测试**：重写 `tests/infra/llm/test_langfuse.py`（替身 / mock，不再以开关决定是否运行）—— 必须在"pytest 全绿"被当作证据之前完成（D14）。
3. **打开开关**：把 `.env` / `.env.template` 改为 `true`，在 dev 上跑一轮真实对话，核对四方 id 对齐（响应头 / 日志 / SSE done / Langfuse UI）。**同批验证两件未实测的事**：① 取消一轮生成后 trace 是否仍在（Risks 首条）；② 把后端停掉后对话是否仍正常完成（故障隔离，D17）。
4. **补清理机制**：先按 D15 做**小规模级联验证**（删 → 查 `observations` / `scores` / `dataset_run_items` 孤儿 → 看 UI），通过后再接定时落地与 dry-run；**顺序不可颠倒**。
5. **清死代码**：确认无引用后删 `LangfuseTracer` 一整套与三个事件。
6. **文档与 ADR**：同步 `docs/agents/`（**含 `glossary.md:207` 与 `code-map.md`**，以及 `cookbook.md:315` 那段）与 `src/cli/README.md`；落 **D16 的新 ADR** 与 **D10 的复评记录**。
7. **prod 落地**（与第 4 步的定时接入同批）：同步 prod 机上的 `.env`（改模板不等于改到机器）；确认 `langfuse-web` 在跑，否则按 D17 走故障隔离验收。

**回滚**：把 `LANGFUSE_ENABLE` 置回 `false` 即恢复"零 trace 产出"（接线代码保留但空转）；如需完全回退，`git revert` 本变更的提交即可，Langfuse 侧无 schema 变更、无需数据回滚。**注意 CLI 侧**同样要受该开关控制（D5），否则回滚不彻底。

## Open Questions

1. **辅助 LLM 与 fork 子代理是否纳入** —— 当前划为 Non-Goals（D11）；若排查时发现"缺了这两块就看不明白一轮对话"，再提前。
2. **清理的定时落地形态** —— 宿主 cron、systemd timer、还是 compose 定时服务；prod（2 台机）与 dev（WSL）是否需要不同方案。
3. ~~`estimate_usage` 的最终宿主~~ —— **已定：并入 `src/infra/llm/token_usage.py`，删除 `rag/stream.py`**（见 D9）。
4. **D10 复评记录的形式** —— 写在 change design 内即可，还是按惯例追加一条新 ADR（本情形属"**确认**"而非"推翻"，倾向不追加；与 D16 那条**新决策** ADR 是两件事，不要合并）。
5. **开关是否拆成两个** —— 见 D13。当前不拆；若日后 prompt 名单加回，须重新评估，因为届时"开 trace 会连带开 prompt 远端"。
6. **入站 trace id 白名单的最终字符集** —— D3 给了建议值 `^[A-Za-z0-9_-]{1,120}$`；最终须与验证项 2（v2 服务端实际接受的字符集）对齐后再钉死，**不能只按客户端层结论定**。
7. **是否给文档闸门补 openspec specs 的扫描范围** —— `src/cli/check_docs.py` 今天不扫 `docs/openspec/specs/`，主规格的失效引用没有机械闸门（本次靠人工扫出）。**不在本变更内**，登记为遗留。
