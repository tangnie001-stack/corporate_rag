# logging-convention-migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立统一日志规范（分层前缀 + 英文 k=v + 五级语义 + retrieval_signal 行为信号），并提供统一 helper，完成 `[retrieval]` 层试点迁移。

**Architecture:** 规范文档先行（rules.md 扩写 + CLAUDE.md 简则），helper 收口在 `src/core/logging.py`（log_event + retrieval_signal 两个函数），然后试点迁移 `[retrieval]` 层（rag_tools / web_tools / retrieval.py）验证规范可执行。存量其余层（verify/agent/session/db/cli）保留在 change tasks 3.2-3.6 待后续批次。

**Tech Stack:** Python 3.11+ / Loguru / pytest / ruff / pyright

## Global Constraints

- 事件消息一律英文小写 k=v；中文仅限用户可见文案（`SSEInteractionTexts`），不得进日志 message
- trace_id 由 logging patcher 自动注入第三段，helper 不重复写 trace_id
- 分层前缀格式 `[层名] 事件 事件k=v`，如 `[retrieval] search start query=... kb_id=...`
- query 截断 40 字符（`query[:40]`）
- 级别语义：debug 诊断细节 / info 里程碑 / warning 降级可恢复（禁记"正常但少见"）/ error 单点已处理 / exception 透传带 traceback
- 单文件 < 400 行红线；本 change 不加新依赖
- `pytest tests/ -v` 全过；`ruff check .` 无错误；`pyright src/` 不新增 error

---

### Task 1: rules.md 日志约定扩写 + CLAUDE.md 简则

**Files:**
- Modify: `docs/agents/rules.md:34-37`（日志约定章节扩写）
- Modify: `CLAUDE.md`（TraceID 段后加日志简则）

**Interfaces:**
- Consumes: 无（纯文档）
- Produces: `docs/agents/rules.md`「日志约定」完整规范（分层前缀表 / k=v 规则 / 五级语义 / 示例），供 Task 2 helper 与后续所有日志写作遵循

- [ ] **Step 1: 扩写 rules.md 日志约定章节**

替换 `docs/agents/rules.md` 第 34-37 行的「日志约定」为完整规范：

```markdown
## 日志约定

### 事件消息格式

- **语言**：事件消息一律英文小写 k=v（`key=value` 空格分隔）；中文仅限展示给最终用户的文案（集中在 `SSEInteractionTexts`），不得进入日志 message
- **分层前缀**：日志行以 `[层名]` 开头标识事件归属，同一类事件全系统只有一个统一前缀与措辞

| 前缀 | 归属 |
|---|---|
| `[retrieval]` | 检索层（rag_tools / web_tools / retrieval.py） |
| `[verify]` | 验证节点（verify 包） |
| `[agent]` | agent 主循环（agent_node / workflow） |
| `[session]` | 会话管理（chat manager / persistence） |
| `[db]` | DB 层（repo / engine） |
| `[llm]` | LLM 调用层 |

示例：`[retrieval] search start query="腾讯2024年报" kb_id=k1 top_k=8`

### 级别语义

- `debug` — 诊断细节，默认关闭（检索中间态、token 流）
- `info` — 正常流程里程碑（请求进出、agent 迭代、检索/联网完成）
- `warning` — 降级/可恢复异常（fallback 生效、重试、超时）；**禁止用于"正常但少见"的执行分支**
- `error` — 单点失败已处理（不阻断）
- `exception` — 透传型异常（带完整 traceback + raise）

与异常三模式联动：**降级型 / 拦截型** → `logger.warning`；**透传型 / 兜底** → `logger.exception`。

### trace_id

trace_id 由 `src/core/logging.py` 的 patcher 自动注入日志行第三段，业务代码不手动写入 message。

### 检索行为信号日志

前缀 `retrieval_signal:`，信号类型：`reretrieve` / `to_web` / `abstain_after_retrieve` / `unsupported` / `cited` / `empty_result`。格式：`retrieval_signal: signal={} query="{}" iteration={} kb_id={} result_count={} ...`。经 `src/core/logging.py` 的 `retrieval_signal()` helper 统一输出（query 截断 40），不在埋点处手拼。
```

