# logging-convention-migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 落地统一日志规范（分层前缀 + 英文 k=v + 值类型编码 + 注册表驱动 helper），补齐 trace 自包含重放（L1 replay + CLI）与会话注入，并把存量 [verify]/[agent]/[session]/[db]/[llm]/cli/入口 日志分批迁移到新体系。

**Architecture:** 事件定义集中在 `src/core/log_events.py`（`Event` 枚举 + `EventSpec` 注册表 + import 期一致性校验，收敛原 `logging.py` 的两个手维护白名单）；`logging.py` helper 改注册表驱动（按 spec.level 路由 info/warning/error，仅 exception 直调），值编码收口到 helper；`trace_context.py` 加 `current_session_id`、`_LOG_FORMAT` 加 session 段实现全行会话注入；`retrieve_kb` 落 replay 事件行，`src/cli/replay_trace.py` 段位无关解析 + 当前配置重放 + drift 对照。规范文档迁至 `docs/agents/logging-rules.md`（rules.md 留指针）。存量迁移按 migration-inventory.md 分批 3.2→3.5 顺序执行。

**Tech Stack:** Python 3.11+ / Loguru / contextvars / pytest / ruff / pyright

## Global Constraints

- 事件消息一律英文小写 k=v；中文仅限用户可见文案（`SSEInteractionTexts`），不得进日志 message
- 前缀主表：`[retrieval]` / `[verify]` / `[agent]` / `[session]` / `[db]` / `[llm]` / `[cli]` / `[app]`，开放登记制（新前缀先登记 `LOG_PREFIXES` 与 logging-rules.md 主表）；`retrieval_signal:` 为保留前缀（P1 Change 2 契约，检索聚合需两条 grep），不改
- 事件名英文小写空格分隔 ≤2~3 词，键 snake_case；禁类名/函数名/工具名（`retrieve_kb`/`search_web` 下划线名）作事件词
- 值类型编码（helper 唯一实现）：int/bool 裸写；字符串 token 安全（`^[A-Za-z0-9_./:@-]+$`）裸写，否则 `json.dumps(value, ensure_ascii=False)` 引号+转义；数组/容器紧凑 JSON（`json.dumps(sep=(",",":"))`）；时长整数毫秒；query/搜索词完整不截断
- helper 按 `EventSpec.level` 路由级别（info/warning/error），调用点不传前缀与级别；**exception 日志保持 `logger.xxx` 直调**（规范化文本，不建 EventSpec）
- session_id 由框架注入 `_LOG_FORMAT` 固定段，埋点不手写 `session_id=`
- `_LOG_FORMAT` = `"{time:YYYY-MM-DD HH:mm:ss.SSS} | {level:<7} | {extra[trace_id]:36} | {extra[session_id]:36} | {name}:{function}:{line} - {message}"`
- 单文件 < 400 行红线；本 change 不加新依赖
- 每批质量门禁：`pytest tests/ -v` 全过；`ruff check .` 无错误；`pyright src/` 不新增 error；`openspec validate logging-convention-migration` 通过

---

## Task 1: 规范归属迁移（openspec tasks 5.1）

**Files:**
- Create: `docs/agents/logging-rules.md`
- Modify: `docs/agents/rules.md`（「日志约定」整节替换为一行指针）
- Modify: `CLAUDE.md`（文档组织表登记 logging-rules.md；日志简则指针指向 logging-rules.md）
- Modify: `docs/agents/glossary.md`（日志术语补 token 字符集 / EventSpec / drift 对照）

**Interfaces:**
- Consumes: 无（纯文档）
- Produces: `docs/agents/logging-rules.md`（规范唯一归属文档），后续所有 Task 的事件命名/编码/前缀登记遵循它

- [ ] **Step 1: 从 rules.md 提取「日志约定」创建 logging-rules.md**

把 `docs/agents/rules.md` 第 34-68 行「日志约定」整节内容迁入新建文件，并按已定稿规范改写为以下结构（正文必须包含这些小节）：

```markdown
# 日志格式规范（唯一归属文档）

## 行模板
`[prefix] 事件名 key=value ...`；`retrieval_signal:` 行保留（见「已知例外」）。
示例：`[retrieval] search done kb_id=k1 query_len=12 result_count=8`

## 前缀主表（开放登记制）
| 前缀 | 归属 |
|---|---|
| `[retrieval]` | 检索层（rag_tools / web_tools / retrieval.py） |
| `[verify]` | 验证节点（verify 包） |
| `[agent]` | agent 主循环（agent_node / workflow） |
| `[session]` | 会话管理（chat manager / persistence） |
| `[db]` | DB 层（repo / engine） |
| `[llm]` | LLM 调用层 |
| `[cli]` | 离线工具（cli/） |
| `[app]` | 应用边界（main.py 生命周期 + 全局异常兜底） |

新语义面前缀：先登记 `src/core/log_events.py` 的 `LOG_PREFIXES` + 本表加一行后启用，不预建。

## 事件命名
英文小写、空格分隔 ≤2~3 词（`search done` / `rerank skip`）；键 snake_case；禁类名/函数名/工具名下划线名作事件词。

## 值类型编码（helper 唯一实现）
- int/bool 裸写；时长整数毫秒
- 字符串 token 安全（`^[A-Za-z0-9_./:@-]+$`）裸写，否则双引号 + JSON 转义
- 数组/容器：紧凑 JSON 文本（无空格）
- query/搜索词完整记录、不按固定长度截断
- 附加字段（含 retrieval_signal）同走此编码

## 级别语义
- debug — 诊断细节默认关；info — 正常里程碑；warning — 降级可恢复（禁"正常但少见"）；
  error — 单点失败已处理；exception — 透传带 traceback
- 事件级别由 EventSpec.level 登记，helper 路由；exception 直调不建 spec

## trace_id / session_id
由 `src/core/logging.py` patcher 自动注入日志行第 3/4 段，业务不手写。

## 事件全集
以 `src/core/log_events.py` 的 `Event` 枚举 + `EVENT_SPECS` 为准，本文件不抄录（防双维护）。

## 已知例外
- `retrieval_signal:` 为 P1 Change 2 既有契约保留前缀，检索域聚合需
  `[retrieval]` + `retrieval_signal:` 两条 grep 模式；待前缀体系重构时统一
```

- [ ] **Step 2: rules.md 日志约定替换为指针**

将 `docs/agents/rules.md` 的「日志约定」小节（原 34-68 行，含事件消息格式/级别语义/trace_id/检索行为信号四节）整体替换为：

```markdown
## 日志约定

完整规范见 docs/agents/logging-rules.md（分层前缀主表 / 事件命名 / 值类型编码 / 级别语义 / 已知例外）。
```

- [ ] **Step 3: CLAUDE.md 文档组织表登记 + 日志简则改指针**

`CLAUDE.md`「文档组织」表在 rules.md 行下新增一行：

```markdown
| docs/agents/logging-rules.md | 日志格式唯一归属：行模板 / 前缀主表(开放登记制) / 事件命名 / 值类型编码(token 字符集) / 级别语义 / 已知例外(retrieval_signal) | 写任何日志、登记事件/前缀前 |
```

并把 `CLAUDE.md`「日志简则」段末行"完整规范见 docs/agents/rules.md「日志约定」"改为"完整规范见 docs/agents/logging-rules.md"。

- [ ] **Step 4: glossary.md 补术语**

`docs/agents/glossary.md` 日志相关术语（若有"响应与追踪"分区则追加到该区，否则新增「日志规范」分区）追加：

```markdown
- EventSpec 注册表：src/core/log_events.py 中 name/prefix/level/fields 的事件描述，helper 渲染/路由级别的事实源；Event 枚举与注册表 import 期一致性校验
- token 安全字符集：`^[A-Za-z0-9_./:@-]+$`，命中则日志字段裸写，否则引号 + JSON 转义
- replay drift 对照：replay_trace CLI 用当前配置重放，事件行记录的"当时参数"并排对比并标注差异
```

- [ ] **Step 5: 自检 + 提交**

Run: `grep -c "logging-rules.md" CLAUDE.md docs/agents/rules.md` → 均 ≥1；`grep -c "开放登记制\|retrieval_signal" docs/agents/logging-rules.md` → ≥2
Commit: `git add docs/agents/logging-rules.md docs/agents/rules.md CLAUDE.md docs/agents/glossary.md && git commit -m "docs: 日志规范归属迁移至 logging-rules.md（前缀主表/编码/级别/已知例外）"`

