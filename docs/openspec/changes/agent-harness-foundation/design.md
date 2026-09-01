# agent-harness-foundation Design

## Context

项目当前定位"Corporate RAG 问答系统"，产品目标转向"企业智能助手 harness"：聊天底座 + 可插拔能力（知识库/RAG 是其中一个工具加载的能力），后续可外挂其他知识库或功能（tools/MCP）。线上问题"腾讯这几年业绩只答 2024"暴露编排工程缺口：无验证循环、无时间结构化约束、工具系统硬编码。

现状关键事实（源码核实）：
- agent 图为 `kb_router → agent → tools → agent_finalize → format`（`src/agents/graph/workflow.py`），本质已是通用 agent 循环
- 工具为 `make_rag_tools` 硬编码 3 个（retrieve_kb / ask_user / search_web），闭包注入（`src/agents/tools/rag_tools.py`）
- `RequestContext.tool_contexts` 已是全局工具结果收集器，引用编号体系全局递增
- `_truncate_history` 已有历史窗口截断（短期记忆 Redis 会话 + 7 天 TTL 已工作）
- 候选年份数据源存在：KB 文档 `meta_info` 有 year / report_period 实体（MySQL `document` 表）

约束：单 Agent 阶段先行，多 Agent/subAgent 留待 P2；坚持"最小改动、手术刀修改"；测试须 mock 外部依赖。

## Goals / Non-Goals

**Goals**
- 建立时间结构化约束，修复"这几年"解析错误，缺失年份由代码算出并注入检索/搜索
- 建立答案验证循环（完整性 + 忠实度），生成后不通过触发修订，限轮转拒答/转人工
- 工具系统注册表化，支持注册/启停/依赖注入，为 MCP 与 subagent 留接口
- CLAUDE.md 认知层转 harness 定位

**Non-Goals**（本 change 明确不做）
- 长期记忆（短期已够，且与 RAG 溯源原则冲突）
- kb_router 下移为工具内部逻辑（P1 与验证循环的图改动合并，避免两次动图）
- 错误分类重试 / 输出护栏 / 上下文压缩（P1）
- Multi-Agent / subAgent 架构（P2，单 Agent 调通后）

## Decisions

### D1: 时间解析采用"LLM 从 KB 派生候选集合中选择 + 代码校验"

**方案**：候选年份由 KB 元数据派生；LLM 结合今日日期与候选集合输出结构化 JSON（years），只能从候选中选择；代码校验合法性；缺失年份由代码比对算出。

**备选与取舍**：
- 纯正则解析：新变体（"近几个财年"）要打补丁 → 否决
- 纯 LLM 自由生成年份：本次 bug 的根因（LLM 漏 2023）→ 否决
- **采纳理由**：理解归 LLM（新变体免费）、数字权威归代码（候选框定 + 校验），呼应 When2Tool 研究（prompt 控制工具决策粗粒度，确定性逻辑上移代码层）

### D2: 验证循环采用"结构化校验优先 + LLM-as-judge 补充"

**方案**：`agent_finalize` → `format` 之间新增校验节点。完整性校验用结构化比对（解析出的年份/实体范围 vs 答案覆盖），每次生成后执行（毫秒级）；忠实度校验用 LLM judge 对照引用上下文逐句核对（Self-RAG IsSup 思路），最多 2 轮，超限转拒答/转人工（financial_rag-main `needs_human_review` 模式）。

**judge 模型**：复用 `RAGAS_LLM_MODEL`（默认 `qwen3.8-max`，temperature 固定 0，独立于生产 LLM 的非推理评估模型）。理由：temperature=0 判断稳定、独立模型避免"自己审自己"偏倚、`LangchainLLMWrapper` 封装可复用（`src/cli/eval_ragas.py`）。结构化校验先行（规则，快且权威），LLM judge 仅补忠实度盲区，标记无支撑句子供修订参考、不直接删内容。

**judge 触发时机（grilling 决策）**：完整性校验（正则，快）每次生成后执行；**LLM judge 只在完整性通过后的最终答案运行**——中途"询问联网→重生成"的过程答案不跑 judge，一次问答 judge 最多 1-2 次（最终版 + judge 打回后的修订版，受 2 轮上限约束）。

**定位说明**：验证循环是所有生产 harness 的标配（harness 12 模块第 10 项，业界公认"demo 与生产"分界线）。编码 agent（claude-code/codex）用规则反馈（测试/linter/类型检查）验证——产物可执行；本项目是 RAG，答案不可执行验证，故用"结构化规则 + LLM judge"组合。deepseek-harness 目前只有工具错误回喂，无独立答案评审，属其缺口而非行业标准。

