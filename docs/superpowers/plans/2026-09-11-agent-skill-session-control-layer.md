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
- **P3-R5**：T6 的直出交付**只做主方案**——在 `_convert_event` 的 `on_chain_end` 分支捕获 `skill_direct` 的 `answer` 并产出 `SSETokenEvent`（**不做**"`serialize_process` 回落"的兜底）。理由：实测 `build_process_events` 对排除类型（`model_info`/`citation`/`done`/`error`）是 `continue` 而**不冲刷待定区**，因此只要产出 token 事件，末尾待定区就是直出正文、`purified_answer` 自然非空；再写回落是同一问题的第二份实现（YAGNI）。**判别方式**：T6 的端到端测试断言 `purified_answer` 非空——若该测试仍不过，才补回落。**若判断有误**：补一次 `process_log` 回落即可（约 6 行）。
- **P3-R6**：`build_system_prompt` 的 `has_skills` **只在 `persona` 非空时**才决定是否追加委派引导段；`persona` 为空时**恒追加**（保住"未选 agent 时 system 段逐字不变"这条硬不变量）。**若判断有误**：未选 agent 的 system prompt 会少一段，逐字不变测试会红。
- **P3-R7**：`unknown`（形如命令但未注册）**复用 `skill_direct` 的 fail-open 通道**（也设 `direct_skill = name`，由节点返回"不存在 + 可用列表"），**不新增短路机制**。理由：直出通道已具备"无法解析 → 出兜底文案"的能力，且 T6 会让该路径的回答正常交付；新增短路需要动 API 层的生成生命周期（写缓冲 + 跳过 `_run_generation`），成本远高于收益。**若判断有误**：未注册前缀的回答会经直出通道交付（观感与直答一致），或需改回短接。
- **P3-R8**：`known_skill_names` 放 **`RequestContext`**（而非随参数层层传递）。理由：读时清洗发生在 `agent_node._initial_messages` 与 `query_router._format_history` 两处，二者都能拿到 `current_request_ctx`；走 ctx 只需一处写入、零签名改动。**若判断有误**：需给这两处补参数（`clean_prefix` 已是纯函数，改动面可控）。

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

- `__init__` 里**先把 `preset_registry = None` 提到 `if Path(skills_dir).exists():` 之前**（否则 skills 目录缺失时后文引用未定义名会 `NameError`），skills 分支内赋真实注册表，分支之后统一 `self._preset_registry = preset_registry`。
- `stream_chat` 签名加 `agent: str = ""`；在返回 `(_, launch_context)` **之前**：

```python
        bound_raw = await self._chat_manager.get_session_agent_async(session_id)
        effective_agent = await self._resolve_session_agent(session_id, agent, bound=bound_raw)
        ctx.agent = effective_agent
        launch_context["agent"] = effective_agent
```

> **已钉死（不再二选一）**：`ChatManager.get_session_agent_async(session_id) -> str` = `await self._persistence.get_session_agent(session_id)`；`PersistenceService.get_session_agent` = `session = await self._chat_repo.get_session_by_id(session_id)` → `return session.agent if session is not None else ""`（**不用 `getattr` 兜底**）。**不采用**"复用 `get_sessions` 过滤"（为取一个字段拉 50 条会话列表不划算）。两者都要在 T3 内实现并纳入其测试断言。

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

`src/api/chat.py`：把 `body.agent` 透传到 `svc.agent_service.stream_chat(...)`（调用点在 `chat.py:307-309`，改为 `stream_chat(kb_id, session_id, query, deep_thinking, agent=body.agent)`）。**同一函数里 :449-450 的 `save_session_async(session_id, query[:20], kb_id, user_id)` 与 `save_user_async(session_id, kb_id, query)` 保持用原文，不要动**（落库保留原文，D25）。

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
  - `build_system_prompt(persona: str, kb_bound: bool, has_skills: bool, prompt_manager) -> list[SystemMessage]` —— 返回 system 消息列表（未绑定 KB 时**两条**，与现状同构）
  - `build_prompt(query, context, history, prompt_manager, kb_bound=True, persona="", has_skills=False)`（新增两个带默认值的形参，**旧调用点零改动**）
- **两个新形参的来源（本任务不负责接线，但必须知道）**：由 **T5** 写进 `RequestContext`（`ctx.persona` = 会话绑定预设的 `system_prompt`；`ctx.has_skills` = `bool(skill_registry.model_visible())`），再由 **T5b** 的 `_initial_messages` 读取并传入 `build_prompt`。因此本任务的测试直接给形参传值即可，**不要**在 `prompt.py` 里去查 registry（那会引入 rag→agents 的依赖反向）。

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
    """persona 非空 → 人设在最前、基础段不再出现，环境约束段仍追加在其后。"""
    pm = _pm(base="基础段正文")
    messages = build_system_prompt(
        persona="你是财务专家，只做财务分析。", kb_bound=True, has_skills=True, prompt_manager=pm
    )
    content = messages[0].content
    assert content.startswith("你是财务专家，只做财务分析。")
    assert "基础段正文" not in content
    assert INLINE_CITATION_INSTRUCTION in content


def test_persona_without_skills_omits_delegate_section():
    """has_skills=False 且 persona 非空 → 环境约束层不含委派引导段（P3-R6）。"""
    pm = _pm(base="基础段正文")
    messages = build_system_prompt(
        persona="你是财务专家。", kb_bound=True, has_skills=False, prompt_manager=pm
    )
    assert DELEGATE_GUIDANCE_SECTION not in messages[0].content


def test_no_persona_always_keeps_delegate_section():
    """persona='' 时即使 has_skills=False 也保留委派引导段（逐字不变的前提）。"""
    pm = _pm(base="基础段正文")
    messages = build_system_prompt(
        persona="", kb_bound=True, has_skills=False, prompt_manager=pm
    )
    assert messages[0].content == pm.get_system_prompt()
```
（文件顶部需 `from src.config.prompts import DELEGATE_GUIDANCE_SECTION, INLINE_CITATION_INSTRUCTION`。）


def test_build_prompt_passes_persona_through():
    """build_prompt 的三参默认值让旧调用点零改动，且能把 persona 传下去。"""
    pm = _pm()
    messages = build_prompt("问题", "", [], pm, kb_bound=True, persona="你是财务专家。")
    assert isinstance(messages[0], SystemMessage)
    assert messages[0].content.startswith("你是财务专家。")
```

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

> `_with_current_date` **已钉死**：直接 `from src.infra.llm.prompt_manager import _with_current_date`（单一事实来源——若在 `prompt.py` 复制一份，两处日期格式将来会漂移，而"未选 agent 时 system 段逐字不变"这条不变量正是靠它成立）。

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

### Task 5b: 注入型隐藏消息的持久化与隐藏（T5 / T8 的共同前置，**必须先做**）

> **为什么独立成一步**：规格三处独立要求——`specs/skill-invocation/spec.md:34`「`/xxx` 触发 SHALL 把 skill 内容作为**一条隐藏消息**注入会话上下文；**后续轮次 SHALL 仍然看到**」、`:41`「前端消息流**不展示**该注入消息，但模型可见」、`specs/agent-preset/spec.md:83` 与 `agent-service/spec.md:5/15`「**不进 system prompt**」，外加 `design.md:154`「**随历史持久化**」。用一个"每请求就丢的 state 字段"无法满足其中任何一条，所以先把注入通道做成持久化消息。

**Files:**
- Modify: `src/config/const.py`（`SKILL_INJECTION_PREFIX` 标记）
- Modify: `src/services/agent_service.py`（新增 `_inject_skill_message`）
- Modify: `src/agents/graph/agent_node.py`（历史映射：把标记行抽成独立 `HumanMessage` 放主 system 段之后）
- Modify: `src/api/sessions.py`（`sessions/messages` **按标记过滤**，前端不展示）
- Test: `tests/services/test_skill_injection_persist.py`、`tests/agents/graph/test_injected_history.py`、`tests/api/test_sessions.py`（追加）

**Interfaces:**
- Produces：
  - `SKILL_INJECTION_PREFIX: str`（内容前缀标记，形如 `"[[skill-injection]]"`）
  - `AgentService._inject_skill_message(session_id: str, kb_id: str, text: str) -> "ChatMessage"` —— 写 Redis + DB，并**返回一条可供本轮使用的 `ChatMessage`**
  - 历史里的标记行 → 独立 `HumanMessage`（**位移到主 system 段之后、普通对话历史之前**）

**机制（A′：marker 标记的持久化 user 消息）**
1. **载体**：一条 `role="user"` 的历史消息，内容 = `SKILL_INJECTION_PREFIX + "\n" + 正文`。不新增 schema、不做第二次迁移。
2. **本轮可见**：`_inject_skill_message` 写回 Redis 后，把新构造的那条 `ChatMessage` **追加到本轮 `launch_context["history"]`**（不必回头再读一次 Redis），保证本轮 prompt 就带上它。
3. **跨轮可见**：因为它已写入 Redis（`chat_history:{sid}`）与 `conversation_history`，**后续轮次的 `get_history_async` 自然读回** → 满足"生效范围=会话级"。
4. **前端隐藏**：`sessions/messages` 过滤掉 `content.startswith(SKILL_INJECTION_PREFIX)` 的行（`data` 仍是数组，契约不变）。
5. **不进 system prompt 的组装**：`build_system_prompt` 完全不知道它；`_initial_messages` 把它抽成**独立**的 `HumanMessage` 放在主 system 段之后、普通历史之前——既不进人设层/环境约束层，也避免"对话中途插 system 消息"的兼容风险（部分模型要求 system 只在首位）。
6. **顺序要求**：注入必须在**读历史之后、写用户原文之前**发生（保持 `stream_chat` 既有的"先取历史（不含当前 query）→ 再写 user"顺序不变）。

