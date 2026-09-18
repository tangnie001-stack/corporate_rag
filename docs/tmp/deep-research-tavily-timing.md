# Deep Research: 316s 静默 + Tavily 接入方式（工具 vs MCP）与串并行耗时

生成日期：2026-09-15 ｜ 深度：Thorough ｜ 一手来源：本仓库源码 + langgraph/langchain/openai/httpx 已装包源码 + 官方文档

## Executive Summary

三个子问题都有明确答案：

1. **接入方式**：**保持现有的工具方式，不换 MCP**。Tavily 官方 MCP 确实存在且工具面吻合（`tavily-search` / `tavily-extract`，与你现在直调的两个端点一一对应），但换过去**不解决 316s 问题，反而有复现它的风险**，同时会破坏你四个已落地的进程内机制（引用编号、限次、行为信号、来源定档）。
2. **串并行**：现在是**三层两并行一串联**结构——回合内多个工具调用**并行**、`search_web` 内部的多个 query **并行**、但 `search` → `extract` **串行**。所以单次 `search_web` 的耗时 = `max(各 query 搜索)` + `正文抽取`。
3. **怎么改**：两处改动**与接不接 MCP 无关**——① 给 `search_web` 加**整体 deadline**；② 给 `get_llm()` 传 `timeout` / `max_retries`。

**本次调研最重要的发现**：两段静默（316s 的 `search_web`、6-7 分钟的 LLM）**属于同一类根因**——**httpx 的 `timeout=X` 是"每个阶段各 X 秒"，不是"整体 X 秒"**。这个陷阱同时出现在你的 `tavily_client.py` 和 OpenAI SDK 的默认值里（`openai/_constants.py:9`）。

## Key Findings

### 1. 并发结构是三层，其中两层并行、一层串行

| 层级 | 粒度 | 并发性 | 证据（一手来源） |
|---|---|---|---|
| **回合级** | 一条 assistant 消息里的多个 `tool_calls` | ✅ **完全并行，无并发上限** | `langgraph/libs/prebuilt/langgraph/prebuilt/tool_node.py:858` — `outputs = await asyncio.gather(*coros)`；同文件 `:10` 注释 "**Parallel execution of multiple tool calls for efficiency**"；全文件无 `semaphore` / `max_concurrency` / `limiter` |
| **工具级** | `search_web` 内的多个 query（≤4） | ✅ **并行** | `src/agents/tools/web_tools.py:81-87` — `asyncio.gather(*[tavily_search(q) for q in queries], return_exceptions=True)` |
| **阶段级** | `search` → `extract` | ❌ **串行** | `src/agents/tools/web_tools.py:122-125` — `extract_urls` 由 search 结果构造，`await tavily_extract(...)` 在 `gather` **之后** |

**耗时公式**：

```
单次 search_web      = max(各 query 的 tavily_search) + tavily_extract
一个回合（多工具）    = max(所有工具各自的耗时)          ← 因为 ToolNode 并行
```

**你方工具全是 `async def`**（`retrieve_kb` / `ask_user` / `search_web`），所以它们会真正并发跑在同一个事件循环里，不存在"同步工具阻塞其他工具"的问题。

### 2. 316s 的结构性解释：并行 + `gather` 等所有 + **全程无整体 deadline**

并行**不代表**更快地失败。`asyncio.gather(..., return_exceptions=True)` 的语义是**等所有协程结束**（包括超时返回的）。所以：

> 4 路查询里只要有 1 路挂住，整个 `search_web` 就挂住 —— **上界是"最慢那一路"，而不是总时长的和**。

这正是 316s 的形态：不是 4×5s 累加，而是**一条路径长时间不返回**。

### 3. 根因类别：httpx 的 `timeout` 是**阶段超时**，不是总时长

httpx 自身文档字符串（一手来源，`.venv/lib/python3.12/site-packages/httpx/_config.py:78-83`）：

```python
Timeout(5.0)                # 5s timeout on all operations.
Timeout(None, connect=5.0)  # 5s timeout on connect, no other timeouts.
```

**"on all operations" 指的是逐阶段（connect / read / write / pool 各自），不是"整个请求 5 秒"**。这意味着两种情形阶段超时都兜不住：

| 情形 | 为什么阶段超时不生效 |
|---|---|
| 服务端**慢速回包**（持续 trickle） | 每次 read 都有数据 → read 超时不断被重置 → 总时长可远超 5s |
| 连接建立阶段卡住（如 DNS） | 不在 read/write 超时覆盖内 |

