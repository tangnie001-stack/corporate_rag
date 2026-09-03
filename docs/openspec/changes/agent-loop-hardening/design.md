# agent-loop-hardening Design

## Context

当前查询链路分两态，agent 循环的 verify/检索逻辑在函数内以 `if not state._resolved_kb_ids` 分叉：

```
态 A · 未选知识库（纯对话，对齐 deepseek-harness / claude-code）
  kb_id="" → retrieve_kb 空返回（KB=RAG 开关硬保证）
  verify 只做 web 引用引导（bug1 修复），不做年份完整性/judge
  时间解析不触发（kb_ids 空短路）

态 B · 选了知识库（RAG 链路）
  retrieve_kb 真检索 → 时间解析（temporal_years/missing_years）
  verify 做完整性（缺失年份→询问联网）+ 忠实度 judge
```

P0 遗留三问题：verify regen×迭代耦合（空白答案 bug）、kb_router 空壳节点 + KBRouter 死代码、KB 答案无溯源。verify_node.py 已 323 行逼近红线。

## Goals / Non-Goals

**Goals:**
- verify regen 与主循环迭代上限解耦（修复空白答案 bug）
- kb_router 节点下移，`_resolved_kb_ids` 退役，KBRouter 死代码清理
- KB 答案强制溯源（态 B 无 [n] → 引导重生成）
- verify_node.py 结构重组（verify 包化，两态校验流程由 verify_node 按态直接编排），满足 400 行红线

**Non-Goals:**
- 不消费 judge `_unsupported` 打回修订（忠实度修订驱动留 P2）
- 不做"转人工"升级路径（产品语义未定义）
- 不改 agent 主循环的 tools 执行逻辑（regen 只在 verify 侧拆解）
- 不做错误分类重试（工具失败结构化回喂，另行评估）
- **不做季度/半年度粒度精确比对（C1-4 已砍，2026-09-03）**：completeness 触发面窄（仅时间词 query），Period 结构升级性价比不足，后续单独评估

## Decisions

### D1: verify 结构 = 两态校验流程，verify_node 按态直接编排各校验模块

**关键修正**：态 A 与态 B 是**两套不同的校验流程**，不是一套 CHECKS 硬塞。verify 包化后：

```
verify/ 包
├── node.py           verify_node 主函数：按 kb 分派两态流程，<80 行
├── checks.py         completeness（按年份覆盖判定）/ extract_years
├── guardrails.py     web_citation_guard（态A）/ kb_citation_guardrail（态B）
├── regen_decision.py decide_missing_web（态B 缺失决策化：询问→决策 regen/直通）
├── ask_confirm.py    _ask_web_confirm（态B 询问联网）
└── faithfulness.py   judge 调用（态B 最终答案，仅标记不驱动流程）

verify_node 编排：
  if 态 A（无 kb）:  仅 web_citation_guard，通过即直通 format
  if 态 B（有 kb）:  completeness 缺失 → decide_missing_web；
                     通过 → kb_citation_guardrail → faithfulness_check
  共享: regen 预算 _verify_regenerations（C1-2）在两态都生效
```

**实现注（2026-09-03 final review）**：早期方案规划的"校验器管道执行器"（pipeline.py：
遍历校验器列表，None=通过 / dict=决策，短路返回）在最终评审中删除——真实编排是异构
直连：completeness 缺失分支的决策化需要 required/missing 额外参数，faithfulness 是
终端 annotator（输出 `_unsupported` 不驱动 regen），都套不进 None=通过/dict=决策 的
统一协议，pipeline.py 自始至终无调用方，属死代码（YAGNI）。现行形态即上表：
verify_node 直接编排各态校验模块（web_citation_guard / decide_missing_web /
kb_citation_guardrail / faithfulness）。若 P2 校验器数量增长使统一协议执行器重新
有价值，可再评估引入。

**理由**：态 A 的 verify 语义是"联网引用完整性"（claude-code 式轻量自检由 prompt 覆盖），态 B 是"知识库覆盖性 + 溯源 + 忠实度"——职责本质不同，必须按态分派。之前单 CHECKS 设计漏了态 A，本次修正。

### D2: verify regen 决策化 = 解耦主循环计数 + 依据 agent 动作决策是否重试

**现状耦合与缺陷**：`_agent_iterations` 身兼两职——route_agent 用它限制主循环（≥5 强制收尾），verify 复用它判断能否 regen。且 verify 只会"发现问题→盲目 regen"，不看 agent 上一轮干了什么——即使联网查了没结果也继续空转，靠 `_agent_iterations` 涨到上限硬停（死循环隐患 + 无效重试）。

**方案（两层）**：

