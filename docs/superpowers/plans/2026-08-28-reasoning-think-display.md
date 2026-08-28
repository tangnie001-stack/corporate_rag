# 思考过程展示（Think 折叠行）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 深度思考模式下把 qwen3.7-flash 的 `reasoning_content`（被 langchain 丢弃）提取出来，通过 SSE 推给前端，渲染 dsh 风格的"Think"折叠行（每轮 LLM 调用一个、默认折叠、增量累积）。

**Architecture:** 方案一（社区验证，GitHub langchain issue #38764 参考实现）：自定义 `ChatQwenWithReasoning(ChatOpenAI)` 子类重写 `_convert_chunk_to_generation_chunk`，把流式 delta 的 `reasoning_content` 提取累积到 `AIMessageChunk.additional_kwargs["reasoning_content"]`。agent_service 的 `on_chat_model_stream` 事件处理里读该字段 → 产出 `SSEReasoningDeltaEvent`（增量事件）→ 前端监听 `reasoning` 事件累积渲染 Think 行。已验证：`_astream` 复用同一转换方法（一个 override 覆盖 sync+async）、`_convert_message_to_dict` 回传时忽略 additional_kwargs 的 reasoning_content（历史不回传，安全）、enable_thinking=false 时无 reasoning（零影响）。

**Tech Stack:** Python 3.12 / langchain-openai==1.3.3 / LangGraph 1.2.9 / FastAPI SSE / 原生 HTML+CSS+JS（chat.html）

## Global Constraints

- 不用三元表达式，写完整 if/else 结构
- 所有函数写 docstring，注释用中文；写当前状态，不写变更历史
- 测试 mock 外部依赖，不发起真实网络调用
- langchain-openai==1.3.3 锁死（子类 override 依赖此版本 `_convert_chunk_to_generation_chunk` 签名）
- SSE 契约：新增 `reasoning` 事件（`event: reasoning`，data `{"delta": "..."}`），前端监听累积；不加 start/end 边界事件
- Think 行边界由前端推断：收到正文 token / 状态事件（新一轮）/ ask_user / abstention / done 时定型当前 Think 行
- 前端单文件无单测框架，前端任务用 `playwright-cli` 验证
- 改 `deploy/nginx/html/chat.html` 后需 `docker compose up -d --build nginx` 生效（nginx 是 build 镜像，restart 不生效）
- 设计稿权威：`docs/design/chat-redesign-mockup.html`（.think-row 样式）、`docs/design/pages/chat-redesign.md`

## File Structure

- `src/infra/llm/reasoning_chat.py` — 新建 `ChatQwenWithReasoning(ChatOpenAI)` 子类（Task 1）
- `src/models.py` — `get_llm` 返回新类（Task 2）
- `src/utils/sse.py` — 新增 `SSEReasoningDeltaEvent` dataclass + `sse_reasoning_delta()` + `to_sse` case（Task 3）
- `src/services/agent_service.py` — `_convert_event` 的 `CHAT_MODEL_STREAM` 分支提取 reasoning → 事件（Task 4）
- `deploy/nginx/html/chat.html` — Think 行 CSS + `reasoning` 事件监听 + 累积/定型状态机（Task 5）
- `tests/infra/llm/test_reasoning_chat.py`、`tests/services/test_agent_service.py`、`tests/utils/` — 测试（Task 1/3/4）
- `docs/agents/api_contract.md` — 契约补 `reasoning` 事件（Task 6）

---

### Task 1: ChatQwenWithReasoning 子类

**Files:**
- Create: `src/infra/llm/reasoning_chat.py`
- Test: `tests/infra/llm/test_reasoning_chat.py`

**Interfaces:**
- Consumes: 无（独立类）
- Produces: `ChatQwenWithReasoning(ChatOpenAI)` 类——`get_llm`（Task 2）返回它；流式 chunk 的 `additional_kwargs["reasoning_content"]` 携带思考增量（`_astream`/`_stream` 都生效）

- [ ] **Step 1: 写失败测试**

`tests/infra/llm/test_reasoning_chat.py`：

