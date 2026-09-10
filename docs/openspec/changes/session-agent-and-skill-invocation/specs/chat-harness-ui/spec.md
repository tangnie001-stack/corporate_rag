## ADDED Requirements

### Requirement: 智能体选择器（新对话页）

新对话页 SHALL 在**知识库选择器右侧**提供智能体选择器（胶囊按钮 + 单选下拉菜单，与知识库选择器同构），列出可用智能体（含默认项）。默认项文案 SHALL 为「**默认**」（对应未选择 ＝ 系统默认人设）。选择器 SHALL 提供当前智能体名与描述，并在菜单内提示"选定后本会话内不可更改"。

#### Scenario: 默认未选择

- **WHEN** 新建会话进入新对话页
- **THEN** 智能体选择器显示「默认」，为未选中态（次级字色）

#### Scenario: 位于知识库选择器右侧

- **WHEN** 新对话页渲染知识库选择器与智能体选择器
- **THEN** 两者位于同一行，智能体选择器在知识库选择器右侧

#### Scenario: 单选选中

- **WHEN** 用户从菜单选择"财务专家"
- **THEN** 胶囊显示"财务专家"（主蓝），该会话以财务专家身份服务

#### Scenario: 历史对话页也可选

- **WHEN** 用户在历史对话页新建会话
- **THEN** 智能体选择器同样可用（与知识库选择器一致）

#### Scenario: 无可用预设

- **WHEN** `agents/` 目录缺失或为空
- **THEN** 选择器仅提供「默认」项，不报错

### Requirement: 技能选择器（输入区）

输入区 SHALL 在**深度思考开关右侧**提供技能选择器（同构 chip + 向上弹出的下拉菜单），列出可用户调用的技能（`user-invocable ≠ false`）。选择 SHALL 将该技能以 `/name ` 字面量插入输入框行首（与手输 `/` 等价），并同步 chip 为选中态；未选择时 chip 显示中性文案「技能」。

该选择器 SHALL 与输入框行首 `/` 补全**共用同一候选数据与同一后端解析**，不新增第二条调用通道。新对话页与历史对话页 SHALL 均提供。

#### Scenario: 选择技能插入字面量

- **WHEN** 用户从技能菜单选择 `finance-qa`
- **THEN** 输入框行首插入 `/finance-qa `，chip 变为选中态并显示该技能名

#### Scenario: 位置与深度思考并列

- **WHEN** 输入区渲染底部工具条
- **THEN** 技能选择器与深度思考开关位于同一行，技能选择器在其右侧

#### Scenario: 隐藏不可用户调用的技能

- **WHEN** 某 skill 声明 `user-invocable: false`
- **THEN** 该 skill 不出现在技能选择器菜单

#### Scenario: 清除选择

- **WHEN** 用户在菜单选择「不使用技能」
- **THEN** 输入框行首的 `/name` 前缀被移除，chip 恢复中性文案「技能」

#### Scenario: 无可用技能

- **WHEN** 没有任何可用户调用的技能
- **THEN** chip 仍可点击，菜单显示空态提示，不隐藏 chip

### Requirement: 智能体会话级不可变

智能体 SHALL 在新建会话时选定，**会话内不可修改**；要更换智能体 SHALL 新建会话重新选择。历史对话页顶栏 SHALL 回显该会话的智能体名（未选择时不显示或显示默认）。

#### Scenario: 会话内不可更改

- **WHEN** 用户在已开始的会话中尝试更换智能体
- **THEN** 选择器不可用，提示需新建对话

#### Scenario: 历史会话回显

- **WHEN** 打开一个选定"财务专家"的历史会话
- **THEN** 顶栏回显"财务专家"

### Requirement: 输入框 `/` 命令补全

输入框 SHALL 在行首输入 `/` 时展示可调用 skill 的候选列表（按名过滤），选中后插入 `/name ` 字面量；不展示 `user-invocable: false` 的 skill。

#### Scenario: 行首 `/` 触发候选

- **WHEN** 用户在输入框行首输入 `/`
- **THEN** 展示可调用 skill 候选列表

#### Scenario: 过滤与插入

- **WHEN** 用户继续输入 `/fin`
- **THEN** 候选按名过滤；选中"finance-qa"后输入框插入 `/finance-qa `

#### Scenario: 隐藏仅模型可调用的 skill

- **WHEN** 某 skill 声明 user-invocable: false
- **THEN** 该 skill 不出现在候选列表
