## ADDED Requirements

### Requirement: 入站消息推导确定性的会话标识

系统 SHALL 由入站消息推导 `session_id`：群聊按 `chatid`（整群共享一段上下文），单聊按 `from_userid`；形如 `wecom:{bot_key}:group:{chatid}` 与 `wecom:{bot_key}:single:{from_userid}`。同一群/同一用户的连续消息 SHALL 映射到同一个 `session_id`。

#### Scenario: 群聊消息

- **WHEN** 收到 `chattype=group` 的入站消息
- **THEN** `session_id` 为 `wecom:{bot_key}:group:{chatid}`，同群其他成员的消息得到相同 `session_id`

#### Scenario: 单聊消息

- **WHEN** 收到 `chattype=single` 的入站消息
- **THEN** `session_id` 为 `wecom:{bot_key}:single:{from_userid}`

### Requirement: 重复入站消息只处理一次

系统 SHALL 以 `msgid` 去重：同一 `msgid` 的重复入站 SHALL 被丢弃且不产生第二次生成。去重记录 SHALL 使用带过期时间与容量上限的有界结构（不得为无界增长的裸映射）。

#### Scenario: 重复 msgid

- **WHEN** 同一 `msgid` 在去重窗口内再次到达
- **THEN** 该消息被丢弃，不触发新的 Agent 生成

#### Scenario: 记录有界淘汰

- **WHEN** 去重记录超过容量上限或超过 TTL
- **THEN** 最旧/过期记录被淘汰，进程内存不随消息量单调增长

### Requirement: 每轮生成携带 trace_id 并三路返回

系统 SHALL 在处理入口为每个入站生成 `trace_<uuid>` 形式的 `trace_id`，并 SHALL 将其写入当前追踪上下文（使 Langfuse 与日志携带该值）。系统 SHALL 通过反馈标识与日志锚点两路返回该值。

#### Scenario: 日志锚点

- **WHEN** 处理一条入站消息
- **THEN** 日志中同时出现 `trace_id` 与 `bot_key`/`msgid`，可据任一定位链路

#### Scenario: 反馈标识承载

- **WHEN** 该轮回复的首个流式帧发送
- **THEN** 该帧携带以 `trace_id` 为值的反馈标识；用户对该回复反馈时，回传事件中可还原出 `trace_id`

### Requirement: 入站消息喂入站点同款 Agent 管线

系统 SHALL 以推导出的 `session_id`、`kb_id`（允许为空）与用户文本调用与站点**同一条** Agent 生成管线；是否检索、是否联网、是否澄清 SHALL 由 Agent 自主决策，通道侧 SHALL NOT 强制或阻止任一工具。

#### Scenario: 有知识库

- **WHEN** 该机器人绑定了知识库且 Agent 决定检索
- **THEN** 走既有检索流程，结果与站点一致

#### Scenario: 无知识库

- **WHEN** `kb_id` 为空且 Agent 未选择检索
- **THEN** 通道仍正常工作，Agent 走联网/纯生成等其余能力，不因缺库报错

### Requirement: 澄清答案回填挂起请求

企微侧 SHALL 支持 `ask_user` 澄清：挂起期间向用户呈现问题；用户的下一条入站文本在该会话存在挂起澄清时 SHALL 作为答案回填，而非开启新回合；无挂起时 SHALL 按普通消息处理。群聊会话中任一成员的回答 SHALL 均可回填。

#### Scenario: 存在挂起澄清

- **WHEN** 某会话存在挂起的 `ask_user`，用户在该会话发送文本
- **THEN** 该文本作为澄清答案回填，生成继续而非重开一轮

#### Scenario: 无挂起澄清

- **WHEN** 会话无挂起澄清，用户发送文本
- **THEN** 按普通消息开启一轮生成

### Requirement: 长连接驱动等待认证并暴露失败

驱动 `start()` SHALL 在建立连接后等待认证结果：认证成功 SHALL 才视为该台连接就绪；认证失败 SHALL 产生可见的告警且该台 SHALL 不计入"连接成功"。

#### Scenario: 认证成功

- **WHEN** 某台机器人连接且认证返回成功
- **THEN** 该台计入成功台数，启动锚点日志反映其成功

#### Scenario: 认证失败可见

- **WHEN** 某台机器人认证失败
- **THEN** 记录告警且该台不计入成功；不得静默地"看似已连上"

### Requirement: 被顶号后停止重连

驱动 SHALL 识别"被新连接顶替/被服务端断开"的情形，并在该情形下 SHALL NOT 发起自动重连（避免多方互踢）。

#### Scenario: 被顶号

- **WHEN** 本连接被同机器人的新连接顶替
- **THEN** 驱动停止重连，不与被顶者互相抢占

### Requirement: 长流保活

在流式发送期间，投影层 SHALL 以不超过 4 分钟为间隔发送至少一次非终态帧，以避免首帧起 6 分钟的收尾时限导致连接被断开。

#### Scenario: 长耗时生成

- **WHEN** 一轮生成耗时接近或超过 6 分钟
- **THEN** 期间保持发送非终态帧，流不被超时断开

### Requirement: 通道声明能力位

通道驱动 SHALL 以声明式能力位（如 stream/markdown 等）暴露自身能力，业务侧 SHALL 依据能力位降级，而 SHALL NOT 依赖平台分支判断。

#### Scenario: 查询能力

- **WHEN** 业务侧需要渲染某类内容
- **THEN** 通过能力位判断该通道是否支持，并按结果选择渲染或降级
