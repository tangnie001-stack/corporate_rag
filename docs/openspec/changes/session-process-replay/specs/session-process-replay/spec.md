# session-process-replay Specification

## ADDED Requirements

### Requirement: 生成期事件按序采集

生成任务 SHALL 在事件转换的统一采集点（覆盖主循环与 clarify 通道两条路径）将每个产出的 SSE 事件（type + payload）按到达顺序追加进本次生成的私有事件日志（`_StreamCapture.events_log`）。delegate/ask_user 等经 clarify 通道到达的事件帧 SHALL 同样被采集。该日志 SHALL 独立于 StreamingRunManager 事件缓冲，不受其 MAX_ITEMS=2000 截断与 TTL=300s 清理影响。

#### Scenario: 一次生成的完整事件被采集

- **WHEN** 一次生成完成，期间产生 status×7、token×431、citation×7、model_info×1、done×1
- **THEN** events_log SHALL 按到达顺序包含全部 447 条记录（type 与 payload 与 SSE 帧一致）

#### Scenario: clarify 通道事件被采集

- **WHEN** 生成中发生委派（delegate start/delta/end 经 clarify 通道到达）
- **THEN** events_log SHALL 包含这些 delegate 事件帧，回放时委派区可完整重建

#### Scenario: 取消路径保留已采集事件

- **WHEN** 用户在生成中途点击停止，abort 触发 CancelledError
- **THEN** 落库的 process SHALL 包含到中断点为止的全部已采集事件

### Requirement: 终态序列化写入 process 列

生成收尾落库时，系统 SHALL 将事件日志按轮次语义分拣后序列化为 JSON 对象 `{"format_version": 1, "events": [{seq, type, payload}, ...]}`（seq 从 1 按到达顺序递增，format_version 标识 payload 结构版本，读取端不认识的版本 SHALL 明确报错）写入 assistant 消息的 `process` 列，并将实际模型名写入既有 `model_name` 列。分拣规则：tool_calls 收尾轮的 content token 帧 SHALL 保留为旁白事件；末轮 answer 的 token 帧 SHALL 剔除（正文由 answer 列承载，避免历史回放重复渲染）；model_info/abstention/done/error/citation 五类帧 SHALL 均不入 process（分别由既有 model_name 列、answer 措辞+消息 status、sources 列承载）。`conversation_history` 表 SHALL 新增 `process MEDIUMTEXT NULL` 列（model_name 列已存在直接复用），存量消息 process 为 NULL。测试用例 SHALL 使用固化的真实帧序样本（tests/fixtures/）。

#### Scenario: 正常完成落库

- **WHEN** 一次生成正常完成并执行 save_assistant_message
- **THEN** assistant 行的 process SHALL 为含 format_version 与完整 events 数组的 JSON，model_name SHALL 为本次实际模型名

#### Scenario: answer 段 token 帧被剔除

- **WHEN** 末轮 answer 的 431 条 token 帧到达采集日志并执行序列化
- **THEN** process SHALL 不包含这些 token 帧，历史回放渲染的正文 SHALL 不重复

#### Scenario: 存量消息兼容

- **WHEN** 读取 2026-09-09 之前落库的历史消息
- **THEN** process SHALL 为 NULL，接口正常返回不报错

### Requirement: messages 接口返回过程数据

`/api/sessions/messages` 的 assistant 消息 SHALL 返回 `process`（事件 JSON 数组，存量消息为 null）与 `model_name`（可空字符串，复用既有列）字段。

#### Scenario: 历史回放携带过程数据

- **WHEN** 前端调用 /api/sessions/messages 且该会话含本变更后生成的回答
- **THEN** assistant 消息项 SHALL 包含非空 process 数组与 model_name

### Requirement: 旁白判定——轮次收尾方式