- [ ] **Step 1: 写失败测试**

`tests/services/test_skill_injection_persist.py`：

```python
"""注入型隐藏消息：写 Redis + DB、本轮可见、后续轮可从历史读回。"""

from unittest.mock import AsyncMock, MagicMock

from src.config.const import SKILL_INJECTION_PREFIX
from src.services.agent_service import AgentService


def _service() -> tuple[AgentService, AsyncMock]:
    svc = AgentService.__new__(AgentService)
    svc._chat_manager = AsyncMock()
    svc._chat_manager.add_message_async = AsyncMock()
    svc._chat_manager.save_user_async = AsyncMock()
    return svc, svc._chat_manager


async def test_injection_writes_redis_and_db_with_marker():
    """注入同时写 Redis 与 DB，且内容带标记前缀。"""
    svc, chat_manager = _service()
    entry = await svc._inject_skill_message("sess_1", "kb1", "方法论正文")

    assert entry.content.startswith(SKILL_INJECTION_PREFIX)
    assert "方法论正文" in entry.content
    chat_manager.add_message_async.assert_awaited_once()
    saved = chat_manager.save_user_async.await_args[0]
    assert saved[0] == "sess_1"
    assert SKILL_INJECTION_PREFIX in saved[2]


async def test_injection_returns_entry_for_current_turn():
    """返回的 ChatMessage 可直接追加进本轮 history（保证本轮就生效）。"""
    svc, _ = _service()
    entry = await svc._inject_skill_message("sess_1", "kb1", "方法论正文")
    assert entry.role == "user"
```

`tests/agents/graph/test_injected_history.py`：

```python
"""历史里的注入标记行 → 独立 HumanMessage，位于 system 段之后、普通历史之前。"""

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from src.agents.graph.agent_node import _initial_messages
from src.agents.graph.state import AgentState
from src.config.const import SKILL_INJECTION_PREFIX
from src.infra.llm.request_context import RequestContext, current_request_ctx


class _HistoryMsg:
    """最小历史消息替身（只读 role / content）。"""

    def __init__(self, role: str, content: str):
        self.role = role
        self.content = content


def test_injected_history_becomes_separate_human_message(monkeypatch):
    """注入行抽成独立 HumanMessage 且排在普通历史之前；普通历史不受影响。"""
    pm = MagicMock()
    pm.get_base_system_prompt.return_value = "基础段"
    pm.get_system_prompt.return_value = "基础段"
    pm.get_user_template.return_value = "用户模板"

    state = AgentState(
        session_id="s1",
        kb_id="kb1",
        query="腾讯2024",
        _history=[
            _HistoryMsg("user", SKILL_INJECTION_PREFIX + "\n方法论正文"),
            _HistoryMsg("user", "上一轮问题"),
            _HistoryMsg("assistant", "上一轮回答"),
        ],
    )
    ctx = RequestContext(session_id="s1")
    ctx.known_skill_names = set()
    ctx.persona = ""
    ctx.has_skills = False
    token = current_request_ctx.set(ctx)
    try:
        messages = _initial_messages(state, pm)
    finally:
        current_request_ctx.reset(token)

    types = [type(m) for m in messages]
    assert types.index(SystemMessage) == 0
    injected_idx = next(i for i, m in enumerate(messages) if isinstance(m, HumanMessage) and SKILL_INJECTION_PREFIX in m.content)
    prev_user_idx = next(i for i, m in enumerate(messages) if isinstance(m, HumanMessage) and m.content == "上一轮问题")
    assert injected_idx < prev_user_idx          # 注入在普通历史之前
    assert messages[injected_idx].content.startswith(SKILL_INJECTION_PREFIX)
```

`tests/api/test_sessions.py` 追加：mock 的 `get_messages` 返回一条带标记的行 + 一条普通行，断言 `/sessions/messages` 的 `data` **只含普通行**且仍是数组。

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/services/test_skill_injection_persist.py tests/agents/graph/test_injected_history.py -v`
Expected: FAIL —— `ImportError: SKILL_INJECTION_PREFIX` / `AttributeError: _inject_skill_message`

- [ ] **Step 3: 加标记常量**

`src/config/const.py`（`FORK_FORBIDDEN_TOOLS` 附近）：

```python
SKILL_INJECTION_PREFIX: str = "[[skill-injection]]"
"""注入型隐藏消息的内容前缀标记。

用途：① `sessions/messages` 据此过滤（前端不展示）；② `agent_node._initial_messages`
据此把该行抽成独立 HumanMessage（模型可见）。标记必须是 ASCII 且用户不可能自然打出。
"""
```

- [ ] **Step 4: 实现注入通道**

`src/services/agent_service.py`：

```python
    async def _inject_skill_message(
        self, session_id: str, kb_id: str, text: str
    ) -> "ChatMessage":
        """把 skill 正文作为一条隐藏消息写入会话上下文（Redis + DB）。

        Args:
            session_id: 会话 ID
            kb_id: 当前知识库 ID（落库用，可为空）
            text: 已渲染的 skill 正文

        Returns:
            新构造的 ChatMessage；调用方应把它**追加到本轮 history**（否则本轮 prompt 看不到）
        """
        content = f"{SKILL_INJECTION_PREFIX}\n{text}"
        await self._chat_manager.add_message_async(session_id, "user", content)
        await self._chat_manager.save_user_async(session_id, kb_id, content)
        return ChatMessage(role="user", content=content)
```
（顶部 import `SKILL_INJECTION_PREFIX` 与 `ChatMessage`——后者与 `chat/manager.py:get_history_async` 用的是同一个类。）

- [ ] **Step 5: 历史映射改造**

`src/agents/graph/agent_node.py` 的 `_initial_messages`：

```python
    def _initial_messages(state: AgentState, prompt_manager) -> list[BaseMessage]:
        # 历史窗口截断（最近 N 轮 + token 双上限）后再组装初始消息
        history = _truncate_history(state._history or [])
        ctx = current_request_ctx.get()
        if ctx is not None:
            persona = ctx.persona
            has_skills = ctx.has_skills
            known = ctx.known_skill_names
        else:
            persona = ""
            has_skills = False
            known = set()
        injected: list[BaseMessage] = []
        normal: list[ChatMessage] = []
        for msg in history:
            if msg.role == "user" and msg.content.startswith(SKILL_INJECTION_PREFIX):
                injected.append(HumanMessage(content=msg.content))
            else:
                normal.append(msg)
        messages = build_prompt(
            clean_prefix(state.query, known),
            "",
            normal,
            prompt_manager,
            kb_bound=bool(state.kb_id),
            persona=persona,
            has_skills=has_skills,
        )
        # 注入消息放在主 system 段之后、普通对话历史之前（不进人设层/环境约束层）
        if injected:
            insert_at = 0
            for i, m in enumerate(messages):
                if isinstance(m, SystemMessage):
                    insert_at = i + 1
            messages[insert_at:insert_at] = injected
        return messages
```
> 注意：`build_prompt` 内部对 `normal` 的历史消息做同样的 `clean_prefix` 清洗（见 T5 Step 3 的说明），两处都从 `ctx.known_skill_names` 取名单。

- [ ] **Step 6: 前端隐藏（`sessions/messages` 过滤）**

`src/api/sessions.py` 里构造返回列表处，过滤标记行（保持 `data` 为数组、其余字段不变）：

```python
        visible = [
            m for m in result
            if not (m.role == "user" and (m.content or "").startswith(SKILL_INJECTION_PREFIX))
        ]
