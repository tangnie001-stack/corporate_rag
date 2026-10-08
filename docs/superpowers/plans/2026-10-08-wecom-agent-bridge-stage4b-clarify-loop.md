# 企微接入 Agent 管线 — 阶段 4b（澄清闭环：呈现 · 仅触发者回填 · 答案注入）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让「澄清（clarify / `ask_user`）」在企微通道真正可用：把问题**呈现**给用户、把用户的文字答复**只认触发者**地解析成答案、并**注入原来那个挂起的回合**让同一条流续跑。

**Architecture（本阶段的核心，必须先读懂）**：澄清**不是新一轮对话**。`ask_user` 工具在**当前回合内**挂起——它在进程级 `pending_asks[session_id]` 登记一个 `Future`，把问题经 `ctx.clarify_channel` 推成 `SSEAskUserEvent`，然后 `wait_with_abort_and_timeout` 等最多 120 秒（`ASK_USER_TIMEOUT`）。**那条 SSE 流仍然开着**、投影层仍在 `await` 事件流。所以用户回一条文本时，通道**绝不能**调 `start_turn` 开新回合，而要：`parse(text) → resolve(future)` ⇒ 原回合继续产生事件 ⇒ **原来那个 presenter 自动把后续内容接着投到同一个气泡里**（无需新建 presenter、无需新气泡）。

由此推出本阶段的三个设计点：
1. **呈现**：投影层原来**显式丢弃** `SSEAskUserEvent`（阶段 3 有意留白）。现在要把它**渲染进气泡的累积内容**（快照语义：问题在前、答案在后），**且不 finalize**（回合未结束，流的终态帧只能发一次）。
2. **仅触发者**：阶段 3 已把 `session_id → 触发者 userid` 登记进 `_triggers`（活体验过）。只有**触发者**的下一条文本才被当作答案；非触发者的消息走原路径（会因会话闸门得到 BUSY 文案）。
3. **注入而非新回合**：把文本解析成站点同构的 `answers = [{id, selected, custom}]`，调**下沉后的** `resolve_clarify_answer`；成功即 `return`（不 `start_turn`）。

**Tech Stack:** Python 3.11+ / FastAPI / LangChain 1.x + LangGraph / asyncio（`Future`）/ loguru / pytest（`@pytest.mark.asyncio`）。

**Spec:** `openspec/changes/wecom-agent-bridge/design.md`（**D14** 澄清仅触发者 / **D9** 三路 trace / **D12** 下沉入口 / **D15** 桥接仅长连接）+ `specs/wecom-agent-bridge/spec.md` + tasks.md 的**组 6** 与 **1.5**；**站点侧现行契约**以主规格 `openspec/specs/clarification-interaction/spec.md` 为准（**结构化答案契约**：`{answers:[{id,selected,custom}]}`；**澄清超时**：120s、超时 resolve 收尾清表、超时后 404 且不写历史）；SDK 行为见 `docs/agents/wecom-sdk-facts.md`。
> ⚠️ **主规格里没有"来源校验"条款** ⇒ "仅触发者"是**通道侧新增约束**，本阶段在 change 的 spec delta 里落它。

**Worktree:** `/root/code/corporate_rag-wecom-stage4b`（分支 `feat/wecom-agent-bridge-stage4b`，基线 `9a0b88b`）。

## Global Constraints

以下为项目级硬约束，**每个任务的要求都隐含包含本节**；值一律照抄。

- **层间规则**：`api/` 不得直接 import `infra/` 或 `config/`（必须经 `services/`）；`channels/` 不得 import `api/`；本阶段允许 `channels/` import `services` 的**叶子模块**（既有形态）。
- **文件与函数红线**：单文件 ≤ **400 行**（含测试文件；超过必须拆分——本仓惯例：共享辅助放 `tests/<pkg>/<name>_fakes.py` 这类普通模块、绝对导入）；单函数 ≤ 80 行。
- **代码风格**：**不用三元表达式**（写完整 if/else）；类型不确定处用显式 `isinstance` / `is not None`，**不得**用 `getattr(x, "attr", default)` 兜底。
- **配置集中**：新增常量/文案入 `src/config/`——通道层进 `src/config/wecom_channel.py`（`WeComChannelTexts` 类），服务层阈值进 `settings.py`/`const.py`。
- **日志**：英文 `k=v` + `[wecom]`（通道）/ `[clarify]`（服务）前缀；**不得 `print`**；不可信值用 `encode_value`；**原始异常只进日志，绝不进发给用户的内容**。
- **有界**：任何进程内累积结构必须 TTL + 容量双淘汰（用既有 `src/channels/wecom/bounded_map.py` 的 `BoundedTtlMap`）。
- **测试**：宿主侧一律 `POSTGRES_HOST=localhost pytest ...`；异步用例 `@pytest.mark.asyncio`（本仓未开 `asyncio_mode=auto`）。
- **测试替身/共享辅助**：跨文件复用请放 `tests/<pkg>/<name>_fakes.py`（样板 `tests/api/mock_data.py`、`tests/channels/wecom_driver_fakes.py`）；**不得**两处各定义一份。
- **不得削弱既有断言**。**允许**的等价调整有两处（本阶段确有契约变更，逐处在任务里点名）：① `tests/api/test_clarify.py` 的 monkeypatch **目标**随下沉而改（断言强度不变）；② `tests/channels/test_wecom_presenter.py::test_ask_user_event_produces_no_frame` 的**前提已不成立**（阶段 3 的"澄清事件不产帧"契约被本阶段**显式取代**），须**改写为**"澄清事件渲染进内容且不 finalize"，并在 change 的 spec delta 里同步。
- **worktree 环境既有事实**：缺 gitignored 夹具 `data/test_docs/*` → `tests/parsers/` 的 `FileNotFoundError` 属**环境性**，别修；`.venv` / `.env` 已软链到主仓。
- **提交命令形状**：先单独 `git add <文件>` → `git diff --cached --name-only` 核对 → 再 `git commit`。**不要**写 `git add … && git commit … > log`（`add` 失败会短路、连日志都不生成）。**注意 `openspec/` 是软链**：改 change 工件必须用真实路径 `docs/openspec/...`。
- **站点侧现行契约（不得改变语义）**：答案元素为 `{"id": str, "selected": list[str], "custom": str}`；`str(answers)` 就是喂给 LLM 的返回值（`ask_tools.py:176`）；`pending_asks` 为**单槽/每会话**（第二次挂起返回 `ASK_USER_LIMIT_REACHED`，不覆盖）；超时文案 `ASK_USER_TIMEOUT_TEXT`（`const.py:296`）；`ask_confirm` / `confirm_gate` 与澄清**共用同一张 `pending_asks`**（勿动其语义）。

---

## 阶段地图

