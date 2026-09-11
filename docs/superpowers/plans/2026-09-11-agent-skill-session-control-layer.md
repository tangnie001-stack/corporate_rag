# 智能体/技能 会话·调用控制·prompt·交付层 Implementation Plan（Plan 3 / 4）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让「会话绑定智能体」（bind-once、无 400）与「`/xxx` 前缀调用」真正可用：`sessions.agent` 落库并在 `sessions/list` 回显、请求体可带 `agent`、system prompt 按三层组装（人设/环境约束）、`/xxx` 触发 inline 单轮或 fork 直出、能力清单两个只读接口可用，并补齐 Plan 2 遗留的**直出轮回答交付与落库**、确认门、预设预绑定预加载。

**Architecture:** 四块：① **存储链**——`sessions.agent` 走手工 SQL 迁移 + `ChatRepo.bind_session_agent`（`UPDATE … WHERE agent=''`，只写一次）实现 bind-once，不一致只忽略 + warn；② **前缀层**——新增 `src/agents/skills/prefix.py` 的**纯函数** `parse_prefix` / `clean_prefix`（解析与读时清洗共用同一实现），`AgentService` 是唯一调用点；③ **prompt 层**——`PromptManager.get_base_system_prompt()` 抽出"基础段"，新增 `build_system_prompt(persona, kb_bound, has_skills)` 组织"人设层 + 环境约束层"，保证未选 agent 时 system 段**逐字不变**；④ **交付层**——`skill_direct` 直出轮的回答经 `_convert_event` 变为 SSE 事件并被 `serialize_process` 落库（Plan 2 遗留的 C1），确认门与预加载收尾。

**Tech Stack:** Python 3.11+ / FastAPI / SQLAlchemy(async) + MySQL 8 / Redis / LangGraph 1.2.9 / LangChain 1.3.11 / pytest。**无新增依赖。**

**Spec:** `docs/openspec/changes/session-agent-and-skill-invocation/`（proposal.md / design.md / specs/ / tasks.md）——本计划实现其中的 §5.4–5.14、§7 中依赖本轮实现的条目，以及被移入的 §4.6（确认门）与 §4.8（预设预绑定）。

**前置（Plan 1 / Plan 2 已交付，勿重做）**：`SkillRecord`/`AgentPreset` 模型与加载器、双轴推导与 `user_visible()`/`model_visible()`（§1/§2/§3.1–3.3/§5.1–5.3）；fork 子上下文隔离、`DelegateRun`、`create_agent` 子代理、工具 seam、执行者选择 + `maxTurns`、verify 判据随材料走、图入口分派 + `skill_direct` 直出节点（§4.1–4.5/4.7/4.9–4.11）。

## Global Constraints

- 注释与文档一律**中文**；每个函数写 docstring；每个 dataclass 字段加**行内注释**（来源/范围/用途）。
- **不用三元表达式**，写完整 `if/else`。
- **类型不确定的值不用 `getattr(x, "attr", default)` 隐式兜底**，用 `isinstance` / `x.attr if x is not None else default` 显式判断。
- 常量/阈值/文案集中到 `src/config/`：`settings.py` 运行参数 / `prompts.py` LLM 提示词与消息模板 / `const.py` 事件与固定阈值、`SSEInteractionTexts` 用户可见文案。
- 单文件 < 400 行；单函数 < 80 行。
- 日志：事件消息**英文 k=v** + `[层名]` 前缀；**新增事件先登记 `src/core/log_events.py` 的 `Event` + `src/core/log_event_specs.py` 的 `EVENT_SPECS`（同名同集，否则 import 期 AssertionError）再启用**。
- API 路由 handler 必须标注请求体与返回类型；响应走统一信封 `ResponseModel`（`data=`）。
- **请求字段 `agent` 的值是预设 `name`（ASCII slug），不是 id**；不一致**忽略 + warning**，**无 400**。
- **落库保留原文**：`/name ` 前缀只影响"组装 prompt 时的读取"，`conversation_history` 与 Redis 历史里存的仍是用户原话（design D25）。
- 测试 **mock 外部依赖**，不发真实网络调用；`tests/infra/db/test_mysql_db.py` 既有范式是用真实 `session_factory`（需 docker MySQL 在跑）——沿用既有范式，不要另造。测试输出保持**无未断言 warning**。
- 门禁：`pytest tests/ -v` 全绿、`ruff check .` 无错、`pyright src/` 不新增 error、`python -m src.cli.check_docs` 0 error。**只格式化本次改动的文件**（`ruff format` 传具体路径，切勿 `ruff format .`）。
- 契约变更须同步 `docs/agents/api_contract.md` 与受影响测试断言。

## 预检扫描

### A. 任务对 / 接缝

| pair | produces → consumes | 结论 |
|---|---|---|
| T1 → T3 | `ChatRepo.bind_session_agent` / `SessionItem.agent` ← bind-once 写与回显 | 一致；T3 依赖 T1 |
| T2 → T5 | `parse_prefix` / `clean_prefix` ← 生成入口解析与读时清洗 | 一致；T2 先做（纯函数，可独立测） |
| T3 → T5 | `ctx.agent` / `agent_used` 事件 ← 生成入口 | **顺序**：T3 先于 T5（T5 要用已绑定的 `ctx.agent`） |
| T4 → T5 | `build_system_prompt(persona, ...)` ← 注入人设 | 一致；**逐字不变守卫**必须在 T4 内建 |
| T5 → T6 | 直出轮 `state.answer` ← 交付/落库 | **Plan 2 遗留 C1**：T5 让直出可触发，T6 让回答可见可落库。T6 必须走**生产交付链**测试 |
| T6 → T7 | 交付链上的 `_needs_regenerate` 语义 ← 确认门互斥 | 一致 |
| T3 → T8 | 会话绑定值 + `skills` 声明 ← 预加载 | 一致；T8 依赖 T3 |
| T9 独立 | `GET /api/skills` / `GET /api/agents` | 一致；只消费 Plan 1 的 registry，不依赖 T5 |
| T10 → 全部 | 文档/契约/门禁 | 一致 |

### B. 单任务自洽性

| task | 检查 | 结论 |
|---|---|---|
| T1 存储链 | 手工 SQL 与 `SessionModel` 字段必须同名同默认（`agent VARCHAR(64) NOT NULL DEFAULT ''`）；`bind_session_agent` 的"只写一次"语义与 `create_session` 的幂等不冲突 | 一致 |
| T2 前缀纯函数 | 三态（plain/known/unknown）与 `<720px` 无关；`clean_prefix` 复用 `parse_prefix` | 一致 |
| T3 bind-once | 绑定写入必须在 `StreamingResponse` 之前；`agent_used` 是流事件不是响应体 | 一致 |
| T4 prompt 三层 | "未选 agent 时逐字不变"与"抽出 `get_base_system_prompt`"是否真等价 —— 用**快照测试**判别 | 一致（T4 建快照守卫） |
| T5 生成入口 | 前缀清洗要覆盖**当前轮 + 历史**两条读取路径（`AgentState._history` 与 `query_router._format_history`），且**落库仍原文** | 需在 T5 内明确两条路径 |
| T6 交付 | `serialize_process` 只认 `"token"`；直出轮无 token → 必须给 `skill_direct` 补一条产出或让 `serialize_process` 回落 | 一致（T6 给候选并择一） |
| T7 确认门 | 子代理不持有 `ask_user`（Plan 2 已由 `FORK_FORBIDDEN_TOOLS` 保证）→ 需 marker + 编排层检测 | 一致 |
| T8 预加载 | "首轮一次 + 隐藏消息 + 不进 system prompt"与 `_history` 组装点（`agent_node._initial_messages`）的关系 | 需在 T8 内明确注入点 |
| T9 清单接口 | api 层只转发（层间规则）、失败 fail-open | 一致 |

### C. Rulings（预检）

