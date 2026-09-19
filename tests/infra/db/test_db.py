"""关系型数据层集成测试 — 使用 KbRepo / DocumentRepo / ChatRepo（PostgreSQL）。"""

import uuid

import pytest

from src.infra.db.engine import session_factory
from src.infra.db.models.document import DocModel as DocEntity
from src.infra.db.mysql_db import DocumentRepo, KbRepo


@pytest.fixture
async def repos():
    """提供 KbRepo 和 DocumentRepo 实例。"""
    kb_repo = KbRepo(session_factory)
    doc_repo = DocumentRepo(session_factory)
    yield kb_repo, doc_repo


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
    from src.infra.db.mysql_db import ChatRepo

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
    from src.infra.db.mysql_db import ChatRepo

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
    from src.infra.db.mysql_db import ChatRepo

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

    改造前这里有一处 asyncio.sleep(1.1) —— 那是为绕过 MySQL DATETIME 的秒级
    精度。PostgreSQL 的时间戳是微秒精度，且两条消息各自独立事务提交，
    不需要再等待；断言用 <= 也已宽容到能接受相同时间戳。
    """
    from src.infra.db.models.chat import MessageModel
    from src.infra.db.mysql_db import ChatRepo

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


@pytest.mark.asyncio
async def test_create_session_with_agent_persists():
    """create_session 落 agent；未传时落空串（存量语义不变）。"""
    from src.infra.db.models.chat import SessionModel
    from src.infra.db.mysql_db import ChatRepo

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