| 阶段 | 范围 | 状态 |
|---|---|---|
| 0 / 1 / 2 / 3 / 4a | Spike / 编排下沉 / 投影层 / 桥接 handler / 驱动可靠性 | ✅ 已并入 `dev-wsl` |
| **4b（本文件）** | **组 6 + 1.5：澄清呈现 · 仅触发者回填 · 答案下沉与注入** | 本次实施 |
| 4c（可选，另立） | 卡片二期（`task_id` + `event_key` 做澄清按钮） | 未开始 |
| 5 | 组 7：灰度 E2E / 鲁棒验收 / 群聊验收 / 文档 / 发布 runbook | 未开始 |

**4b 的验收面**：单测覆盖"呈现 / 解析 / 注入 / 仅触发者 / 无效答案 / 超时回落"六面；端到端（确认点 C）需真实连网：@ `dev` 问一个会触发 `ask_user` 的问题 → **气泡里看到问题与选项** → 回复答案 → **同一气泡续跑出最终答案**；群里**非触发者**插话**不得**被当作答案。

---

## File Structure

| 文件 | 责任 | 动作 |
|---|---|---|
| `src/services/clarify_service.py` | 澄清答案的**编排**（下沉目标）：定位并 resolve 挂起 Future + 落库用户答案 + 文本格式化 | **新建** |
| `src/api/clarify.py` | 变**薄**：校验请求体 → 调 service → 映射 200/404 | 修改 |
| `src/channels/wecom/clarify_parse.py` | **纯函数**：把用户文本解析成站点同构 `answers`（含多问/选项/multi_select/无效判定） | **新建** |
| `src/channels/wecom/clarify_render.py` | **纯函数**：问题清单 → 企微可读文本块（投影层只调用；让 presenter 保持 <400 行） | **新建** |
| `src/channels/wecom/presenter.py` | 把 `SSEAskUserEvent` **渲染进累积内容**（取代"显式丢弃"），不 finalize | 修改 |
| `src/channels/wecom/handler.py` | 入站分流插一层"仅触发者的答案注入"；登记/清除 `_questions` | 修改 |
| `src/config/wecom_channel.py` | 澄清相关文案（渲染模板 / 无效答案提示） | 修改 |
| `tests/services/test_clarify_service.py` · `tests/channels/test_wecom_clarify.py` | 上述两处新单元 | **新建** |
| `tests/channels/test_wecom_presenter*.py` · `tests/channels/wecom_presenter_fakes.py` | presenter 测试**拆分**（原文件 620 行已越线）+ 澄清渲染新用例 | 新建/拆分 |
| `tests/api/test_clarify.py` · `tests/channels/test_wecom_handler.py` | 契约变更与新增用例 | 修改 |
| `docs/agents/{code-map,glossary}.md` · `docs/openspec/changes/wecom-agent-bridge/{design.md,specs/…,tasks.md}` | 登记（含 spec delta：仅触发者约束） | 修改（Task 4） |

---

### Task 1: 下沉澄清答案编排到 `services`

**Files:**
- Create: `src/services/clarify_service.py`
- Modify: `src/api/clarify.py`
- Test: `tests/services/test_clarify_service.py`（新建）、`tests/api/test_clarify.py`（改）

**Interfaces:**
- Consumes: `src.infra.llm.request_context.pending_asks: dict[str, asyncio.Future]`（既有）；`AppService`（`add_message_async` / `get_session_by_id` / `save_user_async`）。
- Produces:
  - `clarify_service.format_answers_text(answers: list) -> str`
  - `async clarify_service.resolve_clarify_answer(svc: AppService, *, session_id: str, answers: list) -> bool`（True=已投递给挂起的回合；False=查无/已结束——调用方据此决定回落）

- [ ] **Step 1: 写失败测试**

创建 `tests/services/test_clarify_service.py`：

```python
"""澄清答案编排（下沉自 api 层）：单次消费、落库、文本格式化。"""

from __future__ import annotations

import asyncio

import pytest

from src.infra.llm.request_context import pending_asks
from src.services import clarify_service


class _FakeSvc:
    """记录落库调用的假 AppService。"""

    def __init__(self, kb_id: str | None = "kb-1") -> None:
        self.added: list[tuple[str, str, str]] = []
        self.saved: list[tuple[str, str, str]] = []
        self._kb_id = kb_id

    async def add_message_async(
        self, session_id: str, role: str, content: str
    ) -> None:
        self.added.append((session_id, role, content))

    async def get_session_by_id(self, session_id: str) -> dict | None:
        if self._kb_id is None:
            return None
        return {"kb_id": self._kb_id}

    async def save_user_async(
        self, session_id: str, content: str, kb_id: str
    ) -> None:
        self.saved.append((session_id, content, kb_id))


@pytest.fixture(autouse=True)
def _clean_pending():
    """每个用例前后清空进程级挂起表，避免跨用例污染。"""
    pending_asks.clear()
    yield
    pending_asks.clear()


def test_format_answers_text_selected_and_custom():
    text = clarify_service.format_answers_text(
        [
            {"id": "q1", "selected": ["甲", "乙"], "custom": ""},
            {"id": "q2", "selected": [], "custom": "自定义说明"},
        ]
    )
    assert text == "甲、乙；自定义说明"


def test_format_answers_text_skips_empty_items():
    assert clarify_service.format_answers_text(
        [{"id": "q1", "selected": [], "custom": ""}]
    ) == ""


@pytest.mark.asyncio
async def test_resolve_returns_false_when_no_pending():
    svc = _FakeSvc()

    ok = await clarify_service.resolve_clarify_answer(
        svc, session_id="S1", answers=[{"id": "q1", "selected": ["甲"], "custom": ""}]
    )

    assert ok is False
    assert svc.added == []
    assert svc.saved == []


@pytest.mark.asyncio
async def test_resolve_returns_false_when_future_already_done():
    loop = asyncio.get_running_loop()
    done = loop.create_future()
    done.set_result([])
    pending_asks["S1"] = done
    svc = _FakeSvc()

    ok = await clarify_service.resolve_clarify_answer(
        svc, session_id="S1", answers=[{"id": "q1", "selected": ["甲"], "custom": ""}]
    )

    assert ok is False
    assert "S1" not in pending_asks  # pop 已发生（单次消费）
    assert svc.added == []


@pytest.mark.asyncio
async def test_resolve_delivers_answers_and_persists():
    loop = asyncio.get_running_loop()
    fut: asyncio.Future = loop.create_future()
    pending_asks["S1"] = fut
    svc = _FakeSvc(kb_id="kb-9")
    answers = [{"id": "q1", "selected": ["甲"], "custom": ""}]

    ok = await clarify_service.resolve_clarify_answer(
        svc, session_id="S1", answers=answers
    )

    assert ok is True
    assert fut.result() == answers  # 原样投递给挂起的 ask_user
    assert "S1" not in pending_asks  # 单次消费
    assert svc.added == [("S1", "user", "甲")]
    assert svc.saved == [("S1", "甲", "kb-9")]


@pytest.mark.asyncio
async def test_resolve_skips_persist_when_text_empty():
    loop = asyncio.get_running_loop()
    fut: asyncio.Future = loop.create_future()
    pending_asks["S1"] = fut
    svc = _FakeSvc()

    ok = await clarify_service.resolve_clarify_answer(
        svc, session_id="S1", answers=[{"id": "q1", "selected": [], "custom": ""}]
    )

    assert ok is True
    assert svc.added == []  # 无文本可落时不得写空消息
    assert svc.saved == []


@pytest.mark.asyncio
async def test_resolve_tolerates_missing_session_row():
    """会话查不到（kb_id 缺失）时不得抛；Redis 已写、MySQL 跳过。"""
    loop = asyncio.get_running_loop()
    fut: asyncio.Future = loop.create_future()
    pending_asks["S1"] = fut
    svc = _FakeSvc(kb_id=None)

    ok = await clarify_service.resolve_clarify_answer(
        svc, session_id="S1", answers=[{"id": "q1", "selected": ["甲"], "custom": ""}]
    )

    assert ok is True
    assert svc.added == [("S1", "user", "甲")]
    assert svc.saved == []
```