这与你记忆里的判读树完全对应（`elapsed_ms≈300000 ok` → 慢速回包；`elapsed_ms≈300000 err=ReadTimeout` → 超时晚触发）。

### 4. 第 2 段静默（LLM 6-7 分钟）是**同一类根因**，且默认更宽

一手来源：已装包 `.venv/lib/python3.12/site-packages/openai/_constants.py:9-10`

```python
DEFAULT_TIMEOUT = httpx.Timeout(timeout=600, connect=5.0)
DEFAULT_MAX_RETRIES = 2
```

而你的 `src/models.py:153` 的 `get_llm()` 只传 `model / temperature / api_key / base_url / callbacks`，**没有传 `timeout`、也没有传 `max_retries`**（`langchain_openai/chat_models/base.py:686,711` 两个字段默认都是 `None`，即回落 SDK 默认）。

**两层放大**：
- 600s 是**阶段超时**（同第 3 条的陷阱），同样不约束总时长
- `max_retries=2` → 一次逻辑调用最坏 **3 次尝试**，暴露量上限约 1800s

观测到的 6-7 分钟（≈400s）落在"单次 600s 预算内"的区间，与"某次尝试耗时很长但最终成功"一致。

### 5. Tavily 官方 MCP 存在，工具面与你现在用的**完全对应**

一手来源：`https://docs.tavily.com/documentation/mcp`（本次已抓取全文）

| 项 | 内容 |
|---|---|
| 远程地址 | `https://mcp.tavily.com/mcp/?tavilyApiKey=<key>`（**Streamable HTTP**，无需本地安装） |
| 本地方式 | `npx -y mcp-remote ...` 或 `npx tavily-mcp` |
| 工具 | **`tavily-search`**、**`tavily-extract`** —— 与你的 `tavily_search` / `tavily_extract` 一一对应 |
| 官方也提供 | LangChain 集成、Agent Skills、CLI |

另据 `apigene.ai`：**MCP 规范已废弃 SSE，改为 Streamable HTTP**。

### 6. **MCP 不提供整体 deadline** —— 换过去不解决 316s

MCP 规范对超时的定位是：**发送方 SHOULD 在超时时发 `$/cancelRequest` 通知并停止等待**（来源：`cyanheads/model-context-protocol-resources` 开发指南）。也就是说**超时实现责任在客户端，协议本身不给总时长保证**。

Octopus Deploy 的实践文章《Resilient AI agents with MCP: Timeout and retry strategies》也正是这个立场——生产级系统要**自己**加 timeout / retry / circuit breaker，MCP 不附送。

### 7. ⚠️ 更值得警惕：**MCP 工具超时经 ToolNode 调用有失效历史**

一手来源：`langchain-ai/langchainjs` issue **#8279**《MCP-adapter tool timeouts are never applied when invoked via ToolNode》

- 状态：**closed**（2025-05-30 创建，2025-07-19 关闭）
- 关因：`@langchain/mcp-adapters@0.5.4` 引入了 `defaultToolTimeout`
- **但后续评论（2025-10-22）报告该特性"仍然完全不能工作"**，并指出根因在调用路径（`tool.invoke` vs `tool.call`，且 `invoke` 的超时"只对 < 60s 生效"）
- 跟进 **issue #9136** 与 **PR #9165** 截至该评论**仍未合并**

**⚠️ 证据缺口**：以上全部来自 **langchainjs（JS）**。我用 `repo:langchain-ai/langchain-mcp-adapters + timeout + ToolNode` 检索**返回 0 条**——**Python 侧没有公开证据**表明该问题存在或已修复。所以不能断言 Python 也坏，但**也无法断言它好**。

对这个问题的诚实结论是：**换 MCP 把"你完全掌控的超时逻辑"换成"一个你无法掌控、且同类问题在姊妹项目里有未收敛历史的超时逻辑"**。

### 8. 换 MCP 会破坏四个已落地的进程内机制

`search_web` 现在不只是"调 API"，它在**本进程内**做了四件事，全部依赖能访问 `RequestContext`：

| 机制 | 位置 | MCP 化后 |
|---|---|---|
| **引用编号**（`[n]` 全局递增，与 `retrieve_kb` 共用池） | `web_tools.py:136-164` — `collector.append(RAGContext(...))` | ❌ 远程工具无法写入你的 `ctx.tool_contexts` |
| **限次控制**（`WEB_SEARCH_PER_TURN_LIMIT`，一次多查询只占 1 次） | `web_tools.py:63-70` | ❌ 远程计数不可控 |
| **行为信号**（`Signal.TO_WEB`，区分"自主降级"与"verify 指派"） | `web_tools.py:168-176` | ❌ 拿不到 `ctx.web_guided` / `ctx.kb_bound` |
| **来源定档**（T0–T4） | `web_tools.py:149` — `resolve_source_tier(...)` | ❌ 同上下文不可达 |