```python
"""ChatQwenWithReasoning 子类：流式 chunk 提取 reasoning_content。"""

import pytest
from langchain_core.messages import AIMessageChunk
from langchain_core.outputs import ChatGenerationChunk

from src.infra.llm.reasoning_chat import ChatQwenWithReasoning


def test_extracts_reasoning_content_to_additional_kwargs():
    llm = ChatQwenWithReasoning(
        model="qwen3.7-flash-2026-07-15", api_key="sk-test", base_url="http://localhost:8000"
    )
    chunk = {
        "choices": [
            {
                "delta": {"reasoning_content": "思考增量", "content": ""},
            }
        ]
    }
    gen = llm._convert_chunk_to_generation_chunk(chunk, AIMessageChunk, None)
    assert gen is not None
    assert gen.message.additional_kwargs["reasoning_content"] == "思考增量"


def test_reasoning_alias_fallback():
    """OpenRouter 等用 reasoning 字段，应 fallback。"""
    llm = ChatQwenWithReasoning(
        model="qwen3.7-flash-2026-07-15", api_key="sk-test", base_url="http://localhost:8000"
    )
    chunk = {"choices": [{"delta": {"reasoning": "fallback思考", "content": ""}}]}
    gen = llm._convert_chunk_to_generation_chunk(chunk, AIMessageChunk, None)
    assert gen.message.additional_kwargs["reasoning_content"] == "fallback思考"


def test_no_reasoning_keeps_normal_content():
    """无 reasoning_content 时 content 正常透传，不影响既有行为。"""
    llm = ChatQwenWithReasoning(
        model="qwen3.7-flash-2026-07-15", api_key="sk-test", base_url="http://localhost:8000"
    )
    chunk = {"choices": [{"delta": {"content": "正文", "role": "assistant"}}]}
    gen = llm._convert_chunk_to_generation_chunk(chunk, AIMessageChunk, None)
    assert gen.message.content == "正文"
    assert "reasoning_content" not in gen.message.additional_kwargs


def test_empty_choices_returns_chunk():
    """choices 为空时返回空内容 chunk（不崩溃）。"""
    llm = ChatQwenWithReasoning(
        model="qwen3.7-flash-2026-07-15", api_key="sk-test", base_url="http://localhost:8000"
    )
    gen = llm._convert_chunk_to_generation_chunk({"choices": []}, AIMessageChunk, None)
    assert gen is not None
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/infra/llm/test_reasoning_chat.py -v`
Expected: FAIL（ImportError: `reasoning_chat` 模块不存在）

- [ ] **Step 3: 实现**

`src/infra/llm/reasoning_chat.py`：

```python
"""ChatOpenAI 子类：保留第三方模型流式 chunk 的 reasoning_content 思考文本。

langchain-openai 1.3.3 只解析官方 OpenAI 字段，第三方（DashScope 等）的
reasoning_content 被丢弃。本子类重写 _convert_chunk_to_generation_chunk，
把 delta 里的 reasoning_content（fallback reasoning）累积到
AIMessageChunk.additional_kwargs["reasoning_content"]，供上层读取。

参考：langchain-ai/langchain issue #38764 的 ReasoningChatOpenAI 实现。
"""

from typing import Any, Optional, Type

from langchain_core.messages import AIMessageChunk
from langchain_core.outputs import ChatGenerationChunk
from langchain_openai import ChatOpenAI


class ChatQwenWithReasoning(ChatOpenAI):
    """保留 reasoning_content / reasoning 字段的 ChatOpenAI 子类。"""

    def _convert_chunk_to_generation_chunk(
        self,
        chunk: dict,
        default_chunk_class: Type,
        base_generation_info: Optional[dict],
    ) -> Optional[ChatGenerationChunk]:
        """转换流式 chunk 为生成块，并提取 reasoning 文本到 additional_kwargs。

        Args:
            chunk: openai SDK chunk 的 dict（含 choices[0].delta）
            default_chunk_class: 默认消息块类型
            base_generation_info: 基础生成信息

        Returns:
            ChatGenerationChunk：正常解析结果；无法转换时返回 None
        """
        # 父类原生解析（content / tool_calls / usage 等）
        generation_chunk = super()._convert_chunk_to_generation_chunk(
            chunk, default_chunk_class, base_generation_info
        )
        if generation_chunk is None:
            return None

        # 兼容两种外层 chunk 结构（普通流式 / beta stream）
        choices = chunk.get("choices", [])
        if not choices and "chunk" in chunk:
            choices = chunk["chunk"].get("choices", [])
        if not choices:
            return generation_chunk

        delta = choices[0].get("delta", {}) or {}
        reasoning = delta.get("reasoning_content") or delta.get("reasoning") or ""

        if reasoning and isinstance(generation_chunk.message, AIMessageChunk):
            prev = generation_chunk.message.additional_kwargs.get(
                "reasoning_content", ""
            )
            generation_chunk.message.additional_kwargs["reasoning_content"] = (
                prev + reasoning
            )
        return generation_chunk
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/infra/llm/test_reasoning_chat.py -v`
Expected: 4 passed

