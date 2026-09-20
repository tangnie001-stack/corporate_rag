"""关系型数据层集成测试 — 使用 KbRepo / DocumentRepo / ChatRepo（PostgreSQL）。"""

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text

from src.infra.db.engine import session_factory
from src.infra.db.models.document import DocModel as DocEntity
from src.infra.db.repos import DocumentRepo, KbRepo

# 本文件三个消息用例写入 `conversation_history` 的 session_id 前缀。
# `save_message` 是纯 INSERT（新 uuid 主键），该表既无 user_id 列也无外键，
# 消息行不与任何 sessions 行挂钩，故只能按前缀定位。生产会话 id 是裸 UUID
# （形如 94feec1d-c013-...），不含 "sess-" 片段，谓词与生产/开发数据不相交。
# 守卫用例复用同一 WHERE，保证断言与 fixture 删除范围一致。
_TEST_MESSAGE_WHERE = (
    "session_id LIKE 'sess-status-%'"
    " OR session_id LIKE 'sess-st-%'"
    " OR session_id LIKE 'sess-ts-%'"
)
_DELETE_TEST_MESSAGES = text(
    "DELETE FROM conversation_history WHERE " + _TEST_MESSAGE_WHERE
)


@pytest.fixture
async def repos():
    """提供 KbRepo 和 DocumentRepo 实例。"""
    kb_repo = KbRepo(session_factory)
    doc_repo = DocumentRepo(session_factory)
    yield kb_repo, doc_repo


@pytest_asyncio.fixture(autouse=True)
async def _cleanup_test_user_rows():
    """每个用例前后清掉本文件写入真实 PG 的记录（直写真实 PG 的代价由本 fixture 承担）。

    刻意**不**调 `tests/reset_data.reset_pg()` —— 那会连 176 分块语料一起清掉。
    只删本文件自己造的数据：`user_id='test-user'` 的知识库及其文档/分块、
    `test-user`、`u_agent` 两类会话（后者见 test_bind_session_agent_is_bind_once
    与 test_create_session_with_agent_persists），以及三个消息用例按前缀写入的
    `conversation_history` 行（见 `_TEST_MESSAGE_WHERE`）。

    删除顺序受外键约束：`chunks.kb_id` 指向 `knowledge_base.id`，故 chunks 先删；
    文档按 `kb_id` 归属删除而非 `document.user_id`（本文件写入的文档该列为空串，
    按 user_id 删不掉）。
    """
    async with session_factory() as s:
        await s.execute(
            text(
                "DELETE FROM chunks WHERE kb_id IN"
                " (SELECT id FROM knowledge_base WHERE user_id = 'test-user')"
            )
        )
        await s.execute(
            text(
                "DELETE FROM document WHERE kb_id IN"
                " (SELECT id FROM knowledge_base WHERE user_id = 'test-user')"
            )
        )
        await s.execute(text("DELETE FROM knowledge_base WHERE user_id = 'test-user'"))
        await s.execute(
            text("DELETE FROM sessions WHERE user_id IN ('test-user', 'u_agent')")
        )
        # 消息行按 session_id 前缀删除：conversation_history 无 user_id 列、无外键，
        # 且 save_message 是插入而非 upsert，无法靠 sessions 关联定位。
        await s.execute(_DELETE_TEST_MESSAGES)
        await s.commit()
    yield
    async with session_factory() as s:
        await s.execute(
            text(
                "DELETE FROM chunks WHERE kb_id IN"
                " (SELECT id FROM knowledge_base WHERE user_id = 'test-user')"
            )
        )
        await s.execute(
            text(
                "DELETE FROM document WHERE kb_id IN"
                " (SELECT id FROM knowledge_base WHERE user_id = 'test-user')"
            )
        )
        await s.execute(text("DELETE FROM knowledge_base WHERE user_id = 'test-user'"))
        await s.execute(
            text("DELETE FROM sessions WHERE user_id IN ('test-user', 'u_agent')")
        )
        # 消息行按 session_id 前缀删除：conversation_history 无 user_id 列、无外键，
        # 且 save_message 是插入而非 upsert，无法靠 sessions 关联定位。
        await s.execute(_DELETE_TEST_MESSAGES)
        await s.commit()


@pytest.mark.asyncio
async def test_create_and_get_kb():
    """测试创建和查询知识库的完整流程。"""
    kb_repo = KbRepo(session_factory)
    user_id = "test-user"
    name = f"test-kb-{uuid.uuid4().hex[:8]}"
    kb_id, is_new = await kb_repo.get_or_create_kb(user_id, name)
    assert is_new is True
    found_id = await kb_repo.get_kb_by_name(user_id, name)
    assert found_id == kb_id


