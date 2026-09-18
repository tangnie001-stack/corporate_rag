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

## 两条硬规则

1. **审提案与审 diff 是两件事**：④ 用 `architecture-review`（写文件**之前**），⑥ 用 `requesting-code-review`（改完**之后**）。二者不可互替。
2. **评审与验证是必配，不是按需**：其余支撑 skill 都可省，这两处不可省——单 agent 自审会漏。

## 相关

- 产物分工（ADR / specs / changes 谁管"为什么 / 怎么设计 / 怎么执行"）：`docs/adr/README.md`
- 质量门禁（pytest / ruff / pyright）：`CLAUDE.md`「验证」
- 术语（开发期工具链 skill vs 业务侧技能委派）：`docs/agents/glossary.md`
