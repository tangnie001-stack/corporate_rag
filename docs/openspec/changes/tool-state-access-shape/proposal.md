## Why

工具经 `langgraph.prebuilt.InjectedState` 取图状态时，**注入值的形状由承载它的图实现决定**：外层自建 `StateGraph` 给 `AgentState` **实例**（属性可用），而 `langchain.agents.create_agent` 给 **`dict`**（实测：`create_agent(state_schema=<dataclass>)` 与默认 schema **都**注入 dict）。本仓 5 处取数用**属性访问**，一旦承载它的图换成 `create_agent` 就会 `AttributeError`，且被工具节点的错误回喂吞成普通 `ToolMessage` 错误 ⇒ **检索恒空、澄清恒失败**，而图照常跑完、日志上看不到异常。

**为什么现在**：两个在途变更都会触发它，且**触发者先落地**——

- `skill-execution-and-delegation` 的 **P2（D7/D8：子代理工具面默认继承）** 会让 fork 子代理**首次拿到 `retrieve_kb`**，而子代理本就跑在 `create_agent` 上 ⇒ 其 P2 验收（11.10「通用委派并让子代理检索」）**当场踩空**。
- `one-loop-two-roles`（主循环统一到 `create_agent`）同样依赖本修复，但它**排在 `skill-execution-and-delegation` 整体之后**——等它来不及。

⇒ 本修复必须**独立成变更、并抢在前者的 P2 之前落地**。

## What Changes

- **5 处工具取数改为显式形状判定**：`rag_tools.py:102`（`state.kb_id`）与 `:187`（`state._agent_iterations`）、`ask_tools.py:82`（`state.query`）/ `:83`（`state._agent_iterations`）/ `:166-167`（`state.kb_id`）——先判 `isinstance(state, dict)` 再走对应通道，**两条路径都要有取值行为**；**不得**用 `getattr(..., default)` 之类的隐式兜底（违反 CLAUDE.md 的显式类型检查规则）。
- **降级必须可观测**：字段缺失走降级分支时 SHALL 记 warning（含工具名与缺失字段名）。现状是抛 `AttributeError`（至少作为工具错误可见）；改判降级后若不打日志，就会变成**彻底静默取空**——那是比崩溃更坏的失败模式。
- **补断言**：在 `create_agent` 承载下调用 `retrieve_kb` / `ask_user`，断言注入状态为 `dict` 时两者都能取到值、且不抛 `AttributeError`（**当前全仓 0 覆盖 `InjectedState`**）。
- **模块 docstring** 写明：注入状态的形状由承载它的图实现决定，故必须显式判定。
- **明确不做**：不改工具的检索/澄清语义；不改 `InjectedState` 的使用方式；不新增工具；不动 `skills/` 内容；不改 `create_agent` 的调用方（那是两个在途变更的事）。

## Capabilities

### New Capabilities

- `tool-state-access`：工具从**注入的图状态**取数时的形状无关契约——不得假定承载图给定某一种形状；必须以显式形状判定取数；缺字段时显式降级并**记 warning**（不得静默取空）。

### Modified Capabilities

（无——本条此前没有任何规格规定，属新立契约）

## Impact

**代码**

- `src/agents/tools/rag_tools.py` — 2 处取数（`:102` / `:187`）+ 模块/工具 docstring
- `src/agents/tools/ask_tools.py` — 3 处取数（`:82` / `:83` / `:166-167`）+ docstring
- `src/core/log_events.py` / `src/core/log_event_specs.py` — 若新增降级 warning 事件，须**两处同名登记**（字段集与 `log_event(...)` 实参严格一致，import 期硬断言）

**测试**

- `tests/agents/tools/` 下新增"`create_agent` 承载下取数正确"用例（`retrieve_kb` + `ask_user`）；如已有对应测试文件则就地补充
- 现有 `tests/agents/tools/` 的 `InjectedState` 相关断言复核（若存在以 dataclass 为前提的写法，需同时覆盖两种形状）

**文档**

- `docs/agents/defensive-patterns.md` — 复核/补一条（`create_agent` 下 `InjectedState` 是 `dict`）——**已在本轮立项时登记**（「自定义 middleware 必须声明 state_schema」同批的「agent 循环与 LangChain 中间件」一节）
- `docs/agents/api_contract.md` — 若工具签名/行为注释涉及注入状态形状，同步一句
- `requirements_pool.md` — 收口 **F-36**（本条即其落点）

**依赖与排序（关键）**

- **本变更 MUST 排在 `skill-execution-and-delegation` 的 P2 之前**（其 P2 一落地即触发本缺陷）；建议与 `one-loop-two-roles` 的关系为：本变更先行，后者不再重复实现（引用本变更）。
- 无 DB 迁移、无 API 破坏、无部署面变化。
