# 参考项目清单

> 本地 `../github/` 下镜像的开源参考项目，用于在对应场景下优先参考其实现模式。
> 评分基于 2026-09 对 corporate_rag 的契合度分析（技术栈一致度、当前痛点匹配度、可迁移深度），仅供排序参考，随项目演进可调整。
> 本清单只承载"项目是什么 + 何时查阅 + 参考价值"；具体架构细节以对应仓库源码为准。

## 评分排序（高 → 低）

| 排名 | 项目 | 评分 | 一句话定位 |
|------|------|------|-----------|
| 1 | langgraph-1.2.10 | 9.5 | 本项目 graph 底层依赖（官方） |
| 2 | fastapi-0.141.1 | 9.0 | 本项目 API 层底层依赖（官方） |
| 3 | financial_rag-main | 9.0 | 同领域财税法务 RAG + 答案校验/多智能体 |
| 4 | claude-code | 8.5 | 框架层混合编排最完整参考 |
| 5 | fastapi-langgraph-agent-production-ready-template | 7.5 | 与本项目技术栈一致的生产模板 |
| 6 | dify-1.16.1 | 7.0 | 工作流引擎 / RAG 管道 / 插件体系 |
| 7 | deepseek-harness | 7.0 | 事件钩子横切 + 工具内嵌子循环 |
| 8 | codex | 6.5 | 编排工具化 + Rust 执行/沙箱 |
| 9 | ragflow-0.26.4 | 6.0 | 文档解析 / 模板化分块 / 引用溯源 |
| 10 | Qwen-Agent | 5.0 | Python 薄层应用框架，多智能体简单实现 |
| 11 | awesome-llm-apps-main | 4.5 | 泛模板集，理念参考为主 |
| 12 | full-stack-fastapi-template-0.10.0 | 4.0 | 含前端的全栈模板，本项目后端为主 |
| 13 | fastapi-best-architecture-1.15.0 | 4.0 | 与官方文档重叠，本项目规则已覆盖 |

## 项目详情

### 高价值（≥8.5，必留）

**langgraph-1.2.10**（9.5）
- LangGraph 官方源码。StateGraph、流式事件（astream_events）、子图、Checkpoint、Human-in-the-loop。
- **何时查阅**：改 `src/agents/graph/` 下 workflow/state/agent_node 时；排查 graph 执行事件、流式行为时。

**fastapi-0.141.1**（9.0）
- FastAPI 官方源码。API 路由、中间件、依赖注入、异常处理、响应模型。
- **何时查阅**：改 `src/api/` 路由、`src/middleware/`、全局异常处理器时。

**financial_rag-main**（9.0）
- 财税法务 RAG 知识库（Python + LangGraph）。多智能体协作、状态机、知识图谱、混合检索、三层记忆体系。
- 与本项目同领域，且实现了本项目缺失的环节：`quality_nodes.py` FaithfulnessChecker（逐句核对 + score<0.7 重生成）、RetrievalGrader（CRAG 式检索评分分流）、`quality_review_function.py` 五维质量评审（最多 2 轮迭代 + needs_human_review）、`reflect_agent.py` 执行→反思→改进。
- **何时查阅**：做答案校验/评审环节、多智能体协作、混合检索、记忆分层时。

**claude-code**（8.5）
- Anthropic 终端编码 agent（闭源，~51 万行 TS；2026-03 因 npm source map 误发布泄露，本地为社区还原版 `claude-code-best/claude-code`）。
- 核心 `queryLoop()`（Async Generator + while(true)，状态机 Compaction→APICall→ToolExecution）+ 六层 harness：43+ 工具 / 5 种权限模式 / 26 事件 × 4 类型 Hooks / 沙箱 / 上下文工程（CLAUDE.md + 记忆 + 四级压缩）/ 多 agent 编排（coordinator，Orchestrator 模式 5 并行子代理）。人驱动 REPL + Plan mode。
- **何时查阅**：设计 agent 混合编排/横切环节/工具系统时优先参考（混合编排、横切环节校验/hooks/权限/压缩、子代理隔离上下文）。

### 中高价值（6.5–7.5，建议保留）

