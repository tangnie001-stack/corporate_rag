## Why

当前 prompt 有三处结构性缺陷，且它们都源于**同一件事：没有任何文档规定"哪一类规则住哪一层"**（`docs/tmp/deep-research-prompt-management.md` 第一部分）。

**触发事件（溯源）**：本变更的直接起点是**用户在真实界面上的手工走查**，不是纸面推导。走查观察到三个现象：① 选定智能体后，内置的检索与联网兜底规则在最终 prompt 中消失（`trace_c54ce259` 的 `persona_source=preset` 为其日志证据）；② 同一个问题被反复检索、直到第 5 轮迭代上限仍无收敛；③ 最终答案为空（`answer_len=0`）。代码分析随后确认三者同源 —— base 层是杂物袋，被 persona 整体替换后系统规则一并丢失。该走查的有效步骤由其后的 change `e2e-playwright-regression` 固化为回归用例（其 proposal「承接 P1 各 change 的人工 E2E/验收场景」一节）。

1. **载体与代码混杂**：19 个 prompt 常量以 Python 字符串字面量写在 `src/config/prompts.py`（322 行），产品侧无法编辑，任何文案调整都要走代码评审与发版。
2. **检索协议住错了层，被 persona 覆盖掉**：`FINANCIAL_SYSTEM_PROMPT`（`prompts.py:42-64`）把 role + 处理流程 + 检索与联网兜底 + 回答规则**全塞在 base 层**，而 `src/rag/prompt.py:48-53` 的 `if persona: base = persona` 会**整体替换**它。后果已实证：`trace_c54ce259` 中 `persona_source=preset`，模型只拿到预设的 4 条工作原则 + 一句"必须先检索"，检索阶梯（规则 4–8，含唯一出口）**完全不在最终 prompt 里**。
3. **补丁复制时丢失出口**：`KB_BOUND_RETRIEVAL_DISCIPLINE`（`prompts.py:74-77`）是规则 2 的**部分拷贝** —— 保留了"必须先检索 / 不要预先猜测问题是否在知识库范围内"，却删掉了"全部明显不相关则按第 4 条处理"这句出口指引。同一事实有两个 owner，且拷贝版漂移。

同时，架构定位需要澄清：本项目**是通用问答助手，现阶段偏财务**，而非财报专用。目标形态是"选定知识库 → 加载该领域的 prompt；未选 → 通用回答"，后续可扩展到人事等领域。这要求 base 段**可被领域内容替换**，而运行时契约（工具、来源选择、完成条件、输出形态）**恒定不被覆盖**。

## What Changes

- **BREAKING** prompt 载体从 Python 字符串常量迁到 **YAML 文件**（`config/prompts/`）。P0 阶段要求**最终 prompt 逐字不变**（纯搬运）。终态预留远端读取（产品维护），本期为 Git 内文件、开发维护。
- **BREAKING** 确立 **6 段模型**（对齐 WeKnora 九段中本项目适用的 6 段），每段有唯一 owner：
  `base`（角色/领域方法）/ `runtime_contract`（数据·指令边界 + 完成条件）/ `sources`（来源选择：检索与联网）/ `tools`（通用工具约定）/ `skills`（按需加载的方法论）/ `output`（输出形态 + 引用编码）。
- **BREAKING** persona 语义由"整体替换 base"改为"**只填 `base` 段**，运行时各段恒定追加"。
- **检索阶梯从 `base` 搬到 `sources` 段**（它描述的是"跨调用的工具使用习惯"，不是"我是谁"）；`KB_BOUND_RETRIEVAL_DISCIPLINE` 的漂移拷贝随之消解（同一事实回到唯一 owner）。
- **领域 prompt 绑定**：`base` 段按三源优先级解析 —— 用户选定的智能体预设 **>** 知识库绑定的领域 prompt **>** 内置默认 base。未绑定知识库时用内置默认（通用）。
- **内容对齐 WeKnora（P2）**：base 瘦身；补三条本项目缺失的规则 —— ① 完成即停止调用工具（`A progress update alone does not complete the task`）；② 已返回的完整内容不需要再读；③ 数据与指令边界声明。
- **不做 i18n**：WeKnora 的模板含 4 语言块，本项目为中文单语。结构上预留位置，本期不引入多语言工程。

## Capabilities

### New Capabilities

- `prompt-carrier`: prompt 的载体形态与加载方式 —— YAML 文件结构、加载入口、占位符规则、i18n 与远端读取的预留位。

