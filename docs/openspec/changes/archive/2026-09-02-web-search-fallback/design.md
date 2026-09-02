## Context

当前系统为 `kb_router → agent 循环 → format` 架构，agent 工具集仅 `retrieve_kb` 和 `ask_user`（rag_tools.py:169）。知识库覆盖不了的问题（如"阿里云云防火墙计费区别"）会被强制检索（rerank 最高分 0.0059）后生硬拒答"未在文档中找到相关数据"，信息"没了"。

根因：
- `FINANCIAL_SYSTEM_PROMPT` 规则 1 强制"先检索、不得凭记忆"，压过工具描述"闲聊不需要调用"，无非 KB 边界
- 无 web 搜索工具
- `ask_user` 只能按 company/period/metric 维度由系统注入 KB 候选，非 KB 澄清被锁死

已知约束：
- 项目历史（query-rewrite 变更）已证明 rerank 分数无绝对意义（相关 0.14-0.37 与无关 0.07-0.15 重叠），不能用阈值判定
- 容器 override 挂载 `src/`（改 .py 无需 --build，restart 生效），但新增 Python 依赖需重建镜像
- KB 存在重复文档（neusoft_2025_q1.pdf×4、tencent_2024_annual.pdf×2），污染检索 top-8 多样性

## Goals / Non-Goals

**Goals:**
- 知识库覆盖不了的问题能联网兜底（search_web + Tavily）
- "是否在 KB"判定改为"一律先检索 + 模型读内容判定"（测量代替预测），提升判定准确率
- ask_user 支持 dsh 式自由澄清，提供 `ASK_USER_MODE_DSH` 开关（默认 true）便于 AB 对比
- 判定/兜底全程可观测（路径日志）、可控（开关/限次/熔断）

**Non-Goals:**
- KB 公司清单注入（判定辅助，不做）
- prompt 防注入（搜索结果仅作资料的安全约束）
- KB 边界评估集、多轮指代 standalone query 验证
- 前端 web 引用样式适配（`kind` 字段先透传，渲染后置）
- MCP 化（接口固定保证可换，MCP 接入后置）

## Decisions

### D1: 一律先检索 + 模型读内容判定

非闲聊实质性问题一律调用 `retrieve_kb`，不预判 KB/非 KB。模型读检索到的 chunk **内容**判定相关性；判定标准为"chunk 含 query 至少一个核心实体才算相关"。

- **理由**：KB 覆盖度是知识库的属性，模型无法预测（它不知道 KB 库存），但检索结果是测量。预判 gate 引入"判错路径"的新出错点，降准确率。
- **备选**：① 预判 gate（公司清单/classify）——省成本但降准确率，且 agentic 变更刚删过 classify；② 并行 KB+web——web 信息污染精确财务数据，成本翻倍。
- **rerank 角色**：只做排序（top-5 质量），**不做分数阈值**（项目历史否决绝对阈值）。

### D2: 换词再检召回兜底

检索空/全部无关时，模型**提炼核心实体换一种问法**重新 `retrieve_kb`，第二枪 `top_k` 加大到 10（判定"不在 KB"前看更多候选，降低相关内容排在后面没被看到的概率）。**机制**：prompt 规则 4 指示模型第二次检索时显式传 `top_k=10`（`RetrieveKBArgs.top_k` 已支持 le=10），工具本身不感知"第几枪"。

- **理由**："不在 KB"结论受检索召回约束，多枪 + 更多候选压低召回漏判。
- **备选**：仅单次检索（召回漏判风险高）。

### D3: `search_web` 用 httpx 直调 Tavily REST（search + extract）

`search_web(query, top_k)` 内部用 httpx 调 Tavily API：先 `search` 拿摘要列表，再对 **top-1~2 的 URL 用 `extract` 拉正文**保证回答质量（extract 有额外成本/延迟，默认最多 2 个 URL，避免拉一堆）。**extract 正文注入 tool_contexts 前截断（上限 2000 字符）**，防长网页撑爆上下文窗口。