---

## Task 2: 事件定义层 log_events.py（openspec tasks 5.2）

**Files:**
- Create: `src/core/log_events.py`
- Test: `tests/core/test_log_events.py`

**Interfaces:**
- Consumes: 无
- Produces:
  - `LOG_PREFIXES: frozenset[str]`、`SIGNAL_PREFIX = "retrieval_signal:"`
  - `class Signal(str, Enum)`（6 成员）
  - `class Event(str, Enum)`（Task 2 先登记 [retrieval] 核心事件，后续 Task 批量追加成员）
  - `class EventSpec` frozen dataclass `(name, prefix, level, fields)`
  - `EVENT_SPECS: dict[str, EventSpec]`
  - `class ReplayEvent` frozen dataclass（replay 行字段容器）
  - import 期抛 `AssertionError`：Event 成员 ↔ EVENT_SPECS 不一致、prefix 不在 `LOG_PREFIXES`、level 不在 `{info,warning,error}`

- [ ] **Step 1: 写失败测试 `tests/core/test_log_events.py`**

```python
"""事件定义层单测 — 注册表一致性 + import 校验。"""

import pytest

import src.core.log_events as le


def test_core_events_registered():
    # 每个 Event 成员必须有 EventSpec，且 spec.name 与成员值一致
    for member in le.Event:
        spec = le.EVENT_SPECS[member.value]
        assert spec.name == member.value
        assert spec.prefix in le.LOG_PREFIXES
        assert spec.level in {"info", "warning", "error"}


def test_signal_enum_covers_six():
    assert {s.value for s in le.Signal} == {
        "reretrieve", "to_web", "abstain_after_retrieve",
        "unsupported", "cited", "empty_result",
    }


def test_replay_event_fields():
    r = le.ReplayEvent(
        query="腾讯2024年报", query_len=6, kb_id="k1", iteration=2,
        top_k=8, dedup_max_per_doc=1, hybrid=True, rerank=True,
    )
    assert r.query_len == 6 and r.hybrid is True


def test_registry_import_validation_detects_drift():
    # 模拟 Event 新增成员但未登记 spec → import 校验必须抛
    from types import SimpleNamespace
    # 直接测校验函数（避免真的改类）
    with pytest.raises(AssertionError):
        le._validate_registry(events={"a"}, specs={})
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/core/test_log_events.py -v`
Expected: FAIL（ModuleNotFoundError: src.core.log_events）

- [ ] **Step 3: 实现 `src/core/log_events.py`**

```python
"""统一事件定义层 — EventSpec 注册表 / Event 枚举 / Signal 枚举 / ReplayEvent。

单一事实源：分层前缀允许集、信号类型、普通事件名与级别均在此定义并在 import 期校验。
src/core/logging.py 的 helper 只从本模块读取，禁止在别处硬编码前缀/事件字符串。

规范来源：docs/agents/logging-rules.md（唯一归属文档）。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

# 分层前缀允许集（logging-rules.md 前缀主表：6 处理层 + cli + app）
LOG_PREFIXES: frozenset[str] = frozenset(
    {"retrieval", "verify", "agent", "session", "db", "llm", "cli", "app"}
)

# 信号行保留前缀（P1 Change 2 契约，独立于 [层] 前缀的已知例外）
SIGNAL_PREFIX = "retrieval_signal:"

_ALLOWED_LEVELS = frozenset({"info", "warning", "error"})


class Signal(str, Enum):
    """检索行为信号类型（retrieval_signal 行的 signal= 值）。"""

    RERETRIEVE = "reretrieve"
    TO_WEB = "to_web"
    ABSTAIN_AFTER_RETRIEVE = "abstain_after_retrieve"
    UNSUPPORTED = "unsupported"
    CITED = "cited"
    EMPTY_RESULT = "empty_result"


class Event(str, Enum):
    """普通事件枚举，值 = 事件名（log_event 的事件 key）。

    新增事件两步：1) 在此加成员；2) 在 EVENT_SPECS 登记对应 EventSpec。
    二者不一致会在本模块 import 时抛 AssertionError（import 期一致性校验）。
    事件名遵循 logging-rules.md：英文小写空格分隔、禁用函数/工具下划线名。
    """

    # [retrieval] 核心里程碑（3.1 试点存量，Task 3 转换调用点）
    SEARCH_DONE = "search done"
    HYBRID_DONE = "hybrid done"
    RERANK_SKIP = "rerank skip"
    RERANK_DONE = "rerank done"
    RERANK_FAILED = "rerank failed"
    RERANK_TIMEOUT = "rerank timeout"
    RETRIEVE_DONE = "retrieve done"
    WEB_SEARCH_DONE = "web search done"
    RETRIEVE_REPLAY = "retrieve replay"


@dataclass(frozen=True)
class EventSpec:
    """普通事件描述（注册表条目）。

    name: 事件名（Event 成员的值，注册表 key）
    prefix: 分层前缀（须在 LOG_PREFIXES 内）
    level: 事件标准级别（helper 按此路由 logger 级别）
    fields: 字段名登记（供 review / 文档对照，不参与运行时校验）
    """

    name: str
    prefix: str
    level: str
    fields: tuple[str, ...] = ()


EVENT_SPECS: dict[str, EventSpec] = {
    Event.SEARCH_DONE.value: EventSpec(
        Event.SEARCH_DONE.value, "retrieval", "info",
        ("kb_id", "query_len", "result_count"),
    ),
    Event.HYBRID_DONE.value: EventSpec(
        Event.HYBRID_DONE.value, "retrieval", "info",
        ("kb_id", "query_len", "result_count"),
    ),
    Event.RERANK_SKIP.value: EventSpec(
        Event.RERANK_SKIP.value, "retrieval", "info", ("reason",)
    ),
    Event.RERANK_DONE.value: EventSpec(
        Event.RERANK_DONE.value, "retrieval", "info",
        ("doc_count", "query_len"),
    ),
    Event.RERANK_FAILED.value: EventSpec(
        Event.RERANK_FAILED.value, "retrieval", "warning",
        ("attempts", "query", "err"),
    ),
    Event.RERANK_TIMEOUT.value: EventSpec(
        Event.RERANK_TIMEOUT.value, "retrieval", "warning",
        ("timeout_s", "query"),
    ),
    Event.RETRIEVE_DONE.value: EventSpec(
        Event.RETRIEVE_DONE.value, "retrieval", "info",
        ("iteration", "query", "result_count", "latency_ms"),
    ),
    Event.WEB_SEARCH_DONE.value: EventSpec(
        Event.WEB_SEARCH_DONE.value, "retrieval", "info",
        ("query_count", "result_count", "latency_ms"),
    ),
    Event.RETRIEVE_REPLAY.value: EventSpec(
        Event.RETRIEVE_REPLAY.value, "retrieval", "info",
        (
            "query", "query_len", "kb_id", "iteration", "top_k",
            "dedup_max_per_doc", "hybrid", "rerank",
        ),
    ),
}


@dataclass(frozen=True)
class ReplayEvent:
    """检索重放上下文（retrieve replay 事件行字段容器）。

    query: 检索词全文（重放输入）
    query_len: 检索词字符数
    kb_id: 知识库 ID（空 = 未绑定）
    iteration: agent 迭代序号
    top_k: 工具层返回上限（精排后截断）
    dedup_max_per_doc: 同文档去重上限（settings.RETRIEVAL_MAX_PER_DOC）
    hybrid: 是否启用混合检索
    rerank: 是否执行精排
    """

    query: str
    query_len: int
    kb_id: str
    iteration: int
    top_k: int
    dedup_max_per_doc: int | None
    hybrid: bool
    rerank: bool


def _validate_registry(events: set[str], specs: dict[str, EventSpec]) -> None:
    """校验 Event 成员集与 EVENT_SPECS 一致且元数据合法（import 期调用）。"""
    if events != set(specs):
        missing = events - set(specs)
        extra = set(specs) - events
        raise AssertionError(
            "Event 成员与 EVENT_SPECS 不一致 "
            f"(缺 spec: {sorted(missing)}; 多余 spec: {sorted(extra)})"
        )
    for name, spec in specs.items():
        if spec.name != name:
            raise AssertionError(f"EventSpec name 与 key 不一致: {spec.name} != {name}")
        if spec.prefix not in LOG_PREFIXES:
            raise AssertionError(
                f"事件 {name} 前缀 {spec.prefix!r} 未在 LOG_PREFIXES 登记"
            )
        if spec.level not in _ALLOWED_LEVELS:
            raise AssertionError(f"事件 {name} 级别非法: {spec.level}")


_validate_registry({e.value for e in Event}, EVENT_SPECS)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/core/test_log_events.py -v`
Expected: PASS（4 passed）