@pytest.mark.asyncio
async def test_document_crud():
    """测试文档的增删查操作。"""
    kb_repo = KbRepo(session_factory)
    doc_repo = DocumentRepo(session_factory)
    # 先创建知识库，再添加文档以满足外键约束
    user_id = "test-user"
    kb_name = f"test-doc-kb-{uuid.uuid4().hex[:8]}"
    kb_id, _ = await kb_repo.get_or_create_kb(user_id, kb_name)
    doc_id = str(uuid.uuid4())
    doc = DocEntity(
        id=doc_id, kb_id=kb_id, filename="test.pdf", file_type="pdf", file_size=100
    )
    await doc_repo.add_document(doc)
    docs = await doc_repo.get_documents(kb_id)
    doc_ids = [d.id for d in docs]
    assert doc_id in doc_ids


@pytest.mark.asyncio
async def test_get_kb_name_by_id():
    """测试根据知识库 ID 查询名称。"""
    kb_repo = KbRepo(session_factory)
    user_id = "test-user"
    name = f"test-kb-name-{uuid.uuid4().hex[:8]}"
    kb_id, _ = await kb_repo.get_or_create_kb(user_id, name)

    result = await kb_repo.get_kb_name_by_id(kb_id)
    assert result == name

    # 不存在的 ID 返回 None
    result = await kb_repo.get_kb_name_by_id(str(uuid.uuid4()))
    assert result is None


@pytest.mark.asyncio
async def test_get_doc_names():
    """测试根据文档 ID 列表查询文件名。"""
    kb_repo = KbRepo(session_factory)
    doc_repo = DocumentRepo(session_factory)
    user_id = "test-user"
    kb_name = f"test-doc-names-{uuid.uuid4().hex[:8]}"
    kb_id, _ = await kb_repo.get_or_create_kb(user_id, kb_name)

    # 创建多个文档
    doc_id_1 = str(uuid.uuid4())
    doc_id_2 = str(uuid.uuid4())
    doc_id_3 = str(uuid.uuid4())
    await doc_repo.add_document(
        DocEntity(
            id=doc_id_1,
            kb_id=kb_id,
            filename="report.pdf",
            file_type="pdf",
            file_size=100,
        )
    )
    await doc_repo.add_document(
        DocEntity(
            id=doc_id_2,
            kb_id=kb_id,
            filename="summary.docx",
            file_type="docx",
            file_size=200,
        )
    )
    await doc_repo.add_document(
        DocEntity(
            id=doc_id_3,
            kb_id=kb_id,
            filename="data.xlsx",
            file_type="xlsx",
            file_size=300,
        )
    )

    # 查询部分文档
    result = await doc_repo.get_doc_names([doc_id_1, doc_id_3])
    assert result == {doc_id_1: "report.pdf", doc_id_3: "data.xlsx"}

    # 空列表返回空字典
    result = await doc_repo.get_doc_names([])
    assert result == {}

    # 不存在的 ID 不包含在结果中
    result = await doc_repo.get_doc_names([str(uuid.uuid4())])
    assert result == {}


@pytest.mark.asyncio
async def test_get_all_kb_doc_count():
    """get_all_kb 的 doc_count 应实时统计未删除的文档数，而非读取静态列。

    回归场景：SQLAlchemy 异步迁移后 doc_count 直接读列值（恒为 0），
    与文档列表的真实数量不一致。
    """
    kb_repo = KbRepo(session_factory)
    doc_repo = DocumentRepo(session_factory)
    user_id = "test-user"
    kb_name = f"test-doc-count-{uuid.uuid4().hex[:8]}"
    kb_id, _ = await kb_repo.get_or_create_kb(user_id, kb_name)

    # 添加 2 个文档
    for i in range(2):
        await doc_repo.add_document(
            DocEntity(
                id=str(uuid.uuid4()),
                kb_id=kb_id,
                filename=f"doc-{i}.pdf",
                file_type="pdf",
                file_size=100,
            )
        )

    kbs = await kb_repo.get_all_kb(user_id)
    target = next(kb for kb in kbs if kb.id == kb_id)
    assert target.doc_count == 2


@pytest.mark.asyncio
async def test_create_session_idempotent():
    """同一 session_id 重复创建应幂等跳过，不抛主键冲突异常。"""
    from src.infra.db.models.chat import SessionModel
    from src.infra.db.repos import ChatRepo

    chat_repo = ChatRepo(session_factory)
    session_id = f"session-{uuid.uuid4().hex[:8]}"
    base = {"user_id": "test-user", "title": "测试会话", "kb_id": ""}

    await chat_repo.create_session(SessionModel(id=session_id, **base))
    # 第二次创建同 id：应静默跳过而非抛 IntegrityError
    await chat_repo.create_session(SessionModel(id=session_id, **base))

    found = await chat_repo.get_session_by_id(session_id)
    assert found is not None
    assert found.title == "测试会话"


@pytest.mark.asyncio
async def test_message_status_default_complete():
    from src.infra.db.models.chat import MessageModel
    from src.infra.db.repos import ChatRepo

    chat_repo = ChatRepo(session_factory)
    session_id = f"sess-status-{uuid.uuid4().hex[:8]}"
    await chat_repo.save_message(
        MessageModel(session_id=session_id, kb_id="", role="user", content="q")
    )
    msgs = await chat_repo.get_messages(session_id)
    assert msgs[0].status == "complete"


