# Tasks

## 1. 规格 delta

- [ ] 1.1 `docs/openspec/changes/delegate-controls-realign/specs/delegate-execution-controls/spec.md` — delta 覆盖「fork thinking 跟随请求级 deep_thinking」与「fork turn 上限」两条 MODIFIED requirement 全文，标题与主规格逐字一致
- [ ] 1.2 逐条核对 delta 的每句事实与代码一致（`src/agents/skills/executor.py:298-304`、`:414-418`、`src/config/const.py:92,103-106`、`src/agents/skills/loader.py:139`）

## 2. 文档

- [ ] 2.1 `docs/agents/data-flow.md` 的 fork 链路（`:81`）改为按工具面判定的现语义：子代理默认继承执行者只读工具面、可自行检索；「材料由 agent 预检索」降为「无检索工具时」的条件分支
- [ ] 2.2 复核 `docs/agents/` 已无同根因的「零工具」残留（`glossary.md` / `code-map.md` / `api_contract.md` 预期已干净；若有则记录不擅改）

## 3. 测试话术

- [ ] 3.1 `tests/agents/skills/test_first_batch_skills.py` 的守卫 `test_fork_prompt_must_not_mention_tool_names` docstring 去掉「零工具子代理」前提，改为现行约束（正文不点名工具名）；**用例名与断言不动**

## 4. 验证

- [ ] 4.1 `openspec validate --all` 全绿
- [ ] 4.2 `POSTGRES_HOST=localhost pytest tests/ -q` 全绿（本 change 不改行为，预期与基线同为 1323 passed）
- [ ] 4.3 `ruff check .` 无错误、`pyright src/` 不新增 error
- [ ] 4.4 `git diff --stat` 确认改动面仅限 delta 规格 / `docs/agents/data-flow.md` / 测试 docstring（`src/` 零改动）
