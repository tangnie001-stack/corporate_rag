# 参考项目清单

> 本地 `../github/` 下镜像的开源参考项目，用于在对应场景下优先参考其实现模式。
> 按**功能域**分组（要做什么 → 直接进对应域找参考），组内按价值排序。
> **排序权重（2026-09-18 修订）**：领域契合 > 生产验证 > 可迁移深度 > 当前痛点匹配 > 技术栈契合。
> **语言/框架不同不构成降级理由**：本项目从参考项目取的是**模式与边界划分**，不是代码。同领域、且已在生产规模上验证过的项目，排在语言相同但未经验证的演示项目之前；仅在"连模式都不适用"时才因技术栈差异后置。
> **同一项目可跨多个域登记**：项目体量大、横跨多个功能域时（如 WeKnora 同属域 2、域 3、域 4），在各域分别登记其**该域相关的那一面**，不压缩成一条。
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
- **注意**：`create_react_agent` 已废弃（官方迁至 `langchain.agents.create_agent`），参数名 `system_prompt`（非 `prompt`）；本项目 fork 子代理已改用 `create_agent`（调研结论见 change `session-agent-and-skill-invocation` D16）。
- **何时查阅**：改 `src/agents/graph/` 下 workflow/state/agent_node 时；排查 graph 执行事件、流式行为时。

**fastapi-0.141.1**
- FastAPI 官方源码。API 路由、中间件、依赖注入、异常处理、响应模型。
- **何时查阅**：改 `src/api/` 路由、`src/middleware/`、全局异常处理器时。

---

## 域 2：同领域 RAG（最贴合参考）

**WeKnora**
- 腾讯开源的企业级知识平台（`github.com/Tencent/WeKnora`，MIT，23.4k star，2025-07 起活跃）。**Go 1.26 后端 + Vue/TS 前端**，Python 仅出现在 `docreader/`（gRPC 文档解析服务）。**同领域 + 生产验证过**（非演示项目），按本清单权重排本域首位；语言不同只影响"是否抄代码"，不影响"是否抄模式"。
- **检索管线是插件式事件驱动**（`internal/application/service/chat_pipeline/chat_pipeline.go:11` 定义 `Plugin` 接口 + `EventManager`），每个阶段独立成文件：`filter_top_k` 截断 / `extract_entity` / `merge_expand`·`merge_faq`·`merge_overlap`·`merge_parent_resolve` 多路合并 / `load_history` / `into_chat_message` / `chat_completion_stream`；多库检索走 `knowledgebase_search_{fanout,fusion,faq,storegroup}.go`（扇出 + 融合）。可与本项目 `src/rag/` 逐段对照。
- **取数口径（2026-09-18 实测取得，本项目已据其改口径）**：候选池 `DefaultRetrievalTopK = 50`（`internal/types/retrieval_config.go:40-44`），精排后 `RerankTopK` 租户 10 / agent 5（`custom_agent.go:540-550`），实际过取 `max(MatchCount×5, 50) × KB数`、硬上限 500（`knowledgebase_search.go:191-195`）；三层绝对值阈值（向量 0.15 / 关键词 0.3 / 精排 0.2）；去重键为 **chunk id / parent id / (kb, chunk_index) + 内容签名**（`tools/knowledge_search.go:770-776`）—— **无"每文档保留 N 条"这类配额**，有效规则是"每父块 1 条"，多样性交给 **MMR λ=0.7**（`applyMMR`，Jaccard 冗余惩罚）。
- **context 渲染**：文档级元数据每篇只渲一次、chunk 只渲自己的内容（`writeKnowledgeMetadataHeader`，注释明写"repeated results from the same document do not waste model context"）—— 与"同一文档多条结果是否浪费"的解法直接相关。
- **GraphRAG 完整落地**（`application/service/graph.go` 的 `graphBuilder`）：LLM 抽实体/关系 → `calculateWeights`/`calculateDegrees` → `buildChunkGraph` 构图 → 关系反向回溯 chunk 参与召回，并以 `query_knowledge_graph` 工具暴露给 agent。
- **Wiki 模式**（`wiki_ingest*`/`wiki_linkify`/`wiki_lint`）：agent 把原始文档蒸馏成语义互链的 markdown 知识库并自维护，**反向生产**知识而非只消费——本项目无此形态。
- 长期记忆（`service/memory/` + `repository/memory_{vector,extraction,lifecycle}.go`）按 profile/preference/fact/task/interest 分层，有独立抽取与生命周期管理；本项目已决策不做长期记忆，可作反例复核。
- **何时查阅**：设计/重构检索管线（阶段拆分、多路合并、融合排序、取数口径、多样性机制）时；做 GraphRAG、Wiki 化知识生产时；判断"同领域生产系统在这个问题上怎么取值"时**优先查它**。
- Agent 编排/harness 机制（skill 沙箱、MCP、上下文压缩、审批闸门、prompt 分段）**另见域 3**；生产部署与异步任务治理**另见域 4**。