```
（`result` 即现有 `data` 的列表来源；只加这一层过滤，不改其它映射。）

- [ ] **Step 7: 跑测试确认通过**

Run: `pytest tests/services/test_skill_injection_persist.py tests/agents/graph/test_injected_history.py tests/api/test_sessions.py -v && pytest tests/ -q`
Expected: PASS

- [ ] **Step 8: 提交**

```bash
git add src/config/const.py src/services/agent_service.py src/agents/graph/agent_node.py src/api/sessions.py tests/services/test_skill_injection_persist.py tests/agents/graph/test_injected_history.py tests/api/test_sessions.py
git commit -m "feat(session): 注入型隐藏消息持久化（marker 标记 + 前端隐藏 + 跨轮生效）"
```

---

### Task 5: `/xxx` 生成入口接线（inline 单轮 / fork 直出 / 未知前缀 / 读时清洗）

**Files:**
- Modify: `src/services/agent_service.py`（`stream_chat` 内解析前缀并分派；提升 `self._skill_registry`；写 `ctx.persona` / `ctx.has_skills` / `ctx.known_skill_names`）
- Modify: `src/infra/llm/request_context.py`（新增 `known_skill_names: set[str]` / `persona: str` / `has_skills: bool`）
- Modify: `src/agents/graph/state.py`（`make_initial_state` 加 `direct_skill=""`）
- Modify: `src/agents/graph/agent_node.py`（历史读时清洗 + persona/has_skills 传给 `build_prompt`）
- Modify: `src/agents/graph/skill_direct.py`（查不到 → "不存在 + 可用列表"；**与"非 FORK"分开**成两条文案）
- Modify: `src/infra/search/query_router.py:184`（`_format_history` 读时清洗；**补 import `current_request_ctx`**）
- Test: `tests/services/test_skill_prefix_dispatch.py`、`tests/agents/graph/test_prefix_cleaning.py`（新建）

**Interfaces:**
- Consumes：T2 的 `parse_prefix` / `clean_prefix`；T3 的 `ctx.agent`；T5b 的 `_inject_skill_message`；Plan 2 的 `direct_skill` 与 `skill_direct` 节点
- Produces：
  - `RequestContext.known_skill_names: set[str]`（**只含 `user_visible()` 的名字**——`specs/skill-invocation/spec.md:62` 要求 `user-invocable:false` 禁止 `/xxx` 调用）
  - `RequestContext.persona: str` / `RequestContext.has_skills: bool`（T4 的 `build_system_prompt` 的两个入参来源）
  - `make_initial_state(cls, session_id, kb_id, query, history, deep_thinking=False, direct_skill="")`

**关键修正（本轮复审）**：① `known_names` 必须取 `self._skill_registry.user_visible()` 的名字，**不是** `names()`（后者含 `user-invocable:false`，会让禁用技能被 `/xxx` 调起）；② inline 注入走 **T5b 的持久化隐藏消息**，不再用"每请求的 state 字段"（否则第二轮就失效，违反「持续生效」）；③ `persona` / `has_skills` 经 `RequestContext` 传给 `agent_node`（graph 层不持有 preset registry，否则会话智能体人设永远为空）。

**分派规则（三态 → 四路）**
| 解析结果 | 处理 |
|---|---|
| `plain` | 现状不变（走 `agent`，主 agent 多轮） |
| `known` + 记录**不可用户调用** | 不可能出现：`known_names` 只含 `user_visible()` 的名字（见上"关键修正①"） |
| `known` + `record.context == INLINE` | 渲染 `record.inline_prompt`（`$ARGUMENTS` = `parsed.task`）→ **经 T5b `_inject_skill_message` 持久化注入**，并把返回的 `ChatMessage` 追加进本轮 history；**不设** `direct_skill` → 主 agent 单轮 |
| `known` + `record.context == FORK` | 设 `direct_skill = name`，`query = parsed.task` → 图入口分派到 `skill_direct`（主 agent 零 LLM 轮） |
| `unknown` | **也设 `direct_skill = name`** —— 复用 `skill_direct` 的 fail-open 通道输出"不存在 + 可用列表"（**不新增短路机制**，见 P3-R7） |

- [ ] **Step 1: 写失败测试**

`tests/services/test_skill_prefix_dispatch.py`：用 `AgentService.__new__` 构造（照 `tests/services/test_agent_service.py:116` 的既有范式），注入 fake skill registry（`names()` 与 `user_visible()` 返回**不同**集合，用来判别是否只认 user-invocable）/ preset registry / chat_manager，断言：
- `plain` → `launch_context["direct_skill"] == ""`，且**没有**写注入消息；
- **inline 可用技能** → `direct_skill == ""`、`launch_context["query"] == parsed.task`、**`add_message_async` 被调用了两次且其中一次内容带 `SKILL_INJECTION_PREFIX`**（持久化注入）、`launch_context["history"]` 末尾是该注入条目；
- **`user-invocable: false` 的技能**（只出现在 `names()` 不在 `user_visible()`）→ 按 `unknown` 处理（`direct_skill == 该名`，走 fail-open 文案），**不得**当作 `known` 注入；
- fork 技能 → `direct_skill == "finance-analyst"`、`query == parsed.task`；
- 未知技能 → `direct_skill == "ghost"`；
- 以上所有情况下，写用户消息用的仍是**原文 `query`**（落库保留原文，D25）；
- `ctx.persona` 等于会话预设的 `system_prompt`（未绑定或无 preset 时为空串）、`ctx.has_skills` 等于 `bool(model_visible())`。

`tests/agents/graph/test_prefix_cleaning.py`：历史里的 `/name xxx` 行（`name` 属于 `ctx.known_skill_names`）在组装 prompt 时被剥掉前缀；不在名单里的 `/ghost xxx` 原样保留；`state.query` 同样被剥。

- [ ] **Step 2: 跑测试确认失败** → Run: `pytest tests/services/test_skill_prefix_dispatch.py tests/agents/graph/test_prefix_cleaning.py -v`

- [ ] **Step 3: 实现**

- `RequestContext` 加三个字段（行内注释写来源/范围/用途）：`known_skill_names: set[str]`（**只含 user_visible 的名字**）、`persona: str = ""`、`has_skills: bool = False`。
- `AgentState` 加 `direct_skill: str = ""`（**不加** `injected_system`——注入走 T5b 的持久化消息）；`make_initial_state` 加 `direct_skill=""` 形参并写进返回的 state。
- `_run_generation`：把 `launch_context["direct_skill"]` 传进 `make_initial_state`。
- `agent_node._initial_messages`：见 T5b Step 5 的完整实现（抽注入行 + 读 `ctx.persona`/`ctx.has_skills`/`ctx.known_skill_names` + 对 `state.query` 与普通历史做 `clean_prefix`）。
- `query_router._format_history`（`:184`）：对每条 `content` 做 `clean_prefix(content, known_names)`；`known_names` 从 `current_request_ctx.get()` 取——**该模块当前没有这个 import，需补**。
- `skill_direct` 节点：把"查不到"与"非 FORK"**分开**成两条文案——`record is None` → `SSEInteractionTexts.UNKNOWN_SKILL_PREFIX.format(skill=..., available=...)`（可用列表取 `skill_registry.user_visible()` 的名字拼串）；`record.context != FORK` → 保留既有 `SKILL_DIRECT_UNAVAILABLE`。
- `agent_service.stream_chat`：在建 `ctx` 之后、`launch_context` 之前插入分派块（实际写法用完整 `if/elif/else`，**禁三元**）：

```python
        # 只取"用户可调用"的技能名（user-invocable:false 禁止 /xxx 调用）
        known = set(r.name for r in self._skill_registry.user_visible())
        ctx.known_skill_names = known
        # persona / has_skills：T4 的 build_system_prompt 入参来源（graph 层拿不到 registry）
        session_preset = None
        if self._preset_registry is not None and effective_agent:
            session_preset = self._preset_registry.get(effective_agent)
        if session_preset is not None:
            ctx.persona = session_preset.system_prompt
        else:
            ctx.persona = ""
        ctx.has_skills = bool(self._skill_registry.model_visible())

        parsed = parse_prefix(query, known, self._skill_registry)
        direct_skill = ""
        effective_query = query
        if parsed.kind == "known":
            record = parsed.record
            if record is not None and record.context == SkillContext.INLINE:
                injected_text = render_skill_body(record.inline_prompt or "", parsed.task)
                entry = await self._inject_skill_message(session_id, kb_id, injected_text)
                history = history + [entry]        # 本轮即生效（T5b）
                effective_query = parsed.task
            else:
                direct_skill = parsed.skill_name
                effective_query = parsed.task
        elif parsed.kind == "unknown":
            direct_skill = parsed.skill_name
            effective_query = parsed.task
        launch_context["direct_skill"] = direct_skill
        launch_context["history"] = history
        launch_context["query"] = effective_query
```
（四点注意：① `launch_context` 的 `query` 改为 `effective_query`、`history` 需写回（因为 T5b 可能追加了一条注入消息）；② **已核实** `src/api/chat.py:326-336` 的 `answer_builder` 用的正是 `launch_ctx["query"]` 与 `launch_ctx["history"]`，因此改这两个键就能让图里的 `state.query` / `state._history` 生效，无需再改 API 层；③ `add_message_async(...)`（写用户原文）与 `chat.py:449-450` 的落库仍必须用**原文 `query`**，顺序不要调换；④ `effective_agent` 来自 T3 的 `_resolve_session_agent`。）
- [ ] **Step 4: 跑测试确认通过** → Run: `pytest tests/services/ tests/agents/graph/ -v && pytest tests/ -q`
- [ ] **Step 5: 提交**

```bash
git add src/services/agent_service.py src/infra/llm/request_context.py src/agents/graph/state.py src/agents/graph/agent_node.py src/agents/graph/skill_direct.py src/infra/search/query_router.py tests/services/test_skill_prefix_dispatch.py tests/agents/graph/test_prefix_cleaning.py
git commit -m "feat(skills): /xxx 生成入口分派（inline 单轮 / fork 直出 / 未知告警）与读时清洗"
```

---

### Task 6: 直出轮回答交付与落库 + 重跑消费指引（Plan 2 遗留 5.12 / 5.13）

**Files:**
- Modify: `src/services/agent_service.py`（`_convert_event` 增 `skill_direct` 分支；`_run_generation` 的 `full_answer` 与 `capture` 覆盖该来源）
- Modify: `src/agents/graph/skill_direct.py`（重跑时消费 `state.messages` 里 verify 注入的指引）
- Test: `tests/services/test_direct_round_delivery.py`（新建，**必须走 `astream_events` 生产链**）
- **不改** `src/chat/process_log.py`（见 P3-R5：产出 token 事件后 `purified_answer` 自然非空，回落属第二份实现）

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

`src/services/agent_service.py` 的 `_convert_event`，在既有 `on_chain_end` 分支（`agent_service.py:315-337`）里、`format` 分支之后、`agent_finalize` 分支之后**追加**（注意该函数各分支是 `return [...]` 风格，**不要**改成累加列表）：

```python
        if name == LangGraphNode.SkillDirect.NAME and capture is not None:
            output = item.get(LangGraphKey.DATA, {}).get(LangGraphKey.OUTPUT) or {}
            direct_answer = output.get("answer", "")
            capture.final_answer = direct_answer
            capture.final_contexts = output.get("tool_contexts", [])
            return [SSETokenEvent(direct_answer)]
        return []