- [ ] **Step 2: CLAUDE.md 加日志简则**

在 `CLAUDE.md` 的「TraceID」段（约 79-81 行）之后追加：

```markdown
## 日志简则

- 事件消息英文 k=v + `[层名]` 前缀；中文仅限用户可见文案（SSEInteractionTexts）
- 分级：debug 诊断 / info 里程碑 / warning 降级可恢复 / error 单点 / exception 透传
- 检索行为信号用 `retrieval_signal:` helper，不手拼
- 完整规范见 docs/agents/rules.md「日志约定」
```

- [ ] **Step 3: 校验文档**

Run: `grep -c "分层前缀\|检索行为信号\|retrieval_signal" docs/agents/rules.md CLAUDE.md`
Expected: ≥4（两文件均含新增关键词）

- [ ] **Step 4: Commit**

```bash
git add docs/agents/rules.md CLAUDE.md
git commit -m "docs: 日志规范定稿（分层前缀/英文k=v/五级语义/检索行为信号）"
```

---

### Task 1b: glossary 术语同步 + 存量日志盘点（change 1.3 / 1.4）

**Files:**
- Modify: `docs/agents/glossary.md`（"响应与追踪"分区补日志术语）
- Create: `docs/openspec/changes/logging-convention-migration/migration-inventory.md`（存量迁移清单，随 change 归档）

**Interfaces:**
- Consumes: Task 1 的规范文档（前缀表 / 级别语义 / retrieval_signal 格式）
- Produces:
  - glossary 新增术语：`retrieval_signal` / `[层名]` 分层前缀 / 五级级别语义
  - `migration-inventory.md`：239 处 logger 调用按模块分组的迁移清单（文件 → 目标前缀 → 是否中文），作为 change tasks 3.2-3.6 后续批次依据

- [ ] **Step 1: glossary.md 补日志规范术语**

`docs/agents/glossary.md` "响应与追踪" 分区（在 SSE 事件流 bullet 后）追加：

```markdown
## 响应与追踪

- **响应信封**：统一响应包装 `{"code", "message", "data"}`，仅由 `ResponseEnvelopeMiddleware` 产生；业务层只 `raise` 异常，不 `return JSONResponse`
- **SSE 事件流**：聊天流式输出的事件序列，`status → token → citation → done`

## 日志规范

- **分层前缀**：日志 message 以 `[层名]` 开头标识事件归属，层名 ∈ `{retrieval, verify, agent, session, db, llm}`；同一类事件全系统只有一个统一前缀
- **retrieval_signal**：检索行为信号日志前缀，类型 ∈ `{reretrieve, to_web, abstain_after_retrieve, unsupported, cited, empty_result}`；格式 `retrieval_signal: signal=... query="..." iteration=... kb_id=...`
- **五级级别语义**：`debug`（诊断，默认关）/ `info`（里程碑）/ `warning`（降级可恢复）/ `error`（单点已处理）/ `exception`（透传带 traceback）
```

**说明**：日志规范术语集中一个"日志规范"分区，不散进 RAG 流水线等分区——日志是横切关注点，独立分区便于检索。

- [ ] **Step 2: 盘点存量 logger 调用，生成迁移清单**

执行盘点命令（全量 src 下 logger 调用按文件分组统计）：

```bash
cd /mnt/d/code/demo/AIAgent/corporate_rag
# 全部 logger 调用总数（基线应为 239 上下，含 warning/error/info/debug/exception）
grep -rn "logger\.\(debug\|info\|warning\|error\|exception\)(" src/ --include=*.py \
  | grep -v __pycache__ | wc -l
# 按文件分组输出（文件名 行数）
grep -rn "logger\.\(debug\|info\|warning\|error\|exception\)(" src/ --include=*.py \
  | grep -v __pycache__ \
  | sed 's|\(.*\):[0-9]*:.*|\1|' | sort | uniq -c | sort -rn
```