- [ ] **Step 5: Commit**

```bash
git add src/core/log_events.py tests/core/test_log_events.py
git commit -m "feat(core): 事件定义层 log_events.py（Event 枚举 + EventSpec 注册表 + import 一致性校验）"
```

---

## Task 3: helper 注册表驱动升级 + [retrieval] 调用点转换（openspec tasks 5.3 + 5.4）

> 本 Task 在同一 commit 内完成"helper 改造 + 全部存量调用点转换"，否则现有调用点（`log_event("retrieval", ...)`）在签名变更后立即编译失败，测试无法保持绿。

**Files:**
- Modify: `src/core/logging.py`（helper 注册表驱动 + 值编码 + 去截断；删除 `_LOG_PREFIXES`/`_RETRIEVAL_SIGNALS`/`_LOG_QUERY_TRUNCATE`/`_truncate`；`import json`/`import re` 加到模块顶部，勿放中段避免 ruff E402）
- Modify: `src/rag/retrieval.py`（log_event/warning 调用点）
- Modify: `src/agents/tools/rag_tools.py`（log_event + retrieval_signal + warning 调用点；query[:40] 清理）
- Modify: `src/agents/tools/web_tools.py`（log_event + retrieval_signal 调用点）
- Modify: `src/agents/graph/nodes.py`（retrieval_signal cited 调用点）
- Modify: `src/agents/graph/verify/node.py`（retrieval_signal unsupported 调用点）
- Modify: `src/services/agent_service.py`（retrieval_signal abstain 调用点）
- Modify: `tests/core/test_logging_helpers.py`

**Interfaces:**
- Consumes: Task 2 的 `Event` / `Signal` / `EVENT_SPECS` / `SIGNAL_PREFIX`
- Produces:
  - `log_event(event: Event, **fields) -> None`：按 `EVENT_SPECS[event.value]` 渲染 `[prefix] name k=v...`，`logger.log(spec.level, message)`
  - `retrieval_signal(signal: Signal, query: str, iteration: int, **fields) -> None`：输出 `retrieval_signal: signal=<val> query=<json.dumps(query)> iteration=N ...`，`logger.info(message)`
  - `encode_value(value) -> str`：值类型编码（供 round-trip 解析测试共用规则）
  - `log_event` 对 `query` 等含空格/中文的字段自动引号 + JSON 转义（不再 `[:40]`）

- [ ] **Step 1: 改写 `src/core/logging.py` 统一事件 helper 段**

先在文件顶部 import 区（`import logging`/`import os` 附近）补 `import json` 与 `import re`（勿放模块中段，避免 ruff E402），再将第 155 行（`# ==== 统一事件日志 helper ====`）至文件末尾整段替换为：

```python
# ==== 统一事件日志 helper（注册表驱动） ====

from src.core.log_events import EVENT_SPECS, SIGNAL_PREFIX, Event, Signal

# token 安全字符集：命中则裸写，否则引号 + JSON 转义（logging-rules.md）
_TOKEN_SAFE = re.compile(r"^[A-Za-z0-9_./:@-]+$")


def encode_value(value: object) -> str:
    """按值类型编码日志字段文本（helper 唯一实现，`需要才引`）。

    int/bool 裸写；token 安全字符串裸写；其余字符串 `json.dumps`
    （引号 + JSON 转义）；数组/容器紧凑 JSON（无空格）。query 等长文本
    由调用方完整传入，本函数不截断。
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        if _TOKEN_SAFE.fullmatch(value):
            return value
        return json.dumps(value, ensure_ascii=False)
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def log_event(event: Event, **fields: object) -> None:
    """按注册表驱动输出分层前缀英文 k=v 事件日志。

    事件 key = Event 枚举成员（Task 2 import 校验保证必然已登记），
    前缀/事件词/级别取 EVENT_SPECS，调用点不传前缀与级别。

    首行 Event(event) 收口：裸字符串合法值规范化为枚举成员；非法值抛
    ValueError（堵住绕过枚举的自由文本路径，无任何兜底落盘）。

    Args:
        event: 事件枚举成员（值 = 事件名）
        fields: k=v 字段，经 encode_value 值类型编码
    """
    event = Event(event)  # 收口：裸字符串合法值规范化，非法值抛 ValueError
    spec = EVENT_SPECS[event.value]
    parts = [f"{k}={encode_value(v)}" for k, v in fields.items()]
    message = f"[{spec.prefix}] {spec.name}"
    if parts:
        message += " " + " ".join(parts)
    logger.log(spec.level, message)


def retrieval_signal(signal: Signal, query: str, iteration: int, **fields: object) -> None:
    """输出检索行为信号日志（P1 Change 2 保留前缀，info 级）。

    Args:
        signal: Signal 枚举成员（reretrieve/to_web/...）
        query: 用户查询文本（完整记录，不截断，JSON 转义保持行可解析）
        iteration: agent 迭代序号
        fields: 附加字段（kb_id/result_count/reason 等），与 log_event 同走值编码
    """
    parts = [f"{k}={encode_value(v)}" for k, v in fields.items()]
    message = (
        f"{SIGNAL_PREFIX} signal={signal.value} "
        f"query={json.dumps(query, ensure_ascii=False)} iteration={iteration}"
    )
    if parts:
        message += " " + " ".join(parts)
    logger.info(message)
```

同时删除文件顶部 `import json` 与 `import re` 重复（若已 import 则复用，勿重复 import）。检查文件内是否还有其他 `json`/`re` 使用，避免删除后 NameError。

- [ ] **Step 2: 改写 helper 单测 `tests/core/test_logging_helpers.py`**

```python
"""日志 helper 单测 — 注册表驱动 + 值类型编码 + round-trip。"""

import json
from unittest.mock import patch

from src.core.log_events import Event, Signal
from src.core.logging import encode_value, log_event, retrieval_signal


@patch("src.core.logging.logger")
def test_log_event_routes_level_from_spec(mock_logger):
    log_event(Event.SEARCH_DONE, kb_id="k1", query_len=12, result_count=8)
    mock_logger.log.assert_called_once()
    level, msg = mock_logger.log.call_args[0][0], mock_logger.log.call_args[0][1]
    assert level == "info"
    assert msg.startswith("[retrieval] search done")
    assert "kb_id=k1" in msg
    assert "query_len=12" in msg


@patch("src.core.logging.logger")
def test_log_event_warning_level(mock_logger):
    log_event(Event.RERANK_TIMEOUT, timeout_s=3, query="腾讯2024年报")
    level, msg = mock_logger.log.call_args[0][0], mock_logger.log.call_args[0][1]
    assert level == "warning"
    assert f'query="腾讯2024年报"' in msg


@patch("src.core.logging.logger")
def test_log_event_coerces_raw_string(mock_logger):
    # 裸字符串合法值 → 规范化枚举；非法值 → ValueError（收口，无自由文本落盘）
    log_event("search done", kb_id="k1", query_len=1, result_count=1)
    assert mock_logger.log.call_args[0][0] == "info"
    import pytest

    with pytest.raises(ValueError):
        log_event("search don", kb_id="k1", query_len=1, result_count=1)


@patch("src.core.logging.logger")
def test_retrieval_signal_full_query_no_truncation(mock_logger):
    long_query = "长" * 100
    retrieval_signal(Signal.TO_WEB, long_query, 3, kb_id="k1", reason="query too vague")
    mock_logger.info.assert_called_once()
    msg = mock_logger.info.call_args[0][0]
    assert msg.startswith("retrieval_signal: signal=to_web")
    assert "iteration=3" in msg
    # query 完整记录（不再截断 40）
    assert f'query="{long_query}"' in msg
    # 附加字段含空格 → 引号 + JSON 转义
    assert 'reason="query too vague"' in msg


def test_encode_value_charset_roundtrip():
    # round-trip：编码输出能被解析回原值（与 replay CLI 共用规则）
    raw = "腾讯 2024 年报 \"Q&A\"\\n第二行"
    encoded = encode_value(raw)
    assert json.loads(encoded) == raw
    assert encode_value("kb1") == "kb1"
    assert encode_value(8) == "8"
    assert encode_value(True) == "true"
    assert encode_value(["2023", "2025"]) == '["2023","2025"]'
```

