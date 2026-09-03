## ADDED Requirements

### Requirement: 测试侧捕获 trace_id

E2E 测试 SHALL 在测试侧拦截浏览器请求，从每次响应头读取 `X-Trace-ID`（后端 trace_id 中间件保证所有响应携带该头），无需修改前端 `api.js`/`chat.js` 或后端代码。捕获到的 trace_id SHALL 与产生它的请求/用例关联。

#### Scenario: 读取响应头 trace_id

- **WHEN** 测试执行过程中浏览器收到含 `X-Trace-ID` 头的响应
- **THEN** 测试侧拦截逻辑读取并记录该 trace_id

#### Scenario: 不改前端代码

- **WHEN** 检查前端 `api.js` / `chat.js`
- **THEN** 前端代码不被修改即可获得 trace_id（由测试侧捕获响应头实现）

### Requirement: 失败自动附 trace_id

用例断言失败时，SHALL 自动把该用例最近一次关联的 trace_id 写入测试报告（annotation/attachment），并随 trace、截图一并呈现，供定位后端根因。

#### Scenario: 失败报告含 trace_id

- **WHEN** 一个用例断言失败
- **THEN** 测试报告中包含该用例的 trace_id、截图与失败信息

#### Scenario: trace_id 可用于后端定位

- **WHEN** 测试失败报告给出 trace_id
- **THEN** 可在后端日志 `/data/logs` 按该 trace_id grep 出同一请求链路的日志行
