## ADDED Requirements

### Requirement: 集中式依赖注入
系统 SHALL 通过 `src/api/dependencies.py` 提供统一的 `get_app_service` 依赖，替代各 API 模块中重复的 `_get_service()` 实现。

`get_app_service` SHALL 采用延迟初始化模式，首次调用时创建 `AppService` 实例，后续复用。

#### Scenario: 依赖集中管理
- **WHEN** API 路由函数需要 `AppService` 实例
- **THEN** 通过 `svc: AppService = Depends(get_app_service)` 注入，而非调用模块级 `_get_service()`

### Requirement: 测试 mock 通过 dependency_overrides
系统 SHALL 支持通过 FastAPI `dependency_overrides` 机制替换 `get_app_service` 依赖，消除对 `@patch` 路径字符串的依赖。

测试 SHALL 通过 `tests/api/conftest.py` 中的 `mock_app_service` fixture 提供 mock 实例。

#### Scenario: 测试使用 mock_app_service fixture
- **WHEN** 测试函数使用 `mock_app_service` fixture
- **THEN** 路由函数通过 `dependency_overrides` 自动接收 mock 实例，无需 `@patch` 装饰器

#### Scenario: 测试间隔离
- **WHEN** 每个测试结束后
- **THEN** `dependency_overrides` SHALL 被清理，不影响后续测试用例