- [ ] **Step 3: 转换 `src/rag/retrieval.py` 调用点**

替换三处 `log_event("retrieval", ...)` 与一处 `logger.warning(...)`：

```python
# 原: log_event("retrieval", "hybrid done", kb_id=kb_id, query_len=len(query), result_count=len(results))
log_event(Event.HYBRID_DONE, kb_id=kb_id, query_len=len(query), result_count=len(results))

# 原: log_event("retrieval", "search done", kb_id=kb_id or "all", query_len=len(query), result_count=result_count)
log_event(Event.SEARCH_DONE, kb_id=kb_id or "all", query_len=len(query), result_count=result_count)

# 原: log_event("retrieval", "rerank skip", reason="empty_input")
log_event(Event.RERANK_SKIP, reason="empty_input")

# 原: logger.warning("[retrieval] rerank failed after {} attempts query={}: {}", RETRY_MAX_ATTEMPTS, query[:40], e)
#    → 注册的 warning 事件（query 完整，不再 [:40]），e 值经 encode_value
log_event(
    Event.RERANK_FAILED,
    attempts=RETRY_MAX_ATTEMPTS,
    query=query,
    err=str(e),
)

# 原: log_event("retrieval", "rerank done", doc_count=len(results), query_len=len(query))（字段保持原样，勿臆造）
log_event(
    Event.RERANK_DONE,
    doc_count=len(results),
    query_len=len(query),
)
```

import 改为 `from src.core.log_events import Event`（与既有 `log_event` import 并列）。

- [ ] **Step 4: 转换 `src/agents/tools/rag_tools.py` 调用点**

```python
# —— retrieve_kb 尾部信号与里程碑 ——
from src.core.log_events import Event, Signal
import src.core.logging as core_logging  # 既有别名保留

# 原: core_logging.retrieval_signal("empty_result", query, iteration, kb_id=kb_id, result_count=0)
core_logging.retrieval_signal(
    Signal.EMPTY_RESULT, query, iteration, kb_id=kb_id, result_count=0
)

# 原: core_logging.retrieval_signal("reretrieve", query, iteration, kb_id=kb_id, call_seq=..., result_count=len(results))
core_logging.retrieval_signal(
    Signal.RERETRIEVE,
    query,
    iteration,
    kb_id=kb_id,
    call_seq=ctx.retrieve_call_seq,
    result_count=len(results),
)

# 原: core_logging.log_event("retrieval", "retrieve_kb done", iteration=iteration, query=query[:40], result_count=len(contexts), latency_ms=...)
#    事件名去掉工具名（retrieve_kb），命名规则禁止函数/工具下划线名；query 完整
core_logging.log_event(
    Event.RETRIEVE_DONE,
    iteration=iteration,
    query=query,
    result_count=len(contexts),
    latency_ms=int((time.monotonic() - start) * 1000),
)
```

另把该文件 rerank timeout 的直调 warning 转注册事件（约 138 行附近）：

```python
# 原: logger.warning("[retrieval] rerank timeout after {}s query={}", RERANK_TIMEOUT, query[:40])
# 改为:
log_event(
    Event.RERANK_TIMEOUT,
    timeout_s=RERANK_TIMEOUT,
    query=query,
)
```

（`logger.warning` 原引用若不再使用，检查该 import 是否仍被其它行用到，避免误删。）

- [ ] **Step 5: 转换 `web_tools.py` / `nodes.py` / `verify/node.py` / `agent_service.py` 的信号调用**

`src/agents/tools/web_tools.py`（to_web 信号 + search_web done 里程碑）：

```python
# 原: core_logging.retrieval_signal("to_web", ", ".join(queries), 0, kb_id=ctx.kb_id, result_count=len(blocks), latency_ms="...")
core_logging.retrieval_signal(
    Signal.TO_WEB,
    ", ".join(queries),
    0,
    kb_id=ctx.kb_id,
    result_count=len(blocks),
    latency_ms=int((time.monotonic() - start) * 1000),
)

# 原: core_logging.log_event("retrieval", "search_web done", session_id=ctx.session_id, query_count=..., result_count=..., latency_ms="...")
#    session_id 由框架注入固定段（Task 5），此处移除手写 session_id；事件名去掉工具名
core_logging.log_event(
    Event.WEB_SEARCH_DONE,
    query_count=len(queries),
    result_count=len(blocks),
    latency_ms=int((time.monotonic() - start) * 1000),
)
```

`src/agents/graph/nodes.py`（format_node cited 信号）：

```python
# 原: core_logging.retrieval_signal("cited", query_text, 0, kb_id="", citation_count=len(citations), kind="|".join(sorted(kinds)))
core_logging.retrieval_signal(
    Signal.CITED,
    query_text,
    0,
    kb_id="",
    citation_count=len(citations),
    kind="|".join(sorted(kinds)),
)
```

`src/agents/graph/verify/node.py`（unsupported 信号）：

```python
# 原: retrieval_signal("unsupported", state.query, state._agent_iterations, kb_id=state.kb_id, unsupported_count=len(unsupported))
retrieval_signal(
    Signal.UNSUPPORTED,
    state.query,
    state._agent_iterations,
    kb_id=state.kb_id,
    unsupported_count=len(unsupported),
)
```

`src/services/agent_service.py`（abstain 信号）：

```python
# 原: retrieval_signal("abstain_after_retrieve", query, 0, kb_id=kb_id, tool_context_count=len(capture.final_contexts or []))
retrieval_signal(
    Signal.ABSTAIN_AFTER_RETRIEVE,
    query,
    0,
    kb_id=kb_id,
    tool_context_count=len(capture.final_contexts or []),
)
```

每个文件的 `from src.core.log_events import Signal`（及需要时 `Event`）加在 import 区。

- [ ] **Step 6: 全量测试 + 修复下游断言**

Run: `pytest tests/ -v`
Expected: 大部分通过。若有失败，多为断言旧事件名/截断信号的测试，按下述逐一更新：
- 旧事件名 `retrieve_kb done` → `[retrieval] retrieve done`（grep `retrieve_kb done` 于 tests/，改断言）
- 旧事件名 `search_web done` → `[retrieval] web search done`（grep `search_web done`，改断言）
- 断言 query 截断（含 `…` / `query[:40]`）的信号用例 → 改为断言完整 query
- mock `logger.info` 断言 log_event 的用例 → 改为 `mock_logger.log`

Run: `ruff check . && ruff format .`
Expected: 无 error，格式已统一

- [ ] **Step 7: Commit**

```bash
git add src/core/logging.py src/rag/retrieval.py src/agents/tools/rag_tools.py \
        src/agents/tools/web_tools.py src/agents/graph/nodes.py \
        src/agents/graph/verify/node.py src/services/agent_service.py \
        tests/core/test_logging_helpers.py
git commit -m "refactor(core): helper 注册表驱动升级（spec.level 路由 + 值编码 + 去截断）+ [retrieval] 调用点转换"
```

---

## Task 4: retrieve_kb 落 replay 事件行（openspec tasks 5.5）

**Files:**
- Modify: `src/agents/tools/rag_tools.py`（retrieve_kb 内、rerank 超时兜底分支后）
- Modify: `src/core/log_events.py`（如需新增字段，重排；Event.RETRIEVE_REPLAY 已在 Task 2 登记）

**Interfaces:**
- Consumes: Task 2 `Event.RETRIEVE_REPLAY`、Task 3 `log_event`
- Produces: 每条 `retrieve_kb`（kb_id 非空）落一行 `[retrieval] retrieve replay query="..." query_len=.. kb_id=.. iteration=.. top_k=.. dedup_max_per_doc=.. hybrid=.. rerank=..`；该行 query/kb 供 replay CLI 重放，其余参数作"当时值" drift 对照