- [ ] **Step 2: 跑测试确认失败**

Run: `POSTGRES_HOST=localhost pytest tests/services/test_clarify_service.py -q`
Expected: FAIL —— `ModuleNotFoundError: No module named 'src.services.clarify_service'`。

- [ ] **Step 3: 实现（`src/services/clarify_service.py`）**

把 `src/api/clarify.py` 里既有的编排**逐字搬过来**（含 docstring 语义），api 只留薄壳：

```python
"""澄清答案编排（下沉自 api 层，design D12 的同构下沉）。

站点与企微通道**共用同一实现**：定位并 resolve 进程级挂起的 ask_user Future，
然后把用户答案作为 user 消息写入 Redis 历史与 MySQL（对齐 chat_stream 入口的
用户消息落库模式，kb_id 从会话记录取）——仅写 Redis 时刷新后澄清回答即丢、
回放叙事断裂。

单次消费：`pop` 保证无论成功解析还是已超时，注册表只允许被消费一次。
"""

from __future__ import annotations

import asyncio
from typing import Any

from loguru import logger

from src.infra.llm.request_context import pending_asks
from src.services.app_service import AppService


def format_answers_text(answers: list) -> str:
    """把用户答案数组拼成可读文本（与前端 formatAnswers 展示一致）。

    Args:
        answers: 用户答案列表，每条为 {"id", "selected": [...], "custom": "..."}

    Returns:
        可读文本：每条答案按 "选项1、选项2；自定义" 拼接，多条答案以 "；" 相连
    """
    parts: list[str] = []
    for ans in answers:
        item_parts: list[str] = []
        selected = ans.get("selected") or []
        if selected:
            item_parts.append("、".join(str(s) for s in selected))
        if ans.get("custom"):
            item_parts.append(str(ans["custom"]))
        if item_parts:
            parts.append("；".join(item_parts))
    return "；".join(parts)


async def resolve_clarify_answer(
    svc: AppService, *, session_id: str, answers: list
) -> bool:
    """把答案投递给该会话挂起的 ask_user，并落库用户答案。

    Args:
        svc: AppService（Redis 历史 + MySQL 会话写入）
        session_id: 会话 ID（挂起 Future 的键）
        answers: 站点同构答案列表 `[{id, selected, custom}]`

    Returns:
        True 表示已投递；False 表示该会话没有挂起（或已结束），调用方应回落
    """
    future: asyncio.Future | None = pending_asks.pop(session_id, None)
    if future is None:
        logger.info("[clarify] resolve miss session_id={}", session_id)
        return False
    if future.done():
        logger.info("[clarify] resolve already done session_id={}", session_id)
        return False
    try:
        future.set_result(answers)
    except asyncio.InvalidStateError:
        logger.warning("[clarify] resolve raced session_id={}", session_id)
        return False

    text = format_answers_text(answers)
    if text:
        await svc.add_message_async(session_id, "user", text)
        session: dict[str, Any] | None = await svc.get_session_by_id(session_id)
        if session is not None:
            await svc.save_user_async(session_id, text, session.get("kb_id", ""))
    logger.info("[clarify] resolved session_id={} text_len={}", session_id, len(text))
    return True
```

- [ ] **Step 4: 改薄 `src/api/clarify.py`**

保留 `ClarifyAnswerBody` 与路由装饰器，路由体改为：

```python
@router.post("/chat/clarify-answer", response_model=ResponseModel)
async def clarify_answer(
    body: ClarifyAnswerBody,
    svc: AppService = Depends(get_app_service),
):
    """解析挂起的 ask_user Future；查无或已结束返回 404。

    编排已下沉 `services.clarify_service`（design D12），本路由只做请求校验与
    状态码映射，行为与下沉前一致。

    Args:
        body: 澄清答案提交请求体（session_id + answers）
        svc: AppService 实例（依赖注入）

    Returns:
        ResponseModel: 成功时 data=True

    Raises:
        HTTPException 404: 该会话没有挂起的澄清（或已结束/已超时）
    """
    delivered = await clarify_service.resolve_clarify_answer(
        svc, session_id=body.session_id, answers=body.answers
    )
    if not delivered:
        raise HTTPException(status_code=404, detail="no pending clarify for session")
    return ResponseModel(data=True)
```

同时：删掉本文件里的 `_format_answers_text`（唯一归属已移到 service）、`from src.infra.llm.request_context import pending_asks` 与不再使用的 import；新增 `from src.services import clarify_service`。

- [ ] **Step 5: 同步既有 api 测试（等价调整，不得削弱断言）**

先 `git ls-files tests/api | grep -i clarify` 自查是否存在 `tests/api/test_clarify.py`（或路由测试散在 `tests/api/test_*.py`）。若存在：
- 把 monkeypatch 的**目标**从 `src.api.clarify.pending_asks` 改到 `src.services.clarify_service.pending_asks`（或改为直接操作 `request_context.pending_asks` 真实对象）；
- 断言（状态码 / `data` / 落库调用）**逐条保留**，不得削弱；
- 补一条：**同一 session 第二次提交** → 第二次 404（单次消费）。

- [ ] **Step 6: 跑测试确认通过**

Run: `POSTGRES_HOST=localhost pytest tests/services/ tests/api/ -q`
Expected: 全 passed。

- [ ] **Step 7: 提交**

```bash
git add src/services/clarify_service.py src/api/clarify.py tests/services/test_clarify_service.py
git commit -m "refactor(services): 澄清答案编排下沉 resolve_clarify_answer（api 变薄路由，行为不变）"
```

---

