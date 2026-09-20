# ADR-0010：Langfuse 侧 3 个 prompt 出列，本地模板为唯一事实源

- **Status**：Accepted
- **Date**：2026-09-21
- **Deciders**：用户（决策）；Claude（调研、实测与评审）
- **关系**：**不取代 ADR-0002**。它是 ADR-0002"第一阶段"内部的一个子决策 —— 把"唯一事实源"这一性质从终态提前到现在。

## 背景与问题

`src/infra/llm/prompt_manager.py:69` 的 `PROMPT_NAMES` 只对**恰好 3 个** prompt 名走远端：`financial-system-prompt` / `user-prompt-template` / `classifier-prompt`（其余 16 个常量只能改代码）。这 3 个远端读取的性质：

- **无版本固定**：URL 只拼 name 取 latest；返回的 version 只进日志、不参与请求（`:117-126`）
- 按 name 缓存 60s
- **`LANGFUSE_ENABLE=true` 且远端有同名文本时，远端恒优先于本地兜底** —— 改了本地文案不生效，日志也不会告诉你本地没被用（`:156-160`）

**实测当前部署**：`src/config/settings.py` 默认 `"true"`，但 `.env` 设为 `false`；运行时日志出现 `[llm] prompt fallback` → **这 3 个 prompt 实际走本地兜底，本地是当前的事实源，但这是靠一个 env 开关维持的，不是靠机制。**

**为什么这件事在本轮变成必须决策**：change `prompt-layering-and-domain-binding` 的 P0 阶段以"最终 prompt **逐字不变**"为**唯一**闸门（用于证明载体搬运无损，见 ADR-0002 的"前置闸门约束"）。若该闸门成立与否取决于 `LANGFUSE_ENABLE` 的取值、且远端只取 latest，则同一次提交在开发机绿、在别人机器上红 —— 闸门失去意义。

## 候选方案

| 方案 | 做法 | 代价 | 收益 |
|---|---|---|---|
| A | 不动 | —— | 零成本 |
| B | 保留远端读取，把 latest 改为固定 label / 版本 | 需要 Langfuse 侧有版本管理流程；本地仍非唯一源 | 远端可复现 |
| C | **3 个 name 出列（从读取名单移除），本地模板为唯一事实源** | 失去"线上改 prompt 即生效"的热回滚路径；出列后回滚 = 回滚镜像 | P0 闸门靠机制而非开关；与 ADR-0002 的"唯一事实源"方向一致 |

## 决策

**选 C。** `PROMPT_NAMES` 中的三个远端名不再走远端读取，本地模板（`src/config/prompts/*.yaml`）成为**唯一事实源**。

**保留 `PromptManager` 的远端读取实现**（不删除代码路径），使终态接入时是"加回名单 + 固定版本"，而不是重写。

## 理由

**最关键的一行：闸门必须靠机制，不能靠一个 `.env` 开关。** "逐字不变"是本变更唯一能证明"搬运无损"的手段；它的可重复性不能取决于部署环境的开关取值，也不能被一个只取 latest 的远端在任意时刻打红。

- **A** 把闸门的可重复性留在环境差异上。
- **B** 解决了可复现，但需要 Langfuse 侧存在版本 / label 流程；本期没有维护远端 prompt 的角色，等于为一个不存在的流程付成本，且本地仍不是唯一源（与 ADR-0002 的"本地 fallback 必须是一等资产"要求相抵）。
- **C 与 ADR-0002 不冲突**：ADR-0002 规定的是"终态为远端读取、加载入口与数据源解耦"；本决策只把"单一事实源"这一性质从终态提前到第一阶段，终态方向不变。

**连锁范围（不是"删 3 行名单"）**：`get_user_template`（`:198`，`_get` 在 `:211`）与 `get_classifier_prompt`（`:214`，`_get` 在 `:237-238`）仍按 `PROMPT_NAMES` 索引；`_FALLBACK_SYSTEM_PROMPT`（`:31`）以 `FINANCIAL_SYSTEM_PROMPT` 为前缀。因此还牵动 `tests/infra/llm/test_prompt_manager_fallback.py`、`tests/infra/test_prompt_manager.py`，以及 5 个定义 `PromptManager` 桩的 graph 测试。完整清单落在 change `prompt-layering-and-domain-binding` 的 Impact 与 tasks。

## 后果

**正面**：

- P0 的"逐字不变"闸门可重复、不依赖环境变量。
- 本地模板成为唯一事实源，消除"线上版本与仓库不一致"这一类不可复现问题。
- 去掉一个外部依赖对 prompt 内容的影响面。

**负面 / 接受的代价**：

- **失去线上 prompt 热改回滚**：出列后，prompt 出问题的回滚手段是**回滚镜像**，而不是"在 Langfuse 改回旧版本"。这在 P0–P2 期间会放大运维成本。
- 需要同步改动 `get_user_template` / `get_classifier_prompt` / `_FALLBACK_*` 与若干测试（见「连锁范围」）。
- 与 `docs/agents/requirements_pool.md:143-146` 的"Langfuse 作为唯一权威来源"在**本期**处于相反状态（该处主张的终态不变，见复查条件）。

**不解决的问题**：

- 不实现远端 prompt 服务与产品侧编辑器（仍是 ADR-0002 的终态）。
- 不解决"提交级 prompt 版本化"（若将来需要，属远端侧的 label / version 设计）。
- 不改变 `PromptManager` 的远端读取实现本身。

## 复查触发条件

- **出现"产品需要独立改文案且不能走发版"的实际需求** → 启动 ADR-0002 的第二阶段：**加回名单并固定 label / 版本**（不得再用 latest）。
- **P0–P2 期间因 prompt 缺陷发生线上事故，且回滚镜像的代价不可接受** → 重新评估是否临时恢复远端读取。
- **`requirements_pool:143-146` 被正式采纳为"必须立刻上 Langfuse"** → 与 ADR-0002 的复查条件同款处理（第一阶段的结构与契约不必推翻）。
