# 深度思考开关 + 前端设计稿落地 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现 `chat-thinking-toggle`（前端"深度思考"开关 → `enable_thinking` 动态控制），并按 `docs/design/pages/agentic-clarification.md` 设计稿对齐前端 8 处视觉差异。

**Architecture:** 深度思考是纯参数透传链路：`chat.html` 开关 → `/chat/stream?deep_thinking=` → `chat_stream` → `stream_chat` → `AgentState.deep_thinking` → agent 节点 `model.astream(..., extra_body={"enable_thinking": state.deep_thinking})`。已验证 langchain-openai 1.3.3 的 `_get_request_payload` 中 per-call kwargs 覆盖构造时的 `extra_body`。前端视觉落地仅改 `chat.html` 的 CSS/JS 与设计稿对齐，不涉及后端逻辑。

**Tech Stack:** FastAPI / LangGraph 1.2.9 / langchain-openai 1.3.3 / 原生 HTML+CSS+JS（单文件 chat.html）/ pytest / playwright-cli

## Global Constraints

- 不用三元表达式，写完整 if/else 结构
- 常量/文案进 `src/config/`（`prompts.py` 只放提示词；`const.py` 放常量）；`deep_thinking` 是 URL 参数，不入 const
- 测试 mock 外部依赖，不发起真实网络调用
- 所有函数写 docstring，注释用中文；写当前状态，不写变更历史
- 版本锁死：langgraph==1.2.9、langchain-core==1.4.8、langchain-openai==1.3.3
- 层间调用规则：api/ 不得直接调 infra/ 或 config/（经 services/）
- 前端设计稿权威：`docs/design/pages/agentic-clarification.md` + `docs/design/agentic-clarification-mockup.html`
- 前端单文件无单测框架，前端任务用 `playwright-cli` 验证
- 每次改 `frontend/chat.html` 后同步 `deploy/nginx/html/chat.html`（md5 必须一致）

## File Structure

- `src/agents/graph/state.py` — `AgentState` 加 `deep_thinking` 输入字段 + `make_initial_state` 参数（Task 1）
- `src/api/chat.py` — `chat_stream` 加 Query 参数 + `_stream_rag_response` 透传（Task 2）
- `src/services/agent_service.py` — `stream_chat` 加参数 → `make_initial_state`（Task 3）
- `src/agents/graph/agent_node.py` — `agent_model` 的 `model.astream` 加 `extra_body`（Task 4）
- `tests/services/test_agent_service.py`、`tests/api/test_chat.py`、`tests/agents/graph/test_agent_node.py` — 测试（Task 5）
- `frontend/chat.html` + `deploy/nginx/html/chat.html` — 深度思考开关 + 设计稿视觉落地（Task 6-7）
- `docs/agents/api_contract.md` — 契约确认（Task 5，deep_thinking 参数已在前面文档更新中补齐）

---

### Task 1: AgentState 加 deep_thinking 字段

**Files:**
- Modify: `src/agents/graph/state.py:13-55`
- Test: `tests/agents/graph/test_agent_node.py`

**Interfaces:**
- Consumes: 无（新字段）
- Produces: `AgentState.deep_thinking: bool = False`；`AgentState.make_initial_state(session_id, kb_id, query, history, deep_thinking=False)`——Task 3 调用

- [ ] **Step 1: 写失败测试**

在 `tests/agents/graph/test_agent_node.py` 追加：

```python
def test_make_initial_state_deep_thinking_default_false():
    state = AgentState.make_initial_state("s1", "kb1", "q", [])
    assert state.deep_thinking is False


def test_make_initial_state_deep_thinking_true():
    state = AgentState.make_initial_state("s1", "kb1", "q", [], deep_thinking=True)
    assert state.deep_thinking is True
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/agents/graph/test_agent_node.py -v -k deep_thinking`
Expected: FAIL（`AgentState` 无 `deep_thinking` 字段 / `make_initial_state` 不接受参数）

- [ ] **Step 3: 实现**

`src/agents/graph/state.py`，在 `AgentState` 输入区（`trace_id` 之后）加字段：

```python
    deep_thinking: bool = False  # 深度思考开关（来源：/chat/stream?deep_thinking；用途：agent LLM enable_thinking 参数）
```

`make_initial_state` 签名改为：

