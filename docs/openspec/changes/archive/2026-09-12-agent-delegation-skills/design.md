> **SUPERSEDED（2026-09-08）**：本文档 delegate-observability 相关设计——「D14 SSE 状态」
> （STAGE_DELEGATE 推送开始/结束）与「D11 fork 可观测性」（同级观测、不产生 MODEL_TURN）
> ——已由 delegate-hardening-observability 演进（`delegate` 事件 start/delta/end、过程增量、
> 完成/中断区分、delegate 轮次日志）；其余设计（skill 加载器 / delegate_task 双执行 /
> fork 上下文隔离 / 迭代预算联动 / 懒重载等）不变。

## Context

主 agent 是单循环 LangGraph（agent ↔ tools → agent_finalize → verify → format，`src/agents/graph/workflow.py`）。能力全以工具平铺（retrieve_kb/search_web/ask_user）。企业多业务域演进需要"领域专家能力"，但多域专家人格无法全量进主 agent 上下文；领域能力需业务侧可增删改、与代码解耦。

参照物：claude-code（主从委派范式：AgentTool + skill frontmatter `context: fork`）、deepseek-harness（skill/配置机制：system-prompt 注册表 + scope）、agency-agents（子代理预设内容库：finance 域 5 个财务专家人格）。三者抽象出本方案：**主 agent 保留全局执行 + delegate_task 工具按需调 skill；skill 是声明式能力文件，加载成运行时注册表**。

## Goals / Non-Goals

**Goals:**
- 主 agent 能通过 `delegate_task(task, skill)` 按需调用领域能力，无需改代码加能力
- skill 是磁盘声明式文件（`skills/<name>/SKILL.md`），业务侧可增删改、免重启生效
- 两种执行形态：inline（skill 指令注入主 agent，主 agent 自己答）+ fork（生成独立子代理深度执行，隔离上下文）
- fork 子代理的可观测性、超时兜底、结果回流质量闸门与现有体系一致
- 与现有 verify/format/引用编号体系兼容（fork 结果不带 [n]，主 agent 重组后走主图校验）

**Non-Goals:**
- 不做协调器式多 agent（financial_rag 的 orchestrator/registry 路由）——本场景是"主 agent 委派执行者"，非"多执行者协作"
- 不做单 agent 分档（同一主 agent 换温度/模型）——skill 的 model/thinking 是 skill 属性，非全局档位
- 不做 fork 子代理内部 token 透传给前端（首版只推 delegate 开始/结束状态）
- 不做子代理 verify/format（fork 子代理无检索工具，verify 是主 agent 检索链闸门）
- 不做 skill 版本管理（单 team 演进，目录就地更新即 v-next）
- 不做 skill 间通信/组合编排

## Decisions

### D1. skill = 声明式能力文件，运行时注册表（SkillRegistry）
`skills/<name>/SKILL.md`（项目根顶层目录，与 docs/src 平级）。frontmatter + 正文。加载成 SkillRecord，注册进进程内 SkillRegistry。**skill 是内容/配置，不是代码**——业务解耦核心。
**目录语义区分**：顶层 `skills/` 是**运行时 skill 内容库**（本 change 使用，业务侧管理）；项目已有 `.claude/skills/` 是**开发期 skill**（openspec 等工具链使用）。两者语义不同、互不读取——文档/README 需明确区分，避免放错目录。

### D2. skill frontmatter 字段
```
name / description（委派匹配依据）/ context（inline|fork 默认值）/
model（fork 覆盖，空=继承主 agent）/ thinking（fork 是否开思考）/
allowed-tools（fork 子代理工具白名单，**预留字段，本版恒不启用**——fork 零工具见 D7）/
max-iterations（fork 迭代上限，0=继承；**零工具下无工具循环、单轮直出最终答案，本版该字段不生效**——与 allowed-tools 同为预留，allowed-tools 启用后才有意义）
```
用 PyYAML 解析（已可用 6.0.3）。

### D3. 一领域拆两 skill（inline 方法论 + fork 专家人格），非单 skill 双模式
inline skill 正文 = 短方法论（主 agent 学方法自己答）；fork skill 正文 = 完整专家人格（agency-agents 裁剪适配后作子代理 system_prompt）。两者正文形态不同，拆开各自清晰。

