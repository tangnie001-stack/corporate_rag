## Context

**现状**（`docs/tmp/deep-research-prompt-management.md` 第一部分穷举）：

prompt 有 7 类来源：静态常量（`src/config/prompts.py` 19 个）、Langfuse 远端（3 个，无版本固定）、`agents/*.md` 智能体预设（**整体替换** base）、`skills/*/SKILL.md`（叠加、跨轮持久）、运行时组装（`src/rag/prompt.py:26-77`）、工具 docstring、`cli/compare_rewrite.py` 的重复分支。

组装顺序（`src/rag/prompt.py`）：

```
if persona:            base = persona          （:48-53）★ 整体替换
else:                  base = 内置基础段
+ KB_BOUND_RETRIEVAL_DISCIPLINE  if kb_bound and persona   （:58）
+ DELEGATE_GUIDANCE_SECTION      if has_skills or not persona（:64）
+ KB_UNBOUND_SYSTEM_PROMPT       if not kb_bound          （:68）
+ INLINE_CITATION_INSTRUCTION    （_FALLBACK_SYSTEM_PROMPT 内）
```

**约束**：

- **没有归属规则**：`prompts.py:41` 的注释自述基础段含"一律先检索 / 含核心实体才算相关 / 二次检索加大候选 / 确认无法覆盖才联网兜底"，即把 role + 流程 + 工具使用习惯 + 输出规则混在一层。
- **同一条事实有两个 owner**：`KB_BOUND_RETRIEVAL_DISCIPLINE` 是规则 2 的部分拷贝，且拷贝时删掉了出口指引（"全部明显不相关则按第 4 条处理"）。
- **注释已记录当时的推理且该推理已被证伪**：`prompt.py:54-57` 写"persona 为空时人设层即 FINANCIAL_SYSTEM_PROMPT，其处理流程 2–9 已含先检索后作答；无条件注入会破坏默认行为逐字不变（端到端快照）需求"。
- **`tests/rag/test_prompt_layers.py:59` 断言 `"基础段正文" not in content`** —— 该断言保护的正是"预设替换掉基础段"这一缺陷。
- **既有否决**：`docs/tmp/deep-research-prompt-management.md` 第四部分明确否决引入 Jinja2（dify 双语法并存的教训），占位符只用"仅标识符式替换、未知变量原样保留"。
- **既有立场**（`docs/agents/requirements_pool.md:143-146`）：主张"运行时管理优于 Git 管理"、"Langfuse 作为唯一权威来源"、"避免双轨制"。

**同域参照**（WeKnora，生产验证）：

- 载体：`config/prompt_templates/*.yaml`（11 个文件），模板含 `id` / `name` / `description` / `mode` / `default` / `i18n`（4 语言）/ `content`。
- 段模型：九段 —— `base` / `steering` / `runtime_contract` / `sources` / `tools` / `skills` / `output` / `memory` / `protocol`；唯一入口 `BuildSystemPromptSections`，空段丢弃，每段打 `[Agent][Prompt] section=... bytes=...`。
- 归属原则（其文档末节 + deepseek-harness 的 `every fact in the prompt has exactly one owner`）：角色与方法 → `base`；查什么 → `sources`；怎么调用 → 工具定义 / `tools`；输出形态 → `output`；引用编码 → `protocol`；**并明写"不要把同一条规则复制到多个模板中来'加强优先级'"**。
- 自定义正文**只替换 `base`**，运行时各段强制保留；条件注入按**实际注册的工具集合**判断（注释明说"不用配置开关"）。
- base 极薄：rag 模式只有 5 行（角色 + 4 条 KB 检索习惯 + 一句"以运行时来源规则为准"），**不含检索阶梯、不含联网规则、不含停止条件** —— 那些都在 `runtime_contract` / `sources`。

**架构定位澄清**：本项目是通用问答助手（现阶段偏财务），目标形态是"选定知识库 → 加载该领域 prompt；未选 → 通用回答"，后续可扩展人事等领域。

## Goals / Non-Goals

