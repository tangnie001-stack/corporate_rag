"""MySQL 数据层集成测试 — 使用 KbRepo / DocumentRepo。"""

import uuid

import pytest

from src.infra.db.engine import session_factory
from src.infra.db.mysql_db import KbRepo, DocumentRepo
from src.infra.db.models.document import DocModel as DocEntity


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
async def test_create_session_idempotent():
    """同一 session_id 重复创建应幂等跳过，不抛主键冲突异常。"""
    from src.infra.db.mysql_db import ChatRepo
    from src.infra.db.models.chat import SessionModel

    chat_repo = ChatRepo(session_factory)
    session_id = f"session-{uuid.uuid4().hex[:8]}"
    base = dict(user_id="test-user", title="测试会话", kb_id="")

    await chat_repo.create_session(SessionModel(id=session_id, **base))
    # 第二次创建同 id：应静默跳过而非抛 IntegrityError
    await chat_repo.create_session(SessionModel(id=session_id, **base))

    found = await chat_repo.get_session_by_id(session_id)
    assert found is not None
    assert found.title == "测试会话"