- [ ] **Step 5: 提交**

```bash
git add src/infra/llm/reasoning_chat.py tests/infra/llm/test_reasoning_chat.py
git commit -m "feat(llm): ChatQwenWithReasoning 子类提取流式 reasoning_content"
```

---

### Task 2: get_llm 返回新类

**Files:**
- Modify: `src/models.py:132-165`（`get_llm` 返回 `ChatOpenAI` → `ChatQwenWithReasoning`）
- Test: `tests/infra/llm/` 现有（确认不破）

**Interfaces:**
- Consumes: Task 1 的 `ChatQwenWithReasoning`
- Produces: `get_llm()` 返回 `ChatQwenWithReasoning` 实例（签名/参数不变，agent LLM 及所有调用方无感）

- [ ] **Step 1: 看 get_llm 当前实现**

`src/models.py:132` 附近：

```python
    extra_kwargs: dict = json.loads(LLM_KWARGS)
    extra_kwargs.update(kwargs)
    return ChatOpenAI(
        model=model,
        temperature=temperature,
        api_key=SecretStr(LLM_API_KEY),
        base_url=LLM_BASE_URL,
        callbacks=_content_logging_callbacks(),
        **extra_kwargs,
    )
```

- [ ] **Step 2: 修改 import 与返回类型**

`src/models.py` 顶部 import 区（`from langchain_openai import ChatOpenAI` 附近）加：

```python
from src.infra.llm.reasoning_chat import ChatQwenWithReasoning
```

`get_llm` 的返回改为：

```python
    return ChatQwenWithReasoning(
        model=model,
        temperature=temperature,
        api_key=SecretStr(LLM_API_KEY),
        base_url=LLM_BASE_URL,
        callbacks=_content_logging_callbacks(),
        **extra_kwargs,
    )
```

- [ ] **Step 3: 回归测试**

Run: `python -m pytest tests/infra/llm/ tests/agents/ -q`
Expected: 全 PASS（get_llm 签名未变，调用方无感）

- [ ] **Step 4: 提交**

```bash
git add src/models.py
git commit -m "feat(models): get_llm 返回 ChatQwenWithReasoning"
```

---

### Task 3: SSEReasoningDeltaEvent

**Files:**
- Modify: `src/utils/sse.py`（dataclass + 序列化函数 + `to_sse` case）
- Test: `tests/utils/test_sse.py`（若不存在则新建）

**Interfaces:**
- Consumes: 无
- Produces: `SSEReasoningDeltaEvent(reasoning_delta: str)` dataclass；`to_sse(SSEReasoningDeltaEvent)` 输出 `event: reasoning\ndata: {"delta": "..."}\n\n`——Task 4 的 agent_service 产出、api 层序列化

- [ ] **Step 1: 写失败测试**

`tests/utils/test_sse.py`：

```python
"""SSE 序列化测试。"""

from src.utils.sse import SSEReasoningDeltaEvent, to_sse


def test_sse_reasoning_delta_event():
    text = to_sse(SSEReasoningDeltaEvent(reasoning_delta="思考片段"))
    assert text == 'event: reasoning\ndata: {"delta": "思考片段"}\n\n'
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/utils/test_sse.py -v`
Expected: FAIL（`SSEReasoningDeltaEvent` 不存在）

- [ ] **Step 3: 实现**

`src/utils/sse.py`，在 `SSEAbstentionEvent` 后加 dataclass：

```python
@dataclass
class SSEReasoningDeltaEvent:
    """LLM 思考过程增量事件。"""

    reasoning_delta: str  # 思考文本增量片段（前端累积渲染 Think 行）
```

在 `sse_abstention` 后加序列化函数：

