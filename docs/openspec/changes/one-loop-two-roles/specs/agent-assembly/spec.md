# agent-assembly Specification

## ADDED Requirements

### Requirement: 主/子角色共用同一 agent 装配

系统 SHALL 提供**唯一**的 agent 装配入口（装配工厂），主 agent 的循环与 fork 子代理 SHALL 都经由它生成；SHALL NOT 存在第二套等价的循环装配实现。装配入口 SHALL 接受模型、工具面、system 提供方式、角色名、回合上限与 middleware 集合作为参数，并返回可直接执行/嵌入的编译产物。

#### Scenario: 两角色同源

- **WHEN** 主 agent 的循环与一个 fork 子代理被生成
- **THEN** 二者 SHALL 由同一装配入口生成，且循环本体实现相同

#### Scenario: 不存在第二套装配

- **WHEN** 审查 `src/agents/` 下的 agent 生成代码
- **THEN** SHALL NOT 出现绕过装配入口、自行拼接循环节点与工具节点的第二套实现

### Requirement: 装配差异仅由参数提供

两角色的差异 SHALL 全部由装配参数表达——至少包括：**system 提供方式**（静态串 / 经运行态携带）、**工具面**、**回合上限**、**middleware 集合**、**角色名**。装配入口 SHALL NOT 通过角色分支（`if role == ...`）内联两套行为。

#### Scenario: 子角色用静态 system

- **WHEN** 生成 fork 子代理
- **THEN** 其 system 由静态串提供（执行者人设 + 执行契约），无需 prompt middleware

#### Scenario: 主角色用运行态 system

- **WHEN** 生成主 agent 循环
- **THEN** 其 system 由运行态携带（含未绑定 KB 时的第二条 system 消息），由 prompt middleware 施加

### Requirement: 装配入口不产出编译日志

装配入口 SHALL NOT 产出图编译日志（`graph compiled` 语义的事件）；该事件 SHALL 只由图装配层产出一次。理由：子角色每次委派都会调用装配入口，若它也产出编译日志，每次委派都会多出一条与"图已就绪"语义无关的日志。

#### Scenario: 子代理装配不产生编译日志

- **WHEN** 一次委派触发生成 fork 子代理
- **THEN** SHALL NOT 新增 `graph compiled` 事件

### Requirement: middleware 不得持有 per-request 实例状态

装配入口生成的 middleware 实例 SHALL 在进程内**跨请求共享**（随图构造一次）。因此 middleware SHALL NOT 在实例属性上保存任何 per-request 状态（计数、本轮参数、缓冲区等）；per-request 数据 SHALL 只经**运行态 schema** 或请求上下文传递。

#### Scenario: 并发请求不串号

- **WHEN** 两个请求并发使用同一 middleware 实例
- **THEN** 各自的本轮计数与参数 SHALL 相互独立，不出现跨请求污染

### Requirement: 工具取图状态 MUST 与图实现无关

工具经图状态注入（`langgraph.prebuilt.InjectedState`）拿到的值，其**具体形状由承载它的图实现决定**——外层自建图给 `dataclass` 实例，`create_agent` 给 `dict`。因此工具 SHALL NOT 无条件假定该值为某一种形状：既不得只做属性访问，也不得只做下标访问。

工具 SHALL 以**显式形状判定**取数（先判 `isinstance(state, dict)`，再走对应通道；两条路径都要有取值行为），SHALL NOT 使用 `getattr(..., default)` 之类的隐式兜底。

装配产出的运行链路 SHALL 把**工具运行所需的字段**（至少会话的 `kb_id`、本轮 `query`、迭代序号）传入图状态——由调用方在**构造图输入**时 seed；SHALL NOT 让工具只能从图状态之外取这些值。未传字段时工具会走"显式降级"分支，症状是**静默取空**（检索恒空、澄清恒失败），与形状不兼容同样危险。

**理由**：本仓工具现以属性访问取 `kb_id` / `query` / 迭代序号；主循环改由 `create_agent` 承载后，该访问方式会抛 `AttributeError`，且会被工具节点的错误回喂吞成普通工具错误——表现为"检索恒空、澄清恒失败"而图照常跑完，日志上看不出来（2026-09-28 实测复现）。同理，fork 子代理一旦拿到工具面（默认继承规则）也会踩同一条。

#### Scenario: 两种图实现下取数一致

- **WHEN** 同一工具在主循环（`create_agent`）与子代理路径下被调用，且图状态同时含有该字段
- **THEN** 工具 SHALL 在两种形态下都取到相同的值，SHALL NOT 出现 `AttributeError`

#### Scenario: 字段缺失时显式降级

- **WHEN** 注入的图状态中不含该工具需要的字段
- **THEN** 工具 SHALL 走显式判定的降级分支（按缺失处理），SHALL NOT 抛异常

#### Scenario: 生产链路端到端携带字段

- **WHEN** 主循环经装配入口运行，并在图内调用 `retrieve_kb` / `ask_user`
- **THEN** 这两个工具 SHALL 取到**真实的** `kb_id` / `query`（SHALL NOT 落在降级分支）
- **AND** 该断言 SHALL 覆盖"生产节点 → 子图 → 工具"的完整链路，SHALL NOT 用测试中手工塞入的 state 替代

#### Scenario: 覆盖断言存在

- **WHEN** 工具取数逻辑或装配入口的字段传递被修改
- **THEN** SHALL 存在至少一条在 `create_agent` 承载下断言取数正确的测试（含 `retrieve_kb` 与 `ask_user`）

### Requirement: 回合上限由装配参数决定并可动态放宽

回合上限 SHALL 作为装配参数传入，并由装配产出的预算 middleware 强制。

「已发生委派」的判据 SHALL 同时覆盖**本轮**与**此前轮次**：本轮模型调用声明了委派工具时 SHALL 立即置位该标志（置位 SHALL 先于本次上限判定），此前任一轮置位过则 SHALL 保持。有效上限 = 基础上限 + 固定增量（原 `MAX_DELEGATE_BONUS` 语义）。

上限命中时的行为见 `agent-loop-observability` 的「循环回合上限命中时的消息状态」。

#### Scenario: 触顶后停止且不注入消息

- **WHEN** 回合数达到上限且最后一次模型调用声明了工具调用
- **THEN** 循环 SHALL 停止且不执行该工具调用，也 SHALL NOT 注入任何提示性消息

#### Scenario: 委派轮放宽上限

- **WHEN** 本轮模型调用声明了委派工具调用
- **THEN** 有效上限 SHALL 放宽固定增量，使主循环有余量整合委派结果

#### Scenario: 上限同轮声明的委派仍被执行

- **WHEN** 基础上限已用满，且该轮模型调用声明了委派工具调用
- **THEN** 该轮的有效上限 SHALL 立即按放宽后的值计算，委派工具 SHALL 被执行（SHALL NOT 因未及时放宽而被跳过）

#### Scenario: 委派后的余量在后续轮次保持

- **WHEN** 第 N 轮发生过委派，第 N+1 轮的模型调用不再声明委派
- **THEN** 有效上限 SHALL 仍为放宽后的值（余量不因本轮未声明委派而回落）