**fastapi-langgraph-agent-production-ready-template**（7.5）
- FastAPI + LangGraph 生产模板。LLM 容错、长期记忆、Rate Limiting、Eval 框架、生产监控。`app/api/v1 + core/langgraph(tools/prompts) + services/llm + models/schemas` 分层与本项目同构。
- **何时查阅**：生产化改造（限流、容错、评估、监控、记忆）时。

**dify-1.16.1**（7.0）
- Dify 官方（LLMOps 平台）。AI 应用编排、工作流引擎、RAG 管道、插件体系。
- **何时查阅**：做可视化工作流编排、RAG 管道拆解、插件化扩展时（平台型，迁移成本高，参考思路为主）。

**deepseek-harness**（7.0）
- DeepSeek 官方开源 agent harness（2026-08 公测，MIT）。TypeScript monorepo（~230 workspace），一切皆插件（Cordis 元框架）。核心 `ReactLoopAgent`（turn/step 双层 ReAct）+ 事件钩子横切（waterfall/serial：plan-mode/goal/guard/compaction 挂 agent/pre-step 等边界）+ 工具内嵌子循环（subagent→独立 Agent、workflow→worker 线程 JS 脚本）+ 外包驱动（goal-round-driver）+ Session Log 事件溯源。
- **何时查阅**：设计 agent 混合编排/横切环节/工具系统时优先参考（横切插件机制、事件钩子、上下文压缩、会话可重建日志；TS/Cordis 绑定，参考思路为主）。

**codex**（6.5）
- OpenAI 官方开源 coding agent（Rust monorepo `codex-rs`，Codex CLI + Harness 全面开源）。核心 `session/turn.rs` ReAct 循环 + **编排工具化**（plan/multi_agents spawn-wait-send_input-resume-close/request_permissions 均为模型可调工具）+ 执行/沙箱/压缩（compact 8+ 变体）/hooks/记忆/worktree。
- **何时查阅**：设计 agent 混合编排/横切环节/工具系统时优先参考（编排工具化让模型自治、沙箱/权限/压缩；Rust，语言不同，参考模式）。

**ragflow-0.26.4**（6.0）
- RAGFlow 开源 RAG 引擎。DeepDoc 文档解析、模板化分块、知识编译器、引用溯源、Agent 沙箱。
- **何时查阅**：文档解析、分块策略、引用溯源与质量时（与 `src/chunking/` 互补参考）。

### 中低价值（4.5–5，可留可删）

**Qwen-Agent**（5.0）
- 阿里官方开源 Python agent 应用框架（`qwen_agent/` 仅 1.28 万行）。Agent 基类（269 行）封装工具调用模板/解析器；多智能体三件套：react_chat（ReAct）/ router（MultiAgentRouter 路由）/ group_chat（多 agent 群聊）；集成 RAG/代码解释器/MCP。无 compaction/hooks/沙箱等 harness 基础设施。
- **何时查阅**：做简单多智能体路由/群聊、工具调用模板时（Python 同语言，参考下限）。

**awesome-llm-apps-main**（4.5）
- AI Agent/RAG 模板集（advanced_ai_agents / mcp_ai_agents / agent_skills 等目录）。理念参考为主，直接可迁移的少。
- **何时查阅**：头脑风暴 agent 功能形态、技能/记忆/MCP 用法时。

### 低价值（≤4，建议清理）

**full-stack-fastapi-template-0.10.0**（4.0）
- FastAPI 官方全栈模板（backend + frontend）。响应模型设计、用户认证、项目分层。
- **为何低分**：本项目是纯后端 API，前端非重点；分层/认证模式本项目已有成熟结构。

**fastapi-best-architecture-1.15.0**（4.0）
- FastAPI 社区最佳实践（统一响应格式、全局异常处理、RBAC 权限）。
- **为何低分**：与 fastapi-0.141.1 官方文档重叠；统一响应/异常处理本项目 `docs/agents/rules.md` 已覆盖。

## 清理建议

- **建议删**（<5 分）：`awesome-llm-apps-main`、`full-stack-fastapi-template-0.10.0`、`fastapi-best-architecture-1.15.0`
- **可留可删**（5–6 分）：`Qwen-Agent`、`ragflow-0.26.4`——视后续是否做多智能体/分块优化
- **必留**（≥7 分）：其余 9 个
