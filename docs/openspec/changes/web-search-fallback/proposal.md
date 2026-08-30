# web-search-fallback

## Why

当前系统对知识库覆盖不了的问题处理体验差。实测案例（trace_2155eb07）：问"阿里云云防火墙包年包月和按流量付费区别"，被强制走 `retrieve_kb`（rerank 最高分仅 0.0059，全部无关），随后生硬拒答"未在文档中找到相关数据"。

根因有三个：

1. **prompt 无领域边界**：`FINANCIAL_SYSTEM_PROMPT` 规则 1"需要文档数据才能回答的问题必须先调用 retrieve_kb，不得凭记忆或常识作答"压过了 `retrieve_kb` 工具描述里的"闲聊、一般概念问题不需要调用"，导致模型对任何实质性问题都先检索，检索到垃圾再拒答。
2. **无 web 搜索工具**：agent 工具集只有 `retrieve_kb` 和 `ask_user`（rag_tools.py:169），模型想联网补充也没有工具可调。
3. **ask_user 被 KB 维度锁死**：澄清只能按 company/period/metric 维度由系统注入 KB 候选，非 KB 问题无法让模型自主构造澄清条件。

目标：知识库覆盖不了的问题能联网兜底；"是否在 KB"的判定改为"一律先检索 + 模型读内容判定"（测量代替预测），提升判定准确率；ask_user 支持 dsh 式自由澄清。

## What Changes

### 1. 判定流程重构（一律先检索 + 内容判定）

- 非闲聊实质性问题**一律先调用 retrieve_kb**，不预判 KB/非 KB
- 模型**读检索内容判定相关性**，rerank 分数不做绝对阈值（项目历史已证明相关 0.14-0.37 与无关 0.07-0.15 重叠，阈值会误杀）
- 判定标准：chunk 内容含 query 至少一个核心实体才算"相关"
- 检索空/全部无关 → **提炼核心实体换词再检**（第二枪 `top_k` 加大到 10，降低"相关内容排在后面没看到"的概率）
- 再检仍空 → 确认不在 KB → **search_web 联网兜底**（回答先说明"该问题不在当前知识库范围内"，保留网络来源引用）

### 2. `search_web` 工具（Tavily）

- 原生 tool 加入 `make_rag_tools`，接口固定 `search_web(query, top_k)`，服务商可换
- 实现：httpx 直调 Tavily REST（`search` + 关键结果 `extract` 拉正文保证回答质量），**不新增 tavily-python 依赖**（避免镜像重建）
- 结果追加进 `RequestContext.tool_contexts`，与 `retrieve_kb` 共用 format_node 引用机制，前端照常渲染
- 熔断：Tavily 超时/限流/key 失效 → 返回空结果 → 走纯拒答，不阻塞 agent

### 3. `format_node` 双 abstention 区分 + 引用 kind

- 纯拒答（"未在文档中找到"）→ `citations=[]`（现状）
- web 兜底（"该问题不在当前知识库范围内"，不在 ABSTENTION_MARKERS 里）→ **保留引用**
- citations 项新增 `kind` 字段（`kb`/`web`），前端可区分来源类型

### 4. 重复文档去重

- 检索结果按 doc_id 去重（当前 KB 存在 neusoft_2025_q1.pdf×4 重复，污染 top-8 召回多样性，直接破坏"内容判定"前提）

### 5. ask_user 双模式 + 开关

- `AskQuestion` 新增 `options` 字段（模型可自带候选）
- 新增开关 `ASK_USER_MODE_DSH`（默认 `true`）：
  - `true`：全部按 deepseek-harness 自由格式——模型自主构造问题与选项，系统不注入候选
  - `false`：双模式——KB 相关问题按 dimension 注入 KB 真实候选（防编造）；非 KB 问题模型自带选项

### 6. prompt 重写（`FINANCIAL_SYSTEM_PROMPT` 11 条）

