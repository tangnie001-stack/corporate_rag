## Context

`delegate-execution-controls` 是 `2026-09-12-delegate-hardening-observability` 归档时建立的能力，8 条 requirement 描述 fork 的三层防失控（事件级空闲 watchdog / 总时长保险丝 / turn 上限）、请求取消、中断原因枚举与超时文案。其中 2 条的事实基础已被后续两个 change 改写，但主规格未同步：

- `2026-09-18-session-agent-and-skill-invocation` 废弃了 skill 的 `thinking` / `max-iterations` 字段，并把 fork 执行者由「固定形态」改为「会话/预设决定」。它的 `proposal.md:51` 明写要修订「fork thinking 跟随请求级 deep_thinking」，但该 change 的 `specs/` 里**没有 `delegate-execution-controls` 目录** —— 修订从未落盘（一次归档漏同步）。
- `2026-09-28-skill-execution-and-delegation`（D7）把 fork 子代理由「零工具」改为「默认继承执行者只读工具面」，使 `fork turn 上限` 里的「零工具场景默认 5」「零工具防御上限」框定失效。

代码事实（本 change **不改代码**，仅以其为准）：

| 事实 | 位置 |
|---|---|
| turn 上限 = `preset.max_turns` 优先，缺省 `DELEGATE_DEFAULT_MAX_TURNS` | `src/agents/skills/executor.py:298-304`、`src/config/const.py:92` |
| `thinking` / `max-iterations` 属废弃字段，读到即忽略 + warning | `src/config/const.py:103-106`、`src/agents/skills/loader.py:139` |
| fork `enable_thinking` 恒取请求级 `ctx.deep_thinking` | `src/agents/skills/executor.py:414-418`（来源写入 `src/services/agent_service.py:991`） |

同根因（D7）的残留另有两处：`docs/agents/data-flow.md:81` 与 `tests/agents/skills/test_first_batch_skills.py` 的守卫 docstring。

## Goals / Non-Goals

**Goals**

- 让 `delegate-execution-controls` 的两条失实 requirement 与代码事实一致。
- 清掉 D7 同根因的文档与测试话术残留，使「fork 工具面」在规格/文档/测试三处口径统一。

**Non-Goals**

- **不改任何运行时代码**（`src/` 零改动）。行为本就正确，错的只是描述。
- **不改该 capability 其余 6 条 requirement**：`请求级 deep_thinking 可及`、`fork 流空闲 watchdog`、`fork 总时长保险丝`、`fork 响应请求级取消`、`中断原因统一枚举`、`超时结果文案与日志` —— 逐条比对代码后与现状一致（60s / 240s / 600s / `DelegateStopReason` / ctx 写入）。
- **不处理 F-34 ①**（三份外部 skill description 尾句）：按 `skill-execution-and-delegation` 的 design D14，已转交 `skill-external-sources`。
- **不补本 change 与在途 change 的 ADR**（含与 `fork-tool-face-by-inheritance` 的编号协调）：属独立待办，不混入本次规格对齐。

## Decisions

### D1 只改这 2 条 requirement，其余 6 条不动

依据：逐条把 6 条 requirement 与 `settings.py` / `const.py` / `verify` 侧实现比对，一致（空闲 60s、总时长 240s/600s、`DelegateStopReason` 六值、超时文案、`ctx.deep_thinking` 写入）。改动面越小，越容易复核。
代价（若判断错）：若有比对遗漏，该条继续失实，由后续核查兜。

### D2 保留两条 requirement 的标题不变

依据：标题本身仍准确 —— 它确实「跟随请求级 `deep_thinking`」，也确实「有 turn 上限」。改标题会让 delta 的 MODIFIED 匹配失去锚点，且无谓扩大 diff。
代价（若判断错）：无。

### D3 「skill 声明被忽略」保留为**独立场景**，而非整段删除

依据：删掉会让读者以为「这里从来只有请求级来源」；保留一句显式的「声明不生效（忽略 + warning）」，可防止后人把 `thinking` / `max-iterations` 重新当有效通道加回（该回退正是 F-34 的教训）。
代价（若判断错）：多一个场景。

### D4 `data-flow.md` 的 fork 链路改写为按工具面判定的现语义

依据：D7 之后「材料由 agent 预检索」只在子代理**无**检索工具时成立（`delegate-task`「材料由主 agent 预检索」）。原文把「零工具」与「agent 预检索」并列写成 fork 的定义，两句都已不是通则。
代价（若判断错）：描述多一句，需与 `delegate-task` 主规格保持同义。

### D5 测试只改 docstring，用例名与断言不动

依据：守卫本身仍成立（在库三份 skill 正文确实不点名工具名），失效的只是它的**理由**（「零工具子代理」）。改断言会引入无收益的测试改动。
代价（若判断错）：一条理由更贴切的守卫，行为不变。

## Risks / Trade-offs

- [规格清掉「零工具」后，历史归档件仍写它] → 归档件不可变、按当时事实为准，属预期；主规格以现状为准。
- [D3 的场景可能被误读为「声明会报错」] → 场景写明「忽略并记 warning、上限/取值仍按既有来源取」。
- [两份在途 change（`langfuse-trace-enrichment` / `skill-external-sources`）共享 `llm-tracing` / `skill-registry`] → 本 change 只碰 `delegate-execution-controls`，与二者无重叠，sync 无顺序约束。

## Migration Plan

无迁移、无回滚风险：改动仅规格与文档/测试话术。归档时由 `openspec-sync-specs` 把本条 delta 合入主规格；回滚 = 恢复旧 requirement 文本。

## Open Questions

- `docs/agents/data-flow.md` 同段落里 `delegate_task(task, skill)` 的签名仍是 D6 之前的写法（`skill` 现已可选、省略即通用委派）——属**另一个决策**（D6）的残留，未列入本 change 范围，另行处置。
- 因 D7 而失效的守卫 `tests/agents/skills/test_first_batch_skills.py::test_fork_prompt_must_not_mention_tool_names`：其原始依据（「零工具下子代理无工具可调，正文声明工具集属误导」）未随 `agent-delegation-skills` 进入主规格，而 `skill-registry`「防腐校验」反而**明确容忍** skill 内容出现工具名（示例/伪代码排除表）。本 change 只把 docstring 从失实的「零工具」前提改为陈述事实（D5），**是否删除该守卫**留待定夺。
