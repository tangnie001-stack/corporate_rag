# documentation-anti-rot Specification

## Purpose
TBD - created by archiving change check-docs-symbol-lookup-perf. Update Purpose after archive.
## Requirements
### Requirement: 锚点校验的单向口径

系统 SHALL 对 `docs/agents/` 下的受检文档执行锚点校验，覆盖**路径**（文档中的 `src/**` 引用在代码库是否存在）、**路由**（文档声明的 `METHOD /api/...` 是否有对应的 `@router` 注册）、**符号**（反引号内的标识符能否在 `src/` 检索到）三类。

校验 SHALL 为单向：文档声称存在 → 代码必须找得到；文档漏写新代码 SHALL NOT 判为错误。

**skill 工具名锚点**（`skills/*/SKILL.md` frontmatter 的 `allowed-tools` 是否已在代码注册）的归属与口径以 `skill-registry` 的「防腐校验」requirement 为准，本 capability 不另立规则。

#### Scenario: 路径锚点失效

- **WHEN** 文档引用 `src/**` 下某路径而该路径在代码库不存在
- **THEN** 产生 `error` 档结论，退出码为 1（拦截提交）

#### Scenario: 路由锚点失效

- **WHEN** 文档声明 `METHOD /api/...` 而 `src/api/` 下无对应 `@router` 注册
- **THEN** 产生 `error` 档结论

#### Scenario: 符号锚点未检索到

- **WHEN** 文档反引号内出现"应定义"命名形态（CamelCase / UPPER_SNAKE / 带下划线的 snake_case）的标识符，而在 `src/` 中检索不到
- **THEN** 产生 `warn` 档结论，且 SHALL NOT 影响退出码

### Requirement: 排除表可配置

系统 SHALL 从 `pyproject.toml` 的 `[tool.doc_anchors]` 读取排除表（`exclude_docs` / `exclude_paths` / `exclude_routes` / `exclude_symbols` / `exclude_skill_tools`），并 SHALL 内置 `requirements_pool.md` 与 `reference-projects.md` 的默认排除（意向清单与外部仓库引用）。

#### Scenario: 配置扩展排除项

- **WHEN** 在 `[tool.doc_anchors]` 的某排除表中新增一项
- **THEN** 命中该项的锚点 SHALL 被跳过，不再产生结论

### Requirement: 符号校验基于单次代码快照

**就符号锚点校验而言**，系统 SHALL 在一次检查运行内至多读取一次 `src/` 下 `.py` 文件的文本内容，全部符号锚点查询 SHALL 基于该次读取所形成的快照完成；系统 SHALL NOT 对每个符号重复遍历或重复读取代码库。

该快照 SHALL 仅存活于单次进程（随进程退出释放），SHALL NOT 落盘持久化，SHALL NOT 跨进程共享。

> 边界：路由收集器与工具名收集器各自只扫一次、不随符号数放大，其独立读盘**不受本要求约束**（本要求约束的是"随符号数放大的重复读取"）。

#### Scenario: 同一符号被多篇文档引用

- **WHEN** 同一个标识符出现在多篇受检文档中
- **THEN** 代码库文本只被读取一次，后续查询复用该快照

#### Scenario: 读取次数不随符号数增长（自动化守卫）

- **WHEN** 在同一进程内连续调用符号检索，调用次数远超代码库文件数
- **THEN** 对 `src/` 下 `.py` 的读取次数 SHALL 保持为常数（至多等于文件数），不随调用次数增长
- **AND** 该断言 SHALL 由**确定性用例**守卫（计数方式：替换 `pathlib.Path.read_text` 计数），SHALL NOT 采用耗时断言

#### Scenario: 新进程看到最新代码

- **WHEN** 修改 `src/` 下某文件后重新运行检查（新进程）
- **THEN** 快照包含该修改，判定结果反映最新代码

#### Scenario: 跨进程不共享快照

- **WHEN** 先后运行两次检查
- **THEN** 第二次运行重新读取代码库，不复用上一次进程的快照

### Requirement: 全量检查耗时上限

全量检查（对 `docs/agents/` 下全部受检文档执行锚点校验）SHALL 在 **60 秒**内完成。

该阈值的用途是拦截"退回逐符号重读"的性能回归：改造前实测约 497 秒，改造后本机基线约 5.5 秒。测量环境为项目根目录下的常规开发 / CI 文件系统。

#### Scenario: 全量检查在阈值内完成

- **WHEN** 在项目根运行 `python -m src.cli.check_docs`
- **THEN** 进程 SHALL 在 60 秒内结束

#### Scenario: 回归被拦截

- **WHEN** 符号校验被改回"每个符号遍历并读取一遍代码库"
- **THEN** 全量耗时将达到数百秒量级，超出本要求，即可判定规格被违反

### Requirement: 判定结果不依赖读取次数

符号锚点的判定 SHALL 仅依据"源码剥除行内注释后的文本"与"以 `[A-Za-z0-9]` 为词边界"的包含关系；判定结果 SHALL NOT 因快照实现、读取次数或遍历顺序而变化。同一次运行内所有查询 SHALL 基于同一份快照，使整轮结论自洽。

#### Scenario: 与逐行扫描等价

- **WHEN** 对同一份代码与文档，分别以"逐行扫描"与"单次快照"两种方式判定符号锚点
- **THEN** 两者产出的结论集合（文档、行号、锚点、档位）SHALL 完全一致

#### Scenario: 注释内的符号

- **WHEN** 某标识符只出现在源码的行内注释中
- **THEN** 该符号 SHALL 判为"未检索到"（注释不计入），与逐行扫描口径一致
