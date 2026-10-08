# history-summarization Specification

## Purpose
TBD - created by archiving change cross-turn-history-summary. Update Purpose after archive.
## Requirements
### Requirement: 摘要触发判据 SHALL 落在「将被丢弃的那一段」

系统 SHALL 以**与历史裁剪相同的切分口径**算出「保留尾部之外、将被丢弃的那一段」，并对**该段**求 token；仅当该段非空且超过 `HISTORY_SUMMARY_TRIGGER_TOKENS`（`src/config/const.py` 的集中绝对值，经验值）时才生成摘要。

系统 SHALL NOT 以「全量历史 token」作为判据——当历史条数不超过轮数粗筛上限时保留尾部覆盖全部历史，被替换段为空，以全量判据会导致每轮调用摘要模型却永不落地。

#### Scenario: 保留尾部覆盖全部历史时不生成

- **WHEN** 历史条数不超过轮数粗筛上限（保留尾部即全量）
- **THEN** 系统 SHALL NOT 调用摘要模型
- **AND** SHALL NOT 产生后台摘要任务

#### Scenario: 将被丢弃段未超触阈值时不生成

- **WHEN** 将被丢弃段的 token 量不超过 `HISTORY_SUMMARY_TRIGGER_TOKENS`
- **THEN** 系统 SHALL NOT 调用摘要模型

#### Scenario: 将被丢弃段超触阈值时生成

- **WHEN** 将被丢弃段非空且超过 `HISTORY_SUMMARY_TRIGGER_TOKENS`
- **THEN** 系统 SHALL 以该段为摘要输入发起生成

### Requirement: 摘要 SHALL 仅在回合正常完成后异步生成

摘要生成 SHALL 仅在**回合正常完成**的收尾路径上触发，且 SHALL 在该轮的 assistant 消息写入会话历史**成功之后**。用户中止、任务取消、异常中断等**未正常完成**的回合 SHALL NOT 触发摘要（这些路径不写 assistant 历史，以其为输入会把"未作答的问题"当作史实）。生成 SHALL NOT 延迟回合收尾或用户可见输出；同一次回合收尾 SHALL 至多发起一次。

#### Scenario: 正常完成触发且不阻塞

- **WHEN** 回合正常完成且将被丢弃段超触阈值
- **THEN** 系统 SHALL 在 assistant 历史写入成功后以后台任务发起摘要
- **AND** 回合收尾与用户可见输出 SHALL NOT 等待该任务

#### Scenario: 取消或异常不触发

- **WHEN** 回合因用户中止、取消或异常而未正常完成
- **THEN** 系统 SHALL NOT 发起摘要生成
- **AND** SHALL NOT 产生以未作答问题为输入的摘要

#### Scenario: 触阈当轮的既有裁剪不变

- **WHEN** 本轮触阈并已发起后台摘要
- **THEN** 本轮的注入与裁剪 SHALL 不受该生成影响
- **AND** 摘要 SHALL 自后续轮次起参与注入

### Requirement: 摘要生成 SHALL 有带 TTL 的独立锁、启动清理与超时保险丝

并发守卫 SHALL 使用**独立的**锁键 `chat_summary_lock:{session_id}`；SHALL NOT 复用会话轮次锁 `chat_lock:{session_id}`。

该锁 SHALL **带 TTL**（不短于摘要调用超时），且其前缀 SHALL **纳入服务启动期的残留锁清理**——否则进程被异常终止会留下永久锁，使该会话此后**永远无法再生成摘要**。

守卫为 **best-effort**：获取失败即跳过本次，不等待、不排队。摘要模型调用 SHALL 套超时保险丝，超时按失败降级。

#### Scenario: 拿不到锁则跳过

- **WHEN** 后台摘要启动时无法取得 `chat_summary_lock:{session_id}`
- **THEN** 系统 SHALL 跳过本次生成
- **AND** SHALL 不阻塞、不排队、不报错

#### Scenario: 残留锁在启动期被清理

- **WHEN** 服务启动
- **THEN** 启动清理 SHALL 覆盖 `chat_summary_lock:*`
- **AND** 残留锁 SHALL NOT 造成该会话永久无法生成摘要