闲聊直接答 / 一律先检索 / 判定标准 / 换词再检 / web 兜底文案 / 纯拒答降级为最后手段 / 证据不足分支 / 防滥用 guard（知识库能回答的问题不要调用 search_web）/ 仅文档作答 / 标注年份期间 / 语言一致

**注意**：`[n]` 引用指令不在 FINANCIAL_SYSTEM_PROMPT 内，由 `prompt_manager.py:28` 运行时追加（`_INLINE_CITATION_INSTRUCTION`），重写**不得破坏**该追加机制（它通用覆盖 KB 与 web 引用）；若配置了 Langfuse 提示词，需同步更新 Langfuse 侧（当前日志确认走本地 fallback）。

## Capabilities

### New Capabilities

- `web-search-fallback`: KB 检索未命中时联网兜底——`search_web` 工具（Tavily search+extract、结果注入 tool_contexts、限次/熔断/开关）、web 兜底回答文案与引用保留
- `retrieval-judgment`: 一律先检索 + 模型读内容判定（含核心实体才算相关）+ 换词再检召回兜底 + 检索结果按 doc_id 去重
- `ask-user-mode`: ask_user 双模式（`ASK_USER_MODE_DSH` 开关）——dash 全自由格式 / dual 维度注入 + 非 KB 自由格式

### Modified Capabilities

- `retrieval-quality`: abstention 决策路径补充 web 兜底分支——KB 检索未命中时模型可转 search_web 联网补充，而非仅 abstain / ask_user / escalate
- `answer-grounding`: 引用展示新增 `kind` 字段区分 KB/网络来源；web 兜底回答保留引用（区别于纯拒答）

## Impact

- **修改文件**：
  - `src/config/prompts.py` — `FINANCIAL_SYSTEM_PROMPT` 重写（11 条判定/兜底规则）
  - `src/rag/context.py` — `RAGContext` 新增 `kind` 字段（`kb`/`web`，默认 kb），作为引用来源类型的载体
  - `src/agents/tools/rag_tools.py` — 新增 `search_web` 工具；`ask_user` 双模式；`retrieve_kb` 描述微调
  - `src/agents/graph/nodes.py` — `format_node` 双 abstention 区分（防御：仅含拒答标记且无 `[n]` 引用才判纯拒答）+ citations `kind` 字段
  - `src/config/settings.py` — 新增 `TAVILY_API_KEY` / `WEB_SEARCH_ENABLED` / `WEB_SEARCH_PER_TURN_LIMIT` / `TAVILY_TIMEOUT` / `ASK_USER_MODE_DSH`
  - `src/config/const.py` — 新增 web 兜底文案与限次常量、`STAGE_WEB_SEARCH` 状态事件
  - `src/utils/sse.py` — `SSECitationEvent` 新增 `kind` 字段；`SSEStatusEvent` 复用承载 web 搜索阶段文案
  - `src/rag/retrieval.py` — 检索结果按 doc_id 去重
  - `src/services/agent_service.py` — citations `kind` 透传、search_web 工具事件映射
- **依赖**：pyproject.toml 显式声明 `httpx`（Tavily REST，当前已随 langchain 传递安装，显式声明防未来镜像重建时漂移）；**不新增** tavily-python
- **配置（.env，不提交）**：`TAVILY_API_KEY`、`WEB_SEARCH_ENABLED`（默认 true）、`WEB_SEARCH_PER_TURN_LIMIT`（默认 3）、`TAVILY_TIMEOUT`（默认 5s）、`ASK_USER_MODE_DSH`（默认 true）
- **协调**：本 change 与进行中的 `agentic-clarification` 均修改 `prompts.py` / `rag_tools.py` / ask_user，实施前确认其状态，避免合并冲突
- **部署**：改 .py 后 `docker compose restart app`（override 挂载 src/，无需 --build）
- **不做（后置项）**：KB 公司清单注入、prompt 防注入、KB 边界评估集、前端 web 引用样式适配、多轮指代 standalone query 验证、MCP 化