```
（`LangGraphEvent` / `LangGraphKey` / `LangGraphNode` 已在 `agent_service.py` 顶部导入；`SSETokenEvent` 同文件已在用。）

`src/chat/process_log.py` **不需要改**：`build_process_events` 对 `model_info`/`citation`/`done`/`error` 是 `continue`（不冲刷待定区），所以上游产出 token 事件后，末尾待定区就是直出正文，`purified_answer` 自然非空（见 P3-R5）。若 Step 4 的端到端测试仍断言失败，再补"无 token 段时回落取末段 preamble 文本"的 6 行。

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
- Modify: `src/config/const.py`（`FORK_CONFIRM_MARKER` + `SSEInteractionTexts.CONFIRM_UNCONFIRMED_NOTE`）
- Modify: `src/config/prompts.py`（`FORK_EXECUTION_CONTRACT`）
- Modify: `src/agents/skills/executor.py:275-289`（`_executor_system_prompt` 追加执行契约）
- Modify: `src/agents/graph/skill_direct.py:42-64`（直出轮在拿到子代理文本后过确认门）
- Modify: `src/core/log_events.py` + `src/core/log_event_specs.py`（登记 `FORK_CONFIRM_ASKED` / `FORK_CONFIRM_UNCONFIRMED`）
- Test: `tests/agents/graph/test_confirm_gate.py`、`tests/agents/skills/test_executor_contract.py`（新建）

**Interfaces:**
- Produces：
  - `detect_confirm_request(answer: str) -> str` —— **纯函数**，命中 `FORK_CONFIRM_MARKER` 时返回其后的提问文本，未命中返回 `""`
  - `ask_confirm_question(question: str, session_id: str) -> str | None` —— 复用澄清链路问用户；拒绝 / 超时 / 澄清槽被占 → `None`
  - `S_SKILL_DIRECT_UNCONFIRMED_NOTE`（`SSEInteractionTexts` 文案，拼在答案尾部）
  - `FORK_EXECUTION_CONTRACT`（追加进子代理 system prompt 的执行契约）

**机制（design D18，已按既有代码对齐）**
1. fork 子代理**不持有 `ask_user`**（Plan 2 的 `FORK_FORBIDDEN_TOOLS` 已硬保证），所以"需确认"只能由**正文 marker**表达：执行契约要求子代理在需要确认时单独输出一行 `CONFIRM_REQUIRED: <问题>`。
2. 编排层按**规则**检测（0 LLM 调用），命中后**复用澄清链路**问用户——照抄 `ask_confirm._ask_web_confirm`（`src/agents/graph/verify/ask_confirm.py`）的写法：`ctx.clarify_channel.put({"type":"ask_user", ...})` + 进程级 `pending_asks[session_id]` 单槽 + `wait_with_abort_and_timeout(fut, ctx.abort_signal, ASK_USER_TIMEOUT)`。
3. 答复 → **带答复重跑一次**子代理；被拒 / 超时 / 槽被占 → 基于现有信息出结论 + 尾部标注"未经确认"，**不再进 verify 重跑**（与 D22「每轮最多重跑 1 次」互斥而非叠加，**不需要额外计数器**）。
4. 放行（用户已答复）时返回的重跑结果仍照常进 verify（verify 自己决定是否再重跑一次）——注意此时**不得**再调确认门（一次性）。
5. **额度记账（已钉死）**：确认门**不消耗** `ctx.ask_count`（那是 LLM 澄清额度），也**不消耗** `ctx.verify_ask_count`（那是 verify 的联网确认额度）——三者是不同语义的独立计数；它只用 `pending_asks` 的**单槽**做互斥。理由与 `ask_confirm` 使用独立计数完全同源：混用会让某一类询问被另一类"吃掉"。

- [ ] **Step 1: 写失败测试**

`tests/agents/graph/test_confirm_gate.py`：

```python
"""直出轮确认门：规则检测 + 复用澄清链路 + 与 verify 重跑互斥（D18）。"""

import pytest

from src.agents.graph.verify.confirm_gate import (
    ask_confirm_question,
    detect_confirm_request,
)
from src.config.const import FORK_CONFIRM_MARKER, SSEInteractionTexts
from src.core.log_events import Event


def test_detect_returns_question_when_marker_present():
    """命中 marker → 返回其后的提问文本。"""
    text = f"我判断需要确认。\n{FORK_CONFIRM_MARKER} 要按 2024 还是 2023 口径？"
    assert detect_confirm_request(text) == "要按 2024 还是 2023 口径？"


def test_detect_returns_empty_without_marker():
    """无 marker → 空串（直通 verify）。"""
    assert detect_confirm_request("这是正常结论。") == ""


def test_detect_ignores_marker_in_middle_only_of_a_line():
    """marker 必须出现在行首（防正文里偶然提到）。"""
    assert detect_confirm_request(f"前文 {FORK_CONFIRM_MARKER} 不是行首") == ""


@pytest.mark.asyncio
async def test_ask_returns_none_when_ctx_missing():
    """无请求上下文 → None（按未确认处理）。"""
    assert await ask_confirm_question("问题？", "sess_1") is None


class _FakeQueue:
    """最小 clarify_channel 替身：只记录 put 的载荷。"""

    def __init__(self):
        self.items = []

    async def put(self, item):
        self.items.append(item)


class _FakeSignal:
    """最小 abort_signal 替身。"""

    def is_set(self):
        return False


class _FakeCtx:
    """最小 RequestContext 替身（够 ask_confirm_question 走通）。"""

    def __init__(self):
        self.clarify_channel = _FakeQueue()
        self.abort_signal = _FakeSignal()


def _patch_ctx(monkeypatch, ctx) -> _FakeCtx:
    """把 confirm_gate 模块里的 current_request_ctx 换成固定返回 ctx 的替身。"""
    holder = type("V", (), {"get": staticmethod(lambda: ctx)})
    monkeypatch.setattr("src.agents.graph.verify.confirm_gate.current_request_ctx", holder)
    return ctx


@pytest.mark.asyncio
async def test_ask_returns_none_when_wait_times_out(monkeypatch):
    """等待返回超时文案（既有 wait_with_abort_and_timeout 返回哨兵而非抛异常）→ None。"""
    ctx = _patch_ctx(monkeypatch, _FakeCtx())

    async def _timeout_result(*args, **kwargs):
        return SSEInteractionTexts.ASK_USER_TIMEOUT_TEXT

    monkeypatch.setattr(
        "src.agents.graph.verify.confirm_gate.wait_with_abort_and_timeout", _timeout_result
    )
    assert await ask_confirm_question("问题？", "sess_1") is None
    assert ctx.clarify_channel.items  # 问题确实经澄清通道投递给了前端


@pytest.mark.asyncio
async def test_ask_returns_reply_text(monkeypatch):
    """用户答复 `[{"selected": ["按 2024 口径"]}]`（clarify.py 的既有消费形状）→ 返回该文本。"""
    _patch_ctx(monkeypatch, _FakeCtx())

    async def _reply(*args, **kwargs):
        return [{"selected": ["按 2024 口径"]}]

    monkeypatch.setattr(
        "src.agents.graph.verify.confirm_gate.wait_with_abort_and_timeout", _reply
    )
    assert await ask_confirm_question("问题？", "sess_1") == "按 2024 口径"
```
```

`tests/agents/skills/test_executor_contract.py`：

```python
"""执行契约必须随执行者人设一起下发给子代理（否则确认门收不到 marker）。"""

from pathlib import Path

from src.agents.skills.executor import SkillExecutor
from src.config.prompts import FORK_EXECUTION_CONTRACT


def test_system_prompt_always_carries_execution_contract():
    """无 preset 时：默认人设 + 执行契约。"""
    exe = SkillExecutor(main_llm=object())
    prompt = exe._executor_system_prompt(None)
    assert FORK_EXECUTION_CONTRACT in prompt


def test_preset_persona_also_carries_execution_contract():
    """有 preset 时：preset 人设 + 执行契约（契约是执行约束，不由内容作者决定）。"""
    class _Preset:
        system_prompt = "你是财务专家。"

    exe = SkillExecutor(main_llm=object())
    prompt = exe._executor_system_prompt(_Preset())
    assert prompt.startswith("你是财务专家。")
    assert FORK_EXECUTION_CONTRACT in prompt
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/agents/graph/test_confirm_gate.py tests/agents/skills/test_executor_contract.py -v`
Expected: FAIL —— `ModuleNotFoundError: src.agents.graph.verify.confirm_gate` / `ImportError: FORK_EXECUTION_CONTRACT`

- [ ] **Step 3: 常量与契约文案**

`src/config/const.py`（`VERIFY_KB_CITATION_MARKER` 之后）：

```python
FORK_CONFIRM_MARKER: str = "CONFIRM_REQUIRED:"
"""子代理"需确认"信号行首标记（编排层规则检测；子代理不持有 ask_user）。"""
```

`SSEInteractionTexts` 内：

```python
    CONFIRM_UNCONFIRMED_NOTE: str = "\n\n> 注：本结论未经用户确认，仅供参考。"
    CONFIRM_QUESTION_TMPL: str = "执行该技能需要你确认：{question}"
```