**备选与取舍**：
- 纯 LLM 自查：无外部锚点自纠错不可靠（zylos 2026 研究一致结论）→ 否决
- 纯规则校验：覆盖不了忠实度（语义判断）→ 否决
- 独立配置专用 judge 模型：多一个模型管理成本 → 否决（复用 RAGAS_LLM_MODEL 已满足）
- **采纳理由**：财务场景确定逻辑用代码（年份缺失可精确比对），语义判断用 LLM，各取所长

### D3: 工具注册表化采用"注册表 + 每工具独立 handler"

**方案**：新增 `ToolRegistry`（注册/启停/依赖注入/返回启用工具列表），`make_rag_tools` 改为向注册表注册 3 个工具后取列表；每个工具保留独立 schema 与执行函数；预留 MCP 适配器入口。

**备选与取舍**：
- 保持闭包注入硬编码：加能力要改工具工厂 → 否决（阻塞定位转变）
- **采纳理由**：为"外挂知识库/功能（tools/MCP）"铺路；subagent 未来也是一个注册工具（codex 模式）

### D4: 校验节点作为 LangGraph 独立节点，不动 kb_router

**方案**：`workflow.py` 在 `agent_finalize` 与 `format` 之间插入 `verify` 节点（条件边：通过→format，不通过→触发补充或转拒答）；kb_router 保持现状（P1 再下移）。

**缺失处理（grilling 决策）**：完整性校验检测到答案缺失年份时，**不自动修订补充**，而是通过 `ask_user` 机制（复用 `clarify_channel` 投递，同现有澄清卡片）询问用户"是否联网补充缺失年份"——用户确认后才触发 search_web 联网；用户拒绝则返回现有答案并标注"知识库仅覆盖 X 年"。**会话内记住确认**（`RequestContext.web_confirmed`）：用户在某会话确认过"需要联网"后，后续缺失年份不再询问，直接联网。**verify 的"是否联网"询问独立计数**（不计入 `MAX_ASK_PER_TURN`，每轮最多询问 1 次，避免与 LLM 澄清互相挤占额度）。

**纯对话（未绑定 KB）轻量自检（grilling 决策）**：verify 节点仅在绑定 KB 时生效（无检索证据可核对）；纯对话场景跳过 verify，改用 **claude-code 式 prompt 行为准则**（零额外 LLM 调用）：system prompt 要求"不确定/无法验证的内容如实说明、不编造；可联网核实就联网；不把没查证的当查证了"（呼应 claude-code `prompts.ts` 的忠实报告准则）。

**备选与取舍**：
- 现在下移 kb_router：单独动一次图 + 改一批测试 → 否决（P1 与验证循环一起动图，图只改一次）

### D5: 时间约束实现位置——工具内部而非独立节点

**方案**：时间解析作为 `retrieve_kb` 工具调用前的内部步骤（或独立可复用的解析函数），解析结果写入 `RequestContext`（`temporal_years` / `missing_years`），供验证循环读取做完整性验收标准。

**触发条件（grilling 决策）**：解析前用**时间词正则粗筛**（`近|这|上|今|去|几|最近|前几年` 等），仅命中才调 LLM 解析；未命中直接走"无时间约束"路径（零成本）。**仅当会话绑定 KB 时执行**（未绑定 KB 不调 retrieve_kb，自然跳过时间解析）。

**备选与取舍**：
- 独立图节点：多一次动图、多一个 LLM 调用路径 → 否决（工具内更贴合"按需解析"）
- 每次检索都解析：无时间词查询白调 LLM → 否决（正则粗筛先行）
- **采纳理由**：验证循环的完整性验收依赖解析输出，两者通过 `RequestContext` 共享；不动图

### D6: 缺失年份 → 联网触发机制——分场景，用户确认后才联网

**方案**（grilling 决策，推翻原"注入给 LLM 自主调 search_web"）：

| 场景 | 联网触发 |
|------|---------|
| **选了知识库** | 缺失年份 → **verify 检测 → ask_user 询问"是否联网" → 用户确认才调 search_web**；拒绝则按 KB 数据回答并标注缺失；会话内已确认（`web_confirmed`）则直接联网 |
| **没选知识库** | 不走 RAG，纯对话 → **LLM 自主判断**是否联网 |