### D4. context 是默认值，主 agent 运行时判断是否升级到 fork
skill 的 description 写清"轻量问题 inline / 深度问题 delegate fork skill"。主 agent 拿到 inline 指引后若需深析，可再 delegate fork skill。

### D5. delegate_task 工具（新增主 agent 工具）
`delegate_task(task, skill)`。description 动态生成（registry.to_tool_description()：只列 skill 名 + 一句话 whenToUse，渐进披露，预算截断）。skill 的 context 决定执行路径。

### D6. inline 执行：skill 指令注入主 agent
context=inline → delegate_task 返回 skill 的 inline_prompt（含 {task} 占位）。主 agent 收到指引后自己走正常循环执行。**不消耗独立子代理**。
**内容规模约束**：inline_prompt 应控制在短方法论规模（≤500 字）——内容注入后留在 messages 历史，长文会造成上下文累积膨胀（多轮 inline 不同 skill 会累积）。领域长文内容应建模为 fork skill（独立子代理上下文，不占主对话）。

### D7. fork 执行：create_react_agent 子代理，零工具

> **已被修订**：本 D7 已被 `session-agent-and-skill-invocation` 修订（fork 工具放开 + 执行者选择），以该 change 的 design D6/D7 为准。

context=fork → delegate_task 内部用 create_react_agent 起独立子代理。**子代理工具集恒空**——材料由主 agent 先检索好塞进 task。
**为何恒空而非 allowed-tools 白名单**：现有业务工具（retrieve_kb/search_web）均写主请求共享的 `RequestContext.tool_contexts`（单例 ContextVar）。子代理若持这些工具，调用时会污染主 agent 的 tool_contexts（引用编号错乱）与 ask/pending_asks（澄清槽冲突）。给子代理做独立 RequestContext 隔离成本高（子 ctx + 独立编号 + 结果合并），属已知扩展点（见 Risks）。首版零工具使 D7 隔离承诺无例外。`allowed-tools` frontmatter 字段**保留但标注预留**，本版恒不启用。
**构建期解耦（防循环依赖）**：SkillExecutor **不持有 make_rag_tools 实例**。make_rag_tools → delegate_task → SkillExecutor →（若再依赖 make_rag_tools 产工具）→ 循环。零工具下 executor 无需构造子代理工具集，循环依赖天然消失。

### D8. fork 子代理上下文隔离
子代理独立 create_react_agent（独立消息历史 + 独立 system_prompt=skill 正文 + 独立工具集）。看不到主对话历史，只收 task。**关键**：fork 子代理不共享主 agent 的检索通道。

### D9. fork 结果不带 [n]，主 agent 重组
fork 子代理返回**纯分析文本**（无引用编号）。主 agent 整合后用自己的 tool_contexts/引用体系走 verify → format。避免子代理答案里的 [n] 与主 agent 上下文编号错乱。
**引用来源声明**：主 agent 重组时的引用仅指向其自身 tool_contexts 中的来源（预检索的 chunk）；子代理分析文本**不写入 tool_contexts**、不作为可引用来源——format 不会为子代理分析配 citation。主 agent prompt 需引导"引用检索来源，不把专家分析当来源"。
**专家分析豁免（态 B 溯源护栏，M7 回归）**：纯分析型 fork 答案（零 [n]、有 kb context）会被 `kb_citation_guardrail` 误判为"检索到 KB context 但答案无引用"而触发一次补标 regen——对只给专家观点、无检索依据的答案这是误伤。约束：4.1 引导主 agent 把无源专家观点措辞为 `EXPERT_ANALYSIS_MARKER`（const.py，"基于领域经验的分析"），`kb_citation_guardrail` 对含该短语的答案豁免补标 regen。该短语是 prompt 与代码的共享约定（同文锁定，见 4.1 测试），检索事实仍须带 [n]，防模型用该短语绕过溯源。

### D10. 结果截断
delegate_task 返回截断 ~1000 字结构化摘要（"子代理已产出完整分析 N 字，摘要：..."），防主 agent 上下文膨胀。按需索取（retrieve_delegate 分段取回）**放第二迭代**——YAGNI，无数据证明主 agent 频繁需要细节。

