# tool-registry Specification

## Purpose

将 `make_rag_tools` 硬编码的工具列表升级为可插拔工具注册表，统一工具的注册、启停与依赖注入，为外挂其他知识库/功能（tools/MCP 加载）与未来 subagent 工具（多 Agent 阶段）留出扩展接口。

## ADDED Requirements

### Requirement: 工具注册表

系统 SHALL 提供工具注册表（ToolRegistry），支持按工具名注册/注销工具定义（名称、描述、参数 schema、执行函数、依赖注入），并返回当前启用的工具列表供 LLM 绑定。

#### Scenario: 注册并启用工具

- **WHEN** 系统启动时依次注册 retrieve_kb / ask_user / search_web
- **THEN** 注册表返回启用工具列表，LLM 绑定这组工具执行 agent 循环

### Requirement: 工具按粒度启停

系统 SHALL 支持按单个工具启用/停用（如现有 `WEB_SEARCH_ENABLED` 语义泛化为任意工具的配置开关），停用工具不出现在 LLM 可见的工具列表中。

#### Scenario: 停用搜索工具

- **WHEN** 配置停用 search_web
- **THEN** search_web 从启用工具列表移除，LLM 不可见、不可调用

### Requirement: 扩展工具接入

系统 SHALL 允许在不修改 agent 主循环代码的前提下注册新工具（新知识库检索、外部系统能力等）；新工具 SHALL 复用现有工具上下文汇聚机制（`RequestContext.tool_contexts`）与引用编号体系。

#### Scenario: 注册新知识库工具

- **WHEN** 新增一个"销售知识库"检索工具并注册
- **THEN** 无需改动 `workflow.py` / agent 循环代码，新工具进入 LLM 工具列表并可正常产出引用

### Requirement: MCP 工具适配预留

系统 SHALL 为 MCP 工具接入预留统一入口（工具适配器），MCP 工具 SHALL 以与本地工具一致的 schema 形态注册进注册表，LLM 视角无差别。

#### Scenario: MCP 工具注册

- **WHEN** 接入一个 MCP server 并加载其工具
- **THEN** MCP 工具经适配器转换为注册表统一 schema 注册，可与其他本地工具一并启用/停用
