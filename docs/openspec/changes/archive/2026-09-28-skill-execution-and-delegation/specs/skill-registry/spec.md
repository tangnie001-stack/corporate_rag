# skill-registry Specification (Delta)

## MODIFIED Requirements

### Requirement: Skill 文件结构

系统 SHALL 从项目根 `skills/<name>/SKILL.md` 加载 skill。

#### Scenario: 目录结构识别

- **WHEN** 扫描 `skills/` 目录
- **THEN** 每个含 `SKILL.md` 的子目录识别为一个 skill
- **AND** 目录名作为 skill 名

#### Scenario: frontmatter 解析

- **WHEN** 解析 SKILL.md
- **THEN** 提取 frontmatter 字段：name、description、context、model、allowed-tools、user-invocable、disable-model-invocation、agent
- **AND** name 缺省时用目录名；description 缺省时用正文首段
- **AND** `name`（含缺省取自目录名时）**仅允许 ASCII slug** `^[A-Za-z0-9][A-Za-z0-9_-]*$`——`/xxx` 命令天然是 ASCII 惯例，非 ASCII 名会让 `/财报分析` 落进"非命令形态"分支被**静默**当普通文本；违反者加载期记 warning 并跳过
- **AND** 中文展示需求走 `description`，不塞进 `name`
- **AND** `allowed-tools` 以**逗号分隔字符串**书写，内部转为列表
- **AND** `agent`（可选）声明该 fork skill 的执行者预设名，覆盖会话选定值
- **AND** 已废弃的 `thinking` 与 `max-iterations` 字段不再被识别（历史写法忽略并记 warning；迭代上限改由执行者 agent 定义的 `maxTurns` 控制）

### Requirement: SkillRecord 运行时对象

系统 SHALL 为每个加载的 skill 生成 SkillRecord，含 {name, description, context, context_source, inline_prompt, fork_body, agent, model, allowed_tools, user_invocable, disable_model_invocation, source_path}。`context_source` 记录该 skill 的 `context` 取值来源（`explicit` = 作者显式声明 / `auto_oversize` = 因正文超限自动按 fork / `default` = 缺省取 inline），供排障判定。

#### Scenario: context 未声明取 inline

- **WHEN** SKILL.md 未声明 `context` 字段，且正文长度未超 `INLINE_PROMPT_MAX_CHARS`
- **THEN** 该 skill 按 `inline` 处理（与上游默认一致：不写即 inline），`context_source=default`

#### Scenario: 正文超限自动按 fork

- **WHEN** SKILL.md 未声明 `context`，且正文长度超过 `INLINE_PROMPT_MAX_CHARS`
- **THEN** 加载期按 `fork` 处理（正文存为 `fork_body`），`context_source=auto_oversize`，并记 warning 说明"正文 N 字符超出 inline 预算、已自动按 fork 处理；如确需 inline 请显式声明 `context: inline` 并精简正文"
- **AND** 理由：长正文注入后留在 messages 历史会挤占历史预算（原 F-13 问题）；把该规则放在**加载期按长度自动判定**，使外部来源的 skill 无需人工补 frontmatter 即可被正确承载（与"外部 skill 保持上游原样"的原则一致），同时保持"不写即 inline"的上游默认

#### Scenario: 显式声明优先于自动判定

- **WHEN** SKILL.md 显式声明 `context`
- **THEN** 以显式值为准、`context_source=explicit`，不再参与超限自动判定
- **AND** 显式声明 `context: inline` 且正文超限时，保持 inline 并记 warning（超限守卫测试按硬约束断言，由该测试承担失败）

#### Scenario: context 非法值

- **WHEN** context 字段为 "fork" 或 "inline" 之外的值
- **THEN** 加载期抛错并跳过该 skill，记 warning
- **AND** 不静默降级到任何一侧（降级到 inline 是静默失效、降级到 fork 会让作者以为声明生效），与非法 `name` 的处理风格一致

#### Scenario: 正文按 context 解析

- **WHEN** context=inline
- **THEN** 正文存为 inline_prompt（供主 agent 注入执行）
- **WHEN** context=fork
- **THEN** 正文存为 fork_body（供子代理作为 **user message** 的任务/方法论内容）

#### Scenario: 长内容的建模去向

- **WHEN** 一份 skill 的正文超出 `INLINE_PROMPT_MAX_CHARS`
- **THEN** 加载期记 warning 提示作者处理（未显式声明时同时按上一场景自动改用 fork）
- **AND** 长内容 SHALL 优先拆分到 skill 目录下的附随文件（渐进披露：正文留骨架，细节按需读取）——但附随文件读取工具尚未提供（见 requirements_pool F-12），故在此之前长文分析型 skill 落在 fork 路径
- **AND** 长文与 `context: fork` 的绑定是**上下文预算**理由，不构成"长文必须 fork"的规则；`context: fork` 的适用判据是自包含且不需要中途用户输入（见 `delegate-task` 的直出与确认门）

#### Scenario: 双轴控制字段默认推导

- **WHEN** skill 未显式声明 user-invocable / disable-model-invocation
- **THEN** 加载期依据 allowed-tools 与工具 readonly 属性推导默认值（见 skill-invocation capability），并写入 SkillRecord 的 user_invocable / disable_model_invocation