`src/config/prompts.py`（`FORK_DEFAULT_EXECUTOR_PROMPT` 之后）：

```python
FORK_EXECUTION_CONTRACT: str = (
    "\n\n执行契约（必须遵守）："
    f"如果你需要用户先确认才能给出结论，请单独输出一行 `{FORK_CONFIRM_MARKER} <你的问题>`"
    "并停止作答，不要自行假设后给出结论；其余情况直接给出结论。"
)
```
（顶部 import `FORK_CONFIRM_MARKER` from `src.config.const`。）

- [ ] **Step 4: 追加进子代理 system prompt**

`src/agents/skills/executor.py` 的 `_executor_system_prompt`（现 :275-289）末尾改为：

```python
        if preset is not None:
            prompt = preset.system_prompt
            if prompt:
                return prompt + FORK_EXECUTION_CONTRACT
        return FORK_DEFAULT_EXECUTOR_PROMPT + FORK_EXECUTION_CONTRACT
```

- [ ] **Step 5: 实现确认门**

`src/agents/graph/verify/confirm_gate.py`：

```python
"""直出轮确认门（design D18）——规则检测子代理的"需确认"信号并复用澄清链路问用户。

fork 子代理不持有 ask_user（FORK_FORBIDDEN_TOOLS 硬保证），故"需确认"由正文
marker 表达；本模块只做规则检测与"问一句"，编排（是否重跑、如何标注）由
`skill_direct` 节点负责，保证本模块可纯测。
"""

import asyncio

from src.agents.tools.ask_tools import wait_with_abort_and_timeout
from src.config.const import (
    ASK_USER_TIMEOUT,
    FORK_CONFIRM_MARKER,
    SSEInteractionTexts,
)
from src.core import logging as core_logging
from src.core.log_events import Event
from src.infra.llm.request_context import current_request_ctx, pending_asks


def detect_confirm_request(answer: str) -> str:
    """检测子代理返回的"需确认"信号。

    Args:
        answer: 子代理聚合文本

    Returns:
        命中行首 marker 时返回其后的提问文本（去空白）；未命中返回空串
    """
    for line in answer.splitlines():
        stripped = line.strip()
        if stripped.startswith(FORK_CONFIRM_MARKER):
            return stripped[len(FORK_CONFIRM_MARKER) :].strip()
    return ""


async def ask_confirm_question(question: str, session_id: str) -> str | None:
    """经澄清链路向用户提问并等待答复。

    Args:
        question: 子代理提出的确认问题
        session_id: 会话 ID（pending_asks 单槽键）

    Returns:
        用户的答复文本；ctx 缺失 / 超时 / 澄清槽被占 / 答复不可解析 → None
    """
    ctx = current_request_ctx.get()
    if ctx is None:
        return None
    if session_id in pending_asks:
        # 单槽保护：LLM 澄清或联网确认已挂起时放弃本次确认（按未确认处理）
        return None
    core_logging.log_event(Event.FORK_CONFIRM_ASKED, session_id=session_id)
    payload = {
        "type": "ask_user",
        "questions": [
            {
                "id": "fork_confirm",
                "question": SSEInteractionTexts.CONFIRM_QUESTION_TMPL.format(question=question),
                "dimension": "free",
                "options": [],
                "multi_select": False,
            }
        ],
    }
    loop = asyncio.get_running_loop()
    fut = loop.create_future()
    pending_asks[session_id] = fut
    try:
        await ctx.clarify_channel.put(payload)
        answers = await wait_with_abort_and_timeout(fut, ctx.abort_signal, ASK_USER_TIMEOUT)
    finally:
        pending_asks.pop(session_id, None)
        fut.cancel()
    # 请求取消（abort）时 wait_with_abort_and_timeout 抛 CancelledError，**必须原样透传**
    # （不要 catch），由直出/委派的取消路径收尾；超时则返回文案哨兵，在此按"未确认"处理
    # ——与 ask_confirm._ask_web_confirm 的判定口径保持一致。
    if not isinstance(answers, list) or not answers:
        core_logging.log_event(
            Event.FORK_CONFIRM_UNCONFIRMED, session_id=session_id, reason="no_answer"
        )
        return None
    first = answers[0]
    if not isinstance(first, dict):
        return None
    text = first.get("text")
    if not isinstance(text, str):
        text = ""
    if not text.strip():
        # clarify.py 的既有消费形状是 selected 数组（自由问答也归一化到该字段）
        selected = first.get("selected")
        if isinstance(selected, list) and selected:
            text = " ".join(str(item) for item in selected)
    if not text.strip():
        core_logging.log_event(
            Event.FORK_CONFIRM_UNCONFIRMED, session_id=session_id, reason="empty"
        )
        return None
    return text.strip()
```

- [ ] **Step 6: 直出节点接线（一次性重跑，不与 verify 叠加）**

`src/agents/graph/skill_direct.py` 的 `skill_direct` 内，把 `text = await executor.execute(...)` 之后改为：

```python
        question = detect_confirm_request(text)
        if question:
            reply = await ask_confirm_question(question, state.session_id)
            if reply is None:
                # 拒绝/超时/槽被占 → 出结论 + 标注"未经确认"，不再进 verify 重跑
                return {
                    "answer": text + SSEInteractionTexts.CONFIRM_UNCONFIRMED_NOTE,
                    "tool_contexts": run.ctx.tool_contexts,
                    "verify_temporal_years": run.ctx.temporal_years,
                    "_needs_regenerate": False,
                }
            run = DelegateRun(
                delegate_id=uuid.uuid4().hex[:8],
                skill_name=record.name,
                ctx=main_ctx.child(),
            )
            text = await executor.execute(
                record, f"{state.query}\n\n用户补充说明：{reply}", run
            )
        return {
            "answer": text,
            "tool_contexts": run.ctx.tool_contexts,
            "verify_temporal_years": run.ctx.temporal_years,
        }
```
（顶部补 import：`from src.agents.graph.verify.confirm_gate import ask_confirm_question, detect_confirm_request`。**重跑不再过确认门**——一次性。）

- [ ] **Step 7: 登记两个日志事件**

`Event` 加 `FORK_CONFIRM_ASKED = "fork_confirm_asked"`、`FORK_CONFIRM_UNCONFIRMED = "fork_confirm_unconfirmed"`；`EVENT_SPECS` 同名同集（`prefix="agent"`；前者 `level="info"`、`fields=("session_id",)`；后者 `level="warning"`、`fields=("session_id", "reason")`）。

- [ ] **Step 8: 跑测试确认通过**

Run: `pytest tests/agents/graph/test_confirm_gate.py tests/agents/skills/test_executor_contract.py tests/agents/graph/ tests/agents/skills/ -v && pytest tests/ -q`
Expected: PASS

- [ ] **Step 9: 提交**

```bash
git add src/agents/graph/verify/confirm_gate.py src/config/const.py src/config/prompts.py src/agents/skills/executor.py src/agents/graph/skill_direct.py src/core/log_events.py src/core/log_event_specs.py tests/agents/graph/test_confirm_gate.py tests/agents/skills/test_executor_contract.py
git commit -m "feat(graph): 直出轮确认门（规则检测 + 复用澄清链路），与 verify 重跑互斥"
```

---

### Task 8: 预设预绑定 skill 预加载（§4.8）

**Files:**
- Modify: `src/services/agent_service.py`（`stream_chat` 内在 T5 分派块之后加预加载；新增 `_preload_skills_text`）
- Modify: `src/core/log_events.py` + `src/core/log_event_specs.py`（登记 `SKILL_PRELOAD_SKIP`）
- Test: `tests/services/test_preset_skill_preload.py`（新建）

**Interfaces:**
- Consumes：T3 的生效 `effective_agent`；T5 的 `direct_skill` 与 `ctx.known_skill_names`；**T5b 的 `_inject_skill_message`**；Plan 1 的 `AgentPreset.skills`（`list[str]`，声明顺序即渲染顺序）；Plan 2 的 `render_skill_body(body, task)`
- Produces：`AgentService._preload_skills_text(skill_names: list[str]) -> str`；`AgentService._preload_if_first_round(effective_agent: str, history: list) -> str`

**规则（§4.8 + design D12/D154）**
- 仅当**三个条件同时成立**时注入：① 本轮**没有**显式 `/xxx`（`direct_skill == ""`）**且 history 里的首轮判定成立**（见下"只在首轮"）；② 会话**已绑定**预设（`effective_agent` 非空）；③ 该预设声明了 `skills:` 非空。
- 该预设的每个 skill 取正文（`inline_prompt` 优先，回落 `fork_body`）用 `render_skill_body(body, "")` 渲染（**不带任务文本**——预加载注入的是方法论，不是某次任务），按 `skills` 声明顺序用 `"\n\n"` 拼接。
- **只在首轮注入**：判定 `not history`。`stream_chat` 第一个 await 拿到的历史不含当前 query，首轮必为空；若本轮已有 inline `/xxx`（T5 已往 history 追加过注入条目），history 也非空 → **自动跳过**（这正好实现条件①，无需额外 flag）。
- 声明的技能名查不到 → 记 `SKILL_PRELOAD_SKIP`（warn）并跳过该条，**不影响其余技能**（查不到的判定用 `user_visible()` 名单，与 T5 同源）。
- **注入物走 T5b 的 `_inject_skill_message`**（持久化隐藏消息），**不是** system prompt，也不是每请求字段——否则第二轮就不生效（`specs/agent-preset/spec.md:83`「以隐藏消息注入会话上下文（不进 system prompt）」）。