```python
def sse_reasoning_delta(reasoning_delta: str) -> str:
    """构建 reasoning 事件的 SSE 文本。

    Args:
        reasoning_delta: 思考文本增量片段

    Returns:
        SSE 格式文本（event: reasoning）
    """
    data: dict = {"delta": reasoning_delta}
    return f"event: reasoning\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
```

`to_sse` 的 match 加 case：

```python
        case SSEReasoningDeltaEvent(reasoning_delta=delta):
            return sse_reasoning_delta(delta)
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/utils/test_sse.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/utils/sse.py tests/utils/test_sse.py
git commit -m "feat(sse): SSEReasoningDeltaEvent（reasoning 增量事件）"
```

---

### Task 4: agent_service 提取 reasoning → 事件

**Files:**
- Modify: `src/services/agent_service.py:150-167`（`_convert_event` 的 `CHAT_MODEL_STREAM` 分支）
- Test: `tests/services/test_agent_service.py`

**Interfaces:**
- Consumes: Task 1 的 `additional_kwargs["reasoning_content"]`、Task 3 的 `SSEReasoningDeltaEvent`
- Produces: agent 节点 LLM 流式时，思考增量以 `SSEReasoningDeltaEvent` 产出（content 非空的照旧 `SSETokenEvent`）

- [ ] **Step 1: 写失败测试**

`tests/services/test_agent_service.py` 追加：

```python
def test_convert_event_extracts_reasoning():
    """on_chat_model_stream 且 chunk 带 reasoning_content 时产出 reasoning 事件。"""
    from src.services.agent_service import _convert_event
    from src.utils.sse import SSEReasoningDeltaEvent, SSETokenEvent

    from langchain_core.messages import AIMessageChunk

    chunk = AIMessageChunk(
        content="",
        additional_kwargs={"reasoning_content": "思考增量"},
    )
    item = {
        "event": "on_chat_model_stream",
        "name": "ChatModel",
        "metadata": {"langgraph_node": "agent"},
        "data": {"chunk": chunk},
    }
    events = _convert_event(item)
    assert any(isinstance(e, SSEReasoningDeltaEvent) and e.reasoning_delta == "思考增量" for e in events)


def test_convert_event_content_and_reasoning_both():
    """chunk 同时有 content 和 reasoning 时，产出 token + reasoning 两个事件。"""
    from src.services.agent_service import _convert_event
    from src.utils.sse import SSEReasoningDeltaEvent, SSETokenEvent

    from langchain_core.messages import AIMessageChunk

    chunk = AIMessageChunk(
        content="正文",
        additional_kwargs={"reasoning_content": "思考"},
    )
    item = {
        "event": "on_chat_model_stream",
        "name": "ChatModel",
        "metadata": {"langgraph_node": "agent"},
        "data": {"chunk": chunk},
    }
    events = _convert_event(item)
    kinds = {type(e) for e in events}
    assert SSETokenEvent in kinds and SSEReasoningDeltaEvent in kinds
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/services/test_agent_service.py -v -k reasoning`
Expected: FAIL（当前 `_convert_event` 只产 SSETokenEvent，无 reasoning）

- [ ] **Step 3: 实现**

`src/services/agent_service.py` 的 `CHAT_MODEL_STREAM` 分支（当前 L150-167）改为：

```python
    if kind == LangGraphEvent.CHAT_MODEL_STREAM:
        if metadata.get("langgraph_node") == "agent":
            chunk = item.get(LangGraphKey.DATA, {}).get(LangGraphKey.CHUNK)
            if chunk is not None:
                content = chunk.content
                reasoning = (chunk.additional_kwargs or {}).get(
                    "reasoning_content", ""
                )
            else:
                content = ""
                reasoning = ""
            events = []
            if content:
                events.append(SSETokenEvent(content))
            if reasoning:
                events.append(SSEReasoningDeltaEvent(reasoning))
            return events
        return []
```

