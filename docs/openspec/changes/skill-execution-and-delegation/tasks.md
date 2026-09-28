## 0. 阶段划分与闸门

本变更**一次评审、分两次执行**（design 的决策不因分阶段而改变）。分界线是**是否改变子代理的能力边界**：

| 阶段 | 内容 | 所属节 | 是否改能力边界 |
|---|---|---|---|
| **P1** | D1 加载语义 · D2 非法值口径 · D3 预加载 · D15 委派路径子上下文隔离 · D5 标记剥离 · D11 trace | §1 §2 §4 §6 §7 | **否**——只统一口径、修隔离、剥协议串、补观测 |
| **P2** | D7/D8 工具面继承与禁用集 · D16 引用链策略 · D6 通用委派 · D10 会话级预算 | §3 §5 | **是**——子代理从"零能力只读"变成"能自检索、将来可能有写权限" |

**为什么这么切**：两轮独立架构评审共 4 条 Blocker **全部落在 P2 的半径内**（引用池污染、写权限外流、隔离措辞冲突、引用链断裂），P1 侧零 Blocker。风险量级不同，不该承担同一套验证强度。

### 闸门（P1 → P2）

- [ ] 0.1 **P1 全部条目完成且 §11 的 P1 验收清单通过前，不得开始 P2 的任何条目**（尤其 §3——它一落地子代理就拿到工具，而 §4 的隔离必须已经在位）
- [ ] 0.2 **P2 开工前的前提确认**（**轻量复核，非完整评审**）：逐条核对 P1 的实测结果是否推翻 P2 的设计——
  - `readonly_map()` 在真实注册顺序下是否已填充（§3.5 的空表极性）
  - 真实模型下子代理拿到工具后是否**确实**会自检索（决定 §3b 的引用链策略是否成立）
  - 直出轮子代理标 `[n]` 后 `citations` 编号是否真能对上（§3b 的验收前提）
  - 真实会话的委派分布（§5 的 `DELEGATE_MAX_PER_SESSION` 取值）
  - **复核方式**：复核对象是**上述 4 条的实测证据**（不是 design 全文——D7/D8/D16 已被两轮独立架构评审覆盖）。**须由独立复核方执行，不得由本变更作者自证**。任一条被推翻 → 后果是"P2 核心设计错"，须**升级为完整架构评审**，或按 §0.4 把 P2 拆成独立 change 重走 propose
- [ ] 0.3 **P1 代码需过 code review**（架构评审评的是提案，实现另需评审）：P1 落地后按项目常规走一次代码评审，再进入 §0.2 的前提确认
- [ ] 0.4 **例外条款**：若 §0.2 发现 D7 需要重新设计，则把 P2（§3/§3b/§5）的 spec 改动与 tasks 搬到一个独立 change，本变更在 P1 完成后即归档（此时主规格同步的是 P1 部分）。**注意不能"原封搬文件"**——`specs/delegate-task/spec.md` 同一文件混装 P1 与 P2 的 requirement，须**按 requirement 拆条目**；MODIFIED 的「fork 执行」本身属 P2
- [ ] 0.5 **spec 同步说明**：`openspec archive` 是整包动作——本变更分两阶段执行时，**P1 结束时主规格尚未更新**，四份 delta spec 的同步发生在 P2 完成后一次性进行；滞后期间以 change 目录内的 delta spec 为中间态记录

## 1. 加载语义：缺省 inline + 超限自动 fork + 非法值口径（design D1/D2，P1）

