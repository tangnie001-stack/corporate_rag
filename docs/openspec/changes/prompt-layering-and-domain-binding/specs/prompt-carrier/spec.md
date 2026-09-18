## ADDED Requirements

### Requirement: YAML 载体与目录结构

系统的 prompt 内容 SHALL 存放在 `config/prompts/` 下的 YAML 文件中，而非 Python 源码字面量。每个模板文件 SHALL 以 `templates:` 为根，每个模板 SHALL 包含 `id`（kebab-case 标识符）、`content`（正文）与可选的结构化元数据。

模板正文 SHALL 使用 YAML 块标量（`content: |`）保存原始换行与缩进，使文本内容与源码字符串逐字对应。

#### Scenario: 模板可从 YAML 读取
- **WHEN** 加载器读取 `config/prompts/` 下的模板文件
- **THEN** SHALL 得到以 `id` 为键的模板映射，每个模板的 `content` 与 YAML 中的块标量逐字相同

#### Scenario: 新增文案不改 Python
- **WHEN** 需要修改某个 prompt 的文案
- **THEN** SHALL 只需修改 `config/prompts/` 下的 YAML 文件，不改动任何 `.py`

### Requirement: 加载入口与占位符规则

系统 SHALL 提供唯一的 prompt 加载入口：YAML → 段映射 → 组装的 prompt 文本。加载入口 SHALL 是段模型唯一的读取点。

占位符 SHALL 只支持**仅标识符式**替换（形如 `{identifier}`）。系统 SHALL NOT 引入条件、循环或表达式语法；未在上下文中提供的占位符 SHALL 原样保留，不得被替换为空字符串。

#### Scenario: 未提供的占位符原样保留
- **WHEN** 模板含 `{query}` 而调用方未提供该变量
- **THEN** 输出文本 SHALL 原样包含 `{query}`，而非空串

#### Scenario: 不引入模板表达式语法
- **WHEN** 模板正文含 `{% if %}` 或 `{{ }}` 之类的表达式写法
- **THEN** 系统 SHALL 将其视为普通文本原样输出，不做求值

### Requirement: 载体替换的逐字不变量

把 prompt 从 Python 常量迁移到 YAML 的过程 SHALL 保持最终 prompt 文本逐字不变。该不变量 SHALL 由端到端 prompt 快照测试证明，且该测试的预期值 SHALL NOT 被修改来适配迁移结果。

#### Scenario: 迁移后最终 prompt 逐字相同
- **WHEN** prompt 载体完成从 Python 常量到 YAML 的迁移
- **THEN** 端到端 prompt 快照 SHALL 与迁移前完全一致，不接受任何预期值调整

#### Scenario: 逐字不变量先于结构调整
- **WHEN** 还需要调整段归属或改写内容
- **THEN** 逐字不变量的测试 SHALL 先通过，之后才允许开始结构调整

### Requirement: 载体数据源可替换（远端读取预留）

加载入口 SHALL 与数据源解耦：模板来源应可替换为远端数据源（面向产品侧维护），而段模型与组装逻辑不变。

系统 SHALL NOT 在本期内实现远端数据源，但加载入口的接口形态 SHALL 使其成为一个实现替换而非结构改动。

#### Scenario: 数据源可替换而不改段模型
- **WHEN** 模板来源由本地文件改为远端数据源
- **THEN** 段模型、段归属与组装顺序 SHALL 保持不变，仅数据读取实现被替换

#### Scenario: 本期不实现远端读取
- **WHEN** 检查本期的实现范围
- **THEN** SHALL 只存在本地 YAML 数据源，不含远端服务的调用代码

### Requirement: i18n 结构预留但不启用

模板 SHALL 在结构上允许按语言键组织正文，但系统 SHALL NOT 在本期内引入多语言选择或语言回退逻辑。

#### Scenario: 本期按单语言解析
- **WHEN** 加载一个模板正文
- **THEN** 系统 SHALL 直接取单一正文，不进行语言选择