#### Scenario: 锁有 TTL 且不复用轮次锁

- **WHEN** 实现摘要并发守卫
- **THEN** 锁 SHALL 带 TTL
- **AND** SHALL 使用独立键 `chat_summary_lock:{session_id}`，SHALL NOT 使用 `chat_lock:{session_id}`

#### Scenario: 摘要调用超时按失败降级

- **WHEN** 摘要模型调用超出超时保险丝
- **THEN** 系统 SHALL 按失败处理并释放摘要锁

### Requirement: 摘要输入 SHALL 为生成发起时冻结的形态

后台任务的历史输入 SHALL 在**发起时**读取并冻结（作为参数传入任务），任务内 SHALL NOT 再次读取会话历史。否则在后台任务执行期间写入的、尚未作答的下一轮用户消息会被计入历史与覆盖边界。

#### Scenario: 冻结后不再读历史

- **WHEN** 后台摘要在执行期间检测到会话历史已变化
- **THEN** 该变化 SHALL NOT 影响本次摘要的输入与覆盖边界

### Requirement: 摘要 SHALL 结构化，并持久化到会话级存储（含内存降级）

摘要 SHALL 按固定结构化段产出（用户目标 / 已达成的决定 / 未解约束 / 关键事实与数字 / 下一步），使用集中管理的 prompt 模板。

摘要 SHALL 持久化到会话级存储：Redis 键 `chat_summary:{session_id}`（TTL 与既有历史一致，写入时续期），承载**摘要正文**与**覆盖边界（已摘要到的消息条数）**；存储层 SHALL 同时具备**非 Redis 环境下的降级分支**，SHALL NOT 因缺少 Redis 而静默失效。

会话历史被清空时，摘要与摘要锁 SHALL 一并清除。

#### Scenario: 摘要写入并跨轮复用

- **WHEN** 后台摘要成功生成
- **THEN** 摘要正文与覆盖边界 SHALL 写入会话级存储
- **AND** 后续轮次的注入 SHALL 复用该摘要

#### Scenario: 无 Redis 时仍有降级路径

- **WHEN** 运行环境缺少 Redis（存储降级为进程内）
- **THEN** 摘要的读写 SHALL 在降级存储上工作
- **AND** SHALL NOT 静默失效

#### Scenario: 清空会话一并清摘要

- **WHEN** 会话历史被清空
- **THEN** 摘要与摘要锁 SHALL 被一并清除
- **AND** 后续轮次 SHALL NOT 注入旧摘要

#### Scenario: 覆盖边界按消息条数可复现

- **WHEN** 记录摘要覆盖边界
- **THEN** 边界 SHALL 以消息条数表示
- **AND** 切点 SHALL 落在两条消息之间，可精确复现

### Requirement: 摘要 SHALL 满足四条硬不变量，且摘要段 SHALL 受预算约束

系统 SHALL 在采用摘要前校验以下四条；任一不满足即**不采用**（见降级要求）：

1. **更小**：摘要 token 量 SHALL 小于被替换段的 token 量。
2. **严格不增长**：就地更新时，新摘要的 token 量 SHALL NOT 超过上一版摘要。
3. **拒绝截断摘要**：摘要模型返回被判定为截断（正常结束原因之外）时 SHALL 视为失败——为此摘要调用 SHALL 设置显式输出上限，使该判据可判定。
4. **不做摘要之摘要**：输入含既有摘要时 SHALL 以标记识别，在其基础上增量更新。

此外，**「摘要段 + 保留尾部」的聚合 token 量 SHALL NOT 超过 `HISTORY_TOKEN_BUDGET`**；超限时 SHALL 先缩减摘要，仍超再裁剪尾部。摘要段 SHALL NOT 因"不在裁剪路径上"而绕过预算口径。

token 量一律经统一计数入口 `src/infra/llm/token_count.py`。

#### Scenario: 不小于被替换段则不采用

- **WHEN** 生成的摘要 token 量 ≥ 被替换段
- **THEN** 系统 SHALL NOT 采用该摘要
- **AND** 走降级路径

#### Scenario: 严格不增长

- **WHEN** 已有上一版摘要，本轮在其基础上更新
- **THEN** 新摘要 token 量 SHALL ≤ 上一版
- **AND** 若更长，SHALL NOT 采用并走降级路径

