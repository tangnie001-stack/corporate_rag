## MODIFIED Requirements

### Requirement: 每台机器人一条长连接驱动，逐台装配、降级与关停

系统 SHALL 为每台机器人构造一个长连接驱动并各自建立连接**并完成认证**；某台连接或认证失败时 SHALL 只记 warning 并跳过该台（不阻塞应用启动），SHALL NOT 影响其余机器人。驱动 SHALL 集中存放于 `wecom_service` 的注册表（键为 `bot_key`）——**含未认证就绪者，以便关停**。`stop()` SHALL 逐台断开，单台断开失败 SHALL 记 warning 并继续处理其余台，且 SHALL 幂等（重复调用安全）。启动完成时系统 SHALL 记录一条 INFO 锚点日志，给出**认证成功台数与总台数**（成功台数只计**认证就绪**者，与关停集合口径分离）。

#### Scenario: 全部连接成功

- **WHEN** `WECOM_BOT_MODE=long_connection` 且配置合法、企微端点可达、各台认证成功
- **THEN** 每台机器人各建立一条长连接，且记录锚点日志显示认证成功台数等于总台数

#### Scenario: 单台连接失败

- **WHEN** 某台机器人建立连接时抛错
- **THEN** 该台被跳过并记 warning，其余机器人仍建立连接，应用启动成功，且锚点日志反映认证成功台数小于总台数

#### Scenario: 认证失败不计入成功

- **WHEN** 某台机器人连接建立但认证失败
- **THEN** 该台记 warning、不计入锚点的认证成功台数（驱动侧"认证失败即断开且不重连"的行为规范归 `wecom-agent-bridge`，不在此重复）

#### Scenario: 关停时单台断开失败

- **WHEN** 关停过程中某台 `disconnect()` 抛错
- **THEN** 其余台仍被断开，失败台记 warning；重复调用 `stop()` 不报错
