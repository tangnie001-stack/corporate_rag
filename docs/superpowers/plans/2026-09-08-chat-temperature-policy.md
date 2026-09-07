# chat-temperature-policy Implementation Plan（可并行，无共享文件）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 主 agent 采样温度由全局固定 0.1 改为按会话是否绑定 KB 分档：绑 KB（RAG）0.1；非 KB 0.6（可配），同请求档位恒定。

**Architecture:** 在 `agent_node` 每轮 `model.astream(...)` 调用处：未绑 KB 时追加 `temperature=settings.NON_KB_MAIN_TEMPERATURE`（0.6）；绑 KB 不传 temperature（沿用构造温度 `LLM_TEMPERATURE`=0.1）。langchain-openai 请求 payload 会把显式传入的 `temperature` 合并进 `_default_params`（`{**default, **kwargs}`），仓库已有 `ainvoke(..., temperature=0)` 先例（`src/rag/temporal.py:128`），无需双实例/重建图。档位判定源为 `state.kb_id`（请求首轮即固定，同请求全轮一致）。

**Tech Stack:** Python 3.11 / FastAPI / langchain-openai per-call kwargs。规则见 CLAUDE.md。

**Spec:** `docs/openspec/changes/chat-temperature-policy/`（proposal.md / design.md / specs/chat-temperature-policy/spec.md / tasks.md）

## Global Constraints

- 温度分档只作用于**主 agent** 采样；fork 子代理、分类/检索等内部调用不受影响（不引入 skill temperature 字段——见 change proposal Non-Goals）。
- 档位判定源为 `state.kb_id`；同请求各 agent 轮次一致（`kb_id` 首轮即固定）。
- **绑 KB 档不新增旋钮**：KB 主 agent 不传 per-call temperature，沿用模型构造温度 `LLM_TEMPERATURE`（默认 0.1，即 spec 的 KB=0.1 档）。理由：① tasks.md 1.1「绑 KB 固定 0.1 常量」指该档值恒定，其来源（构造默认）不必另设 env；② 若把 KB 档做成独立 env（旧版 `KB_MAIN_TEMPERATURE`）会与 `LLM_TEMPERATURE` 形成双旋钮且与 proposal「仅非 KB 默认值参数化」冲突（强模型 review 确认）。
- 非 KB 档 = `settings.NON_KB_MAIN_TEMPERATURE`（默认 0.6，唯一新增可调参数）。
- 常量集中管理：新增可调项进 `src/config/settings.py`；业务代码不散落字面量。
- 实现首步以拦截 astream kwargs 的单测断言档位（design D1 验证：per-call temperature 被 langchain 透传的机制已在源码级核对成立——langchain-openai 1.3.3 `_get_request_payload` 为 `{**self._default_params, **kwargs}`，`ainvoke/astream(..., temperature=)` 覆盖构造值，仓库先例 `src/rag/temporal.py:128`）。
- 质量门禁：`pytest tests/ -v`、`ruff check .`、`pyright src/`（不新增 error）；无 `print()`/TODO。
- 实施顺序：core → 温度（可并行）→ task-board。本 change 仅触碰 `agent_node.py`/`settings.py` 及其测试（不改 const.py，避免与 core 并行编辑共享文件）。
- 完成冒烟还需确认非 KB 联网事实答案引用横条仍正常（design Risks：提温与 web 引用护栏相互作用，态 A `web_citation_guard` 会引导补标 regen 兜底，护栏语义不变）。

---

### Task A: 温度档位配置 + agent_node per-call temperature

**Files:**
- Modify: `src/config/settings.py`（模型选择区，`LLM_TEMPERATURE` 后，只加非 KB 档）
- Modify: `src/agents/graph/agent_node.py:76-158`（`agent_model` 节点每轮调用处，非 KB 传 temperature）
- Test: `tests/agents/graph/test_agent_node.py`

**Interfaces:**
- Consumes: `AgentState.kb_id`、`settings.NON_KB_MAIN_TEMPERATURE`
- Produces: `model.astream(messages, extra_body={"enable_thinking": ...}, temperature=<NON_KB 档>)`（仅未绑 KB 传 temperature；绑 KB 不传）

- [ ] **Step 1: 写失败测试**