- [ ] 1.1 `src/agents/skills/loader.py` — `_resolve_context`（`:152-166`）缺省值由 `SkillContext.FORK` 改为 `SkillContext.INLINE`；**新增超限自动判定**：正文长度 > `INLINE_PROMPT_MAX_CHARS` 时改用 `SkillContext.FORK` + 记 warning（"正文 N 字符超出 inline 预算、已自动按 fork 处理；如确需 inline 请显式声明并精简正文"）。判定需要正文长度，`_resolve_context` 现只吃 `meta` → 把长度（或正文）一并传入
- [ ] 1.2 `src/agents/skills/loader.py` — 显式声明优先：`meta` 里有 `context` 时以显式值为准、不参与超限判定；显式 `inline` 且超限 → 保持 inline 记 warning（超限守卫测试承担失败）
- [ ] 1.3 `src/agents/skills/loader.py` — 非法值分支保持**抛 `ValueError` → `load_all` 记 warning 并跳过**（`:164-165` 现状），不改代码；docstring 点明"不静默降级"的理由（与非法 `name` 同款）
- [ ] 1.4 `src/agents/skills/models.py` — `SkillRecord` 新增 `context_source`：**给默认值 `"default"`**、**追加到字段列表末尾**（设为必填或插中段会引爆 `loader.py:96` 与测试里约 16 处构造）；取值 `explicit | auto_oversize | default`，行内注释写来源/范围/用途
- [ ] 1.5 **同批修正一批已说谎的注释/文档**（评审两轮指出，注意定位别改错地方）：`src/config/const.py:94-96`（`INLINE_PROMPT_MAX_CHARS` 的"超出**仅记 warning**"→ 说明它同时是承载方式开关）；`src/agents/skills/loader.py:8` 模块 docstring；**`src/agents/skills/models.py:30`**（`SkillRecord.context` 的字段注释——不是 `SkillContext` 类；`:29` 是 `description` 字段，别错行）
- [ ] 1.6 `context_source` 进**加载/启动日志**（记 skill 名 + `context_source` + 正文长度），使"为何按 fork 承载"无需人工 E2E 即可判定
- [ ] 1.7 `src/agents/skills/loader.py` — `_resolve_body`（`:168-172`）确认在超限自动判定后 inline_prompt / fork_body 分流正确
- [ ] 1.8 确认**不修改** `skills/` 下任何 SKILL.md（D1 的设计目标之一就是免除这件事）

## 2. 预加载只注入 inline 正文（design D3，P1）

- [ ] 2.1 `src/services/agent_service.py` — `_preload_skills_text`（`:876-905`）删除 `body = record.fork_body` 回落分支（`:898-899`）；`inline_prompt` 为空时记 `Event.SKILL_PRELOAD_SKIP`（`reason="fork_skill"`）并 `continue`
- [ ] 2.2 同步改写该方法 docstring：说明"只注入 inline 正文；fork 正文属于子代理 prompt，注入主 agent 是语义错配"

## 3. 子代理工具面：默认继承、声明收窄（design D7/D8，**P2**）

- [ ] 3.1 `src/agents/skills/fork_tools.py` — `select_fork_tools`（`:15-40`）删除缺省零工具分支（`if not allowed: return []`）；改为"先减禁用集 → **再减非只读工具**（依 `readonly_map()`，表中缺项按非只读处理）→ 声明 `allowed-tools` 时取交集 → 执行者预设声明 `tools` 时再取交集"；同步改写模块 docstring 的交集口径
- [ ] 3.2 `src/config/const.py` — `FORK_FORBIDDEN_TOOLS` 保持 `ask_user` / `delegate_task`；新增主 agent 专属工具类（`task_*`）的排除集合，行内注释说明三档理由不同故分列（design D8）
- [ ] 3.3 **禁工具集完备性守卫测试**（评审纠正：**域是工具池，不是注册表**）：`task_*` **不注册进 `ToolRegistry`**，而是由 `workflow.py:88` 直接 `[*(base_tools or []), *make_task_tools()]` 挂进 `tool_sink`——遍历注册表看不到它们，正是 D8 最担心的对象会漏网。守卫须遍历**本次启用的工具池**（`make_rag_tools(...) + make_task_tools()`），断言方式：以**空 `allowed-tools`** 调 `select_fork_tools`，断言结果中**不含任何非只读工具**、不含禁用集成员。**不得**写成"池中每个工具要么只读要么在禁用集"——那会在将来出现**合法的写类工具**时误爆（第三轮评审指出）