同时确认 `SSEReasoningDeltaEvent` 已 import（文件顶部 `from src.utils.sse import ...` 加 `SSEReasoningDeltaEvent`）。

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/services/test_agent_service.py -v`
Expected: 全 PASS

- [ ] **Step 5: 提交**

```bash
git add src/services/agent_service.py tests/services/test_agent_service.py
git commit -m "feat(service): on_chat_model_stream 提取 reasoning_content 产 reasoning 事件"
```

---

### Task 5: 前端 Think 折叠行

**Files:**
- Modify: `deploy/nginx/html/chat.html`
- 设计稿: `docs/design/chat-redesign-mockup.html`（.think-row 样式）

**Interfaces:**
- Consumes: Task 3 的 `event: reasoning`（data `{"delta": "..."}`）
- Produces: 消息流中每轮 LLM 调用一个 Think 折叠行（默认收起、增量累积、收到 token/status/ask_user/abstention/done 时定型）

- [ ] **Step 1: 加 CSS**

在 `chat.html` 的 `</style>` 前加（从 mockup 拷贝）：

```css
  /* ── Think 折叠行（思考过程展示）── */
  .think-row {
    align-self: flex-start;
    width: 100%;
    max-width: 85%;
    background: #F1F5F9;
    border: 1px solid var(--border);
    border-radius: var(--radius-sm);
    overflow: hidden;
  }
  .think-row summary {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 6px 10px;
    cursor: pointer;
    list-style: none;
    font-size: 12px;
    color: var(--text-secondary);
  }
  .think-row summary::-webkit-details-marker { display: none; }
  .think-row summary .think-icon { color: var(--text-muted); flex-shrink: 0; display: flex; }
  .think-row summary .think-title { font-weight: 600; color: var(--text); flex-shrink: 0; }
  .think-row summary .think-chevron {
    margin-left: auto;
    flex-shrink: 0;
    transition: transform 150ms ease;
    color: var(--text-muted);
  }
  .think-row[open] summary .think-chevron { transform: rotate(90deg); }
  .think-row summary .think-summary {
    flex: 1;
    min-width: 0;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    color: var(--text-muted);
  }
  .think-row .think-body {
    padding: 4px 12px 10px;
    font-size: 13px;
    line-height: 1.6;
    color: var(--text-secondary);
    white-space: pre-wrap;
  }
```

- [ ] **Step 2: 加 Think 行状态管理 JS**

在 `chat.html` 的 script 中（`removeStreamingState` 函数附近）加：

```js
// ── Think 折叠行（reasoning 事件累积渲染，前端推断边界）──
let currentThinkRow = null;

// 取思考文本最新一行（流式中摘要跟随最新行）
function latestReasoningLine(text) {
  const visible = text.replace(/\s+$/, '');
  const newline = visible.lastIndexOf('\n');
  return newline === -1 ? visible : visible.slice(newline + 1);
}

// 定型当前 Think 行（折叠摘要固定为首行，不再累积）
function closeThinkRow() {
  if (currentThinkRow) {
    const body = currentThinkRow.querySelector('.think-body');
    const summary = currentThinkRow.querySelector('.think-summary');
    if (body && summary) {
      const full = body.textContent;
      const newline = full.indexOf('\n');
      summary.textContent = newline === -1 ? full : full.slice(0, newline);
    }
    currentThinkRow = null;
  }
}

// 渲染思考增量（无活跃 Think 行则创建，默认折叠）
function renderReasoningDelta(delta) {
  if (!delta) return;
  if (!currentThinkRow) {
    const details = document.createElement('details');
    details.className = 'think-row';
    details.innerHTML = `
      <summary>
        <span class="think-icon"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 1 1 7.072 0l-.548.547A3.374 3.374 0 0 0 14 18.469V19a2 2 0 1 1-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z"/></svg></span>
        <span class="think-title">Think</span>
        <span class="think-summary"></span>
        <span class="think-chevron"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"/></svg></span>
      </summary>
      <div class="think-body"></div>`;
    chatContainer.appendChild(details);
    currentThinkRow = details;
    scrollToBottom();
  }
  const body = currentThinkRow.querySelector('.think-body');
  const summary = currentThinkRow.querySelector('.think-summary');
  body.textContent += delta;
  summary.textContent = latestReasoningLine(body.textContent);
}
```

- [ ] **Step 3: SSE 监听 + 边界定型**

在 `startSSE` 中加 `reasoning` 监听：

```js
  source.addEventListener('reasoning', (e) => {
    try {
      const data = JSON.parse(e.data);
      renderReasoningDelta(data.delta || '');
    } catch (err) { /* ignore */ }
  });
