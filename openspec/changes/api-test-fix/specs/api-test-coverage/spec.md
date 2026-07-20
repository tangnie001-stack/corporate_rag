## ADDED Requirements

### Requirement: API 端点单元测试覆盖
系统 SHALL 为所有 API 路由端点提供单元测试覆盖，每个端点至少包含一个 happy path 场景和一个错误场景。

测试 SHALL 使用 `fastapi.testclient.TestClient` 进行 HTTP 请求模拟，并通过 `@patch` 替换 service 层依赖。

#### Scenario: 有认证要求的端点返回正确结构
- **WHEN** 使用 `auth_client` fixture 发起 POST 请求
- **THEN** 返回状态码 200，响应体包含 `{"code", "message", "data"}` 结构

#### Scenario: 无认证要求的端点正常响应
- **WHEN** 使用 `client` fixture 发起 POST 请求
- **THEN** 请求不需要 Cookie 或 Authorization 头

### Requirement: 公共测试基础设施
系统 SHALL 通过 `tests/api/conftest.py` 提供统一的测试 fixture，消除各测试文件中重复的辅助函数。

`conftest.py` SHALL 提供：
- `client` fixture：返回裸 TestClient（无认证）
- `auth_client` fixture：返回带认证 Cookie 的 TestClient，自动绕过中间件 token 校验

#### Scenario: auth_client 绕过中间件认证
- **WHEN** 测试函数使用 `auth_client` fixture 对受保护端点发起请求
- **THEN** 请求不经过 `UserAuth` 中间件的 token 校验，直接路由到业务 handler

### Requirement: 认证端点测试
系统 SHALL 测试 auth 端点（login / verify / logout）的以下场景：

#### Scenario: login 新用户自动注册
- **WHEN** 用户使用未注册的账号调用 login
- **THEN** 返回 200，响应中包含 token 和 user_id

#### Scenario: login 已有用户密码正确
- **WHEN** 用户使用已注册的账号和正确密码调用 login
- **THEN** 返回 200，响应中包含 token

#### Scenario: login 密码错误
- **WHEN** 用户使用已注册的账号但密码错误调用 login
- **THEN** 返回 401

#### Scenario: verify token 有效
- **WHEN** 携带有效 token 调用 verify
- **THEN** 返回 200，valid=True

#### Scenario: verify 无 token
- **WHEN** 不携带 cookie 调用 verify
- **THEN** 返回 200，valid=False

#### Scenario: anonymous 新用户
- **WHEN** 调用 anonymous 端点
- **THEN** 返回 200，响应中包含 user_id

### Requirement: 会话端点测试
系统 SHALL 测试 sessions 端点（list / messages / delete）的 happy path 和错误场景。

#### Scenario: 列出会话
- **WHEN** 调用 sessions/list
- **THEN** 返回会话列表，包含 id / title / kb_name / message_count / timestamps

#### Scenario: 会话消息
- **WHEN** 调用 sessions/messages 且 session 存在
- **THEN** 返回消息列表，每条消息包含 role / content / sources / created_at

#### Scenario: 会话消息 session 不存在
- **WHEN** 调用 sessions/messages 且 session 不存在
- **THEN** 返回 404

#### Scenario: 删除会话
- **WHEN** 调用 sessions/delete 且 session 存在
- **THEN** 返回 200，success=True

#### Scenario: 删除不存在的会话
- **WHEN** 调用 sessions/delete 且 session 不存在
- **THEN** 返回 404

### Requirement: 评估端点测试
系统 SHALL 测试 kb_eval 端点（eval/latest）的响应正确性。

#### Scenario: 获取最新评估结果
- **WHEN** 调用 kbs/eval/latest 且 KB 有评估记录
- **THEN** 返回评估报告，包含 faithfulness / answer_relevancy / context_precision / context_recall / overall_score / passed / qa_count

#### Scenario: 获取最新评估结果但 KB 无记录
- **WHEN** 调用 kbs/eval/latest 且 KB 无评估记录
- **THEN** 返回 200，data 为 null

### Requirement: 聊天端点测试
系统 SHALL 测试 chat 端点（stream）的基本响应结构。

#### Scenario: 聊天 SS E 返回流式响应
- **WHEN** 调用 chat/stream
- **THEN** 返回 200，content-type 为 text/event-stream
