# session-process-replay Implementation Plan（v2，经强模型评审修正）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** AI 回答的过程叙事（状态行/思考块/旁白/委派分析）按事件到达顺序持久化到 MySQL `conversation_history.process` 列，messages 接口返回，前端以与实时路径相同的渲染管线重建——刷新后与当时观看一致。

**Architecture:** 事件采集（`_StreamCapture.events_log` 双路统一 append，经 `partial_holder` 传递到落库点，无服务端聚合状态机）→ 终态按轮次语义分拣序列化（tool_calls 收尾轮的 content token 帧=旁白保留；末轮 answer token 帧剔除；model_info/abstention/done/error/citation 五类不入 process）→ messages 返回 → 前端历史回放喂给与实时共用的过程元素渲染函数。相邻同型合并在渲染层，JSON 逐元素粒度。

**Tech Stack:** Python 3.11 / FastAPI / LangChain（langchain-openai 1.3.3）/ MySQL（SQLAlchemy Mapped 风格）/ 原生 JS（deploy/nginx/html/chat.html）

**Spec:** `docs/openspec/changes/session-process-replay/`（proposal.md / design.md / specs/session-process-replay/spec.md）

## Global Constraints

- 单文件超过 400 行必须拆分；单函数超过 80 行拆子函数
- 不用三元表达式（`a if cond else b`），写完整 if/else
- 类型不确定的值不用 `getattr(x, "attr", default)` 隐式兜底，用显式判断（chat_repo 既有 getattr 白名单模式除外，对齐现状）
- 所有函数必须有 docstring；SQLAlchemy 模型用 `Mapped/mapped_column` 风格（对齐 models/chat.py 既有写法）
- 日志事件消息英文 k=v；测试 mock 外部依赖不发起真实网络调用
- 门禁：`pytest tests/ -v` 全过、`ruff check .` 无错误、`pyright src/` 无新增 error
- 提交信息格式：`feat(scope): 中文描述` / `test(scope): ...`

## 关键代码事实（实施前必读，均已实证）

- 事件到达有**两条路径**：主循环 `astream_events → _convert_event`（agent_service.py:493-513，retrieve/status/token 帧）与并行 clarify drain（agent_service.py:399-426，delegate/ask_user 帧）——采集必须覆盖两路
- `model_info`/`abstention` 是循环结束后由 capture 直接补发（agent_service.py:546-554），`done` 在 chat.py:218/231/243 直发——三类不经 `_convert_event`
- 落库链四层：chat.py `_run_with_finalize`（L152，经 `svc.save_assistant_async`）→ app_service.py:246 → ChatManager（manager.py:126）→ `persistence.save_assistant_message` → `chat_repo.save_message`（mysql_db/chat_repo.py:89，**白名单式重建 MessageModel**，新字段必须在此透传）
- `capture` 是 `_run_generation` 局部变量，落库点拿不到——events_log 与模型名经 `partial_holder` 共享 dict 传递（其本就是为取消/出错回读设计的通道）
- `conversation_history` 已有 `model_name` 列（models/chat.py:36，chat_repo:107 已透传）——**复用它，不加 model_used 列**
- 主模型工厂 `get_llm` 返回 `ChatQwenWithReasoning`（models.py:153，reasoning_chat.py:16，自带 chunk 侧 reasoning 提取）——回传子类必须继承它
- 请求侧覆写点：langchain_openai 1.3.3 的 `_convert_message_to_dict` 是**模块级函数**（base.py:348）不可覆写；实例方法覆写点为 `_get_request_payload`（base.py:1720，调用形如 `payload = self._get_request_payload(messages, stop=stop, **kwargs)`）

---

### Task 1: 固化帧样本 fixture + build_process_events 分拣函数（TDD）

**Files:**
- Create: `tests/fixtures/process_frames_sample.json`
- Create: `src/chat/process_log.py`
- Test: `tests/chat/test_process_log.py`

**Interfaces:**
- Produces: `build_process_events(events_log: list[dict]) -> dict` —— 输入 `[{"type": str, "payload": dict}, ...]`，输出 `{"format_version": 1, "events": [{"seq": int, "type": str, "payload": dict}, ...]}`。分拣：`token` 帧累积待定区，遇非 token 事件固化为 `preamble` 元素；`model_info`/`abstention`/`done`/`error`/`citation` 五类不入结果；末次待定区（answer 段）丢弃。
- Produces: `serialize_process(events_log: list[dict]) -> str`（Task 4 落库调用）

- [ ] **Step 1: 写固化 fixture**

创建 `tests/fixtures/process_frames_sample.json`（源自 `trace_3157b559` 真实帧序裁剪）：