- **理由**：httpx 已随 langchain 传递安装，不新增依赖 → 免镜像重建；Tavily 免费 1000 credits/月、API 简单。
- **备选**：① tavily-python SDK——需新增依赖+重建镜像；② 博查——免费是一次性资源包，中文好但作为备选源后置；③ MCP server——多外部服务时才值得，后置。
- **接口固定**：`search_web(query)` 契约不变，服务商可换实现。
- **Key 安全**：`TAVILY_API_KEY` 仅作 HTTP `Authorization` 头，**任何日志不得打印请求头/URL 查询参数中的 key**（项目 API Key 脱敏规则，校验 src/utils/desensitize.py）。

### D4: search_web 结果追加 tool_contexts，citations 加 kind 字段

`search_web` 结果以 source=URL 的 RAGContext 形式追加进 `RequestContext.tool_contexts`，与 `retrieve_kb` 共用 format_node 引用机制。**沿用 retrieve_kb 的全局递增编号**（`offset = len(tool_contexts)`），避免 KB 与 web 引用编号冲突。

`RAGContext` 新增 `kind: str = "kb"` 字段作为来源类型载体：`retrieve_kb` 创建时保持默认 `kb`，`search_web` 创建时设 `web`；`format_node` 将 `ctx.kind` 透传到 citation，`SSECitationEvent` 携带 `kind` 下发前端（前端渲染后置）。

- **理由**：format_node 不区分来源，一套引用机制；kind 为前端可信度区分预留。
- **备选**：web 引用单独通道（多一套机制，复杂）。

### D5: format_node 双 abstention 区分（防御式）

`ABSTENTION_MARKERS = ("未在文档中找到",)` 保持不变；web 兜底文案"该问题不在当前知识库范围内"**不在** ABSTENTION_MARKERS 中。

纯拒答判定增加防御条件：**仅当回答命中拒答标记 且 不含任何 `[n]` 引用标记时**才判纯拒答（`citations=[]`）。这样即使 web 兜底回答措辞混入了"未在文档中找到"（如"未在文档中找到该信息，该问题不在当前知识库范围内，以下是网络搜索结果[1]…"），只要带了引用标记就不会被误删。

- **理由**：不依赖 prompt 保证两种文案严格不混用，format_node 自身能区分"纯拒答（无引用）"与"兜底回答（有引用）"。

### D6: 重复文档去重

检索结果按 `doc_id` 去重（保留每个文档最先出现的结果），**位置在 RRF 融合后、rerank 前**——去重后再精排，保证 top-5 候选多样性。当前 KB 存在 neusoft×4 重复，不去重会占满 top-8、漏掉其他文档内容，直接破坏"内容判定"前提。

- **备选**：入库 md5 去重（改动面大、涉及存量重建，后置）；检索结果去重（本次最小改动）。

### D7: ask_user 双模式 + `ASK_USER_MODE_DSH` 开关

`AskQuestion` 新增 `options: list[str] | None`（模型可自带候选）。`ASK_USER_MODE_DSH`（env，默认 `true`）控制解析逻辑：

- `true`（dsh 风格）：`options = q.options or []`——模型全自由构造，系统不注入
- `false`（双模式）：`q.options` 非空用模型自带；否则按 `dimension` 走 `_load_dimension_options`（KB 真实候选防编造）

- **理由**：对照 deepseek-harness 的 `ask_user_question`（纯模型驱动 + 自由格式），用开关 AB 对比两种澄清策略。
- **备选**：只做 dsh 单模式（丢失 KB 防编造注入能力，不可逆）。

### D8: 控制与可观测