- [ ] 3.4 `src/agents/skills/executor.py` — `_fork_tools`（`:298` 起）按新口径取工具；无 `tool_provider` 时的兜底不退回"零工具"而是记 warning
- [ ] 3.5 `src/agents/skills/delegate_run.py` — `DelegateRun` 新增**路径标识字段**（如 `via: "direct" | "delegate"`），由 `skill_direct` 与 `delegate_task` 各自填；执行器据此决定给子代理的 `[n]` 指示。**不得**靠任务文本猜路径（第三轮评审：设计须给出载体，否则实施者各自发明）
- [ ] 3.6 **空表极性写明**（第二轮评审指出）：`readonly_map()` 为空时 fork 侧按 **fail-closed**（不下发）+ warning，与 `derive_invocation_flags` 对空表的 **fail-open**（`invocation.py:33-36`）极性相反——在代码注释与 spec 写明理由（同一张表两个消费者失败代价不同），防后人"统一"
- [ ] 3.7 `src/agents/skills/loader.py` — `_warn_fork_without_tools`（`:141-150`）语义作废：删除或改判为"已不再表示零工具"，同步删掉 `context: fork` 未声明 `allowed-tools` 的 warning

### 3b 引用链策略（design D16，与 §3 同批，先做 §4 再落本项）

- [ ] 3.8 `src/config/prompts/__init__.py` — `FORK_DEFAULT_EXECUTOR_PROMPT`（`:81-84`）现**无条件**要求子代理"不标注引用编号 `[n]`"，与**直出路径**"子代理须标 `[n]`（无主 agent 补标）"相反 → 按路径给不同指示（直出路径追加"本轮请标注 `[n]`"的要求；委派路径维持不标）
- [ ] 3.9 确认委派路径的引用**完全来自主 agent 自身检索**（子代理检索只补事实、不回流主池、不标 `[n]`），与 `docs/agents/prompt-ownership.md` 的 `output-delegate-citation` 既有决策一致
- [ ] 3.10 `docs/agents/glossary.md` / `prompt-ownership.md` — 写明**两条路径的 `[n]` 策略不同是有意为之**，防实施者"统一"


## 4. 委派路径的子上下文隔离（design D15，P1）

> **顺序要求**：§3 一落地，子代理立刻拿到工具；若 §4 未同批完成，子代理检索会写进**主**引用池，直接产生引用错配。故 §4 与 §3 必须同批，且 §4 先做。

- [ ] 4.1 `src/agents/skills/delegate_task.py` — 定点与通用两个分支**都建 `DelegateRun`**（`delegate_id=…`、`skill_name=…`、`ctx=main_ctx.child()`）并传 `executor.execute(record_or_none, task, run)`（现状 `:90,94,135` 均不传，走 `executor.py:177-179` 的"主 ctx、不隔离"分支）
- [ ] 4.2 **措辞边界**：委派状态**写在该次委派的子上下文 `run.ctx` 上是对的**（`executor.py:175`、`fork_stream.py:74` 已是这个写法，天然按委派分槽）；要禁止的是写**主上下文**。**不要**改成"删除 `RequestContext` 单值字段"——那会打断既有管线
- [ ] 4.3 **豁免分支**：`delegate_task.py:92-94` 存在 `current_request_ctx.get() is None` 的情况，此时无父上下文可 `child()`；按既有 fail-open 处理 + 记 warning，不得抛错
- [ ] 4.4 `tests/agents/skills/` — 断言委派路径的子代理检索写**子**引用池、主池保持为空；一轮两次委派各写各的子池互不串号

## 5. 通用委派与会话级预算（design D6/D10，**P2**）