```python
    @classmethod
    def make_initial_state(
        cls, session_id, kb_id, query, history, deep_thinking=False
    ):
        """创建图初始状态，只设输入字段，中间态/输出由各节点填充。

        Args:
            session_id: 会话 ID
            kb_id: 知识库 ID
            query: 用户查询文本
            history: 对话历史列表
            deep_thinking: 深度思考开关（默认 False），传给 agent LLM enable_thinking
        """
        return cls(
            session_id=session_id,
            kb_id=kb_id,
            query=query,
            _history=history,
            deep_thinking=deep_thinking,
        )
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/agents/graph/test_agent_node.py -v -k deep_thinking`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/agents/graph/state.py tests/agents/graph/test_agent_node.py
git commit -m "feat(state): AgentState 加 deep_thinking 输入字段"
```

---

### Task 2: api/chat.py 透传 deep_thinking 参数

**Files:**
- Modify: `src/api/chat.py:275-341`（`chat_stream`）、`src/api/chat.py:150-189`（`_stream_rag_response`）
- Test: `tests/api/test_chat.py`

**Interfaces:**
- Consumes: 无
- Produces: `chat_stream(..., deep_thinking: bool = Query(False))`；`_stream_rag_response(svc, kb_id, session_id, query, user_id="", deep_thinking=False)` → 透传给 `svc.agent_service.stream_chat(kb_id, session_id, query, deep_thinking)`——Task 3 的 `stream_chat` 签名

- [ ] **Step 1: 写失败测试**

`tests/api/test_chat.py` 中 `test_chat_stream` 系列。先读现有测试确认结构，然后追加断言 deep_thinking 透传。mock `svc.agent_service.stream_chat` 记录调用参数：

```python
def test_chat_stream_passes_deep_thinking(monkeypatch):
    captured = {}

    async def fake_stream_chat(kb_id, session_id, query, deep_thinking=False):
        captured["deep_thinking"] = deep_thinking
        yield to_sse(SSETokenEvent("ok"))

    svc = Mock()
    svc.agent_service.stream_chat = fake_stream_chat
    ...
    # 用 TestClient 调 GET /api/chat/stream?deep_thinking=true，断言 captured["deep_thinking"] is True
```

（实现时按现有 `test_chat.py` 的 mock 方式对齐——若现有测试已 mock `stream_chat` 签名，需同步补 `deep_thinking` 默认参数。）

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/api/test_chat.py -v -k deep_thinking`
Expected: FAIL（TypeError: stream_chat 不接受 deep_thinking / 参数未透传）

- [ ] **Step 3: 实现**

`src/api/chat.py` `chat_stream` 签名加参数（`query` 之后）：

```python
    query: str = Query(..., description="User question"),
    deep_thinking: bool = Query(False, description="深度思考开关（enable_thinking）"),
    svc: AppService = Depends(get_app_service),
```

`_stream_rag_response` 签名与调用处：

```python
async def _stream_rag_response(
    svc: AppService,
    kb_id: str,
    session_id: str,
    query: str,
    user_id: str = "",
    deep_thinking: bool = False,
) -> AsyncGenerator[str, None]:
```

调用处（`_stream_with_lock` 内）：

```python
            async for event in _stream_rag_response(
                svc, kb_id, session_id, query, user_id, deep_thinking
            ):
```

`_stream_rag_response` 内部调用：

```python
        async for event in svc.agent_service.stream_chat(
            kb_id, session_id, query, deep_thinking
        ):
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/api/test_chat.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/api/chat.py tests/api/test_chat.py
git commit -m "feat(api): /chat/stream 支持 deep_thinking 参数"
```

---

### Task 3: agent_service.stream_chat 参数透传

**Files:**
- Modify: `src/services/agent_service.py:322-341`（`stream_chat` 签名 + `make_initial_state` 调用）
- Test: `tests/services/test_agent_service.py`

**Interfaces:**
- Consumes: Task 1 的 `make_initial_state(..., deep_thinking=False)`、Task 2 的调用方传 `deep_thinking`
- Produces: `stream_chat(kb_id, session_id, query, deep_thinking=False)` → 传给 `AgentState.make_initial_state`

- [ ] **Step 1: 写失败测试**

`tests/services/test_agent_service.py` 的 `test_stream_chat_emits_full_event_sequence` 现有 mock `fake_astream`。追加一个断言初始 state 的 deep_thinking：

