# 深度调研：Agent 上下文管理与记忆模块

> 调研方法：Firecrawl 联网检索（一手来源优先）+ 本地参考项目源码实读（`/root/code/github/`）+ 本项目代码实测。
> 调研范围：**上下文管理 / 上下文压缩 / 记忆模块**三块；产出面向 Corporate Agent Harness 的**破坏式升级方案**（用户已确认：可直接采用最佳实践，不考虑历史负担与技术负债）。
> 调研日期：2026-10-08。

---

## 执行摘要

本项目目前**只有一层最朴素的上下文处理**：`_truncate_history` 按「最近 10 轮 + 固定 token 预算」硬截断，预算还是写死的、且调用处不传参（详见「现状盘点」）。它同时缺三样东西——**持久摘要**（超窗即永久丢弃，多轮后语义漂移）、**轮内累积上限**（单轮内工具结果/检索块/思考回传可无限膨胀，实测同轮输入已达 6.17 万 token）、**跨会话长期记忆**（Redis List + MySQL 落库只存原始对话，无任何用户级记忆）。

外部调研与本地参考项目实读高度收敛到一个**三层阶梯架构**：① 零 LLM 的「写入侧重度治理」（工具结果写入即截断/去重/外置）；② 零 LLM 的「请求前瘦身」（`pre_model_hook` 阶段裁旧工具结果、按 tool-pair 平衡切点）；③ 阈值触发的 **LLM 摘要压缩**（结构化 user 消息注入 + boundary 标记 + 就地更新不得增长）；其上是 **LangGraph 原生记忆原语**（checkpointer 管短期 thread / Store 管跨 thread 长期，PostgresStore + pgvector 为官方生产推荐）。生产级实现（Claude Code 三层 MicroCompact/SessionMemory/API 摘要、deepseek-harness 可组合插件、WeKnora、codex、Manus、Deep Agents）全部落在这套骨架上，差别只在分层细节与工程闭环。

两个**实测可用的关键杠杆**改变了落地成本：本项目已装的 `langchain==1.3.11` **直接内置 `SummarizationMiddleware`**（`trigger`/`keep`/`summary_prompt` 全可配，默认摘要模板已是 4 段结构），可免去手写摘要节点；`tiktoken==0.13.0` 也已在环境里，可把 `len(content)//2` 的粗估换成真实计数；唯一缺的依赖是 **`langgraph-checkpoint-postgres`**（当前未装，它是 `PostgresStore`/`AsyncPostgresStore` 的宿主包）。原「不做长期记忆」的顾虑（凭记忆回答引入幻觉、与 RAG 溯源冲突）在调研中有明确解法：**把记忆限定为「用户是谁 / 之前发生过什么」的 episodic + user-semantic，事实类问题仍强制走 RAG 取证**——记忆与 RAG 是互补而非替代。

---

## 一、现状盘点（本项目，实测）

| # | 事实 | 证据 |
|---|------|------|
| 1 | 历史截断只用「最近 10 轮 + token 预算」硬截断 | `src/config/const.py:77-78`（`HISTORY_MAX_TURNS=10` / `HISTORY_TOKEN_RATIO=0.3`）；`src/agents/graph/agent_node.py:28-57` |
| 2 | **预算口径错误**：`context_window` 默认写死 8000 且调用处不传参 | `agent_node.py:32`（`context_window: int = 8000`）、`agent_node.py:119`（`_truncate_history(state._history or [])` 不传该参）→ 实际预算 = 0.3×8000 = **2400 token**，而配置 `MODEL_CONTEXT_WINDOW_TOKENS=32768`（`src/config/settings.py:92-94`）从未参与 |
| 3 | 截断是「从最旧逐条 pop」，无摘要、丢弃即永久丢失 | `agent_node.py:54-56` |
| 4 | 短期历史载体 = Redis List（`chat_history:{sid}`，TTL 7 天）+ MySQL 落库，**两套并行** | `src/chat/manager.py:28-58,192-322`；`MEMORY_WINDOW=6`（`settings.py:289`）为另一套独立窗口 |
| 5 | **轮内累积无上限**：同一轮内 tool 结果 + 检索块 + 思考回传持续累加 | 实测同轮输入 61,698 token、配置窗口 32,768（记忆 `token-accounting-and-context-window`）；`MAX_AGENT_ITERATIONS=5`（`const.py:53`） |
| 6 | **无长期记忆**：无跨会话用户画像/事实存储 | `ChatManager` 全部方法只读写原始消息；原决策「长期记忆不做」（记忆 `harness-architecture-roadmap`） |

> 结论：缺的**不是**「更聪明的截断」，而是三件事——**正确的窗口口径**、**持久摘要层**、**轮内治理 + 跨会话记忆**。

---

## 二、关键发现

以下按「问题实证 → 压缩手段 → 官方原语 → 生产实现 → 记忆架构 → 风险」分组。**粗体**标注对本项目直接可用者。

### A. 长上下文确实衰减（问题成立）