- [ ] **Step 1: 写失败测试**

`tests/services/test_preset_skill_preload.py`：

```python
"""预设预绑定 skill 首轮预加载：一次生效、不进 system prompt、缺失只 warn。"""

from unittest.mock import AsyncMock, MagicMock

from src.services.agent_service import AgentService


class _Record:
    """最小 SkillRecord 替身（preload 只读 inline_prompt / fork_body）。"""

    def __init__(self, name: str, inline: str = "", fork: str = ""):
        self.name = name
        self.inline_prompt = inline
        self.fork_body = fork


def _service(records: dict[str, _Record]) -> AgentService:
    """构造只带预加载所需依赖的 AgentService（跳过 __init__）。"""
    svc = AgentService.__new__(AgentService)
    registry = MagicMock()
    registry.get = MagicMock(side_effect=lambda name: records.get(name))
    svc._skill_registry = registry
    return svc


def test_preload_renders_in_declared_order():
    """按 skills 声明顺序拼接，inline_prompt 优先于 fork_body。"""
    svc = _service(
        {
            "a": _Record("a", inline="方法论 A：$ARGUMENTS"),
            "b": _Record("b", fork="方法论 B"),
        }
    )
    text = svc._preload_skills_text(["a", "b"])
    assert text.index("方法论 A") < text.index("方法论 B")
    assert text.count("\n\n") == 1


def test_preload_renders_without_task_text():
    """预加载不带任务文本：占位符渲染为空串（注入的是方法论）。"""
    svc = _service({"a": _Record("a", inline="方法论 A：$ARGUMENTS")})
    assert svc._preload_skills_text(["a"]) == "方法论 A："


def test_preload_skips_unknown_skill(monkeypatch):
    """声明了但查不到的技能 → 跳过该条，其余照常，且记 warn。"""
    logged: list[dict] = []
    monkeypatch.setattr(
        "src.services.agent_service.core_logging.log_event",
        lambda event, **fields: logged.append({"event": event, **fields}),
    )
    svc = _service({"a": _Record("a", inline="方法论 A")})
    text = svc._preload_skills_text(["ghost", "a"])
    assert text == "方法论 A"
    assert any("ghost" in str(item) for item in logged)


async def test_preload_only_on_first_round(monkeypatch):
    """首轮返回正文、非首轮返回空（history 非空即跳过）。"""
    svc = _service({"a": _Record("a", inline="方法论 A")})
    svc._preset_registry = MagicMock()
    preset = MagicMock()
    preset.skills = ["a"]
    svc._preset_registry.get = MagicMock(return_value=preset)

    assert svc._preload_if_first_round("finance-expert", []) == "方法论 A"
    assert svc._preload_if_first_round("finance-expert", [object()]) == ""


def test_preload_skipped_when_inline_already_injected():
    """本轮已有 inline `/xxx`（T5 已往 history 追加注入条目）→ 不预加载（条件①）。"""
    svc = _service({"a": _Record("a", inline="方法论 A")})
    svc._preset_registry = MagicMock()
    preset = MagicMock()
    preset.skills = ["a"]
    svc._preset_registry.get = MagicMock(return_value=preset)

    assert svc._preload_if_first_round("finance-expert", [object()]) == ""
```

- [ ] **Step 3b: 首轮注入走 T5b 的持久化通道（断言写库 + 本轮可见）**

追加一条测试：`_preload_if_first_round` 命中后，`stream_chat` 会调用 `_inject_skill_message` 一次（`add_message_async` 收到带 `SKILL_INJECTION_PREFIX` 的内容），且 `launch_context["history"]` 末尾是该注入条目。

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/services/test_preset_skill_preload.py -v`
Expected: FAIL —— `AttributeError: 'AgentService' object has no attribute '_preload_skills_text'`

- [ ] **Step 3: 实现**

`src/services/agent_service.py`（放在 `stream_chat` 之前，T3 的 `_resolve_session_agent` 附近）：

```python
    def _preload_skills_text(self, skill_names: list[str]) -> str:
        """按预设声明的 skills 顺序渲染并拼接各技能正文（隐藏注入用）。

        Args:
            skill_names: 预设 frontmatter 的 skills 列表（声明顺序即渲染顺序）

        Returns:
            用 "\\n\\n" 拼接的正文；全部查不到时返回空串
        """
        parts: list[str] = []
        for name in skill_names:
            if self._skill_registry is None:
                break
            record = self._skill_registry.get(name)
            if record is None:
                core_logging.log_event(
                    Event.SKILL_PRELOAD_SKIP, skill=name, reason="not_found"
                )
                continue
            body = record.inline_prompt
            if not body:
                body = record.fork_body
            if not body:
                continue
            parts.append(render_skill_body(body, ""))
        return "\n\n".join(parts)

    def _preload_if_first_round(self, effective_agent: str, history: list) -> str:
        """首轮预加载判定：仅在首轮、已绑定预设且该预设声明 skills 时给出待注入正文。

        Args:
            effective_agent: 本会话生效的智能体名（空=未绑定）
            history: 本轮的历史消息（**若本轮已有 inline `/xxx` 注入，T5 已往其中追加条目**→非空）

        Returns:
            预加载正文；任一条件不满足返回空串
        """
        if history:
            return ""
        if not effective_agent:
            return ""
        if self._preset_registry is None:
            return ""
        preset = self._preset_registry.get(effective_agent)
        if preset is None or not preset.skills:
            return ""
        return self._preload_skills_text(preset.skills)
```

`stream_chat` 内 T5 分派块**之后**、`launch_context` 之前插入：

```python
        if direct_skill == "":
            preload_text = self._preload_if_first_round(effective_agent, history)
            if preload_text:
                preload_entry = await self._inject_skill_message(
                    session_id, kb_id, preload_text
                )
                history = history + [preload_entry]
                launch_context["history"] = history
```
（预加载与 `/xxx` 走**同一条**持久化隐藏消息通道（T5b）；`launch_context["history"]` 需写回，本轮与后续轮才都生效。）

- [ ] **Step 4: 登记 `SKILL_PRELOAD_SKIP`**

`Event` 加 `SKILL_PRELOAD_SKIP = "skill_preload_skip"`；`EVENT_SPECS` 同名同集（`prefix="session"`、`level="warning"`、`fields=("skill", "reason")`）。

- [ ] **Step 5: 跑测试确认通过**

Run: `pytest tests/services/test_preset_skill_preload.py tests/services/ -v && pytest tests/ -q`
Expected: PASS

- [ ] **Step 6: 提交**

```bash
git add src/services/agent_service.py src/core/log_events.py src/core/log_event_specs.py tests/services/test_preset_skill_preload.py
git commit -m "feat(session): 预设预绑定 skill 首轮预加载（隐藏注入、一次生效）"
```

---

### Task 9: 能力清单服务 + 两个只读接口（§5.10）

**Files:**
- Create: `src/services/capability_service.py`
- Create: `src/api/capabilities.py`
- Modify: `src/main.py:196-208`（import + `include_router`）
- Modify: `src/services/agent_service.py`（把 `skill_registry` / `preset_registry` 提升为 `self._*` 并构造 `self.capability_service`）
- Modify: `src/core/log_events.py` + `src/core/log_event_specs.py`（登记 `CAPABILITY_DEGRADED`）
- Test: `tests/services/test_capability_service.py`、`tests/api/test_capabilities.py`（新建）

**Interfaces:**
- Consumes：`SkillRegistry.user_visible() -> list[SkillRecord]`（`registry.py:72-78`，已按 `user_invocable` 过滤）；`AgentPresetRegistry.all() -> list[AgentPreset]`（`presets/registry.py:53-55`，已按名排序）
- Produces：
  - `CapabilityService(skill_registry, preset_registry)`
  - `CapabilityService.list_skills() -> list[dict]` → `[{"name","description"}]`
  - `CapabilityService.list_agents() -> list[dict]` → `[{"name","display_name","description"}]`
  - `AgentService.capability_service: CapabilityService`
  - `GET /api/skills` → `ResponseModel(data={"skills": [...]})`；`GET /api/agents` → `ResponseModel(data={"agents": [...]})`

**硬性要求（design D19）**：**不引入 catalog 文件**（清单由 registry 派生）；api 层只转发（不读文件/不扫目录）；skills 服务端过滤 `user-invocable:false`（由 `user_visible()` 保证）；读取失败 **fail-open**（空列表 + 200，**不 500**）。`ResponseModel` 在 **`src/api/schema.py`**（不在 `model/response.py`）。

- [ ] **Step 1: 写失败测试**

`tests/services/test_capability_service.py`：

```python
"""能力清单服务：由 registry 派生、字段投影正确、无 registry 时降级空列表。"""

from unittest.mock import MagicMock

from src.services.capability_service import CapabilityService


class _Skill:
    def __init__(self, name: str, description: str):
        self.name = name
        self.description = description


class _Preset:
    def __init__(self, name: str, display_name: str, description: str):
        self.name = name
        self.display_name = display_name
        self.description = description


def test_list_skills_projects_name_and_description():
    """只投影 name/description（不透传整对象）。"""
    registry = MagicMock()
    registry.user_visible.return_value = [_Skill("finance-qa", "财务问答")]
    svc = CapabilityService(registry, MagicMock())
    assert svc.list_skills() == [{"name": "finance-qa", "description": "财务问答"}]