**这不是小改**：要让 MCP 版本保住这四项，你仍然得写一个**本地包装工具**（调 MCP client → 再做编号/限次/定档）。那一刻 MCP 只剩"多一跳"，却没有任何收益。

## Detailed Analysis

### 串行/并行的完整账（回答你的第 2 问）

```
回合开始
  │
  ├─ LLM 返回 tool_calls = [retrieve_kb, search_web]        ← 一次调用
  │
  ├─ ToolNode.ainvoke → asyncio.gather(...)                  【并行】
  │    ├─ retrieve_kb   ──┐
  │    └─ search_web    ──┤ 同时跑，各自耗时 T1 / T2
  │                       │
  │      search_web 内部： │
  │        gather(4 queries)                                【并行】
  │          = max(q1..q4) = Ts
  │        then await tavily_extract(...)                   【串行，等 Ts 之后】
  │          = Te
  │        ⇒ T2 = Ts + Te
  │
  └─ 回合耗时 = max(T1, T2) = max(T1, Ts + Te)
```

**关键推论**：如果模型在同一回合里既调 `retrieve_kb` 又调 `search_web`，**总耗时由较慢的那个决定，不是相加**。所以"把工具改成并行"这件事**已经做完了**——`ToolNode` 替你做了。

你真正缺的不是并行度，是**任何一层都没有整体 deadline**。

### 为什么"换 MCP"与"修 316s"是两件不相干的事

316s 的必要条件有两个，且都不在传输层：

1. **单条路径可以无限期不返回**（httpx 阶段超时不约束总时长 + 无 wait_for 兜底）
2. **并行收集器无条件等待所有**（`asyncio.gather` 语义）

MCP 改变的是"工具怎么被调用"（HTTP/stdio 协议、工具发现、鉴权），**不改变**这两条：

- MCP 客户端同样需要自己实现超时（见 Finding 6）
- 经 ToolNode 调用时，adapter 层的超时配置有失效历史（见 Finding 7）
- ToolNode 的 `asyncio.gather` 语义在两种方案下**完全相同**

**结论：MCP 不是这个问题的解法。**

### 两处改动的具体形状

#### 改动 1：给 `search_web` 加整体 deadline

**目标**：无论内部发生什么（慢速回包、DNS、阶段超时晚触发），本次工具调用在 `D` 秒内必须返回。

改在 `src/agents/tools/web_tools.py` 的 `gather` 与 `extract` 两处外层，包一个 `asyncio.wait_for`：

```python
# 结构示意（非最终代码）：给"搜索阶段"和"抽取阶段"各自的整体上界
try:
    results_list = await asyncio.wait_for(
        asyncio.gather(*[tavily_search(...) for q in queries], return_exceptions=True),
        timeout=settings.WEB_SEARCH_SEARCH_DEADLINE,   # 新增配置项
    )
except asyncio.TimeoutError:
    logger.warning("[retrieval] search_web deadline exceeded stage=search ...")
    results_list = []      # 走既有"全部失败"降级路径（web_tools.py:112-118）
```

要点：
- **`wait_for` 会取消内层任务**，所以 `gather` 必须保留 `return_exceptions=True`，且退出后按既有逻辑过滤异常项（`web_tools.py:89-97`）——现有代码结构**已经兼容**
- 抽取阶段（`web_tools.py:125`）需要**独立**的一个 deadline，否则 `Ts+Te` 仍可超
- **区分"熔断"与"降级"**：deadline 触发应走已有的 `return ""` / 摘要兜底路径，不新增分支
- 按你 `CLAUDE.md` 的"硬编码集中管理"，两个新阈值放 `src/config/settings.py`（`WEB_SEARCH_SEARCH_DEADLINE` / `WEB_SEARCH_EXTRACT_DEADLINE`）

**一个必须留意的风险**：`wait_for` 只在**协程层面**有效。如果卡点发生在**阻塞事件循环**的地方（例如某个同步 DNS 解析），`wait_for` 的回调根本没机会被调度。这正是你 4 处 TIMING 埋点 + 任务栈 dump 要取证的东西——**先看 `TIMING silence_idle_s=...` 后面的任务栈**，它会直接给出挂起点的 `文件:行号`，从而判定属于哪种情形。

#### 改动 2：给 LLM 客户端显式传参

改在 `src/models.py:153` 的 `get_llm()`：