```json
[
  {"type": "status", "payload": {"stage": "agent", "message": "正在思考..."}},
  {"type": "token", "payload": {"token": "参考文档为空，我将"}},
  {"type": "token", "payload": {"token": "通过检索知识库和联网搜索来获取腾讯2025年的财务数据。"}},
  {"type": "status", "payload": {"stage": "retrieve", "message": "正在检索相关文档...", "detail": "query=腾讯2025年毛利率 净利率 ROE 财务数据"}},
  {"type": "status", "payload": {"stage": "retrieve", "message": "检索完成，正在分析..."}},
  {"type": "status", "payload": {"stage": "agent", "message": "正在思考..."}},
  {"type": "token", "payload": {"token": "知识库中只有2024年的数据，没有2025年的数据。"}},
  {"type": "token", "payload": {"token": "我需要联网搜索腾讯2025年的财务数据。"}},
  {"type": "status", "payload": {"stage": "web_search", "message": "正在联网搜索...", "detail": "queries=['腾讯2025年年报 毛利率 净利率 ROE']"}},
  {"type": "status", "payload": {"stage": "web_search", "message": "联网搜索完成，正在分析..."}},
  {"type": "status", "payload": {"stage": "agent", "message": "正在思考..."}},
  {"type": "token", "payload": {"token": "根据联网搜索到的腾讯2025年年报数据，我现在可以为您详细计算和分析。"}},
  {"type": "token", "payload": {"token": "## 腾讯2025年毛利率、净利率、ROE计算与趋势解读"}},
  {"type": "citation", "payload": {"source": "https://example.com/a", "page": 0, "snippet": "...", "index": 1}},
  {"type": "model_info", "payload": {"model": "qwen3.7-flash", "is_fallback": false}},
  {"type": "done", "payload": {"trace_id": "trace_test", "cancelled": false}}
]
```

- [ ] **Step 2: 写失败测试**

创建 `tests/chat/test_process_log.py`：

```python
"""process_log 分拣函数测试——fixture 源自 trace_3157b559 真实帧序裁剪。"""

import json
from pathlib import Path

from src.chat.process_log import build_process_events

FIXTURE = Path(__file__).parent.parent / "fixtures" / "process_frames_sample.json"


def _load_events() -> list[dict]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


class TestBuildProcessEvents:
    def test_structure_and_version(self):
        result = build_process_events(_load_events())
        assert result["format_version"] == 1
        assert all("seq" in e and "type" in e and "payload" in e for e in result["events"])
        assert [e["seq"] for e in result["events"]] == list(range(1, len(result["events"]) + 1))

    def test_answer_tokens_excluded(self):
        result = build_process_events(_load_events())
        text = json.dumps(result, ensure_ascii=False)
        # 末轮 answer 的 token 不入 process（answer 列承载），避免历史回放正文重复
        assert "详细计算和分析" not in text
        assert "腾讯2025年毛利率、净利率、ROE计算与趋势解读" not in text

    def test_preamble_tokens_kept(self):
        result = build_process_events(_load_events())
        preambles = [e for e in result["events"] if e["type"] == "preamble"]
        assert len(preambles) == 2
        assert "参考文档为空" in preambles[0]["payload"]["text"]
        assert "知识库中只有2024年的数据" in preambles[1]["payload"]["text"]

    def test_excluded_types_dropped(self):
        result = build_process_events(_load_events())
        types = {e["type"] for e in result["events"]}
        assert not types & {"model_info", "abstention", "done", "error", "citation", "token"}

    def test_status_events_kept_in_order(self):
        result = build_process_events(_load_events())
        statuses = [e for e in result["events"] if e["type"] == "status"]
        assert len(statuses) == 7
        assert statuses[1]["payload"]["detail"].startswith("query=腾讯2025年毛利率")

    def test_empty_log(self):
        assert build_process_events([]) == {"format_version": 1, "events": []}

    def test_chitchat_no_tool_all_tokens_dropped(self):
        # 纯闲聊：无工具调用，全部 token 为正文 → process 为空（正文走 answer 列）
        events = [{"type": "token", "payload": {"token": "今天天气不错"}}]
        result = build_process_events(events)
        assert result["events"] == []
```

- [ ] **Step 3: 运行测试确认失败**

Run: `pytest tests/chat/test_process_log.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'src.chat.process_log'`

- [ ] **Step 4: 实现 build_process_events**

创建 `src/chat/process_log.py`：

```python
"""过程事件日志分拣——把生成期采集的原始事件序列整理为可回放的过程元素。

分拣规则（design.md D1/D2）：
- token 帧累积为待定区，遇非 token 事件固化为 preamble（旁白）元素；
  日志末次的待定区是末轮 answer 的正文流，丢弃（正文由 answer 列承载）。
- model_info / abstention / done / error / citation 不入 process：
  模型名与拒答语义由既有列承载，引用由 sources 列承载，终态非可见元素。
"""

import json
from typing import Any

PROCESS_FORMAT_VERSION = 1

_EXCLUDED_TYPES = frozenset({"model_info", "abstention", "done", "error", "citation"})


def build_process_events(events_log: list[dict[str, Any]]) -> dict[str, Any]:
    """把采集的原始事件序列分拣为过程元素序列。

    Args:
        events_log: 采集的事件列表，元素为 {"type": str, "payload": dict}（按到达顺序）

    Returns:
        {"format_version": 1, "events": [{"seq", "type", "payload"}, ...]}，
        seq 从 1 递增；answer 段 token 帧与排除类型不出现在结果中
    """
    events: list[dict[str, Any]] = []
    pending_tokens: list[str] = []

    for ev in events_log:
        ev_type = ev["type"]
        if ev_type in _EXCLUDED_TYPES:
            # 排除类型截断待定区：其后的 token 属于正文段（answer 列承载）
            pending_tokens = []
            continue
        if ev_type == "token":
            pending_tokens.append(ev["payload"].get("token", ""))
            continue
        # 非 token 事件到达：待定 token 固化为旁白块（若非空）
        if pending_tokens:
            events.append({"type": "preamble", "payload": {"text": "".join(pending_tokens)}})
            pending_tokens = []
        events.append({"type": ev_type, "payload": ev["payload"]})

    # 循环结束仍持有的待定区 = 末轮 answer 正文流 → 丢弃，不入 process
    return {"format_version": PROCESS_FORMAT_VERSION, "events": events}


def serialize_process(events_log: list[dict[str, Any]]) -> str:
    """序列化 process 列内容（落库用），供调用方直传。"""
    return json.dumps(build_process_events(events_log), ensure_ascii=False)
```