- **P3-R1**：`/xxx` 的解析与清洗做成**纯函数**（`parse_prefix(text, known_names)` / `clean_prefix(text, known_names)`）而不是方法，便于单测且避免把 registry 传进纯逻辑。**若判断有误**：改为接收 registry（测试需造 registry）。
- **P3-R2**：`agent` 的绑定点放在 **`AgentService.stream_chat` 之内、返回之前**（Plan 2 的 `stream_chat` 已经在这里建 `ctx`），而不是 API 层——因为只有服务层持有 `AgentPresetRegistry` 与 `ChatManager`。**若判断有误**：需把 registry 提升到 API 层依赖注入。
- **P3-R3**：`agent_used` 复用既有 `model_info` 同层位置（post-loop 事件），**新增独立 `SSEAgentUsedEvent`**（不复用 `SSEModelInfoEvent` 的字段）——因为语义不同（会话绑定值 vs 本轮模型）且前端纠正顶栏需要独立事件名。**若判断有误**：前端要多解析一个字段。
- **P3-R4**：`build_system_prompt(persona, kb_bound, has_skills)` 放 **`src/rag/prompt.py`**（`build_prompt` 的归属文件），`get_base_system_prompt()` 在 `PromptManager` 内——保持"prompt 组装在 rag/prompt.py、prompt 取值在 PromptManager"的既有分工。**若判断有误**：两处调用点要改 import。
- **P3-R5**：T6 的直出交付**择"`_convert_event` 捕获 `skill_direct` 的 `on_chain_end` 产出 token 事件"**为默认方案（另一候选"`serialize_process` 回落"只作为兜底同时实现）。理由：token 事件是既有交付链的正规入口，前端零改动即可显示。**若判断有误**：改走 serialize 回落，前端仍可显示但无流式。

---

### Task 1: `sessions.agent` 存储链（手工迁移 + repo + 透传 + 接口回显）

**Files:**
- Create: `scripts/migrations/2026-09-11-add-session-agent.sql`
- Modify: `src/infra/db/models/chat.py:18`（`SessionModel` 加列）
- Modify: `src/infra/db/mysql_db/chat_repo.py`（`create_session` 落 agent、`get_sessions` SELECT 加 agent、新增 `bind_session_agent`）
- Modify: `src/chat/persistence.py`（`save_session` 透传 agent、新增 `bind_session_agent`）
- Modify: `src/chat/manager.py`（`save_session_async` 透传、新增 `bind_session_agent_async`）
- Modify: `src/api/model/response.py:160-169`（`SessionItem` 加 `agent`）
- Modify: `src/api/sessions.py:32-67`（list 透传 `agent`）
- Test: `tests/infra/db/test_mysql_db.py`、`tests/chat/test_persistence_process.py`、`tests/api/test_sessions.py`（均**追加**用例）

**Interfaces:**
- Produces（后续任务按这些签名调用）：
  - `SessionModel.agent: Mapped[str]`（`String(64)`，`default=""`，DB 列 `VARCHAR(64) NOT NULL DEFAULT ''`）
  - `ChatRepo.bind_session_agent(session_id: str, agent: str) -> bool`（`UPDATE sessions SET agent=:a WHERE id=:sid AND agent=''`；返回**本次是否写入**）
  - `ChatRepo.get_sessions(user_id: str = "") -> list`（返回的 Row 多一个 `.agent`）
  - `PersistenceService.save_session(session_id, title, kb_id, user_id="", agent="")` / `PersistenceService.bind_session_agent(session_id, agent) -> bool`
  - `ChatManager.save_session_async(session_id, title, kb_id, user_id="", agent="")` / `ChatManager.bind_session_agent_async(session_id, agent) -> bool`
  - `SessionItem.agent: str = ""`

- [ ] **Step 1: 写失败测试（repo 层，沿用本文件既有"真实 session_factory"范式）**

`tests/infra/db/test_mysql_db.py` 追加（文件顶部若无 `import uuid` 则补上）：

```python
async def test_bind_session_agent_is_bind_once():
    """bind-once：首次写入成功；再次写入（含不同值）不改动已有绑定。"""
    from src.infra.db.engine import session_factory
    from src.infra.db.models.chat import SessionModel
    from src.infra.db.mysql_db import ChatRepo

    chat_repo = ChatRepo(session_factory)
    sid = f"sess_{uuid.uuid4().hex[:12]}"
    await chat_repo.create_session(
        SessionModel(id=sid, user_id="u_agent", title="绑定测试", kb_id="")
    )

    assert await chat_repo.bind_session_agent(sid, "finance-expert") is True
    assert await chat_repo.bind_session_agent(sid, "legal-expert") is False

    rows = await chat_repo.get_sessions("u_agent")
    target = [row for row in rows if row.id == sid]
    assert len(target) == 1
    assert target[0].agent == "finance-expert"


async def test_create_session_with_agent_persists():
    """create_session 落 agent；未传时落空串（存量语义不变）。"""
    from src.infra.db.engine import session_factory
    from src.infra.db.models.chat import SessionModel
    from src.infra.db.mysql_db import ChatRepo

    chat_repo = ChatRepo(session_factory)
    sid = f"sess_{uuid.uuid4().hex[:12]}"
    await chat_repo.create_session(
        SessionModel(id=sid, user_id="u_agent", title="带智能体", kb_id="", agent="finance-expert")
    )

    rows = await chat_repo.get_sessions("u_agent")
    target = [row for row in rows if row.id == sid]
    assert len(target) == 1
    assert target[0].agent == "finance-expert"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/infra/db/test_mysql_db.py -k "bind_session_agent or create_session_with_agent" -v`
Expected: FAIL —— `SessionModel` 无 `agent` 字段 / `ChatRepo` 无 `bind_session_agent`（MySQL 未加列时还会报 Unknown column）

- [ ] **Step 3: 写迁移文件（模板与 `2026-08-31-add-message-status.sql` 一致）**

`scripts/migrations/2026-09-11-add-session-agent.sql`：

```sql
-- 为 sessions 增加 agent 列（本会话绑定的智能体预设名；'' = 未绑定）
-- 执行：docker exec -i corporate-rag-mysql mysql -uroot -pfinancial_qa_pass financial_qa < 本文件
ALTER TABLE sessions
  ADD COLUMN agent VARCHAR(64) NOT NULL DEFAULT ''
  COMMENT '会话绑定的智能体预设名（ASCII slug；空=未绑定）';
```

并在报告里给出**实际执行结果**（表已存在列时 MySQL 会报 `Duplicate column name`，属预期，记录即可）。

- [ ] **Step 4: 加 ORM 列**

`src/infra/db/models/chat.py` 的 `SessionModel` 内（`kb_id` 之后）：

```python
    agent: Mapped[str] = mapped_column(
        String(64), default="", comment="会话绑定的智能体预设名（ASCII slug；空=未绑定）"
    )
```

- [ ] **Step 5: repo 三处改动**

`src/infra/db/mysql_db/chat_repo.py`：
- 顶部 import 补 `update`（与既有 `select` / `func` 同处）。
- `create_session` 内构造处补 agent（显式判空，不用 `or`/三元）：

```python
                agent = session.agent
                if agent is None:
                    agent = ""
                s_obj = SessionModel(
                    id=session.id,
                    user_id=session.user_id,
                    title=session.title,
                    kb_id=session.kb_id,
                    agent=agent,
                )
```
- `get_sessions` 的 `select(...)` 列表补 `SessionModel.agent,`。
- 新增方法：

```python
    async def bind_session_agent(self, session_id: str, agent: str) -> bool:
        """首次绑定会话智能体（bind-once）：仅当当前绑定为空时写入。

        Args:
            session_id: 会话 ID
            agent: 已校验的智能体预设名（ASCII slug）

        Returns:
            True=本次写入成功（此前未绑定）；False=已绑定，未改动
        """
        async with self._sf() as s:
            stmt = (
                update(SessionModel)
                .where(SessionModel.id == session_id, SessionModel.agent == "")
                .values(agent=agent)
            )
            result = await s.execute(stmt)
            await s.commit()
            return result.rowcount > 0
```

- [ ] **Step 6: 透传（persistence + manager）**

`src/chat/persistence.py`：`save_session` 签名加 `agent: str = ""` 并在构造 `SessionModel(...)` 时传入；新增：

```python
    async def bind_session_agent(self, session_id: str, agent: str) -> bool:
        """委托 repo 首次绑定会话智能体（bind-once）。"""
        return await self._chat_repo.bind_session_agent(session_id, agent)
```

`src/chat/manager.py`：`save_session_async` 签名加 `agent: str = ""` 并透传 `self._persistence.save_session(...)`；新增 `bind_session_agent_async` 委托 `self._persistence.bind_session_agent(...)`。

- [ ] **Step 7: 接口回显（`SessionItem` + `/sessions/list`）**

`src/api/model/response.py` 的 `SessionItem` 追加（行内注释写清来源）：

```python
    agent: str = ""  # 会话绑定的智能体预设名（来源：sessions.agent；空=未绑定）
```

`src/api/sessions.py` 的 `list_sessions` 里构造 `SessionItem` 时补 `agent=item.agent`（`item` 是 repo 返回的 Row，Step 5 已让 SELECT 含该列）。

- [ ] **Step 8: 补接口与透传测试**