```python
return ReasoningPreservingChatQwen(
    model=model,
    temperature=temperature,
    api_key=SecretStr(LLM_API_KEY),
    base_url=LLM_BASE_URL,
    timeout=settings.LLM_REQUEST_TIMEOUT,     # 新增，覆盖 SDK 默认 600s/阶段
    max_retries=settings.LLM_MAX_RETRIES,     # 新增，覆盖 SDK 默认 2 次
    callbacks=_content_logging_callbacks(),
    **extra_kwargs,
)
```

要点：
- `langchain_openai/chat_models/base.py:686,711` 两个字段都接受显式值，且 `:1207` 会把 `timeout` 透传给 OpenAI client
- **必须先决定是否保留重试**：`max_retries=2` 意味着一次逻辑调用最多 3 次尝试，是 316s/6-7min 的**放大器**。金融问答场景里，"等更久"通常不如"尽快失败并降级"
- 与 `verify` 的 regen 预算（`MAX_VERIFY_REGENERATIONS`）语义要对齐——不要出现"外层有预算、内层还在默默重试"的双层重试

## Contrarian Views And Risks

### 支持换 MCP 的论据（如实呈现）

1. **标准化与复用**：一次接入 MCP，未来换搜索供应商（或加第二个）不需要重写客户端；你现在为 Tavily 手写了 `tavily_client.py`。
2. **远程无需本地依赖**：`https://mcp.tavily.com/mcp/` 是 Streamable HTTP，不用装 npx、不用管版本。
3. **鉴权标准化**：官方支持 OAuth 流（`claude mcp add --transport http tavily https://mcp.tavily.com/mcp`，不带 key 会走 OAuth），比 API key 更规范。
4. **工具发现**：`list_tools()` 让工具面可演进（Tavily 新增 `crawl` / `map` / `research` 时自动可用），你现在每加一个端点都要手写。
5. **符合你 P2 路线**：`src/agents/tools/registry.py:3` 已经为 MCP 预留了入口——如果 P2 本来就要接 MCP，这一刀是迟早要挨的。

### 反对/风险

| 风险 | 说明 |
|---|---|
| **不解决超时** | 见 Finding 6/7；可能把已定位的问题重新变成不可控 |
| **超时失效史** | 姊妹项目（langchainjs）同类问题在合并修复后仍被报告"完全不能用"，且跟进 PR 未合 |
| **破坏四项进程内机制** | 见 Finding 8；要么功能回退，要么写包装层（等于没换） |
| **多一跳延迟** | 客户端 → MCP 服务器 → Tavily API，比直连多一次网络往返；对"简单问题省延迟"的目标是反向的 |
| **新增依赖** | `langchain-mcp-adapters` / `langchain.mcp.MCPAdapter` + 连接生命周期管理（stdio 需起子进程） |
| **调试链路变长** | 现在 panic 时你能直接读 `tavily_client.py` 的埋点；MCP 化后要多读一层 adapter |

### 一个"两者都要"的可能路径

如果 P2 确实要接 MCP，**可以按供应商分层决定**，而不必一刀切：

- **Tavily 保持直调**（它有进程内耦合的四项机制，且这是你最痛的 316s 路径）
- **未来的新供应商走 MCP**（无历史包袱，标准化收益最大化）

这样 P2 的 MCP 能力有落点，而不会拿你最熟、最需要控制的一环去试错。

## Open Questions

1. **316s 属于哪一类？**（决定改动 1 是纯 deadline 还是还需取消防线）
   - `elapsed_ms≈300000 err=ReadTimeout` → 超时晚触发（事件循环被阻塞或 DNS 卡在同步路径）
   - `elapsed_ms≈300000 ok` → 服务端慢速回包，**阶段超时原理上就不约束总时长**（此情形 `wait_for` 必然有效）
   - **判据在你已上线的 4 处 TIMING 埋点里**，关键是 `silence_idle_s` 之后的**任务栈 dump**

2. **DNS（`getaddrinfo`）是否阻塞事件循环？** 记忆里列为"最可能的 316s 来源"，但尚无取证。若是**协程级**等待，`wait_for` 能救；若是**线程级阻塞事件循环**，`wait_for` 也救不了——那就需要换 resolver 或前置预解析。

3. **Python 侧 `langchain-mcp-adapters` 经 ToolNode 的超时行为**：无公开证据（检索 0 条）。若真要评估 MCP 路线，需在隔离环境实测。

4. **`max_retries` 该留还是该去？** 取决于你更怕"慢"还是更怕"偶发失败"。建议按 trace 数据决策，而非默认。

