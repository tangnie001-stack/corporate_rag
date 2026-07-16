## ADDED Requirements

### Requirement: 三层目录结构

src/ 的目录结构 MUST 遵循 `api → services/rag/chat → infra` 三层分层，每层职责明确，禁止跨层依赖。

#### Scenario: api/ 层不直接调用 infra/

- **WHEN** api/ 下的文件需要访问数据库或向量存储
- **THEN** MUST 通过 services/ 层调用，不得直接 import infra/

#### Scenario: 每个目录有清晰的职责边界

- **WHEN** 查看 src/ 目录结构
- **THEN** 每个顶层目录的用途 MUST 能从名称和 README/docstring 推断

### Requirement: 文件大小红线

代码文件 MUST 控制单文件大小和单函数长度，以保持可读性。

#### Scenario: 单文件不超过 400 行

- **WHEN** src/ 下的某个 .py 文件超过 400 行
- **THEN** MUST 拆分为模块包
- **NOTE** 本次重构聚焦 api/、services/、rag/、chat/ 层，`infra/db/` 等基础设施层遗留的大文件（如 mysql_db.py 782 行）在后续迭代中逐步治理

#### Scenario: 单函数不超过 80 行

- **WHEN** 某个函数（含私有方法）超过 80 行
- **THEN** MUST 拆分为多个子函数

### Requirement: 测试目录镜像 src/ 结构

tests/ 的目录结构 MUST 镜像 src/ 的模块组织，方便定位对应模块的测试。

#### Scenario: tests 目录与 src 目录一一对应

- **WHEN** src/ 下新增一个模块包
- **THEN** tests/ 下 MUST 有对应的测试目录

#### Scenario: 平铺测试文件归入子目录

- **WHEN** 测试文件不归属任何 src/ 子模块
- **THEN** 可以保留在 tests/ 根目录（限 conftest、跨层工具测试）

### Requirement: SSE 格式化独立模块

SSE 事件格式化函数 MUST 集中在独立模块中，不混入路由文件。

#### Scenario: SSE 函数在 sse_utils 中

- **WHEN** 需要生成 SSE 事件文本
- **THEN** 调用 `src.api.sse_utils` 中的函数，路由文件不包含 SSE 格式化逻辑

### Requirement: 中间件不依赖业务层

中间件 MUST 直接依赖基础设施，不经过业务编排层。

#### Scenario: auth 中间件直接使用 Redis

- **WHEN** auth 中间件需要验证 token
- **THEN** 通过 `src.infra.redis_client` 获取 Redis 连接，不走 `services.AppService`
