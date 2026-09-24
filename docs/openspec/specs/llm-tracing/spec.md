# llm-tracing Specification

## Purpose
应用层接入 Langfuse 的追踪契约：一轮对话与一条 trace 的一一对应及 id 四方对齐（响应头 / 日志 / SSE `done` / Langfuse）、trace 根的落点与生命周期、主 agent 每轮推理的 generation 字段与用量口径、开关语义与故障隔离、trace 的保留与清理，以及 trace 内容的记录范围与边界。

## Requirements
### Requirement: 一轮生成一个 trace，id 与请求 trace_id 一致

应用层 SHALL 为每一轮对话生成恰好一个 Langfuse trace。该 trace 的 id SHALL 与请求级 `trace_id`（形如 `trace_<uuid>`，由 `X-Trace-ID` 请求头或查询参数决定，缺失时自动生成）**逐字相同**，使日志行、响应头、SSE `done` 事件与 Langfuse 四方指向同一条链路。

**入站 id 是不可信输入**：`X-Trace-ID` / `?trace_id` 完全由客户端提供。系统 SHALL 在使用前做字符集与长度白名单校验；**不合法时 SHALL 静默丢弃该值并服务端重新生成**（与缺失时的行为一致），**SHALL NOT 因此拒绝请求**。重新生成的值成为四方一致的那个 id —— 即"对齐"要求不受影响，被拒的只是调用方指定的那个字符串。

校验与重生成 SHALL 在 **id 被写入请求上下文之前**完成（即任何下游读取该 id 之前），使响应头、日志、SSE 事件与 trace 从源头取到同一个值。若在下游重生成，响应头会停留在未校验的旧值，四方分叉。

#### Scenario: 正常一轮对话的 id 对齐

- **WHEN** 客户端发起一轮对话并收到完整 SSE 响应
- **THEN** Langfuse 中存在且仅存在一条 trace，其 id 等于响应头 `X-Trace-ID` 的值
- **AND** 该 id 同时等于本轮全部日志行注入的 `trace_id`，以及 SSE `done` 事件携带的 `trace_id`

#### Scenario: 由日志反查 Langfuse

- **WHEN** 从日志中取得某个 `trace_<uuid>`
- **THEN** 可在 Langfuse 中按该 id 检索到对应的 trace

#### Scenario: 非法入站 id 被拒且不污染观测库

- **WHEN** 请求携带含非法字符或超长的 `X-Trace-ID`（例如带空格、斜杠、或超长串）
- **THEN** 该值 SHALL NOT 成为 Langfuse 的 trace id（既不写入也不报错失败）
- **AND** 服务端重新生成一个合法 id，响应的 `X-Trace-ID`、日志行与该 trace 的 id 三者仍一致
- **AND** 请求本身**照常成功处理**（不因 id 非法而返回错误状态）

#### Scenario: 校验发生在 id 生效之前

- **WHEN** 请求携带既非空、又合法的 `X-Trace-ID`
- **THEN** 响应头 `X-Trace-ID`、该请求全部日志行的 `trace_id`、SSE `done` 事件的 `trace_id` 与 Langfuse 中那条 trace 的 id **四者同为该值**

> 这条专门锁住"校验/重生成的位置"：若在下游才重生成，响应头会与其余三处分叉，本断言即失败。

#### Scenario: CLI 评测链路的 id 对齐

- **WHEN** 运行 `eval_ragas` 评估并产出 CSV
- **THEN** CSV 中每个问题的 `trace_id`（形如 `eval_<hex>`）各自对应 Langfuse 中的一条 trace
- **AND** 该 id 与评估期间该问题全部日志行的 `trace_id` 一致

### Requirement: trace 根落在后台生成任务内

trace 根 SHALL 覆盖"准备就绪后的整轮生成"（图事件循环的完整生命周期），且 SHALL 在承载生成的那个 asyncio task 内部开启与结束。SSE 订阅与推送侧 SHALL NOT 单独产生 trace。

#### Scenario: 根的生命周期覆盖整轮生成

- **WHEN** 一轮生成包含多轮 agent 迭代与工具调用
- **THEN** 这些迭代与工具调用产出的 observation 均隶属于同一条 trace 之下

#### Scenario: 生成被取消时 trace 不被丢失

- **WHEN** 用户在生成过程中触发取消
- **THEN** 已产生的 observation 正常上报，该 trace 仍可在 Langfuse 中查到

### Requirement: 主 agent 每轮推理记为 generation

主 agent 的每一次 LLM 推理（图循环内每次模型调用）SHALL 产出一条 generation 类型的 observation，SHALL 至少记录模型名、输入消息、输出文本、token 用量与首 token 时间。

#### Scenario: 单轮迭代的 generation 字段

- **WHEN** 主 agent 完成一次模型调用
- **THEN** 对应 generation 携带模型名、输入消息、输出文本与首 token 到达时间
- **AND** token 用量取流聚合后的真实计数

#### Scenario: 用量缺失时回落估算

- **WHEN** 模型响应未返回 token 用量元数据
- **THEN** generation 的用量 SHALL 使用文本长度估算值，并以可识别的方式标注该值为估算（例如写入 metadata）

#### Scenario: 多轮迭代各成一条

- **WHEN** 一轮对话内主 agent 迭代两次
- **THEN** 该 trace 下存在两条 generation，各自对应一次模型调用

### Requirement: 开关语义与故障隔离

