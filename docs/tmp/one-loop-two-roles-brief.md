# 简报：一循环两角色（主 / 子 Agent 同一循环）

> **用途**：交接给**新对话**的起点。旧对话在探索后期被"fork 工具面（allowed-tools）"带偏，本简报只保留与目标相关的内容。
> **性质**：对齐材料，**不是设计定稿**——设计由新对话产出。

## 一、目标

让主 Agent 与子 Agent 跑**同一个循环、两种角色**（对齐 claude-code / deepseek-harness），并把领域节点（`verify` / `format` / `agent_finalize` 的领域部分）**移出循环**。

**根因链（原始需求 → 目标）**：原始需求是"日志里 `ToolTraceCollector` 能否复用"；排查发现不可见的根因是**主 / 子不是同一个循环**——子代理是另一套 `create_agent`，其事件流被 `var_child_runnable_config.set(None)` 主动切断（`src/agents/skills/executor.py:189`），故本地日志与 Langfuse 都看不到子代理内部（ADR-0015 的「不解决的问题」已登记）。

## 二、目标形态的实证（两家参考）

- **claude-code**：子代理**复用同一个 `query()`**（`packages/builtin-tools/src/tools/AgentTool/runAgent.ts:776`），用 `createSubagentContext()` 派生上下文 + sidechain transcript + 独立 sub-trace（共享 sessionId）。
- **deepseek-harness**：子代理**用同一个 agent 工厂** `parent.ctx.agents.create(...)`（`packages/subagent/subagent-in-process-driver/src/index.ts:134`），跑在**独立 Session**。

⇒ 共同点：**一套 loop 代码、两种角色**；继承 / 隔离 / 观测的差异由**上下文与作用域**提供，**不是两套实现**。

## 三、本仓现状

主图 = 自建 `StateGraph`（`src/agents/graph/workflow.py`）：
`START → (agent ⇄ tools) → agent_finalize → verify → format → END`，另有 `skill_direct` 入口。

节点归属（新目标下要分「通用」与「领域」）：

| 节点 | 归属 | 依据 |
|---|---|---|
| `agent`（`make_agent_model_node`） | **混合**：循环机制通用；prompt 组装（persona / kb_domain / sources 段）、kb 温度分档 属领域 | `agent_node.py:148-317` |
| `tools`（ToolNode） | **纯通用** | `agent_node.py:320-334` |
| `agent_finalize` | **混合**：取 answer 通用；抄 `ctx.tool_contexts` / `temporal_years` 领域 | `agent_node.py:337-364` |
| `verify` | **纯领域**（KB 完整性 / 引用护栏 / 联网引导）；无接地工具时 no-op | `verify/node.py:20-62` |
| `format` | **纯领域**（`[n]` → citations 组装）；无接地时 no-op | `nodes.py:61-136` |
| `skill_direct` | 会话 / 技能域（另一入口） | `skill_direct.py` |

子代理 = 另一套实现：`create_agent`（`executor.py:272`），事件消费 = `fork_stream.consume_fork_events`（只处理 `on_chat_model_*`）。

## 四、推断的做法（待新对话验证）

```
通用循环（可复用给主 / 子）：agent(model+tools) ⇄ tools + 迭代上限 + 答案提取
        │
        └─ grounding 能力（工具在册才挂，只有主需要）：材料装载 → verify → format
```

**关键接缝**：**prompt 来源要参数化**（主 → RAG 组装；子 → fork 执行者提示），否则通用循环带不走两角色。

⚠️ 非定稿。核心待决：**统一到自建 `StateGraph`（参数化）** 还是 **统一到 `create_agent`**。

## 五、必须一并决定的前置：子代理边界七轴

两角色共用循环后，**边界语义仍需显式**（旧对话产出，仍有效）：

1. **身份** `subagent_id` 一等，随运行态传递，**不落单值字段**
2. **血统** `parent` / `depth`；单一 spawn 入口；本期 depth 上限 0
3. **继承** 显式白名单（逐项声明；本轮不继承父 history 需显式写明）
4. **隔离** messages / tool_contexts / 预算 / 日志线 / 记忆作用域各自独立
5. **观测** 父线只见 lifecycle 边界与最终结果；子内部步骤归子线、带 `subagent=` 判别（**环境注入**，由日志 helper 自动追加）
6. **生命周期** `start → running → end(reason)`；本期同步阻塞；取消传播；三层超时保持
7. **并发** 一轮 N 个子代理；per-child 状态不落单值字段