- [ ] **Step 1: 确认 replay 字段来源可用**

`settings.RETRIEVAL_MAX_PER_DOC` 存在（logging 基线已引用）；hybrid 当前值 = `from src.rag.retrieval import HYBRID_SEARCH_ENABLED`（或 import 处别名）；rerank 在本文件恒执行（`True`）；top_k = 函数入参 `top_k`；iteration = 上文 `iteration` 变量；`query_len = len(query)`。

- [ ] **Step 2: 在 rag_tools.py 的 rerank 兜底与信号之间插入 replay 行**

定位 `retrieve_kb` 内 `if kb_id and not results:`（empty_result 信号前），插入：

```python
        # 检索重放上下文（L1）：query/kb 为重放输入，其余参数为"当时值"供 drift 对照；
        # 态 A（kb_id 空）不检索、不落 replay 行
        if kb_id:
            from src.rag.retrieval import HYBRID_SEARCH_ENABLED

            core_logging.log_event(
                Event.RETRIEVE_REPLAY,
                query=query,
                query_len=len(query),
                kb_id=kb_id,
                iteration=iteration,
                top_k=top_k,
                dedup_max_per_doc=settings.RETRIEVAL_MAX_PER_DOC,
                hybrid=HYBRID_SEARCH_ENABLED,
                rerank=True,
            )
```

> 注：`settings` 在本文件顶部已 import（`from src.config.settings import settings` 或等价别名）。若局部 import 与顶部重复，去掉局部、仅用顶部。

- [ ] **Step 3: 运行 rag_tools 相关测试**

Run: `pytest tests/agents/tools/test_rag_tools.py -v`
Expected: PASS（若失败多为测试断言事件名/信号行，按 Task 3 Step 6 规则同步更新）

- [ ] **Step 4: Commit**

```bash
git add src/agents/tools/rag_tools.py
git commit -m "feat(retrieval): retrieve_kb 落 retrieve replay 上下文事件行（query/kb 重放输入 + 参数 drift 对照）"
```

---

## Task 5: session_id 会话注入固定段（openspec tasks 5.7）

**Files:**
- Modify: `src/infra/llm/trace_context.py`（新增 `current_session_id`）
- Modify: `src/core/logging.py`（`_LOG_FORMAT` 加段；`logger.configure` extra 补 session_id；patcher 写 extra）
- Modify: `src/api/chat.py`（`_run_with_finalize` 入口 set/reset `current_session_id`）
- Test: `tests/core/test_session_injection.py`

**Interfaces:**
- Consumes: `RequestContext.session_id`（agent_service.stream_chat 已存，经 ctx 传入 `_run_with_finalize`）
- Produces: `current_session_id: ContextVar[str]`；日志行第 4 段为 session_id；chat 生成任务期间全行携带会话

- [ ] **Step 1: 写失败测试 `tests/core/test_session_injection.py`**

```python
"""会话注入单测 — current_session_id ContextVar + 日志行第 4 段。"""

from loguru import logger

from src.infra.llm.trace_context import current_session_id


def test_session_contextvar_default_empty():
    assert current_session_id.get() == ""


def test_log_line_contains_session_segment():
    from src.core import logging as core_logging

    # 装 patcher（configure 覆盖，幂等），使 sink 按 _LOG_FORMAT 渲染 extra
    core_logging._setup_trace_id_patcher()
    captured: list[str] = []
    sink_id = logger.add(lambda m: captured.append(m), format=core_logging._LOG_FORMAT)
    try:
        token = current_session_id.set("sess_123")
        try:
            logger.info("hello")
        finally:
            current_session_id.reset(token)
    finally:
        logger.remove(sink_id)
    assert captured, "sink 未捕获任何输出"
    # session_id 落在第 4 段（time | level | trace_id | session_id | ...）
    assert any("sess_123" in line for line in captured), captured
```

- [ ] **Step 2: 实现 `current_session_id` + 格式段**

`src/infra/llm/trace_context.py`（import 区后追加）：

```python
current_session_id: ContextVar[str] = ContextVar("current_session_id", default="")
```

`src/core/logging.py`：

```python
_LOG_FORMAT = "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level:<7} | {extra[trace_id]:36} | {extra[session_id]:36} | {name}:{function}:{line} - {message}"
```

`_setup_trace_id_patcher`（logging.py:100-103）改为同时注入 session_id：

```python
    def _patcher(record):
        from src.infra.llm.trace_context import (
            current_session_id as _session_var,
            current_trace_id as _trace_var,
        )

        record["extra"]["trace_id"] = _trace_var.get() or ""
        record["extra"]["session_id"] = _session_var.get() or ""
```

`setup_logging` 内 `logger.configure(extra=...)`（logging.py:122，`extra={"trace_id": ""}`）与 `_setup_trace_id_patcher` 内 `logger.configure(extra=..., patcher=...)`（logging.py:103）**两处 extra 字典都必须补 `"session_id": ""` 默认值**——否则 CLI/非 patcher 路径（`configure_trace_id=False`）格式化 `_LOG_FORMAT` 时 `extra[session_id]` 缺失会 KeyError。`_setup_trace_id_patcher` 顶部已有对 `_trace_var` 的 CLI 自动生成逻辑，保持不动。

- [ ] **Step 3: `_run_with_finalize` 入口 set/reset**

`src/api/chat.py` `_run_with_finalize`（约 200-201 行）：

```python
    ctx_token = current_request_ctx.set(ctx)
    trace_token = current_trace_id.set(trace_id or None)
    session_token = current_session_id.set(ctx.session_id)
```

在该函数 finally 的 reset 段补 `current_session_id.reset(session_token)`（与 trace/ctx 的 reset 并列）。在文件顶部补 `from src.infra.llm.trace_context import current_session_id`。docstring 的"contextvars 不会自动传播……显式 set current_request_ctx / current_trace_id"补一句 session_id。

- [ ] **Step 4: 运行测试**

Run: `pytest tests/core/test_session_injection.py tests/agents/tools/test_rag_tools.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/infra/llm/trace_context.py src/core/logging.py src/api/chat.py tests/core/test_session_injection.py
git commit -m "feat(logging): session_id 会话注入固定格式段（current_session_id ContextVar + _LOG_FORMAT 加段）"
```

---

## Task 6: replay_trace CLI（openspec tasks 5.6，本期 L1）

**Files:**
- Create: `src/cli/replay_trace.py`
- Create: `tests/cli/test_replay_trace.py`

**Interfaces:**
- Consumes: Task 3 `encode_value`（round-trip 配套）、Task 4 replay 事件行
- Produces: `parse_log_line(line) -> dict|None` / `parse_trace_logs(log_dir, trace_id) -> list[dict]`（纯函数可单测）；`_replay_all(rows, vector_store, bm25, reranker)` + `_print_snippets(fields, contexts)`（当前配置重放 + drift 对照，store 构造沿用 eval_ragas 先例）；`main()`；模块 docstring 标注容器运行方式

- [ ] **Step 1: 写解析单测 `tests/cli/test_replay_trace.py`**