- [ ] 5.1 `src/agents/skills/delegate_task.py` — `DelegateTaskArgs.skill` 改可选（`str | None = None`），description 说明省略即通用委派
- [ ] 5.2 同文件 — 无 `skill` 时走通用委派分支：不查注册表、不加载正文；执行者人设按"会话选定智能体 > 系统默认"；`task` 直接作为子代理输入
- [ ] 5.3 `src/agents/skills/executor.py` — `_render_fork_task`（`:130-143`）与 `_build_sub_agent`（`:259-277`）支持"无 SkillRecord"输入；`_resolve_executor` 在无 record 时跳过 `record.agent` 分支
- [ ] 5.4 委派事件与任务看板条目区分通用/定点（`DelegateRun.skill_name` 用固定占位或空值，实现时定），保证 `delegate_start` / `delegate_end` / task execution 条目仍可逐次关联
- [ ] 5.5 确认通用委派复用 `maxTurns` / 总时长兜底 / 取消传播 / 引用池隔离（复用 `_run_fork`，不新起执行路径）
- [ ] 5.6 `src/config/settings.py` + **中性进程级单例**（与 `src/chat/task_registry.py` 同址同类）— 新增 `DELEGATE_MAX_PER_SESSION`（默认值实施时定，量级参考 50）与计数器 `check_and_incr(session_id)` / `reset(session_id)`。**不得放在 `src/services/agent_service.py`**（层间规则：`services/` 是 `agents/` 的上层，反向 import 越界）
- [ ] 5.7 **TTL 惰性清理**（第三轮评审：防内存泄漏）：只挂会话删除不够——Redis 历史 7 天到期**不会**触发本进程清理，进程内 dict 只增不减。复用 `task_registry` 同款 `TASK_TTL_SECONDS=1800` + `sweep_expired()`（`src/chat/task_registry.py:18,235`）
- [ ] 5.8 **复位点显式挂钩**（评审纠正：**全仓不存在 `/clear`**，不得再写这个幻影路径）：会话删除会调 `src/services/app_service.py:244` 的 `chat_manager.clear_history_async(session_id)`（定义在 `src/chat/manager.py:303`）→ 复位挂在那里；进程重启即清。把复位点写进测试与 **11.11** 验收（预算复位在 P2 验收清单，不在 11.3）
- [ ] 5.9 计数器**取消/异常时不回滚**（已发起即计数），在代码注释与 `logging-rules.md` 写明，防实施者自行发明回滚语义
- [ ] 5.10 `src/agents/skills/delegate_task.py` — 调用前检查预算：超限返回**可读原因**（提示改用自身能力、**不要再重试委派**）并记 `delegate skip`（`reason=budget_exhausted`），不抛异常、不中断本轮
- [ ] 5.11 明确**不实现嵌套深度上限**，在代码注释与归属文档写明理由（子代理不持有 `delegate_task`，深度恒为 1），防后人照抄外部实现的"5 层封顶"

## 6. 确认标记不泄漏（design D5，P1）

- [ ] 6.1 `src/agents/skills/delegate_task.py` — 子代理返回文本回给主 agent 前，**剥离协议前缀但保留标记行上的问题文本**（转为普通文本行）。**不得直接复用 `strip_confirm_marker`**——它是**整行删除**（`confirm_gate.py:37-44`），会把"子代理在等什么确认"静默吞掉（第二轮评审 Blocker）；新写一个"剥前缀、留文本"的处理
- [ ] 6.2 确认剥离后的工具结果仍便于主 agent 自行决定是否向用户提问（不额外加结构化信号）

## 7. 子代理在 Langfuse trace 上可见（design D11，P1）

- [ ] 7.1 `src/infra/llm/tool_trace.py` — 采集器参数化：新增 `scope`（`main`/`delegate`）、可注入的**父 span**、span 名前缀（委派域 `delegate:`）；主域参数缺省即现状，对外行为不变。入口过滤 `metadata["langgraph_node"] == "tools"` **无需放宽**（已实测成立）
- [ ] 7.2 `src/agents/skills/executor.py` — `_run_fork` 开合**委派父 span**（标注 `delegate_id` / `skill`；通用委派标注为通用），`close()` 挂 `finally` 覆盖 idle / total / turn / cancelled 全部中断路径
- [ ] 7.3 `src/agents/skills/fork_stream.py` — `consume_fork_events` 消费子代理事件时**同时**喂给委派域采集器（**不得**改动 `var_child_runnable_config.set(None)` 的 SSE 隔离）
- [ ] 7.4 实例划分：**主域每请求 1 个**、**委派域每次委派 1 个**（不可写成"每请求每 scope 一个"——`scope=delegate` 是所有委派共享的取值，按它建实例等于全部委派共用一个实例）；避免 `_round` 单值状态跨流串台
- [ ] 7.5 盯守点：验证 `_open_round` 在子代理独立图下**不会**被重复开合（评审期实测只覆盖了事件形状，未覆盖这一点）

## 8. 外部 skill 原样与登记项（design D13/D14，跨阶段）