```python
@pytest.mark.asyncio
async def test_stream_chat_passes_deep_thinking():
    service, _ = _make_service()
    seen = {}

    async def fake_astream(initial_state, version):
        seen["deep_thinking"] = initial_state.deep_thinking
        yield _chat_model_end_item("gpt-4o")
        yield _finalize_end_item("答案", has_contexts=True)

    service._graph = Mock()
    service._graph.astream_events = fake_astream

    async for _ in service.stream_chat("kb1", "session1", "q", deep_thinking=True):
        pass
    assert seen["deep_thinking"] is True
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/services/test_agent_service.py -v -k deep_thinking`
Expected: FAIL（TypeError: stream_chat 不接受 deep_thinking）

- [ ] **Step 3: 实现**

`src/services/agent_service.py` `stream_chat` 签名：

```python
    async def stream_chat(
        self,
        kb_id: str,
        session_id: str,
        query: str,
        deep_thinking: bool = False,
    ) -> AsyncGenerator[SSEEvent, None]:
```

docstring Args 追加：

```
        deep_thinking: 深度思考开关（默认 False）；为 True 时 agent LLM 以思考模式调用（enable_thinking）
```

`make_initial_state` 调用处：

```python
        initial_state = AgentState.make_initial_state(
            session_id, kb_id, query, history, deep_thinking
        )
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/services/test_agent_service.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/services/agent_service.py tests/services/test_agent_service.py
git commit -m "feat(service): stream_chat 透传 deep_thinking 到 AgentState"
```

---

### Task 4: agent 节点 astream 传 extra_body

**Files:**
- Modify: `src/agents/graph/agent_node.py:81`（`model.astream` 调用）
- Test: `tests/agents/graph/test_agent_node.py`

**Interfaces:**
- Consumes: Task 1 的 `state.deep_thinking`
- Produces: agent LLM 每次调用携带 `extra_body={"enable_thinking": <bool>}`（思考模式开关），思考内容经 `reasoning_content` 返回但被 langchain 丢弃，前端不展示思考

- [ ] **Step 1: 写失败测试**

`tests/agents/graph/test_agent_node.py` 中 agent_model 测试，断言 `astream` 收到的 kwargs 含 extra_body。先读现有测试确认 `model` mock 方式，然后追加：

```python
async def test_agent_model_passes_enable_thinking(mocker):
    # mock model.astream 为异步生成器，记录 kwargs
    captured = {}

    async def fake_astream(messages, **kwargs):
        captured["extra_body"] = kwargs.get("extra_body")
        yield AIMessageChunk(content="")

    llm = Mock()
    llm.bind_tools.return_value = Mock()
    llm.bind_tools.return_value.astream = fake_astream
    node = make_agent_model_node(llm, [], Mock())

    state = AgentState(query="q", deep_thinking=True, _history=[])
    await node(state)
    assert captured["extra_body"] == {"enable_thinking": True}
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/agents/graph/test_agent_node.py -v -k enable_thinking`
Expected: FAIL（astream 未传 extra_body）

- [ ] **Step 3: 实现**

`src/agents/graph/agent_node.py` 第 81 行：

```python
        async for chunk in model.astream(
            messages, extra_body={"enable_thinking": state.deep_thinking}
        ):
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/agents/graph/test_agent_node.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/agents/graph/agent_node.py tests/agents/graph/test_agent_node.py
git commit -m "feat(agent): agent LLM 按 deep_thinking 动态传 enable_thinking"
```

---

### Task 5: 后端回归 + 契约确认

**Files:**
- Modify: `tests/services/test_agent_service.py`、`tests/api/test_chat.py`（若有签名断言需补 deep_thinking 默认参数）
- Read: `docs/agents/api_contract.md`（deep_thinking 参数已在前置文档更新中补齐，此处确认）

**Interfaces:**
- Consumes: Task 1-4 全部改动
- Produces: 全量测试通过；契约文档与实现一致

- [ ] **Step 1: 跑全量测试**

Run: `python -m pytest tests/ -q`
Expected: 全 PASS（若现有测试 mock `stream_chat`/`chat_stream` 缺 `deep_thinking` 参数，补默认参数使 mock 兼容）

- [ ] **Step 2: lint**

Run: `ruff check src/ tests/`
Expected: All checks passed

