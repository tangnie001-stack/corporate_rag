"""API 测试统一 mock 数据工厂。

所有 mock 数据集中管理，各测试文件通过 import 引用。
工厂函数支持 **kw 参数，按需覆盖默认字段。
"""

from datetime import datetime
from decimal import Decimal


def make_kb(id="kb-1", name="年报知识库", doc_count=0):
    """创建模拟知识库数据。"""
    return {"id": id, "name": name, "doc_count": doc_count}


def make_doc(id="doc-1", filename="test.pdf", status="ready", **kw):
    """创建模拟文档数据。"""
    base = {
        "id": id,
        "filename": filename,
        "file_type": "pdf",
        "file_size": 1024,
        "status": status,
        "chunk_count": 10,
        "created_at": datetime(2026, 7, 1),
    }
    base.update(kw)
    return base


def make_chunk(id="c1", content="test", page=1, parent_content=None):
    """创建模拟分块数据。"""
    chunk = {
        "id": id,
        "content": content,
        "metadata": {"page": page, "tokens": len(content), "block_type": "text"},
    }
    if parent_content:
        chunk["metadata"]["parent_content"] = parent_content
    return chunk


def make_session(id="s1", title="财报问答", **kw):
    """创建模拟会话数据。"""
    base = {
        "id": id,
        "title": title,
        "kb_id": "kb-1",
        "kb_name": "年报",
        "message_count": 3,
        "created_at": datetime(2026, 1, 1),
        "updated_at": datetime(2026, 1, 2),
    }
    base.update(kw)
    return base


def make_message(role="user", content="hello", **kw):
    """创建模拟消息数据。"""
    base = {
        "role": role,
        "content": content,
        "sources": None,
        "created_at": datetime(2026, 1, 1),
    }
    base.update(kw)
    return base


def make_eval_report(overall_score=0.84, passed=True, **kw):
    """创建模拟评估报告数据。"""
    base = {
        "eval_date": datetime(2026, 6, 15),
        "faithfulness": Decimal("0.85"),
        "answer_relevancy": Decimal("0.90"),
        "context_precision": Decimal("0.78"),
        "context_recall": Decimal("0.82"),
        "overall_score": Decimal(str(overall_score)),
        "passed": passed,
        "qa_count": 20,
        "run_type": "full",
    }
    base.update(kw)
    return base


def make_user(id="u1", account="test", password="hashed_pwd"):
    """创建模拟用户数据。"""
    return {"id": id, "account": account, "password": password}
