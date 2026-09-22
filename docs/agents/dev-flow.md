# 开发流程与 skill 选用

> 从「提出需求 / 报 bug」到「执行收尾」的六个环节，各环节该用哪个开发期工具链 skill。
> 本文只做**路由**：skill 自身内容见其 `SKILL.md`，不复述于此。

## 三段划分

六个环节归三段，每段**一个主 skill**，支撑 skill 按"遇到什么障碍"插入。带 `/` 前缀的只能由你打斜杠调用。

- **澄清** ① 需求 / bug 提出 → ② 完善需求
- **决策** ③ 出方案 → ④ 验证可行性
- **落地** ⑤ 生成文件 → ⑥ 执行

## 选用表

| 环节 | 主 skill | 支撑 / 必配闸门 | 何时插入 |
|------|---------|----------------|---------|
| ① 需求 / bug 提出 | 对话 + `docs/agents/requirements_pool.md` | — | 无 issue tracker，输入面即对话与需求池；不引入分诊类 skill |
| ② 完善需求 | `openspec-explore` | `grilling`；`/grill-with-docs`（边拷问边产 ADR / 术语） | 需求模糊需要逼问；要在拷问同时产出 ADR 或术语时改用后者 |
| ③ 出方案 | `openspec-propose` | `codebase-design`、`domain-modeling` | 需要定模块边界 / 领域模型时 |
| ④ 验证可行性 | `architecture-review` | `prototype`（跑起来证伪）；`research` 或 `firecrawl-deep-research`（补外部事实） | **必配**；数值或状态类假设纸上推不准时加 `prototype` |
| ⑤ 生成文件 | `writing-plans`（落 `docs/superpowers/plans/`）**或** `openspec-propose` 的 tasks | — | **二选一，不可都写**，否则一事两档 |
| ⑥ 执行 | `executing-plans` 或 `openspec-apply-change` | `verification-before-completion` → `requesting-code-review`；收尾 `finishing-a-development-branch` | 两道闸门必走 |

## ⑤ 的例外：分阶段变更是「change 承载规格 + plan 承载步骤」

跨子系统的大变更会同时用到 ⑤ 的两个产物，**不构成"一事两档"**。判据只有一条：**同一份文件里不出现两套可执行的步骤清单**。

- openspec change 只承载**为什么 + 规格**（`proposal.md` / `design.md` / `specs/`）
- `tasks.md` **不再承载任务清单**，退化为「**执行档索引**」：写明阶段划分，并指向各阶段的计划文件
- 实施计划按阶段落在 `docs/superpowers/plans/`，由 ⑥ 执行

### 判据一：是否跨子系统（满足任一即是）

- 触及 **≥2 个独立模块**（如 `src/infra/db` + `src/rag` + `src/chunking`）
- 触及 **≥2 个有真实行为变化的 capability**（纯命名同步不算）

### 判据二：跨子系统之后，是否该拆阶段（满足任一即该拆）

- **硬顺序依赖**——A 不做完，B 无从下手（底座先行）
- 需要**中途交付一个可运行状态**（不能"全部改完才第一次能跑"）
- 各阶段**验收标准不同**（如存储迁移等价性 vs 检索命中率），混在一起无法一次判定
- 与**在途 change 重叠**，需声明谁先行

> 口诀：**规模决定计划有多长；子系统数量与顺序依赖，决定要不要拆阶段。**

**实例**：`postgres-storage-consolidation` 跨了关系型存储 / 向量存储 / 词法检索 / 分块 / 取数融合 / 迁移 / 部署 / 依赖共八个面（proposal 自记"capability 面 14 个"），加之 P1 的 PG 底座必须先行、各阶段验收标准不同 → 拆 P1–P4，`tasks.md` 退化为索引。

**反例**：`langfuse-v2-downgrade` 有 60 条任务，却只跨"可观测后端部署"一个工作流，`src/` 零改动、无硬顺序、无中途交付需求 → **不分阶段**，一份 `tasks.md` 到底。

## ⑥ 执行器三选一：看 ⑤ 的产物 + 工作性质

| 执行器 | 对应 ⑤ 产物 | 适用 |
|--------|-----------|------|
| `openspec-apply-change` | openspec change 的 `tasks.md` | 变更带 spec/capability 语义；需要归档并把 delta 合并进主规格 |
| `subagent-driven-development` | `docs/superpowers/plans/*.md` | **有子代理时的计划执行，默认选它** |
| `executing-plans` | 同上 | 无子代理时；或要把执行换到并行会话 |

### 不适合走 SDD 的情形

`subagent-driven-development` 的模型是「**隔离 worktree 内、可逐任务独立评审的代码任务** → 结尾合并」。以下四类改用 `openspec-apply-change`，或按计划人工执行：

1. **无代码 diff 的工作** —— 纯部署 / 配置 / 数据操作（改 compose、改 env、跑迁移、操作运行中的容器）。worktree 隔离与结尾 merge 在这里没有意义。
2. **含不可逆或破坏性操作** —— drop 数据库、删卷、删数据。这类操作在 SDD 里属**必须停下追问**的停止条件，与它"任务之间不 check in、连续执行"的取向相抵。
3. **任务紧耦合、切不出独立评审单元** —— SDD 的前置就是"任务大体彼此独立"。
4. **改动面不落在这棵工作树上** —— 如面向远程环境或运行中容器。

一句话判据：**SDD 要的是"能切成独立任务、能在 worktree 里评审、结尾能 merge"的代码工作**；三者缺一就别选它。（各执行器内部机制见其 `SKILL.md`。）

### 执行器不代跑闸门

⑥ 的两道闸门**不由执行器代跑**，而三个执行器内嵌它们的程度不同（实测各 `SKILL.md`）：

| 执行器 | `verification-before-completion` | `requesting-code-review` | `finishing-a-development-branch` |
|--------|:---:|:---:|:---:|
| `subagent-driven-development` | ❌ | ✅（Final Review 派 reviewer） | ✅（Finish） |
| `executing-plans` | ❌ | ❌ | ✅ |
| `openspec-apply-change` | ❌ | ❌ | ❌（改为提示 `archive`） |

`verification-before-completion` **三个执行器均未提及** —— 它是**贯穿式纪律**（任何"成功"声明前当场取证），不是某一步，不适用"被执行器带到"。

**含义**：走 **openspec 线**时两道闸门**必须显式触发**（skill 不会替你跑）；走 SDD 时 code review 与收尾会被自动带到，但验证纪律仍须自己守。**换执行器换的是主 skill，不换闸门** —— 「两道闸门必走」是 ⑥ 的阶段级约束。

## 两条硬规则

1. **审提案与审 diff 是两件事**：④ 用 `architecture-review`（写文件**之前**），⑥ 用 `requesting-code-review`（改完**之后**）。二者不可互替。
2. **评审与验证是必配，不是按需**：其余支撑 skill 都可省，这两处不可省——单 agent 自审会漏。

## 相关

- 产物分工（ADR / specs / changes 谁管"为什么 / 怎么设计 / 怎么执行"）：`docs/adr/README.md`
- 质量门禁（pytest / ruff / pyright）：`CLAUDE.md`「验证」
- 术语（开发期工具链 skill vs 业务侧技能委派）：`docs/agents/glossary.md`
