"""aiomysql 异步数据库操作测试。"""

import uuid

import pytest

from src.infra.db.mysql_db import MySQLDB


@pytest.mark.asyncio
async def test_create_and_get_kb():
    """测试创建和查询知识库的完整流程。"""
    db = MySQLDB()
    user_id = "test-user"
    name = f"test-kb-{uuid.uuid4().hex[:8]}"
    kb_id, is_new = await db.get_or_create_kb(user_id, name)
    assert is_new is True
    found_id = await db.get_kb_by_name(user_id, name)
    assert found_id == kb_id
    await db.close()


@pytest.mark.asyncio
async def test_document_crud():
    """测试文档的增删查操作。"""
    db = MySQLDB()
    # 先创建知识库，再添加文档以满足外键约束
    user_id = "test-user"
    kb_name = f"test-doc-kb-{uuid.uuid4().hex[:8]}"
    kb_id, _ = await db.get_or_create_kb(user_id, kb_name)
    doc_id = str(uuid.uuid4())
    await db.add_document(doc_id, kb_id, "test.pdf", "pdf", 100)
    docs = await db.get_documents(kb_id)
    doc_ids = [d["id"] for d in docs]
    assert doc_id in doc_ids
    await db.close()


@pytest.mark.asyncio
async def test_get_kb_name_by_id():
    """测试根据知识库 ID 查询名称。"""
    db = MySQLDB()
    user_id = "test-user"
    name = f"test-kb-name-{uuid.uuid4().hex[:8]}"
    kb_id, _ = await db.get_or_create_kb(user_id, name)

    result = await db.get_kb_name_by_id(kb_id)
    assert result == name

    # 不存在的 ID 返回 None
    result = await db.get_kb_name_by_id(str(uuid.uuid4()))
    assert result is None

    await db.close()


@pytest.mark.asyncio
async def test_get_doc_names():
    """测试根据文档 ID 列表查询文件名。"""
    db = MySQLDB()
    user_id = "test-user"
    kb_name = f"test-doc-names-{uuid.uuid4().hex[:8]}"
    kb_id, _ = await db.get_or_create_kb(user_id, kb_name)

    # 创建多个文档
    doc_id_1 = str(uuid.uuid4())
    doc_id_2 = str(uuid.uuid4())
    doc_id_3 = str(uuid.uuid4())
    await db.add_document(doc_id_1, kb_id, "report.pdf", "pdf", 100)
    await db.add_document(doc_id_2, kb_id, "summary.docx", "docx", 200)
    await db.add_document(doc_id_3, kb_id, "data.xlsx", "xlsx", 300)

    # 查询部分文档
    result = await db.get_doc_names([doc_id_1, doc_id_3])
    assert result == {doc_id_1: "report.pdf", doc_id_3: "data.xlsx"}

    # 空列表返回空字典
    result = await db.get_doc_names([])
    assert result == {}

    # 不存在的 ID 不包含在结果中
    result = await db.get_doc_names([str(uuid.uuid4())])
    assert result == {}

    await db.close()