`tests/api/test_sessions.py` 追加：mock service 返回的会话项带 `agent`，断言 `/sessions/list` 的 `data[0]["agent"] == "finance-expert"`，且 `data` 仍是**数组**（`sessions/messages` 契约不变的同批守卫）。
`tests/chat/test_persistence_process.py` 追加：`PersistenceService(MagicMock()).save_session(..., agent="finance-expert")` → `repo.create_session.call_args[0][0].agent == "finance-expert"`。

- [ ] **Step 9: 跑测试确认通过**

Run: `pytest tests/infra/db/test_mysql_db.py tests/chat/ tests/api/test_sessions.py -v && pytest tests/ -q`
Expected: PASS

- [ ] **Step 10: 提交**

```bash
git add scripts/migrations/2026-09-11-add-session-agent.sql src/infra/db/models/chat.py src/infra/db/mysql_db/chat_repo.py src/chat/persistence.py src/chat/manager.py src/api/model/response.py src/api/sessions.py tests/infra/db/test_mysql_db.py tests/chat/test_persistence_process.py tests/api/test_sessions.py
git commit -m "feat(sessions): sessions.agent 存储链与 bind-once 写入，sessions/list 回显"
```

---

### Task 2: `/xxx` 前缀解析与读时清洗（纯函数）

**Files:**
- Create: `src/agents/skills/prefix.py`
- Modify: `src/config/const.py`（`SSEInteractionTexts` 加未注册前缀文案）
- Test: `tests/agents/skills/test_skill_prefix.py`（新建）

**Interfaces:**
- Produces：
  - `PrefixParse(kind: str, skill_name: str, task: str, record: SkillRecord | None)` dataclass；`kind ∈ {"plain", "known", "unknown"}`
  - `parse_prefix(text: str, known_names: set[str], registry) -> PrefixParse` —— **`registry` 只用于 `known` 时取 `SkillRecord`**，`known_names` 用于三态判定（便于纯测试）
  - `clean_prefix(text: str, known_names: set[str]) -> str` —— 读时清洗（design D25），**内部复用 `parse_prefix`**
  - `SSEInteractionTexts.UNKNOWN_SKILL_PREFIX`（未注册前缀的用户可见文案）

**为什么这样切**：三态判定必须能区分"不是命令"（按普通文本）与"是命令但未注册"（要显式告知用户 + 可用列表），所以 `parse_prefix` 不能只返回 `Optional`；把 `known_names` 与 `registry` 分开传，使核心判定成为可纯测的集合运算。

- [ ] **Step 1: 写失败测试**

`tests/agents/skills/test_skill_prefix.py`：

```python
"""`/xxx` 前缀解析与读时清洗（design D22/D25）。"""

from pathlib import Path

from src.agents.skills.models import SkillContext, SkillRecord
from src.agents.skills.prefix import clean_prefix, parse_prefix


class _FakeRegistry:
    """只实现 get，供 parse_prefix 取 record。"""

    def __init__(self, records: list[SkillRecord]):
        self._by_name = {r.name: r for r in records}

    def get(self, name: str):
        return self._by_name.get(name)


def _fork(name: str = "finance-analyst") -> SkillRecord:
    return SkillRecord(
        name=name,
        description="d",
        context=SkillContext.FORK,
        fork_body="任务：$ARGUMENTS",
        source_path=Path(f"/tmp/{name}/SKILL.md"),
    )


def _inline(name: str = "finance-qa") -> SkillRecord:
    return SkillRecord(
        name=name,
        description="d",
        context=SkillContext.INLINE,
        inline_prompt="方法论 $ARGUMENTS",
        source_path=Path(f"/tmp/{name}/SKILL.md"),
    )


def test_plain_text_is_not_a_command():
    """不以 / 开头 → plain，task 为原文。"""
    parsed = parse_prefix("帮我分析腾讯", {"finance-analyst"}, _FakeRegistry([]))
    assert parsed.kind == "plain"
    assert parsed.task == "帮我分析腾讯"
    assert parsed.record is None


def test_known_fork_skill_is_parsed():
    """命中已注册 fork skill → known，task 为去掉前缀后的文本。"""
    registry = _FakeRegistry([_fork()])
    parsed = parse_prefix("/finance-analyst 腾讯2024", {"finance-analyst"}, registry)
    assert parsed.kind == "known"
    assert parsed.skill_name == "finance-analyst"
    assert parsed.task == "腾讯2024"
    assert parsed.record is not None
    assert parsed.record.context == SkillContext.FORK


def test_known_inline_skill_is_parsed():
    """命中已注册 inline skill → known。"""
    registry = _FakeRegistry([_inline()])
    parsed = parse_prefix("/finance-qa 毛利率怎么算", {"finance-qa"}, registry)
    assert parsed.kind == "known"
    assert parsed.record is not None
    assert parsed.record.context == SkillContext.INLINE


def test_bare_slash_is_plain():
    """/ 开头但后接空白 → 不是命令形态 → plain。"""
    parsed = parse_prefix("/ 帮我看看", {"finance-analyst"}, _FakeRegistry([]))
    assert parsed.kind == "plain"


def test_slash_path_is_plain():
    """/ 开头但含路径分隔符（非 ASCII slug）→ plain（不误判为命令）。"""
    parsed = parse_prefix("/api/v1/kbs 是什么", {"finance-analyst"}, _FakeRegistry([]))
    assert parsed.kind == "plain"


def test_unknown_skill_is_reported_not_silent():
    """形如 skill 名但未注册 → unknown（不静默按普通文本）。"""
    parsed = parse_prefix("/ghost 任务", {"finance-analyst"}, _FakeRegistry([]))
    assert parsed.kind == "unknown"
    assert parsed.skill_name == "ghost"


def test_clean_prefix_strips_known_prefix_only():
    """读时清洗：只剥已注册技能的 /name 前缀，其它原样保留（D25）。"""
    known = {"finance-analyst"}
    assert clean_prefix("/finance-analyst 腾讯2024", known) == "腾讯2024"
    assert clean_prefix("/ghost 任务", known) == "/ghost 任务"
    assert clean_prefix("普通问题", known) == "普通问题"
    assert clean_prefix("/finance-analyst", known) == ""


def test_clean_prefix_is_idempotent():
    """已清洗过的文本再清洗不变（历史消息可能被清洗多轮）。"""
    known = {"finance-analyst"}
    once = clean_prefix("/finance-analyst 腾讯2024", known)
    assert clean_prefix(once, known) == once
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/agents/skills/test_skill_prefix.py -v`
Expected: FAIL —— `ModuleNotFoundError: src.agents.skills.prefix`

- [ ] **Step 3: 实现 `prefix.py`**

```python
"""`/xxx` 前缀解析与读时清洗（design D22/D25）。

解析（生成入口用）：行首 `/name` 且 name 形如 ASCII slug ⇒ 命令形态；
  命中注册表 ⇒ known（task = 去掉前缀后的文本）；
  未命中 ⇒ unknown（必须显式告知用户，不静默降级为普通文本）。
清洗（组装 prompt 前用）：只剥**已注册**技能的 `/name ` 前缀，其余原样返回；
  与解析共用同一实现，保证"能触发的才清洗"口径一致。落库保留原文。
"""

import re
from dataclasses import dataclass

from src.agents.skills.models import SkillRecord

PREFIX_PATTERN = re.compile(r"^/([A-Za-z0-9][A-Za-z0-9_-]*)(?:\s+([\s\S]*))?$")
"""命令形态：行首 / + ASCII slug + 可选空白 + 其余任务文本。"""


@dataclass
class PrefixParse:
    """一次前缀解析的结果。

    kind: 三态 —— plain（非命令）/ known（命中）/ unknown（形如命令但未注册）
    skill_name: 解析出的技能名（kind=plain 时为空串）
    task: 任务文本（known 为去前缀后的剩余文本；plain 为原文）
    record: 命中的 SkillRecord（仅 kind=known 非空）
    """

    kind: str  # plain / known / unknown
    skill_name: str  # 解析出的技能名
    task: str  # 任务文本
    record: SkillRecord | None  # 命中的技能记录


def parse_prefix(text: str, known_names: set[str], registry) -> PrefixParse:
    """解析行首 `/name` 前缀。

    Args:
        text: 用户输入原文
        known_names: 已注册技能的可见名集合（用于三态判定）
        registry: 技能注册表（仅 kind=known 时按 name 取 SkillRecord）

    Returns:
        PrefixParse；非命令形态返回 kind="plain" 且 task 为原文
    """
    match = PREFIX_PATTERN.match(text)
    if match is None:
        return PrefixParse(kind="plain", skill_name="", task=text, record=None)
    name = match.group(1)
    rest = match.group(2)
    if rest is None:
        rest = ""
    if name not in known_names:
        return PrefixParse(kind="unknown", skill_name=name, task=rest, record=None)
    record = registry.get(name)
    if record is None:
        return PrefixParse(kind="unknown", skill_name=name, task=rest, record=None)
    return PrefixParse(kind="known", skill_name=name, task=rest, record=record)


def clean_prefix(text: str, known_names: set[str]) -> str:
    """剥掉行首**已注册**技能的 `/name ` 前缀（design D25）。

    Args:
        text: 待清洗文本（当前轮 query 或历史 user 消息）
        known_names: 已注册技能的可见名集合

    Returns:
        去前缀后的文本；非命令形态或未注册前缀原样返回（保证幂等）
    """
    match = PREFIX_PATTERN.match(text)
    if match is None:
        return text
    if match.group(1) not in known_names:
        return text
    rest = match.group(2)
    if rest is None:
        return ""
    return rest
```

