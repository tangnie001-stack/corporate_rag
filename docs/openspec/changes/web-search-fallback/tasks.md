# web-search-fallback Tasks

## 1. 配置与常量

- [ ] 1.1 `src/config/settings.py` 新增 env：`TAVILY_API_KEY`、`WEB_SEARCH_ENABLED`（默认 true）、`WEB_SEARCH_PER_TURN_LIMIT`（默认 3）、`TAVILY_TIMEOUT`（默认 5s）、`ASK_USER_MODE_DSH`（默认 true）
- [ ] 1.2 `src/config/const.py` 新增 web 兜底文案常量：`"该问题不在当前知识库范围内"`（不进 ABSTENTION_MARKERS）、web 每轮限次提示文案、web 引用 kind 常量（kb/web）、`STAGE_WEB_SEARCH` 状态事件与"正在联网搜索…"文案
- [ ] 1.3 `pyproject.toml` 显式声明 `httpx`（当前已传递安装，声明防未来镜像重建漂移；无需重建）

## 2. prompt 重写

- [ ] 2.1 `FINANCIAL_SYSTEM_PROMPT` 重写为 11 条：闲聊直接答 / 一律先检索不预判 / 判定标准（含核心实体才算相关）/ 换词再检（提炼核心实体）/ web 兜底（先说明"不在知识库范围"再联网）/ 纯拒答为最后手段 / 证据不足分支 / **防滥用 guard（知识库能答的不联网）** / 仅文档作答 / 标注年份期间 / 语言一致
- [ ] 2.2 核对 `USER_PROMPT_TEMPLATE` 与新增 web 兜底路径不冲突（"使用工具获取所需信息"已覆盖 search_web）
- [ ] 2.3 确认 `prompt_manager.py:28` 的 `_INLINE_CITATION_INSTRUCTION` 追加机制不被重写破坏（`_FALLBACK_SYSTEM_PROMPT` 拼接仍成立，通用覆盖 KB/web 引用）
- [ ] 2.4 确认当前走本地 fallback（日志 "Using fallback prompt"）；若目标环境配置了 Langfuse 提示词，同步更新 Langfuse 侧

## 3. search_web 工具

- [ ] 3.1 实现 Tavily search 调用（httpx，`TAVILY_TIMEOUT` 超时熔断，失败返回空；确认日志不打印 `Authorization` 头中的 key）
- [ ] 3.2 实现 Tavily extract 拉取 top-1~2 个关键 URL 正文（补足摘要不足以回答的场景；正文注入前截断，上限 2000 字符防上下文爆窗）
- [ ] 3.3 新增 `search_web` 工具（args schema + 工具函数），结果以 source=URL 追加进 `tool_contexts`，**沿用 retrieve_kb 的全局递增编号机制**（offset=len(tool_contexts)）
- [ ] 3.4 `WEB_SEARCH_ENABLED=false` 时不注册 search_web 工具
- [ ] 3.5 `RequestContext` 增加 web 调用计数，达 `WEB_SEARCH_PER_TURN_LIMIT` 后返回限次提示
- [ ] 3.6 `agent_service` 映射 search_web 工具 start/end → `SSEStatusEvent(STAGE_WEB_SEARCH)`（start"正在联网搜索…" / end"联网搜索完成，正在分析…"）

## 4. 判定与检索

- [ ] 4.1 `src/rag/retrieval.py` 检索结果按 doc_id 去重（**RRF 融合后、rerank 前**，保留每个文档最先出现的结果）
- [ ] 4.2 换词再检第二枪 `top_k` 加大：prompt 规则 4 指示模型第二次检索显式传 `top_k=10`（工具已支持 le=10）

## 5. format_node 与引用

- [ ] 5.1 `src/rag/context.py` 的 `RAGContext` 新增 `kind: str = "kb"` 字段；`retrieve_kb` 创建时默认 kb，`search_web` 创建时设 web
- [ ] 5.2 `format_node` 区分纯拒答（命中拒答标记**且**不含 `[n]` 引用才 `citations=[]`）与 web 兜底回答（保留引用）；citations 项带 `kind`（从 ctx.kind 透传）
- [ ] 5.3 `src/utils/sse.py` 的 `SSECitationEvent` 新增 `kind` 字段
- [ ] 5.4 `agent_service` 的 citations 转换透传 `kind`

## 6. ask_user 双模式

- [ ] 6.1 `AskQuestion` 新增 `options: list[str] | None` 字段（模型可自带候选）
- [ ] 6.2 `ask_user` 按 `ASK_USER_MODE_DSH` 分支解析选项：true → 模型自带（可空）；false → 有 options 用模型自带，否则按 dimension 走 `_load_dimension_options`
- [ ] 6.3 ask_user 工具描述补充：知识库问题可用 dimension（系统注入真实候选）、非知识库问题自行提供 options

## 7. 可观测性

- [ ] 7.1 判定路径日志（在 retrieve_kb / search_web 工具返回处记录）：`judge: query=... stage=retrieve/retry/web_confirm/answer/abstain reason=...`
- [ ] 7.2 search_web 调用日志带用量：`tool=search_web iteration=... count=... latency_ms=...`（观察 Tavily 免费额度消耗）

## 8. 测试

- [ ] 8.1 `format_node` 双文案区分测试（含"混入拒答语但带 [n]"防御场景）+ `kind` 字段断言
- [ ] 8.2 `search_web` 测试（mock Tavily：正常返回构造 `[n]` 块 / 超时异常→空结果不阻塞）
- [ ] 8.3 `ask_user` 双模式测试（dash 用模型 options / dual 按 dimension 注入）
- [ ] 8.4 检索 doc_id 去重测试
- [ ] 8.5 **更新存量测试断言**：`tests/agents/tools/test_ask_user.py`（AskQuestion 加 options）、`tests/services/test_agent_service.py` + `test_dual_stream.py`（citations 加 kind）、`tests/api/test_clarify.py`（ask_user 流程）

## 9. 验证闭环

- [ ] 9.1 `pytest tests/ -v` 全量通过
- [ ] 9.2 `ruff check .` 无错误、`pyright src/` 不新增 error
- [ ] 9.3 `.env` 配置 `TAVILY_API_KEY` 后 `docker compose restart app`，手动验证五条路径：闲聊直接答 / KB 命中文档作答 / KB 未命中换词再检 / 转 web 兜底（含引用 kind=web）/ Tavily 不可用时纯拒答
- [ ] 9.4 对照 spec 自检：web-search-fallback / retrieval-judgment / ask-user-mode 三新能力 + retrieval-quality / answer-grounding 两 delta 的每个 requirement 均有对应实现