```python
"""replay_trace CLI 单测 — 日志解析（mock 检索，不发真实网络）。"""

from src.cli.replay_trace import parse_log_line, parse_trace_logs


def test_parse_log_line_extracts_replay_fields():
    line = (
        "2026-09-03 12:00:00.000 | INFO    | trace_abc                  "
        "| sess_1                     | src.agents.tools.rag_tools:200 - "
        '[retrieval] retrieve replay query="腾讯2024年报 营收" query_len=10 '
        "kb_id=k1 iteration=2 top_k=8 dedup_max_per_doc=1 hybrid=true rerank=true"
    )
    fields = parse_log_line(line)
    assert fields["query"] == "腾讯2024年报 营收"
    assert fields["query_len"] == 10
    assert fields["kb_id"] == "k1"
    assert fields["iteration"] == 2
    assert fields["dedup_max_per_doc"] == 1


def test_parse_log_line_segment_agnostic():
    # 旧 5 段格式（无 session 段）也应能解析 —— 按 trace 子串 + " - " 切分
    line = (
        "2026-09-02 12:00:00.000 | INFO    | trace_abc                  "
        "| src.agents.tools.rag_tools:200 - "
        '[retrieval] retrieve replay query="腾讯" query_len=2 kb_id=k1 iteration=1 '
        "top_k=8 dedup_max_per_doc=1 hybrid=false rerank=true"
    )
    fields = parse_log_line(line)
    assert fields["query"] == "腾讯"


def test_parse_trace_logs_filters_by_trace(tmp_path):
    other = (
        "2026-09-03 12:00:00.000 | INFO    | trace_zzz                  | "
        "src.a:1 - [retrieval] retrieve replay query=\"a\" query_len=1 kb_id=k1 "
        "iteration=1 top_k=8 dedup_max_per_doc=1 hybrid=false rerank=true"
    )
    (tmp_path / "app_2026-09-03.log").write_text(
        "2026-09-03 12:00:00.000 | INFO    | trace_abc                  | "
        "src.a:1 - [retrieval] retrieve replay query=\"腾讯\" query_len=2 kb_id=k1 "
        "iteration=1 top_k=8 dedup_max_per_doc=1 hybrid=false rerank=true\n" + other,
        encoding="utf-8",
    )
    rows = parse_trace_logs(str(tmp_path), "trace_abc")
    assert len(rows) == 1
    assert rows[0]["kb_id"] == "k1"
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/cli/test_replay_trace.py -v`
Expected: FAIL（ModuleNotFoundError: src.cli.replay_trace）

- [ ] **Step 3: 实现解析 + 主流程骨架**

```python
"""replay_trace — 按 trace 离线重放检索（本期 L1）。

用法（容器内）：docker compose exec app python -m src.cli.replay_trace --trace trace_xxx

读取全部 app_*.log（按天轮转，trace 可跨天），段位无关解析：按 trace_id 子串
过滤行、取最后一个 " - " 之后为 message（兼容 _LOG_FORMAT 加 session 段前后）。
输出语义：对当前 KB、当前配置重放（非历史快照）；事件行的 top_k/dedup/hybrid/rerank
为"当时值"，与本次实际执行参数并排对照并标注差异（drift 检测）。
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
from pathlib import Path


# k=v 切分：token 裸写 或 双引号 JSON 串（值与 helper encode_value 配套，round-trip）
_KV = re.compile(r"([A-Za-z0-9_]+)=(\"[^\"]*\"|[^ ]+)")


def parse_log_line(line: str) -> dict | None:
    """从一行日志解析 retrieve replay 字段；非 replay 行返回 None。

    段位无关：不依赖 | 分段，直接在整行找 trace 子串与 message；
    message 取最后一个 " - " 之后的内容，再按事件名定位 replay 行。
    """
    if " - [retrieval] retrieve replay " not in line:
        return None
    message = line.split(" - ", 1)[-1]  # 取 message（首个 " - " 后即为 message 头）
    if not message.startswith("[retrieval] retrieve replay "):
        return None
    body = message[len("[retrieval] retrieve replay ") :]
    fields: dict = {}
    for key, raw in _KV.findall(body):
        if raw.startswith('"'):
            fields[key] = json.loads(raw)
        elif raw == "true":
            fields[key] = True
        elif raw == "false":
            fields[key] = False
        else:
            try:
                fields[key] = int(raw)
            except ValueError:
                fields[key] = raw
    return fields


def parse_trace_logs(log_dir: str, trace_id: str) -> list[dict]:
    """扫 log_dir 下全部 app_*.log，返回该 trace 的 replay 行字段列表（按文件行序）。"""
    rows: list[dict] = []
    for path in sorted(glob.glob(os.path.join(log_dir, "app_*.log"))):
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                if trace_id not in line:
                    continue
                fields = parse_log_line(line)
                if fields:
                    rows.append(fields)
    return rows


import asyncio

from src.config import BM25_INDEX_DIR, HYBRID_SEARCH_ENABLED
from src.infra.db.vector_store import VectorStore
from src.infra.search.bm25_index import BM25Index
from src.models import get_rerank
from src.rag.retrieval import rerank_results, search


def _print_snippets(fields: dict, contexts) -> None:
    """打印一次重放的命中片段与 drift 对照。

    drift：事件行记录的"当时参数"（dedup_max_per_doc 等）与本次实际配置不同
    时标注差异——检索栈内部读模块常量，无法用事件参数覆盖（Q4/Q3），因此
    replay 只做对照诊断，不做参数实验。
    """
    print(f"  [iteration={fields.get('iteration')}] query={fields['query']!r}")
    # 事件记录值 vs 当前实际执行（dedup 等经模块常量，无法在此覆盖）
    row_dedup = fields.get("dedup_max_per_doc")
    print(
        f"  params: replay 当时 dedup={row_dedup} / 本次按当前配置执行"
        f"（差异即 drift，标注供诊断）"
    )
    for ctx in contexts[: int(fields.get("top_k", 8))]:
        snippet = (ctx.content or "").replace("\n", " ")[:80]
        print(f"    source={ctx.source} page={ctx.page} score={ctx.score:.3f} | {snippet}")


async def _replay_all(rows: list[dict], vector_store, bm25, reranker) -> None:
    """对当前 KB/配置逐行重放检索（Q4：search/rerank 内部读模块常量）。"""
    for fields in rows:
        results = await search(fields["query"], fields["kb_id"], vector_store, bm25)
        contexts = rerank_results(fields["query"], results, reranker)
        _print_snippets(fields, contexts)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="按 trace 重放检索（当前配置，drift 对照）")
    parser.add_argument("--trace", required=True, help="trace_id，如 trace_xxx")
    parser.add_argument("--log-dir", default=os.getenv("LOG_DIR", "logs"))
    args = parser.parse_args(argv)

    rows = parse_trace_logs(args.log_dir, args.trace)
    if not rows:
        print(f"trace {args.trace} 无检索重放事件（纯对话/纯联网或不存在的 trace）")
        return
    print(f"trace {args.trace}: {len(rows)} 次检索重放（对当前 KB/配置，非历史快照）")

    # store/reranker 构造沿用 cli 既有先例（eval_ragas.py 构造段）
    vector_store = VectorStore()
    bm25 = BM25Index(index_dir=BM25_INDEX_DIR) if HYBRID_SEARCH_ENABLED else None
    reranker = get_rerank()
    asyncio.run(_replay_all(rows, vector_store, bm25, reranker))


if __name__ == "__main__":
    main()
```

> 注：`settings` 需在本模块 import（`from src.config.settings import settings` 或与 BM25 常量同源的 `src.config` 导出对齐）；`VectorStore()/get_rerank()/BM25Index()` 构造与 `search`/`rerank_results` 调用签名以 eval_ragas.py 构造段为现场基准微调。`search` 为 async、`rerank_results` 为 sync，已在上面体现。Q3 决议：**无 `--max-per-doc`**，N=1 vs N>1 A/B 降级为离线实验（改 settings 重启跑整链路），replay 只做 drift 对照。

- [ ] **Step 4: 运行测试**

Run: `pytest tests/cli/test_replay_trace.py -v`
Expected: PASS（3 passed；`parse_log_line` / `parse_trace_logs` 已验证段位无关与字段还原）

- [ ] **Step 5: Commit**

```bash
git add src/cli/replay_trace.py tests/cli/test_replay_trace.py
git commit -m "feat(cli): replay_trace L1（段位无关解析 + 当前配置重放契约 + drift 对照）"
```

---

## Task 7: 迁移批 3.2 `[verify]`（openspec tasks 3.2 + 3.6）

**Files:**
- Modify: `src/agents/graph/verify/*.py`（node / regen_decision / guardrails / ask_confirm / faithfulness / checks）
- Modify: `src/core/log_events.py`（登记本批 verify 事件）
- 对照清单: `docs/openspec/changes/logging-convention-migration/migration-inventory.md` 中目标前缀 `[verify]` 行

**Interfaces:**
- Consumes: Task 3 helper（`log_event(Event.X, ...)` / `retrieval_signal`）；inventory 清单
- Produces: verify 批事件登记进 Event 枚举 + EVENT_SPECS；`[verify] judge start`（e2e"态A 不跑 judge"正面锚）；judge done 带 `unsupported_count`