**备选与取舍**：
- 代码自动调 search_web：绕过用户知情同意，财务数据可靠性无保障 → 否决
- 注入给 LLM 让它自主调（原方案）：LLM 不靠谱（本次 bug 根因）、且绕过了用户对联网数据的知情 → 否决
- **采纳理由**：有 KB 时"数据为准 + 联网需知情同意"（联网数据可能与 KB 冲突/质量不可靠）；无 KB 时本就纯对话，LLM 自主是自然行为

**search_web 多查询升级（grilling 决策）**：确认联网后缺失多个年份（如 [2023, 2025]）时，`search_web` 升级为 `queries: list[str]` 数组（deepseek-harness `web_search` 模式，最多 4 个 query × 每 query 若干结果），一次工具调用覆盖所有缺失年份——省 `web_count` 额度、省 agent 迭代（每次迭代都是 LLM token 成本）。

### D7: KB = RAG 开关，跨库检索废弃（grilling 决策）

**方案**：会话绑定 KB 才启用 RAG 检索；**未绑定 KB 时 `retrieve_kb` 不出现在 LLM 工具列表**、system prompt 不引导检索，agent 走纯对话/联网。后端 `kb_id=""` 语义从"搜索所有知识库（跨库）"改为"不检索"（`kb_router_node` 空值时 `_resolved_kb_ids=[]`，retrieve_kb 即便被调也返回空）。**跨库检索本轮废弃**（UI 不提供"全部知识库"选项），未来如需以"知识库多选/分组"方式加回（P2）。

**备选与取舍**：
- 保留隐式跨库（kb_id="" → kb_router 路由）：与"KB 会话级绑定"、"没选不调 RAG"冲突 → 否决
- **采纳理由**：先跑通单库 + harness 底座；契约语义明确化（kb_id 空 = 无 KB，非跨库）

## Risks / Trade-offs

- [验证循环增加延迟与 token 成本] → 结构化校验优先（快）、LLM judge 限 1-2 轮；终止条件硬上限
- [LLM judge 误判（好答案打回 / 错答案放过）] → 结构化规则优先，LLM judge 仅补充忠实度；财务数字用规则比对兜底
- [工具注册表化重构影响现有测试] → 纯重构不改行为，回归测试保障；新增注册表单测
- [时间解析依赖 KB 元数据质量（缺失/不准）] → 元数据缺失时走"无时间约束"默认路径，不阻塞主流程
- [验证循环与前置解析耦合（完整性验收依赖解析输出）] → 解析失败时校验退化为仅忠实度检查，不空转
- [本 change 不动 kb_router，架构名实暂不符] → 认知层（CLAUDE.md）先行，P1 完成架构落位，期间功能不受影响

## Migration Plan

按依赖顺序分 5 步（**后端先行，UI 最后**），每步可独立验证：
1. **CLAUDE.md 认知层**（文档，零风险）：标题/角色/技术栈/参考项目优先级
2. **时间结构化约束**：时间词正则粗筛 + 解析模块 + `retrieve_kb` 内接入 + `RequestContext` 扩展 → 验证：问"腾讯这几年业绩"解析出 [2023,2024,2025] 且缺失 [2023,2025]
3. **验证循环**：`workflow.py` 插入 `verify` 节点（含 ask_user 询问确认、`web_confirmed` 会话内记住）→ 验证：缺失年份触发"是否联网"询问，确认后补充；可开关
4. **工具注册表化 + KB=RAG 开关**：`ToolRegistry` 替换 `make_rag_tools`；未绑定 KB 时不注册 retrieve_kb、`kb_id=""` 语义改"不检索" → 验证：全量 pytest 通过，工具启停生效，空 kb_id 不检索
5. **聊天页 UI（chat-harness-ui）**：按 `docs/design/pages/chat-harness.md` + `frontend-design` skill 落地 `chat.html` → 验证：playwright 对照设计稿

回滚策略：每步独立提交；验证循环节点与时间解析可用配置开关（env）即时关闭；注册表为纯重构，行为不变，回退即恢复旧工厂。

## Open Questions

- **"这几年"默认年份窗口**：候选窗口取最近 N 个完整年度（N=3）还是 KB 有几年算几年？倾向"候选 = KB 覆盖 ∪ 最近 3 年"，需业务确认
- **工具注册表的配置化**：注册表是否需要读取配置文件（yaml）声明启停，还是仅代码注册 + env 开关？倾向先代码注册 + env 开关，配置化 P1 再考虑
- **"是否联网"确认的 UX 文案**：询问卡片的文案与选项（"需要/不需要"）定稿，实施时与前端确认