- [ ] **Step 4: 加未注册前缀文案**

`src/config/const.py` 的 `SSEInteractionTexts` 内（挨着既有 `DELEGATE_UNKNOWN_SKILL`）：

```python
    UNKNOWN_SKILL_PREFIX: str = "技能不存在：/{skill}。可用技能：{available}"
```

- [ ] **Step 5: 跑测试确认通过**

Run: `pytest tests/agents/skills/test_skill_prefix.py -v`
Expected: PASS（8 passed）

- [ ] **Step 6: 提交**

```bash
git add src/agents/skills/prefix.py src/config/const.py tests/agents/skills/test_skill_prefix.py
git commit -m "feat(skills): /xxx 前缀解析与读时清洗纯函数（三态 + 幂等）"
```

---

### Task 3: 请求体 `agent` 字段 + 会话 bind-once + `agent_used` 流事件

**Files:**
- Modify: `src/api/model/request.py:73-79`（`ChatStreamRequest` 加 `agent`）
- Modify: `src/api/chat.py`（把 `body.agent` 传进 `svc.agent_service.stream_chat(...)`）
- Modify: `src/services/agent_service.py`（存 `self._preset_registry`；`stream_chat` 加 `agent=""` 形参并在返回前完成绑定；`_run_generation` 发 `agent_used`）
- Modify: `src/utils/sse.py`（新增 `SSEAgentUsedEvent` + `to_sse` 分派 + `from_payload` 反解）
- Test: `tests/services/test_session_agent_binding.py`（新建）、`tests/chat/test_process_log.py` 或 `tests/chat/test_streaming.py`（补 `agent_used` 序列化）、`tests/api/test_chat.py`（补 body 透传）

**Interfaces:**
- Consumes：T1 的 `ChatManager.bind_session_agent_async`；Plan 2 T5 已构造的 `AgentPresetRegistry`（本任务把它提升为 `self._preset_registry`）
- Produces：
  - `ChatStreamRequest.agent: str = ""`
  - `AgentService.stream_chat(kb_id, session_id, query, deep_thinking=False, agent="")`
  - `AgentService._resolve_session_agent(session_id: str, requested: str) -> str`（返回**生效值**；内部完成 bind-once 与日志）
  - `SSEAgentUsedEvent(agent: str = "", type: str = "agent_used", seq: int | None = None)`

- [ ] **Step 1: 写失败测试**

`tests/services/test_session_agent_binding.py`：

```python
"""会话智能体 bind-once：首轮绑定 / 沿用 / 忽略不一致 + warn / 未注册降级。"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.services.agent_service import AgentService


def _service(bound: str = "", known: list[str] | None = None) -> tuple[AgentService, AsyncMock]:
    """构造只带绑定所需依赖的 AgentService（跳过 __init__）。"""
    svc = AgentService.__new__(AgentService)
    svc._chat_manager = AsyncMock()
    svc._chat_manager.bind_session_agent_async = AsyncMock(return_value=True)
    registry = MagicMock()
    registry.get = MagicMock(
        side_effect=lambda name: MagicMock(name=name) if name in (known or []) else None
    )
    svc._preset_registry = registry
    return svc, svc._chat_manager


async def test_binds_on_first_request():
    """未绑定 + 合法 → 固化并返回该值。"""
    svc, chat_manager = _service(known=["finance-expert"])
    assert await svc._resolve_session_agent("sess_1", "finance-expert") == "finance-expert"
    chat_manager.bind_session_agent_async.assert_awaited_once_with("sess_1", "finance-expert")


async def test_empty_request_keeps_unbound():
    """未绑定 + 传入空 → 保持未绑定（不写库）。"""
    svc, chat_manager = _service(known=["finance-expert"])
    assert await svc._resolve_session_agent("sess_1", "") == ""
    chat_manager.bind_session_agent_async.assert_not_awaited()


@pytest.mark.asyncio
async def test_inconsistent_request_is_ignored_with_warning(monkeypatch):
    """已绑定 + 传入不同值 → 忽略传入值、按绑定值继续，记 warning（不阻断）。"""
    svc, chat_manager = _service(known=["finance-expert", "legal-expert"])
    warned: list[dict] = []
    monkeypatch.setattr(
        "src.services.agent_service.core_logging.log_event",
        lambda event, **fields: warned.append({"event": event, **fields}),
    )

    result = await svc._resolve_session_agent("sess_1", "legal-expert", bound="finance-expert")

    assert result == "finance-expert"
    chat_manager.bind_session_agent_async.assert_not_awaited()
    assert any("agent" in str(item) for item in warned)


async def test_unknown_request_is_ignored():
    """未绑定 + 传入未注册名 → 忽略 + 返回空（降级系统默认）。"""
    svc, _ = _service(known=[])
    assert await svc._resolve_session_agent("sess_1", "ghost") == ""
```

> 说明：`bound` 通过形参注入，避免测试依赖 repo 读取——实现时 `_resolve_session_agent(session_id, requested, bound="")`，生产由 `stream_chat` 从 `sessions/list` 或 `get_session_by_id` 取已绑定值后传入（见 Step 3）。

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/services/test_session_agent_binding.py -v`
Expected: FAIL —— `AttributeError: 'AgentService' object has no attribute '_resolve_session_agent'`

- [ ] **Step 3: 实现绑定（`agent_service.py`）**

- `__init__` 里把 Plan 2 T5 的局部 `preset_registry` 保存为 `self._preset_registry = preset_registry`（未命中分支存 `None`）。
- `stream_chat` 签名加 `agent: str = ""`；在返回 `(_, launch_context)` **之前**：

```python
        bound_raw = await self._chat_manager.get_session_agent_async(session_id)
        effective_agent = await self._resolve_session_agent(session_id, agent, bound=bound_raw)
        ctx.agent = effective_agent
        launch_context["agent"] = effective_agent
```

> `get_session_agent_async` 是 T1 的 `ChatManager` 上需要补的一个薄方法（`await self._persistence.get_session_agent(session_id)` → repo `get_session_by_id(...).agent`）；若不想新增，可复用 `get_sessions` 过滤——**择一，本任务内完成并在报告里写明**。

```python
    async def _resolve_session_agent(
        self, session_id: str, requested: str, bound: str = ""
    ) -> str:
        """解析本会话生效的智能体名（bind-once，无 400）。

        Args:
            session_id: 会话 ID
            requested: 请求体传入的智能体名（可为空）
            bound: 会话已绑定的智能体名（空=未绑定）

        Returns:
            生效值；已绑定一律返回绑定值（传入不一致 → 忽略 + warning）
        """
        if bound:
            if requested and requested != bound:
                core_logging.log_event(
                    Event.AGENT_BIND_IGNORED,
                    session_id=session_id,
                    bound=bound,
                    requested=requested,
                )
            return bound
        if not requested:
            return ""
        if self._preset_registry is None or self._preset_registry.get(requested) is None:
            core_logging.log_event(
                Event.AGENT_BIND_IGNORED, session_id=session_id, bound="", requested=requested
            )
            return ""
        await self._chat_manager.bind_session_agent_async(session_id, requested)
        return requested
```

- [ ] **Step 4: 加 `agent` 请求字段与接线**

`src/api/model/request.py` 的 `ChatStreamRequest`：

```python
    agent: str = ""  # 会话绑定智能体预设名（ASCII slug；空=未指定；与已绑定值不一致时服务端忽略）
