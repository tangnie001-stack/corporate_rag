## Context

API 目录重构后 mock 路径全部失效，19 个端点中 11 个无测试覆盖。现有测试使用了手工封装的辅助函数（`_setup_auth()`），在不同文件中重复实现。

## Goals / Non-Goals

**Goals:**
- 修复现有 6 个测试的 mock 路径，使其能真实验证接口
- 补齐 auth、sessions、kb_eval、documents 子端点的测试
- 提取公共 fixture 到 conftest.py，消除重复
- 添加 `make test-api` 命令

**Non-Goals:**
- 不改造现有测试框架（保留 pytest + unittest.mock）
- 不做集成测试或端到端测试
- 不修改 API 业务代码（只改测试）

## Decisions

1. **conftest.py + fixture 模式**：统一通过 `auth_client` fixture 提供认证态，替代各文件中手写的 `_setup_auth()`。`client` fixture 提供裸 TestClient 给无需认证的端点（auth、health）。

2. **auth 测试特殊处理**：`UserAuth.get_user_id_from_token_async` 同时在中间件和 auth 端点中被调用，auth 测试不依赖 `auth_client` 的中间件 patch，而是直接 patch `src.api.auth.UserAuth`。

3. **SSE 测试只验证状态码和 content-type**：TestClient 对 StreamingResponse 的惰性求值限制，追 SSE 事件流属于集成测试范畴。

## Risks / Trade-offs

- 每个测试仍需 `@patch("src.api.xxx._get_service")`，路径字符串仍可能写错。该风险在后续 DI 重构（di-refactor change）中统一解决。
- SSE 端点（chat/stream）无法通过单元测试验证内容正确性，只能验证响应格式。

## 参考

完整设计文档见 `docs/superpowers/specs/2026-07-17-api-test-design.md`