### Task 2: 投影层呈现澄清问题（取代"显式丢弃"）

> **⚠️ 本任务含两处结构调整（派单前已核实的硬约束，别等评审来提）**
> 1. `src/channels/wecom/presenter.py` 现 **379 行**，本就贴着 400 红线 ⇒ 渲染逻辑**必须抽成独立模块**（`src/channels/wecom/clarify_render.py`），presenter 只留一行调用；否则会越线。
> 2. `tests/channels/test_wecom_presenter.py` 现 **620 行**（**已越线**，属阶段 3 累积、此前未被逮到）⇒ 本任务**必须拆分**它（本仓惯例：共享辅助放普通模块、绝对导入），并把本任务的新增用例放进**新的** `tests/channels/test_wecom_presenter_clarify.py`。

**Files:**
- Create: `src/channels/wecom/clarify_render.py`（纯函数：问题清单 → 文本块）
- Create: `tests/channels/wecom_presenter_fakes.py`（共享替身：`_RecordingSink`、`_stream(...)` 等，**唯一定义**）
- Create: `tests/channels/test_wecom_presenter_clarify.py`（本任务的新增/改写用例）
- Move/Split: `tests/channels/test_wecom_presenter.py` → 拆为"核心投影"与"流式/降级"两个文件（见 Step 1）
- Modify: `src/channels/wecom/presenter.py`、`src/config/wecom_channel.py`

**Interfaces:**
- Consumes: `SSEAskUserEvent`（`src/utils/sse.py`，字段 `questions: list[dict]`，元素 `{id, question, options, multi_select, dimension}`）。
- Produces:
  - `WeComChannelTexts.CLARIFY_HEADER / CLARIFY_ITEM / CLARIFY_OPTIONS / CLARIFY_HINT`
  - `clarify_render.render_questions(questions: list) -> str`（无有效问题时返回空串）
  - `WeComPresenter._clarify_text: str`（渲染后的问题块；`_render_answer()` 把它与正文拼成快照）

- [ ] **Step 0: 拆分越线的 presenter 测试文件（先做，保证后续步骤有干净落点）**

1. 新建 `tests/channels/wecom_presenter_fakes.py`：把 `tests/channels/test_wecom_presenter.py` 里**被多处复用**的替身与工具搬进去（如 `_RecordingSink`、`_stream(...)`、以及任何帧构造辅助），成为**唯一定义**；两个/三个测试模块都从 `from tests.channels.wecom_presenter_fakes import …` 引入。
2. 按**主题**把剩余用例拆成两个文件（示例分法，按实际内容调整并保持主题内聚）：
   - `tests/channels/test_wecom_presenter.py` —— 核心投影：骨架/快照累积/节流/帧数与长度上限/来源段/终态与 footer/兜底；
   - `tests/channels/test_wecom_presenter_stream.py` —— 流式与降级：保活（队列 + 超时）、上游异常置错误文案、取消语义、发送失败降级。
3. **断言一字不改**：只搬文件与 import；拆分后**用例总数不变**（先记下拆前 `pytest tests/channels/test_wecom_presenter.py -q` 的通过数，拆后**跨文件合计必须相等**）。
4. 结果：`test_wecom_presenter.py`、`test_wecom_presenter_stream.py`、`wecom_presenter_fakes.py` 与（后续的）`test_wecom_presenter_clarify.py` **都 < 400 行**；`wecom_presenter_fakes.py` 不以 `test_` 开头、不被 pytest 收集。

- [ ] **Step 1: 写失败测试（改写旧契约用例 + 新增 2 条，落在 `tests/channels/test_wecom_presenter_clarify.py`）**

见下方代码块（三例）。注意第一例是**改写**阶段 3 的 `test_ask_user_event_produces_no_frame`（**该契约被本阶段显式取代**），**旧用例须从原文件删除、不得并存**。

```python
"""投影层对澄清事件的呈现（阶段 4b 取代阶段 3 的"不产帧"契约）。"""

import pytest

from src.channels.wecom.presenter import WeComPresenter
from src.config.wecom_presenter import WeComPresenterTexts
from src.utils.sse import SSEAskUserEvent, SSEDoneEvent, SSETokenEvent
from tests.channels.wecom_presenter_fakes import _RecordingSink, _stream


@pytest.mark.asyncio
async def test_ask_user_event_is_rendered_into_content():
    """澄清事件渲染进累积内容且**不 finalize**（回合未结束，终态帧只能发一次）。"""
    sink = _RecordingSink()
    presenter = WeComPresenter(sink, "trace_1", min_interval_seconds=0)

    await presenter.run(
        _stream(
            [
                SSEAskUserEvent(
                    questions=[
                        {
                            "id": "q1",
                            "question": "选哪个口径？",
                            "options": ["营收", "毛利"],
                            "multi_select": False,
                        }
                    ]
                ),
            ]
        )
    )

    content = sink.contents[-1]
    assert "选哪个口径？" in content
    assert "营收" in content
    assert sink.calls[-1][1] is False  # 仍是非终态帧：等待用户作答


@pytest.mark.asyncio
async def test_clarify_question_and_answer_share_one_bubble():
    sink = _RecordingSink()
    presenter = WeComPresenter(sink, "trace_1", min_interval_seconds=0)

    await presenter.run(
        _stream(
            [
                SSEAskUserEvent(
                    questions=[
                        {
                            "id": "q1",
                            "question": "选哪个口径？",
                            "options": ["营收"],
                            "multi_select": False,
                        }
                    ]
                ),
                SSETokenEvent(token="按营收口径："),
                SSEDoneEvent(trace_id="trace_1"),
            ]
        )
    )

    final = sink.contents[-1]
    assert "选哪个口径？" in final  # 问题仍在（快照累积）
    assert "按营收口径：" in final  # 后续答案接在同一气泡
    assert sum(1 for _content, finish in sink.calls if finish) == 1  # 终态帧只一次


@pytest.mark.asyncio
async def test_ask_user_event_without_questions_renders_nothing():
    sink = _RecordingSink()
    presenter = WeComPresenter(sink, "trace_1", min_interval_seconds=0)

    await presenter.run(_stream([SSEAskUserEvent(questions=[]), SSEDoneEvent()]))

    assert WeComPresenterTexts.FALLBACK_TEXT in sink.contents[-1]  # 走既有兜底
```

- [ ] **Step 2: 跑测试确认失败**

Run: `POSTGRES_HOST=localhost pytest tests/channels/test_wecom_presenter_clarify.py -q`
Expected: FAIL（`test_ask_user_event_is_rendered_into_content` 内容里没有"选哪个口径？"；另两条同理）。

- [ ] **Step 3: 加文案（`src/config/wecom_channel.py`）**

在 `WeComChannelTexts` 内追加：

