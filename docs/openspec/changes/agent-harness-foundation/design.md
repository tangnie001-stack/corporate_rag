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

**方案**：`workflow.py` 在 `agent_finalize` 与 `format` 之间插入 `verify` 节点（条件边：通过→format，不通过→补检索/修订→回 agent 或直接转拒答）；kb_router 保持现状（P1 再下移）。

**备选与取舍**：
- 现在下移 kb_router：单独动一次图 + 改一批测试 → 否决（P1 与验证循环一起动图，图只改一次）

### D5: 时间约束实现位置——工具内部而非独立节点

**方案**：时间解析作为 `retrieve_kb` 工具调用前的内部步骤（或独立可复用的解析函数），解析结果写入 `RequestContext`（`temporal_years` / `missing_years`），供验证循环读取做完整性验收标准。

**备选与取舍**：
- 独立图节点：多一次动图、多一个 LLM 调用路径 → 否决（工具内更贴合"按需解析"）
- **采纳理由**：验证循环的完整性验收依赖解析输出，两者通过 `RequestContext` 共享；不动图

### D6: 缺失年份 → search_web 触发机制采用"注入给 LLM，不自动调"

**方案**：代码算出缺失年份后，不作为自动工具调用，而是作为 `retrieve_kb` 工具结果的附加信息注入给 LLM（如"知识库仅覆盖 2024 年，缺失 2023/2025，请对缺失年份调用 search_web"）。LLM 看到明确缺失清单后自主调用 search_web 补充。

**备选与取舍**：
- 代码自动调 search_web：绕过 LLM 决策，与"工具由模型驱动"架构矛盾、丢失 LLM 对检索词的构造能力 → 否决
- 仅写入 `RequestContext` 不注入消息：LLM 看不到，不会触发搜索 → 否决
- **采纳理由**：缺失清单是结构化信号，但执行搜索仍是 LLM 决策；与 P1"错误分类重试"（错误回喂模型修正）机制衔接；实现为"检索结果文本追加缺失说明段"，改动小、可测试

## Risks / Trade-offs

- [验证循环增加延迟与 token 成本] → 结构化校验优先（快）、LLM judge 限 1-2 轮；终止条件硬上限
- [LLM judge 误判（好答案打回 / 错答案放过）] → 结构化规则优先，LLM judge 仅补充忠实度；财务数字用规则比对兜底
- [工具注册表化重构影响现有测试] → 纯重构不改行为，回归测试保障；新增注册表单测
- [时间解析依赖 KB 元数据质量（缺失/不准）] → 元数据缺失时走"无时间约束"默认路径，不阻塞主流程
- [验证循环与前置解析耦合（完整性验收依赖解析输出）] → 解析失败时校验退化为仅忠实度检查，不空转
- [本 change 不动 kb_router，架构名实暂不符] → 认知层（CLAUDE.md）先行，P1 完成架构落位，期间功能不受影响

## Migration Plan

按依赖顺序分 4 步，每步可独立验证：
1. **CLAUDE.md 认知层**（文档，零风险）：标题/角色/技术栈/参考项目优先级
2. **时间结构化约束**：新增解析模块 + `retrieve_kb` 内接入 + `RequestContext` 扩展 → 验证：问"腾讯这几年业绩"解析出 [2023,2024,2025] 且缺失 [2023,2025]
3. **验证循环**：`workflow.py` 插入 `verify` 节点 → 验证：生成后完整性/忠实度检查生效，可开关
4. **工具注册表化**：`ToolRegistry` 替换 `make_rag_tools` → 验证：全量 pytest 通过，工具启停生效

回滚策略：每步独立提交；验证循环节点与时间解析可用配置开关（env）即时关闭；注册表为纯重构，行为不变，回退即恢复旧工厂。

## Open Questions

- **LLM judge 模型选择**：复用现有 flash（便宜但精度存疑）还是独立配置一个 judge 模型？倾向复用 flash + 结构化兜底，P0 先不做模型升级
- **"这几年"默认年份窗口**：候选窗口取最近 N 个完整年度（N=3）还是 KB 有几年算几年？倾向"候选 = KB 覆盖 ∪ 最近 3 年"，需业务确认
- **校验不通过后的动作**：自动修订（重生成）优先还是直接标注缺失？倾向"先修订 1 次，仍不达标标注缺失并保留已引用内容"，需确认
- **工具注册表的配置化**：注册表是否需要读取配置文件（yaml）声明启停，还是仅代码注册 + env 开关？倾向先代码注册 + env 开关，配置化 P1 再考虑