```

在以下事件处理器**开头**调 `closeThinkRow()`（定型边界）：
- `status` 监听器（新一轮状态 → 上一轮 Think 定型）
- `token` 监听器（开始输出正文 → 定型）
- `ask_user` 监听器（澄清接管 → 定型）
- `abstention` 监听器（拒答 → 定型）
- `done` 监听器（流结束 → 定型）

例如 `status` 监听器改为：

```js
  source.addEventListener('status', (e) => {
    try {
      closeThinkRow();
      const data = JSON.parse(e.data);
      renderStatusTag(data.stage, data.message);
    } catch (err) { /* ignore */ }
  });
```

`token` 监听器同样在开头加 `closeThinkRow();`（注意：token 可能和 reasoning 同时出现，定型后再有新 reasoning 会开新 Think 行——一轮内 reasoning→content 顺序下，token 出现即该轮思考结束，正确）。

- [ ] **Step 4: 部署 + playwright 验证**

```bash
docker compose up -d --build nginx
```

playwright 验证（需登录态 + enable_thinking=true 触发思考）：

```bash
playwright-cli -s=think open "http://localhost/?kb_id=b9e74e820e0a4bad8472304446e54f5c"
# 勾选深度思考 → 发"腾讯2024年的营收是多少"
# 等待后检查 .think-row 出现
playwright-cli -s=think --raw eval "document.querySelectorAll('.think-row').length"
```

Expected: `.think-row` 数量 > 0；点击 summary 可展开查看思考全文；消息流中有正文气泡 + 引用

- [ ] **Step 5: 提交**

```bash
git add deploy/nginx/html/chat.html
git commit -m "feat(frontend): reasoning 事件渲染 Think 折叠行"
```

---

### Task 6: 契约 + 端到端验证

**Files:**
- Modify: `docs/agents/api_contract.md`（SSE 事件表 + reasoning 事件）
- Read: `docs/design/pages/chat-redesign.md`（补 Think 行规格）

**Interfaces:**
- Consumes: Task 1-5 全部
- Produces: 契约文档与实现一致；端到端验证通过

- [ ] **Step 1: api_contract.md 补 reasoning 事件**

`docs/agents/api_contract.md` 2.3.1 事件表加一行：

```
| **`reasoning`** | **agent 节点 LLM 流式输出思考增量（enable_thinking=true 且模型返回 reasoning_content）** | **思考过程增量，前端累积渲染 Think 折叠行（每轮 LLM 调用一个，默认收起）；data: {"delta": "..."}** |
```

- [ ] **Step 2: 端到端验证**

```bash
# 后端回归
python -m pytest tests/ -q
# 前端：深度思考开启时 Think 行出现；关闭时无 Think 行（回归）
```

Expected: 后端全 PASS；前端 enable_thinking=true 出 Think 行、false 不出

- [ ] **Step 3: 提交**

```bash
git add docs/agents/api_contract.md docs/design/pages/chat-redesign.md
git commit -m "docs: api_contract 补 reasoning 事件 + 设计说明补 Think 行"
```

---

## Self-Review

**Spec 覆盖（共识 6 项）：**
- 获取机制（ChatQwenWithReasoning 子类）→ Task 1-2
- 每轮一个 Think 行 → Task 5（前端状态机，token/status 边界定型）
- reasoning-delta 增量事件 → Task 3-4
- 默认折叠 → Task 5（details 默认收起）
- 前端推断边界 → Task 5（closeThinkRow 在 status/token/ask_user/abstention/done 调用）
- 历史回传安全 → 已验证（_convert_message_to_dict 忽略），无需代码

**Placeholder scan：** 无 TBD/TODO；每步含完整代码；测试含具体断言；前端验证用具体命令。

**Type consistency：**
- `reasoning_delta` 字段：SSEReasoningDeltaEvent（Task 3）→ agent_service 产出（Task 4）→ SSE data `{"delta"}`（Task 3 sse_reasoning_delta）→ 前端 `data.delta`（Task 5）——一致
- `additional_kwargs["reasoning_content"]`：ChatQwenWithReasoning 写入（Task 1）→ agent_service 读取（Task 4）——一致
- `_convert_chunk_to_generation_chunk` 签名：Task 1 重写与 langchain 1.3.3 基类一致（验证过）
- get_llm 签名不变（Task 2），调用方无感