`tests/agents/graph/test_agent_node.py` 现有 `CapturingThinkingModel` 只捕获 `extra_body`。新增捕获 temperature 的模型与断言测试：

```python
@pytest.mark.asyncio
async def test_agent_model_temperature_binds_kb_uses_construction_value():
    """绑 KB（kb_id 非空）→ 不传 per-call temperature（沿用构造温度 LLM_TEMPERATURE=0.1）。"""
    captured = {}

    class CapturingTempModel(MockChatModel):
        async def astream(self, messages, **kwargs):
            captured["temperature"] = kwargs.get("temperature")
            yield self.response

    llm = CapturingTempModel(AIMessage(content="ok"))
    node = make_agent_model_node(llm, [], StubPromptManager())
    state = AgentState.make_initial_state("s1", "kb-1", "2024 营收?", [])
    await node(state)
    assert captured["temperature"] is None  # 不覆盖构造值（LLM_TEMPERATURE 默认 0.1）


@pytest.mark.asyncio
async def test_agent_model_temperature_non_kb_uses_default():
    """未绑 KB（kb_id 空）→ 每轮 astream 传 temperature=NON_KB_MAIN_TEMPERATURE。"""
    captured = {}

    class CapturingTempModel(MockChatModel):
        async def astream(self, messages, **kwargs):
            captured["temperature"] = kwargs.get("temperature")
            yield self.response

    llm = CapturingTempModel(AIMessage(content="ok"))
    node = make_agent_model_node(llm, [], StubPromptManager())
    state = AgentState.make_initial_state("s1", "", "聊聊人生", [])
    await node(state)
    from src.config import settings

    assert captured["temperature"] == settings.NON_KB_MAIN_TEMPERATURE


@pytest.mark.asyncio
async def test_agent_model_temperature_same_tier_across_turns():
    """同请求多轮：kb_id 恒定 → 各轮 temperature 一致（档位不随轮次漂移）。"""
    captured = []

    class MultiTurnModel(MockChatModel):
        async def astream(self, messages, **kwargs):
            captured.append(kwargs.get("temperature"))
            yield self.response

    llm = MultiTurnModel(AIMessage(content="ok"))
    node = make_agent_model_node(llm, [], StubPromptManager())
    state = AgentState.make_initial_state("s1", "", "q", [])
    state.messages = [HumanMessage(content="q")]  # 后续轮
    await node(state)
    await node(state)
    assert captured == [captured[0], captured[0]]
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/agents/graph/test_agent_node.py -v`
Expected: FAIL（`settings.NON_KB_MAIN_TEMPERATURE` 不存在 + 非 KB 的 astream kwargs 尚无 temperature）。

- [ ] **Step 3: settings 增加非 KB 档参数**

`src/config/settings.py` `LLM_TEMPERATURE`（41 行）之后追加：

```python
# 主 agent 采样温度（chat-temperature-policy）：绑 KB 沿用 LLM_TEMPERATURE（默认 0.1）；
# 非 KB 聊天/探讨用中温避免过干（proposal：唯一新增可调参数，KB 档不另设旋钮）
NON_KB_MAIN_TEMPERATURE: float = float(os.getenv("NON_KB_MAIN_TEMPERATURE", "0.6"))
```

- [ ] **Step 4: agent_node per-call temperature（非 KB 分支）**

`src/agents/graph/agent_node.py` import 区补 settings：

```python
from src.config import settings
```

`agent_model` 内现调用处（agent_node.py:91-95）：

```python
        turn_start = time.monotonic()
        chunks = []
        async for chunk in model.astream(
            messages, extra_body={"enable_thinking": state.deep_thinking}
        ):
            chunks.append(chunk)
```

改为（只改调用行，`turn_start` 已存在，不要重复定义；extra_body 浅合并注释保留在 88-90 行原处，可补一句 temperature 同为浅合并覆盖）：

```python
        turn_start = time.monotonic()
        # 采样温度分档（chat-temperature-policy）：未绑 KB → 非 KB 档（默认 0.6）；
        # 绑 KB → 不传 temperature，沿用模型构造温度 LLM_TEMPERATURE（默认 0.1），
        # 同请求档位恒定（kb_id 首轮即固定）
        chunks = []
        if state.kb_id:
            async for chunk in model.astream(
                messages, extra_body={"enable_thinking": state.deep_thinking}
            ):
                chunks.append(chunk)
        else:
            async for chunk in model.astream(
                messages,
                extra_body={"enable_thinking": state.deep_thinking},
                temperature=settings.NON_KB_MAIN_TEMPERATURE,
            ):
                chunks.append(chunk)
```