- [ ] **Step 5: 运行测试确认通过**

Run: `pytest tests/chat/test_process_log.py -v`
Expected: 全部 PASS

- [ ] **Step 6: Commit**

```bash
git add tests/fixtures/process_frames_sample.json tests/chat/test_process_log.py src/chat/process_log.py
git commit -m "feat(chat): 过程事件日志分拣——build_process_events 按轮次语义分离旁白与正文"
```

---

### Task 2: 双路统一事件采集接线

**Files:**
- Modify: `src/services/agent_service.py`（`_StreamCapture` 定义处约 L69-84、主循环约 L493-513、`_drain_clarify_channel` 约 L399-426、循环收尾）
- Modify: `src/api/chat.py`（`_run_with_finalize` 落库点，本任务只接线 partial_holder）
- Test: `tests/services/test_agent_service.py`（追加）

**Interfaces:**
- Consumes: 现有 `_convert_event(item, capture)`（drain 与主循环均调用）、`partial_holder` 共享 dict（chat.py 传入）
- Produces: `_StreamCapture.events_log: list[dict]`（元素 `{"type", "payload"}`）；`_record_event(capture, event) -> None`；`partial_holder["events_log"]`（events_log 的同一列表引用）与 `partial_holder["model_name"]`（收尾写入）——Task 4 落库依赖

- [ ] **Step 1: 写失败测试**

`tests/services/test_agent_service.py` 追加：

```python
class TestEventsLogCollection:
    def test_main_loop_events_collected(self):
        """主循环路径：转换出的 SSE 事件被采集进 events_log。"""
        capture = _StreamCapture()
        event = SSEStatusEvent(SSEInteractionTexts.STAGE_AGENT, SSEInteractionTexts.AGENT_STATUS_THINKING)
        _record_event(capture, event)
        assert capture.events_log == [{"type": "status", "payload": event.payload_for_buffer()}]

    def test_drain_path_events_collected(self):
        """clarify drain 路径：delegate/ask_user 帧同样被采集（评审 F2）。"""
        capture = _StreamCapture()
        delegate_item = {"type": "delegate", "action": "start", "delegate_id": "d1", "skill": "finance-analyst"}
        for ev in _convert_event(delegate_item, capture):
            _record_event(capture, ev)
        assert capture.events_log[0]["type"] == "delegate"

    def test_capture_none_noop(self):
        _record_event(None, SSEStatusEvent("s", "m"))  # 不抛异常即可
```

（导入区补 `from src.services.agent_service import _StreamCapture, _record_event, _convert_event` 与 SSE 事件类。）

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/services/test_agent_service.py -k EventsLog -v`
Expected: FAIL（`_record_event` 未定义 / events_log 属性不存在）

- [ ] **Step 3: 实现**

`_StreamCapture` 加字段（dataclass）：

```python
    events_log: list[dict[str, Any]] = field(default_factory=list)  # 过程事件采集（历史回放持久化源，design D1）
```

模块级 helper（`_convert_event` 之后）：

```python
def _record_event(capture: _StreamCapture | None, event) -> None:
    """事件采集：追加进本次生成的私有事件日志（design D1，独立于 manager 缓冲）。

    Args:
        capture: 流捕获容器（None 时静默跳过——无消费者的采集无意义）
        event: 转换后的 SSE 事件
    """
    if capture is None:
        return
    capture.events_log.append({"type": event.type, "payload": event.payload_for_buffer()})
```

两处调用点（**两条路径都要接**）：

```python
# 主循环（_run_generation 内，约 L496）
for event in _convert_event(item, capture):
    _record_event(capture, event)                                   # ← 新增
    manager.add_event(session_id, event.type, event.payload_for_buffer())

# drain 任务（_drain_clarify_channel 内，约 L419）
for event in _convert_event(item, capture):
    _record_event(capture, event)  # ← 新增（drain 循环变量名与主循环一致）
    manager.add_event(session_id, event.type, event.payload_for_buffer())
```

`_run_generation` 内 capture 创建后（partial_holder 非 None 时）挂引用，循环 finally 写模型名：

```python
    capture = _StreamCapture()
    if partial_holder is not None:
        partial_holder["events_log"] = capture.events_log   # 共享列表引用，取消路径亦持续可见
    ...
    finally:
        drain_task.cancel()
        await asyncio.gather(drain_task, return_exceptions=True)
        if partial_holder is not None:
            partial_holder["model_name"] = capture.model_used