- [ ] **Step 3: 确认契约**

`docs/agents/api_contract.md` 2.3.1 参数表应已含：

```
| `deep_thinking` | 深度思考开关（可选，默认 `false`）：`true` 时 agent 主 LLM 以思考模式调用（`enable_thinking=true`）；`false` 显式关闭。来自 `chat-thinking-toggle` capability |
```

若无则补上（一行，中文）。

- [ ] **Step 4: 提交**

```bash
git add tests/ docs/agents/api_contract.md
git commit -m "test: 后端深度思考链路回归 + 契约确认"
```

---

### Task 6: 前端深度思考开关

**Files:**
- Modify: `frontend/chat.html`、`deploy/nginx/html/chat.html`（同步）
- 设计稿: `docs/design/agentic-clarification-mockup.html`（thinking-toggle 组件）

**Interfaces:**
- Consumes: 后端 `/chat/stream?deep_thinking=` 参数（Task 2 已实现）
- Produces: 输入区"深度思考"chip 开关，默认不选中；选中 → 请求带 `deep_thinking=true`；未选中 → `deep_thinking=false`

- [ ] **Step 1: 加 HTML 结构**

`frontend/chat.html` 输入区（`chat-footer-inner` 内，`chatInput` 前）加：

```html
<button type="button" id="thinkingToggle" class="thinking-chip" aria-pressed="false" title="深度思考">
  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 1 1 7.072 0l-.548.547A3.374 3.374 0 0 0 14 18.469V19a2 2 0 1 1-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z"/></svg>
  <span>深度思考</span>
</button>
```

- [ ] **Step 2: 加 CSS**

CSS（`chat-footer-inner` 样式附近）加：

```css
  .thinking-chip {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    height: 28px;
    padding: 0 12px;
    border: none;
    border-radius: 24px;
    background: transparent;
    color: var(--text-secondary);
    font-size: 12px;
    font-weight: 500;
    font-family: var(--font-body);
    cursor: pointer;
    transition: background var(--transition), color var(--transition);
    flex-shrink: 0;
  }
  .thinking-chip:hover { background: var(--bg); color: var(--text); }
  .thinking-chip.active { background: var(--primary); color: #fff; }
  .thinking-chip:focus-visible { box-shadow: 0 0 0 3px rgba(59,130,246,0.3); outline: none; }
```

- [ ] **Step 3: 加 JS 状态与切换**

`chat.html` script 中，state 定义区加：

```js
  deepThinking: false,
```

URL 构建处（`const params = new URLSearchParams({...})`）加：

```js
    deep_thinking: state.deepThinking,
```

DOM 初始化处（`state.sessionId = getOrCreateSessionId()` 附近）绑定 toggle：

```js
  const thinkingToggle = $('thinkingToggle');
  if (thinkingToggle) {
    thinkingToggle.addEventListener('click', () => {
      state.deepThinking = !state.deepThinking;
      thinkingToggle.classList.toggle('active', state.deepThinking);
      thinkingToggle.setAttribute('aria-pressed', String(state.deepThinking));
    });
  }
```

- [ ] **Step 4: 同步部署副本并验证**

```bash
cp frontend/chat.html deploy/nginx/html/chat.html
md5sum frontend/chat.html deploy/nginx/html/chat.html
```

Expected: 两个 md5 相同

playwright 验证：

```bash
playwright-cli -s=think open "http://localhost/?kb_id=b9e74e820e0a4bad8472304446e54f5c"
playwright-cli -s=think click e<thinkingToggle ref>
playwright-cli -s=think --raw eval "document.getElementById('thinkingToggle').classList.contains('active')"
```

Expected: 点击后 `active` 为 true；再次点击为 false

- [ ] **Step 5: 提交**

```bash
git add frontend/chat.html deploy/nginx/html/chat.html
git commit -m "feat(frontend): 输入区加深度思考开关"
```

---

### Task 7: 前端设计稿视觉落地（8 处差异）

**Files:**
- Modify: `frontend/chat.html`、`deploy/nginx/html/chat.html`（同步）
- 设计稿: `docs/design/pages/agentic-clarification.md`（组件规格）、`docs/design/agentic-clarification-mockup.html`

**Interfaces:**
- Consumes: 现有组件实现（composer/abstention/feedback/model_info/citation/status-tag）
- Produces: 8 处视觉差异与设计稿对齐