- [ ] 8.1 `src/config/const.py:92` — `DELEGATE_DEFAULT_MAX_TURNS` 注释标注"前提（fork 零工具）已废，待 `agent-round-budget` 重新标定"；**不改值**
- [ ] 8.2 `docs/agents/requirements_pool.md` — 收口 F-13（默认值与长文去向已由 D1 处置）与 F-34：②（未声明 `allowed-tools`）随 D7 失效；①（description 尾句）**因"外部 skill 原样"不在本变更移除**，转交 `skill-external-sources`
- [ ] 8.3 `docs/agents/requirements_pool.md` — 登记本次核查的三条新发现：`to_tool_description(max_chars=500)` 截断 skill 列表；三个问用户渠道只有一个受 `MAX_ASK_PER_TURN` 限流；`financial-statement-analyzer` 的 `references/analysis-methodology.md` 悬空引用（归 F-12）
- [ ] 8.4 `docs/agents/requirements_pool.md` — 登记轮次护栏 change（暂名 `agent-round-budget`）及"其 apply 须在本变更之后"

## 9. 测试（按落点点名）

- [ ] 9.1 `tests/agents/skills/test_skill_loader.py` — 缺省 inline；超限自动 fork（含 warning 文案）；显式声明优先（显式 inline + 超限保持 inline）；非法值跳过 + warning；`context_source` 三态
- [ ] 9.2 `tests/agents/skills/test_first_batch_skills.py` — `test_all_inline_skills_within_budget`（`:33`）语义按超限自动判定调整（显式 inline 才可能触发；在库三份超限 skill 应落 `auto_oversize`）；`test_fork_prompt_must_not_mention_tool_names` 前提（零工具）已废 → 按新语义重定或删除
- [ ] 9.3 `tests/agents/skills/test_fork_tools.py` — 缺省继承、声明收窄、禁用集三档（含 `task_*` 排除）、执行者-预设双重收窄
- [ ] 9.4 `tests/agents/skills/test_fork_executor_selection.py` / `test_fork_sub_agent_contract.py` — 工具面口径变化后的装配断言；通用委派（无 record）装配
- [ ] 9.5 `tests/agents/skills/test_delegate_budget.py`（新） — 超限拒绝并返回可读原因；`/xxx` 直出不消耗预算；会话隔离与重置；委派深度恒为 1
- [ ] 9.6 `tests/services/test_preset_skill_preload.py` — fork skill 不被预加载（跳过 + warning、其余项正常）
- [ ] 9.7 `tests/services/test_agent_service.py` — 通用委派分支（`skill` 省略时不查注册表、事件可区分）；未知 skill 仍返回列表；确认标记被剥离
- [ ] 9.8 `tests/infra/llm/test_tool_trace.py` — 委派域 span 挂到委派父 span、名前缀可区分、域间不串台、观测异常只记 warning 不阻断、取消路径不留悬空 span
- [ ] 9.9 `tests/services/test_run_generation_tracing.py` — 主图工具 span 结构不回归
- [ ] 9.10 `tests/agents/graph/test_direct_skill_round.py` — 直出既有断言保持（主 agent 零轮 / task=query / 引用池归属 / 确认门）
- [ ] 9.11 `tests/agents/skills/test_fork_tools.py` — **非只读工具默认不下发**、显式 `allowed-tools` 声明后下发（评审 Blocker 的守卫）
- [ ] 9.12 委派域采集器：**多工具轮**（一个委派内多轮工具调用共用同一采集器）与**并发委派**（一轮两个委派各自独立采集器）两场景

## 10. 文档

