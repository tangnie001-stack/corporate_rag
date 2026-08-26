# composer-ui Specification

## Purpose
TBD - created by syncing change agentic-clarification. Update Purpose after sync.
## Requirements

### Requirement: 澄清时输入区被问题表单接管

系统 SHALL 在收到 `SSEAskUserEvent`（新增事件类型，`SSEClarificationEvent` 退役）后，隐藏主聊天输入区（textarea + 发送按钮），改由问题表单（composer）接管；表单包含问题文本、选项（单选 radio / 多选 checkbox，label+description）、自定义文本输入框；用户提交后表单消失，输入区恢复。澄清期间 SSE 连接 SHALL 保持打开，不得关闭。

#### Scenario: 澄清期间输入区接管
- **WHEN** 前端收到澄清事件且处于流式连接中
- **THEN** 主输入框被隐藏，问题表单显示，用户无法发送新消息

#### Scenario: 提交后恢复输入
- **WHEN** 用户提交澄清答案
- **THEN** 表单消失，输入区恢复，原 SSE 流继续输出后续 token

### Requirement: 提交不重开流

系统 SHALL 在澄清提交时改为 `POST /api/chat/clarify-answer`（携带 session_id 与答案结构），不得关闭当前 EventSource 或发起新的 `/chat/stream` 请求；后端在收到答案后于**同一** SSE 流继续推送 token/status/citation/done。

#### Scenario: 澄清后同流续答
- **WHEN** 用户提交澄清答案
- **THEN** 前端不创建新 EventSource，后端在原流上继续推送最终答案与 done 事件

### Requirement: 多问题批量渲染

系统 SHALL 在澄清事件含多个 questions 时渲染多 section 表单（每问题一个 section，含选项与自定义输入），一次提交返回全部答案；section 数量封顶 `MAX_ASK_PER_TURN`。

#### Scenario: 多问题一次提交
- **WHEN** 澄清事件携带 3 个 question
- **THEN** 表单渲染 3 个 section，用户一次提交返回全部 3 个答案

### Requirement: 断连期间澄清状态清理

系统 SHALL 在 SSE 连接异常中断（网络错误/服务端关闭）且存在未提交澄清时，移除表单并恢复输入区，提示用户可重新提问；不得残留无响应的表单。

#### Scenario: 澄清中断连
- **WHEN** 表单显示期间 SSE 连接断开
- **THEN** 表单被移除，输入区恢复，用户可重新发送消息

### Requirement: 澄清超时前端处理

系统 SHALL 在 `CLARIFYING` 状态下收到 SSE `done` 事件（ask_user 超时后 turn 结束）时：关闭 composer、恢复输入区、显示"该问题已超时，请重新提问"。提交答案的 fetch 返回非 2xx（如超时后 404）时 SHALL 同样关闭 composer 并提示重问，覆盖 POST 先于 done 到达的竞态。

#### Scenario: 超时后 done 关闭表单
- **WHEN** ask_user 超时且 SSE 已发送 done 而用户尚未提交
- **THEN** done 处理器关闭 composer、恢复输入、提示"该问题已超时，请重新提问"

#### Scenario: 超时后提交返回 404
- **WHEN** 用户超时后才提交澄清答案
- **THEN** fetch 收到 404，composer 关闭并提示重问

### Requirement: 流程状态事件

系统 SHALL 按事件类型驱动流程状态提示（agent 化后无节点，`SSE_STATUS` 节点名映射失效）：`on_chat_model_start`（agent 节点）→ "正在思考..."；`on_tool_start`（retrieve_kb）→ "正在检索相关文档..."；`on_tool_start`（ask_user）→ 不发状态（composer 接管输入区）；`on_tool_end`（retrieve_kb）→ "检索完成，正在分析..."。前端 status-tag 渲染逻辑不变。

#### Scenario: 检索状态提示
- **WHEN** agent 循环调用 retrieve_kb 工具
- **THEN** 前端显示"正在检索相关文档..."状态，工具结束后显示"检索完成，正在分析..."

#### Scenario: 澄清时不显示状态条
- **WHEN** agent 循环调用 ask_user 工具
- **THEN** 不发送状态事件（composer 已接管输入区，无需状态条）