- [ ] **Step 3: 写 migration-inventory.md 清单**

Create `docs/openspec/changes/logging-convention-migration/migration-inventory.md`，结构：

```markdown
# 存量日志迁移清单

生成日期：2026-09-03（盘点基线：<上面 wc 输出数> 处 logger 调用）
目标规范：docs/agents/rules.md「日志约定」（分层前缀 / 英文 k=v / 五级语义）

| 文件 | logger 数 | 目标前缀 | 含中文日志? | 归属批次 |
|------|----------|---------|------------|---------|
| src/agents/tools/rag_tools.py | <n> | [retrieval] | 否 | 3.1（已迁） |
| src/agents/tools/web_tools.py | <n> | [retrieval] | 否 | 3.1（已迁） |
| src/rag/retrieval.py | <n> | [retrieval] | 否 | 3.1（已迁） |
| src/agents/graph/verify_node.py | <n> | [verify] | 否 | 3.2 |
| src/agents/graph/agent_node.py | <n> | [agent] | 否 | 3.3 |
| src/agents/graph/workflow.py | <n> | [agent] | 否 | 3.3 |
| src/chat/manager.py | <n> | [session] | 否 | 3.4 |
| src/chat/persistence.py | <n> | [session] | 否 | 3.4 |
| src/infra/db/** | <n> | [db] | 否 | 3.4 |
| src/cli/* | <n> | [cli] | **是** | 3.5 |
| src/services/agent_service.py | <n> | [agent]/[session] 混合 | 否 | 3.3/3.4 |
| ...（按 Step 2 实际文件补齐）| | | | |

## 中文日志清单（3.5 重点）

| 文件 | 中文日志原文 | 迁移后英文 k=v |
|------|-------------|---------------|
| src/cli/eval_ragas.py | "加载测试集: {} 条 QA 对" | `[cli] testset loaded count={}` |
| ...（按盘点实际列出全部含中文行）| | |
```

**说明**：每文件目标前缀按 Task 1 前缀表归属（rag_tools/web_tools/retrieval → retrieval；verify_node → verify；agent_node/workflow → agent；chat → session；infra db → db；cli 无前缀表条目归 `[cli]`）；agent_service 是编排层横切多域，按具体日志行就近归类（图节点相关 [agent]、会话任务 [session]、SSE [session]）。含中文行的逐个列出迁移后文案。

- [ ] **Step 4: Commit**

```bash
git add docs/agents/glossary.md docs/openspec/changes/logging-convention-migration/migration-inventory.md
git commit -m "docs: glossary 补日志术语 + 存量 239 处盘点生成迁移清单"
```

---

### Task 2: 统一日志 helper（log_event + retrieval_signal）

**Files:**
- Modify: `src/core/logging.py`
- Test: `tests/core/test_logging_helpers.py`（新建）

**Interfaces:**
- Consumes: Task 1 的规范（前缀表 / k=v 规则）
- Produces:
  - `def log_event(prefix: str, event: str, **fields: object) -> None` — 分层前缀事件日志
  - `def retrieval_signal(signal: str, query: str, iteration: int, **fields: object) -> None` — 行为信号日志，query 截断 40
  - 两者内部均读当前 module logger（`loguru.logger`），不写 trace_id

- [ ] **Step 1: 写失败测试**

Create `tests/core/test_logging_helpers.py`:

