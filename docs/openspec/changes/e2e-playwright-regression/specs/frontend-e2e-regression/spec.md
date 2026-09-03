## ADDED Requirements

### Requirement: E2E 测试工程独立目录

项目 SHALL 提供独立的前端 E2E 测试工程，位于仓库根 `e2e/` 目录，包含 `package.json`、`playwright.config.ts`、`tests/` 子目录，使用 Playwright TypeScript 编写确定性回归用例。该工程 SHALL 与 Python `tests/` 目录分离，不被 `pytest tests/` 全量门禁收集或驱动。

#### Scenario: 工程可初始化依赖

- **WHEN** 在 `e2e/` 目录执行 `npm install`
- **THEN** 安装 `@playwright/test` 依赖且生成可运行的 playwright 测试环境

#### Scenario: 测试可从目录执行

- **WHEN** 在 `e2e/` 目录执行 `npx playwright test`
- **THEN** 运行 `tests/` 下的全部 spec，产出通过/失败结果与报告

#### Scenario: 质量门禁隔离

- **WHEN** 在仓库根执行 `pytest tests/ -v`
- **THEN** 不加载或尝试执行 `e2e/` 下的任何 TypeScript 测试文件

### Requirement: 真实联调运行形态

E2E 回归 SHALL 以真实联调形态运行：通过 docker compose 启动 mysql/redis/app/nginx 全栈，Playwright 复用已运行服务（`reuseExistingServer`），通过 nginx 的宿主端口访问页面与 API。nginx 宿主端口映射 SHALL 调整为 8080（容器内 `8080:80`），不要求占用宿主 80 端口。

#### Scenario: 通过 8080 访问页面

- **WHEN** docker compose 全栈已启动且 nginx 映射为 `8080:80`
- **THEN** 浏览器可访问 `http://localhost:8080/chat.html`，页面内 `/api` 请求由 nginx 反代到后端

#### Scenario: 复用已起服务不重复拉起

- **WHEN** Playwright 配置 `webServer` 指向已运行的全栈
- **THEN** 测试启动不重复拉起服务，`baseURL` 指向 `http://localhost:8080`

### Requirement: 测试报告与失败取证

E2E 回归 SHALL 开启 Playwright trace（`retain-on-failure`），失败用例自动保留 trace 与截图。测试输出 SHALL 能定位到具体 spec 与用例。

#### Scenario: 失败保留 trace

- **WHEN** 一个用例断言失败
- **THEN** 该用例的 trace 与截图被保留到报告产物中，成功的用例不保留 trace

#### Scenario: 报告可查看

- **WHEN** 测试运行结束
- **THEN** 产出 HTML 报告，可展开查看每个用例的步骤、网络与失败详情