**Goals:**

- prompt 与 Python 代码解耦：文案改动不再需要改 `.py`（P0）。
- 建立段模型与归属表，使"一条事实只有一个 owner"可执行、可测试（P1）。
- 让 base 可被领域内容替换，而运行时契约恒定不被覆盖（P1）。
- 消除"检索协议被 persona 覆盖"与"补丁拷贝漂移"两个实证缺陷（P1）。
- 补齐三条本项目缺失、且直接命中已实证问题的运行时规则（P2）。
- 为终态的远端读取（产品维护）预留接口。

**Non-Goals:**

- 不引入 i18n（中文单语，结构预留即可）。
- 不实现远端 prompt 服务与产品侧编辑器。
- 不引入 Jinja2 或任何模板条件/循环语法。
- 不做完整 section registry + 命名 order 表（deepseek-harness 路线，P3，暂不建议）。
- 不改检索取数口径（另见 change `retrieval-fetch-and-dedup`）。
- 不做 steering / protocol / memory 三段。

## Decisions

### D1：载体选 Git 内 YAML，分两阶段；终态为远端读取

**候选**：

| 方案 | 代价 | 收益 |
|---|---|---|
| A 保留 Python 常量 | 产品无法编辑；文案改动走代码评审与发版 | 零迁移 |
| B 外置为 Markdown 文件 | md 无结构化字段（id/mode/段名），需靠文件名与前置约定 | 可读性好 |
| C **Git 内 YAML** | 引入一个加载器与文件契约 | 有结构化字段；与 WeKnora 同形态；可平滑迁到远端 |
| D 直接上 Langfuse / DB | 现在没有产品角色与远端服务；prompt 变运行时数据 → 编译期无检查、code review 看不到、测试难覆盖 | 一步到位 |

**选 C。** 理由：

- **本期要解决的是"与代码混杂"，不是"管理权归属"。** A 解决不了前者，D 现在没有使用方。
- **YAML 有结构**（`id` / `mode` / `段名` / `content`），是 B 缺的；而 WeKnora 已在生产上用同一形态，路径已被验证。
- **与 `requirements_pool:143-146` 不冲突，是它的第一阶段**：`Git 内 YAML（开发维护）→ 远端读取（产品维护）`，终态与该处主张一致。加载入口 SHALL 设计成可替换的数据源，使第二阶段只换实现、不动段模型。
- 取 D 的时机（复查触发条件）：出现"产品需要独立改文案且不能走发版"的实际需求时。

**占位符规则**：只做**仅标识符式**替换（`{identifier}`），未知变量原样保留 —— 沿用既有否决（不引入 Jinja2）。

### D2：6 段模型与归属表

| 段 | owner（内容边界） | 本项目现状 | WeKnora 对应 |
|---|---|---|---|
| `base` | 角色 / 领域方法（指标口径、报告期、同比等） | `agents/*.md` **替换** + `FINANCIAL_SYSTEM_PROMPT` 混装 | `base` |
| `runtime_contract` | 数据·指令边界、当前文档范围、**完成条件（完成即停调工具）** | 缺（`KB_BOUND_RETRIEVAL_DISCIPLINE` 只有半条） | `runtime_contract` |
| `sources` | **来源选择**：何时检索 / 何时换词再检 / 何时联网 / 何时如实说明 | **缺**（检索阶梯错位在 `base`） | `sources` |
| `tools` | 通用工具使用约定（具体参数留在工具定义里） | 工具 docstring + 散落 | `tools` |
| `skills` | 按需加载的方法论 | `skills/*/SKILL.md` 注入，但走 user message | `skills` |
| `output` | 输出形态、引用编码、完成检查 | `INLINE_CITATION_INSTRUCTION` | `output` + `protocol` |

**跳过的三段及理由**：