**本批验收清单（执行 + review 双重检查）:**
- 事件名 ≤3 词、英文小写空格分隔；无类名（`xxx_node:`）、函数名、工具下划线名（`retrieve_kb`/`search_web`）、自由句
- 级别落在五级语义：info 里程碑 / warning 降级可恢复 / error 单点已处理；禁"正常但少见"记 warning
- 字段键 snake_case；值经 helper 编码（不手拼引号、不手写 `session_id=`）；query 完整不截断
- 事件名全局唯一：Event 枚举 name 跨前缀不重复（重复会在 import 校验抛 AssertionError）
- helper 调用不传前缀与级别；exception 保持 `logger.xxx` 直调 + 文本规范化
- grep 验收：本批文件 `logger.info/warning/error` 直调清零（仅 exception 残留）；同层事件一条 grep 可聚合；无中文混行（保留用户可见文案除外）

- [ ] **Step 1: 盘点 verify 批调用点**

Run: `grep -n "logger\.\(info\|warning\|error\|exception\)(" src/agents/graph/verify/*.py`
对照 inventory 目标前缀 `[verify]` 的文件行逐一列出。

- [ ] **Step 2: 逐调用点转换 + 登记事件**

对本批每个 logger 调用执行转换决策（唯一两轨）：
1. **info/warning/error** → 在 `Event` 枚举加成员、`EVENT_SPECS` 加对应 spec（prefix=`verify`，level 按语义：里程碑 info / 降级 warning / 失败 error），调用点改 `log_event(Event.XXX, ...)`。
2. **exception** → 保持 `logger.exception` 直调，但 message 文本改为 `[verify] 事件 英文k=v`（去掉中文/自由句；exception 不建 spec）。

命名遵循约束：`[verify] check done` / `completeness missing=[...]` / `judge start` / `judge done` / `skip` / `guide ...`；禁类名（`verify_node:`）作前缀。

关键：verify 跑 judge 前打 `log_event(Event.JUDGE_START, ...)`（无字段即可，作为"跑过 judge"正面锚），judge 返回后打 `log_event(Event.JUDGE_DONE, unsupported_count=len(unsupported), ...)`（在 `faithfulness_check` 调用点附近）。

- [ ] **Step 3: 每批回归**

Run: `pytest tests/ -v` → 全过
Run: `ruff check . && ruff format .` → 无 error
Run: `grep -n "logger\.\(info\|warning\)(" src/agents/graph/verify/*.py` → 期望除 exception 外无直调残留（本批 info/warning 全走 helper）

- [ ] **Step 4: Commit**

```bash
git add src/agents/graph/verify/ src/core/log_events.py
git commit -m "refactor(logging): [verify] 批日志迁移注册表驱动（judge start|done 正面锚 + 事件登记）"
```

---

## Task 8: 迁移批 3.3 `[agent]`（openspec tasks 3.3 + 3.6）

**Files:**
- Modify: `src/agents/graph/agent_node.py`、`src/agents/graph/workflow.py`、`src/agents/graph/nodes.py`（format 相关）+ `src/services/agent_service.py`（[agent] 归类行）
- Modify: `src/core/log_events.py`（登记 agent 批事件）
- 对照: migration-inventory.md 目标前缀 `[agent]`

**本批验收清单（执行 + review 双重检查）:**
- 事件名 ≤3 词、英文小写空格分隔；无类名（`format_node:`）、函数名、工具下划线名、自由句
- 级别落在五级语义：info 里程碑 / warning 降级可恢复（iteration limit 达上限属"达到上限的预期分支"，按语义归 warning 而非 error）/ error 单点已处理
- 字段键 snake_case；值经 helper 编码（不手拼引号、不手写 `session_id=`）；query 完整不截断
- 事件名全局唯一：Event 枚举 name 跨前缀不重复（重复会在 import 校验抛 AssertionError）
- helper 调用不传前缀与级别；exception 保持 `logger.xxx` 直调 + 文本规范化
- grep 验收：`format_node:` 类前缀残留清零；本批文件 `logger.info/warning/error` 直调清零（仅 exception 残留）；无中文混行（保留用户可见文案除外）

- [ ] **Step 1: 盘点 + 转换**

同 Task 7 规则。agent_node / workflow 迁移 `iteration done` / `iteration limit`（limit 达上限 → warning 级事件）等。**主 agent 每轮推理**在主模型调用点（`agent_node.py` 的 `model.astream` 调用前后，约 80-95 行）落 `[agent] model turn`：

```python
        # —— 主 agent 每轮推理 model turn 摘要（Task 8）——
        # 插在 astream 聚合出 result 之后、return 之前（原 agent_node.py ~91-94 行之间）
        from src.rag.stream import estimate_usage

        turn_start = time.monotonic()
        # ... 原 astream 循环聚合 result（保留现有 chunk 合并逻辑）...

        # usage：真实值取聚合后 usage_metadata；缺失用 estimate_usage 兜底并标注
        meta = getattr(result, "usage_metadata", None) or {}
        if meta.get("input_tokens") or meta.get("output_tokens"):
            usage_in = int(meta.get("input_tokens", 0))
            usage_out = int(meta.get("output_tokens", 0))
            usage_estimated = False
        else:
            est = estimate_usage(messages, getattr(result, "content", "") or "")
            usage_in, usage_out, usage_estimated = (
                est.prompt_tokens,
                est.completion_tokens,
                True,
            )
        log_event(
            Event.MODEL_TURN,
            model=model.model_name if hasattr(model, "model_name") else str(model),
            usage_in=usage_in,
            usage_out=usage_out,
            usage_estimated=usage_estimated,
            fallback=False,  # 若该节点模型经 fallback 包装则取实际降级态，否则 False
            latency_ms=int((time.monotonic() - turn_start) * 1000),
            iteration=iteration,
        )
```

> `Event.MODEL_TURN` 在 `EVENT_SPECS` 登记的 fields 含 `("model", "usage_in", "usage_out", "usage_estimated", "fallback", "latency_ms", "iteration")`。usage 估算兜底与标注（usage_estimated）为 Q4 决议：真实缺失时仿 stream.py `estimate_usage` 补齐并标 `usage_estimated=true`，避免估算值混入成本口径。

model turn 埋点位置 = agent_node 主模型调用点（[agent] 域）；judge/temporal/query_router 的 LLM 调用本期不加摘要。`nodes.py` 中 `logger.info("format_node: citations=...")`（类前缀残留）→ 转 `[agent] format done citations=..`。

- [ ] **Step 2: 登记 agent 批事件（Event 枚举 + EVENT_SPECS，含 MODEL_TURN）**

- [ ] **Step 3: 回归**

Run: `pytest tests/ -v`；`ruff check . && ruff format .`
Run: grep 抽查无 `format_node:` / 中英混行（保留用户可见文案除外）

- [ ] **Step 4: Commit**

```bash
git add src/agents/graph/ src/services/agent_service.py src/core/log_events.py
git commit -m "refactor(logging): [agent] 批日志迁移 + model turn 摘要（主 agent 推理点）"
```

---

## Task 9: 迁移批 3.4 `[session]` / `[db]` / `[llm]`（openspec tasks 3.4 + 3.6）

**Files:**
- Modify: `src/chat/manager.py`、`src/chat/persistence.py`、`src/chat/streaming.py`（[session]）；`src/infra/db/vector_store/*`、`src/infra/db/file_store.py`（[db]）；`src/infra/llm/*`（[llm]，含 langfuse_tracing/prompt_manager/llm_content_logging 等在 inventory 标 [llm] 的行）
- Modify: `src/core/log_events.py`
- 对照: migration-inventory.md 目标前缀 `[session]`/`[db]`/`[llm]`

**本批验收清单（执行 + review 双重检查）:**
- 事件名 ≤3 词、英文小写空格分隔；无类名（`[SQL]`/`[API]` 类前缀归位为 `[db]`/`[session]` 等）、函数名、工具下划线名、自由句
- 级别落在五级语义：info 里程碑 / warning 降级可恢复 / error 单点已处理
- 字段键 snake_case；值经 helper 编码（不手拼引号、不手写 `session_id=`——session 已由 Task 5 全行注入）；query 完整不截断
- 事件名全局唯一：Event 枚举 name 跨前缀不重复（重复会在 import 校验抛 AssertionError）
- helper 调用不传前缀与级别；exception 保持 `logger.xxx` 直调 + 文本规范化
- grep 验收：本批文件 `logger.info/warning/error` 直调清零（仅 exception 残留）；core/logging.py 的 `[SQL]` 与 middleware `[API]` 属未排期已知例外、不动；无中文混行（保留用户可见文案除外）