```python
    # 澄清问题块：头部 + 每条问题模板（{index}/{question}）与选项行模板（{options}）
    CLARIFY_HEADER: str = "需要您补充信息（请直接回复本条消息）："
    CLARIFY_ITEM: str = "{index}. {question}"
    CLARIFY_OPTIONS: str = "（可选：{options}）"
    CLARIFY_HINT: str = "请回复上面的问题；超时未回复我会按已有信息继续。"
```

- [ ] **Step 4: 新建渲染模块（`src/channels/wecom/clarify_render.py`）**

```python
"""把澄清问题清单渲染成企微可读文本块（design D14；投影层只调用、不实现）。

纯函数、无副作用：便于单测，也让 `presenter.py` 保持体量（红线 ≤400 行）。
"""

from __future__ import annotations

from src.config.wecom_channel import WeComChannelTexts


def render_questions(questions: list) -> str:
    """渲染问题清单；无有效问题时返回空串。

    Args:
        questions: SSEAskUserEvent.questions（元素含 question/options）

    Returns:
        形如 "需要您补充信息…\\n1. 选哪个口径？（可选：营收、毛利）\\n…" 的文本块
    """
    lines: list[str] = []
    index = 0
    for item in questions:
        if not isinstance(item, dict):
            continue
        question = str(item.get("question", "")).strip()
        if not question:
            continue
        index += 1
        line = WeComChannelTexts.CLARIFY_ITEM.format(index=index, question=question)
        options = item.get("options") or []
        if options:
            joined = "、".join(str(option) for option in options)
            line = f"{line} {WeComChannelTexts.CLARIFY_OPTIONS.format(options=joined)}"
        lines.append(line)
    if not lines:
        return ""
    lines.append(WeComChannelTexts.CLARIFY_HINT)
    return "\n".join([WeComChannelTexts.CLARIFY_HEADER, *lines])
```

同时（可选但推荐）在 `tests/channels/test_wecom_presenter_clarify.py` 追加 1 条**纯函数**用例，钉住"无有效问题返回空串 / 有选项则带选项"：

```python
def test_render_questions_pure_function():
    from src.channels.wecom.clarify_render import render_questions

    assert render_questions([]) == ""
    assert render_questions([{"question": "  "}]) == ""
    rendered = render_questions(
        [{"question": "选哪个？", "options": ["甲", "乙"]}]
    )
    assert "1. 选哪个？" in rendered
    assert "甲、乙" in rendered
```

- [ ] **Step 5: 改 `src/channels/wecom/presenter.py`**

① import 区增：`from src.channels.wecom.clarify_render import render_questions`。

② `__init__` 增字段：

```python
        self._clarify_text: str = ""
```

③ `_render_answer()` 改为"问题块在前、正文在后"的快照（**仍是单一返回值**，保持原有截断语义）：

```python
    def _render_answer(self) -> str:
        """当前累积内容（澄清问题块 + 正文）；超过长度上限时保留尾部。"""
        if self._clarify_text and self._text:
            composed = f"{self._clarify_text}\n\n{self._text}"
        elif self._clarify_text:
            composed = self._clarify_text
        else:
            composed = self._text
        if len(composed) > self._max_chars:
            return composed[-self._max_chars :]
        return composed
```

④ `update` 里把**丢弃分支**换成渲染（保留 `return`，**不 finalize**）：

```python
        elif isinstance(event, SSEAskUserEvent):
            # 澄清问题要用户看见：渲染进累积内容（快照），但不 finalize——
            # 回合仍在挂起等待（见 design D14 与通道侧 spec）。
            rendered = render_questions(event.questions)
            if rendered:
                self._clarify_text = rendered
                self._last_sent = None  # 绕过"内容未变即跳过"，确保问题发得出去
                self._flush()
            return
```

- [ ] **Step 6: 跑测试确认通过 + 红线自查**

Run: `POSTGRES_HOST=localhost pytest tests/channels/ -q`（全绿；含拆分后的各文件）
Run: `wc -l src/channels/wecom/presenter.py src/channels/wecom/clarify_render.py tests/channels/test_wecom_presenter*.py tests/channels/wecom_presenter_fakes.py`
Expected: **每个文件都 < 400**；presenter ≈ 390。

- [ ] **Step 7: 提交**

```bash
git add src/channels/wecom/presenter.py src/channels/wecom/clarify_render.py src/config/wecom_channel.py tests/channels/wecom_presenter_fakes.py tests/channels/test_wecom_presenter.py tests/channels/test_wecom_presenter_stream.py tests/channels/test_wecom_presenter_clarify.py
git commit -m "feat(channels): 澄清问题渲染进气泡（取代显式丢弃，不 finalize）；拆出渲染模块并拆分越线的 presenter 测试"
```

---

### Task 3: 文本答案解析 + 仅触发者注入

**Files:**
- Create: `src/channels/wecom/clarify_parse.py`
- Modify: `src/channels/wecom/handler.py`、`src/config/wecom_channel.py`
- Test: `tests/channels/test_wecom_clarify.py`（新建）、`tests/channels/test_wecom_handler.py`

**Interfaces:**
- Consumes: Task 1 的 `clarify_service.resolve_clarify_answer(svc, *, session_id, answers) -> bool`；Task 2 的渲染（问题形状一致）；阶段 3 的 `_triggers: BoundedTtlMap`、`registered_trigger(session_id) -> str | None`、`session.derive_session_id(...)`。
- Produces:
  - `clarify_parse.parse_answers(text: str, questions: list) -> list[dict] | None`（`None` = 不能作为答案）
  - `RagChannelHandler.__init__` 新增参数 `resolve_answer: ResolveAnswerCallable`（`build_default` 默认注入 `clarify_service.resolve_clarify_answer`）
  - `RagChannelHandler._questions: BoundedTtlMap`（`session_id → 问题清单 JSON`）

- [ ] **Step 1: 写失败测试（纯函数优先）**

创建 `tests/channels/test_wecom_clarify.py`：