**financial_rag-main**
- 财税法务 RAG 知识库（Python + LangGraph）。与本站同领域，实现了本站缺失/待加强环节：`quality_nodes.py` FaithfulnessChecker（逐句核对 + score<0.7 重生成）、RetrievalGrader（CRAG 式检索评分分流）、`quality_review_function.py` 五维质量评审（最多 2 轮迭代 + needs_human_review）、`reflect_agent.py` 执行→反思→改进；多智能体协作、状态机、知识图谱、混合检索、三层记忆。
- **注意**：演示项目，无生产规模对照；prompt 以目录化 YAML 组织（`app/prompts/agents/<name>/{agent.yaml,system.md}` + `shared/*.yaml`），但其 `shared/tool_fallback.yaml` 是"提示词硬编码命令调用某工具、无可用性判断"的**反面案例**（`docs/tmp/deep-research-prompt-management.md` 引为同款缺陷）。
- **何时查阅**：做答案校验/评审环节、多智能体协作、混合检索、记忆分层时。

---

## 域 3：Agent 编排 / harness（找模式）

> 本站"主从委派 + skill 化业务解耦"升级路线的主参考域。
> **本域按"分工"排序，不是按价值** —— 四者覆盖不同侧面（委派范式 / 机制层 / 预设内容 / 企业级实物），互补而非可选，因此不适用域内的价值权重。
> 组内分工：claude-code 提供**委派范式**（AgentTool/skill fork）；deepseek-harness 提供**skill/配置机制层**（注册表/scope）；agency-agents 提供**智能体预设内容库**（现成的领域专家人格，可直接转成 `AgentPresetRegistry` 条目，见 change `session-agent-and-skill-invocation`）；WeKnora 提供**企业级机制实物**（skill 沙箱分发 / MCP OAuth / 压缩包 / 审批闸门 / **prompt 九段拼装**；Go 栈且**不含委派**）。

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