#### Scenario: 截断的摘要不被采用

- **WHEN** 摘要模型返回被判定为截断
- **THEN** 系统 SHALL NOT 采用该摘要
- **AND** 走降级路径

#### Scenario: 不做摘要之摘要

- **WHEN** 输入包含上一版摘要
- **THEN** 系统 SHALL 以标记识别它为摘要
- **AND** 要求在其基础上增量更新

#### Scenario: 摘要段与尾部聚合不超预算

- **WHEN** 组装「摘要段 + 保留尾部」
- **THEN** 其聚合 token 量 SHALL ≤ `HISTORY_TOKEN_BUDGET`
- **AND** 超限时 SHALL 先缩摘要、仍超再裁尾部

### Requirement: 摘要失败 SHALL 降级、不重试、不中断对话

读摘要、生成、校验、写回任一步骤失败时，系统 SHALL 精确捕获异常、记录带原因的降级信号，并**保留既有纯裁剪行为**；SHALL NOT 重试本次生成，SHALL NOT 抛出异常中断对话，SHALL NOT 静默产生损坏的历史。写回失败时 SHALL NOT 遗漏释放摘要锁。

#### Scenario: 生成失败记录降级且不重试

- **WHEN** 摘要生成失败或产出不合规
- **THEN** 系统 SHALL 记录降级信号（含原因）
- **AND** SHALL NOT 在本次重试
- **AND** 本轮对话 SHALL 正常完成

#### Scenario: 写回失败不影响用户且释放锁

- **WHEN** 摘要写回会话级存储失败
- **THEN** 系统 SHALL 记录降级信号并继续
- **AND** SHALL NOT 影响用户可见结果
- **AND** 摘要锁 SHALL 被释放

### Requirement: 摘要段 SHALL 只读注入、带让位声明、且仅窄幅剥离引用编号

seed 点 SHALL **只读取**摘要（经外部预取传入的值通道），SHALL NOT 在该点生成、SHALL NOT 在该点直接访问存储。

摘要段 SHALL 作为**独立历史消息**注入在 system 段之后、保留的最近若干轮之前，SHALL NOT 并入 system 段。注入路径 SHALL 独立于技能注入机制（后者只扫描历史列表）。

摘要段 SHALL 带可辨识标签，并附「仅供对话背景；事实性结论仍须以本轮检索结果为准；不确定时请用户复述」语义。

摘要正文中形如 `[数字]` 的引用编号 SHALL 被剥离或拦截；系统 SHALL NOT 仅因包含此类编号而丢弃整篇摘要。

#### Scenario: 无摘要时行为与既有路径一致

- **WHEN** 会话级存储中不存在摘要
- **THEN** 历史注入 SHALL 与未引入本能力时一致
- **AND** SHALL NOT 因缺失摘要报错或改变顺序

#### Scenario: 注入顺序

- **WHEN** 存在摘要并组装首轮消息
- **THEN** 顺序 SHALL 为「system 段 → 摘要段 → 保留的最近若干轮原文」
- **AND** 摘要段 SHALL 为独立历史消息，不进 system 段

#### Scenario: 带让位声明

- **WHEN** 注入摘要段
- **THEN** 该段 SHALL 含「仅供背景、事实以本轮检索为准」与「不确定时请用户复述」语义

#### Scenario: 编号被窄幅剥离而不丢弃摘要

- **WHEN** 摘要正文含 `[数字]` 形式的编号
- **THEN** 系统 SHALL 剥离或拦截该编号
- **AND** SHALL NOT 因此丢弃整篇摘要
- **AND** SHALL NOT 误伤年份、Markdown 链接等非编号内容

### Requirement: 摘要 SHALL NOT 写入会话回放存储

摘要 SHALL NOT 写入 MySQL 的会话消息表；用户侧历史回放 SHALL 保持**完整原文**（摘要仅为模型侧上下文的内部产物）。

#### Scenario: 回放不含摘要

- **WHEN** 客户端请求会话消息列表
- **THEN** 返回内容 SHALL 为完整原文消息
- **AND** SHALL NOT 包含摘要正文