一轮 = 一次模型调用。轮以 tool_calls 收尾时，本轮的 content 流 SHALL 归类为旁白（preamble）；轮未出 tool_calls 自然收尾时，本轮 content 流 SHALL 归类为正式回答（不进入过程序列，走 answer 数据）。前端实时路径 SHALL 以待定区机制实现等价判定：工具调用首次出现前的 token 进待定缓冲，工具调用出现时固化为旁白块；直到 done 均无工具调用则待定缓冲内容即为正文。

#### Scenario: 中间轮旁白隔离

- **WHEN** 第一轮模型输出 content「参考文档为空，我将通过检索…」后发起 retrieve_kb 工具调用
- **THEN** 该段 content SHALL 渲染为 assistant 气泡 bubble-content 内的旁白正文段（与正式回答同正文样式，v3 单气泡叙事），而非独立样式块

#### Scenario: 纯问答无工具调用

- **WHEN** 一次生成全程无任何工具调用，模型的 content 直接收尾
- **THEN** 全部 content SHALL 作为正式回答渲染进气泡，不产生旁白块

### Requirement: 历史回放重建过程区（气泡内）

`loadSessionMessages` 对含非空 process 的 assistant 消息 SHALL 按数组顺序将事件喂给与实时路径相同的**过程元素渲染函数**，在该回答的 assistant 气泡 `bubble-content` 内重建过程区（状态行/旁白正文段/委派区等，全部位于气泡内、正式正文之前），渲染产物 SHALL 与实时观看一致。复用边界：SHALL 仅复用过程元素渲染函数，SHALL NOT 触发实时 handler 的运行期副作用（done 收尾、loadSessions、composer 接管）。交互类事件 SHALL 静态化：`ask_user` 帧渲染为静态注记（非交互卡片）；`error`/`done` 帧跳过（中断语义由消息 status 标签承载）。process 为 NULL 的存量消息 SHALL 跳过重建（仅终稿）。本要求取代既有 D7「历史重载不重建」决策。

#### Scenario: 刷新后过程原样重现

- **WHEN** 用户刷新页面加载含 process 的会话
- **THEN** 该回答的气泡内过程区 SHALL 按事件顺序重建（状态行、旁白正文段、委派区与实时观看一致），正文渲染 answer 且 SHALL NOT 出现正文重复

#### Scenario: 历史中的澄清事件静态化

- **WHEN** process 数组中包含 ask_user 事件帧
- **THEN** 历史回放 SHALL 渲染为静态注记，SHALL NOT 渲染交互卡片或接管输入区

#### Scenario: 存量消息降级

- **WHEN** 前端加载 process 为 null 的历史消息
- **THEN** 仅渲染终稿与引用，无气泡内过程区，无 JS 错误

### Requirement: 相邻同型合并渲染

过程区渲染（bubble-content 内）SHALL 将「相邻且同类型」的元素合并为一个组 div（如连续 status 归入同一组），类型不同即分拆；只看相邻，不看全局；整体严格按到达顺序。实时路径与历史回放 SHALL 共用同一合并实现。

#### Scenario: 连续状态合并

- **WHEN** 过程元素序列为 status, status, status, think, status
- **THEN** 渲染为 3 个组 div（status×3 合并、think 独立、status 独立）

#### Scenario: 类型交错不合并

- **WHEN** 过程元素序列为 status, preamble, think, status, retrieve
- **THEN** 渲染为 5 个组 div（相邻均不同类型）

### Requirement: 深度思考开启时 reasoning_content 回传

深度思考开启时，agent 循环向模型发起的每轮请求 SHALL 在 assistant 消息中携带其 `reasoning_content`，且请求 SHALL 启用 `preserve_thinking=true`（qwen3.7-flash）。深度思考关闭时无 reasoning_content，行为不变。

#### Scenario: 多轮工具调用回传思考链

- **WHEN** 深度思考开启、第一轮模型产生 reasoning_content 并发起工具调用
- **THEN** 第二轮请求的 assistant 消息 SHALL 包含该 reasoning_content，请求参数 SHALL 含 preserve_thinking=true