```

`src/api/chat.py`：把 `body.agent` 透传到 `svc.agent_service.stream_chat(kb_id, session_id, query, deep_thinking, agent=body.agent)`（找到既有调用点，按位置/关键字补齐）。

- [ ] **Step 5: 加 `agent_used` 流事件**

`src/utils/sse.py`：

```python
@dataclass
class SSEAgentUsedEvent:
    """会话绑定智能体回传（语义=本会话绑定值，不含 fork 执行者）。"""

    agent: str = ""  # 本会话绑定的智能体名（空=未绑定）
    type: str = "agent_used"
    seq: int | None = None

    def payload_for_buffer(self) -> dict:
        """缓冲/落盘用载荷。"""
        return {"type": self.type, "agent": self.agent}
```
- 加进 `SSEEvent` union；在 `to_sse` 的 `match/case` 里加一条（`event: agent_used` + `data: {"agent": ...}`，照 `model_info` 写法）；在 `from_payload` 里加反解分支（回放需要）。

`src/services/agent_service.py` 的 `_run_generation`：在进入 `graph.astream_events` **之前**发一次（前端可立即纠正顶栏）：

```python
        manager.add_event(SSEAgentUsedEvent(agent=ctx.agent))
```

- [ ] **Step 6: 登记 `AGENT_BIND_IGNORED` 事件**

`src/core/log_events.py` 的 `Event` 加 `AGENT_BIND_IGNORED = "agent_bind_ignored"`；`src/core/log_event_specs.py` 的 `EVENT_SPECS` 加同名 `EventSpec`（`prefix="agent"`、`level="warning"`、`fields=("session_id", "bound", "requested")`）。**两处同名同集**，否则 import 期 `AssertionError`。

- [ ] **Step 7: 跑测试确认通过**

Run: `pytest tests/services/test_session_agent_binding.py tests/api/test_chat.py tests/chat/ -v && pytest tests/ -q`
Expected: PASS

- [ ] **Step 8: 提交**

```bash
git add src/api/model/request.py src/api/chat.py src/services/agent_service.py src/utils/sse.py src/core/log_events.py src/core/log_event_specs.py src/chat/manager.py src/chat/persistence.py src/infra/db/mysql_db/chat_repo.py tests/services/test_session_agent_binding.py tests/api/test_chat.py tests/chat/
git commit -m "feat(session): 请求 agent 字段 + bind-once 解析 + agent_used 流事件回传"
```

---

### Task 4: system prompt 三层组装（人设层 + 环境约束层）

**Files:**
- Modify: `src/infra/llm/prompt_manager.py:167-183`（新增 `get_base_system_prompt()`；`get_system_prompt()` 语义不变）
- Modify: `src/rag/prompt.py`（新增 `build_system_prompt`；`build_prompt` 改走它）
- Test: `tests/infra/llm/test_prompt_manager_fallback.py`（追加）、`tests/rag/test_prompt_layers.py`（新建）

**Interfaces:**
- Produces：
  - `PromptManager.get_base_system_prompt() -> str` —— **"基础段"**：`_get(PROMPT_NAMES["system"], _FALLBACK_SYSTEM_PROMPT)`，**不做**引用指令 / 委派引导 / 日期追加
  - `build_system_prompt(persona: str, kb_bound: bool, has_skills: bool) -> list[SystemMessage]` —— 返回 system 消息列表（未绑定 KB 时**两条**，与现状同构）
  - `build_prompt(query, context, history, prompt_manager, kb_bound=True, persona="", has_skills=False)`（新增两个带默认值的形参，**旧调用点零改动**）

**不可动摇的不变量（本任务的核心验收）**：`persona=""` 时，`build_prompt` 产出的 `messages` 与改动前**逐字相同**（含消息条数）。为此：
- 环境约束层的追加顺序必须与现状一致：基础段 → `INLINE_CITATION_INSTRUCTION`（带 `not in` 幂等守卫）→ `DELEGATE_GUIDANCE_SECTION`（带守卫）→ `_with_current_date()`。
- 未绑定 KB 时**仍是一条独立的 `SystemMessage(KB_UNBOUND_SYSTEM_PROMPT)`**（不要合进第一条的字符串）。

- [ ] **Step 1: 写失败测试（含"逐字不变"快照守卫）**

`tests/rag/test_prompt_layers.py`：

```python
"""system prompt 三层组装：人设层 + 环境约束层；未选 agent 时逐字不变。"""

from unittest.mock import MagicMock

from langchain_core.messages import SystemMessage

from src.rag.prompt import build_prompt, build_system_prompt


def _pm(base: str = "基础段正文") -> MagicMock:
    """最小 PromptManager 替身：只实现本任务用到的两个取值方法。"""
    pm = MagicMock()
    pm.get_base_system_prompt.return_value = base
    pm.get_system_prompt.return_value = base + "环境约束"
    pm.get_user_template.return_value = "用户模板"
    return pm


def test_no_persona_keeps_system_messages_byte_identical():
    """persona='' 时第一条 system 消息与 get_system_prompt() 完全一致（逐字）。"""
    pm = _pm()
    messages = build_system_prompt(persona="", kb_bound=True, has_skills=False, prompt_manager=pm)
    assert len(messages) == 1
    assert messages[0].content == pm.get_system_prompt()


def test_no_persona_unbound_adds_second_system_message():
    """未绑定 KB 仍是独立的第二条 SystemMessage（结构不变）。"""
    pm = _pm()
    messages = build_system_prompt(persona="", kb_bound=False, has_skills=False, prompt_manager=pm)
    assert len(messages) == 2
    assert isinstance(messages[1], SystemMessage)
    assert messages[1].content != ""


def test_persona_replaces_base_segment():
    """persona 非空 → 人设层用 persona，环境约束层照旧追加（顺序不变）。"""
    pm = _pm(base="基础段正文")
    messages = build_system_prompt(
        persona="你是财务专家，只做财务分析。", kb_bound=True, has_skills=True, prompt_manager=pm
    )
    content = messages[0].content
    assert content.startswith("你是财务专家，只做财务分析。")
    assert "基础段正文" not in content
    assert content.endswith(pm.get_base_system_prompt.return_value + "环境约束") is False


def test_build_prompt_passes_persona_through():
    """build_prompt 的三参默认值让旧调用点零改动，且能把 persona 传下去。"""
    pm = _pm()
    messages = build_prompt("问题", "", [], pm, kb_bound=True, persona="你是财务专家。")
    assert isinstance(messages[0], SystemMessage)
    assert messages[0].content.startswith("你是财务专家。")
```

> 第三个用例的最后一条断言按实现落地后的实际拼接结果调整（人设 + 环境约束 + 日期），**但必须断言"人设在最前、基础段不出现"**——这是本任务的可判别点。

`tests/infra/llm/test_prompt_manager_fallback.py` 追加：

```python
def test_get_base_system_prompt_excludes_env_appends():
    """基础段不含日期追加（get_system_prompt 才追加）。"""
    from src.infra.llm.prompt_manager import PromptManager

    pm = PromptManager()
    base = pm.get_base_system_prompt()
    assert base
    assert "今天是" not in base
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/rag/test_prompt_layers.py tests/infra/llm/test_prompt_manager_fallback.py -v`
Expected: FAIL —— `ImportError: cannot import name 'build_system_prompt'`

- [ ] **Step 3: 实现**

`src/infra/llm/prompt_manager.py`：

```python
    def get_base_system_prompt(self) -> str:
        """取"基础段"system prompt（不含环境约束追加段与日期）。

        与 get_system_prompt() 的分工：本方法只负责"人设层的默认来源"，
        不做引用指令 / 委派引导 / 日期追加——那些属环境约束层，由
        `src/rag/prompt.build_system_prompt` 统一追加（保证追加顺序唯一）。
        """
        return self._get(self.PROMPT_NAMES["system"], _FALLBACK_SYSTEM_PROMPT)

    def get_system_prompt(self) -> str:
        """完整 system prompt（基础段 + 环境约束层 + 日期）。语义与既有调用方保持不变。"""
        prompt = self.get_base_system_prompt()
        if INLINE_CITATION_INSTRUCTION not in prompt:
            prompt += INLINE_CITATION_INSTRUCTION
        if DELEGATE_GUIDANCE_SECTION not in prompt:
            prompt += DELEGATE_GUIDANCE_SECTION
        return _with_current_date(prompt)
