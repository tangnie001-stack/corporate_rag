## MODIFIED Requirements

### Requirement: AppService 直接持有全局依赖

`AppService` SHALL 直接持有 `chat_manager`（在 AppService 层创建，不经中间封装间接获取）。流式生成状态（任务注册表与事件缓冲）SHALL `AppService` 直接持有，不依赖额外进程。`AgentService` SHALL 在构造期确定 `llm` 和 `reranker`：构造参数传入实例时直接使用，缺省时回退到 `src.models` 的工厂（`get_llm` / `get_rerank`）。

词法检索不再作为独立组件被持有：其能力并入向量存储组件（两路同源于一个数据库实例）。

#### Scenario: 应用启动

- **WHEN** `AppService` 初始化
- **THEN** `chat_manager` 直接在 AppService 层创建，AgentService 通过构造函数注入获取

#### Scenario: 测试注入

- **WHEN** 测试构造 `AgentService`
- **THEN** 可直接传入 mock `llm`/`reranker`，无需 4 层 `@patch`

#### Scenario: 无独立词法索引组件

- **WHEN** 检查应用装配
- **THEN** SHALL NOT 存在独立的词法索引组件（无进程内索引对象、无索引文件路径配置）