按设计稿逐一改（每项一个子步骤，改完一次 playwright 验证）：

- [ ] **Step 1: status-tag 加呼吸圆点**

CSS 加：

```css
  .status-dot {
    width: 6px; height: 6px; border-radius: 50%;
    background: var(--primary);
    animation: statusPulse 1.2s ease-in-out infinite;
  }
  @keyframes statusPulse { 0%,100% { opacity: 1; } 50% { opacity: 0.35; } }
```

`renderStatusTag`（约 L657）改为：

```js
function renderStatusTag(stage, message) {
  const div = document.createElement('div');
  div.className = 'status-tag';
  div.innerHTML = `<span class="status-dot"></span> ${escapeHtml(message)}`;
  chatContainer.appendChild(div);
  scrollToBottom();
}
```

- [ ] **Step 2: composer 提交按钮改 primary**

CSS `.clarification-submit` 的 `background: var(--accent)` → `var(--primary)`；hover `var(--accent-hover)` → `var(--primary-hover)`。

- [ ] **Step 3: composer 卡片头加 SVG 图标**

`renderComposer` 的 `composer.innerHTML` 中 `clarification-label` 改为带图标：

```js
      <div class="clarification-label">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>
        需要补充信息
      </div>
```

CSS `.clarification-label` 加 `display: inline-flex; align-items: center; gap: 5px;`。

- [ ] **Step 4: composer 自定义输入虚线边框**

CSS `.clarification-section .clarification-input` 的 `border: 1px solid var(--border)` → `border: 1px dashed var(--border)`；`:focus` 时改实线（当前 focus 有 `border-color: var(--primary)`，补 `border-style: solid;`）。

- [ ] **Step 5: abstention amber 提示条 + 橙按钮**

CSS `.abstention-tag` 由 `cursor: default` 改为：

```css
  .abstention-tag {
    align-self: flex-start;
    display: inline-flex;
    align-items: center;
    gap: 10px;
    background: #FFFBEB;
    border: 1px solid #FDE68A;
    border-radius: var(--radius-lg);
    padding: 12px 16px;
    font-size: 13px;
    color: #92400E;
  }
```

`.abstention-btn` 的 border/color `var(--primary)` → `var(--accent)`；hover `var(--primary-light)` → `var(--accent)` + `color: #fff`。

`renderAbstention` 的提示文本前加信息图标：

```js
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>
```

- [ ] **Step 6: feedback 改 SVG 图标按钮**

`appendFeedback` 的 `row.innerHTML` 中 emoji 👍/👎 替换为 SVG（thumb up/down，viewBox 24）：

```js
  row.innerHTML = `
    <button class="feedback-btn" type="button" data-rating="positive" aria-label="有帮助">
      <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 9V5a3 3 0 0 0-3-3l-4 9v11h11.28a2 2 0 0 0 2-1.7l1.38-9a2 2 0 0 0-2-2.3zM7 22H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h3"/></svg>
    </button>
    <button class="feedback-btn" type="button" data-rating="negative" aria-label="无帮助">
      <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10 15v4a3 3 0 0 0 3 3l4-9V2H5.72a2 2 0 0 0-2 1.7l-1.38 9a2 2 0 0 0 2 2.3zm7 0h3a2 2 0 0 0 2-2V6a2 2 0 0 0-2-2h-3"/></svg>
    </button>
  `;
```

CSS `.feedback-btn` 改为图标按钮（28×28，居中）：

```css
  .feedback-btn {
    width: 28px; height: 28px;
    padding: 0;
    border: none;
    background: transparent;
    border-radius: 6px;
    display: flex; align-items: center; justify-content: center;
    color: var(--text-muted);
    cursor: pointer;
    transition: background var(--transition), color var(--transition);
  }
  .feedback-btn:hover { background: var(--primary-light); color: var(--primary); }
```

- [ ] **Step 7: model_info fallback 徽标 + 左对齐**

CSS `.model-info` 的 `align-self: flex-end` → `flex-start`；`.fallback-badge` 改为 amber 胶囊：

```css
  .model-info .fallback-badge {
    background: #FFFBEB;
    border: 1px solid #FDE68A;
    color: #92400E;
    font-size: 10px;
    padding: 1px 6px;
    border-radius: 999px;
    margin-left: 6px;
  }
```