```python
"""日志 helper 单测 — 格式与 query 截断。"""

from unittest.mock import patch

from src.core.logging import log_event, retrieval_signal


@patch("src.core.logging.logger")
def test_log_event_emits_prefixed_kv(mock_logger):
    log_event("retrieval", "search start", query="q1", kb_id="k1")
    mock_logger.info.assert_called_once()
    msg = mock_logger.info.call_args[0][0]
    assert msg.startswith("[retrieval] search start")
    assert "query=q1" in msg
    assert "kb_id=k1" in msg


@patch("src.core.logging.logger")
def test_retrieval_signal_truncates_query(mock_logger):
    long_query = "长" * 100
    retrieval_signal("to_web", long_query, 3, kb_id="k1")
    mock_logger.info.assert_called_once()
    msg = mock_logger.info.call_args[0][0]
    assert msg.startswith("retrieval_signal: signal=to_web")
    assert "iteration=3" in msg
    # query 被截断为 40 字符（无论中英文，按字符截断）
    assert len(msg.split("query=")[1].split(" iteration")[0]) <= 40


@patch("src.core.logging.logger")
def test_retrieval_signal_extra_fields(mock_logger):
    retrieval_signal("cited", "腾讯营收", 1, kb_id="k1", citation_count=3)
    mock_logger.info.assert_called_once()
    msg = mock_logger.info.call_args[0][0]
    assert "citation_count=3" in msg
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/core/test_logging_helpers.py -v`
Expected: FAIL with `ImportError: cannot import name 'log_event'`（函数未定义）

- [ ] **Step 3: 实现 helper**

在 `src/core/logging.py` 末尾追加：

```python
# ==== 统一事件日志 helper ====

# 分层前缀允许值（与 rules.md「日志约定」前缀表一致）
_LOG_PREFIXES = {"retrieval", "verify", "agent", "session", "db", "llm"}

# 行为信号类型（与 rules.md「检索行为信号日志」一致）
_RETRIEVAL_SIGNALS = {
    "reretrieve",
    "to_web",
    "abstain_after_retrieve",
    "unsupported",
    "cited",
    "empty_result",
}

# query/搜索词截断长度（与 rules.md 约定一致）
_LOG_QUERY_TRUNCATE = 40


def _truncate(value: str, limit: int = _LOG_QUERY_TRUNCATE) -> str:
    """按字符截断长字段，超出加省略号。"""
    if len(value) <= limit:
        return value
    return value[:limit] + "…"


def log_event(prefix: str, event: str, **fields: object) -> None:
    """输出带分层前缀的英文 k=v 事件日志。

    规范（rules.md「日志约定」）：
      - prefix 必须是 _LOG_PREFIXES 内层名
      - message = [层名] 事件 + 各字段 k=v
      - trace_id 由 logging patcher 注入，不在此写入

    Args:
        prefix: 层名前缀（retrieval/verify/agent/session/db/llm）
        event: 事件名（如 "search start" / "rerank done"）
        fields: k=v 字段（值会被 str() 化后拼入）
    """
    if prefix not in _LOG_PREFIXES:
        logger.warning("log_event unknown prefix={}", prefix)
        prefix = "core"
    kv = " ".join(f"{k}={v}" for k, v in fields.items())
    message = f"[{prefix}] {event}"
    if kv:
        message += f" {kv}"
    logger.info(message)


def retrieval_signal(
    signal: str, query: str, iteration: int, **fields: object
) -> None:
    """输出检索行为信号日志（P1 检索质量诊断地基）。

    规范（rules.md「检索行为信号日志」）：
      - 前缀 retrieval_signal:
      - signal 必须是 _RETRIEVAL_SIGNALS 内类型
      - query 截断 40 字符
      - trace_id 由 logging patcher 注入，不在此写入

    Args:
        signal: 行为信号类型（reretrieve/to_web/abstain_after_retrieve/unsupported/cited/empty_result）
        query: 用户查询文本（自动截断）
        iteration: agent 迭代序号
        fields: 附加字段（kb_id/result_count/reason 等）
    """
    if signal not in _RETRIEVAL_SIGNALS:
        logger.warning("retrieval_signal unknown signal={}", signal)
    kv = " ".join(f"{k}={v}" for k, v in fields.items())
    message = (
        f"retrieval_signal: signal={signal} query=\"{_truncate(query)}\" "
        f"iteration={iteration}"
    )
    if kv:
        message += f" {kv}"
    logger.info(message)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/core/test_logging_helpers.py -v`
Expected: 3 passed

- [ ] **Step 5: 质量门禁**