```

`src/rag/prompt.py`：

```python
def build_system_prompt(
    persona: str,
    kb_bound: bool,
    has_skills: bool,
    prompt_manager,
) -> list[SystemMessage]:
    """组装 system 消息（人设层 + 环境约束层）。

    Args:
        persona: 人设层正文（会话智能体预设的 system_prompt / fork 执行者人设）；
            空串=未选 agent → 用 prompt_manager.get_base_system_prompt() 作人设层
        kb_bound: 是否绑定知识库（False 时追加 KB_UNBOUND_SYSTEM_PROMPT 独立消息）
        has_skills: 本次会话是否有可用技能；**仅当 persona 非空**时用于决定是否追加
            委派引导段（persona 为空时恒追加，保证未选 agent 的 system 段逐字不变）
        prompt_manager: PromptManager 实例

    Returns:
        system 消息列表（未绑定 KB 时为两条：主 system + KB_UNBOUND）
    """
    if persona:
        base = persona
    else:
        base = prompt_manager.get_base_system_prompt()
    if INLINE_CITATION_INSTRUCTION not in base:
        base += INLINE_CITATION_INSTRUCTION
    if has_skills or not persona:
        if DELEGATE_GUIDANCE_SECTION not in base:
            base += DELEGATE_GUIDANCE_SECTION
    messages = [SystemMessage(content=_with_current_date(base))]
    if not kb_bound:
        messages.append(SystemMessage(content=KB_UNBOUND_SYSTEM_PROMPT))
    return messages
```

> `_with_current_date` 从 `src/infra/llm/prompt_manager.py` import（当前是模块私有函数，**改为可从 prompt.py 复用**：优先直接 import 该私有函数；若嫌跨模块引用私有名，则在 `prompt.py` 内复制其 12 行实现并加注释说明"与 PromptManager 的日期追加同源"——**择一并在报告里写明**）。

`build_prompt` / `build_simple_prompt` 改为：

```python
def build_prompt(query, context, history, prompt_manager, kb_bound=True, persona="", has_skills=False) -> list:
    """...（docstring 保留并补 persona/has_skills 说明）..."""
    messages = build_system_prompt(persona, kb_bound, has_skills, prompt_manager)
    for msg in history:
        if msg.role == "user":
            messages.append(HumanMessage(content=msg.content))
        else:
            messages.append(AIMessage(content=msg.content))
    messages.append(HumanMessage(content=prompt_manager.get_user_template(context=context, query=query)))
    return messages
```

- [ ] **Step 4: 跑测试确认通过（含既有 prompt/agent_node 回归）**

Run: `pytest tests/rag/ tests/infra/llm/ tests/agents/graph/ -v && pytest tests/ -q`
Expected: PASS —— **既有 system 段相关测试一条不改地通过**（这是"逐字不变"的实证）

- [ ] **Step 5: 提交**

```bash
git add src/infra/llm/prompt_manager.py src/rag/prompt.py tests/rag/test_prompt_layers.py tests/infra/llm/test_prompt_manager_fallback.py
git commit -m "feat(prompt): system prompt 三层组装（人设层 + 环境约束层），未选 agent 逐字不变"
```

---

### Task 5: `/xxx` 生成入口接线（inline 单轮 / fork 直出 / 未知前缀 / 读时清洗）

**Files:**
- Modify: `src/services/agent_service.py`（`stream_chat` 内解析前缀并分派；`self._skill_registry` 提升）
- Modify: `src/infra/llm/request_context.py`（新增 `known_skill_names: set[str]`）
- Modify: `src/agents/graph/state.py`（`make_initial_state` 加 `direct_skill=""` 与 `injected_system=""`）
- Modify: `src/agents/graph/agent_node.py`（`_initial_messages` 注入 `injected_system` + 历史消息读时清洗）
- Modify: `src/agents/graph/skill_direct.py`（未知前缀 → 返回"不存在 + 可用列表"文案）
- Modify: `src/infra/search/query_router.py:184`（`_format_history` 读时清洗）
- Test: `tests/services/test_skill_prefix_dispatch.py`（新建）、`tests/agents/graph/test_injected_system.py`（新建）

**Interfaces:**
- Consumes：T2 的 `parse_prefix` / `clean_prefix`；T3 的 `ctx.agent`；Plan 2 的 `direct_skill` 与 `skill_direct` 节点
- Produces：
  - `RequestContext.known_skill_names: set[str]`（读时清洗的名单来源）
  - `make_initial_state(cls, session_id, kb_id, query, history, deep_thinking=False, direct_skill="", injected_system="")`
  - `AgentState.injected_system: str = ""`（隐藏指令：inline 技能正文 / T8 预加载内容）

**分派规则（三态 → 四路）**
| 解析结果 | 处理 |
|---|---|
| `plain` | 现状不变（走 `agent`，主 agent 多轮） |
| `known` + `record.context == INLINE` | 渲染 `record.inline_prompt`（`$ARGUMENTS` = `parsed.task`）→ 写入 `injected_system` 注入；**不设** `direct_skill` → 主 agent 单轮 |
| `known` + `record.context == FORK` | 设 `direct_skill = name`，`query = parsed.task` → 图入口分派到 `skill_direct`（主 agent 零 LLM 轮） |
| `unknown` | **也设 `direct_skill = name`** —— 复用 `skill_direct` 的 fail-open 通道输出"不存在 + 可用列表"（**不新增短路机制**，见 P3-R7） |

- [ ] **Step 1: 写失败测试**

`tests/services/test_skill_prefix_dispatch.py`：用 `AgentService.__new__` 构造（照 `tests/services/test_agent_service.py:116` 的既有范式），注入 fake skill registry / preset registry / chat_manager，断言：
- `plain` → `launch_context["direct_skill"] == ""` 且 `injected_system == ""`；
- inline 技能 → `injected_system` 含渲染后的方法论、`direct_skill == ""`、`query` 为去前缀后的任务文本；
- fork 技能 → `direct_skill == "finance-analyst"`、`query == parsed.task`；
- 未知技能 → `direct_skill == "ghost"`；
- 三种情况下 `add_message_async` 收到的仍是**原文**（落库保留原文，D25）。

`tests/agents/graph/test_injected_system.py`：`agent_finalize`/`_initial_messages` 组装时，`state.injected_system` 非空 → 在 system 段之后追加**一条 `SystemMessage`**，且历史里的 `user` 消息前缀被 `clean_prefix` 剥掉（用 registered name 的 `/name xxx` 历史消息断言）。

- [ ] **Step 2: 跑测试确认失败** → Run: `pytest tests/services/test_skill_prefix_dispatch.py tests/agents/graph/test_injected_system.py -v`

- [ ] **Step 3: 实现**

- `RequestContext` 加字段（行内注释写来源/范围/用途）：
```python
    known_skill_names: set[str] = field(
        default_factory=set
    )  # 已注册技能名集合（来源：stream_chat 解析前缀前一次性写入；范围：请求内只读；用途：历史消息读时清洗 /xxx 前缀）
```
- `AgentState` 加 `injected_system: str = ""`；`make_initial_state` 加 `direct_skill=""`、`injected_system=""` 两个带默认值的形参并写进返回的 state。
- `agent_service.stream_chat`：在建 `ctx` 之后、`launch_context` 之前插入分派块（伪码给出分支，实际写法用完整 `if/elif/else`，**禁三元**）：

```python
        known = set(self._skill_registry.names())
        ctx.known_skill_names = known
        parsed = parse_prefix(query, known, self._skill_registry)
        direct_skill = ""
        injected_system = ""
        effective_query = query
        if parsed.kind == "known":
            if parsed.record is not None and parsed.record.context == SkillContext.INLINE:
                injected_system = render_skill_body(parsed.record.inline_prompt or "", parsed.task)
                effective_query = parsed.task
            else:
                direct_skill = parsed.skill_name
                effective_query = parsed.task
        elif parsed.kind == "unknown":
            direct_skill = parsed.skill_name
            effective_query = parsed.task
        launch_context["direct_skill"] = direct_skill
        launch_context["injected_system"] = injected_system
        launch_context["query"] = effective_query