### D11. fork 可观测性（与主 agent 同级观测）
fork 子代理复用主 agent 的 llm 实例（skill 未声明 model/thinking 时）或 `get_llm(model=...)` 新建（声明时）——与主 agent **同级观测**：同一实例自带 callbacks（`get_llm()` 已挂 LlmContentLoggingHandler）。**子代理不打 MODEL_TURN 事件**（那是主 agent 循环日志）；Langfuse 是否捕获取决于现有网关/实例接线，应用层不新增 Langfuse 专项工作（见 Risks「create_react_agent 观测缺口」）。

### D12. fork 模型与 thinking
skill frontmatter `model` 空 → delegate 时复用主 agent llm 实例；写了（如 qwen3-max）→ `get_llm(model=record.model)` 新建（轻，几 ms）。thinking：skill 写了开 → 构造时 extra_body enable_thinking；没写 → 跟随主 agent。

### D13. fork 超时兜底
delegate_task 内 `asyncio.wait_for(..., timeout=DELEGATE_TIMEOUT)`——首版必做，防子代理死循环/外部 API 挂起烧钱。abort_signal 透传给子代理 astream 放后置。

### D14. SSE 状态（仅 fork 推送，工具体内投递）
新增 `STAGE_DELEGATE` + 文案（"正在调用领域专家分析..."/"领域专家分析完成"）进 SSEInteractionTexts（const.py）。**仅 fork 执行期间推开始/结束状态**，前端显示"专家分析中"。inline 命中是主 agent 自己答、无独立子代理，**不推** STAGE_DELEGATE——避免"正在调用领域专家"文案误导用户。

**推送机制**：不走外层 astream_events 的 on_tool_start/end 映射（该事件在工具体运行前产出，无法预知本次 delegate 走 inline 还是 fork）。改为 delegate_task 的 fork 分支内主动投递：fork 开始/完成时经 ctx 事件通道 put `{"type": "status", "stage": "delegate", "phase": "start"|"end"}`（复用 ask_user 的 clarify_channel 先例），`_drain_clarify_channel` 并行消费、`_convert_event` 加 status 分支转 SSEStatusEvent。

### D15. 迭代预算联动
主 agent `MAX_AGENT_ITERATIONS=5`。delegate 轮计入 iteration 但放宽联动：本轮含 delegate_task 时允许 +2 整合余量（delegate 后主 agent 要再答一轮整合子代理结果）。单请求总上限仍封顶防死循环。
**实现**：AgentState 加 `_delegate_used: bool = False`；agent_model 节点在 LLM 输出含 delegate_task tool_call 时置位（该轮工具调用确定含 delegate 即算）；route_agent/超限判定读取：`_delegate_used` 置位时 `_max_agent_iterations + 2`。单请求总上限封顶仍由 verify 保险丝 + 图级 recursion_limit 兜底。
**verify regen 复位（预算语义一致）**：guardrails（web_citation_guard/kb_citation_guardrail）与 regen_decision 的 regen dict 现只复位 `_agent_iterations: 0`（regen = 全新 5 轮预算）；若 `_delegate_used` 残留 True，每段 regen 上限会被放大成 5+2=7，破坏该语义。三处 regen dict 统一补 `_delegate_used: False`。

### D16. 懒重载（免重启）
SkillRegistry 懒重载：delegate_task 调用前查 `skills/` 目录 mtime，变化才重扫。几十 ms 开销，业务"下午三点上新 skill 不重启"诉求。
**前提**：容器部署时 `skills/` 必须以 volume 挂载（`./skills:/app/skills`）——目录在镜像内则改文件不生效，懒重载失去意义。docker-compose.prod.yml 补挂载。

### D17. 命名空间与删除安全
skill 名 = 目录名。注册表 key 冲突 → 加载期报错（fail-fast）。skill 被删 → delegate 返回"skill 不存在"，主 agent 降级自己答（天然安全）。

### D18. 防腐扩展
skill frontmatter 引用的工具名（allowed-tools）纳入 check_docs 扫描（或独立 skill-check）——防工具改名后 skill 静默失效。这是既有防腐机制的自然延伸。

