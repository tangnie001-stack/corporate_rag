# sse-tool-detail Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** SSE 状态事件携带工具调用明细（工具名 + query/搜索词），前端等待时能看到"在检索/搜索什么"。

**Architecture:** 复用 `SSEStatusEvent.detail`（sse.py 全链路已透传，不新增事件类型）——`_convert_event` 的 TOOL_START 分支从 LangGraph on_tool_start 事件的 `data.input` 读工具入参填入 detail；前端 `renderStatusTag` 接收 detail 并展示。buffer/resume 天然兼容。

**Tech Stack:** Python 3.11+ / 前端原生 JS（chat.html）/ pytest

## Global Constraints

- 不新增 SSE 事件类；`SSEStatusEvent.detail` 已存在且 to_sse/from_payload/payload_for_buffer 全链路透传
- detail 内容 = 工具名 + 入参要点（人可读）：retrieve_kb 带 query、search_web 带 queries
- query 截断 40 字符；无 detail 时前端走原逻辑（向后兼容）
- `pytest tests/ -v` 全过；`ruff check .` 无错误；`pyright src/` 不新增 error

---

### Task 1: 后端 TOOL_START 填 detail

**Files:**
- Modify: `src/services/agent_service.py:205-221`（TOOL_START 分支）
- Test: `tests/services/test_agent_service.py` / `tests/services/test_dual_stream.py`

**Interfaces:**
- Consumes: LangGraph `on_tool_start` 事件的 `data.input` dict（含工具入参）
- Produces: `SSEStatusEvent(stage, message, detail="query=...")`——detail 格式 `query={q}` / `queries={[...]}`

- [ ] **Step 1: 写失败测试**

在 `tests/services/test_agent_service.py`（或 test_dual_stream，取决于 _convert_event 测试所在）追加：

```python
def test_convert_tool_start_retrieve_kb_carries_query_detail():
    """retrieve_kb on_tool_start → SSEStatusEvent.detail 含 query。"""
    from src.services.agent_service import _convert_event
    from src.agents.graph.state import LangGraphEvent, LangGraphKey

    item = {
        LangGraphKey.EVENT: LangGraphEvent.TOOL_START,
        LangGraphKey.NAME: "retrieve_kb",
        LangGraphKey.DATA: {
            "input": {"query": "腾讯2024年报 业绩", "top_k": 8}
        },
    }
    events = _convert_event(item)
    assert len(events) == 1
    status = events[0]
    assert status.detail is not None
    assert "腾讯2024年报" in status.detail
    assert "query=" in status.detail


def test_convert_tool_start_search_web_carries_queries_detail():
    """search_web on_tool_start → SSEStatusEvent.detail 含 queries。"""
    from src.services.agent_service import _convert_event
    from src.agents.graph.state import LangGraphEvent, LangGraphKey

    item = {
        LangGraphKey.EVENT: LangGraphEvent.TOOL_START,
        LangGraphKey.NAME: "search_web",
        LangGraphKey.DATA: {"input": {"queries": ["腾讯 2023 年报", "腾讯 2025 年报"]}},
    }
    events = _convert_event(item)
    assert len(events) == 1
    status = events[0]
    assert status.detail is not None
    assert "腾讯 2023 年报" in status.detail


def test_convert_tool_start_ask_user_no_detail():
    """ask_user 等不展示的工具不填 detail（保持原行为）。"""
    from src.services.agent_service import _convert_event
    from src.agents.graph.state import LangGraphEvent, LangGraphKey

    item = {LangGraphKey.EVENT: LangGraphEvent.TOOL_START, LangGraphKey.NAME: "ask_user"}
    assert _convert_event(item) == []
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/services/test_agent_service.py -v`
Expected: FAIL（detail 当前为 None）

- [ ] **Step 3: 实现 detail 填充**

`src/services/agent_service.py` TOOL_START 分支改造（205-221 行）：

```python
    if kind == LangGraphEvent.TOOL_START:
        if name == "search_web":
            detail = _tool_detail_from_input(item, "queries")
            return [
                SSEStatusEvent(
                    SSEInteractionTexts.STAGE_WEB_SEARCH,
                    SSEInteractionTexts.WEB_SEARCH_STATUS_START,
                    detail=detail,
                )
            ]
        if name == "retrieve_kb":
            detail = _tool_detail_from_input(item, "query")
            return [
                SSEStatusEvent(
                    SSEInteractionTexts.STAGE_RETRIEVE,
                    SSEInteractionTexts.AGENT_STATUS_RETRIEVING,
                    detail=detail,
                )
            ]
        # ask_user 等其他工具不发状态（composer 接管输入区）
        return []
```

模块级新增辅助函数（放 `_convert_event` 上方）：

