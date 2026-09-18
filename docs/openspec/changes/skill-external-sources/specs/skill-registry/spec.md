## MODIFIED Requirements

### Requirement: Skill 文件结构

系统 SHALL 从项目根 `skills/<name>/SKILL.md` 加载 skill。

该路径 SHALL 是**唯一的读取面**，与 skill 的来源无关：手工放置的、以及由外部来源安装的（见 `skill-acquisition`），落盘后都 SHALL 以同一形态被识别。

扫描 SHALL 忽略以下 `.` 开头的目录（安装暂存目录），SHALL NOT 将其识别为 skill。

#### Scenario: 目录结构识别

- **WHEN** 扫描 `skills/` 目录
- **THEN** 每个含 `SKILL.md` 的子目录识别为一个 skill
- **AND** 目录名作为 skill 名

#### Scenario: 外部安装的 skill 与手工放置的无差别
- **WHEN** 一个 skill 由外部来源安装到 `skills/<name>/SKILL.md`
- **THEN** 它 SHALL 与手工放置的 skill 得到完全相同的解析与注册处理

#### Scenario: 暂存目录被忽略

- **WHEN** 扫描 `skills/` 目录且存在以 `.` 开头的暂存目录
- **THEN** 该目录 SHALL NOT 被识别为 skill，SHALL NOT 出现在 skill 列表中

#### Scenario: frontmatter 解析

- **WHEN** 解析 SKILL.md
- **THEN** 提取 frontmatter 字段：name、description、context、model、allowed-tools、user-invocable、disable-model-invocation、agent
- **AND** name 缺省时用目录名；description 缺省时用正文首段
- **AND** `name`（含缺省取自目录名时）**仅允许 ASCII slug** `^[A-Za-z0-9][A-Za-z0-9_-]*$`——`/xxx` 命令天然是 ASCII 惯例，非 ASCII 名会让 `/财报分析` 落进"非命令形态"分支被**静默**当普通文本；违反者加载期记 warning 并跳过
- **AND** 中文展示需求走 `description`，不塞进 `name`
- **AND** `allowed-tools` 以**逗号分隔字符串**书写，内部转为列表
- **AND** `agent`（可选）声明该 fork skill 的执行者预设名，覆盖会话选定值
- **AND** 已废弃的 `thinking` 与 `max-iterations` 字段不再被识别（历史写法忽略并记 warning；迭代上限改由执行者 agent 定义的 `maxTurns` 控制）