### D19. 首批 skill 内容
1 个 inline（财务问答规则）+ 1 个 fork（从 agency-agents finance 域裁剪 Financial Analyst → 精简到几百字作子代理 system_prompt；fork 子代理零工具，材料由主 agent 预检索塞 task，skill 正文无需声明工具集）。

### D20. 真实图接线与装配（防 delegate_task 注册静默失效）
只改 `make_rag_tools` 签名还不够——`workflow.build_graph` 默认路径（tools=None）裸调 `make_rag_tools(vector_store, bm25, reranker, prompt_manager)` 不会传 delegate_task，生产图会**永不注册该工具**（全量单测仍绿，因单测都显式传 tools 或 mock）。接线约定：
- `build_graph` 增可选参 `delegate_task: BaseTool | None = None`，tools 为 None 的默认分支透传给 `make_rag_tools(..., delegate_task=delegate_task)`；
- `AgentService.__init__` 装配：skills 目录（env `SKILLS_DIR` 覆盖，缺省项目根 `skills/`）存在且注册表非空时构造 `SkillRegistry(SkillLoader(dir))` + `SkillExecutor(self._llm)` → `make_delegate_task`，传入 build_graph；
- **装配可观测**：skills 目录缺失/注册表为空时 delegate_task 不注册，须打 warning（Event 枚举登记 `DELEGATE_SKIP`，app 层前缀，reason=skills_dir_missing/registry_empty）——否则部署 volume 未挂载时主 agent 静默退回三件套无感知；
- **图级接线守卫**：补 `build_graph` 透传单测（monkeypatch make_rag_tools 断言 delegate_task 参数送达），防未来改默认分支漏传。

## Risks / Trade-offs

- **fork 子代理零工具的局限**：子代理不能自主查库，材料靠主 agent 预检索塞 task。若将来要子代理自主检索，需做子 RequestContext 隔离（子 ctx + 独立编号 + 结果合并，成本高），作为已知扩展点——届时 allowed-tools 字段方启用。
- **无中间心跳**：fork 执行（零工具 = 单轮深度 LLM 调用）期间前端仅见 STAGE_DELEGATE 开始/结束，无逐 token 或心跳。零工具下子代理基本是单轮强模型调用，时长与现有生成可比，可接受；若上线后真实反馈"等待焦虑"再加心跳（SSE status 通道已备，成本低）。
- **截断丢细节**：delegate 返回截断到 1000 字，主 agent 若需全文细节暂无法取（第二迭代加 retrieve_delegate）。
- **create_react_agent 观测缺口**：子代理不打 MODEL_TURN 事件，逐轮 usage 不进主 agent 日志体系（Langfuse 兜底）。若需子代理逐轮日志，外围补聚合记录。
- **abort 透传后置**：首版 fork 只做超时兜底，客户端取消后子代理可能继续跑完（后台烧 token）。已知限制。
- **预算放宽的死循环风险**：delegate +2 余量可能被模型滥用（反复 delegate）。依赖单请求总上限 + fork 超时双保险。
- **skill 腐化**：skill 内容（工具名/流程）随代码演进漂移。D18 防腐扩展缓解，叙述级仍需人审——inline skill 正文会引用 retrieve_kb 等工具指令，正文工具名防腐用一致性测试兜底（正文出现的已知工具名须在代码实际注册集合内），与 D18 的 frontmatter 校验互补。
- **模型成本**：fork 用强模型跑整轮是主 agent 倍数成本。靠 skill 显式指定 + 默认继承控制——只有 spawn 才烧。
- **tool description 静态**：delegate_task 的 description 在装配时由 `to_tool_description()` 生成一次。运行期**新增** skill 文件不会自动进入已注册工具的 description（需图重建/重启才被 LLM 看到）；**已注册** skill 的内容修改经 registry 懒重载即时生效。运行期新增 skill 免重启只对"已披露 skill 的内容更新"成立，新 skill 的 LLM 可见性需重启。
- **Langfuse 观测依赖网关层**：fork 子代理与主 agent 同级观测；Langfuse 生成级 span 是否捕获取决于现有网关/实例接线，应用层不新增 Langfuse 专项（见 D11 收敛表述）。若日后需要子代理独立 span/usage 明细，作为独立扩展。
