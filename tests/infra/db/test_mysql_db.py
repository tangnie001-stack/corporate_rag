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