```

- [ ] **Step 4: 运行测试确认通过 + 全量回归**

Run: `pytest tests/services/test_agent_service.py -v && pytest tests/ -v`
Expected: 新测试 PASS，存量全过

- [ ] **Step 5: Commit**

```bash
git add src/services/agent_service.py src/api/chat.py tests/services/test_agent_service.py
git commit -m "feat(agent): 过程事件双路统一采集——主循环与 clarify drain 共用 _record_event"
```

---

### Task 3: reasoning_content 回传（继承 ChatQwenWithReasoning，覆写 _get_request_payload）

**Files:**
- Modify: `src/infra/llm/reasoning_chat.py`（追加子类）
- Modify: `src/models.py:153`（get_llm 返回类型替换）
- Test: `tests/infra/test_reasoning_preserving.py`

**Interfaces:**
- Consumes: `ChatQwenWithReasoning`（reasoning_chat.py:16，chunk 侧 reasoning 提取——**必须继承它**，否则深度思考 reasoning 帧全部消失）
- Produces: `ReasoningPreservingChatQwen(ChatQwenWithReasoning)`——覆写 `_get_request_payload(messages, stop=None, **kwargs)`，把 assistant 消息的 `additional_kwargs.reasoning_content` 注入 payload 对应 dict 的 `reasoning_content` 字段；构造时 extra_body 自动带 `{"preserve_thinking": True}`。`get_llm()` 返回类型不变

- [ ] **Step 1: 写失败测试**

创建 `tests/infra/test_reasoning_preserving.py`：

```python
"""reasoning_content 请求侧回传测试（design D4）——覆写点为 _get_request_payload。"""

from langchain_core.messages import AIMessage, HumanMessage

from src.infra.llm.reasoning_chat import ReasoningPreservingChatQwen


def _llm() -> ReasoningPreservingChatQwen:
    return ReasoningPreservingChatQwen(model="qwen3.7-flash", api_key="test")