**WeKnora**
- 腾讯开源企业级知识平台（`github.com/Tencent/WeKnora`，MIT，23.4k star）。**agent 编排是纯 Go 手写 ReAct**（无图框架、无 LangGraph 等价物）：`internal/agent/engine.go:271 Execute` → `:473 executeLoop` → `:588 runReActIteration`；`think.go`（`streamLLMToEventBus` / `callLLMWithRetry` / `watchStreamStall` 流停滞看门狗）/ `act.go:387 runToolCall`（`executeToolCallsParallel` 并行工具）/ `observe.go`（`manageContextWindow` / `analyzeResponse`）三文件即 Reason-Act-Observe。是"**不带框架手写 ReAct 长什么样**"的完整样本。
- **⚠ 无主从委派**：全仓 0 处 subagent / spawn / 子代理概念，skill 走"渐进披露 + 沙箱挂载"而非 fork 子代理——**域 3 只覆盖"skill 化"一半，"委派"一半它不覆盖**（委派仍看 claude-code / codex）。
- 机制侧四项深度（本项目均空白，实现位置已核）：**① skill 沙箱分发** — `internal/agent/skills/` 的 `Manager`（`Initialize`/`LoadSkill`/`ReadSkillFile`/`SandboxSkillDir`/`Reload`/`WithTenantSource`），来源支持 ClawHub / SkillHub / github / gitlab / skills.sh / zip / 直连 SKILL.md（`client/skill.go:55`），安装需起沙箱执行、以分钟计，故走异步 install-events 流。
- **② MCP 全套** — `agent/tools/mcp_{catalog,exposure,schema,tool,oauth}.go`，含 **OAuth2 授权**、按轮 `@MCP` 注入（`SetPinnedMentions`）、超时与图片结果处理。本项目目前只有 `src/agents/tools/registry.py:3` 一行预留注释。
- **③ 上下文压缩独立成包** — `agent/compaction/`（`cutpoint` 选切点 / `overflow` / `serialize` / `prepare` / `settings` / `prompts` / `fileops`），不塞在引擎里，配 `agent/token/estimator.go`；另有防御设计：`engine.go` 的 `overflowRecovered` 让一轮**只允许一次** compact-and-retry，二次溢出直接放弃（注释理由："说明历史长度从来不是问题"）。
- **④ 工具审批闸门** — `agent/approval/{gate,tool_policy}.go`。
- **⑤ prompt 九段拼装（2026-09-18 实测取得，本项目 prompt 重构的直接依据）** — 唯一入口 `internal/agent/prompts.go:400-466` `BuildSystemPromptSections`，九段 `base`/`steering`/`runtime_contract`/`sources`/`tools`/`skills`/`output`/`memory`/`protocol`，空段丢弃，每段打 `[Agent][Prompt] section=... bytes=...`。**base 极薄**（rag 模式仅 5 行，不含检索阶梯/联网规则/停止条件）；载体为 `config/prompt_templates/*.yaml`（含 `id`/`mode`/`default`/`i18n`）；自定义正文**只替换 base**，运行时各段恒定；条件注入按**实际 registry** 判断（注释明说"不用配置开关"）；规则归属表见 `docs/agent-prompt-assembly.md` 末节，并明写"不要把同一条规则复制到多个模板中来'加强优先级'"。三条本项目缺失的运行时规则在 `runtime_contract`：完成即停止调用工具 / 数据与指令边界（`types.SourceDataBoundaryPrompt`）/ "已返回的完整内容不需要再次读取"。
- 另有可借的横切件：`steer.go` 的 `SteerSink`（用户消息注入运行中的轮次，`maxSteerOverruns=1` 限一次）、`internal/event/` EventBus、`internal/modelcontext/` 模型句柄的单请求边界、`internal/tracing/langfuse` span、`internal/sandbox/`（Docker / Cube 远程 / desktop + `docker_idle_sweeper` 回收）。引擎**跨轮次无状态、历史每轮从 DB 重建**（`engine.go` 包注释），与本项目 session-agent 同思路。
- **何时查阅**：设计 skill 加载与沙箱隔离、MCP 接入与凭证生命周期、上下文压缩包、工具权限闸门、**prompt 分段与归属、prompt 载体形态**时（Go，参考机制与边界划分，不抄代码）。RAG 侧另见域 2；生产部署/队列治理/多租户另见域 4。

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