| 段 | 不做的理由 |
|---|---|
| `steering` | 用户插话（中途补充指令）的整体代码不存在 |
| `memory` | 已由消息层承担（`_truncate_history`，`agent_node.py:30-59`），再开一段是重复 owner |
| `protocol` | 引用编码沿用 `output` 段；WeKnora 的 `protocol` 服务于 handle 机制，本项目已决定不抄（`docs/tmp/kb-boundary-visibility-problem.md` 附录 D） |

**为什么必须是"6 段 + 无条件注入"，而不是"两层"**：检索阶梯被 persona 覆盖的根因不是"替换动作"，而是**阶梯住错了层**。同一条事实若住在会被覆盖的层，任何替换语义都会丢掉它。证据：`FORK_EXECUTION_CONTRACT` 一开始就放在"子代理段"（无条件追加），所以 fork 那条路从未出过这个问题。

**⚠ 与既有"三层"规范的关系（不是两套，是嵌套）**：在效 capability `prompt-composition`（2026-09-18 由 `session-agent-and-skill-invocation` 归档并入）已规定 system prompt 按**三层**组装。本变更的**六段与之嵌套**，不另立一套：

```
┌─ system prompt ─────────────────────────────────────────────┐
│  ① 人设层（可替换）                                          │
│    └─ base 段        ← 领域 base 或 preset 正文（三选一）      │
│  ② 环境约束层（系统强制，不可被 preset / 领域内容覆盖）        │
│    ├─ runtime_contract 段   数据·指令边界 / 完成条件           │
│    ├─ sources 段            来源选择 / 检索阶梯                │
│    ├─ tools 段              工具调用时机 / 委派                │
│    └─ output 段             输出形态 / 引用编码                │
└─────────────────────────────────────────────────────────────┘
（③ 运行时层 —— 不进 system prompt）
     ├─ skills 段            ← [[skill-injection]] 隐藏 user 消息
     └─ verify 指引          ← 运行期注入的 SystemMessage
```

本变更对 `prompt-composition` 的修改即：把"三层"细化为上表（② 内部再分四段）、把 base 解析写成显式的三选一（替换）、并补三条 ADDED（段归属 / 已返回内容不重复读取 / 分段字节数日志）。

### D3：`base` 段按**三选一（替换）**解析

```
选定智能体预设        → preset 正文
否则知识库领域已知    → 该领域的默认 base
否则                 → 内置通用 base
```

**候选**：**三选一（替换）** / 领域 base + 预设叠加 / 预设优先而领域被忽略。

**选三选一（替换）。** 理由：

- **替换 + base 瘦身是成对的**：原缺陷不是"替换"这个动作，而是 **base 是杂物袋**（`FINANCIAL_SYSTEM_PROMPT` 十二条规则含唯一出口）。base 瘦身到"人设 + 领域方法"后，被替换掉的不再包含任何系统规则 —— 替换因此是安全的。Anchoring：WeKnora 自定义正文**替换** `base`（`internal/config/agent_prompts.go` 的 `if body != "" { return body }`，注释"never rewrite user content"；`BuildSystemPromptSections` 三选一），其 `base` 也只装"角色 + 领域工作流"。
- **叠加会把重复 owner 固化进 prompt**：领域 base 与 preset 可能就同一件事各说一遍（如"关注报告期口径"），叠加会把两份都留着，违反"一条事实只有一个 owner"（ADR-0003 原则）。
- **在效规范已定"替换"**：`prompt-composition`（2026-09-18 并入）原文"选定智能体后 SHALL **替换**为该 AgentPreset 的正文"。本决策与之一致，不构成规范冲突。

**⚠ 明确接受的代价**：选定预设后，**知识库领域方法不参与组装** —— "选了财务专家 + 绑定人事库"时人事领域方法消失。系统记日志但不阻断。这是替换语义的既定代价，**不是缺陷**（后人不应把它当 bug 去修）。

**数据来源**：`knowledge_base` 新增 `domain` 列（默认 `general`），作为"知识库 → 领域 base"的唯一事实源；理由：KB→领域是需要被管理页展示与编辑的一等关系，放配置映射会与 kb_id（UUID）耦合且不可查询。未绑定 KB 或领域未知 → 回退内置通用 base。