class TestReasoningPreserving:
    def test_assistant_reasoning_injected_into_payload(self):
        messages = [
            HumanMessage(content="计算腾讯2025年毛利率"),
            AIMessage(
                content="",
                tool_calls=[{"name": "retrieve_kb", "args": {"query": "q"}, "id": "t1"}],
                additional_kwargs={"reasoning_content": "先检索知识库"},
            ),
        ]
        payload = _llm()._get_request_payload(messages)
        assistant = [m for m in payload["messages"] if m["role"] == "assistant"][0]
        assert assistant["reasoning_content"] == "先检索知识库"
        assert assistant["tool_calls"][0]["function"]["name"] == "retrieve_kb"

    def test_no_reasoning_no_field(self):
        messages = [AIMessage(content="hi")]
        payload = _llm()._get_request_payload(messages)
        assert "reasoning_content" not in payload["messages"][0]

    def test_extra_body_preserve_thinking(self):
        assert _llm().extra_body.get("preserve_thinking") is True
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/infra/test_reasoning_preserving.py -v`
Expected: FAIL（`ReasoningPreservingChatQwen` 不存在）

- [ ] **Step 3: 实现**

`src/infra/llm/reasoning_chat.py` 末尾追加：

```python
class ReasoningPreservingChatQwen(ChatQwenWithReasoning):
    """在 chunk 侧 reasoning 提取之上，增加请求侧 reasoning_content 回传。

    DashScope 要求：深度思考模式多轮工具调用时，assistant 消息须携带
    reasoning_content（省略降低工具调用准确性），请求须启用 preserve_thinking。
    覆写点为 _get_request_payload（实例方法，langchain-openai 1.3.3 的
    _convert_message_to_dict 是模块级函数不可覆写）。
    """

    def __init__(self, **kwargs) -> None:
        extra = dict(kwargs.pop("extra_body", None) or {})
        extra.setdefault("preserve_thinking", True)
        kwargs["extra_body"] = extra
        super().__init__(**kwargs)

    def _get_request_payload(self, messages, stop=None, **kwargs):
        payload = super()._get_request_payload(messages, stop=stop, **kwargs)
        for msg, msg_dict in zip(messages, payload.get("messages", [])):
            if msg_dict.get("role") != "assistant":
                continue
            reasoning = (getattr(msg, "additional_kwargs", None) or {}).get(
                "reasoning_content", ""
            )
            if reasoning and not msg_dict.get("reasoning_content"):
                msg_dict["reasoning_content"] = reasoning
        return payload
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/infra/test_reasoning_preserving.py -v`
Expected: PASS

- [ ] **Step 5: 接入 get_llm 工厂**

`src/models.py`：import 处加 `ReasoningPreservingChatQwen`；L153 的 `return ChatQwenWithReasoning(` 改为 `return ReasoningPreservingChatQwen(`（参数不变——子类继承其 chunk 侧 reasoning 提取，深度思考行为无损）。

Run: `pytest tests/ -v -k "model or llm" && ruff check src/models.py src/infra/llm/reasoning_chat.py`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/infra/llm/reasoning_chat.py src/models.py tests/infra/test_reasoning_preserving.py
git commit -m "feat(llm): reasoning_content 请求侧回传——继承 ChatQwenWithReasoning 覆写 _get_request_payload"
```

---

### Task 4: 落库链四层透传（DDL + MessageModel + persistence + manager + app_service + chat_repo + chat.py）

**Files:**
- Modify: `src/infra/db/models/chat.py`（MessageModel 加 process 列）
- Modify: `src/chat/persistence.py`（save_assistant_message 加参）
- Modify: `src/chat/manager.py:126`（save_assistant_async 加参）
- Modify: `src/services/app_service.py:246`（save_assistant_async 加参）
- Modify: `src/infra/db/mysql_db/chat_repo.py:89`（save_message 白名单加 process）
- Modify: `src/api/chat.py`（`_run_with_finalize` 落库调用点 L211/L224）
- Test: `tests/chat/test_persistence_process.py`

**Interfaces:**
- Consumes: Task 1 `serialize_process(events_log) -> str`；Task 2 `partial_holder["events_log"]`（列表引用）、`partial_holder["model_name"]`
- Produces: 四层透传签名 `save_assistant_async(..., status, process_json=None, model_name=None)` 与 `save_assistant_message(..., status, process_json=None, model_name=None)`；MessageModel 新列 `process`（MEDIUMTEXT NULL）；`model_name` 复用既有列

- [ ] **Step 1: DDL（手动执行；cookbook 登记在 Task 8）**

```sql
ALTER TABLE conversation_history ADD COLUMN process MEDIUMTEXT NULL COMMENT '过程事件JSON（历史回放）';
```

（`model_name` 列已存在，无需 DDL。）

- [ ] **Step 2: 写失败测试**

创建 `tests/chat/test_persistence_process.py`（mock 模式对齐既有 chat 测试；chat_repo 层用真实 MessageModel 断言白名单透传）：

```python
"""process 列四层透传测试。"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.chat.persistence import ChatPersistence


class TestProcessPersistsThroughChain:
    async def test_persistence_sets_process_on_model(self):
        repo = MagicMock()
        repo.save_message = AsyncMock()
        persistence = ChatPersistence.__new__(ChatPersistence)
        persistence._chat_repo = repo
        await persistence.save_assistant_message(
            "sess_1", "kb_1", "正文",
            process_json='{"format_version": 1, "events": []}',
            model_name="qwen3.7-flash",
        )
        saved = repo.save_message.call_args[0][0]
        assert saved.process == '{"format_version": 1, "events": []}'
        assert saved.model_name == "qwen3.7-flash"

    async def test_defaults_backward_compatible(self):
        repo = MagicMock()
        repo.save_message = AsyncMock()
        persistence = ChatPersistence.__new__(ChatPersistence)
        persistence._chat_repo = repo
        await persistence.save_assistant_message("sess_1", "kb_1", "正文")
        saved = repo.save_message.call_args[0][0]
        assert saved.process is None
```

（若 ChatPersistence 构造方式不同，对齐既有 persistence 测试的 mock 写法。）

- [ ] **Step 3: 运行确认失败**

Run: `pytest tests/chat/test_persistence_process.py -v`
Expected: FAIL（`save_assistant_message` 无 `process_json` 参数）

- [ ] **Step 4: 实现（四层 + 模型列）**

`src/infra/db/models/chat.py` MessageModel 加（Mapped 风格对齐现状）：

```python
    process: Mapped[str | None] = mapped_column(MEDIUMTEXT, nullable=True, comment="过程事件JSON（历史回放）")
```

（`from sqlalchemy.dialects.mysql import MEDIUMTEXT` 导入。）

`src/chat/persistence.py` save_assistant_message 签名与 MessageModel 构造：

```python
    async def save_assistant_message(
        self,
        session_id: str,
        kb_id: str,
        assistant_msg: str,
        sources: list[dict] | None = None,
        status: str = "complete",
        process_json: str | None = None,   # 过程事件JSON（历史回放，None=存量语义）
        model_name: str | None = None,     # 实际回答模型名（复用既有列）
    ) -> None:
        ...
            await self._chat_repo.save_message(
                MessageModel(
                    session_id=session_id,
                    kb_id=kb_id,
                    role="assistant",
                    content=assistant_msg,
                    sources=sources_json,
                    status=status,
                    process=process_json,       # ← 新增
                    model_name=model_name or "",  # ← 新增
                )
            )
```

`src/chat/manager.py:126` save_assistant_async 加 `process_json: str | None = None, model_name: str | None = None` 并透传给 `self._persistence.save_assistant_message(...)`。

`src/services/app_service.py:246` save_assistant_async 同样加参透传给 `self.chat_manager.save_assistant_async(...)`。

`src/infra/db/mysql_db/chat_repo.py:89` save_message 的 MessageModel 构造加一行：

```python
                process=getattr(msg, "process", None),  # 白名单透传（既有 getattr 模式）
```

`src/api/chat.py` `_run_with_finalize` 两处 `svc.save_assistant_async(...)` 调用（L211 正常、L224 中断）改为：

```python
            await svc.save_assistant_async(
                session_id, kb_id, partial,
                partial_holder.get("sources", []),
                status="complete",  # 中断分支为 "interrupted"
                process_json=serialize_process(partial_holder.get("events_log") or []),
                model_name=partial_holder.get("model_name", ""),
            )
```

（顶部 import：`from src.chat.process_log import serialize_process`。中断分支的 events_log 到中断点为止，design D5。）

- [ ] **Step 5: 运行测试确认通过 + 回归**

Run: `pytest tests/chat/ -v && pytest tests/ -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/infra/db/models/chat.py src/chat/persistence.py src/chat/manager.py src/services/app_service.py src/infra/db/mysql_db/chat_repo.py src/api/chat.py tests/chat/test_persistence_process.py
git commit -m "feat(chat): process 事件四层透传落库（复用 model_name 列）"
```

---

### Task 5: messages 接口返回

**Files:**
- Modify: `src/services/app_service.py:169`（get_messages 行枚举补 process）
- Modify: `src/api/model/response.py`（MessageItem）
- Modify: `src/api/sessions.py:100-112`（get_session_messages 组装）
- Test: `tests/api/` 既有 messages 测试文件追加

**Interfaces:**
- Consumes: Task 4 落库列
- Produces: MessageItem 新字段 `process: dict | None`（`{"format_version", "events"}` 反序列化对象）、`model_name: str | None`

- [ ] **Step 1: 写失败测试**

既有 messages 测试追加用例：

```python
class TestMessagesProcessField:
    async def test_process_deserialized_to_dict(self):
        # mock svc.get_messages 返回含 process JSON 串与 model_name 的行
        ...
        assert item.process == {"format_version": 1, "events": []}
        assert item.model_name == "qwen3.7-flash"

    async def test_legacy_null_process(self):
        # process/model_name 为 None 的存量行
        ...
        assert item.process is None
        assert item.model_name is None
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/api/ -k messages -v`
Expected: FAIL（MessageItem 无 process 字段）

- [ ] **Step 3: 实现**

`app_service.py` get_messages 行枚举加 `"process": m.process,`（model_name 已在枚举中 ✓）。

`response.py` MessageItem 加：

```python
    process: dict | None = None     # 过程事件对象（历史回放，存量消息 null）
    model_name: str | None = None   # 实际回答模型名（存量消息 null）
```

`sessions.py` 组装处（L100-112）：

```python
process_raw = row.get("process")
process = json.loads(process_raw) if process_raw else None
result.append(
    MessageItem(
        ...,
        process=process,
        model_name=row.get("model_name"),
    )
)
```

（`import json` 若缺则补。）

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/api/ -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/services/app_service.py src/api/model/response.py src/api/sessions.py tests/api/
git commit -m "feat(api): messages 接口返回 process 过程数据与 model_name"
```

---

### Task 6: 前端实时路径——旁白待定区 + 相邻合并（修正版）

**Files:**
- Modify: `deploy/nginx/html/chat.html`（CSS 区、渲染函数区 L1043-1141、handler 区、`removeStreamingState` L1685）

**Interfaces:**
- Produces: `appendProcessEvent` 内联逻辑（status 组合并、旁白预览、待定区分流）；Task 7 历史回放**不复用**实时 handler（副作用边界，design D6），仅复用 CSS 与静态渲染函数

- [ ] **Step 1: 补充 CSS（从设计稿 v2 搬运，类名对齐）**

chat.html 样式区（`.think-row` 之后）追加设计稿 v2 的组与旁白样式：

```css
  .pg-group{display:flex;flex-direction:column;gap:5px}
  .preamble{border-left:3px dashed var(--accent);background:var(--surface);border-radius:0 8px 8px 0;
    padding:7px 12px;font-size:13px;line-height:1.6;color:var(--text-secondary);font-style:italic;
    position:relative;width:fit-content;max-width:100%}
```

- [ ] **Step 2: 实现状态与公共逻辑（修正版）**

`ensureProcessContainer` 之后新增，并重构既有零散指针：

```javascript
// ── 过程元素公共状态（design D3/D6）──
let pendingTokens = [];        // 旁白待定区：首个工具调用前的 token
let toolCallSeen = false;      // 是否已出现工具调用（之后 token = 正文）
let pendingPreviewEl = null;   // 待定区预览 div（单一节点引用，固化时原位保留）
let currentStatusGroup = null; // 相邻同型合并：当前 status 组 div

function resetProcessRenderState() {
  pendingTokens = [];
  toolCallSeen = false;
  pendingPreviewEl = null;
  currentStatusGroup = null;
  currentThinkRow = null;
}

// 相邻合并关键：任何非 status 元素入容器后，闭合当前 status 组
function closeStatusGroup() {
  currentStatusGroup = null;
}

function appendStatusTagInto(group, el) {
  group.appendChild(el);
}

function buildStatusTag(stage, message, detail) {
  const div = document.createElement('div');
  div.className = 'status-tag';
  let text = escapeHtml(message);
  if (detail) text += ' <span class="status-detail">' + escapeHtml(detail) + '</span>';
  div.innerHTML = `<span class="status-dot"></span> ${text}`;
  return div;
}

// 待定区预览：单一 div 原位更新（不重复建节点）
function updatePendingPreview() {
  if (!pendingPreviewEl || !pendingPreviewEl.isConnected) {
    pendingPreviewEl = document.createElement('div');
    pendingPreviewEl.className = 'preamble';
    ensureProcessContainer().appendChild(pendingPreviewEl);
  }
  pendingPreviewEl.textContent = pendingTokens.join('');
}

// 固化：待定区转正式旁白块（预览 div 原位保留即成正式块）
function flushPendingAsPreamble() {
  pendingPreviewEl = null;   // 节点已在容器内，保留
  pendingTokens = [];
}

// 待定区丢弃（answer 段并入气泡前清理预览节点）
function dropPendingPreview() {
  if (pendingPreviewEl && pendingPreviewEl.isConnected) pendingPreviewEl.remove();
  pendingPreviewEl = null;
  pendingTokens = [];
}
```

- [ ] **Step 3: 改造既有渲染函数**

`renderStatusTag` 改为（status 进组 + 工具类触发固化）：

```javascript
function renderStatusTag(stage, message, detail) {
  closeThinkRow();
  if (stage === 'retrieve' || stage === 'web_search') {
    toolCallSeen = true;
    flushPendingAsPreamble();
  }
  if (!currentStatusGroup || !currentStatusGroup.isConnected) {
    currentStatusGroup = document.createElement('div');
    currentStatusGroup.className = 'pg-group';
    ensureProcessContainer().appendChild(currentStatusGroup);
  }
  const div = buildStatusTag(stage, message, detail);
  currentStatusGroup.appendChild(div);
  scrollToBottom();
}
```

`renderAiAnswerStream` 分流（B1）：

```javascript
function renderAiAnswerStream(text) {
  if (!toolCallSeen) {
    pendingTokens.push(text);
    updatePendingPreview();
    return;
  }
  streamBuffer += text;
  streamBubble = ensureAiBubble();
  ...（原节流渲染逻辑不变）
}
```

`renderReasoningDelta` 与委派区入口（`ensureDelegateSection`）追加 `closeStatusGroup()`——非 status 元素入容器后闭合 status 组（相邻合并规则）。

`removeStreamingState` 保持现有实现（复位 processContainer/currentThinkRow），**追加** `resetProcessRenderState()` 调用（新提问时全量复位）；ask_user 分支的 `removeStreamingState()` 调用**替换为** `closeThinkRow()`（D6：澄清暂停不复位容器，澄清后帧继续进原容器）。

`done` handler 的 `finalizeAnswer` 之前加：

```javascript
if (pendingTokens.length && !toolCallSeen) {
  // 全程无工具调用：待定区即正文，倒入气泡
  const text = pendingTokens.join('');
  dropPendingPreview();
  streamBuffer = text + streamBuffer;  // 待定内容位于正文开头（preview 已展示过，此处合并定型）
  streamBubble = ensureAiBubble();
}
```

- [ ] **Step 4: 浏览器手工冒烟**

Run: 打开 chat.html 发起一次真实提问（触发检索+联网），DevTools 断言：
- 旁白以虚线斜体块出现在过程容器内，正文气泡无旁白粘连
- 连续 status 在同一个 `.pg-group` 内；preamble/think 之后新 status 开新组
- 纯闲聊提问（无工具调用）：正文正常进气泡，无残留旁白块

- [ ] **Step 5: Commit**

```bash
git add deploy/nginx/html/chat.html
git commit -m "feat(chat): 实时路径旁白待定区隔离与相邻 status 合并"
```

---

### Task 7: 前端历史回放（D7 反转，修正版）

**Files:**
- Modify: `deploy/nginx/html/chat.html`（loadSessionMessages L2452-2481）

**Interfaces:**
- Consumes: Task 6 的 `.pg-group`/`.preamble` CSS 与 status 组模式（历史回放**不复用**实时 handler——副作用边界，design D6；使用容器参数化的静态渲染变体）
- Produces: `rebuildProcessFromEvents(container, processObj)`——历史过程容器重建入口

- [ ] **Step 1: 实现**

`loadSessionMessages` assistant 分支改为（**先重建过程容器，再渲染气泡**——修正 DOM 顺序）：

```javascript
} else if (m.role === 'assistant') {
  // D7 反转：先重建过程容器（append 到 chatContainer 末尾），后渲染气泡
  if (m.process && m.process.format_version === 1 && Array.isArray(m.process.events)) {
    rebuildProcessFromEvents(m.process.events);
  }
  renderAiAnswer(m.content || '', m.status === 'interrupted');
  attachHistoryCitations(lastAiRow(), m.sources);
}
```

新增静态渲染函数（插在 loadSessionMessages 之前；容器参数化，零运行期状态）：

```javascript
// 历史过程重建：按序渲染持久化事件（design D6 复用边界——静态变体，无副作用）
function rebuildProcessFromEvents(events) {
  const container = document.createElement('div');
  container.className = 'process-group';
  chatContainer.appendChild(container);
  let statusGroup = null;    // 相邻合并（组内局部指针）
  let thinkBody = null;      // 开放的 think 块
  let delegateSec = null;    // 开放的委派区
  for (const ev of events) {
    const t = ev.type, p = ev.payload || {};
    if (t === 'status') {
      if (thinkBody) { thinkBody.closest('details')?.remove(); thinkBody = null; } // 防御：未闭合 think 不入历史
      if (!statusGroup) {
        statusGroup = document.createElement('div');
        statusGroup.className = 'pg-group';
        container.appendChild(statusGroup);
      }
      const div = document.createElement('div');
      div.className = 'status-tag';
      const detail = p.detail ? ' <span class="status-detail">' + escapeHtml(p.detail) + '</span>' : '';
      div.innerHTML = `<span class="status-dot"></span> ${escapeHtml(p.message || '')}${detail}`;
      statusGroup.appendChild(div);
    } else if (t === 'preamble') {
      statusGroup = null;
      const div = document.createElement('div');
      div.className = 'preamble';
      div.textContent = p.text || '';
      container.appendChild(div);
    } else if (t === 'delegate') {
      statusGroup = null;
      // 委派区重建：复用设计稿 v2 的 delegate 结构（start 建、end 定格；delta 已聚合为最终文本）
      if (p.action === 'start') {
        delegateSec = document.createElement('section');
        delegateSec.className = 'delegate open';
        delegateSec.innerHTML = `
          <div class="d-head"><span class="dot ok"></span>
            <span class="d-title">${escapeHtml(p.skill || '专家')} · 领域专家分析</span>
            <span class="d-state">领域专家分析完成</span>
            <span class="chev"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M6 9l6 6 6-6"/></svg></span></div>
          <div class="d-body">
            <details class="d-think" open><summary>思考过程</summary><div class="d-think-text"></div></details>
            <div class="d-content"></div></div>`;
        container.appendChild(delegateSec);
      } else if (p.action === 'delta' && delegateSec) {
        const target = p.kind === 'thinking'
          ? delegateSec.querySelector('.d-think-text')
          : delegateSec.querySelector('.d-content');
        if (target) target.textContent += p.delta || '';
      } else if (p.action === 'end' && delegateSec) {
        delegateSec.querySelector('.d-state').textContent =
          p.ok !== false ? '领域专家分析完成' : '分析中断 · ' + (p.reason || '');
      }
    }
    // reasoning 帧历史上不单独持久化块级文本（实时聚合为 preamble/think 已在序列化侧处理）：
    // 若出现（深度思考场景），按 think 块累积渲染
    else if (t === 'reasoning') {
      statusGroup = null;
      let details = container.querySelector('.think-row:last-child');
      if (!details || details.dataset.closed === '1') {
        details = document.createElement('details');
        details.className = 'think-row';
        details.innerHTML = `<summary><span class="think-title">Think</span>
          <span class="think-chevron"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 18l6-6-6-6"/></svg></span></summary>
          <div class="think-body"></div>`;
        container.appendChild(details);
        details.dataset.closed = '0';
      }
      details.querySelector('.think-body').textContent += p.delta || '';
    }
    // token/citation/model_info/done/error/ask_user：跳过
    // （正文=sources/content 列、引用=attachHistoryCitations、模型=model_name 列、澄清=静态注记见下）
  }
}

// ask_user 帧的静态注记（历史回放专用）：澄清内容已在后续 user 消息中
function renderAskUserNote(container) {
  const div = document.createElement('div');
  div.className = 'status-tag';
  div.innerHTML = `<span class="status-dot idle"></span> 已向用户澄清`;
  container.appendChild(div);
}
```

（ask_user 帧在 rebuild 循环内调用 `renderAskUserNote(container)`——补入循环分支 `else if (t === 'ask_user') { renderAskUserNote(container); }`。）

- [ ] **Step 2: 浏览器验证**

Run: 刷新页面加载含 process 的会话（Task 8 端到端生成后），DevTools 断言：
- 过程容器在气泡**之前**、单容器重建、状态组/旁白块/委派区与实时一致
- 正文气泡无重复（answer 段已剔除）
- 加载 process=null 的旧会话：无过程容器、无 JS 错误

- [ ] **Step 3: Commit**

```bash
git add deploy/nginx/html/chat.html
git commit -m "feat(chat): 历史回放重建过程容器——D7 反转，双路径渲染同构"
```

---

### Task 8: 端到端验证 + 文档登记

**Files:**
- Modify: `docs/agents/api_contract.md`（§2.4 messages 新字段：process 结构含 format_version、model_name 字段）
- Modify: `docs/agents/glossary.md`（旁白/过程轨迹术语、D7 反转）
- Modify: `docs/agents/cookbook.md`（ALTER TABLE 操作）
- Modify: `docs/design/pages/chat-harness.md`（D7 反转，已部分同步）

- [ ] **Step 1: 标准门禁**

Run: `pytest tests/ -v` && `ruff check .` && `pyright src/`
Expected: 全过、无新增 error

- [ ] **Step 2: 端到端三方一致性**

1. 页面发起「计算腾讯2025年毛利率…」提问，记录 trace_id（回答完成后 5 分钟内）
2. 按 cookbook「调试」流程回放帧（trace → session_id → events 接口）
3. 调 `/api/sessions/messages` 取回 process
4. 断言：process 事件序 = 帧序分拣结果（answer 段剔除、旁白保留、五类排除）；前端刷新后重建的过程容器与实时一致且正文不重复

- [ ] **Step 3: 文档登记**（C1-C4 落笔：messages 新字段含 format_version/model_name；glossary 加「旁白（preamble）」「过程轨迹」术语与 D7 反转；cookbook 登记 ALTER TABLE）

- [ ] **Step 4: Commit**

```bash
git add docs/agents/ docs/design/
git commit -m "docs: session-process-replay 契约与术语登记"
```

---

## Self-Review 结论（v2）

- **Spec 覆盖**：7 条 Requirement ↔ Task 1（分拣+fixture）、Task 2（双路采集+partial_holder）、Task 3（回传）、Task 4（四层落库+DDL）、Task 5（接口）、Task 6-7（旁白/合并/回放/静态化）、Task 8（登记）——全覆盖
- **占位符扫描**：v1 的幻影函数 `renderAiAnswerStreamForce` 已内联；无其他未定义引用
- **类型一致性**：`build_process_events`/`serialize_process`/`_record_event`/`process_json`/`model_name`/`{"format_version", "events"}` 跨任务一致
- **评审问题闭环**：强模型 8 项高优先级发现全部落入 Task 2（partial_holder）、3（覆写点/基类）、4（四层链）、5（get_messages）、6（合并重置/预览单节点）、7（数据形状/DOM 顺序）