**未决（旧对话提到、未定）**：

- `ctx.child()` 具体继承哪些字段（现为 8 个"拍的"）
- 子代理材料**是否回传父**（模型委派路径不回传、`/xxx` 直出回传 —— 口径不一致）
- 观测**接线机制**（本地日志子线怎么接；Langfuse 侧独立 trace vs 子 span）
- 子代理预算 / 压缩、记忆作用域（机制未设计；记忆层未建）

## 六、明确出界（本目标不碰）

| 项 | 归属 |
|---|---|
| fork 工具面规则（默认继承 + 三减法） | **另一会话**的 change（暂名 `fork-tool-face-by-inheritance`） |
| 委派工具形态 A / B / C | 独立决策 |
| 异步 / 后台子代理 | 依赖共享事件设施，延后 |
| 内容侧：agent vs skill 方法论分层、3 个 fork skill 的"预检索"描述 | 内容治理 |

## 七、素材移交：工具面规则（转另一会话）

旧对话已推导完整规则，另一会话可直接取用：

```
child_tool_face =
      继承主 agent 当前工具面（build_graph 写入 fork_tool_pool 的同一份快照）
    − FORK_FORBIDDEN_TOOLS（ask_user / delegate_task）
    − { t | readonly(t) 不是 True }（写类 + 只读性未知；fail-safe；未声明时构建期 warning）
    ∩ preset.tools（仅当声明）
    ∩ skill.allowed_tools（仅当声明；显式 [] = 收到零；loader 需区分「缺失」与「显式空」）
```

配套：未知名 **fail-loud**；`task_*` 注册进 `ToolRegistry` 并按只读性标注；排除写类工具是**本产品领域选择**（非对齐 CC）。

## 八、流程状态（新对话接手前须知）

- **ADR-0016 已作废（2026-09-26）**：`docs/adr/0016-subagent-contract.md` 已删除，`docs/adr/README.md` 索引行已还原（`check_adr` 15 条 0 error）。作废原因：它把"工具面"当主问题、且 D4 写"本期允许异构"，两者均与本目标相反。因当时**未提交**，作废零成本、**不占编号**。→ **编号 `0016` 现空出**。
- 仍有效的内容已全部收入本简报（七轴、工具面规则、作业清单），作废不丢信息。
- 旧对话的偏离：后期主轴换成了工具面（另一会话的活）。
- 新对话建议顺序：① 定"一循环两角色"的形态 → ② 决定契约七轴如何落 ADR → ③ 工具面交给另一会话。
- **worktree**：按 `docs/agents/dev-flow.md`「变更开工前置」先问，不得默认就地。建议新建独立 worktree（如 `corporate_rag-one-loop-two-roles`），与另一会话的 `corporate_rag-fork-tool-face` 并列。
- **交接时工作区现状（2026-09-26）**：旧对话在**主工作区** `/root/code/corporate_rag`（分支 `dev-wsl`，HEAD `b6e2f39`），**未产生任何提交**。机器上另有 worktree：`corporate_rag-fork-tool-face`（`feat/fork-tool-face-by-inheritance`，另一会话的工具面 change）、`corporate_rag-fork-result`（遗留）。`dev-wsl` 会被并行会话推进 → 提交有互踩风险，一律用显式路径 `git add`，**勿用 `git add -A`**（工作区有未跟踪文件）。

## 九、新对话的待决清单（起点）

1. 统一到哪套 loop：自建 `StateGraph` 参数化，还是统一到 `create_agent`？
2. prompt 来源接缝怎么设计（通用循环如何带两角色的 system 来源）？
3. `verify` / `format` 归位：作为"接地能力贡献的循环后阶段"，还是别的形态？
4. `agent_finalize` 拆分（通用出口 vs 领域材料装载）？
5. 七轴契约与新形态的 ADR 划分（一条还是多条）。
6. 与另一会话的编号 / 顺序协调（0016 空出后归谁）。