`renderModelInfo` 的 fallback 文本 `⚡ 已降级` 改为 `fallback`：

```js
    div.innerHTML += ' <span class="fallback-badge">fallback</span>';
```

- [ ] **Step 8: citation 加 hover**

CSS `.citation-item` 加：

```css
    transition: border-color var(--transition), box-shadow var(--transition);
  }
  .citation-item:hover { border-color: var(--primary); box-shadow: var(--shadow); }
```

- [ ] **Step 9: 同步部署副本 + 验证**

```bash
cp frontend/chat.html deploy/nginx/html/chat.html
md5sum frontend/chat.html deploy/nginx/html/chat.html
```

Expected: md5 相同

playwright 验证清单（对照 mockup）：

```bash
playwright-cli -s=design2 open "http://localhost/design-preview.html"   # 预览已迁移到 docs/design，需临时复制到 nginx 或直接对照代码
playwright-cli -s=chat open "http://localhost/?kb_id=b9e74e820e0a4bad8472304446e54f5c"
# 逐项核对：状态圆点 / composer 蓝提交 / 卡片头图标 / 虚线输入 / abstention amber / feedback SVG / model_info 徽标 / citation hover
```

Expected: 8 处与设计稿一致

- [ ] **Step 10: 提交**

```bash
git add frontend/chat.html deploy/nginx/html/chat.html
git commit -m "feat(frontend): 对齐设计稿 8 处组件视觉"
```

---

### Task 8: 端到端验证

**Files:**
- 无代码改动，验证
- Read: `docs/openspec/changes/agentic-clarification/specs/chat-thinking-toggle/spec.md`

**Interfaces:**
- Consumes: Task 1-7 全部完成
- Produces: 全链路验证通过（深度思考开关 + 既有澄清/引用/转人工/反馈不回归）

- [ ] **Step 1: 后端全量回归**

Run: `python -m pytest tests/ -q && ruff check src/ tests/`
Expected: 全 PASS + All checks passed

- [ ] **Step 2: 深度思考开关端到端（前端）**

```bash
playwright-cli -s=e2e open "http://localhost/?kb_id=b9e74e820e0a4bad8472304446e54f5c"
# 1. 默认不选中 → 发"腾讯2024年的营收是多少" → 回答正常（非思考模式）
# 2. 勾选深度思考 → 发"腾讯2024年的营收是多少" → 回答正常（思考模式，耗时略增；前端看不到思考文本）
# 3. 检查请求 URL 含 deep_thinking=true/false
```

Expected: 开关状态与请求参数一致；两种模式回答均正常

- [ ] **Step 3: 既有功能不回归（前端）**

playwright 验证：澄清 composer（"营收是多少"→ ask_user 卡片 → 提交 → 同流续答）、citation 编号、abstention 转人工、feedback 按钮、model_info。

Expected: 全部正常（对照 Task 7 验证清单）

- [ ] **Step 4: 提交收尾**

```bash
git status --short
git add -A
git commit -m "chore: 深度思考开关与设计稿落地收尾"
```

---

## Self-Review

**Spec 覆盖：**
- `chat-thinking-toggle/spec.md` 3 条 Requirement → Task 1-4（后端透传）+ Task 6（前端开关）；"思考不展示"为既有行为（langchain 丢弃 reasoning_content），无额外实现，Task 4 备注
- tasks.md 8.1→Task 1、8.2→Task 2、8.3→Task 3、8.4→Task 4、8.5→Task 6-7、8.6→Task 5、8.7→Task 5 Step 3（契约已前置补齐）
- 设计稿审查 8 处差异 → Task 7（Step 1-8 逐一对应）

**Placeholder scan：** 无 TBD/TODO；每个代码步骤含实际代码；测试含具体断言；前端验证用具体 playwright 命令。

**Type consistency：**
- `deep_thinking: bool` 全程一致（AgentState → stream_chat → chat_stream → URL 参数）
- `extra_body={"enable_thinking": state.deep_thinking}` 与 chat-thinking-toggle spec 一致
- `make_initial_state(..., deep_thinking=False)` 签名在 Task 1 定义、Task 3 调用，参数名一致
- 前端 `state.deepThinking`（JS camelCase）与 URL 参数 `deep_thinking`（后端 snake_case）在 Task 6 Step 3 明确映射