**第一层：计数解耦**（消除"regen 被主循环上限吞掉"的 bug）
```
AgentState 新增: _verify_regenerations: int = 0
const 新增: MAX_VERIFY_REGENERATIONS = 2

route_agent / agent_node 侧：_agent_iterations 上限只管 agent→tools 主循环，不再被 verify 复用
```

**第二层：verify 决策化**（依据 agent 上一轮实际动作，决定是否值得重试）
```
verify 态B missing 分支（用户已确认联网后）：

  分析上一轮 agent 是否调过 search_web 及其 queries 覆盖度
  （读 messages 里最近的 search_web tool_call 的 args.queries）：
    ├─ 没调过 search_web → 值得试一次 → 注入指引 SystemMessage → regen
    ├─ 调了，但 queries 没带全 missing 年份
    │     → agent 没执行好（search_web 支持一次并行多 query）
    │     → 值得再试 → regen（指引强调"一次带全所有缺失年份"）
    └─ 调了，且 queries 已带全 missing 年份，答案仍缺
          → 网上真没有 → 不再重试 → 标注"知识库与网络均未覆盖 {missing}"直通
```

**预算角色转变**：`_verify_regenerations` 从"主动终止条件"降级为**兜底保险丝**——正常流程应被第二层的决策判定提前终止（试一次 / 试漏重试一次 / 带全还缺即停），`MAX_VERIFY_REGENERATIONS=2` 只防极端情况（如 agent 反复不按指引执行）下 verify→agent 无限往返。

**两计数保留 + 防线分层（2026-09-03 grilling 确认）**：决策化主导，但 `_agent_iterations` 与 `_verify_regenerations` 都保留，各自防线不同、不可相互替代：

```
失控 1 · agent 主循环内无限调工具（agent↔tools）
  LLM 自主反复调 retrieve_kb，永远不 finalize → verify 根本没机会跑
  → 由 _agent_iterations 拦（route_agent 强制收尾）
  → 必须留：决策化发生在 finalize 之后，够不着这一段

失控 2 · verify 让 agent 无限重做（verify→agent）
  → 主逻辑由决策化终止（正常永不依赖计数）
  → _verify_regenerations 是保险丝：决策化失灵时兜底
     （agent 不配合/反复带漏/messages 解析失败 → 决策化自卡死）
  → 建议留：正常用不到，但保证决策化失效时不死循环
```

**决策化盲区（已知边界）**：verify 看 queries 带全没带全，区分不了"网上真没有"与"搜索服务失败返回空"两种情况——当前按"都判没结果处理"，由保险丝兜底；如未来要纯证据决策，需再读 ToolMessage 区分执行成败（YAGNI，暂不做）。

**参考项目对照（2026-09-03）**：本设计的计数+上限+条件判定思路源自 **financial_rag-main**——其 `state.py` 有 `retrieval_iterations/max_retrieval_iterations=2` 与 `regenerate_count/max_regenerate_count=1`，`conditional.py` 的 `route_after_grader`/`route_after_faithfulness` 用"分数 < 阈值且未达上限 → 改写/重生成，否则放行"的条件路由终止循环。但 financial_rag 是**确定性节点链**（grader→rewrite→retrieval→grader，节点无 LLM 自主权），简单计数即可；本项目是 **LLM 自主 agent 循环**（agent 自己决定调不调 search_web），verify 须看 agent 实际动作而非只靠计数——这是本设计相对参考项目的必要延伸。

**claude-code** 采用不同的规避路径：AgentTool 有 `maxTurns`，达上限返回 `max_turns_reached` 附件给主 agent（降级信号）；其 verification agent 做**一次性验证输出 `VERDICT: PASS/FAIL/PARTIAL`**，不做"生成→自评→反复重生成"循环——因为验证对象是代码（可执行），一次真跑 build/test 即可定论，FAIL 交给实现者改。本项目 RAG 答案不可执行验证，无法一次定论，故保留有限修订循环。

**deepseek-harness** 无此类 verify 完整性→联网→重生成机制，其自我纠错是工具失败信息回喂模型（对应 P1"错误分类重试"候选，非本 change 范围）。

结论：C1-2 的决策化（看 agent 行为证据）在参考项目基础上更进一步，源于本项目 agent 循环自由度高；两计数保留作地基护栏与兜底保险丝，符合"验证靠证据、计数只兜底"的分工。

**预算共享语义**：态 A 的 web 引用引导 regen、态 B 的完整性联网 regen、KB 护栏 regen 共享保险丝预算。一次问答典型消耗：完整性联网补一轮 + KB 护栏补一轮 = 恰好 2；忠实度打回不消费（P2 再议）。

