# clarification-interaction Specification

## Purpose
TBD - created by syncing change agentic-clarification. Update Purpose after sync.
## Requirements

### Requirement: ask_user 工具化澄清

系统 SHALL 提供 `ask_user` 工具（LangChain @tool），注册在 agent 循环的工具集中；模型在推理过程中需要补充信息（缺失实体/需要确认）时自主调用该工具。工具参数含 `questions` 数组，每个元素携带 `id`、`question`、`dimension`（company/period/metric 或 free）、`multi_select`（可选）。工具调用后系统 SHALL 将问题通过 SSE 事件推送前端，并阻塞等待用户答案；用户答案回传后作为工具结果（ToolMessage）回喂，agent 在同一 turn 内继续推理，不得重开流程。

#### Scenario: 模型缺实体时主动询问
- **WHEN** agent 推理判断查询缺失关键实体（如公司/期间），且无法从上下文推断
- **THEN** 模型调用 ask_user 工具，系统推送问题卡片给前端并等待答案；用户回答后同一 turn 继续

#### Scenario: 答案回喂同 turn 继续
- **WHEN** 用户提交澄清答案
- **THEN** 答案作为工具结果进入模型上下文，agent 基于完整信息继续检索与生成，最终输出经同一 SSE 流送达

### Requirement: KB 注入候选选项

系统 SHALL 在 ask_user 工具按 `dimension` 查询知识库聚合候选实体（`aggregate_kb_entities`）填充 `options`（label=真实候选，description=附加信息）；无候选时 fallback 静态 `SUGGESTIONS_MAP`；模型不生成候选选项，仅负责问题措辞与询问时机。

#### Scenario: 公司维度选项来自 KB
- **WHEN** ask_user 的 dimension 为 company 且 KB 聚合返回候选公司列表
- **THEN** 选项列表为 KB 真实存在的公司，用户选择后检索必有数据

#### Scenario: 无候选时静态兜底
- **WHEN** KB 聚合无候选且 dimension 无对应 SUGGESTIONS_MAP 项
- **THEN** 选项为空，仅提供自由文本输入

### Requirement: 结构化答案契约

系统 SHALL 定义澄清答案结构 `{answers: [{id, selected: list[str], custom: str|None}]}`；单选中 custom 覆盖 selected，多选中 custom 补充 selected。答案经 `POST /api/chat/clarify-answer` 回传，系统 SHALL 将答案转为工具结果（ToolMessage JSON 文本）回喂模型，并将用户答案作为 user 消息**到达即写**会话历史（Redis），供前端展示与后续轮次上下文。

#### Scenario: 单选选项提交
- **WHEN** 用户在单问题单选项卡片选择"东软集团"
- **THEN** 答案结构为 `{answers: [{id, selected: ["东软集团"]}]}`，且该文本落入会话历史

#### Scenario: 自由文本提交
- **WHEN** 用户直接输入"2024年第一季度"
- **THEN** 答案结构为 `{answers: [{id, selected: [], custom: "2024年第一季度"}]}`

### Requirement: 引用编号全局递增

系统 SHALL 在 `retrieve_kb` 多次调用时采用全局递增编号：每次调用 `offset = len(state.tool_contexts)`，返回文本编号 `[offset+1 .. offset+N]` 并追加 RAGContext；工具通过 InjectedState 读写 state。`format_node` 从答案提取 `[n]` 后 SHALL 校验 `1<=n<=len(tool_contexts)` 再映射，跨轮编号不冲突、跨 turn 旧编号被过滤。

#### Scenario: 多轮检索编号不冲突
- **WHEN** agent 循环先后两次调用 retrieve_kb，各返回 5 条
- **THEN** 第一轮编号 [1..5]、第二轮 [6..10]，`format_node` 可无歧义映射

#### Scenario: 跨 turn 编号失效
- **WHEN** 模型引用上一 turn 的编号 [3]
- **THEN** 因 `tool_contexts` 每请求重建，[3] 超出当前范围被 valid_numbers 过滤，不产生错位引用

### Requirement: 历史注入窗口

系统 SHALL 为 agent 循环历史注入采用"最近 N 轮 + token 双上限"：保留最近 N 轮（默认 10）user/assistant 文本；历史总 token 超过 context 窗口 30% 时从最旧截断；最近 1 轮完整注入。滚动摘要列为后续增强。

#### Scenario: 话题漂移上下文保留
- **WHEN** 用户 A→B→A 话题漂移，A 在最近 N 轮窗口内
- **THEN** A 的上下文保留在注入历史中，回到 A 时模型可继续

#### Scenario: 超窗截断
- **WHEN** 历史轮数超过 N 或总 token 超阈值
- **THEN** 从最旧截断，保留最近 N 轮与最近 1 轮完整内容

### Requirement: 澄清触发与频率护栏

系统 SHALL 将澄清触发从 classify 预判移除（classify 节点删除）。系统 SHALL 限制单 turn 内 ask_user 调用次数（`MAX_ASK_PER_TURN`，默认 2），并对重复问题去重；计数 SHALL 存于 AgentState（`_ask_count`，工具经 InjectedState 读写），保证 per-request 隔离。超出上限时 agent 基于现有信息 best-guess 收尾。

#### Scenario: 超限收尾
- **WHEN** 模型在一个 turn 内 ask_user 调用已达上限仍需更多信息
- **THEN** 后续 ask_user 调用被拒绝，模型基于已有信息给出 best-guess 回答

#### Scenario: 并发会话计数隔离
- **WHEN** 两个 session 同时运行 agent 循环
- **THEN** 各自的 `_ask_count` 互不影响（AgentState per-request 隔离）

### Requirement: 澄清超时

系统 SHALL 为 ask_user 设置等待超时（`ASK_USER_TIMEOUT`，默认 120s）；超时后挂起 Future 以超时原因 resolve，agent 收到超时结果后优雅收尾（提示用户未在限时内补充信息），并清理挂起注册表。用户超时后提交 `POST /clarify-answer` SHALL 返回 404（注册表查无该 session 挂起 Future），不写历史。

#### Scenario: 用户未在限时内回答
- **WHEN** ask_user 等待超过 ASK_USER_TIMEOUT 且用户未提交答案
- **THEN** 工具返回超时结果，agent 告知用户并结束 turn，注册表无残留

#### Scenario: 超时后用户才提交
- **WHEN** 用户超过 ASK_USER_TIMEOUT 后提交澄清答案
- **THEN** POST 返回 404"该澄清问题已超时或不存在"，不写入会话历史

### Requirement: 澄清与检索工具的共存

系统 SHALL 在 agent 循环中同时注册 `retrieve_kb` 与 `ask_user` 工具；模型可先 ask_user 补齐信息再 retrieve_kb 检索，也可在检索不足时 ask_user 追问；两个工具的组合顺序由模型自主决定，不设固定链路。

#### Scenario: 先澄清后检索
- **WHEN** 模型判断查询缺期间，先调用 ask_user 获取期间
- **THEN** 拿到期间后调用 retrieve_kb 用完整查询检索，生成带引用答案