```
（`launch_context` 中原 `query` 键的值改为 `effective_query`——**注意**：`add_message_async`/`save_user_async` 仍用**原文 `query`**，两者的先后顺序不要调换。）
- `_run_generation`：把 `launch_context` 的 `direct_skill` / `injected_system` 传进 `make_initial_state`。
- `agent_node._initial_messages`：先按 `build_prompt(...)` 建 system + 历史，再在 system 段之后插入 `SystemMessage(content=state.injected_system)`（非空时），历史 `user` 内容改为 `clean_prefix(msg.content, ctx.known_skill_names)`。
- `query_router._format_history`：同法清洗（读 `current_request_ctx`）。
- `skill_direct` 节点：`record is None` 分支改为返回 `SSEInteractionTexts.UNKNOWN_SKILL_PREFIX.format(skill=..., available=...)`（可用列表取 `skill_registry.names()` 拼串）。

- [ ] **Step 4: 跑测试确认通过** → Run: `pytest tests/services/ tests/agents/graph/ -v && pytest tests/ -q`
- [ ] **Step 5: 提交**

```bash
git add src/services/agent_service.py src/infra/llm/request_context.py src/agents/graph/state.py src/agents/graph/agent_node.py src/agents/graph/skill_direct.py src/infra/search/query_router.py tests/services/test_skill_prefix_dispatch.py tests/agents/graph/test_injected_system.py
git commit -m "feat(skills): /xxx 生成入口分派（inline 单轮 / fork 直出 / 未知告警）与读时清洗"
```

---

### Task 6: 直出轮回答交付与落库 + 重跑消费指引（Plan 2 遗留 5.12 / 5.13）

**Files:**
- Modify: `src/services/agent_service.py`（`_convert_event` 增 `skill_direct` 分支；`_run_generation` 的 `full_answer` 与 `capture` 覆盖该来源）
- Modify: `src/chat/process_log.py`（无 `"token"` 段时回落取直出 `answer` —— 兜底）
- Modify: `src/agents/graph/skill_direct.py`（重跑时消费 `state.messages` 里 verify 注入的指引）
- Test: `tests/services/test_direct_round_delivery.py`（新建，**必须走 `astream_events` 生产链**）

**Interfaces:**
- Consumes：T5 的 `direct_skill`；Plan 2 的 `skill_direct` 节点与 `LangGraphNode.SkillDirect.NAME`
- Produces：直出轮回答经 `SSETokenEvent` 交付、`capture.final_answer` 被填充、`serialize_process` 的 `purified_answer` 非空

- [ ] **Step 1: 写失败测试（走生产交付链，不走 `stream_mode="updates"`）**

`tests/services/test_direct_round_delivery.py`：构造一个 fake graph，其 `astream_events` 依次 yield：
① `on_chain_start`（`name="skill_direct"`）；② `on_chain_end`（`name="skill_direct"`，`data.output={"answer": "直出结论[1]", "tool_contexts": [<假 context>]}`）；③ `on_chain_end`（`name="format"`，带 `citations`）。
断言：
- 订阅流里出现 `token` 事件且文本为 `"直出结论[1]"`（**这是本任务的核心判别点**）；
- `partial_holder["text"]` 与 `full_answer` 一致；
- `capture.final_answer == "直出结论[1]"`（供落库）；
- `serialize_process(capture.events_log)` 的 `purified_answer == "直出结论[1]"`。

- [ ] **Step 2: 跑测试确认失败** → Run: `pytest tests/services/test_direct_round_delivery.py -v`
Expected: FAIL —— 无 token 事件、`purified_answer == ""`

- [ ] **Step 3: 实现（按 P3-R5：主方案 + 兜底都做）**

`src/services/agent_service.py` 的 `_convert_event`，在既有 `on_chain_end` 分支（`agent_service.py:331-334` 附近）增加：

```python
        if name == LangGraphNode.SkillDirect.NAME:
            direct_answer = output.get("answer", "")
            capture.final_answer = direct_answer
            capture.final_contexts = output.get("tool_contexts", [])
            produced.append(SSETokenEvent(direct_answer))
```

`src/chat/process_log.py` 的 `build_process_events`：若无任何 `"token"` 事件且存在 `"preamble"`，用最后一段 preamble 文本作为 `purified_answer`（**兜底**，防某条路径没产出 token 时再次落空）。

`src/agents/graph/skill_direct.py`（5.13）：调用 executor 之前收集 verify 注入的指引并在重跑时传给子代理：

```python
        retry_guidance = ""
        for message in state.messages:
            content = message.content if isinstance(message.content, str) else ""
            if VERIFY_CITATION_MARKER in content or VERIFY_KB_CITATION_MARKER in content:
                retry_guidance = content
        task_text = state.query
        if retry_guidance:
            task_text = f"{state.query}\n\n{retry_guidance}"
