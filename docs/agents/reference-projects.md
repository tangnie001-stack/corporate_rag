# 参考项目清单

> 本地 `../github/` 下镜像的开源参考项目，用于在对应场景下优先参考其实现模式。
> 按**功能域**分组（要做什么 → 直接进对应域找参考），组内按价值排序（技术栈契合 × 当前痛点匹配 × 可迁移深度，基于 2026-09 对 corporate_rag 的分析）。
> 本清单只承载"项目是什么 + 何时查阅 + 参考价值"；具体架构细节以对应仓库源码为准。
> **例外**：附录另收本地已安装的技能（`~/.agents/skills/`，非 `../github` 镜像），作为"标准 skill 长什么样"的范例。

## 目录

- [域 1：底层依赖（正在使用的官方库）](#域-1底层依赖正在使用的官方库)
- [域 2：同领域 RAG（最贴合参考）](#域-2同领域-rag最贴合参考)
- [域 3：Agent 编排 / harness（找模式）](#域-3agent-编排--harness找模式)
- [域 4：生产化模板 / 平台（参考思路）](#域-4生产化模板--平台参考思路)
- [域 5：RAG 引擎（文档处理互补）](#域-5rag-引擎文档处理互补)
- [域 6：清理候选](#域-6清理候选)
- [附：参考技能（本地已安装）](#附参考技能本地已安装非-github-镜像)

---

## 域 1：底层依赖（正在使用的官方库）

> 项目直接基于、当前在用的官方库。目的不是"找模式"而是"出问题查行为、查 API 用法"。

**langgraph-1.2.10**
- LangGraph 官方源码。StateGraph、流式事件（astream_events）、子图、Checkpoint、Human-in-the-loop。
- **何时查阅**：改 `src/agents/graph/` 下 workflow/state/agent_node 时；排查 graph 执行事件、流式行为时。

**fastapi-0.141.1**
- FastAPI 官方源码。API 路由、中间件、依赖注入、异常处理、响应模型。
- **何时查阅**：改 `src/api/` 路由、`src/middleware/`、全局异常处理器时。

---

## 域 2：同领域 RAG（最贴合参考）

**financial_rag-main**
- 财税法务 RAG 知识库（Python + LangGraph）。与本站同领域，实现了本站缺失/待加强环节：`quality_nodes.py` FaithfulnessChecker（逐句核对 + score<0.7 重生成）、RetrievalGrader（CRAG 式检索评分分流）、`quality_review_function.py` 五维质量评审（最多 2 轮迭代 + needs_human_review）、`reflect_agent.py` 执行→反思→改进；多智能体协作、状态机、知识图谱、混合检索、三层记忆。
- **何时查阅**：做答案校验/评审环节、多智能体协作、混合检索、记忆分层时。

---

## 域 3：Agent 编排 / harness（找模式）

> 本站"主从委派 + skill 化业务解耦"升级路线的主参考域。
> 组内分工：claude-code 提供**委派范式**（AgentTool/skill fork）；deepseek-harness 提供**skill/配置机制层**（注册表/scope）；agency-agents 提供**智能体预设内容库**（现成的领域专家人格，可直接转成 `AgentPresetRegistry` 条目，见 change `session-agent-and-skill-invocation`）。

**claude-code**
- Anthropic 终端编码 agent（闭源，~51 万行 TS；2026-03 因 npm source map 误发布泄露，本地为社区还原版 `claude-code-best/claude-code`）。
- 核心 `queryLoop()`（Async Generator + while(true)，状态机 Compaction→APICall→ToolExecution）+ 六层 harness：43+ 工具 / 5 种权限模式 / 26 事件 × 4 类型 Hooks / 沙箱 / 上下文工程（CLAUDE.md + 记忆 + 四级压缩）/ 多 agent 编排（coordinator，Orchestrator 模式 5 并行子代理）。
- **委派机制**：AgentTool 子代理（`subagent_type` 选预设 Explore/Plan/general-purpose，`whenToUse` 匹配）+ Skill 系统（SKILL.md frontmatter：`context: fork` 起子代理、`agent:` 指定预设、`model/allowed-tools` 控执行）。
- **何时查阅**：设计 agent 混合编排/横切环节/工具系统/主从委派/skill 化时优先参考（委派范式、hooks、权限、压缩、子代理隔离上下文）。

**deepseek-harness**
- DeepSeek 官方开源 agent harness（2026-08 公测，MIT）。TypeScript monorepo（~230 workspace），一切皆插件（Cordis 元框架）。
- 核心 `ReactLoopAgent`（turn/step 双层 ReAct）+ 事件钩子横切（waterfall/serial：plan-mode/goal/guard/compaction 挂 agent/pre-step 等边界）+ 工具内嵌子循环（subagent→独立 Agent、workflow→worker 线程 JS 脚本）+ 外包驱动（goal-round-driver）+ Session Log 事件溯源。
- **skill/配置机制**：system-prompt 注册表（PromptSection order band、插件贡献段落/工具/变量、scope 遮蔽、`{{var}}` 严格插值）——"多域行为协议可插拔"的机制层参考。
- **何时查阅**：做 skill 加载器/配置作用域/事件钩子/上下文压缩/委派执行器时优先参考（横切插件机制、scope 遮蔽、会话可重建日志；TS/Cordis 绑定，参考思路为主）。

**codex**
- OpenAI 官方开源 coding agent（Rust monorepo `codex-rs`，Codex CLI + Harness 全面开源）。核心 `session/turn.rs` ReAct 循环 + **编排工具化**（plan/multi_agents spawn-wait-send_input-resume-close/request_permissions 均为模型可调工具）+ 执行/沙箱/压缩（compact 8+ 变体）/hooks/记忆/worktree。
- **何时查阅**：设计 agent 混合编排/横切环节/工具系统时参考（编排工具化让模型自治、沙箱/权限/压缩；Rust，语言不同，参考模式）。

**Qwen-Agent**
- 阿里官方开源 Python agent 应用框架（`qwen_agent/` 仅 1.28 万行）。Agent 基类（269 行）封装工具调用模板/解析器；多智能体三件套：react_chat（ReAct）/ router（MultiAgentRouter 路由）/ group_chat（多 agent 群聊）；集成 RAG/代码解释器/MCP。无 compaction/hooks/沙箱等 harness 基础设施。
- **何时查阅**：做简单多智能体路由/群聊、工具调用模板时（Python 同语言，参考下限）。

**agency-agents**
- **智能体（子代理）预设内容库**（The Agency，msitarzewski/agency-agents，MIT）。纯内容仓库：19 个 division 目录（engineering/finance/security...），共 ~274 个"专家 agent" Markdown。**是智能体人设定义，不是 skill**（概念区分见 docs/agents/glossary.md「Agent / Skill / Tool」）。
- 每个文件 = frontmatter（`name`/`description` + 展示元数据 `color`/`emoji`/`vibe`，可选 `tools`/`services`/`author`）+ 正文（身份/使命/规则/交付物模板/工作流）。
- `scripts/convert.sh` 把同一份专家转成 16 种工具格式（claude-code `.claude/agents/*.md`、qwen `~/.qwen/agents/*.md`、codex TOML 等）；转 `skill-md`（antigravity/osaurus）时**只保留 `name` + `description`**——佐证 SKILL.md 的标准最小集。
- **与本项目关系**：`AgentPresetRegistry` 的预设内容可直接取自这些 markdown（description 作匹配、正文作 system_prompt）；**change `session-agent-and-skill-invocation` 的首批智能体预设计划取自 finance 域**（需中文化裁剪，仅取 identity / mission / critical rules 三段，避免 prompt 过长）。
- **何时查阅**：做子代理预设/专家人格库、或给委派方案找现成"首批子代理内容"时（内容搬运为主，不涉及机制）。

---

## 域 4：生产化模板 / 平台（参考思路）

**fastapi-langgraph-agent-production-ready-template**
- FastAPI + LangGraph 生产模板。LLM 容错、长期记忆、Rate Limiting、Eval 框架、生产监控。`app/api/v1 + core/langgraph(tools/prompts) + services/llm + models/schemas` 分层与本站同构。
- **何时查阅**：生产化改造（限流、容错、评估、监控、记忆）时。

**dify-1.16.1**
- Dify 官方（LLMOps 平台）。AI 应用编排、工作流引擎、RAG 管道、插件体系。
- **何时查阅**：做可视化工作流编排、RAG 管道拆解、插件化扩展时（平台型，迁移成本高，参考思路为主）。

---

## 域 5：RAG 引擎（文档处理互补）

**ragflow-0.26.4**
- RAGFlow 开源 RAG 引擎。DeepDoc 文档解析、模板化分块、知识编译器、引用溯源、Agent 沙箱。
- **何时查阅**：文档解析、分块策略、引用溯源与质量时（与 `src/chunking/` 互补参考）。

---

## 域 6：清理候选

> 维持原判断（2026-09）：低价值或与现有规则重叠，暂不清理可留作参考。

- **awesome-llm-apps-main**：AI Agent/RAG 模板集（理念参考为主，直接可迁移的少）。头脑风暴 agent 功能形态时查阅。
- **full-stack-fastapi-template-0.10.0**：FastAPI 官方全栈模板（含前端）。本站纯后端 API，前端非重点；分层/认证已有成熟结构。
- **fastapi-best-architecture-1.15.0**：FastAPI 社区最佳实践。与 fastapi-0.141.1 官方文档重叠；统一响应/异常处理本站 `docs/agents/rules.md` 已覆盖。

---

## 附：参考技能（本地已安装，非 `../github` 镜像）

> 经 `npx skills add -g` 装进 `~/.agents/skills/`（多工具共享技能源库，symlink 进 CodeBuddy/Claude Code 等）的技能。
> 用途：作为"标准 skill 长什么样"的**契约范例**与**内容搬运来源**——与上文"智能体预设"（agency-agents）正好凑成 agent / skill 两类范例。

**financial-statement-analyzer**
- 中文「财务报表深度分析」skill（geeksfino/finskills，Apache-2.0）。法证财务分析师视角，9 步法：5 因子杜邦分解 → 盈利质量（应计比率 / 现金转化率红灯阈值）→ 财务健康评分（Altman Z / Piotroski F / Beneish M）→ 营运资本 CCC → 资产负债表隐性风险（商誉减值、关联交易、在建工程不转固、股权质押）→ 业务分部 → 同行基准 → 报告模板。
- **A 股特化**：CAS vs US GAAP 差异、扣非净利润、政府补助依赖、"读附注"提醒、商业承兑汇票坏账风险。
- **契约范例**：frontmatter 仅 `name` + `description` + `license`——与 claude-code / agency-agents 的 `skill-md` 转换一致，是"SKILL.md 标准最小集"的活样本。
- **与本项目关系**：中文财务域，其方法论 / 公式 / 报告模板可直接参考，用于增强或对标 `skills/finance-analyst`；但它是**纯知识 skill**（无 `context`、无工具），搬运时需按本站契约（inline/fork、双轴）适配，且其"确认对象 → 取数"流程与本站"主 agent 先检索再委派"模式不同。
- **配套技能**：文末推荐 `findata-toolkit-cn`（A 股实时行情 / 财务指标 / 北向资金，免费无 API key）。
- **位置**：`~/.agents/skills/financial-statement-analyzer/`（`SKILL.md` + `references/analysis-methodology.md` + `references/output-template.md`）
- **何时查阅**：写财务分析类 skill、或给 `finance-analyst` 补方法论 / 公式 / 报告模板时。