def test_list_agents_projects_display_name():
    """agents 多一个 display_name（选择器显示中文名）。"""
    preset_registry = MagicMock()
    preset_registry.all.return_value = [_Preset("finance-expert", "财务专家", "财务分析")]
    svc = CapabilityService(MagicMock(), preset_registry)
    assert svc.list_agents() == [
        {"name": "finance-expert", "display_name": "财务专家", "description": "财务分析"}
    ]


def test_missing_registry_returns_empty_list():
    """registry 为 None（skills 目录缺失）→ 空列表，不抛。"""
    svc = CapabilityService(None, None)
    assert svc.list_skills() == []
    assert svc.list_agents() == []
```

`tests/api/test_capabilities.py`：

```python
"""GET /api/skills、/api/agents：统一信封 + 服务端过滤 + 失败 fail-open。"""

from unittest.mock import AsyncMock, MagicMock


def test_skills_envelope(auth_client, mock_app_service):
    """信封为 data.skills（前端按 body.data 解析）。"""
    mock_app_service.agent_service.capability_service.list_skills = MagicMock(
        return_value=[{"name": "finance-qa", "description": "财务问答"}]
    )
    resp = auth_client.get("/api/skills")
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == "SUCCESS"
    assert body["data"]["skills"][0]["name"] == "finance-qa"


def test_agents_envelope(auth_client, mock_app_service):
    """信封为 data.agents。"""
    mock_app_service.agent_service.capability_service.list_agents = MagicMock(
        return_value=[{"name": "finance-expert", "display_name": "财务专家", "description": "d"}]
    )
    resp = auth_client.get("/api/agents")
    assert resp.status_code == 200
    assert resp.json()["data"]["agents"][0]["display_name"] == "财务专家"


def test_failure_returns_empty_list_not_500(auth_client, mock_app_service):
    """读取失败 → 空列表 + 200（fail-open，不阻断选择器渲染）。"""
    mock_app_service.agent_service.capability_service.list_skills = MagicMock(
        side_effect=RuntimeError("boom")
    )
    resp = auth_client.get("/api/skills")
    assert resp.status_code == 200
    assert resp.json()["data"]["skills"] == []
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/services/test_capability_service.py tests/api/test_capabilities.py -v`
Expected: FAIL —— `ModuleNotFoundError: src.services.capability_service` / 404

- [ ] **Step 3: 实现服务与接口**

`src/services/capability_service.py`：

```python
"""能力清单服务（design D19）——清单由 registry 派生，不引入 catalog 文件。

只做"取 + 投影"，不做缓存：registry 自身有懒重载（文件 mtime 变化自动生效），
再加一层缓存就等于要养第二份陈旧源。api 层只转发。
"""

from src.agents.presets.registry import AgentPresetRegistry
from src.agents.skills.registry import SkillRegistry


class CapabilityService:
    """对外暴露"可调用技能"与"可选智能体"两个只读清单。"""

    def __init__(
        self,
        skill_registry: SkillRegistry | None,
        preset_registry: AgentPresetRegistry | None,
    ) -> None:
        """注入两个注册表（可为 None —— skills 目录缺失时降级空列表）。"""
        self._skill_registry = skill_registry
        self._preset_registry = preset_registry

    def list_skills(self) -> list[dict]:
        """可被用户 `/xxx` 调用的技能（服务端已过滤 user-invocable:false）。"""
        if self._skill_registry is None:
            return []
        records = self._skill_registry.user_visible()
        return [{"name": r.name, "description": r.description} for r in records]

    def list_agents(self) -> list[dict]:
        """全部可加载的智能体预设（含 display_name，供选择器显示中文名）。"""
        if self._preset_registry is None:
            return []
        presets = self._preset_registry.all()
        return [
            {"name": p.name, "display_name": p.display_name, "description": p.description}
            for p in presets
        ]
```

`src/api/capabilities.py`（照 `src/api/auth.py` 的写法）：

```python
"""能力清单只读接口 —— GET /api/skills、GET /api/agents（design D19）。"""

from fastapi import APIRouter, Depends

from src.api.dependencies import get_app_service
from src.api.schema import ResponseModel
from src.services.app_service import AppService

router = APIRouter()


@router.get("/skills", response_model=ResponseModel)
async def list_skills(svc: AppService = Depends(get_app_service)):
    """返回可被用户调用的技能清单。

    Returns:
        ResponseModel: data 为 {"skills": [{"name", "description"}]}；读取失败返回空列表（fail-open）
    """
    from src.core import logging as core_logging
    from src.core.log_events import Event

    try:
        skills = svc.agent_service.capability_service.list_skills()
    except Exception:  # noqa: BLE001 —— 配置类错误 fail-open，不阻断前端渲染
        core_logging.log_event(Event.CAPABILITY_DEGRADED, resource="skills", reason="read_failed")
        skills = []
    return ResponseModel(data={"skills": skills})


@router.get("/agents", response_model=ResponseModel)
async def list_agents(svc: AppService = Depends(get_app_service)):
    """返回全部可加载的智能体预设清单（含 display_name）。

    Returns:
        ResponseModel: data 为 {"agents": [{"name", "display_name", "description"}]}
    """
    from src.core import logging as core_logging
    from src.core.log_events import Event

    try:
        agents = svc.agent_service.capability_service.list_agents()
    except Exception:  # noqa: BLE001
        core_logging.log_event(Event.CAPABILITY_DEGRADED, resource="agents", reason="read_failed")
        agents = []
    return ResponseModel(data={"agents": agents})
```

`src/main.py`：import 段加 `from src.api import capabilities as capabilities_routes`，并注册：

```python
app.include_router(capabilities_routes.router, prefix="/api", tags=["capabilities"])
```

- [ ] **Step 4: 提升 registry 到实例属性并装配**

`src/services/agent_service.py` 的 `__init__`：
- 在 skills 分支**之前**初始化 `skill_registry = None` / `preset_registry = None`（分支内赋值）。
- skills 分支内 `self._skill_registry = skill_registry`（T5 也要用）、`self._preset_registry = preset_registry`。
- `build_graph` 之后：

```python
        self.capability_service = CapabilityService(skill_registry, preset_registry)
```
（顶部 import `from src.services.capability_service import CapabilityService`；该模块只依赖 agents 层注册表，无循环 import。）

- [ ] **Step 5: 登记 `CAPABILITY_DEGRADED`**

`Event` 加 `CAPABILITY_DEGRADED = "capability_degraded"`；`EVENT_SPECS` 同名同集（`prefix="app"`、`level="warning"`、`fields=("resource", "reason")`）。

- [ ] **Step 6: 跑测试确认通过**

Run: `pytest tests/services/test_capability_service.py tests/api/test_capabilities.py -v && pytest tests/ -q`
Expected: PASS

- [ ] **Step 7: 提交**

```bash
git add src/services/capability_service.py src/api/capabilities.py src/main.py src/services/agent_service.py src/core/log_events.py src/core/log_event_specs.py tests/services/test_capability_service.py tests/api/test_capabilities.py
git commit -m "feat(api): 能力清单服务与 /api/skills、/api/agents 只读接口（registry 派生，fail-open）"
```

---

### Task 10: 收口（测试补齐 / 契约文档 / 门禁）

**Files:**
- Modify: `docs/agents/api_contract.md`（§7.1）
- Modify: `docs/agents/glossary.md`（§7.2）
- Modify: `docs/agents/code-map.md`（§7.3）
- Modify: `CLAUDE.md`（§7.4）
- Modify: `docs/agents/data-flow.md`（§7.6）
- Modify: `docs/agents/reference-projects.md`（§7.7）
- Modify: `docs/openspec/changes/agent-delegation-skills/design.md` 或 `tasks.md`（§7.5：标注 D7 被本 change 修订）
- Modify: `docs/openspec/changes/session-agent-and-skill-invocation/tasks.md`（把本轮完成项标注「已由 Plan 3 完成」）
- Test: 全量门禁

- [ ] **Step 1: 契约文档（§7.1）** —— `docs/agents/api_contract.md` 追加一节（`### 5.6 会话智能体与 `/xxx` 契约`，紧接 Plan 2 收口时新增的 `5.5`）：
  - `POST /chat/stream` 请求体 `agent: str = ""`：**值是预设 `name`（ASCII slug）不是 id**；空＝沿用；与已绑定值不一致 → **服务端忽略 + warning，无 400**（对齐 design D1/D20）。
  - `/xxx` 前缀：只在**行首**且形如 ASCII slug 才按命令解析；未注册 → 返回"不存在 + 可用列表"（不静默）；**落库与 Redis 历史保留原文，仅在组装 prompt 时剥离**（design D25）——踩坑：不要在 `add_message_async` 前改写 `query`。
  - `POST /sessions/list` 新增 `agent` 字段（**`sessions/messages` 契约不变，`data` 仍为数组**）。
  - 流事件 `agent_used`：载荷 `{"agent": string}`；**语义 = 本会话绑定值，不含 fork 执行者**。
  - `GET /api/skills` / `GET /api/agents`：统一信封 `data.skills` / `data.agents`；失败 fail-open 空列表。