Run: `ruff check src/core/logging.py tests/core/test_logging_helpers.py && pyright src/core/logging.py`
Expected: 无 error

- [ ] **Step 6: Commit**

```bash
git add src/core/logging.py tests/core/test_logging_helpers.py
git commit -m "feat: 统一日志 helper（log_event 分层前缀 + retrieval_signal 行为信号）"
```

---

### Task 3: 试点迁移 `[retrieval]` 层（rag_tools / web_tools / retrieval.py）

**Files:**
- Modify: `src/rag/retrieval.py`
- Modify: `src/agents/tools/rag_tools.py`
- Modify: `src/agents/tools/web_tools.py`

**Interfaces:**
- Consumes: Task 2 的 `log_event(prefix, event, **fields)`；既有 `current_request_ctx` / `settings`
- Produces: `[retrieval]` 层日志统一到分层前缀格式（验证规范可执行，作为后续批次的迁移样板）

- [ ] **Step 1: 迁移 retrieval.py**

将 `src/rag/retrieval.py` 的检索日志改为 `log_event`（保留 warning/异常语义，message 结构化为 k=v）：

修改点对照（改后代码）：

```python
from src.core.logging import log_event

# search() 内（约 70-93 行）——原多行 logger.info 替换为：
async def search(query, kb_id, vector_store, bm25=None):
    if HYBRID_SEARCH_ENABLED and bm25 and kb_id:
        dense_t = asyncio.to_thread(
            vector_store.similarity_search, kb_id, query, TOP_K_RETRIEVAL
        )
        bm25_t = asyncio.to_thread(bm25.search, kb_id, query, TOP_K_RETRIEVAL)
        d, b = await asyncio.gather(dense_t, bm25_t)
        results = rrf_fusion(d or [], b or [])
        log_event("retrieval", "hybrid done", kb_id=kb_id, query_len=len(query), result_count=len(results))
        results = _dedup_by_doc_id(results)
        return results
    # ...（dense / search_all 分支）每个分支收尾统一：
    log_event("retrieval", "search done", kb_id=kb_id or "all", query_len=len(query), result_count=len(results) if results else 0)
    results = _dedup_by_doc_id(results or [])
    return results

# rerank_results() 内（约 131-152 行）——原 logger.info/warning 保留级别，改结构化为：
    if not results:
        log_event("retrieval", "rerank skip", reason="empty_input")
        return []
    # 成功路径：
    log_event("retrieval", "rerank done", doc_count=len(results), query_len=len(query))
    # 失败降级（原 logger.warning）：
    # with_retry 抛错 → 保留 logger.warning 但加 [retrieval] 前缀：
    logger.warning("[retrieval] rerank failed after {} attempts query={}: {}", RETRY_MAX_ATTEMPTS, query[:40], e)
```

注意：`_dedup_by_doc_id` 前的 `[DIAG]` 调试日志（131 行）删除或降级 debug——本批删除（正常路径不该有 DIAG 噪音）。

- [ ] **Step 2: 迁移 rag_tools.py**

`src/agents/tools/rag_tools.py` 的检索工具日志改 `log_event`（约 176-200 行）：

```python
from src.core.logging import log_event

# retrieve_kb 内（176-182 行 原 "tool=retrieve_kb..."）替换为：
        log_event("retrieval", "retrieve_kb done", iteration=iteration, query=query[:40], result_count=len(contexts), latency_ms=f"{(time.monotonic() - start) * 1000:.0f}")

# rerank 超时降级（141-147 行 原 logger.warning）替换为：
        logger.warning("[retrieval] rerank timeout after {}s query={}", RERANK_TIMEOUT, query[:40])

# judge 行（195-200 行 原 "judge: query=... stage=retrieve..."）替换为 retrieval_signal 调用（cited 基线暂不在此，format_node 统一打）：
# 本处移除 judge 自由文本，实际行为信号由 Change 2（retrieval-quality-signals）埋点补全
```

- [ ] **Step 3: 迁移 web_tools.py**

`src/agents/tools/web_tools.py` 的 search_web 日志改 `log_event`（60-146 行四处）：

