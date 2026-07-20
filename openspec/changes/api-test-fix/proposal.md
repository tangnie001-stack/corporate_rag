## Why

API 目录重构后（`src/api/routes/*` → `src/api/*`），测试文件中的 `@patch` 路径未同步更新，6 个现有测试全部因路径错误失效，且 19 个端点中 11 个完全没有测试覆盖。测试不能真实拦截回归——"测试通过但接口实际是坏的"。

## What Changes

- 修复 6 个现有 API 测试的 mock 路径错误
- 补齐关键路径端点（auth、sessions、kb_eval、documents 子端点）的单元测试
- 提取公共测试基础设施到 `conftest.py`（TestClient + auth_client fixture）
- 添加 `make test-api` 命令，支持专项运行 API 测试

## Capabilities

### New Capabilities
- `api-test-coverage`: 补齐 API 路由层的单元测试，覆盖 19 个端点的 happy path 和错误场景

### Modified Capabilities
- `evaluation-pipeline`: 新增 `test_kb_eval.py` 测试 `eval/latest` 端点，与 eval_report 表交互的测试用例

## Impact

- `tests/api/` 目录：新增/修改 8 个测试文件
- `Makefile` 或 `pyproject.toml`：新增 `test-api` 运行方式