- [ ] **Step 5: 运行测试 + 冒烟**

Run: `pytest tests/agents/graph/test_agent_node.py tests/agents/graph/test_graph.py -v`
Expected: PASS。

流式冒烟（真实链路确认 per-call temperature 透传进 payload，design D1 验证）：**LLM_LOG_CONTENT 看不到请求参数**（llm_content_logging handler 只记模型名+输入输出），应改用 **Langfuse trace 的 `invocation_params` / `ls_temperature`**（`_get_invocation_params` 会把 per-call kwargs 合并，langfuse 捕获该字段）核对非 KB 请求 temperature=0.6、绑 KB 请求无 per-call 覆盖（0.1）；并确认非 KB 联网事实题引用横条仍正常（regen 兜底不误触发）。

- [ ] **Step 6: 提交**

```bash
git add src/config/settings.py src/agents/graph/agent_node.py tests/agents/graph/test_agent_node.py
git commit -m "feat(agent): tier main-agent sampling temperature by KB binding"
```

---

### Task B: 可配置性验证与文档

**Files:**
- Modify: `docs/agents/api_contract.md`（如描述温度，则补档位说明）
- Modify: `docs/openspec/changes/chat-temperature-policy/`（validate）
- Test: `tests/config/test_settings.py`（默认值断言，env 隔离）

- [ ] **Step 1: 默认值断言（env 隔离）**

`tests/config/test_settings.py` 追加（沿用该文件既有 reload + `patch.dict(os.environ)` 隔离模式，防 .env 污染误报）：

```python
def test_non_kb_temperature_default():
    """NON_KB_MAIN_TEMPERATURE 默认 0.6（.env 污染环境也可稳定断言）。"""
    from importlib import reload

    from unittest.mock import patch
    import os
    import src.config.settings as _s

    with patch("dotenv.load_dotenv"), patch.dict(os.environ):
        os.environ.pop("NON_KB_MAIN_TEMPERATURE", None)
        reloaded = reload(_s)
        assert reloaded.NON_KB_MAIN_TEMPERATURE == 0.6
```

Run: `pytest tests/config/ -v` Expected: PASS。

- [ ] **Step 2: 文档同步**

`docs/agents/api_contract.md` 若含温度说明则补档位一句话（绑 KB=构造温度 LLM_TEMPERATURE 默认 0.1 / 非 KB=`NON_KB_MAIN_TEMPERATURE` 默认 0.6，非 KB 档 settings 可调）；`data-flow.md` 不涉及则跳过。openspec validate：

Run: `npx opsx validate chat-temperature-policy`（如实际命令不同以仓库为准）Expected: valid。

- [ ] **Step 3: 质量门禁 + 提交**

Run:
```bash
pytest tests/ -v
ruff check .
pyright src/
```
Expected: 全绿。

```bash
git add docs/agents/api_contract.md tests/
git commit -m "docs(chat): note temperature tiering contract and validate change"
```

---

## Self-Review 记录

- **Spec 覆盖**：温度按 KB 分档 → Task A Step 3-4（KB=沿用构造温度不传，非 KB=per-call settings 档）；同请求一致 → Task A Step 1 第三测试 + 判定源为 `state.kb_id`（请求固定）；非 KB 可配置 → Task A Step 3 + Task B Step 1；仅作用主 agent → 改动仅限 `agent_node`（fork/分类调用路径不受影响）。
- **Placeholder 扫描**：无占位；测试与实现代码全量给出。
- **Type/名一致**：settings 键 `NON_KB_MAIN_TEMPERATURE` 在 settings、agent_node、测试三处一致；绑 KB 分支不引用任何新增配置。
- **评审修正记录**：①KB 档由独立 env 改为沿用构造温度（消除与 spec/tasks「常量」冲突及 `LLM_TEMPERATURE` 双旋钮）；②冒烟观测手段由 `LLM_LOG_CONTENT` 改为 Langfuse `invocation_params`（前者不记录请求参数）；③实现片段避免重复 `turn_start`（只改调用行）。
