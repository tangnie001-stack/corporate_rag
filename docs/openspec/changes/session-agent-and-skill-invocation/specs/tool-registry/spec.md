## MODIFIED Requirements

### Requirement: 工具注册表

系统 SHALL 提供工具注册表（ToolRegistry），支持按工具名注册/注销工具定义（名称、描述、参数 schema、执行函数、依赖注入、**读写属性 `readonly`**），并返回当前启用的工具列表供 LLM 绑定。`readonly` SHALL 由工具作者在注册时声明，作为 skill 双轴默认推导的事实来源（`readonly=True` 表示只读、无外部副作用；`False` 表示写/改/删/发/外部调用）。

#### Scenario: 注册并启用工具

- **WHEN** 系统启动时依次注册 retrieve_kb / ask_user / search_web
- **THEN** 注册表返回启用工具列表，LLM 绑定这组工具执行 agent 循环
- **AND** 每个工具条目携带其 `readonly` 声明（现有工具均为 True）

#### Scenario: 读写属性供双轴推导

- **WHEN** skill 加载期推导双轴默认
- **THEN** 按名查询其 allowed-tools 对应工具条目的 `readonly`；含任一 `False` 则默认关闭模型自动调用