### Modified Capabilities

- `prompt-composition`: 把既有的"**三层组装**"细化为"**六段模型**"（② 环境约束层内部再分 `runtime_contract` / `sources` / `tools` / `output` 四段，① 人设层 = `base`，③ 运行时层 = `skills` + verify 指引）；人设层解析由隐含的单源改为显式的**三选一（替换）**并补知识库领域绑定；环境约束层补**完成条件**、**数据·指令边界**、**逐条条件注入**；新增段归属、已返回内容不重复读取、分段字节数日志三条要求。

> ⚠ 归属说明：`prompt-composition` 是在效 capability（2026-09-18 由 `session-agent-and-skill-invocation` 归档时并入）。本变更**在其之上做增量**，不另立一套分层规范 —— 六段与三层是**嵌套**关系（见 spec 的表格与说明）。

## Impact

**代码**

- 新增 `config/prompts/*.yaml`（base / runtime_contract / sources / tools / output；skills 段沿用现有 `skills/*/SKILL.md`）
- 新增 prompt loader（YAML → 段字典 → 组装）
- `src/config/prompts.py` — 19 个常量迁出；保留类型契约与常量引用点
- `src/rag/prompt.py:26-77` — `build_system_prompt` 改为按段组装 + `base` 三选一（**替换**）解析；persona 语义由"整体替换 base"改为"填 `base` 段"（语义未变，但 base 已被瘦身，故不再丢系统规则）
- `src/agents/graph/agent_node.py:80-88` — 从 `ctx` 读取领域解析结果
- `src/services/agent_service.py:919-921` 附近 — 解析绑定的 KB 领域并写入 `RequestContext`
- `src/infra/llm/request_context.py` — 新增领域字段（含来源/范围/用途注释 + `child()` 复制）
- `src/agents/graph/verify/regen_decision.py` — verify 注入的两段指引改为按启用工具条件渲染

**数据模型**

- `knowledge_base` 新增 `domain` 列（默认 `general`），作为"知识库 → 领域 prompt"的唯一事实源；需一个 migration。

**测试**

- `tests/rag/test_prompt_layers.py:24`（persona='' 逐字不变）与 `:59`（`assert "基础段正文" not in content`）—— **后者保护的就是被替换掉的缺陷，属契约变更，必须改写**
- 新增 prompt 契约测试（按外部第 2 层手段，请求载荷断言）：① 选中预设后最终 system 文本仍含检索阶梯要素；② 未注册的工具名不出现在最终 prompt；③ `build_system_prompt` 产出的工具名集合 ⊆ 实际注册工具名集合
- 段字节数断言（对齐 WeKnora 的 `[Agent][Prompt] section=... bytes=...`）

**文档**

- `CLAUDE.md` 文档组织表 — 若新增归属文档需登记
- `docs/agents/glossary.md` — 新增段模型相关术语（base / runtime_contract / sources / domain）
- `docs/agents/logging-rules.md` — prompt 分段日志字段登记
- `docs/agents/requirements_pool.md:143-146` — 该处主张"prompt 不应与代码同库、Langfuse 唯一权威"。**本变更不改其方向，而是分两阶段实现它**（Git 内 YAML → 远端读取），需在 ADR 中说明，避免被读成路线反复。

**需重采的基线**

- 端到端 prompt 快照（P1 完成后）
- RAGAS eval 基线（P2 完成后）

**跨变更的归属声明（防两边都改或都不改）**

`KB_BOUND_RETRIEVAL_DISCIPLINE` 常量与检索阶梯（`FINANCIAL_SYSTEM_PROMPT` 规则 4–8）的**段位与措辞**归**本变更**。change `retrieval-fetch-and-dedup` 只在 code 侧提供可用信号（精排分数、去重丢弃量、分路计数），**不修改这两处任何文本**。两者并行执行时文件不重叠。

**明确不在本变更范围**

- steering（用户插话）—— 中间插话的整体代码不存在
- handle / protocol 段 —— 已决定不抄（见 `docs/tmp/kb-boundary-visibility-problem.md` 附录 D）
- memory 段 —— 已由消息层 `_truncate_history`（`agent_node.py:30-59`）承担
- 远端 prompt 服务与产品侧编辑器 —— 终态，本期只预留读取接口
- 检索取数口径（另见 change `retrieval-fetch-and-dedup`）