- [ ] **Step 2: 术语与结构（§7.2/7.3/7.4）**
  - `glossary.md`：加「智能体预设」「会话级 vs 消息级」两条（一句话 + 指针到 design D1/D21，**不复制正文**）。
  - `code-map.md`：登记 `agents/`（运行时内容目录，compose 挂载到 `/app/agents`）、`src/agents/presets/`、`src/agents/skills/prefix.py`、`src/services/capability_service.py`、`src/api/capabilities.py`。
  - `CLAUDE.md` 目录速览：后端分层里补 `agents/` 与 presets 一句；「文档组织」表如新增归属文档则同步登记。
- [ ] **Step 3: 调研与依赖说明（§7.5/7.7）**
  - `agent-delegation-skills` 的 D7：加一行「本 D7 已被 `session-agent-and-skill-invocation` 修订（fork 工具放开 + 执行者选择），以该 change 的 design D3/D7 为准」。
  - `reference-projects.md`：更新 agency-agents 条目（作为智能体预设来源）+ 记录「`create_react_agent` 已废弃 → `langchain.agents.create_agent`（`system_prompt` 非 `prompt`）」结论。
  - `data-flow.md`：核对「答案校验与引用格式化链路」一节与最终实现一致（重点：`state.tool_contexts` / `verify_temporal_years` 的承载与直出轮的判据来源）。
- [ ] **Step 4: 逐条核对 §5.11 的测试清单**（下表每行必须有一个**已存在且能判别**的用例；缺则补，补在对应任务的测试文件里）

| §5.11 断言 | 落在哪 |
|---|---|
| 前缀路由（plain/known/unknown 三态） | `tests/agents/skills/test_skill_prefix.py`（T2） |
| 前缀清洗（当前轮与历史都不含 `/name`，**落库仍为原文**） | `tests/services/test_skill_prefix_dispatch.py` + `tests/agents/graph/test_prefix_cleaning.py`（T5） |
| `/xxx` inline 单轮 | 同上（T5：`direct_skill==""` + 本轮 `history` 末尾有注入条目） |
| `/xxx` fork 直出（主 agent 0 LLM 轮 + citations 非空 + 无 `INVALID_CITATION`） | `tests/agents/graph/test_direct_skill_round.py`（Plan 2）+ `tests/services/test_direct_round_delivery.py`（T6） |
| 注入后持续生效（跨轮） | `tests/services/test_skill_injection_persist.py` + `tests/agents/graph/test_injected_history.py`（T5b：写 Redis+DB、标记行抽成独立 HumanMessage） |
| 注入对前端隐藏 | `tests/api/test_sessions.py`（T5b：`sessions/messages` 过滤标记行且仍为数组） |
| 双轴过滤 | `tests/agents/skills/test_skill_registry.py`（Plan 1）+ `test_capabilities.py`（T9：`user-invocable:false` 不出现） |
| 绑定四态（首轮绑定 / 沿用 / 忽略+warn / 未注册降级） | `tests/services/test_session_agent_binding.py`（T3） |
| 老会话 `bind-if-empty` 可绑定 | `tests/infra/db/test_mysql_db.py::test_bind_session_agent_is_bind_once`（T1） |
| `agent_used` 流事件 | `tests/services/test_session_agent_binding.py` 或 `tests/chat/test_streaming.py`（T3：序列化 + 载荷） |
| 未选 agent 时 system 段快照逐字一致 | `tests/rag/test_prompt_layers.py::test_no_persona_keeps_system_messages_byte_identical`（T4） |
| 两接口信封结构 + 服务端过滤 + 失败降级 | `tests/api/test_capabilities.py`（T9） |
| 非法名称跳过 | `tests/agents/presets/`（Plan 1）+ `test_preload_skips_unknown_skill`（T8） |
| `sessions/messages` 契约不变（`data` 仍为数组） | `tests/api/test_sessions.py`（T1 追加断言） |

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
git commit -m "docs(session-agent): 收口契约/术语/结构/调研与 §5.11 测试清单核对"
```

---

## Self-Review

**1. Spec coverage（对照 change 的 §5 / §7 / §4.6 / §4.8）**

| change 任务 | 覆盖 | 满配度 |
|---|---|---|
| 5.4 前缀解析 + 执行形态分派 + 读时清洗 | T2（解析/清洗）+ T5（分派/接线）+ T5b（注入通道） | 满配 |
| 隐藏消息注入与持久化（「持续生效」Requirement） | T5b | 满配（含前端隐藏 + 跨轮可见 + 本轮可见三条断言） |
| 5.5 未命中判定 + 文案 | T2（三态）+ T5（fail-open 通道）+ T2 Step 4（文案） | 满配 |
| 5.6 请求 `agent` 字段 | T3 | 满配 |
| 5.7 存储链（4 小项） | T1 | 满配 |
| 5.8 bind-once + `agent_used` | T3 | 满配（读绑定值已钉死为 `get_session_agent_async`） |
| 5.9 三层 prompt 组装 | T4 | 满配（含逐字不变守卫；`_with_current_date` 已钉死为直接 import） |
| 5.10 能力清单服务 + 接口 | T9 | 满配 |
| 5.11 测试 | 各任务内嵌 + T10 Step 4 清单核对表 | 满配（清单逐条映射到具体用例） |
| 5.12 直出轮交付/落库 | T6 | 满配（含生产链端到端测试要求） |
| 5.13 直出轮重跑消费指引 | T6 Step 3 | 满配 |
| 5.14 生产 `agents/`/`skills/` 挂载与 COPY | **移出（已接受的延后）** | 见 4. 未覆盖项 |
| 4.6 确认门 | T7 | 满配（`detect_confirm_request` 3 条 + `ask_confirm_question` 3 条 + 执行契约 2 条测试，含完整实现） |
| 4.8 预设预绑定预加载 | T8 | 满配 |
| 7.1–7.7 文档同步 | T10 | 满配（逐份文档的落地清单） |

**2. Placeholder scan**：**T1–T10（含 T5b）全部满配**（含可直接粘贴的测试代码与实现代码；T10 为文档与门禁，已给出逐份文档的落地清单与 §5.11 逐条映射表）。**无刻意留白**——两处原有的二选一（T3 的读绑定值、T4 的 `_with_current_date` 复用）已在本轮复审中钉死为单一实现。

**3. Type consistency**：`PrefixParse(kind, skill_name, task, record)`（T2 定 → T5 消费）；`parse_prefix(text, known_names, registry)` / `clean_prefix(text, known_names)`（T2 定 → T5/T5b 消费）；`bind_session_agent(session_id, agent) -> bool`（T1 定 → T3 消费）；`get_session_agent(session_id) -> str` / `get_session_agent_async(session_id) -> str`（T3 定）；`_resolve_session_agent(session_id, requested, bound="")`（T3 定）；`get_base_system_prompt()` / `build_system_prompt(persona, kb_bound, has_skills, prompt_manager) -> list[SystemMessage]`（T4 定 → T5b 经 ctx 消费）；`RequestContext.known_skill_names: set[str]` / `persona: str` / `has_skills: bool`（T5 定 → T5b/`query_router` 消费）；`SKILL_INJECTION_PREFIX: str` + `AgentService._inject_skill_message(session_id, kb_id, text) -> ChatMessage`（T5b 定 → T5/T8 消费）；`AgentService._preload_skills_text(skill_names)` / `_preload_if_first_round(effective_agent, history)`（T8 定）；`SSEAgentUsedEvent(agent, type, seq)`（T3 定）；`detect_confirm_request(answer)` / `ask_confirm_question(question, session_id)`（T7 定）。命名已核对一致。

**4. 阻塞与前置 / 已知并接受的延后**
- **5.14（生产部署缺口）—— 已接受的延后，不是遗漏**：`Dockerfile` 只 `COPY src/ scripts/ deploy/`，`docker-compose.prod.yml` 只挂 `./skills`、**没有** `agents/` → 生产环境 `_resolve_executor` 恒 `None`、会话智能体能力静默降级。**本轮决定：先在开发环境跑通，不动生产部署**（开发侧 `docker-compose.override.yml` 已挂 `agents/` + `skills/`）。**上生产前必须补**：① `docker-compose.prod.yml` 的 app volumes 增加 `agents/` 与 `skills/` 挂载；② 或 `Dockerfile` 增加 `COPY skills/ agents/`。**触发条件**：任何一个部署包发布前。**若判断有误**：生产上线后「会话智能体」整条主线零效果，且日志里只会看到执行者选择静默降级。
- **"开发环境跑通"的验收标准（6 条，Plan 3 + Plan 4 全部完成后逐条走）**：
  1. 会话绑定智能体后，顶栏回显该智能体，且流式请求体带 `agent`（用 `playwright-cli requests` 核对）；
  2. 未选智能体时，system 段与改动前**逐字不变**（`tests/rag/test_prompt_layers.py` 的快照守卫 + 人工抽看日志）；
  3. `/財務分析…` 这类的 **inline** 技能：主 agent 单轮（1 次 LLM 调用）且方法论生效；
  4. `/finance-analyst …` 这类的 **fork** 技能：主 agent **0 轮 LLM**、回答**可见且落库**（`conversation_history.assistant` 非空）、`citations` 非空；
  5. 技能 chip 插入 `/name ` 与输入框 `/` 补全都生效（Enter 不误发）；
  6. 常规轮（不选智能体、不选技能）回归通过：流式 token / citation / done 正常，无新增 console error。
- **执行顺序（T5b 必须提前）**：T1 → T2 → T3 → T4 → **T5b** → T5 → T6 → T7/T8 → T9 → T10。T5b 是 T5（inline 注入）与 T8（预加载注入）的**共同前置**；T7（确认门）与 T8（预加载）彼此独立，可换序。