```python
"""文本答案解析：单问/多问、选项匹配、multi_select、无效判定。"""

from src.channels.wecom.clarify_parse import parse_answers

_ONE = [{"id": "q1", "question": "选哪个口径？", "options": ["营收", "毛利"],
         "multi_select": False}]
_TWO = [
    {"id": "q1", "question": "哪个指标？", "options": ["营收", "毛利"], "multi_select": False},
    {"id": "q2", "question": "哪一期？", "options": None, "multi_select": False},
]


def test_single_question_option_hit_by_exact_text():
    assert parse_answers("毛利", _ONE) == [
        {"id": "q1", "selected": ["毛利"], "custom": ""}
    ]


def test_single_question_option_hit_by_index():
    assert parse_answers("2", _ONE) == [{"id": "q1", "selected": ["毛利"], "custom": ""}]


def test_single_question_falls_back_to_custom():
    assert parse_answers("看三年平均", _ONE) == [
        {"id": "q1", "selected": [], "custom": "看三年平均"}
    ]


def test_multi_select_splits_by_separators():
    q = [{"id": "q1", "question": "选指标", "options": ["营收", "毛利", "ROE"],
          "multi_select": True}]
    assert parse_answers("营收、ROE", q) == [
        {"id": "q1", "selected": ["营收", "ROE"], "custom": ""}
    ]


def test_two_questions_require_numbered_reply():
    assert parse_answers("1) 营收\n2) 2024 年", _TWO) == [
        {"id": "q1", "selected": ["营收"], "custom": ""},
        {"id": "q2", "selected": [], "custom": "2024 年"},
    ]


def test_two_questions_without_numbering_is_invalid():
    assert parse_answers("营收和 2024 年", _TWO) is None


def test_blank_text_is_invalid():
    assert parse_answers("   ", _ONE) is None


def test_no_questions_is_invalid():
    assert parse_answers("随便", []) is None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `POSTGRES_HOST=localhost pytest tests/channels/test_wecom_clarify.py -q`
Expected: FAIL —— `ModuleNotFoundError: No module named 'src.channels.wecom.clarify_parse'`。

- [ ] **Step 3: 实现解析（`src/channels/wecom/clarify_parse.py`）**

```python
"""把用户在企微里回复的文本解析成站点同构的澄清答案（design D14）。

站点前端有专用作答 UI（逐问提交结构化 answers）；企微只能用**文本回填**，
故本模块把一段文本映射成 `[{id, selected, custom}]`：
- 单问：命中选项（原文/序号）→ `selected`；否则整段作为 `custom`
- 多问：要求**编号回复**（`1) …` / `1. …` / `1、…`），逐条映射；无法编号 → 判定无效
- `multi_select`：先按顿号/逗号/空格切分再与选项比对

返回 `None` 表示"这段文本不能作为答案"，调用方应提示用户重答且**不消耗**挂起。
"""

from __future__ import annotations

import re

_INDEXED_LINE = re.compile(r"^\s*(\d+)\s*[).、]\s*(.+?)\s*$")
_SEPARATORS = re.compile(r"[、,，;；\s]+")


def _normalize(text: str) -> str:
    """去首尾空白并统一全角空格。"""
    return text.replace("\u3000", " ").strip()


def _match_options(text: str, options: list[str], *, multi: bool) -> list[str]:
    """把文本与选项比对，返回命中的选项（保序、去重）。

    Args:
        text: 用户文本（已 normalize）
        options: 候选项（纯字符串）
        multi: 是否多选

    Returns:
        命中的选项列表；无命中返回空列表
    """
    if not options:
        return []
    candidates = [_normalize(text)]
    lowered = text.lower()
    if multi:
        candidates = [part for part in _SEPARATORS.split(text) if part]
    hits: list[str] = []
    for option in options:
        option_norm = _normalize(option)
        option_lower = option_norm.lower()
        for candidate in candidates:
            if candidate and candidate.lower() == option_lower:
                if option not in hits:
                    hits.append(option)
                break
    if hits:
        return hits
    if len(options) == 1 and lowered == _normalize(options[0]).lower():
        return [options[0]]
    return []


def _answer_for(text: str, question: dict) -> dict:
    """把一段文本映射成单问的答案元素。"""
    options = question.get("options") or []
    multi = bool(question.get("multi_select"))
    hits = _match_options(text, [str(o) for o in options], multi=multi)
    if hits:
        return {"id": str(question.get("id", "")), "selected": hits, "custom": ""}
    return {"id": str(question.get("id", "")), "selected": [], "custom": text}


def parse_answers(text: str, questions: list) -> list[dict] | None:
    """把企微文本回复解析成站点同构答案列表。

    Args:
        text: 用户回复的原始文本
        questions: 渲染时使用的澄清问题清单（元素含 id/question/options/multi_select）

    Returns:
        站点同构答案列表；无法解析时返回 None
    """
    normalized = _normalize(text)
    if not normalized or not questions:
        return None
    valid = [q for q in questions if isinstance(q, dict) and str(q.get("id", ""))]
    if not valid:
        return None
    if len(valid) == 1:
        return [_answer_for(normalized, valid[0])]

    numbered: dict[int, str] = {}
    for line in normalized.splitlines():
        matched = _INDEXED_LINE.match(line)
        if matched is None:
            continue
        position = int(matched.group(1))
        numbered[position] = _normalize(matched.group(2))
    if not numbered:
        return None
    answers: list[dict] = []
    for position, question in enumerate(valid, start=1):
        body = numbered.get(position, "")
        answers.append(_answer_for(body, question))
    return answers
```

- [ ] **Step 4: 接入 handler（`src/channels/wecom/handler.py`）**

① 顶部 import 增：`from src.channels.wecom.clarify_parse import parse_answers`；`from src.services import clarify_service`；`import json`（若缺）。

② 类型别名（与既有 `StartTurnCallable` 并列）：

```python
ResolveAnswerCallable = Callable[..., Awaitable[bool]]
```

③ `__init__` 增参数与字段（并同步 docstring）：

```python
        resolve_answer: ResolveAnswerCallable,
        ...
        self._resolve_answer = resolve_answer
        self._questions: BoundedTtlMap = BoundedTtlMap(
            capacity=wecom_channel.TRIGGER_MAP_CAPACITY,
            ttl_seconds=wecom_channel.TRIGGER_MAP_TTL_SECONDS,
        )
```

`build_default` 增 `resolve_answer=clarify_service.resolve_clarify_answer`。

④ `_tap` 登记时**同时记问题**：

```python
            if isinstance(event, SSEAskUserEvent):
                self._triggers.put(session_id, from_userid)
                self._questions.put(session_id, json.dumps(event.questions, ensure_ascii=False))
                logger.info(
                    "[wecom] clarify pending registered session_id={} trace_id={}",
                    encode_value(session_id),
                    encode_value(current_trace_id.get() or ""),
                )
```

⑤ `__call__` 在 `_run_turn` **之前**插入答案注入分支：

```python
        if msg.msgtype == "event":
            self._handle_event(bot_key, msg)
            return
        if not msg.text:
            ...  # 既有 UNSUPPORTED 分支
            return
        if await self._try_resolve_clarify(bot_key, msg, sink):
            return
        await self._run_turn(bot_key, msg, sink)