5. **`WEB_SEARCH_PER_TURN_LIMIT` 与多 query 的交互**：当前"一次多查询只占 1 次额度"（`web_tools.py:70`）是刻意的设计，MCP 化后这个语义无法保持（见 Finding 8）。

## Sources

| # | 来源 | 类型 | 用于 |
|---|---|---|---|
| 1 | `langgraph-1.2.10/libs/prebuilt/langgraph/prebuilt/tool_node.py:10, 858` | 一手（官方源码） | ToolNode 并行执行、`asyncio.gather`、无并发上限 |
| 2 | `corporate_rag/src/agents/tools/web_tools.py:81-87, 112-118, 122-125, 136-176` | 一手（本仓库） | search 并行 / extract 串行 / 四项进程内机制 |
| 3 | `corporate_rag/src/infra/search/tavily_client.py:19-23, 51-84, 104-131` | 一手（本仓库） | httpx client 构造与超时传参 |
| 4 | `.venv/lib/python3.12/site-packages/httpx/_config.py:78-83` | 一手（已装包） | **`timeout=X` 是阶段超时的官方表述** |
| 5 | `.venv/lib/python3.12/site-packages/openai/_constants.py:9-10` | 一手（已装包） | `DEFAULT_TIMEOUT=600`、`DEFAULT_MAX_RETRIES=2` |
| 6 | `.venv/lib/python3.12/site-packages/langchain_openai/chat_models/base.py:686, 711, 1207` | 一手（已装包） | `request_timeout`/`max_retries` 默认 None 并透传 |
| 7 | `corporate_rag/src/models.py:133-158` | 一手（本仓库） | `get_llm()` 未传超时与重试 |
| 8 | [Tavily MCP Server 官方文档](https://docs.tavily.com/documentation/mcp) | 官方一手 | 远程地址 / 传输方式 / `tavily-search`+`tavily-extract` |
| 9 | [tavily-ai/tavily-mcp (GitHub)](https://github.com/tavily-ai/tavily-mcp) | 官方一手 | 远程 vs 本地、OAuth 选项 |
| 10 | [langchain-ai/langchainjs#8279](https://github.com/langchain-ai/langchainjs/issues/8279) | 官方 issue | **MCP timeout 经 ToolNode 失效**；closed 但后续报告仍未工作；指向 #9136 / PR #9165 |
| 11 | [Octopus Deploy: Resilient AI agents with MCP](https://octopus.com/blog/mcp-timeout-retry) | 工程实践 | 生产需自建 timeout/retry/circuit breaker |
| 12 | [MCP 规范 Transports](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports) | 官方一手 | 传输绑定 |
| 13 | [Apigene: MCP SSE vs Stdio (2026)](https://apigene.ai/blog/mcp-sse-vs-stdio) | 二手 | **SSE 已废弃，改为 Streamable HTTP** |
| 14 | [cyanheads MCP 开发指南](https://github.com/cyanheads/model-context-protocol-resources/blob/main/guides/mcp-server-development-guide.md) | 社区指南 | 超时 SHOULD 发 `$/cancelRequest`（超时责任在发送方） |
| 15 | [LangChain MCP 文档](https://docs.langchain.com/oss/python/langchain/mcp) | 官方一手 | Python 侧客户端形态（`MCPAdapter` / `MultiServerMCPClient`） |

**证据质量说明**：核心结论（并发结构、httpx 超时语义、SDK 默认值）全部来自**已装包源码**与**本仓库源码**，可直接复核。MCP 相关结论中，Finding 6/7 是**跨语言推断**（JS issue），Python 侧为**证据缺口**，已在 Open Questions #3 标注。Finding 8 是本仓库代码的**确定性结论**（不是推断）。

## Rerun Inputs

```
workflow: firecrawl-deep-research
topic: 316s 静默根因 + Tavily 接入方式（工具直调 vs MCP）+ 工具调用串并行结构
depth: thorough
output: markdown
queries_used: 12（tavily mcp / mcp timeout spec / mcp sdk timeout 等角度）
sources_scraped: 6（tavily mcp docs / mcp lifecycle / mcp utilities / mcp timeout 实践 / tavily mcp 全文 / github issue）
本地一手来源: 本仓库 4 个文件 + 已装包 3 个文件 + langgraph 源码
credits_used: ~35
notes: 主要证据缺口——(a) Python langchain-mcp-adapters 经 ToolNode 的超时行为无公开证据；
       (b) DNS 是否阻塞事件循环需靠已上线的 TIMING 埋点 + 任务栈 dump 取证；
       (c) 未覆盖：若改用 MCP，包一层本地包装工具后四项机制的实现成本估算
```
