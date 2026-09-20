# architecture-tidy Specification

## Purpose
TBD - created by archiving change architecture-refactoring-phase3. Update Purpose after archive.
## Requirements
### Requirement: 单一问答管道

系统 SHALL 仅有 LangGraph 一条问答管道，`RAGChain.chat_with_citations()` 及其依赖的子函数 SHALL 被删除。CLI eval SHALL 通过 `graph.ainvoke()` 执行问答评估。

#### Scenario: SSE 端点正常响应
- **WHEN** 客户端请求 `GET /chat/stream`
- **THEN** 系统通过 `AgentService.stream_chat()` 返回 SSE 事件流，不依赖 RAGChain

#### Scenario: CLI eval 执行
- **WHEN** 运行 CLI eval 脚本
- **THEN** eval SHALL 通过 `graph.ainvoke()` 获取 `answer` 和 `citations`

### Requirement: API 纯路由

`api/documents.py` SHALL 仅包含参数校验和路由转发，不包含分块富化、合并、全流程编排等业务逻辑。这些逻辑 SHALL 位于 `DocumentService` 中。

#### Scenario: 文档上传处理
- **WHEN** 用户上传文档
- **THEN** `api/documents.py` SHALL 调用 `DocumentService.process_document()` 处理，不自行编排解析/分块/入库流程

#### Scenario: 分块富化
- **WHEN** 文档需要反推 chunk 页码
- **THEN** `DocumentService.enrich_chunk_pages()` SHALL 是唯一实现，API 层无副本

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

### Requirement: ChatManager 统一 async 接口

`ChatManager` SHALL 仅保留 async 方法。sync 版 `get_history`/`add_message`/`clear_history` SHALL 被删除。

#### Scenario: 对话保存
- **WHEN** SSE 流结束保存对话
- **THEN** agent_service 使用 `add_message_async()` 保存用户和助理消息

#### Scenario: 会话清理
- **WHEN** 用户删除会话
- **THEN** sessions.py 使用 `clear_history_async()` 清理 Redis

### Requirement: 工具函数集中管理

纯工具函数（零项目依赖）SHALL 位于 `src/utils/` 目录。`infra/` SHALL 仅包含基础设施模块（连接外部系统、包装第三方库）。

#### Scenario: SSE 格式化工具
- **WHEN** 路由层需要格式化 SSE 事件
- **THEN** `from src.utils.sse import sse_token, sse_status, sse_citation, sse_done, sse_error`

#### Scenario: 异常定义
- **WHEN** 业务层需要抛出错误
- **THEN** `from src.utils.errors import BusinessError, SystemError`

### Requirement: Graph 层无 `asyncio.new_event_loop()`

Graph 节点函数 SHALL NOT 使用 `asyncio.new_event_loop()`。需调用 async 函数的节点 SHALL 自身为 async 函数。

#### Scenario: 检索节点执行
- **WHEN** `retrieve_node` 执行检索
- **THEN** 直接 `await search()`，不创建新事件循环
