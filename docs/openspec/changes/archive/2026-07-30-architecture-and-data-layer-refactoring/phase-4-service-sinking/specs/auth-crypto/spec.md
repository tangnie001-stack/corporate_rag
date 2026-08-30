## ADDED Requirements

### Requirement: 密码哈希函数

系统 SHALL 提供独立的 `hash_password(password: str) -> str` 函数，使用 bcrypt 对明文密码进行哈希。

#### Scenario: 正常哈希
- **WHEN** 传入明文密码
- **THEN** 返回哈希后的密码字符串，每次调用返回不同结果（含随机 salt）

### Requirement: 密码校验函数

系统 SHALL 提供独立的 `verify_password(password: str, password_hash: str) -> bool` 函数，校验明文密码是否匹配 bcrypt 哈希。

#### Scenario: 密码匹配
- **WHEN** 传入正确密码和对应的哈希
- **THEN** 返回 True

#### Scenario: 密码不匹配
- **WHEN** 传入错误密码和哈希
- **THEN** 返回 False

### Requirement: 模块位置与导入层

该模块 SHALL 放置在 `src/utils/auth_crypto.py`，不依赖任何项目内部的业务模块（仅依赖 bcrypt 第三方库）。

#### Scenario: 模块导入
- **WHEN** 从 `src.utils.auth_crypto` 导入
- **THEN** 成功导入 `hash_password` 和 `verify_password` 两个函数