### D4：不提供"叠加/合并"开关

**候选**：(a) 只做替换；(b) 替换 + 显式"追加领域 base"开关；(c) 先 (a)，观察后定 (b)。

**选 (a)。** 理由：叠加方案的重复 owner 问题是（b）绕不开的固有代价；在没有实际"两者都要"的需求之前引入开关，属预判性扩展（违反项目「最小改动」）。若将来真出现"预设与领域都要保留"的场景，再引入显式开关并规定冲突时的裁决规则。

### D5：分三阶段落地，每阶段一道闸门

| 阶段 | 内容 | 闸门（必须绿才进下一阶段） |
|---|---|---|
| **P0 载体** | Python 常量 → YAML；加载器；**逐字搬运** | **最终 prompt 逐字不变**：现有 prompt 层测试必须原样通过，预期值不得修改 |
| **P1 归属** | 6 段拆分；检索阶梯搬到 `sources`；`base` 改**三选一（替换）**解析；`domain` 列 + 领域 base；verify 两段指引改条件渲染 | 契约测试绿 + 端到端 prompt 快照**重采**（人工确认） |
| **P2 内容对齐** | base 瘦身；补三条缺失规则（完成即停 / 已返回不需再读 / 数据·指令边界） | RAGAS eval 对比（此时结构已固定，质量变化可单独归因） |

**P0 的"逐字不变"闸门是硬要求**：它是唯一能证明"搬运无损"的手段。若 P0 与 P1 合并，就再也无法区分"文本变了"是搬运引入的还是结构改动引入的。

⚠ **该闸门必须 pin 到本地路径**：现有测试 `tests/rag/test_prompt_layers.py:24` 断言的是 `messages[0].content == pm.get_system_prompt()`，而 `get_system_prompt()` 是 **Langfuse 远端 + 本地兜底**，且远端侧取 latest 不固定版本（见 Risks「远端/本地双事实源」）。闸门测试若不显式使用本地兜底，结果不可重复 —— 远端一改就红，无法作为"搬运无损"的证明。

### D6：`skills` 段保持现有注入通道，且不参与 system prompt 组装

现状是 skill 以 `[[skill-injection]]` 前缀的 user message 注入（`agent_service.py:1095-1120`），不是 system 段。**本期不改这个通道**：改动它会影响 skill 的跨轮持久语义与既有测试，且与本变更目标（段归属）无关。

段模型里 SHALL 保留 `skills` 段位并标明"承载通道 = 消息层"，但该段 SHALL NOT 参与 system prompt 的拼接 —— 否则"六段一起组装"与"skill 走 user message"两条要求无法同时成立。

### D7：归属表落点 —— 新建 `docs/agents/prompt-ownership.md`

**候选**：a) `docs/agents/rules.md` 加一节；b) 新建专项档；c) 并入 `docs/agents/glossary.md`。

**选 b。** 理由：

- 它是**需要长期维护的规则表**（会被代码评审与实现反复引用），与 `logging-rules.md` 同级同性质 —— 后者正是"格式唯一归属"的专项档，是现成的先例。
- `rules.md` 已承载"异常处理 / 响应包装 / 日志约定 / 排查规范 / 代码注释标准"，再塞一段归属规则会让它继续膨胀（该文件已接近需要拆分）。
- `glossary.md` 是**术语**表，归属规则不是术语，放进去会让它变成杂物档。

按项目规约：新建归属文档必须同步登记进 `CLAUDE.md` 的「文档组织」表，否则视为未完成。

### D8：态 A（未绑定 KB）取"结构不变"，不取"逐字不变"

**冲突**：`runtime_contract`（含完成条件）与 `output` 段是**无条件注入**，因此"未绑定知识库"会话（态 A）的 system 段内容必然变化；而"库边界"项 §3.1 的 **G4** 要求"态 A 的 system 段**逐字不变**"。

**候选**：