@pytest.mark.asyncio
async def test_save_message_passthrough_status():
    from src.infra.db.models.chat import MessageModel
    from src.infra.db.repos import ChatRepo

    chat_repo = ChatRepo(session_factory)
    session_id = f"sess-st-{uuid.uuid4().hex[:8]}"
    await chat_repo.save_message(
        MessageModel(
            session_id=session_id,
            kb_id="",
            role="assistant",
            content="partial",
            status="interrupted",
        )
    )
    msgs = await chat_repo.get_messages(session_id)
    assert msgs[0].status == "interrupted"


@pytest.mark.asyncio
async def test_user_created_at_before_assistant():
    """user 消息必须先于 assistant 消息落库（M1 时序）。

    PostgreSQL 时间戳是微秒精度，且两条消息各自独立事务提交，
    无需等待；断言用 <= 宽容到能接受相同时间戳。
    """
    from src.infra.db.models.chat import MessageModel
    from src.infra.db.repos import ChatRepo

    chat_repo = ChatRepo(session_factory)
    session_id = f"sess-ts-{uuid.uuid4().hex[:8]}"
    # 模拟 M1 时序：user 请求开始写，assistant 延迟（流结束）写
    await chat_repo.save_message(
        MessageModel(session_id=session_id, kb_id="", role="user", content="q")
    )
    await chat_repo.save_message(
        MessageModel(
            session_id=session_id,
            kb_id="",
            role="assistant",
            content="a",
            status="complete",
        )
    )
    msgs = await chat_repo.get_messages(session_id)
    assert [m.role for m in msgs] == ["user", "assistant"]
    assert msgs[0].created_at <= msgs[1].created_at


@pytest.mark.asyncio
async def test_bind_session_agent_is_bind_once():
    """bind-once：首次写入成功；再次写入（含不同值）不改动已有绑定。"""
    from src.infra.db.models.chat import SessionModel
    from src.infra.db.repos import ChatRepo

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


@pytest.mark.asyncio
async def test_create_session_with_agent_persists():
    """create_session 落 agent；未传时落空串（存量语义不变）。"""
    from src.infra.db.models.chat import SessionModel
    from src.infra.db.repos import ChatRepo

    chat_repo = ChatRepo(session_factory)
    sid = f"sess_{uuid.uuid4().hex[:12]}"
    await chat_repo.create_session(
        SessionModel(
            id=sid,
            user_id="u_agent",
            title="带智能体",
            kb_id="",
            agent="finance-expert",
        )
    )

    rows = await chat_repo.get_sessions("u_agent")
    target = [row for row in rows if row.id == sid]
    assert len(target) == 1
    assert target[0].agent == "finance-expert"


@pytest.mark.asyncio
async def test_file_leaves_no_rows_behind_note():
    """本文件跑完后真实 PG 无残留 —— 覆盖 fixture 的每一条删除谓词。

    该用例本身不造数据：autouse fixture 已在本用例进入前完成收尾。
    断言逐句对应 `_cleanup_test_user_rows` 的删除集合：`test-user` 的知识库、
    挂在 `test-user` 知识库下的文档与分块、`test-user`/`u_agent` 会话、
    以及三个消息用例按 `_TEST_MESSAGE_WHERE` 写入的 `conversation_history` 行。
    任一集合非空即说明 fixture 漏删，本断言失败。

    文档/分块另加孤儿断言：本文件写入的文档与分块只挂在自建的 `test-user`
    知识库下，若 fixture 漏删某条删除语句，这些行会因知识库已删而成为孤儿，
    此时"挂在 `test-user` 知识库下"的计数恒为 0（子查询为空），只有孤儿断言能抓到。
    """
    async with session_factory() as s:
        kb_count = await s.scalar(
            text("SELECT count(*) FROM knowledge_base WHERE user_id = 'test-user'")
        )
        doc_count = await s.scalar(
            text(
                "SELECT count(*) FROM document WHERE kb_id IN"
                " (SELECT id FROM knowledge_base WHERE user_id = 'test-user')"
            )
        )
        doc_orphan_count = await s.scalar(
            text(
                "SELECT count(*) FROM document WHERE kb_id NOT IN"
                " (SELECT id FROM knowledge_base)"
            )
        )
        chunk_count = await s.scalar(
            text(
                "SELECT count(*) FROM chunks WHERE kb_id IN"
                " (SELECT id FROM knowledge_base WHERE user_id = 'test-user')"
            )
        )
        chunk_orphan_count = await s.scalar(
            text(
                "SELECT count(*) FROM chunks WHERE kb_id NOT IN"
                " (SELECT id FROM knowledge_base)"
            )
        )
        session_count = await s.scalar(
            text(
                "SELECT count(*) FROM sessions"
                " WHERE user_id IN ('test-user', 'u_agent')"
            )
        )
        message_count = await s.scalar(
            text(
                "SELECT count(*) FROM conversation_history WHERE " + _TEST_MESSAGE_WHERE
            )
        )
    assert kb_count == 0
    assert doc_count == 0
    assert doc_orphan_count == 0
    assert chunk_count == 0
    assert chunk_orphan_count == 0
    assert session_count == 0
    assert message_count == 0
