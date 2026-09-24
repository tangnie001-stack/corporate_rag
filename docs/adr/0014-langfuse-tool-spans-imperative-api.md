# ADR-0014：工具 span 用命令式 Langfuse client 创建，client 实例统一取 decorators 单例

- **Status**：Accepted
- **Date**：2026-09-24
- **Deciders**：用户（决策）；Claude（调研、实测与评审）
- **关系**：为 ADR-0012 / ADR-0013 所依附的 `llm-tracing` 能力**扩展观测面**；与既有「纯装饰器」接入方式并存，不取代任何在先 ADR。本 ADR 新增的 tool span 与 trace 富化字段都是同一 trace 上的普通 observation，落在 ADR-0012 / ADR-0013 已定的保留策略（30 天保留期 + 直连 Postgres 清理）之内，本 ADR 不改变任何保留行为。

## 背景与问题

`llm-tracing` 原定「只用 `@observe` 装饰器接入」。要给**工具调用**建 span 时该路线不够用，实测结论：

| 事实 | 影响 |
|---|---|
| `@observe` 的 `as_type` 只接受 `"generation"`；span 是「有 parent 时的默认类型」 | 无法显式声明 span |
| 没有 `start_as_current_span` 之类的上下文管理器 | **拿不到 start/end 分离的 span** |
| 顶层（无 parent）的 `@observe()`（非 generation）会走 `client.trace(**params)` | 在图节点外这样用会 upsert / 改写 trace |

而工具事件是**成对到达**的（`on_tool_start` / `on_tool_end`），必须在两个事件之间持有同一条 span。

## 决策

1. **工具 span 用命令式 API 创建**：`langfuse_context.client_instance.span(trace_id=…, parent_observation_id=…, …)` → `StatefulSpanClient.end(…)`。
2. **client 实例统一取 `langfuse_context.client_instance`**，**禁止** `new Langfuse()` —— 后者不共享 `LangfuseSingleton`，会绕过 `LANGFUSE_ENABLE` 开关、也不被关停时的 `flush_tracing()` 覆盖（后果：禁用态仍可能出网、关停丢 span）。
3. **不调用 `client.trace(...)`**：SDK 会随请求带上 `timestamp = now`，服务端对该列**无条件覆盖**；改用 `Langfuse.span(trace_id=…)` 后，工具 span 路径**不触碰 trace 行**。
   - ⚠️ 这不等于「trace 的 timestamp 漂移被根除」：`update_current_trace(...)`（既有 generation 路径，每轮一次）**内部就走 `client_instance.trace(id=…)`**，仍在刷新该列 —— 那是**既有行为**，本 ADR 不改变。

## 备选与否决理由

| 备选 | 否决理由 |
|---|---|
| 逐工具加 `@observe` 装饰器 | 要为每个工具单独埋点；MCP 适配器产出的工具**加不上**；且工具在 trace 外被调用会产生游离 trace |
| 在 `tools` 节点函数上包一层 `@observe` | 粒度退化为「一轮一条」，丢掉逐工具耗时与并行区分 |
| `langfuse.callback.CallbackHandler` | 已实测不可用（SDK 2.60.10 依赖 langchain 1.x 已删除的模块，import 即失败） |

## 后果

**正面**：工具调用可点、可读、有耗时；观测代码对工具实现无感 —— 新增工具（含 MCP）经统一入口进 ToolNode 即自动覆盖，不需要改观测代码。

**负面 / 接受的代价**：同一份 trace 上出现两种接入方式（装饰器 + 命令式），需要一份口径说明（本条）与统一的 flush / 禁用语义。

**不解决的问题**：
- fork 子代理的工具调用（其回调传播被 `src/agents/skills/executor.py` 的 `var_child_runnable_config.set(None)` 显式切断，事件不进主图流）
- 工具**内部**子步骤（embed / 检索 / rerank）—— 它们不是 LangChain Runnable，事件流里没有它们的事件
- trace 的 `timestamp` 仍会被 `update_current_trace` 每轮刷新（既有行为）

## 复查触发条件

- Langfuse 升级到 v3+（届时命令式 API 与装饰器 API 都可能变）
- 需要覆盖 fork 子代理的工具观测（届时要在子代理事件流上再挂一个消费者，并单独裁定其 trace 归属）
- `langfuse` SDK 版本变更导致 `Langfuse.span(trace_id=…)` 签名或语义变化