```

⑥ 新增方法（放在 `_run_turn` 之前）：

```python
    async def _try_resolve_clarify(
        self, bot_key: str, msg: InboundMessage, sink: ReplySink
    ) -> bool:
        """若本条消息是"澄清触发者的答复"，解析并注入挂起的回合。

        非触发者的消息**不**进入本路径（会落到 `_run_turn`，由会话闸门给出忙提示）。

        Args:
            bot_key: 机器人别名
            msg: 入站消息（text 非空）
            sink: 回复出口

        Returns:
            True 表示已作为澄清答复处理（调用方**不得**再开新回合）
        """
        session_id = derive_session_id(
            bot_key=bot_key,
            chattype=msg.chattype,
            chatid=msg.chatid,
            from_userid=msg.from_userid,
        )
        trigger = self._triggers.get(session_id)
        if trigger is None or trigger != msg.from_userid:
            return False
        raw_questions = self._questions.get(session_id)
        if raw_questions is None:
            self._triggers.put(session_id, "")  # 登记与问题不一致：作废，走原路径
            return False
        questions = json.loads(raw_questions)
        answers = parse_answers(msg.text or "", questions)
        if answers is None:
            logger.info(
                "[wecom] clarify answer invalid session_id={} text_len={}",
                encode_value(session_id),
                len(msg.text or ""),
            )
            await sink.reply_stream(
                wecom_channel.WeComChannelTexts.CLARIFY_INVALID_TEXT, finish=True
            )
            return True  # 已回应，但**不消耗**挂起（用户可重答）
        svc = await self._get_service()
        delivered = await self._resolve_answer(
            svc, session_id=session_id, answers=answers
        )
        self._triggers.put(session_id, "")
        self._questions.put(session_id, "")
        logger.info(
            "[wecom] clarify answer delivered={} session_id={} answers={}",
            delivered,
            encode_value(session_id),
            len(answers),
        )
        if delivered:
            return True
        return False  # 挂起已超时/已消费：回落，当作新问题处理
```

- [ ] **Step 5: 加文案（`src/config/wecom_channel.py`）**

```python
    # 澄清答复无法解析时的提示（**不消耗**挂起，用户可重答）
    CLARIFY_INVALID_TEXT: str = "没看懂您的回复。请按上面的编号逐条作答，或直接回复选项文字。"
```

- [ ] **Step 6: 补 handler 级测试**

在 `tests/channels/test_wecom_handler.py` 追加（复用既有 `_handler(...)` 替身构造，新增 `resolve_answer` 注入与 `_questions` 造数）：

```python
@pytest.mark.asyncio
async def test_trigger_answer_is_injected_not_new_turn():
    """触发者的答复走注入：调用 resolve_answer 且**不**开新回合。"""
    recorded: dict[str, object] = {"resolves": []}

    async def _resolve(svc, *, session_id, answers):
        recorded["resolves"].append((session_id, answers))
        return True

    handler, recorder = _handler(resolve_answer=_resolve)
    session_id = derive_session_id(
        bot_key="dev", chattype="single", chatid=None, from_userid="U1"
    )
    handler._triggers.put(session_id, "U1")
    handler._questions.put(
        session_id, json.dumps([{"id": "q1", "question": "选哪个？",
                                 "options": ["甲", "乙"], "multi_select": False}])
    )
    sink = _Sink()

    await handler(_msg(text="乙"), sink)

    assert recorded["resolves"] == [(session_id, [{"id": "q1", "selected": ["乙"], "custom": ""}])]
    assert recorder["start_turn_calls"] == []  # 未开新回合
    assert sink.calls == []


@pytest.mark.asyncio
async def test_non_trigger_message_is_not_treated_as_answer():
    """非触发者的消息**不**注入（落原路径）。"""
    recorded: dict[str, object] = {"resolves": []}

    async def _resolve(svc, *, session_id, answers):
        recorded["resolves"].append(answers)
        return True

    handler, recorder = _handler(resolve_answer=_resolve)
    session_id = derive_session_id(
        bot_key="dev", chattype="group", chatid="CHAT9", from_userid="U1"
    )
    handler._triggers.put(session_id, "U1")
    handler._questions.put(session_id, json.dumps(
        [{"id": "q1", "question": "选哪个？", "options": ["甲"], "multi_select": False}]
    ))
    sink = _Sink()

    await handler(_msg(chattype="group", chatid="CHAT9", from_userid="U2", text="我也说一句"), sink)

    assert recorded["resolves"] == []
    assert len(recorder["start_turn_calls"]) == 1  # 走原路径


@pytest.mark.asyncio
async def test_invalid_answer_prompts_and_keeps_pending():
    """无法解析的答复：给提示、**不**调 resolve（挂起保留，用户可重答）。"""
    recorded: dict[str, object] = {"resolves": []}

    async def _resolve(svc, *, session_id, answers):
        recorded["resolves"].append(answers)
        return True

    handler, recorder = _handler(resolve_answer=_resolve)
    session_id = derive_session_id(
        bot_key="dev", chattype="single", chatid=None, from_userid="U1"
    )
    handler._triggers.put(session_id, "U1")
    handler._questions.put(session_id, json.dumps([
        {"id": "q1", "question": "哪个指标？", "options": ["营收"], "multi_select": False},
        {"id": "q2", "question": "哪一期？", "options": None, "multi_select": False},
    ]))
    sink = _Sink()

    await handler(_msg(text="营收和 2024 年"), sink)

    assert recorded["resolves"] == []
    assert sink.calls == [(WeComChannelTexts.CLARIFY_INVALID_TEXT, True)]
    assert recorder["start_turn_calls"] == []


@pytest.mark.asyncio
async def test_resolve_miss_falls_back_to_new_turn():
    """挂起已超时/已消费（resolve 返回 False）：回落为新回合。"""
    async def _resolve(svc, *, session_id, answers):
        return False

    handler, recorder = _handler(resolve_answer=_resolve)
    session_id = derive_session_id(
        bot_key="dev", chattype="single", chatid=None, from_userid="U1"
    )
    handler._triggers.put(session_id, "U1")
    handler._questions.put(session_id, json.dumps(
        [{"id": "q1", "question": "选哪个？", "options": ["甲"], "multi_select": False}]
    ))
    sink = _Sink()

    await handler(_msg(text="甲"), sink)

    assert len(recorder["start_turn_calls"]) == 1