- [ ] 10.1 `docs/agents/glossary.md` — 补术语：通用委派（vs 定点委派）、子代理工具面、`context_source`；修正既有条目中与本次改动冲突的表述
- [ ] 10.2 `docs/agents/logging-rules.md` — 登记新增/变更事件与 skip reason（预加载 fork 跳过、`delegate skip` 的 `budget_exhausted`、超限自动 fork 的 warning）
- [ ] 10.3 `docs/agents/api_contract.md` — `delegate_task` 参数契约变更（`skill` 可选）+ 通用委派语义
- [ ] 10.4 `docs/agents/data-flow.md` / `code-map.md` / `api_contract.md` — **范围须含三处已知的旧语义残留**（第二轮评审指出）：`code-map.md:188`、`api_contract.md:1066`（"allowed 为空→零工具"）、`data-flow.md:81`（"零工具子代理"）；另涉 trace 采集链路与委派链路图则同步
- [ ] 10.5 ADR — 本变更含不可逆取舍（`context` 缺省语义 + 超限自动判定 + 工具面继承 + `delegate_task` 参数放开），按 `docs/adr/README.md` 模板撰写。**写之前先查 `dev-wsl` 分支当前最大编号**（并行分支编号会撞车；`check_adr` 要求连续，故重编号必须与合并同一提交）
- [ ] 10.6 冲突登记（**评审已纠正**）：`turn-provenance-observability` 的代码**大部分已落地**（`STAGE_TURN_AGENT` 在 `const.py:226`、`_preload_skills_text` 已返回 tuple、`_resolve_session_agent` 已返回结构化）→ 不存在"将来同函数冲突"，但要在该 change 里注明"本变更改同一函数的回落分支与跳过语义"；`skill-external-sources` 需注明与本变更共享 `skill-registry: Skill 文件结构` 这条 requirement，sync 顺序本变更在先
- [ ] 10.7 `docs/agents/requirements_pool.md` — 登记**确认门的沉默失败**：子代理不遵守 `FORK_EXECUTION_CONTRACT` 时，直出轮会基于假设直接作答且无任何提示（`detect_confirm_request` 只匹配最终文本行首标记）。本变更只修了标记泄漏，未增强可靠性

## 11. 验证（按阶段分别验收）

**通用（每阶段各跑一次）**

- [ ] 11.1 `POSTGRES_HOST=localhost pytest tests/ -v` 全绿（**按阶段跑该阶段相关测试**，不是等 P2 才跑）
- [ ] 11.2 `ruff format . && ruff check .` 无错误；`pyright src/` 不新增 error

**P1 验收清单（改能力边界之前必须全过——这是 §0.1 闸门的一部分）**

- [ ] 11.3 三份在库 skill 均按 fork 承载（`context_source=auto_oversize`），且**文件与上游副本逐字一致**（`diff` 验证）
- [ ] 11.4 预设声明 fork skill 时该项被跳过并记 warning，会话正常、其余预加载项不受影响
- [ ] 11.5 **委派路径的子上下文隔离生效**（**须用测试替身**）：P1 阶段子代理仍零工具，**真实子代理不会写任何引用池**，故"主池未被写入"恒真、证明不了隔离（第三轮评审：该断言自败）。改为用**会写引用池的工具替身**装配子代理，断言写入落在**子**池、主池保持为空；一轮两次委派各写各的子池
- [ ] 11.6 Langfuse 上出现**委派父 span**（标注 `delegate_id` / `skill`），且主 agent 的工具 span 结构未变。**注意此时子代理零工具，父 span 下应无工具 span**（工具 span 属 P2）

**P2 验收清单**

- [ ] 11.7 `/xxx` 直出：子代理**自行检索**、答案带 `[n]`，本轮 `citations` 编号与之一一对应
- [ ] 11.8 **主 agent 委派路径的引用不受污染**：子代理自检索后，主 agent 答案的 `[n]` 仅来自主 agent 自身检索
- [ ] 11.9 **写权限须显式声明**：造一个非只读工具（测试替身即可），确认它**不**自动进入子代理工具面；某 skill 在 `allowed-tools` 显式声明后才下发；§3.3 的池域守卫测试覆盖 `task_*`
- [ ] 11.10 主 agent 对 skill 未覆盖的任务发起**通用委派**并让子代理检索
- [ ] 11.11 会话级预算：触顶后被拒、返回可读原因、本轮照常收尾；`/xxx` 直出**不消耗**预算；会话删除后计数归零
- [ ] 11.12 委派路径的确认标记被**剥前缀但保留问题文本**（对拍：主 agent 仍能看到"子代理在等什么确认"）
- [ ] 11.13 Langfuse 上委派父 span **下出现子代理的工具 span**（`delegate:` 前缀），多工具轮与并发委派均不串台

**收口**

- [ ] 11.14 `openspec validate skill-execution-and-delegation` 通过；**P2 全部验收通过后**按 `openspec-sync-specs` 同步四份 delta spec 到主规格（见 §0.5：P1 结束时主规格尚未更新）
