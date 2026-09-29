# agent-assembly Specification

## Purpose
唯一 agent 装配入口的契约：主 agent 循环与 fork 子代理由同一装配工厂生成，角色差异只由装配参数表达，运行态字段的传递与回合上限的归属在此定案。
## Requirements

### Requirement: 主/子角色共用同一 agent 装配

系统 SHALL 提供**唯一**的 agent 装配入口（装配工厂），主 agent 的循环与 fork 子代理 SHALL 都经由它生成；SHALL NOT 存在第二套等价的循环装配实现。装配入口 SHALL 接受模型、工具面、system 提供方式、回合上限与 middleware 集合作为参数，并返回可直接执行/嵌入的编译产物。

角色名参数**本变更不启用**：启用会给事件 metadata 增加 `lc_agent_name`，属可观测元数据变更，不在"行为保持"的重构范围内。

#### Scenario: 两角色同源

- **WHEN** 主 agent 的循环与一个 fork 子代理被生成
- **THEN** 二者 SHALL 由同一装配入口生成，且循环本体实现相同

#### Scenario: 三条入口同源

- **WHEN** 审查 agent 的全部生成路径
- **THEN** 主循环、`delegate_task` 委派子代理与 `/xxx` 直出子代理 SHALL 都经由同一装配入口生成（直出与委派共用同一子代理构建路径）

#### Scenario: 不存在第二套装配

- **WHEN** 审查 `src/agents/` 下的 agent 生成代码
- **THEN** SHALL NOT 出现绕过装配入口、自行拼接循环节点与工具节点的第二套实现
- **AND** 该约束 SHALL 由一条静态扫描断言测试守住（`src/agents/` 下对 `create_agent(` 的调用只允许出现在装配入口文件）

### Requirement: 装配差异仅由参数提供

两角色的差异 SHALL 全部由装配参数表达——至少包括：**system 提供方式**（静态串 / 经运行态携带）、**工具面**、**回合上限**（仅主角色生效，见下条）、**middleware 集合**。装配入口 SHALL NOT 通过角色分支（`if role == ...`）内联两套行为。

两角色 SHALL 共用同一图状态 schema（未使用的键保留默认值），SHALL NOT 为角色各建一套 schema——否则"唯一装配"会退化为两套。

#### Scenario: 子角色用静态 system

- **WHEN** 生成 fork 子代理
- **THEN** 其 system 由静态串提供（执行者人设 + 执行契约），无需 prompt middleware

#### Scenario: 子角色不装配观测与预算 middleware

- **WHEN** 生成 fork 子代理
- **THEN** 其 middleware 集合 SHALL 为空——子代理的模型轮次记录由 fork 委派的事件消费侧承担，图内 SHALL NOT 重复产出主循环口径的轮次日志与模型观测

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

### Requirement: 装配后图内工具与中间件仍能取到必需的运行态字段

主循环改由装配入口产出后，图内工具与 middleware 的注入状态**形状随之改变**（外层自建图给 `AgentState` 实例，`create_agent` 给映射）。装配 SHALL 保证二者在**两条承载下都能取到其运行所需字段**：

- 装配 SHALL 把必需字段**带入图状态**（至少：`kb_id`、本轮 `query`、主循环迭代序号、`deep_thinking`），取值来自外层 `AgentState`。**未传入的键在子图状态里根本不存在**（实测：未 seed 时状态只有 `messages` 一个键；schema 上的默认值**不会**被填充）⇒ 漏传即等于该字段**没有值**，而不是取到默认值。
- **`RequestContext` 只能作补充、不得作前提**：`kb_id` 与 `deep_thinking` 在 ctx 里虽有对应项，但存在**不建 ctx 的入口**（CLI 评估脚本只把参数放进图输入）⇒ 此时 ctx 回退**落空**，故这些字段仍 SHALL 由装配带入；`query` 与迭代序号则**只**在图状态里可得。
- 工具侧 SHALL 以**显式形状判定**取数（不得假定 dataclass 或映射之一），`CLAUDE.md` 的「显式类型检查」规则适用于此；**middleware 侧 SHALL NOT 使用属性访问**（实测 middleware 收到的是映射，属性访问抛 `AttributeError`）。
- 字段缺失走降级分支时 SHALL 记 warning（不得静默取空——那会让"检索恒空"这类故障在日志上看不出来）。

#### Scenario: 主循环内工具取到真实 kb_id

- **WHEN** 主循环经装配入口运行，并在图内调用 `retrieve_kb`
- **THEN** SHALL 取到真实 `kb_id`（由带入图状态的值提供，或由请求上下文回退），SHALL NOT 落在降级分支

#### Scenario: 无请求上下文的入口同样能取到字段

- **WHEN** 入口**不建立** `RequestContext`（如 CLI 评估脚本，只把参数放进图输入）时经装配入口运行主循环
- **THEN** 图内工具与 middleware SHALL 仍取到真实 `kb_id` 与 `deep_thinking`（由装配带入），SHALL NOT 因缺少 ctx 而退化为空值/关闭思考

#### Scenario: 主循环内工具取到真实 query

- **WHEN** 主循环经装配入口运行，并在图内调用 `ask_user`
- **THEN** SHALL 取到本轮真实 `query`（该值不在请求上下文中，只能由装配带入图状态），SHALL NOT 为空串

#### Scenario: 主循环内工具取到真实迭代序号

- **WHEN** 主循环经装配入口运行，并在第 N 轮（N > 0）调用 `retrieve_kb`
- **THEN** 该工具读到的迭代序号 SHALL 为真实值，SHALL NOT 恒为 0

#### Scenario: 字段缺失时显式降级并留痕

- **WHEN** 图状态与请求上下文都取不到某工具需要的字段
- **THEN** 工具 SHALL 走显式判定的降级分支（按缺失处理），SHALL NOT 抛异常
- **AND** SHALL 记一条 warning（含工具名与缺失字段名）

#### Scenario: 生产链路端到端覆盖

- **WHEN** 验收本要求
- **THEN** 断言 SHALL 覆盖「生产节点 → 子图 → 工具/中间件」的完整链路，SHALL NOT 用测试中手工塞入的 state 替代

### Requirement: 回合上限由装配参数决定并可动态放宽

回合上限 SHALL 作为装配参数传入，并由装配产出的预算 middleware 强制。**本条只适用于主角色**：子角色的轮次上限**取值与归属见 `delegate-execution-controls` 的「fork turn 上限」**（本变更不改其机制）；装配入口对子角色 SHALL NOT 装配回合预算 middleware——否则停止会提前到 `jump_to: end`，使原有的 turn 超限停止原因不再触发。

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
