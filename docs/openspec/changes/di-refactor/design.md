## Context

6 个 API 模块（auth、knowledge_base、documents、sessions、chat、kb_eval）各自维护一份完全相同的 `_service` + `_get_service()` 模块级单例代码。测试中需要 `@patch("src.api.xxx._get_service")`，路径字符串在目录重构后全部失效，维护成本高。

## Goals / Non-Goals

**Goals:**
- 将 6 处 `_get_service()` 统一为 `src/api/dependencies.py` 中的单个 `get_app_service` 依赖
- 路由函数签名改为 `svc: AppService = Depends(get_app_service)`，依赖显式化
- 测试通过 FastAPI `dependency_overrides` 机制 mock，不再依赖 `@patch` 路径字符串

**Non-Goals:**
- 不修改 `AppService` 本身
- 不修改 `src/infra/`、`src/rag/`、`src/services/` 等下层模块
- `health.py` 保持原样（`_ConfigService` 只有一处使用）

## Decisions

1. **FastAPI `Depends` + `dependency_overrides`**：利用 FastAPI 原生 DI 机制，而不是引入外部 DI 框架。`dependency_overrides` 在测试中替换依赖，消除对 `@patch` 路径的依赖。

2. **模块级 `_service` 变量保留但集中**：延迟初始化模式不变（首次调用时创建），只把 6 份相同代码合并到 1 份。

3. **`mock_app_service` fixture 返回 mock 实例而非 `return_value`**：因为 fixture 直接通过 `dependency_overrides` 注入 mock，测试函数直接操作 `mock_app_service` 即可，无需 `.return_value`。

## Risks / Trade-offs

- `health.py` 保留旧模式不变，后续可独立处理
- `dependency_overrides.clear()` 在测试后清理，确保不影响其他测试用例

## 参考

完整设计文档见 `docs/superpowers/specs/2026-07-17-di-refactor-design.md`