```

（`_handler(...)` 需支持 `resolve_answer=` 透传；`json` 已在该测试文件 import —— 若缺请补。**不得**削弱既有断言。）

- [ ] **Step 7: 跑测试确认通过 + 全量**

Run: `POSTGRES_HOST=localhost pytest tests/channels/ -q` → 全 passed；
Run: `POSTGRES_HOST=localhost pytest tests/ -q` → 与基线一致（仅 `tests/parsers/` 环境性失败）。

- [ ] **Step 8: 提交**

```bash
git add src/channels/wecom/clarify_parse.py src/channels/wecom/handler.py src/config/wecom_channel.py tests/channels/test_wecom_clarify.py tests/channels/test_wecom_handler.py
git commit -m "feat(channels): 澄清答复按触发者解析并注入挂起回合（无效答复提示且不消耗）"
```

---

### Task 4: 文档登记 + change 工件同步（含"仅触发者"spec delta）

**Files:**
- Modify: `docs/agents/logging-rules.md`、`docs/agents/code-map.md`、`docs/agents/glossary.md`
- Modify: `docs/openspec/changes/wecom-agent-bridge/design.md`、`docs/openspec/changes/wecom-agent-bridge/specs/wecom-agent-bridge/spec.md`、`docs/openspec/changes/wecom-agent-bridge/tasks.md`

**Interfaces:**
- Consumes: Task 1–3 的落地行为。
- Produces: 文档与代码一致；change 的 spec delta 含"仅触发者回填"与"呈现"两条新 SHALL。

- [ ] **Step 1: `code-map.md` 登记两个新模块 + `logging-rules.md` 登记 `[clarify]` 前缀**

`docs/agents/logging-rules.md` 的「前缀主表（开放登记制）」追加一行（Task 1 的 service 用了该前缀，**未登记即不合规**）：

```
| `[clarify]` | 澄清答案编排（services/clarify_service） |
```

`docs/agents/code-map.md` 在企微通道层落点处追加：

```
> - `src/services/clarify_service.py`：澄清答案**编排**（下沉目标）：定位并 resolve 挂起 Future + 落库用户答案 + 文本格式化
> - `src/channels/wecom/clarify_parse.py`：把企微**文本回复**解析成站点同构 `answers=[{id,selected,custom}]`（单问/编号多问/multi_select/无效判定）
> - 澄清在企微的链路：投影层**渲染问题**（不 finalize）→ 用户回复 → handler 校验**仅触发者** → 解析 → `resolve_clarify_answer` → **原回合续跑、同一气泡继续累积**
```

- [ ] **Step 2: `glossary.md` 补术语**

追加两条（按该文件既有条目格式）：

```
- **澄清回填（clarify backfill）**：企微无站点那样的作答 UI，用户以**文本回复**作答；通道把文本解析成站点同构 `answers` 并注入挂起的 `ask_user` 回合。**仅触发者**：只有登记在案（`_triggers`）的触发者其回复才被当作答案。
- **澄清呈现**：把 `SSEAskUserEvent` 渲染成气泡内容（问题 + 可选项 + 提示）；**不 finalize**，因为原回合仍在挂起等待。
```

- [ ] **Step 3: change 的 design + spec delta 同步**

1. `design.md`：在 **D14** 之后补一段"**D14b 仅触发者回填与呈现（4b 实测/设计）**"，写清：① 澄清**不是新回合**（原 SSE 流续跑、同一气泡累积）；② 呈现方式（渲染进累积内容、不 finalize）；③ 仅触发者的判据（`_triggers` 登记值 == 发送者 userid）；④ 无效答复**不消耗**挂起；⑤ `resolve` 返回 False（超时/已消费）时回落为新回合。
2. `specs/wecom-agent-bridge/spec.md`：新增两条 SHALL（与该文件既有条目格式一致）：
   - **澄清呈现 SHALL**：收到 `ask_user` 事件时，通道 SHALL 把问题与可选项渲染进当前回复气泡，且**不得**在此事件上结束流式回复。
   - **仅触发者回填 SHALL**：仅当某入站文本的发送者与登记触发者一致时，通道才 SHALL 将其解析为澄清答案；不一致时 SHALL NOT 消耗挂起。解析失败时 SHALL 提示且不消耗。
3. `tasks.md`：勾选**组 6**（逐条对实际落地）与 **1.5**，并在组 6 后加一行注记：`> 4b 已完成并并入 dev-wsl（merge <填实际合并提交>）；确认点 C（真实连网：@dev 触发澄清 → 气泡见问题 → 回复 → 同气泡出答案；群内非触发者插话不被当答案）通过。`（**合并提交号在合并时回填**——若尚未合并，先写"待合并"。）

- [ ] **Step 4: 跑测试与文档闸门**

Run: `POSTGRES_HOST=localhost pytest tests/ -q`（与基线一致）；提交时 pre-commit 会跑 `check_docs` / `doc anti-rot` / `check-adr`（若报错按其提示改，**不得** `--no-verify`）。

- [ ] **Step 5: 提交**

```bash
git add docs/agents/logging-rules.md docs/agents/code-map.md docs/agents/glossary.md docs/openspec/changes/wecom-agent-bridge/design.md docs/openspec/changes/wecom-agent-bridge/specs/wecom-agent-bridge/spec.md docs/openspec/changes/wecom-agent-bridge/tasks.md
git commit -m "docs(agents,openspec): 登记澄清回填链路与术语，补 D14b 与两条 SHALL（呈现 / 仅触发者回填），勾选组 6 与 1.5"
```

---

## 本阶段覆盖对照（自我检查用，不入提交）

| 规范/设计条目 | 落点 |
|---|---|
| 组 6.1 澄清问题**呈现** | Task 2 |
| 组 6.2 **仅触发者**回填 | Task 3（+ Task 4 的 spec delta） |
| 组 6.3 答案解析（选项/自定义/多问/multi_select） | Task 3 |
| 组 6.4 无效/超时/无挂起的处理 | Task 3（无效→提示不消耗；resolve False→回落新回合） |
| **1.5** `resolve` 下沉到 services（站点/通道共用） | Task 1 |
| design D12（下沉入口）/ D14（仅触发者）/ D15（仅长连接） | Task 1 / Task 3 / 全阶段沿用 |
| 文档登记（code-map / glossary / change 工件） | Task 4 |

## 不在 4b 范围

- **卡片二期（4c）**：用 `task_id` + `event_key` 做澄清按钮（已实测可用：`docs/agents/wecom-sdk-facts.md` 的 E5）；点击回调走**同一条** `resolve_clarify_answer` 路径。本阶段只做文本回填。
- **多人协作澄清**（群内多人分别补充）：主规格无此能力，本阶段不做。
- **阶段 5（组 7）**：灰度 E2E、鲁棒验收、群聊验收、发布 runbook。

## 确认点 C 的前置（本阶段做完后执行，不属本文件任务）

1. 本地 `.env`：`WECOM_BOT_ENABLED=true` + `WECOM_BOTS` **只留 `dev` 一台**；生产 app 保持停止。
2. 起本地实例：`.superpowers/spike/local_accept_launcher.py`（`PYTHONPATH=. .venv/bin/python -u …`，端口 8001；**已改为默认跑主工作区**，可用 `LAUNCH_DIR` 覆盖到 worktree）。
3. 触发澄清：@ `dev` 发"请调用提问工具问我一个问题"之类的请求（阶段 3 实测有效话术见台账）；**等气泡里出现问题**（这是 4b 新增的呈现）。
4. 回复答案 → 观察**同一气泡**续跑出最终答案；另在群里让**第二个人**插话，确认其消息**不**被当作答案（应得忙提示或走新回合）。
5. 判据：日志出现 `[wecom] clarify answer delivered=True session_id=… answers=…`，且该轮 `trace_id` 与问题呈现帧同一条流。
