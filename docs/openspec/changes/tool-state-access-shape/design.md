## Context

**现状（已逐行核对）**：本仓有 5 处工具取数走**属性访问**图状态——

| 位置 | 取什么 | 用途 |
|---|---|---|
| `src/agents/tools/rag_tools.py:102` | `state.kb_id` | `retrieve_kb` 决定检索哪个库 |
| `src/agents/tools/rag_tools.py:187` | `state._agent_iterations` | 检索信号日志的迭代序号 |
| `src/agents/tools/ask_tools.py:82` | `state.query` | `ask_user` 澄清问题上下文 |
| `src/agents/tools/ask_tools.py:83` | `state._agent_iterations` | 同上迭代序号 |
| `src/agents/tools/ask_tools.py:166-167` | `state.kb_id` | 澄清候选维度加载 |

全仓 `InjectedState` 只出现在这两个文件（`web_tools.py` 的 `search_web` 无 `InjectedState`；`delegate_task` 非 `InjectedState` 工具）。

**实测（对照探针，本仓 venv / `langchain 1.3.11`）**：同一工具，外层自建 `StateGraph` + `ToolNode` 注入 `AgentState` **实例**（属性可用，取到 `kb-1` / `3`）；`create_agent(state_schema=<dataclass>)` **与**默认 schema **都**注入 **`dict`** ⇒ `AttributeError: 'dict' object has no attribute 'kb_id'`。该异常被 `handle_tool_errors=True` 吞成普通 `ToolMessage` 错误内容 ⇒ 检索恒空、澄清恒失败，**图照常跑完、日志上看不出异常**。

**触发者与排序**：`skill-execution-and-delegation` 的 P2（D7/D8 子代理工具面默认继承）会让 fork 子代理首次拿到 `retrieve_kb`，其 P2 验收当场踩空；`one-loop-two-roles`（主循环统一到 `create_agent`）同样依赖，但它排在 `skill-execution-and-delegation` 整体之后 ⇒ **本变更必须独立并抢在前者 P2 之前**。

**约束**：行为保持（检索/澄清语义不变）；`CLAUDE.md` 的「显式类型检查」规则（类型不确定不用 `getattr` 隐式兜底）；不改任何在途变更的文件。

## Goals / Non-Goals

**Goals:**

- 工具取数与**承载它的图实现**解耦：两种形状下都取到相同的值，SHALL NOT 抛 `AttributeError`。
- 缺字段时**显式降级**，且降级**可观测**（不得静默取空）。
- 补齐"`create_agent` 承载下取数正确"的断言（当前全仓 0 覆盖）。

**Non-Goals:**

- 不改工具的检索/澄清/维度加载语义，不改其对外行为（除"崩溃→降级 + warning"这一处修正）。
- 不把 `create_agent` 换掉、不改两个在途变更的调用方。
- 不新增工具、不动 `skills/` 内容。

## Decisions

### D1 共用一个**显式形状访问器**，不在 5 处各写分支

- 决策：新增 `src/agents/tools/state_access.py` 提供 `read_state_field(state, name, default)`；内部按 `isinstance(state, dict)` 分流（dict 分支 `state.get(name, default)`；`None` 分支返回 `default`；其余走声明字段的属性读取）。5 处改为调用它。
- 理由：形状判定只实现一次，5 处不再重复；便于单测；新增工具时不易漏。
- 备选：**5 处各写 4 行显式分支** → 否决：20 行重复，且将来新增工具必然有人漏写。备选：**统一适配层把 dict 转成 dataclass 再注入** → 否决：要在图外层拦截状态注入，改动面远大于收益，且与两个在途变更的装配面冲突。

### D2 关于 `getattr` 的规则解读（**有意为之，需评审确认**）

`CLAUDE.md` 明令「类型不确定的值不用 `getattr(x, "attr", default)` 隐式兜底」。本决策在访问器**内部**的 dataclass 分支使用了属性读取，理由是：**类型已经由 `isinstance` 显式判定**，与该规则要禁止的"类型不确定就兜底"是两种情形。为把意图写死，访问器内 SHALL：