- `WEB_SEARCH_ENABLED`（默认 true）：全局开关，关闭时不注册 search_web 工具
- `WEB_SEARCH_PER_TURN_LIMIT`（默认 3）：每轮对话 web 调用限次，防模型连环搜
- `TAVILY_TIMEOUT`（默认 5s）：超时/失败 → 返回空 → 走纯拒答，不阻塞 agent
- **web 搜索状态事件**：新增 `STAGE_WEB_SEARCH` 状态事件，search_web 工具 start → "正在联网搜索…"、end → "联网搜索完成，正在分析…"（对齐 retrieve 的 start/end 双文案），agent_service 映射，避免用户干等
- **判定路径日志**（在 retrieve_kb / search_web 工具返回处记录，落点明确）：
  `judge: query=... stage=retrieve/retry/web_confirm/answer/abstain reason=...`
  （`answer`= KB 命中直接作答；`abstain`= 纯拒答）
- **用量观察**：search_web 调用日志带 `tool=search_web iteration=... count=...`，观察免费额度消耗

### D9: 与 agentic-clarification 协调

本 change 与进行中的 `agentic-clarification` 均修改 `prompts.py`、`rag_tools.py`、ask_user 相关代码。实施前确认 agentic-clarification 状态（未归档则按当前代码基线叠加，避免互相覆盖）。

### D10: prompt 重写要点

`FINANCIAL_SYSTEM_PROMPT` 重写为 11 条，除判定/兜底流程外必须包含：
- **防滥用 guard**：知识库能回答的问题不要调用 search_web，仅确认 KB 无法覆盖时才联网（防过度上网烧 Tavily 额度、污染回答）
- **不破坏引用指令**：`[n]` 引用指令由 `prompt_manager.py:28` 运行时追加（`_FALLBACK_SYSTEM_PROMPT = FINANCIAL_SYSTEM_PROMPT + _INLINE_CITATION_INSTRUCTION`），重写后该拼接仍成立；指令通用覆盖 KB 与 web 引用，无需新增
- **Langfuse 提示词源**：当前日志确认走本地 fallback（"Using fallback prompt"）；若某环境配置了 Langfuse 提示词，需同步更新，否则新规则不生效
- `USER_PROMPT_TEMPLATE` / `CLASSIFIER_*` / `REWRITE_*` 均不改（旧 RAGChain 遗留或 query_router/CLI 用，与本需求无关）

## Risks / Trade-offs

- [web 兜底可能给出过时/错误信息] → extract 拉正文 + prompt 要求基于搜索结果作答并标注来源；Tavily 免费额度有限，超量后降级纯拒答
- [一律检索的成本（~0.7s/次非闲聊）] → 接受，准确率优先；后续可叠加库存 gate 做成本优化（本次不做）
- [dsh 模式模型可能编造澄清选项] → 开关默认 true 但可一键关回 dual 模式（KB 注入防编造）
- [prompt 重写影响整个 agent 行为] → 判定路径日志观察；异常可回滚 prompt
- [重复文档去重可能掩盖"同内容不同版本"] → 按 doc_id 去重保留各文档结果，v1 可接受
- [web 引用与 KB 引用混在一起可能误导用户] → `kind` 字段区分（前端渲染后置，但数据已就绪）

## Migration Plan

1. `.env` 新增：`TAVILY_API_KEY`、`WEB_SEARCH_ENABLED=true`、`WEB_SEARCH_PER_TURN_LIMIT=3`、`TAVILY_TIMEOUT=5`、`ASK_USER_MODE_DSH=true`（key 不提交）
2. 改 .py 后 `docker compose restart app`（override 挂载 src/，无需 --build）
3. 验证路径：
   - 闲聊 → 直接答，不检索
   - KB 问题 → 检索命中 → 文档作答 + 引用
   - KB 未命中 → 换词再检 → 仍无 → "不在知识库范围" + web 兜底 + 引用（kind=web）
   - Tavily 不可用 → 熔断 → 纯拒答，agent 不阻塞
4. 观察判定路径日志，确认无系统性误判后逐步放开
