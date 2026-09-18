## ADDED Requirements

### Requirement: `/xxx` 前缀显式调用 skill

系统 SHALL 支持用户在消息中显式调用 skill：消息以 `/` 开头，其后为 skill 名，格式 `/name 剩余文本`（name = skill 名，剩余文本 = 该次调用的任务文本）。后端 SHALL 解析该前缀并路由到对应 skill，**不依赖模型判断**。前端 SHALL 在新建对话页与历史对话页提供**两个等价入口**：输入框行首 `/` 命令补全、输入区技能选择器（下拉，选中即插入 `/name `）；二者共用同一份候选数据与同一个后端解析，不新增第二条调用通道。

#### Scenario: 显式调用命中

- **WHEN** 用户发送 `/finance-qa 腾讯2024营收多少`
- **THEN** 系统解析出 skill=finance-qa、task="腾讯2024营收多少"，加载并执行该 skill，跳过"模型是否委派"的判断

#### Scenario: 未知 skill

- **WHEN** 用户发送 `/不存在的skill 问题`（形如命令）
- **THEN** 系统返回"skill 不存在 + 可用列表"，不当作普通文本静默处理

#### Scenario: 非命令前缀

- **WHEN** 消息不以 `/` 开头，或 `/` 后不构成命令形态（如 `/ 今天天气`）
- **THEN** 按普通文本处理，不报错

#### Scenario: 两个入口共用一条通道

- **WHEN** 用户从技能选择器（输入区下拉）选择技能，或手工输入 `/name`
- **THEN** 二者走同一份候选（`GET /api/skills`）与同一个后端前缀解析，不产生第二条调用路径

#### Scenario: 命令补全

- **WHEN** 用户在输入框行首输入 `/`
- **THEN** 展示可调用 skill 的候选列表，并随输入按名过滤

### Requirement: skill 加载后持续生效

`/xxx` 触发 SHALL 将 skill 内容作为一条隐藏消息注入会话上下文。**触发粒度为消息级，生效范围为会话级**：后续轮次 SHALL 仍然看到该 skill 内容，直到上下文压缩或新会话。

#### Scenario: 后续轮次仍受影响

- **WHEN** 用户第一条消息用 `/finance-qa` 加载财务问答规则，第二条消息未使用任何命令
- **THEN** 第二条消息仍受该 skill 内容影响（内容保留在会话上下文中）

#### Scenario: 注入内容对用户隐藏

- **WHEN** skill 内容注入会话上下文
- **THEN** 前端消息流不展示该注入消息（隐藏），但模型可见

### Requirement: skill 显式调用与智能体解耦

`/xxx` SHALL 只命名 skill，与当前会话选定的智能体无关。二者正交：智能体决定"谁执行"（会话级），skill 决定"按什么手册做"（消息级）。

#### Scenario: 任意智能体下均可调用

- **WHEN** 会话选定任意智能体（含默认），用户使用 `/skill-name`
- **THEN** 该 skill 被正常加载，不因智能体不同而受限或被改写

#### Scenario: 组合生效

- **WHEN** 会话选定"财务专家"且用户使用 `/finance-qa`
- **THEN** 财务专家以该手册的方法论执行本轮

### Requirement: 双轴调用控制

skill frontmatter SHALL 支持两个调用控制字段：`user-invocable`（默认 true；false 时禁止 `/xxx` 调用并从前端补全菜单隐藏）与 `disable-model-invocation`（默认 false；true 时禁止模型自动 `delegate_task` 调用并从其可用列表移除）。

#### Scenario: 禁用用户调用

- **WHEN** skill 声明 `user-invocable: false` 且用户使用 `/name`
- **THEN** 系统拒绝并提示该 skill 只能由模型调用，且该 skill 不出现在 `/` 补全菜单

#### Scenario: 禁用模型调用

- **WHEN** skill 声明 `disable-model-invocation: true`
- **THEN** 模型无法通过 delegate_task 调用它，且该 skill 不出现在 delegate_task 的可用列表

### Requirement: 双轴默认推导

当 skill 未显式声明两个控制字段时，系统 SHALL 依据其 `allowed-tools` 与工具的 `readonly` 属性推导默认：`allowed-tools` 为空或全为只读工具 → 双通道开放；含任一非只读工具 → 默认 `disable-model-invocation: true`（fail-safe）。

#### Scenario: 知识类 skill 双通道

- **WHEN** skill 未声明控制字段且其 allowed-tools 为空或全为只读
- **THEN** 用户可 `/xxx` 调用，模型也可自动调用

#### Scenario: 含写类工具默认锁模型端

- **WHEN** skill 声明了含非只读工具的 allowed-tools 且未显式声明 disable-model-invocation
- **THEN** 默认禁止模型自动调用，并记 warning 提示如需开放须显式写 `disable-model-invocation: false`

#### Scenario: 死 skill 告警

- **WHEN** skill 同时 `user-invocable: false` 且 `disable-model-invocation: true`
- **THEN** 加载期记 warning（既不可用户调用也不可模型调用）