```python
def _tool_detail_from_input(item: dict, key: str) -> str | None:
    """从 LangGraph on_tool_start 事件的 data.input 提取工具入参为可读 detail。

    Args:
        item: astream_events 事件 dict
        key: 要展示的入参键（retrieve_kb→"query"；search_web→"queries"）

    Returns:
        detail 字符串（如 'query="腾讯2024年报"' / 'queries=["腾讯 2023 年报", ...]'）；
        input 缺失或 key 不在 input 时返回 None
    """
    data = item.get(LangGraphKey.DATA) or {}
    tool_input = data.get("input") or {}
    if not isinstance(tool_input, dict):
        return None
    value = tool_input.get(key)
    if value is None:
        return None
    if isinstance(value, list):
        shown = [str(v)[:40] for v in value]
        return f"{key}={shown!r}"
    text = str(value)[:40]
    return f"{key}={text}"
```

（若 `LangGraphKey.DATA` 的 on_tool_start input 实际结构为 `data.input`，用此实现；若事件结构不同（如 input 在别处），以运行时 astream_events 实测为准调整取值路径。）

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/services/test_agent_service.py -v`
Expected: 3 个新用例 PASS；既有 `test_dual_stream`（buffer round-trip）不受影响

- [ ] **Step 5: Commit**

```bash
git add src/services/agent_service.py tests/services/test_agent_service.py
git commit -m "feat: SSE 工具状态事件携带入参明细（detail=query/queries）"
```

---

### Task 2: 前端展示 detail

**Files:**
- Modify: `deploy/nginx/html/chat.html`（renderStatusTag + status 事件处理）

**Interfaces:**
- Consumes: Task 1 的 `SSEStatusEvent.detail`（在 SSE data 中：`{"stage": ..., "message": ..., "detail": ...}`）
- Produces: status-tag 展示"message + detail"

- [ ] **Step 1: renderStatusTag 支持 detail**

`deploy/nginx/html/chat.html` `renderStatusTag`（973-977 行）改签名并拼 detail：

```javascript
function renderStatusTag(stage, message, detail) {
  const div = document.createElement('div');
  div.className = 'status-tag';
  let text = escapeHtml(message);
  if (detail) {
    text += ' <span class="status-detail">' + escapeHtml(detail) + '</span>';
  }
  div.innerHTML = `<span class="status-dot"></span> ${text}`;
  return div;
}
```

CSS 补 `.status-detail`（在 428-440 行 status-tag/dot 样式后）：

```css
.status-detail { opacity: 0.75; font-size: 0.9em; margin-left: 4px; }
```

- [ ] **Step 2: status 事件处理传 detail**

`deploy/nginx/html/chat.html` `buildStreamHandlers` 的 `status` 回调（1533-1539 行，start/resume 流复用统一 handlers）——`renderStatusTag` 调用透传 `data.detail`：

```javascript
    status: (data) => {
      try {
        state.lastSeq = (data.seq !== undefined) ? data.seq : state.lastSeq;
        closeThinkRow();
        renderStatusTag(data.stage, data.message, data.detail);
      } catch (err) { /* ignore */ }
    },
```

**说明**：`data` 是后端 SSE `data:` 帧 JSON 解析对象，含 `{stage, message, seq, detail?}`（detail 仅非空时由后端序列化）；此处是 startStream 与 resumeStream 共用的统一回调，改一处两端生效。

- [ ] **Step 3: 手动/playwright 验证**

Run: playwright 打开 chat.html，发一条绑 KB query
Expected: 检索中显示"正在检索相关文档... query=腾讯2024年报 业绩"；联网中显示"正在联网搜索... queries=[...]"；无 detail 的工具不显示明细

- [ ] **Step 4: Commit**

```bash
git add deploy/nginx/html/chat.html
git commit -m "feat(ui): 状态事件展示工具调用明细（status-detail）"
```

---

### Task 3: 收尾质量门禁

**Files:**
- Modify: `docs/openspec/changes/sse-tool-detail/tasks.md`（勾选全部）
- Modify: `docs/agents/api_contract.md`（SSEStatusEvent 补 detail 说明）

**Interfaces:**
- Consumes: Task 1-2 产物

- [ ] **Step 1: 全量回归 + 门禁**

Run: `pytest tests/ -v && ruff check . && pyright src/`
Expected: 全过 / 无 error / 不新增 pyright error

- [ ] **Step 2: 契约文档同步**

`docs/agents/api_contract.md` 的 SSEStatusEvent 段补充 `detail` 字段说明（前端对接/历史回放用）——格式：`detail?: string`，retrieve_kb 为 `query=...`、search_web 为 `queries=[...]`，可空（向后兼容）。

- [ ] **Step 3: 勾选 change tasks + Commit**

修改 `docs/openspec/changes/sse-tool-detail/tasks.md` 全勾，然后：

```bash
git add docs/openspec/changes/sse-tool-detail/ docs/agents/api_contract.md
git commit -m "docs: sse-tool-detail 完成 + api_contract 补 SSEStatusEvent.detail"
```
