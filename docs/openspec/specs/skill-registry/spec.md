# skill-registry Specification

## Purpose
TBD - created by archiving change agent-delegation-skills. Update Purpose after archive.
## Requirements
### Requirement: Skill 文件结构

系统 SHALL 从项目根 `skills/<name>/SKILL.md` 加载 skill。

#### Scenario: 目录结构识别

- **WHEN** 扫描 `skills/` 目录
- **THEN** 每个含 `SKILL.md` 的子目录识别为一个 skill
- **AND** 目录名作为 skill 名

#### Scenario: frontmatter 解析

- **WHEN** 解析 SKILL.md
- **THEN** 提取 frontmatter 字段：name、description、context、model、thinking、allowed-tools、max-iterations
- **AND** name 缺省时用目录名；description 缺省时用正文首段

### Requirement: SkillRecord 运行时对象
系统 SHALL 为每个加载的 skill 生成 SkillRecord，含 {name, description, context, inline_prompt, agent_prompt, model, thinking, allowed_tools, max_iterations, source_path}。

#### Scenario: context 值约束

- **WHEN** context 字段为 "fork" 或 "inline" 之外的值
- **THEN** 加载期按 "inline" 处理并记 warning

#### Scenario: 正文按 context 解析

- **WHEN** context=inline
- **THEN** 正文存为 inline_prompt（供主 agent 注入执行）
- **AND** inline_prompt 控制在短方法论规模（建议 ≤500 字）——内容注入后留在 messages 历史，长文会造成上下文累积膨胀；长文领域内容应建模为 fork skill（独立子代理上下文，不占主对话）
- **WHEN** context=fork
- **THEN** 正文存为 agent_prompt（供子代理 system_prompt）

### Requirement: 注册表加载与冲突
系统 SHALL 用 SkillRegistry 聚合所有 SkillRecord 并按名索引。

#### Scenario: 名称冲突

- **WHEN** 两个 skill 目录同名
- **THEN** 加载期报错（fail-fast），不静默覆盖

#### Scenario: to_tool_description

- **WHEN** 生成 delegate_task 的工具描述
- **THEN** 只列每个 skill 的名 + description（一句话 whenToUse）
- **AND** 超预算时按可用预算截断（渐进披露，完整内容命中才加载）

### Requirement: 懒重载
系统 SHALL 在 delegate_task 调用前检查 `skills/` 目录 mtime，变化则重扫注册表。

#### Scenario: skill 文件变更生效

- **WHEN** 新增/修改/删除 skill 文件且目录 mtime 变化
- **THEN** 下次 delegate_task 调用前注册表自动更新，无需重启

#### Scenario: 删除 skill

- **WHEN** delegate_task 请求一个已删除的 skill
- **THEN** 返回"skill 不存在"+ 可用列表，主 agent 降级自行回答

### Requirement: 防腐校验

系统 SHALL 将 skill 文件的工具名引用纳入文档防腐检查（check_docs 或独立 skill-check）。

#### Scenario: 工具改名后 skill 失效检出

- **WHEN** skill frontmatter 的 allowed-tools 引用了代码中已不存在的工具名
- **THEN** 防腐检查报错，提示更新 skill

#### Scenario: 排除既有误报

- **WHEN** skill 内容包含示例/伪代码中的工具名
- **THEN** 通过排除表控制，不作为腐化报出