```
（`VERIFY_CITATION_MARKER` / `VERIFY_KB_CITATION_MARKER` 来自 `src/config/const.py`。）

- [ ] **Step 4: 跑测试确认通过** → Run: `pytest tests/services/test_direct_round_delivery.py tests/chat/ tests/agents/graph/ -v && pytest tests/ -q`
- [ ] **Step 5: 提交**

```bash
git add src/services/agent_service.py src/chat/process_log.py src/agents/graph/skill_direct.py tests/services/test_direct_round_delivery.py
git commit -m "fix(graph): 直出轮回答经 SSE 交付并落库，重跑消费 verify 指引（补 Plan 2 C1）"
```

---

### Task 7: 确认门（仅直出轮）与重跑预算互斥（§4.6）

**Files:**
- Create: `src/agents/graph/verify/confirm_gate.py`
- Modify: `src/agents/skills/executor.py`（fork 执行契约里加"需确认时的 marker 输出要求"）
- Modify: `src/config/prompts.py`（`FORK_EXECUTION_CONTRACT` 常量）
- Modify: `src/config/const.py`（`FORK_CONFIRM_MARKER` + 未经确认标注文案）
- Modify: `src/agents/graph/skill_direct.py`（直出轮先过确认门）
- Test: `tests/agents/graph/test_confirm_gate.py`（新建）

**Interfaces:**
- Produces：`async def confirm_gate(state, run_ctx) -> dict | None`
  - 无 marker → `None`（直通 verify）
  - 有 marker 且用户答复 → 带答复重跑一次 → `{"answer": ..., "_needs_regenerate": False}`
  - 有 marker 但被拒 / 超时 / 澄清槽被占 → `{"answer": answer + "未经确认"标注, "_needs_regenerate": False}`（**不再进 verify 重跑**）

**机制要点（design D18）**：子代理**不持有 `ask_user`**（Plan 2 已由 `FORK_FORBIDDEN_TOOLS` 硬保证），因此"需要确认"必须通过**正文 marker** 表达：`FORK_EXECUTION_CONTRACT` 告知子代理"需要用户确认时，输出一行 `CONFIRM_REQUIRED: <你的问题>`"。编排层按规则检测 marker（0 LLM 调用），复用既有澄清链路（`ctx.clarify_channel` + `pending_asks` 单槽，照 `ask_confirm.py` 的写法）。

- [ ] **Step 1: 写失败测试**（四分支：请求确认→答复→重跑；无信号直通；拒绝/超时→出结论+标注；预算不与 verify 叠加）
- [ ] **Step 2: 跑测试确认失败**
- [ ] **Step 3: 实现**（`confirm_gate` + 常量 + 契约文案 + 直出节点接线；`MAX_VERIFY_REGENERATIONS` **不**被确认门消耗，二者互斥而非叠加）
- [ ] **Step 4: 跑测试确认通过** → Run: `pytest tests/agents/graph/ -v && pytest tests/ -q`
- [ ] **Step 5: 提交**

```bash
git add src/agents/graph/verify/confirm_gate.py src/agents/skills/executor.py src/config/prompts.py src/config/const.py src/agents/graph/skill_direct.py tests/agents/graph/test_confirm_gate.py
git commit -m "feat(graph): 直出轮确认门（规则检测 + 复用澄清链路），与 verify 重跑互斥"
```

---

### Task 8: 预设预绑定 skill 预加载（§4.8）

**Files:**
- Modify: `src/services/agent_service.py`（首轮检测：会话已绑定预设且声明 `skills:` → 渲染并注入 `injected_system`）
- Modify: `src/services/persistence.py` 或复用 T1 的 `get_session_agent`（读会话已绑定 agent）
- Test: `tests/services/test_preset_skill_preload.py`（新建）

**规则（§4.8 + design D12）**：会话**已绑定**预设且该预设声明 `skills:` 时，在该会话**首轮生成前**按 `/xxx` **同一路径**（即 `render_skill_body` + `injected_system`）注入一次；**后续轮次不重复注入**（判定：本轮 `history` 为空即首轮）；注入的是隐藏消息，**不进 system prompt 的人设层**。

- [ ] **Step 1: 写失败测试**（首轮注入 / 第二轮不注入 / 预设未声明 skills 不注入 / 预设已被删除 → 只 warn 不注入）
- [ ] **Step 2: 跑测试确认失败**
- [ ] **Step 3: 实现**（在 `stream_chat` 的 T5 分派块之后：若 `direct_skill == "" and injected_system == ""` 且 `history` 为空 → 按预设的 `skills` 逐个渲染并按 "\n\n" 拼接写入 `injected_system`；多技能顺序 = frontmatter `skills` 声明顺序）
- [ ] **Step 4: 跑测试确认通过** → Run: `pytest tests/services/ -v && pytest tests/ -q`
- [ ] **Step 5: 提交**

```bash
git add src/services/agent_service.py tests/services/test_preset_skill_preload.py
git commit -m "feat(session): 预设预绑定 skill 首轮预加载（隐藏注入，一次生效）"
```

---

### Task 9: 能力清单服务 + 两个只读接口（§5.10）

**Files:**
- Create: `src/services/capability_service.py`
- Create: `src/api/capabilities.py`
- Modify: `src/main.py`（注册路由，照既有 router 注册写法）
- Test: `tests/services/test_capability_service.py`、`tests/api/test_capabilities.py`（新建）

**Interfaces:**
- Produces：
  - `CapabilityService(skill_registry, preset_registry)`；`list_skills() -> list[dict]`（`{"name","description"}`，取 `skill_registry.user_visible()`）；`list_agents() -> list[dict]`（`{"name","display_name","description"}`，取 `preset_registry.all()`）
  - `GET /api/skills` → `ResponseModel(data={"skills": [...]})`；`GET /api/agents` → `ResponseModel(data={"agents": [...]})`

**硬性要求（design D19）**：**不引入 catalog 文件**；api 层只转发（不直接读文件/扫目录）；skills 服务端过滤 `user-invocable: false`（由 `user_visible()` 保证）；读取失败 **fail-open**（返回空列表 + warn，**不 500**）。

- [ ] **Step 1: 写失败测试**（信封为 `data.skills` / `data.agents`；`user-invocable: false` 的技能不出现；注册表抛异常时返回空列表 + 200）
- [ ] **Step 2: 跑测试确认失败**
- [ ] **Step 3: 实现**（service 只做"取 + 转 dict"；api handler 标注返回类型 `ResponseModel`；异常 `try/except` 记 warn 返回空列表——**不用三元、不用 getattr**）
- [ ] **Step 4: 跑测试确认通过** → Run: `pytest tests/services/test_capability_service.py tests/api/test_capabilities.py -v && pytest tests/ -q`
- [ ] **Step 5: 提交**

```bash
git add src/services/capability_service.py src/api/capabilities.py src/main.py tests/services/test_capability_service.py tests/api/test_capabilities.py
git commit -m "feat(api): 能力清单服务与 /api/skills、/api/agents 只读接口（registry 派生，fail-open）"
```

---

### Task 10: 收口（测试补齐 / 契约文档 / 门禁）

**Files:**
- Modify: `docs/agents/api_contract.md`（§7.1）、`docs/agents/glossary.md`（§7.2）、`docs/agents/code-map.md`（§7.3）、`CLAUDE.md`（§7.4）、`docs/agents/reference-projects.md`（§7.7）
- Modify: `docs/agents/data-flow.md`（§7.6：核对"答案校验与引用格式化链路"与最终实现一致）
- Modify: `docs/openspec/changes/agent-delegation-skills/*`（§7.5：标注 D7 被本 change 修订）
- Modify: `docs/openspec/changes/session-agent-and-skill-invocation/tasks.md`（勾选/标注本轮完成的条目）
- Test: 全量门禁

- [ ] **Step 1: 契约文档（§7.1）**：`agent` 字段语义（值=预设 `name`、空=沿用、不一致忽略+warn、**无 400**）、`/xxx` 前缀语义与踩坑（**落库保留原文、组装 prompt 时剥离**）、`sessions/list` 新增 `agent`（`sessions/messages` 契约不变）、`agent_used` 事件、两个清单接口的信封。
- [ ] **Step 2: 术语与结构（§7.2/7.3/7.4）**：glossary 加「智能体预设」「会话级 vs 消息级」；code-map 登记 `agents/` 内容目录与 `src/agents/presets/`、`src/agents/skills/prefix.py`、`src/services/capability_service.py`、`src/api/capabilities.py`；`CLAUDE.md` 目录速览补 `agents/`。
- [ ] **Step 3: 调研与依赖说明（§7.5/7.7）**：`agent-delegation-skills` 的 D7 标注被修订；`reference-projects.md` 更新 agency-agents 条目 + 记录"`create_react_agent` 废弃 → `create_agent`"结论。
- [ ] **Step 4: 补齐 §5.11 剩余测试**（前缀清洗的当前轮与历史、注入后持续生效、双轴过滤、绑定四态、老会话 `bind-if-empty` 可绑定、`agent_used` 事件、未选 agent 时 system 段快照逐字一致、两接口信封、非法名称跳过、`sessions/messages` 契约不变）——逐条对照，缺哪条补哪条。
- [ ] **Step 5: 跑全量门禁**

```
timeout 1200 python -m pytest tests/ -q
ruff check .
pyright src/
python -m src.cli.check_docs
```

- [ ] **Step 6: 提交**

```bash
git add docs/ CLAUDE.md
git commit -m "docs(session-agent): 收口契约/术语/结构/调研与测试补齐"
```

---

## Self-Review

**1. Spec coverage（对照 change 的 §5 / §7 / §4.6 / §4.8）**

| change 任务 | 覆盖 | 满配度 |
|---|---|---|
| 5.4 前缀解析 + 执行形态分派 + 读时清洗 | T2（解析/清洗）+ T5（分派/接线） | T2 满配；**T5 步骤含完整分派代码，但 `_run_generation` 与 `agent_node` 的具体插入行需执行时按锚点落** |
| 5.5 未命中判定 + 文案 | T2（三态）+ T5（fail-open 通道）+ T2 Step 4（文案） | 满配 |
| 5.6 请求 `agent` 字段 | T3 | 满配 |
| 5.7 存储链（4 小项） | T1 | 满配 |
| 5.8 bind-once + `agent_used` | T3 | 满配（`get_session_agent_async` 两种实现择一，已在步骤内说明） |
| 5.9 三层 prompt 组装 | T4 | 满配（含逐字不变守卫；`_with_current_date` 复用方式二选一，已说明） |
| 5.10 能力清单服务 + 接口 | T9 | 步骤级（代码形状已定，无逐行代码） |
| 5.11 测试 | 各任务内嵌 + T10 Step 4 | 步骤级 |
| 5.12 直出轮交付/落库 | T6 | 满配（含生产链端到端测试要求） |
| 5.13 直出轮重跑消费指引 | T6 Step 3 | 满配 |
| 5.14 生产 `agents/`/`skills/` 挂载与 COPY | **未覆盖** | ⚠️ 见下 |
| 4.6 确认门 | T7 | 步骤级（四分支已列，机制与常量已定，未逐行贴码） |
| 4.8 预设预绑定预加载 | T8 | 步骤级 |
| 7.1–7.7 文档同步 | T10 | 步骤级 |

**2. Placeholder scan**：T7/T8/T9 + T10 的 Step 3 以"步骤 + 验收标准 + 关键签名/常量"给出，**未逐行贴码**——这是本计划的**已知缺口**（与 Plan 2 相同处理方式）：执行到这些任务前必须按 Plan 1/Plan 2 的粒度补齐代码块与测试代码，补齐后再派发。T1–T6 已满配（含可直接粘贴的测试与实现）。

**3. Type consistency**：`PrefixParse(kind, skill_name, task, record)`（T2 定 → T5 消费）；`parse_prefix(text, known_names, registry)` / `clean_prefix(text, known_names)`（T2 定 → T5 消费）；`bind_session_agent(session_id, agent) -> bool`（T1 定 → T3 消费）；`_resolve_session_agent(session_id, requested, bound="")`（T3 定）；`get_base_system_prompt()` / `build_system_prompt(persona, kb_bound, has_skills, prompt_manager) -> list[SystemMessage]`（T4 定 → T5/T8 消费）；`RequestContext.known_skill_names: set[str]`（T5 定 → `agent_node`/`query_router` 消费）；`AgentState.injected_system: str`（T5 定 → T8 复用）；`SSEAgentUsedEvent(agent, type, seq)`（T3 定）。命名已核对一致。

**4. 阻塞与前置 / 未覆盖项**
- **5.14（生产部署缺口）未纳入本计划**：`Dockerfile` 只 `COPY src/ scripts/ deploy/`、`docker-compose.prod.yml` 未挂 `skills/`/`agents/`。它是 Plan 1 起的既有问题，且改生产部署属"共享基础设施"决策——**建议单列一个部署小任务或在 Plan 4 一并处理**，本计划不擅自改生产 compose。
- T5 依赖 T2/T3/T4 全部就绪（顺序：T1 → T2 → T3 → T4 → T5 → T6 → T7/T8 → T9 → T10）。
- T7（确认门）与 T8（预加载）彼此独立，可换序。