| 方案 | 做法 | 代价 |
|---|---|---|
| **甲** | G4 改为"**结构不变**"：态 A 仍是两条 system 消息、角色序列不变，内容是新的 | G4 的原始断言要改写 |
| 乙 | 库边界项先落地，其快照基线在本变更之后再重采 | 把基线管理复杂度推给后续，且顺序变成 change 2 反而要等库边界项 |
| 丙 | 把 `runtime_contract` / `output` 也做成"仅绑定时注入" | **削掉两段的核心价值** —— 完成条件与数据·指令边界跟"是否绑库"无关，纯对话同样需要 |

**选甲。** 理由：G4 的原始意图是"别把纯对话搞坏"，而不是"文本一个字节都不许动"。"结构不变"（两条 system 消息、角色序列不变）已经守住那个意图；丙会让"模型 5 轮全在调工具、`answer_len=0`"这类失败形态在纯对话下依然无解（缺的正是完成条件）。

**落地**：G4 的措辞在"库边界"项建 change 时按其执行；本变更只需保证态 A 仍产出两条 system 消息，并加一条断言防止"顺手把 `KB_UNBOUND_SYSTEM_PROMPT` 吃掉"。

### D9：`sources` 段按"逐条规则条件渲染"，不做整段开关

**候选**：整段开关（工具集变化 → 整段换一份文本）/ 逐条条件渲染（每条规则挂"依赖哪个工具"的标注，缺工具则该条不输出）。

**选逐条。** 理由：本项目的检索阶梯是**一条规则一个动作**（换词再检 / 联网 / 如实说明）。整段开关会导致"只因缺 `search_web`，就把'换词再检'和'如实说明'一起删掉" —— 而这两条与联网无关。WeKnora 的 `sources` 也是逐工具条件追加（`grounding_prompt.go:35-60`）。代价是每条规则要带工具依赖声明 —— 那正是归属表本来就要登记的东西。

### D10：领域粒度取"一库一域"

**候选**：一库一域（混装列为复查触发条件）/ 库内多域（`domain` 变数组或多对多）/ 取消 KB→域绑定（领域只由用户选定的预设决定）。

**选一库一域。** 理由：第三个候选会让"选定知识库 → 自动加载该领域 prompt"这个目标形态彻底落空（那正是本架构的目的）；第二个候选是为尚未出现的需求付数据模型复杂度，且**库内多域时"该加载哪个领域 base"本身没有唯一答案** —— 那是产品问题，不是建模问题。实测 `b9e74e82` 已混装东软+腾讯两种年报，但两者同属"财务"领域，不构成反例；"财务+人事同库"的出现是复查触发条件（见 Open Question 1）。

## Risks / Trade-offs