**bug 场景修复验证**：首轮 agent 耗 4 次迭代 → verify 确认联网 → regen（走决策化，不再受 `_agent_iterations` 上限影响）→ 回 agent 用完整主循环预算执行 search_web → 产出答案。route_agent 不再因主循环已达上限而吞掉 regen 轮的工具调用。

**"查了没结果"终止验证（根本修复）**：缺失 [2023, 2025] → 确认联网 → verify 注入指引（"一次带全缺失年份"）→ agent 调 `search_web(queries=["腾讯 2023 年报", "腾讯 2025 年报"])` → 网上无 2023 → 答案仍缺 → verify 看到 queries 已带全但仍缺 → **判定网上真没有 → 标注直通**，不再空转。无需等到保险丝计数耗尽。

### D3: 输出护栏 = 态 B 专属校验器（加排除条件）

```
触发条件（全部满足才拦）:
  ① tool_contexts 含 kind=kb 的 context（确实调了 retrieve_kb 拿到结果）
  ② 答案不含 [n] 引用标记
  ③ 答案非拒答（不含 ABSTENTION_MARKERS）
  ④ 答案非"知识库未覆盖"措辞（模型明说没用到文档时不强灌引用）

动作: 注入含 KB citation marker 的 SystemMessage → 置 regen（消耗预算）
防重复: 复用 _citation_guidance_already_injected 查重（KB marker 独立）
```

**排除条件理由**：避免误伤"检索了 KB 但模型判断结果与问题无关"的场景——此时强灌 [n] 会把不相关来源标进答案，比不标更糟。

### D4: kb_router 下移 = 删节点 + `_resolved_kb_ids` 退役

```
workflow.py:   删 kb_router 节点, entry_point 直连 "agent"
state.py:      删 _resolved_kb_ids 字段 + LangGraphNode.KbRouter
消费点换源:
  rag_tools.retrieve_kb:   kb_ids = [state.kb_id] if state.kb_id else []
  verify_node:             态判定 not state._resolved_kb_ids → not state.kb_id
  ask_tools._load_dimension_options: 读 state.kb_id
```

**理由**：P0 废弃跨库后 `_resolved_kb_ids` 只是 `[kb_id]` 镜像，无路由语义。下移后图名实相符，每请求省一次节点跳转。此改动与 D1-D3 同 change 完成"图最后动一次"。

## 两条路线（态 A / 态 B）完整方案对照

| 环节 | 态 A（未选 KB，纯对话） | 态 B（选 KB，RAG） |
|---|---|---|
| kb 判定 | `not state.kb_id` → 纯对话（D4 换源后） | `state.kb_id` 非空 → 检索该库 |
| retrieve_kb | 空返回（KB=RAG 开关，工具内已实现） | 真检索（dense+bm25→rrf→dedup→rerank） |
| 时间解析 | 不触发（kb_ids 空短路） | 正则粗筛 → LLM 年份解析 |
| verify 编排 | web_citation_guard：调过 search_web 但答案无 [n] → 引导补标注重生成 | completeness 缺失 → decide_missing_web 决策；通过 → kb_citation_guardrail → faithfulness（D1，node 按态直连） |
| 完整性 | 不校验（prompt 行为准则覆盖） | 缺失年份 → 询问联网（web_confirmed 单轮记住） |
| KB 溯源护栏 | 不适用（无 KB context） | D3：有 kb context 无 [n] → 引导（含排除条件） |
| 忠实度 judge | 不跑 | 最终答案跑 judge（_unsupported 仅记录，修订 P2） |
| regen 预算 | 消耗（web 引用引导 regen） | 消耗（完整性联网 + KB 护栏），共享 `_verify_regenerations`（D2） |
| 终止 | 无年份要求，引导 1 次后直通 | `_verify_regenerations>=2` 标注缺失直通（软拒答） |

## Risks / Trade-offs

- [regen 预算=2 可能不够（联网补一轮 + 护栏补一轮正好耗尽，judge 修订无余量）] → 忠实度修订留 P2 独立预算；若手测发现预算紧张，const 调整即可
- [KB 护栏误伤"检索了但认为无关"场景] → D3 排除条件（非拒答 ∧ 非"知识库未覆盖"），最坏"该引导没引导"比"强灌无关引用"安全
- [两态校验增加 verify 组织复杂度] → 各校验模块独立、由 verify_node 按态直连编排（统一执行器协议已评估删除，见 D1 实现注）；新增校验逻辑=往对应模块加函数或加条件分派，主函数守住红线
- [verify_node.py 拆包影响测试] → 纯重构零行为变化先行（C1-5 独立 commit + 全量回归），后续改动基于稳定地基
