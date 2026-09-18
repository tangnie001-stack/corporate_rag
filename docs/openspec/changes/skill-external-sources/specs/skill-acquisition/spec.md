## ADDED Requirements

### Requirement: skill 外部来源获取

系统 SHALL 支持从外部来源获取 skill 并落到本地读取面 `skills/<name>/SKILL.md`，使落盘后的 skill 与手工放置的 skill 对加载器**无差别可用**。

本期 SHALL 支持三类来源形态：

- **直连单文件**：一个指向 `SKILL.md` 的 HTTP/HTTPS URL；skill 名取自 frontmatter 的 `name`
- **归档**：zip 或 tar（URL 或本地路径），内含一个或多个 `<name>/SKILL.md`
- **git 仓库**：仓库 URL + 可选子目录与 ref；浅克隆后取该子目录

三类来源 SHALL 归约为同一中间形态（一组"名称 + SKILL.md 内容 + 可选附属文件"），校验与落盘 SHALL 只有一份实现。

#### Scenario: 直连单文件安装
- **WHEN** 给定一个指向 SKILL.md 的 HTTPS URL
- **THEN** 该文件被落到 `skills/<name>/SKILL.md`（`name` 取自 frontmatter），且随即被注册表识别为一个 skill

#### Scenario: 归档含多个 skill
- **WHEN** 给定一个内含两个 `<name>/SKILL.md` 的 zip
- **THEN** 两个 skill 均被落盘并可被识别

#### Scenario: 归档内含非法条目时整体拒绝
- **WHEN** 归档中任一条目名含 `../` 或为绝对路径
- **THEN** 整个归档 SHALL 被拒绝，SHALL NOT 落盘任何文件

### Requirement: 获取过程中的校验

获取 SHALL 在**落盘前**完成全部校验，复用既有的 skill 契约（不新写一套解析）：

- frontmatter 必填字段与类型（见 `skill-registry`）
- `name` 为 ASCII slug（`^[A-Za-z0-9][A-Za-z0-9_-]*$`）
- 与既有 skill 名冲突时 fail-fast，SHALL NOT 静默覆盖
- 归档解包后的总字节数与条目数不超过配置上限

任一项不通过 → 整体失败，SHALL NOT 落盘部分结果。

#### Scenario: 名冲突时拒绝且不覆盖
- **WHEN** 外部来源的 skill 名与 `skills/` 下既有 skill 同名
- **THEN** 安装 SHALL 失败并给出明确冲突信息，既有 skill SHALL 保持不变

#### Scenario: frontmatter 不合法时拒绝
- **WHEN** 外部来源的 SKILL.md 缺 `name` 且目录名不是合法 slug
- **THEN** 安装 SHALL 失败，且 SHALL NOT 落盘任何文件

#### Scenario: 解包超限时拒绝
- **WHEN** 归档解包后的总字节数或条目数超过上限
- **THEN** 安装 SHALL 失败，SHALL NOT 解包到磁盘

### Requirement: 失败不留残留与原子落盘

获取与校验 SHALL 在临时暂存目录完成，仅在全部校验通过后才改名到 `skills/<name>/`。临时目录 SHALL 与目标位于同一文件系统。

暂存目录 SHALL 位于 `skills/` 下且以 `.` 开头，使加载器扫描时忽略它 —— 半成品不得被识别为 skill。

任一环节失败 → 暂存目录 SHALL 被清理，`skills/` 下 SHALL NOT 留下部分结果。

#### Scenario: 校验失败后无残留
- **WHEN** 安装在校验阶段失败
- **THEN** `skills/` 下 SHALL NOT 出现该 skill 的目录，也 SHALL NOT 留下暂存目录

#### Scenario: 暂存目录不被当作 skill
- **WHEN** 加载器扫描 `skills/`
- **THEN** 以下 `.` 开头的暂存目录 SHALL 被忽略，不出现在 skill 列表中

### Requirement: 本期不执行任何脚本

获取 SHALL 只做"取到本地"，SHALL NOT 执行 skill 附带的任何脚本、SHALL NOT 安装依赖、SHALL NOT 起沙箱。

对本期内含可执行内容的来源，系统 SHALL 明确拒绝并给出原因（`"本期只获取、不安装"`），SHALL NOT 静默忽略该部分内容。

收紧 SHALL 只作用于安装路径；`SkillLoader` 对本地目录的既有行为 SHALL NOT 改变。

#### Scenario: 含脚本的来源被明确拒绝
- **WHEN** 外部来源除 SKILL.md 外还含可执行脚本
- **THEN** 安装 SHALL 失败并说明"本期只获取、不安装"，而非静默丢弃脚本后成功

#### Scenario: 本地手工放置的 skill 不受影响
- **WHEN** 用户在 `skills/` 下手工放置一个含脚本的 skill 目录
- **THEN** 既有加载行为 SHALL 保持不变

### Requirement: 来源可追溯

每次安装 SHALL 记录来源元数据（来源类型、来源标识、安装时间），存放在该 skill 目录下**独立于 SKILL.md 的记账文件**中，SHALL NOT 写入 SKILL.md 的 frontmatter。

#### Scenario: 安装后来源可查
- **WHEN** 从任一来源安装一个 skill
- **THEN** 该 skill 目录下存在记账文件，含来源类型与来源标识

#### Scenario: 不污染内容文件
- **WHEN** 检查安装后的 SKILL.md
- **THEN** 其 frontmatter SHALL NOT 因安装而增加来源相关字段