**WeKnora**
- 腾讯开源企业级知识平台（`github.com/Tencent/WeKnora`，MIT，23.4k star；Go 2248 文件 / 578K 行，docreader 为 Python gRPC 服务）。**按本清单权重排本域首位**：本域要的就是"生产化"，而它是本域唯一**已在生产规模验证过的产品级样本**（非模板、非演示）；语言不同不构成降级理由。**同一套 Go 二进制贯穿四种部署形态**：Lite 单机（SQLite，`docs/LITE.md` + `deploy/weknora-lite.service` 的 systemd 最小权限加固）/ Compose / K8s Helm / 沙箱集群（`docs/sandbox-cluster.md`）。镜像侧：`docker/Dockerfile.app` 多阶段 + 构建期注入 `VERSION/COMMIT_ID/BUILD_TIME` + 随镜像分发 golang-migrate + `gosu` 降权；`docker/Dockerfile.docreader` 把重解析依赖（libreoffice/Playwright）隔离成独立 gRPC 服务。
- **依赖矩阵与配置**：`docker-compose.yml`（1044 行 / 25+ 服务）用 **profiles** 表达可选依赖（minio/neo4j/qdrant/milvus/weaviate/doris/searxng/dex/langfuse…），`docker-compose.dev.yml` 只起基础设施、应用本地热重载。`.env.example`（783 行，A-J 十段）是"环境变量即文档"的范本；运行时配置为**四层优先级**（内置默认 → 模板 YAML → 环境变量 → DB 运行时设置，后者优先，多数开关免重启改）。Helm `templates/secrets.yaml` 用 `lookup` 复用已生成密钥，避免 `helm upgrade` 重滚导致加密数据不可读。
- **异步任务治理（本项目最缺的一块）**：`docs/worker-pool-governance.md` 定义 Core/PostProcess/Enrichment/Maintenance/Shared/Wiki **六池 worker** 拓扑与 sizing 公式；`internal/router/task.go` 起六个 asynq Server + 统一重试策略；`internal/middleware/asynqdl/` 死信中间件（**仅最终重试**落 `task_dead_letters` 并把知识行标 failed，让"卡在处理中"变可见）；`internal/types/interfaces/task_queue.go` 是「Redis 队列 + DB 持久化待办/死信」双层设计（`FOR UPDATE SKIP LOCKED` 认领 + 崩溃回收）；管理端队列控制台 `/system/admin/runtime/queues` 可取消/重试/删除。并发侧另有进程级每模型治理 `internal/models/limiter/governor.go`（只拦后台任务、故障 fail-open）与 Redis ZSET 滑动窗口限流 `internal/ratelimit/limiter.go`（无 Redis 降级本地内存）。
- **可观测**：`internal/tracing/langfuse/` 基于 OTel SDK + OTLP 接 Langfuse，`asynq.go` 把 traceparent 写进 payload，使 HTTP→异步任务落在**同一条 trace**；`internal/middleware/logger.go` 的 `X-Request-ID` 贯通请求 ID；`internal/logger/logger.go` 支持 `LOG_FORMAT` 占位符 + lumberjack 滚动。**缺口：无 Prometheus 运维指标**（`application/service/metric/` 是 RAG 质量指标，非运维指标）。
- **多租户与安全**：`internal/middleware/rbac.go` 四角色阶梯（viewer<contributor<admin<owner，带"只记不拦"灰度开关）、`internal/application/access/ownership.go` 资源归属链、`internal/middleware/api_key_gate.go` 机器主体按路由 **fail-closed** 策略、`internal/middleware/auth.go` JWT/X-API-Key/外部头多通道、通用审计表 `audit_logs`（含去重防刷）、`SYSTEM_AES_KEY` 字段级加密。设计文档 `docs/RBAC说明.md`、`docs/OIDC认证调用流程.md`。
- **迁移与启动引导**：96 个版本化 migration（另有 sqlite 17 个），`internal/database/migration.go` 的 dirty state 自动恢复 + 失败只告警**不阻断启动**（保 UI 可达以便诊断），`scripts/migrate.sh` / Makefile 提供 `up/down/force/goto`，排障手册 `docs/migration-troubleshooting.md`。运维 FAQ 40+ 条见 `docs/QA.md`，可当生产排障清单。
- **CI/CD**：`.github/workflows/` 分 `app.yml`（gofmt/vet/test/build + 第三方许可 bundle 校验）、`go-lint.yml`（`only-new-issues` + 按改动模块动态选 lint 目标）、`docker-image.yml`（多架构 Buildx + digest 合并 manifest）、`release-lite.yml`（四平台单二进制）；桌面分发走 `Formula/weknora-lite.rb`。
- **与本项目关系**：Go 栈、代码不可迁移，价值在机制与边界划分——**把"异步任务"当生产对象治理**（六池/死信/双层持久化/队列控制台）而非实现细节，这是本项目流水线最薄的一环。
- **何时查阅**：做生产化改造时——异步任务与队列可靠性、限流与并发治理、多形态部署与 Helm 幂等、DB 迁移与启动引导、RBAC/审计/多租户。RAG 侧另见域 2，Agent 编排/skill/MCP/压缩另见域 3。

**fastapi-langgraph-agent-production-ready-template**
- FastAPI + LangGraph 生产模板。LLM 容错、长期记忆、Rate Limiting、Eval 框架、生产监控。`app/api/v1 + core/langgraph(tools/prompts) + services/llm + models/schemas` 分层与本站同构。
- **何时查阅**：生产化改造（限流、容错、评估、监控、记忆）时；需要"同技术栈、可直接照搬目录结构"的模板时优先查它。

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
- **与本项目关系**：中文财务域，其方法论 / 公式 / 报告模板可直接参考，用于增强或对标 `skills/financial-statement-analyzer`；但它是**纯知识 skill**（无 `context`、无工具），搬运时需按本站契约（inline/fork、双轴）适配，且其"确认对象 → 取数"流程与本站"主 agent 先检索再委派"模式不同。
- **配套技能**：文末推荐 `findata-toolkit-cn`（A 股实时行情 / 财务指标 / 北向资金，免费无 API key）。
- **位置**：`~/.agents/skills/financial-statement-analyzer/`（`SKILL.md` + `references/analysis-methodology.md` + `references/output-template.md`）
- **何时查阅**：写财务分析类 skill、或给 `financial-statement-analyzer` 补方法论 / 公式 / 报告模板时。