1. **Context rot**：Chroma 在 18 个模型上固定任务复杂度只变输入长度，性能随 token **非均匀下降**；NIAH 基准因只测词面检索而掩盖了这点。([research.trychroma.com/context-rot](https://research.trychroma.com/context-rot))
2. **Lost in the middle**：信息位于开头/结尾时最好，位于中间时显著退化（U 形曲线），长上下文模型亦然。([arXiv:2307.03172](https://arxiv.org/abs/2307.03172)，TACL 2023)
3. **Anthropic 定性**：注意预算是有限资源，性能表现为**梯度而非断崖**；「等更大窗口」解决不了污染与相关性。([Anthropic: Effective context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents))

### B. 压缩/精简手段（一手来源）

4. **总纲四类**：compaction（滚动摘要）/ structured note-taking（外部笔记）/ sub-agent 隔离 / just-in-time 检索。判据：需多轮往返用 compaction、有里程碑用笔记、可并行探索用子代理。([Anthropic](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents))
5. **Compaction 细节**：接近上限时摘要后重开窗口；**保架构决策/未解 bug/实现细节，丢冗余工具输出**；Anthropic 警告**过度压缩会丢失"当时看不出重要"的细节**，建议先最大化 recall 再提 precision。([Anthropic compaction](https://platform.claude.com/docs/en/build-with-claude/compaction))
6. **工具结果清理与缓存冲突**：`clear_tool_uses` 按时间清最旧工具结果、保留"调用发生过"的记录；但会**使 prompt cache 前缀失效**，需用 `clear_at_least` 保证一次清够量。([Context editing](https://platform.claude.com/docs/en/build-with-claude/context-editing))
7. **缓存与顺序**：静态内容（工具定义/系统指令/大上下文）**前置**，动态内容（时间戳/用户输入）**后置**。([Prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching))
8. **transient vs persistent（官方关键区分）**：`trim_messages` 是**瞬时**的，只改单次调用视图、**不改 state**；summarization 是**持久**的，永久替换旧消息、对未来所有轮生效。([LangChain context engineering](https://docs.langchain.com/oss/python/langchain/context-engineering))

### C. 官方原语（**本项目已装可直接用**）

9. **`SummarizationMiddleware` 实测存在且可用**：`langchain==1.3.11` 的 `from langchain.agents.middleware import SummarizationMiddleware`，签名实测为 `trigger=("tokens",N)|("fraction",f)|("messages",m)`、`keep=("messages",20)`、`summary_prompt`（默认模板已含 `SESSION INTENT / SUMMARY / ARTIFACTS / NEXT STEPS` 四段）、`token_counter`、`trim_tokens_to_summarize=4000`。([LangChain short-term memory](https://docs.langchain.com/oss/python/langchain/short-term-memory))
10. **LangGraph 记忆原语分工**：`BaseCheckpointSaver` 以 `thread_id` 为主键（短期会话记忆，支持 resume/interrupt/time-travel）；`BaseStore` 跨 thread、按 `namespace` 层级存 `Item`、`search(query=...)` 支持语义检索（长期记忆）。([LangGraph add memory](https://docs.langchain.com/oss/python/langgraph/add-memory)；本地源码 `langgraph-1.2.10/libs/checkpoint/langgraph/store/base/__init__.py:52-79,756-828`)
11. **PostgresStore + pgvector 是官方生产推荐**；本项目 PG+pgvector 栈已就位，**但需新增 `langgraph-checkpoint-postgres` 依赖**（实测：当前 `langgraph.store.postgres` 模块不存在，`InMemoryStore` 存在）。该包同时提供 `langgraph.checkpoint.postgres` 与 `langgraph.store.postgres`（本地源码 `libs/checkpoint-postgres/langgraph/store/postgres/{__init__,aio,base}.py`）。([LangChain long-term memory](https://docs.langchain.com/oss/python/langchain/long-term-memory))
12. **langgraph 仓库本身不含摘要节点**：`trim_messages`（属 langchain-core）与 `SummarizationNode`（属 langmem，本项目未装）都不在 langgraph 内；官方入口只有 `create_agent` 的 `pre_model_hook`，摘要逻辑需自建或走 `SummarizationMiddleware`。

### D. 生产实现（收敛到同一骨架）

13. **Claude Code 三层压缩**：L1 `MicroCompact`（清旧工具输出，**不调 API**）→ L2 `Session Memory Compact`（复用已提取的会话记忆，**不调 API**）→ L3 传统 API 摘要；触发阈值 200K 窗口下 `~180K` 自动压缩；压缩后按 50K 预算**重注入**（文件/技能/CLAUDE.md）；用 `compact_boundary` 消息标记、只处理 boundary 之后的消息。([compaction.mdx](https://platform.claude.com/docs/en/build-with-claude/compaction))
14. **deepseek-harness 可组合插件**：`compaction-tool-result-pruner`（零模型：`thresholdChars=8192`，中段替换为 `[... pruned ...]`）+ `compaction-basic`（`thresholdRatio=0.8 / retainRatio=0.16`）；**摘要以结构化 8 段 Markdown 作为最后一条 user 消息附加、复用 KV cache**；**摘要必须比被替换区间更小**，否则报错。
15. **WeKnora（Go，算法最系统）**：`Threshold = window - reserve`（**绝对保留量而非比例**）；`keepRecent ≤ usable/4`；`FindCutPoint` 保留 `≤ keepRecentTokens` 后缀、**切点不能落在 tool result 前**；摘要作为 **user 消息**并就地更新（**结果不得比上一版更长**）；`validateSummary` 把 `finish_reason=length` 视为失败（**截断的摘要不算摘要**）；20+ provider 溢出正则。
16. **codex（Rust）**：`COMPACT_USER_MESSAGE_MAX_TOKENS=20_000`，重建历史 = [尾部 user 消息] + [摘要]；摘要用前缀识别（`is_summary_message`）；另有**不摘要直接开新窗口**的硬重置变体。
17. **Manus 一手经验**：以 **KV-cache 命中率**为首要指标（缓存/未缓存差约 10×）；工具结果外置文件系统，压缩须**可恢复**（弃网页正文但留 URL）；todo 反复复述目标抗 lost-in-the-middle；**保留错误轨迹**。([Manus](https://manus.im/blog/Context-Engineering-for-AI-Agents-Lessons-from-Building-Manus))
18. **LangChain Deep Agents 三级**：工具结果 >20k token 即卸载文件系统（留路径+前 10 行）；上下文 >85% 卸载旧写入入参；仍不够才结构化摘要，并把原始消息写盘可回读。([Deep Agents blog](https://www.langchain.com/blog/context-management-for-deepagents))
19. **OpenHands**：`LLMSummarizingCondenser`——超阈值保留最近消息、摘要较早内容，`keep_first` 保留首两条。

### E. 记忆架构

20. **分类学**：short-term（thread 内）/ long-term（跨 thread）；semantic（事实）/ episodic（经历）/ procedural（规则）。源头 CoALA。([arXiv:2309.02427](https://arxiv.org/abs/2309.02427)、[arXiv:2404.13501](https://arxiv.org/abs/2404.13501))
21. **方案光谱**：Letta/MemGPT（分层 memory block + LLM 自管理，复杂）／mem0（LLM 抽取事实→向量/图）／Zep/Graphiti（时序知识图谱，冲突处理最强但重）／LangMem（官方 SDK）／**LangGraph 原生 Store（与现有 PG+pgvector 最省事，首选）**。
22. **记忆 vs RAG 的边界（回应原决策顾虑）**：主流共识=**互补**——记忆答「用户是谁、之前发生了什么」（个性化、会话连续），RAG 答「权威来源现在说什么」（事实准确、可溯源）。([AWS Bedrock AgentCore](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/memory-ltm-rag.html)) 故本项目可**把记忆限定为 episodic + user-semantic，事实/知识类问题仍强制 RAG 取证**，在保留溯源的前提下拿到个性化收益。
23. **写入时机**：hot path（实时、透明、增延迟）vs background（异步、不阻塞、触发时机难定）。([LangChain memory concepts](https://docs.langchain.com/oss/python/concepts/memory))

### F. 风险与反方观点

24. **记忆中毒**：记忆写入即攻击面，含 **compaction-driven write** 在内的四条写入通道、九类结构性漏洞，一次写入长期生效，ASR≈50%，提示注入防御覆盖不到。([arXiv:2606.04329](https://arxiv.org/html/2606.04329v1))
25. **该忘不忘（Oblivion）**：平坦记忆 + always-on 检索致干扰与延迟；主张**衰减式不可达而非删除**、读写解耦。([arXiv:2604.00131](https://arxiv.org/html/2604.00131v2))
26. **摘要稳定丢失五类信息**：精确数值、硬约束、决策理由(why)、跨轮依赖、隐性偏好。([mem0，厂商来源](https://mem0.ai/library/context-engineering/context-compression-vs-memory-in-ai-agents))
27. **压缩成本/阈值无共识**：压缩是一次额外 LLM 调用；阈值随模型/任务变（20k token / 85% / 事件数 / 可配置）。Deep Agents 建议用 10–20% 强制触发以放大评测信号。**倾向绝对量**（WeKnora 的 `window - reserve`）而非比例。
28. **Cursor 反面案例**：用户实测接近满自动 summarize 后（~190k→~1k）"需求理解/指令遵循"骤降，**甚至未完成就声称完成**——过度压缩的直接代价。([Cursor forum，用户报告](https://forum.cursor.com/t/customizing-how-cursor-summarizes-context/160865))

---

## 三、本地参考项目对照（源码实读）

| 项目 | 上下文压缩 | 记忆 | 对本项目的价值 |
|------|-----------|------|---------------|
| **WeKnora**（Go） | 最系统：绝对阈值 `window-reserve`、预算=上限、tool-pair 切点、摘要 user 段 + 就地更新不得增长、拒绝截断摘要、20+ 溢出正则 | 长期记忆类型位掩码 + FIFO | **算法最佳蓝本**（`internal/agent/compaction/*`），几乎可逐条移植 |
| **deepseek-harness**（TS） | 可组合插件（pruner + basic）、surface/log 分离 + shadow-price 事件、摘要复用 KV cache、摘要必须更小 | — | **架构最佳参考**：插件化 + 事务锁（`compaction/start→summary→end`） |
| **claude-code**（TS） | 三层 MicroCompact→SessionMemory→API 摘要；threshold 180K；压缩后 50K 重注入；`compact_boundary` 只处理之后 | Session Memory | 三层的分层思想 + 「压缩后重注入」清单 |
| **codex**（Rust） | 摘要用户消息 + 前缀识别；20K 预算重建；硬重置变体；写入时即截断 | rollout budget | 「摘要用户消息模板 + 前缀识别」可照搬 |
| **ragflow**（Go） | `TriggerTokens=100000 / PreserveRecent=4`；`Supersede` 删被后续写覆盖的旧读；摘要后处理限 1200 字符/24 行 | `MemoryType{RAW,SEMANTIC,EPISODIC,PROCEDURAL}` 位掩码 + FIFO 遗忘 | 记忆类型位掩码 + 陈旧操作清除 |
| **openakita**（Python） | 零 LLM `microcompact`（工具结果 sha256 去重 + 旧 thinking 占位）+ `snip_old_segments`；80% 警告 | 类型化提取（只存"用户是谁"不存"任务"，`importance≥0.9` 才升永久）+ 离线整合 | **技术栈最贴近**（Python），提取门控与整合闭环可直接对位 |
| **CowAgent**（Python） | trim/overflow 触发落盘；**一次 LLM 摘要同时服务落盘与当前续接**；后台异步 flush | 日记 + `MEMORY.md`（≤50 条）+ Deep Dream 蒸馏 | 「摘要双用途」+「后台不阻塞回复」工程手法 |
| **LangBot**（Python） | 不内联历史，只给游标 + 窗口数值，runner/插件自管 | TranscriptStore | 解耦思路，本项目不宜照搬（需自己承担） |
| **Qwen-Agent** | 不压缩，按需从长历史 RAG 检索 | —— | 参考价值有限 |
| **dify** | 纯截断（按 user 轮次整批丢弃） | thread | 「按轮次整批丢弃」比逐条 pop 安全 |
| **langgraph-1.2.10** | **无** trim_messages / SummarizationNode | checkpointer + BaseStore | 提供原语，压缩逻辑需自建 |

**共识（三家独立收敛的硬约束）**：① 切点必须 **tool-call/result 成对完整**（claude-code `adjustIndexToPreserveAPIInvariants`、deepseek `tool-pairing.ts`、WeKnora `FindCutPoint`）；② 摘要以**结构化 user 消息**注入并**标签识别上一次摘要，绝不做摘要之摘要**；③ **摘要必须比被替换区间更小**／就地更新不得增长。

---

## 四、落地方案（面向本项目的破坏式升级）

### 4.1 目标架构：四层阶梯

```
┌─ L0 写入侧治理（零 LLM，写入即生效）────────────────────────┐
│  tool 结果写入 state 时按阈值截断（head/tail 保留 + 中段省略）│
│  相同工具结果指纹去重（sha256）；旧 thinking 占位           │
└──────────────────────────────────────────────────────────┘
┌─ L1 请求前瘦身 pre_model_hook（零 LLM）──────────────────┐
│  旧工具结果裁剪 / 外置；按 tool-pair 平衡切点；真实 token 计数 │
│  （用已装 tiktoken 替换 len(content)//2）                  │
└──────────────────────────────────────────────────────────┘
┌─ L2 阈值摘要（LLM，持久）────────────────────────────────┐
│  SummarizationMiddleware（已装可用）或自建结构化摘要节点     │
│  结构化 user 消息注入 + boundary 标记 + 就地更新不得增长     │
│  拒绝截断摘要（finish_reason=length 视为失败，降级不静默）    │
└──────────────────────────────────────────────────────────┘
┌─ L3 长期记忆 Store（跨会话）────────────────────────────┐
│  PostgresStore + pgvector（需加 langgraph-checkpoint-postgres）│
│  类型化事实提取（episodic + user-semantic）+ 离线整合 + TTL   │
│  namespace 多租户隔离；记忆不替代 RAG 取证                  │
└──────────────────────────────────────────────────────────┘
```

### 4.2 分阶段（每阶段独立可交付、可回归）

**Phase A — 地基：口径修正 + 轮内治理（零 LLM，收益立竿见影）**
1. 修掉 `_truncate_history` 的窗口口径错误：调用处传 `MODEL_CONTEXT_WINDOW_TOKENS`，或直接改为读配置的绝对值预算（`const.py` 集中管理，禁止散落）。
2. 用 **tiktoken**（已装 `0.13.0`）替换 `len(content)//2` 粗估；`MODEL_CONTEXT_WINDOW_TOKENS` 按真实模型窗口确证后更新。
3. 引入 **L0 写入侧治理**：工具结果写入 state 时按 `thresholdChars`/`head/tail` 截断、相同指纹去重（照抄 deepseek-harness `compaction-tool-result-pruner` / openakita `microcompact` 思路，纯本地无依赖）。
4. 给轮内累积设硬上限（配合 `MAX_AGENT_ITERATIONS` 决策；当前 5，若提到 10 必须同时设上限）。

**Phase B — 短期记忆统一 + 持久摘要（破坏式）**
5. **把短期记忆的真源统一到 LangGraph checkpointer**（Postgres 后端，配 Phase A 的 `langgraph-checkpoint-postgres` 依赖）：graph state 成唯一真源，Redis 降级为热点缓存，消除「Redis List 窗口 6 / `_truncate_history` 窗口 10 / 配置 32768」三套口径打架。MySQL 落库保留为审计/历史回放。
6. 在 `create_agent` 上接 **`SummarizationMiddleware`**（`trigger=("fraction", 0.8)` 或 `("tokens", N)`、`keep=("messages", M)`、`summary_prompt` 换成金融场景定制版），若因本项目 `build_prompt` 手工分层（system/rest 拆分）无法直接挂载，则自建 `pre_model_hook` 实现等价逻辑（算法照 **WeKnora**）。
7. 落地三条硬约束：tool-pair 平衡切点、摘要 user 段 + boundary 标记、就地更新不增长。

**Phase C — 长期记忆（新能力，先定边界）**
8. 加 `langgraph-checkpoint-postgres`，用 **`AsyncPostgresStore` + pgvector** 建记忆库；`namespace` 带 `user_id`（多租户）。
9. 记忆写入走**类型化提取**（照 openakita：只存「用户是谁/偏好/规则」，不存任务流水账；`importance` 门控；参考 ragflow `MemoryType` 位掩码）；写入时机优先 **background 异步**（不阻塞回复，照 CowAgent）。
10. **记忆只承载 episodic + user-semantic，事实类问题仍强制走 RAG**——用 prompt 规则显式规定边界，保住溯源原则。

**Phase D — 高级优化（可选）**
11. 工具结果**外置到可回读存储**（Manus / Deep Agents 的 file-system offload），压缩「可恢复」；本项目可外置到 MinIO/PG 而非文件系统。
12. **KV-cache 友好**：静态前缀前置、动态后置；摘要调用复用对话前缀。
13. 离线整合（OpenAkita consolidator / CowAgent Deep Dream）：定期把会话摘要蒸馏进长期记忆，设条数上限（CowAgent 用 50 条）。

### 4.3 关键设计决策与取舍

| 决策点 | 建议 | 理由 |
|--------|------|------|
| 短期记忆载体 | LangGraph checkpointer（Postgres）为真源，Redis 做缓存 | 消除多套窗口口径；获得 resume/interrupt/time-travel；用户已接受破坏式 |
| 摘要实现 | 优先 `SummarizationMiddleware`；不行再自建 hook | 已装可用、默认模板已结构化，省自研成本 |
| 阈值语义 | **绝对量**（`window - reserve`），不用比例 | WeKnora 实证；比例会随窗口漂移 |
| 摘要模型 | 便宜模型 + `temperature=0`（沿用 `RAGAS_LLM_MODEL` 模式） | 压缩是额外调用，控成本 |
| 长期记忆范围 | 仅 episodic + user-semantic | 保住 RAG 溯源，回应原「不做」的顾虑 |
| 记忆写入 | background 异步 | 不阻塞回复 |
| 依赖新增 | `langgraph-checkpoint-postgres` | PostgresStore/AsyncPostgresStore 宿主包；当前缺失 |

---

## 五、反方观点与风险

1. **过度压缩的代价是真实的**：Cursor 用户报告压缩后指令遵循骤降、甚至未完成就声称完成。对策：先最大化 recall 再提 precision（Anthropic），并加可恢复性 eval（Deep Agents 的「针尖」测试）。
2. **摘要必然有损**：稳定丢失精确数值/硬约束/决策理由。对策：结构化模板强制写「Key Decisions / Critical Context」（WeKnora 段结构）；关键信息外置可回读。
3. **记忆是攻击面**：compaction-driven write 是四条写入通道之一，一次写错长期生效。对策：写入侧加来源校验（provenance）、记忆只在「关于用户」的维度生效、不与 RAG 证据混用。
4. **该忘不忘**：平坦记忆 + always-on 检索会致干扰与延迟。对策：TTL（LangGraph Store 可配）+ 衰减式不可达 + 类型/重要度门控。
5. **压缩不是免费**：每次压缩 = 一次额外 LLM 调用（成本 + 延迟）；阈值无共识，需按本项目模型实测标定（建议从一个保守值起步，用已接的 Langfuse 落压缩前后 token）。
6. **缓存与清理冲突**：清工具结果省 token 但破 prompt cache，需权衡。对策：用 `clear_at_least` 保证一次清够、控制频率。
7. **【二手/厂商来源打折】**：mem0、Zep 的「记忆优于 RAG」结论带商业立场；Cursor 细节仅论坛用户报告、官方无工程博文；Manus 的工具结果细节部分来自二手笔记。采信以一手（Anthropic/OpenAI/LangChain/AWS 文档 + 论文）为准。

---

## 六、未决问题

1. **本项目摘要该挂哪一层**：`create_agent` 的 `SummarizationMiddleware` 与现有 `build_prompt` 手工分层（system/rest 拆分 + `SKILL_INJECTION_PREFIX` 注入）如何共存？需先做一次集成验证（决定 Phase B 是「挂 middleware」还是「自建 hook」）。
2. **真实模型窗口到底多大**：`MODEL_CONTEXT_WINDOW_TOKENS=32768` 是假设值，实测同轮输入 61.7k 仍成功 → 需确证官方窗口后重设阈值。
3. **checkpointer 迁移的破坏半径**：改用 LangGraph checkpointer 后，Redis List / MySQL 落库 / 历史回放（process_json）三者职责如何重划？是否影响前端「刷新后历史」呈现（见记忆 `pending-history-loss-after-refresh`）？
4. **记忆的产品语义**：哪些问题允许「凭记忆答」（个性化寒暄/偏好）vs 必须 RAG 取证？需产品层显式定义（沿用本项目「先定语义再实现」的纪律）。
5. **轮内上限取值**：`MAX_AGENT_ITERATIONS` 是否从 5 提到 10？提到多少取决于轮内上限方案（二者必须同时定）。
6. **成本基线**：压缩引入的额外 LLM 调用对当前单轮成本（实测约 0.15）的影响，需 A/B 实测。

---

## 七、来源清单

**官方一手**
1. https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents — 上下文工程总纲（compaction/笔记/子代理/just-in-time；梯度非断崖）
2. https://www.anthropic.com/engineering/multi-agent-research-system — 子代理隔离与压缩（90.2%/15× token）
3. https://platform.claude.com/docs/en/build-with-claude/compaction — Claude Code compaction 三种形态
4. https://platform.claude.com/docs/en/build-with-claude/context-editing — 工具结果/思考块清理，及与缓存的冲突
5. https://platform.claude.com/docs/en/build-with-claude/prompt-caching — 静态前置、breakpoint 放稳定前缀末尾
6. https://platform.claude.com/cookbook/tool-use-automatic-context-compaction — SDK `compaction_control` 触发流程
7. https://docs.langchain.com/oss/python/langchain/short-term-memory — trim/delete/`SummarizationMiddleware` 官方用法
8. https://docs.langchain.com/oss/python/langchain/context-engineering — transient vs persistent；state/store/runtime 三分
9. https://docs.langchain.com/oss/python/langchain/long-term-memory — PostgresStore(pgvector) 用法
10. https://docs.langchain.com/oss/python/langgraph/add-memory — LangGraph 短期/长期记忆与语义检索
11. https://docs.langchain.com/oss/python/concepts/memory — 记忆概念指南（分类、hot path vs background、冲突取舍）
12. https://www.langchain.com/blog/context-management-for-deepagents — Deep Agents 三级压缩与 eval
13. https://www.langchain.com/blog/langmem-sdk-launch — LangMem 官方发布说明
14. https://manus.im/blog/Context-Engineering-for-AI-Agents-Lessons-from-Building-Manus — Manus 一手经验
15. https://docs.openhands.dev/sdk/guides/context-condenser — OpenHands condenser
16. https://openai.github.io/openai-agents-python/sessions/ — OpenAI Agents SDK Sessions
17. https://developers.openai.com/cookbook/examples/agents_sdk/session_memory — 会话截断 + 合成摘要
18. https://developers.openai.com/cookbook/examples/context_summarization_with_realtime_api — 阈值触发自动摘要
19. https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/memory-ltm-rag.html — 记忆 vs RAG 官方定位（互补）
20. https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/ddb-langgraph-memory.md — DynamoDB LangGraph Store：TTL + 语义检索
21. https://research.trychroma.com/context-rot — context rot 技术报告（18 模型）
22. https://arxiv.org/abs/2307.03172 — Lost in the Middle（TACL 2023）
23. https://arxiv.org/abs/2309.02427 — CoALA：四类记忆分类学源头
24. https://arxiv.org/abs/2404.13501 — LLM agent 记忆机制综述（写入/管理/读取）
25. https://arxiv.org/abs/2310.08560 — MemGPT 原论文
26. https://www.letta.com/blog/memory-blocks/ — Letta 官方：memory block 设计
27. https://arxiv.org/html/2504.19413v1 — mem0 论文
28. https://arxiv.org/abs/2501.13956 — Zep 论文（Graphiti 时序知识图谱）
29. https://arxiv.org/html/2606.04329v1 — 记忆中毒系统性研究（含 compaction 写入通道）
30. https://arxiv.org/html/2604.00131v2 — Oblivion 记忆控制（该忘不忘）

**二手 / 厂商（仅作背景，关键结论未采信）**
31. https://forum.cursor.com/t/customizing-how-cursor-summarizes-context/160865 — Cursor 用户报告（非官方）
32. https://mem0.ai/library/context-engineering/context-compression-vs-memory-in-ai-agents — 压缩丢失五类与策略（厂商）
33. https://www.getzep.com/ai-agents/temporal-knowledge-graph/ — Zep 主张「图 > 向量 RAG」（厂商）
34. https://redis.io/blog/ai-agent-memory-vs-retrieval/ — Redis：记忆与检索需并存
35. https://atlan.com/know/types-of-ai-agent-memory/ — 分类学通俗转述
36. https://rlancemartin.github.io/2025/10/15/manus/ — Manus 笔记（二手）

**本地源码实读**（`/root/code/github/`）
- WeKnora：`internal/agent/compaction/{settings,cutpoint,compactor,prompts,prepare,overflow,serialize}.go`
- deepseek-harness：`packages/compaction/{compaction,compaction-basic,compaction-tool-result-pruner}/src/*`、`packages/llm/token-meter/src/estimate.ts`
- claude-code：`docs/context/{compaction,token-budget}.mdx`
- codex：`codex-rs/core/src/{compact.rs,compact_token_budget.rs,context_manager/history.rs}`
- langgraph-1.2.10：`libs/checkpoint/langgraph/store/base/__init__.py`、`libs/checkpoint/langgraph/checkpoint/base/__init__.py`、`libs/prebuilt/langgraph/prebuilt/chat_agent_executor.py`、`libs/checkpoint-postgres/langgraph/store/postgres/*`
- ragflow-0.26.4：`internal/harness/core/middlewares/summarization/*`、`common/constants.py`
- openakita：`src/openakita/core/microcompact.py`、`agent/token_budget.py`、`memory/{extractor,consolidator}.py`
- CowAgent：`agent/memory/summarizer.py`
- LangBot：`src/langbot/pkg/agent/runner/context_builder.py`
- dify-1.16.1：`api/core/memory/token_buffer_memory.py`、`api/core/prompt/agent_history_prompt_transform.py`
- Qwen-Agent：`qwen_agent/agents/virtual_memory_agent.py`

**本项目代码实测**
- `src/agents/graph/agent_node.py:28-57,119`、`src/config/const.py:53,77-78`、`src/config/settings.py:92-94,289`、`src/chat/manager.py`
- 运行时实测：`langchain 1.3.11` 含 `SummarizationMiddleware`；`langgraph 1.2.9` + `langgraph-checkpoint 4.1.1`（**缺 `langgraph-checkpoint-postgres`**）；`tiktoken 0.13.0` 在环境内；`langmem` 未装。

---

---

## 八、实测：模型真实上下文窗口（2026-10-08）

### 8.1 测量方法与结果

方法：向 DashScope 兼容模式 `POST /chat/completions` 发送**精确 token 数**的输入（tiktoken cl100k 计数），`max_tokens=1`，逐级放大直到被拒，读错误信息中声明的范围。被拒请求不计费。

| 模型 | 配置角色 | 实测输入上限 | 证据 |
|------|---------|-------------|------|
| `deepseek-v4-pro-0813` | `LLM_MODEL` + `CLASSIFY_MODEL`（2026-10-08 前的运行时模型） | **1,000,000 token**（硬上限） | 545,544 接受；1,000,011 拒绝，错误 `Range of input length should be [1, 1000000]` |
| `qwen3.8-2.4t-a95b` | `LLM_MODEL` + `CLASSIFY_MODEL`（**2026-10-08 起的运行时模型**） | **983,616 token**（硬上限） | 909,561 接受；1,818,651 拒绝，错误 `Range of input length should be [1, 983616]` |
| `qwen3.8-max` | `RAGAS_LLM_MODEL` | **≥ 909,621**（未探到上限） | 909,621 接受（http 200） |
| `glm-5.3` | — | **≥ 909,573**（未探到上限） | 909,573 接受（http 200） |
| `qwen3.8-flash` | — | 未知（403 无额度） | `AllocationQuota.FreeTierOnly`（与已知「上游 403 额度耗尽」同一现象） |

> 成本说明：本轮探针共消耗约 **2.8M 输入 token**（其中 deepseek 探针约 0.95M、qwen3.8-max 与 glm-5.3 各约 0.91M）。

### 8.2 结论：窗口是「一模型一值」，不能用单个环境变量表达

- 现状 `MODEL_CONTEXT_WINDOW_TOKENS=32768` 与实测 **1,000,000 相差约 30 倍**；`_truncate_history` 的默认 8000 相差约 **125 倍**。（2026-10-08 换用 `qwen3.8-2.4t-a95b` 后为 **983,616**，与 32768 仍相差约 30 倍。）
- 同一 provider 下模型窗口也不同（1,000,000 vs ≥909,621），且**错误行为不同**：deepseek 直接给出 `[1, N]`（可免费测量），qwen/glm 会直接接受 909k（需继续放大才知上限）→ 探针必须支持「逐级放大直到报错」，不能假设一次就中。
- **窗口是硬上限，不是目标**：1M 窗口 ≠ 要喂 1M（context rot + 成本 + lost-in-the-middle，见 §二 A）。压缩阈值应远低于窗口。

### 8.3 设计：per-model 窗口解析器

```
window = resolve_context_window(model_name)
  ① 精确命中注册表 MODEL_CONTEXT_WINDOWS[model]           → source=registry
  ② 前缀/别名匹配（如 vanchin/deepseek-v4-pro-* → 1_000_000） → source=pattern
  ③ provider 元数据（LiteLLM /models 若提供 context_length）  → source=provider
  ④ 保守默认（32_768）                                     → source=default
  同时记录 window_source 进日志，便于审计「这次预算按哪个窗口算的」
```

- 注册表为**配置资产**（放 `src/config/`，遵循「硬编码集中管理」），**每模型一项 + 通配项**；换模型只改注册表，不改业务代码。
- 探针做成**离线 CLI**（如 `src/cli/probe_context_window.py`），**绝不在请求路径上探针**；结果人工/CI 写回注册表。
- 预算计算统一走 `resolve_context_window()`，禁止任何地方再写死数字（当前 `agent_node.py:32` 的 8000 与 `settings.py:93` 的 32768 都要收口到这里）。

---

## 九、上下文分段：哪些固定、哪些可压缩

**重要前提**：本项目的 system prompt **已有分段**（`base / runtime_contract / sources / tools / output` 五段，见 `docs/agents/prompt-ownership.md`），但那是按「**归属与可变性（替换/条件/恒定）**」分的，服务于 prompt 维护。本章的分段是**另一个轴**——按「**压缩可变性（fixed vs compressible）**」分，服务于上下文治理。两者正交，不要混为一谈。

一次模型调用时，上下文的全部组成与处置：

| 层 | 组成 | 来源 | 可变性 | 处置 |
|----|------|------|--------|------|
| **S1** system 主段 | base + runtime_contract + sources + tools + output（`build_system_prompt`，`SECTION_ORDER`） | 代码 + YAML | **固定**（会话内不变） | **永不压缩**；放最前（缓存友好） |
| **S2** system 次段 | 未绑定提示（`kb_bound=False`） | `_build_unbound_message` | 固定 | 永不压缩 |
| **S3** 技能注入 | `SKILL_INJECTION_PREFIX` 的 HumanMessage（skill 正文） | 历史中被标记的行 | **半固定**（仅相关轮需要） | 后续轮可移除（渐进披露）；压缩时**优先丢** |
| **S4** 对话历史 | user/assistant 轮对 | Redis / MySQL | **可压缩**（旧轮 → 摘要） | L2 摘要，保留 boundary |
| **S5** 当前用户消息 | 本轮 query | 请求 | **固定（不可动）** | 永不压缩，必须保留 |
| **S6** 轮内工具结果 | `retrieve_kb` 的 chunk、`search_web` 的网页正文 | agent 循环 | **可压缩 / 可外置**（**最大 token 消耗源**） | L0 写入即截断/去重；L1 旧轮裁剪/外置 |
| **S7** 推理回传 | assistant 的 `reasoning_content`（provider 要求回传） | 上一轮 response | **可压缩**（旧轮仅对当时决策有用） | L1 旧轮占位化 |
| **S8** 工具定义 | `bind_tools` 的 tool schema | 代码 | 固定 | 前置（与 S1 同属稳定前缀） |

**判据（谁可以动）**：

1. **绝不压缩**：S1 / S2（指令契约）、S5（当前问题）、S8（工具 schema，改了会破坏缓存且改变能力）。
2. **优先压缩顺序 = 信息密度从低到高**：先扔「体积大、可回读、且已过时」的 **S6 工具结果 → S7 推理 → S3 技能注入 → S4 历史**。语义主线（历史）**最后**才动。
3. **固定前缀 + 可变后缀**：S1 / S2 / S8 放在**最前且会话内不变** → 命中 KV cache；S3–S7 在后。压缩/清理会破坏缓存前缀，故**清理要「一次清够」**而非频繁小幅（见 §二 B6）。
4. **transient vs persistent**：S6 / S7 的裁剪适合 **transient**（只改本次调用视图，不改 state）；S4 的历史压缩应是 **persistent**（改写 state，否则每轮重复计算）。二者在 LangChain 官方是有明确定义的两种机制（§二 B8）。
5. **当前轮的检索证据永不丢**：S6 里**属于本轮**的 chunk 是 `[n]` 引用溯源的基础，压缩只能作用于**已完成轮次**的工具结果。

---

## 十、四层阶梯详解

四层的**触发顺序**：写入时 L0 → 每次模型调用前 L1 → 仍超阈值则 L2 → 跨会话由 L3 提供。

```
写入 state ──▶ L0 写入侧治理（零 LLM，逐条）
                  │
每次模型调用 ──▶ L1 请求前瘦身（零 LLM，消息级，tool-pair 安全切点）
                  │ 仍超阈值？
                  ├──▶ L2 阈值摘要（LLM，持久，改写 state）
                  │
跨会话 ────────▶ L3 长期记忆 Store（PostgresStore + pgvector）
```

### L0 — 写入侧治理（零 LLM，写入即生效）

- **时机**：工具结果/网页正文**写入 state 的那一刻**（`rag_tools.py` / `web_tools.py` 返回、构造 ToolMessage 处）。
- **动作**：① 超 `thresholdChars` → 保留 head + tail，中段替换为 `[... pruned N chars ...]`；② 相同工具结果按 sha256 指纹去重（引用替代）；③ 旧 `thinking` 块占位化。
- **参数**（照 deepseek-harness `compaction-tool-result-pruner`）：`thresholdChars≈8192`、`headChars≈4096`、`tailChars≈1024`；按 Unicode code point 切（避免拆代理对）。
- **不变量**：保留「调用发生过」的痕迹；**不删本轮证据**；无持久可回读替代品时不删。
- **成本**：0。**收益**：直接砍掉最大的 token 源，把昂贵的 L2 推迟/减少。

### L1 — 请求前瘦身（`pre_model_hook`，零 LLM，消息级）

- **时机**：每次模型调用前（`create_agent` 的 `pre_model_hook` 或自建 middleware）。
- **动作**：按 **token 预算**（绝对量，非比例）保留最近尾部，丢弃更旧的**整条**工具结果/推理消息；用 **tiktoken**（已装）替换 `len(content)//2` 粗估。
- **硬约束**：切点必须落在 **tool-call / tool-result 成对完整**处（三家独立收敛，见 §三）；否则 provider 报 400。
- **与 L0 的区别**：L0 是**内容级**（单条内部截断），L1 是**消息级**（整条丢弃）。二者互补，不重复。
- **成本**：0。**产出**：若瘦身后仍超阈值，才进入 L2。

### L2 — 阈值摘要（LLM，持久，改写 state）

- **触发**：`估算token > window - reserve`（**绝对量**，WeKnora 实证优于比例）。
- **动作**：把 boundary **之前**的历史交给摘要模型 → 生成**结构化摘要**，作为**一条 user 消息**注入；boundary **之后**的原文完整保留。重建 = `[system, summary, 保留尾部]`。
- **结构化段**（照 WeKnora `prompts.go`）：`Goal / Progress(Done·InProgress·Blocked) / Key Decisions / Next Steps / Critical Context`。
- **四条硬不变量**：① 摘要必须**比被替换区间更小**；② **就地更新不得增长**（已完成项折叠成一行、废弃方案整段删）；③ **拒绝截断摘要**（`finish_reason=length` 视为失败）；④ 用标签识别上一次摘要，**绝不做「摘要之摘要」**。
- **参数**：`reserve`（留给 output + 当前轮的空间，从 response 预算反推，而非拍脑袋）、`keep`（保留最近 N 条原文）。
- **落点**：优先用**已装**的 `SummarizationMiddleware`（`trigger=("tokens", N)` / `keep=("messages", M)` / 自定义 `summary_prompt`）；若与本项目 `build_prompt` 手工分层（system/rest 拆分 + `SKILL_INJECTION_PREFIX` 注入）冲突，则自建等价 `pre_model_hook`。
- **失败降级**：压缩失败要**熔断**（claude-code `MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES=3`），退回硬截断并记 `degraded` 标记，**绝不静默损坏**；压缩前后 token / 被替换区间 / degraded 一并落 Langfuse。
- **成本**：**1 次额外 LLM 调用**（用便宜模型 + `temperature=0`）。

### L3 — 长期记忆 Store（跨会话）

- **存储**：`AsyncPostgresStore` + pgvector（**需新增依赖 `langgraph-checkpoint-postgres`**，当前未装）；`namespace=(user_id, "memories")` 做多租户隔离。
- **写入**：**background 异步**（不阻塞回复）；**类型化提取**——只存「用户是谁 / 偏好 / 规则 / 经历」（episodic + user-semantic），**不存任务流水账**；`importance` 门控（照 openakita：仅高重要度才升永久）。
- **检索**：语义 `search(query=...)`，注入 system 段或作为工具按需取。
- **边界（回应原「不做长期记忆」的顾虑）**：记忆只答「用户是谁、之前发生过什么」；**事实/知识类问题仍强制走 RAG 取证** → 与溯源原则共存。
- **遗忘**：TTL + 重要度/时间衰减（避免「该忘不忘」）。

### 层间取舍与失败模式

| 层 | 成本 | 失败模式 | 防护 |
|----|------|---------|------|
| L0 | 0 | 删了无法回读的证据 | 只删「有替代/已过时」的；保留调用痕迹 |
| L1 | 0 | 切点切断 tool 对 → 400 | tool-pair 平衡判据 |
| L2 | 1 次 LLM | 摘要漂移 / 越压越大 / 静默截断 | 四条不变量 + 熔断 + degraded 标记 |
| L3 | 写/读各一次 | 记忆中毒 / 该忘不忘 / 凭记忆幻觉 | 来源校验 + TTL + 边界限定 |

---

## Rerun Inputs
```
workflow: firecrawl-deep-research
topic: Agent 上下文管理 / 上下文压缩 / 记忆模块（面向 Corporate Agent Harness）
depth: thorough
output: markdown
```
