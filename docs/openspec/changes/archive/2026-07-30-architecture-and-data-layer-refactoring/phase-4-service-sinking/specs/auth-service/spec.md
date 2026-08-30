## ADDED Requirements

### Requirement: AuthService 封装用户注册逻辑

系统 SHALL 通过 `AuthService.register()` 封装用户注册流程，包括密码验证、密码哈希、写入数据库。

#### Scenario: 注册成功
- **WHEN** 用户提供符合长度要求的账号和密码
- **THEN** `AuthService.register()` 调用 `auth_crypto.hash_password()` 生成密码哈希，调用 `db.add_user()` 写入数据库，返回包含 user_id 和 account 的 dict

#### Scenario: 账号已存在
- **WHEN** 用户注册时使用已存在的账号
- **THEN** `AuthService.register()` 抛出 `BusinessError`，错误码指示账号已存在

### Requirement: AuthService 封装用户登录逻辑

系统 SHALL 通过 `AuthService.login()` 封装用户登录流程，包括密码校验、令牌生成、令牌更新。

#### Scenario: 登录成功
- **WHEN** 用户提供正确的账号和密码
- **THEN** `AuthService.login()` 调用 `auth_crypto.verify_password()` 验证密码，生成 token，调用 `db.update_user_token()` 更新令牌，返回包含 token 和 user_id 的 dict

#### Scenario: 密码错误
- **WHEN** 用户提供错误的密码
- **THEN** `AuthService.login()` 抛出 `BusinessError`，错误码指示密码错误

### Requirement: AuthService 封装令牌验证逻辑

系统 SHALL 通过 `AuthService.verify_token()` 验证用户会话令牌。

#### Scenario: 令牌有效
- **WHEN** 提供一个有效的会话令牌
- **THEN** `AuthService.verify_token()` 返回用户 ID

#### Scenario: 令牌无效
- **WHEN** 提供一个无效或过期的令牌
- **THEN** `AuthService.verify_token()` 返回 None

### Requirement: API 层只保留路由和校验

`api/auth.py` 中的路由 handler SHALL 只做参数校验和路由转发，不包含业务逻辑。

#### Scenario: 注册路由
- **WHEN** POST /api/auth/register 收到请求
- **THEN** handler 校验请求体后调用 `AuthService.register()`，返回 `success()` 包装的响应

#### Scenario: 登录路由
- **WHEN** POST /api/auth/login 收到请求
- **THEN** handler 校验请求体后调用 `AuthService.login()`，返回 `success()` 包装的响应