```python
from src.core.logging import log_event

# 60 行 info（limit reached）→ 保留 warning 语义但结构化：
# 原 "tool=search_web limit reached..." → 属"达限"正常护栏，按规范归 debug（非降级非少见）：
logger.debug("[retrieval] search_web limit reached session_id={} queries={}", ctx.session_id, queries)
# 87 行 warning（部分 query 失败）：
logger.warning("[retrieval] search_web partial_failed session_id={} failed={}/{}", ctx.session_id, failed_count, len(results_list))
# 94 行 info（全失败）→ warning（降级）：
logger.warning("[retrieval] search_web all_failed session_id={} queries={}", ctx.session_id, queries[:3])
# 140-146 行 原 "judge: queries=... stage=web_confirm..." → 移除自由文本 judge，改 log_event：
        log_event("retrieval", "search_web done", session_id=ctx.session_id, query_count=len(queries), result_count=len(blocks), latency_ms=f"{(time.monotonic() - start) * 1000:.0f}")
```

- [ ] **Step 4: 运行全量测试**

Run: `pytest tests/agents/tools/test_rag_tools.py tests/agents/tools/test_web_tools.py tests/rag/test_retrieval.py -v`
Expected: 全过（若测试断言旧日志文本/mock logger，更新断言为不依赖具体 message 或匹配新格式）

- [ ] **Step 5: 抽查同层可聚合**

Run: `grep -rn "\[retrieval\]" src/agents/tools/rag_tools.py src/agents/tools/web_tools.py src/rag/retrieval.py | wc -l`
Expected: ≥5（三文件均出现 `[retrieval]` 前缀）

- [ ] **Step 6: 质量门禁**

Run: `pytest tests/ -v && ruff check src/agents/tools/rag_tools.py src/agents/tools/web_tools.py src/rag/retrieval.py && pyright src/agents/tools/rag_tools.py src/agents/tools/web_tools.py src/rag/retrieval.py`
Expected: 全过 / 无 error

- [ ] **Step 7: Commit**

```bash
git add src/rag/retrieval.py src/agents/tools/rag_tools.py src/agents/tools/web_tools.py
git commit -m "refactor: [retrieval] 层日志迁移到分层前缀 + k=v（试点批）"
```

---

### Task 4: 验证 + 收尾

**Files:**
- Modify: `docs/openspec/changes/logging-convention-migration/tasks.md`（勾选 1.x/2.x/3.1/4.x，标注 3.2-3.6 待后续）

**Interfaces:**
- Consumes: Task 1-3 产物
- Produces: 本 change 的试点批交付确认；存量其余层（verify/agent/session/db/cli）在 change tasks 标记为"后续批次，未在本 plan 范围"

- [ ] **Step 1: 全量回归**

Run: `pytest tests/ -v`
Expected: 全量通过

- [ ] **Step 2: 人工日志抽查**

Run: 一条真实检索 query，观察 `logs/app_{date}.log`：
Expected: `[retrieval] search done query_len=... result_count=...` 出现；无中英混行；trace_id 在第三段

- [ ] **Step 3: 勾选 tasks + 标注后续批次**

修改 `docs/openspec/changes/logging-convention-migration/tasks.md`：
- 勾选 1.1 / 1.2（规范文档，Task 1）；**勾选 1.3 / 1.4（glossary + 存量盘点，Task 1b 完成）**
- 勾选 2.1 / 2.2（helper 已实现）
- 勾选 3.1（试点批已迁）；**3.2-3.5 保持未勾，追加一行注记**："后续批次：verify/agent/session/db/cli 层存量迁移，按 migration-inventory.md 清单独立实施"
- 勾选 4.1 / 4.2（质量门禁）；4.3 依赖 retrieval-quality-signals 后验证，标注待联调

- [ ] **Step 4: Commit**

```bash
git add docs/openspec/changes/logging-convention-migration/tasks.md
git commit -m "docs: logging-convention-migration 试点批完成，后续批次标注"
```