- [ ] **Step 1: 盘点 + 转换**

同 Task 7 规则。本批注意：
- session 事件不需要手写 `session_id=`（Task 5 全行注入）
- `[SQL]`/`[API]` 类前缀残留只在**本批文件内**出现时才归位：core/logging.py 的 `log_sql_result`（`[SQL]`）与 middleware 的 `[API]` 均属 inventory「未排期」域，**不在本批范围**，保持现状并在 Task 11 抽查中列为已知例外
- `logger.exception` 文本规范化（去掉中文自由句），不建 spec

- [ ] **Step 2: 登记本批事件**

- [ ] **Step 3: 回归**

Run: `pytest tests/ -v`；`ruff check . && ruff format .`

- [ ] **Step 4: Commit**

```bash
git add src/chat/ src/infra/db/ src/infra/llm/ src/core/log_events.py
git commit -m "refactor(logging): [session]/[db]/[llm] 批日志迁移 + 事件登记"
```

---

## Task 10: 迁移批 3.5 cli / 入口（openspec tasks 3.5 + 3.6）

**Files:**
- Modify: `src/cli/*.py`（eval_ragas / eval_ragas_generate / rebuild_bm25 / check_retrieval / compare_rewrite → `[cli]`）
- Modify: `src/main.py`（入口生命周期 + 全局异常兜底 → `[app]`）
- Modify: `src/core/log_events.py`
- 对照: migration-inventory.md「中文日志清单」表（workflow 已在 3.3 处理；eval_ragas / eval_ragas_generate / search.py / main.py 的行号映射为最终目标）

**本批验收清单（执行 + review 双重检查）:**
- 事件名 ≤3 词、英文小写空格分隔；无类名、函数名、工具下划线名、自由句；`[cli]`/`[app]` 事件在 Event 枚举 + EVENT_SPECS 登记 prefix=`cli`/`app`
- 级别落在五级语义：info 里程碑 / warning 降级可恢复 / error 单点已处理；入口全局兜底按语义归 error 或 exception 直调
- 字段键 snake_case；值经 helper 编码（不手拼引号、不手写 `session_id=`）；query 完整不截断；`├─` 制表符随迁移去除
- 事件名全局唯一：Event 枚举 name 跨前缀不重复（重复会在 import 校验抛 AssertionError）
- 中文 message 清零（保留 SSEInteractionTexts / CLI 用户可见提示除外）
- grep 验收：本批文件 `logger.info/warning/error` 直调清零（仅 exception 残留）；main.py 无中文日志残留

- [ ] **Step 1: 中文日志转换（按 inventory 行号映射）**

eval_ragas_generate.py 9 行中文、eval_ragas.py 1 行、main.py 8 行、search.py 1 行按 inventory「迁移后英文 k=v」列落到 `[cli]`/`[db]`/`[app]` 注册事件（这些行号是 2026-09-03 快照，批执行以实际为准）。

main.py 示例（归 `[app]`，去 `├─` 制表符，保留用户可见文案不在此处）：

```python
# 原: logger.info("财务问答 API 正在启动")
log_event(Event.APP_STARTING)
# 原: logger.info("财务问答 API 正在关闭")
log_event(Event.APP_STOPPING)
# 原: logger.error("未处理的系统异常: {} {}", method, url) （或对应 exception 直调）
log_event(Event.UNHANDLED_EXCEPTION, method=..., url=...)
# 原: logger.info("  ├─ 嵌套第{}层: type={} msg={}", ...)
log_event(Event.EXCEPTION_CHAIN, depth=..., type=..., msg=...)
```

（异常兜底若含 exception 直调则保持直调 + 文本规范化；main.py 事件在 `Event` 枚举 + `EVENT_SPECS` 登记 prefix=`app`。）

- [ ] **Step 2: cli 其余 logger 调用按 Task 7 两轨规则转 `[cli]` 事件或 exception 直调**

- [ ] **Step 3: 回归**

Run: `pytest tests/ -v`；`ruff check . && ruff format .`；`pyright src/` 不新增 error
Run: `grep -rnP '"[^"]*[\x{4e00}-\x{9fff}]' src/ --include=*.py` 中文日志清理确认（保留 SSEInteractionTexts / CLI 用户可见提示除外）

- [ ] **Step 4: Commit**

```bash
git add src/cli/ src/main.py src/core/log_events.py
git commit -m "refactor(logging): [cli]/[app] 批日志迁移（中文→英文 k=v）+ 事件登记"
```

---

## Task 11: 全量回归 + 收尾（openspec tasks 5.8 / 4.3）

**Files:**
- 无新增；运行验证
- Modify: `docs/openspec/changes/logging-convention-migration/tasks.md`（勾选对应 3.2-3.6 / 5.x 项）

- [ ] **Step 1: 全量质量门禁**

Run: `pytest tests/ -v` → 全过
Run: `ruff check .` → 无 error
Run: `pyright src/` → 不新增 error（存量第三方误报不计）
Run: `openspec validate logging-convention-migration` → Change valid

- [ ] **Step 2: 规范一致性抽查**

Run: 对 8 前缀各 grep 一条样例确认事件可聚合；确认无 `KBRouter:` / `format_node:` / `retrieve_kb done` / `search_web done` 类残留事件词
Run: `grep -rn 'log_event("\|retrieval_signal("' src/ --include=*.py` 应**零命中**（字符串风格 helper 调用已全部转 Event/Signal 枚举；有命中即漏转，需回补）
Run: 对照 logging-rules.md「事件全集以 log_events.py 为准」，抽查 Event 枚举与 EVENT_SPECS 无漂移（Task 2 import 校验已兜底）
Run: `grep -rn '\[SQL\]\|\[API\]\|\[table_preserving\]\|\[parent_child\]\|\[qa\]' src/ --include=*.py` → 命中均为 inventory「未排期」域**已知例外**（core/logging.py SQL / middleware API / chunking 分块域），非本 change 范围

- [ ] **Step 3: 人工端到端冒烟（承接 e2e-playwright-regression）**

重启容器后跑一条真实 query，确认：
- 日志行第 4 段有 session_id、检索有 `[retrieval] retrieve replay` 行、信号行格式正确（query 完整）
- `[verify] judge start/done`、`[agent] model turn` 按态 A/B 出现
- 态 A 无 judge start（正面锚成立）

Run: `docker compose restart app`（日志改动生效，无需 --build）
Run: `docker compose exec app python -m src.cli.replay_trace --trace <上一步真实 query 的 trace_id>` → 输出按 iteration 的 top 片段 + drift 对照（验证 Task 6 重放接线在容器内真实可用；若该 trace 无检索轮则换一条绑定 KB 的 query 重试）

- [ ] **Step 4: tasks.md 收尾勾选 + Commit**

勾选 `docs/openspec/changes/logging-convention-migration/tasks.md` 3.2-3.6 与 5.1-5.8 已完成项（以代码为准）。

```bash
git add docs/openspec/changes/logging-convention-migration/tasks.md
git commit -m "docs(opsx): logging-convention-migration tasks 收尾勾选（3.2-3.6 / 5.1-5.8）"
```

---

## Self-Review Notes（写入时已核对）

- **Spec coverage**：observability-logging spec 的每个 Requirement 都有对应 Task——分层前缀(1/3/7-10)、英文 k=v(3/7-10)、五级语义(3/7-10)、检索信号(3/7-10 信号为保留前缀不动)、query 完整(3/4)、replay 上下文行(4)、离线重放 CLI(6)、事件命名与编码(2/3)、事件注册表与 helper 边界(2/3)、会话注入(5)、生成层可观测(8 judge start/done + model turn)。Migration 批次 3.2-3.5 = Task 7-10。
- **顺序依赖**：Task 2→3→(4,5,6) 顺序执行可并行微调；Task 7-10 依赖 Task 3 helper（先做完 Task 3 再开迁移批，避免双写 helper 接口）。
- **已核对旧 plan**：`2026-09-03-logging-convention-migration.md` 仅覆盖已合入的 3.1 试点，不包含本 plan 的 5.x 与 3.2-3.5，本次为全新 plan（文件名 09-04）。
