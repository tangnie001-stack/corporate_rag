## Why

`delegate-execution-controls` 有两条 requirement 与现状不符，且两条的失实来源不同：

- **「fork turn 上限」** 仍写「零工具场景默认 5。开放工具后由 skill `max_iterations` 声明控制（未声明按默认）」。真值是：上限 = 执行者预设 `maxTurns`，缺省 `DELEGATE_DEFAULT_MAX_TURNS`（`src/agents/skills/executor.py:298-304`、`src/config/const.py:92`）；`max_iterations` 已属 `DEPRECATED_SKILL_FIELDS`（`const.py:103-106`），读到即忽略并记 warning（`loader.py:139`）。其中「零工具」框定被 `skill-execution-and-delegation` 的 D7（子代理改为继承执行者只读工具面）二次证伪。
- **「fork thinking 跟随请求级 deep_thinking」** 仍写「skill 显式声明 `thinking: true/false` 时 SHALL 覆盖该默认」。该覆盖通道自 `2026-09-18-session-agent-and-skill-invocation` 废弃 `thinking` / `max-iterations` 字段起即不存在：`enable_thinking` 恒取 `ctx.deep_thinking`（`executor.py:414-418`）。**上一个 change 的 `proposal.md:51` 明写要修订本条，但它的 `specs/` 里没有 `delegate-execution-controls` 目录 —— 修订从未落盘**，属一次归档漏同步。

同根因的残留另有两处：`docs/agents/data-flow.md:81` 仍把 fork 链路写成「零工具子代理独立分析（材料由 agent 预检索后随 task 一并传入）」（该处被 `skill-execution-and-delegation` 的 tasks §10.4 点名要改，但 `data-flow.md` 漏了）；`tests/agents/skills/test_first_batch_skills.py` 的守卫 `test_fork_prompt_must_not_mention_tool_names` docstring 仍以「零工具子代理」为前提（tasks §9.2 要求重定）。

## What Changes

- 改写 `delegate-execution-controls`「fork turn 上限」：上限来源改为「执行者预设 `maxTurns`，未声明回落系统默认」，删除「零工具场景」框定；删除「skill 声明 `max_iterations`」场景，替换为「`max_iterations` 已废弃、不参与上限」场景。
- 改写 `delegate-execution-controls`「fork thinking 跟随请求级 deep_thinking」：`enable_thinking` 恒取请求级 `deep_thinking`；明确 skill **不持有** `thinking` 声明通道（字段已废弃、忽略并记 warning），删除「skill 显式声明覆盖」场景。
- `docs/agents/data-flow.md` 的 fork 链路描述改为「继承执行者只读工具面的子代理」，并把「材料由 agent 预检索」改为按工具面判定的现语义。
- `tests/agents/skills/test_first_batch_skills.py` 的守卫 docstring 去掉「零工具」前提（改为「正文不点名工具名」的现行约束；断言与用例名不动）。
- **无运行时行为改动、无 BREAKING**：纯规格 / 文档 / 测试话术对齐。

## Capabilities

### New Capabilities
<!-- 无 -->

### Modified Capabilities
- `delegate-execution-controls`: 「fork turn 上限」与「fork thinking 跟随请求级 deep_thinking」两条 requirement 的事实口径订正（上限来源、`thinking` 声明通道的有无）。

## Impact

- 规格：`docs/openspec/specs/delegate-execution-controls/spec.md`（归档同步后生效）
- 文档：`docs/agents/data-flow.md`
- 测试：`tests/agents/skills/test_first_batch_skills.py`（仅 docstring，断言不变）
- 代码：无（`src/` 零改动）
- API / DB / 依赖：无