- **[两条现有测试必然失败]** → `tests/rag/test_prompt_layers.py:24`（persona='' 时首条 system 消息与 `prompt_manager` 输出逐字相同 —— ⚠ 它比的是 `pm.get_system_prompt()`，**不是静态快照**）与 `:59`（`assert "基础段正文" not in content`，即"基础段必须消失"）。二者保护的正是要改的行为。**改测试不是"为了让测试过"，是契约本身变了** —— 必须在提交信息里写清，不可静默改断言。
- **[`pm.get_system_prompt()` 非确定性]** → 该方法是 Langfuse 远端 + 本地兜底，远端侧取 latest。任何以它为准的闸门测试都必须显式走本地兜底，否则不可重复（见 D5 的 pin 说明）。
- **[`prompt.py:54-57` 的注释理由已证伪]** → 该注释的前提（"persona 非空时由预设自己承载等价指引"）与 `trace_c54ce259` 的实测相反。P1 时同步重写注释（按项目规约：注释写当前机制，不写变更历史）。
- **[端到端快照重采是人工步骤]** → P1 完成后必须人工确认基线；这是本变更唯一无法自动化的验收点。
- **[`domain` 列迁移]** → 新增列默认 `general`，存量 KB 全部落入"通用领域"，不破坏现状；回滚即忽略该列。
- **[条件注入需要工具集传到 verify 注入点]** → `VERIFY_GUIDANCE_PROMPT` / `VERIFY_HINT_PROMPT` 由 `regen_decision.py` 在运行期注入，是 `build_system_prompt` 之外的路径。P1 必须把启用工具集合传到这里，否则"下掉 `search_web` 但指引仍在"的问题只修了一半。
- **[远端/本地双事实源 —— 已实测确认，直接决定 P0 闸门的覆盖面]** 事实：走远端的是**恰好 3 个** prompt（`financial-system-prompt` / `user-prompt-template` / `classifier-prompt`，`src/infra/llm/prompt_manager.py:69-73`）；**无版本固定**，URL 只拼 name 取 latest（`:117-123`），返回的 version 只进日志不参与请求（`:126`）；按 name 缓存 **60s**（`:77,:103`）；且 **`LANGFUSE_ENABLE=true` 且远端有同名文本时远端恒优先于本地兜底**（`:156-160`）—— 即"改了本地 YAML 不生效，日志也不会告诉你 YAML 没被用"。

  **当前实际状态**：`src/config/settings.py:276` 默认 `"true"`，但 `.env:63` 设为 `false`；运行时实测 `LANGFUSE_ENABLE=False`、日志出现 `[llm] prompt fallback name=financial-system-prompt` → **这 3 个 prompt 实际走本地兜底，本地是当前的事实源**。

  **结论**：P0 的"逐字不变"闸门在本部署下成立，**但它是靠一个 env 开关维持的，不是靠机制**。这使"3 个远端 prompt 的归属"从"独立问题"变成**决定 P0 覆盖面的问题** —— 见 Open Question 2。
- **[token 与段字节数]** → 6 段无条件注入会净增 system 段体积（`_truncate_history` 的预算只作用于历史，不含 system）。缓解：每段打字节数日志（对齐 WeKnora 的 `section=... bytes=...`），P2 后复核总量。
- **[`docs/agents/requirements_pool.md:143-146` 可能被读成被推翻]** → 在 ADR-0002 里明确"分两阶段实现同一方向"，并在该文档对应条目加一句阶段说明，避免后人误判路线反复。

## Migration Plan

**与 change `retrieval-fetch-and-dedup` 的顺序**：**对端先落地**；本变更的 **P0（YAML 载体搬运）可并行开工**（零行为差异、零文件交集）。**P1/P2 必须排在对端之后** —— P1 与 P2 各要重采一次基线（prompt 快照 + RAGAS eval），而基线所测的 context 内容正是对端在改的东西，先采会直接作废。

按 D5 的三阶段推进，每阶段独立可回滚：

1. **P0**：新建 `config/prompts/*.yaml`，把 19 个常量逐字搬入并分好段名；加载器读 YAML 产出与原常量**逐字节相同**的字符串；跑端到端快照，预期零 diff。
2. **P1**：拆分 base；搬检索阶梯到 `sources`；`base` 改三选一（替换）解析；`knowledge_base.domain` 迁移 + 解析层；verify 注入点接工具集；改写两条契约测试；重采快照。
3. **P2**：base 瘦身 + 补三条规则；跑 RAGAS eval 对比 P1 后的基线。

**回滚**：P0 回滚 = 加载器切回常量（YAML 文件保留不生效）；P1 回滚 = 恢复 `if persona: base = persona` 与旧段布置（快照基线需一并回退）；P2 回滚 = 恢复 base 文本。`domain` 列可保留不用。

## Open Questions

1. **检索阶梯的版本**：完整保留规则 4–8（含"第二次检索显式传 top_k=10"这类具体动作），还是精简为 3 条（何时重试 / 何时联网 / 何时如实说明）？⚠ **依赖 change `retrieval-fetch-and-dedup` 的第 5 节产出**（它给出"库内有无内容"的双形态判据与分数区间）—— 该结论未出之前不应定稿 `sources` 段的措辞。
2. **Langfuse 侧 3 个 prompt 的归属与 P0 闸门覆盖范围**：见 Risks「远端/本地双事实源」。**待定**（第二轮问题）。