`LANGFUSE_ENABLE` SHALL 作为应用侧 trace 产出的总开关，默认值为 `true`，且 SHALL 保留置为 `false` 时完全不产出 trace 的能力。无论开关取值或 Langfuse 后端是否可达，对话功能 SHALL NOT 因 tracing 受到影响。

#### Scenario: 开关关闭时不产出

- **WHEN** `LANGFUSE_ENABLE=false` 且应用完成一轮对话
- **THEN** Langfuse 侧不新增任何 trace
- **AND** 对话正常完成，SSE 事件序列与开启时一致

#### Scenario: CLI 链路同样受控

- **WHEN** 以 `LANGFUSE_ENABLE=false` 运行 `eval_ragas` 评估
- **THEN** Langfuse 侧不新增任何 trace
- **AND** 评估流程照常完成并产出 CSV（不因开关关闭而中断）

#### Scenario: 后端不可达时对话不受影响

- **WHEN** `LANGFUSE_ENABLE=true` 但 Langfuse 后端不可达
- **THEN** 对话仍正常完成，不产生面向用户的错误
- **AND** 故障仅以日志形式暴露

#### Scenario: 缺省配置采用默认开关

- **WHEN** 环境变量未显式提供 `LANGFUSE_ENABLE`
- **THEN** 应用按 `true` 处理

### Requirement: trace 保留与清理

系统 SHALL 提供可重复执行的 trace 清理入口，删除创建时间早于保留期的 trace 及其附属数据，**且 SHALL NOT 留下孤儿附属记录**（越期 trace 对应的 observation / score 等一并消失）。保留期 SHALL 为 30 天，且 SHALL 可通过命令行参数覆盖。清理 SHALL 支持仅预览不删除（dry-run）。

**这是不可逆的破坏性操作，入口 SHALL 带运行期护栏**（阈值见下）：

- 保留期参数 SHALL 有**下界 = 1 天**：低于 1 天直接拒绝，不得执行任何删除
- 非 dry-run 的执行 SHALL 要求**显式确认标志 `--yes`**：未提供即拒绝执行（SHALL NOT 使用交互式输入 —— 定时任务场景无 TTY）
- SHALL 有**单次删除数量上限 = 1000 条**：超过即中止且**不做任何删除**，提示分批
- SHALL 输出**审计**信息（将被删 trace 的数量与标识写入输出或日志，事后可复盘删了什么）
- SHALL 约束**执行环境**（仅允许在指定机器 / 环境变量下执行，防误连另一环境的库）

#### Scenario: 超期数据被删除

- **WHEN** 执行清理且 Langfuse 中存在创建时间早于保留期的 trace
- **THEN** 这些 trace 及其附属 observation 被删除
- **AND** 删除后不存在指向已删 trace 的孤儿附属记录

#### Scenario: 清理后 UI 可正常浏览

- **WHEN** 清理执行完毕后在 Langfuse UI 浏览剩余的 trace 列表与详情
- **THEN** 页面正常渲染，无报错

#### Scenario: 保留期内数据不受影响

- **WHEN** 执行清理且存在创建时间在保留期内的 trace
- **THEN** 这些 trace 保持不变

#### Scenario: 预览模式不删除

- **WHEN** 以 dry-run 模式执行清理
- **THEN** 仅输出将被删除的 trace 数量与标识，不执行任何删除

#### Scenario: 重复执行安全

- **WHEN** 在无超期数据的情况下再次执行清理
- **THEN** 命令正常结束且不报错

#### Scenario: 越界的保留期被拒绝

- **WHEN** 传入低于下界的保留期（例如 0 天或负数）
- **THEN** 命令**拒绝执行并报错退出**，不删除任何 trace

#### Scenario: 未确认时不删除

- **WHEN** 以非 dry-run 执行但未提供显式确认
- **THEN** 命令不执行删除（提示需要确认），退出码非 0

#### Scenario: 超过单次上限时中止

- **WHEN** 待删 trace 数量超过单次上限
- **THEN** 命令中止且**不做任何删除**，提示需要分批执行

#### Scenario: 删除留有审计

- **WHEN** 一次真删执行完成
- **THEN** 输出或日志中可找到本次被删 trace 的数量与标识

### Requirement: trace 内容记录范围

trace 的 generation SHALL 记录该次 LLM 调用的输入与输出原文（含 system prompt、检索上下文与回答），使链路可完整回放。记录输入 SHALL 采用**显式写入**方式；被装饰函数的**内部运行时对象 SHALL NOT 进入 trace**（如请求上下文对象、流式管理器、编译后的图、事件与队列等）。

该行为 SHALL 在文档中作为已知事实登记，并说明 trace 的保留期可能与对话记录的保留期不一致。

#### Scenario: 原文可回放

- **WHEN** 在 Langfuse 中打开某条 trace 的主 agent generation
- **THEN** 可看到该次调用的完整输入消息与输出文本

#### Scenario: 内部运行时对象不进 trace

- **WHEN** 检查某条 trace 的根 observation 或其 generation 的输入字段
- **THEN** 其中只包含该记的业务输入（查询文本 / 消息列表等）
- **AND** 不出现请求上下文对象、流式管理器、编译后的图、事件或队列等内部对象的序列化结果

#### Scenario: 记录范围有明确文档说明

- **WHEN** 查阅 trace 相关文档
- **THEN** 可读到"trace 记录 prompt 与回答原文"及其保留期，无需从代码反推