- 先判 `isinstance(state, dict)` → dict 分支取 `.get(name, default)`
- 再判 `state is None` → 返回 `default`
- 其余分支才读属性，并在注释中写明"此分支已由前两判排除，属性来自已声明的 schema 字段"

若评审认为这仍违反规则字面，替代实现是 5 处各写显式分支（D1 的备选）——**不做静默取舍，按评审结论定**。

### D3 缺字段必须"显式降级 + 记 warning"

- 决策：任一字段缺失（dict 分支取不到 / 属性读取失败）时按缺失处理（沿用既有默认值语义），并**记一条 warning**，含工具名与缺失字段名。
- 理由：现状是抛 `AttributeError` —— **至少作为工具错误在对话与 trace 里可见**；若改成"静默按缺失处理"，失败模式会从"可见的错误"退化为**完全静默的错误答案**（检索恒空、澄清恒失败，日志干净）。降级必须留下信号，否则等于把这次修复变成更难排查的问题。
- 事件登记：若需要新的日志事件，SHALL 在 `src/core/log_events.py` 与 `src/core/log_event_specs.py` **两处同名登记**，字段集与 `log_event(...)` 实参严格一致（import 期硬断言）；若沿用既有事件，则登记其新增字段。

### D4 为什么独立成变更（而不是塞进别的）

- 决策：独立。
- 不塞进 `skill-execution-and-delegation`：它不覆盖本条，且改它的工件属另一会话的写作面。
- 不塞进 `one-loop-two-roles`：后者的开工闸门是"前者 P1+P2 已落地"，**等它开工时缺陷已经爆过**；而且它已有自己的规格面，混入会造成一事两档。
- 因此本变更承担**独立规格**（新 capability `tool-state-access`），另两个变更**引用**它、不重复实现。

### D5 迭代序号的字段名随承载变更而变，访问器只解耦形状

- 现状是 `state._agent_iterations`，而 `one-loop-two-roles` 会把该字段迁入 middleware 的 `_turn_count`。本变更**只解耦形状**，不预设字段名——5 处仍按当前字段名取；改名由 `one-loop-two-roles` 在其自变更内完成（届时只改访问器的实参，不改分流逻辑）。

## Risks / Trade-offs

- **[顺序风险：本变更晚于对方 P2 落地]** → proposal 已写明本变更 MUST 先于其 P2；tasks 的验收含"确认顺序"一项。若无法抢在其前，应在对方 P2 内联修复（此时本变更收敛为只留规格）。
- **[降级分支掩盖编程错误]** → 见 D3：降级必记 warning；tasks 含一条"缺字段时产出 warning"的断言。
- **[规则解读 D2 可能被评审推翻]** → 已显式写明替代实现，改动面很小（回退到 5 处各自显式分支）。
- **[测试覆盖薄]** → 当前全仓 0 覆盖 `InjectedState`，本变更是从零补；若测试基础设施难以在 `create_agent` 内驱动工具，可用假模型 + 最小图构造（本仓库已有假模型用法先例）。

## Migration Plan

- 纯代码、无 DB / API / 部署面变化；无数据迁移。
- **回滚**：恢复 5 处为属性访问即可（但不建议——两种承载都需兼容；回滚后 `create_agent` 路径会立刻回归崩溃）。
- **落地顺序**：本变更 → `skill-execution-and-delegation` 的 P2 → `one-loop-two-roles`。

## Open Questions

- 降级 warning 用**既有事件**（复用工具错误/信号类事件）还是**新事件**？安全默认：新事件（语义独立、便于按字段名聚合），并按 D3 两处登记。
- 迭代序号在 `one-loop-two-roles` 改名后，是否需要一个"字段名集中常量"避免再次散落？安全默认：本变不管，由 `one-loop-two-roles` 在其装配面内决定。
