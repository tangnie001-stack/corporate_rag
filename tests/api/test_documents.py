"""文档 API 端点测试 — list / upload / status / chunks / delete。"""

from unittest.mock import AsyncMock, MagicMock

from src.services.document_service import _merge_tiny_chunks
from src.infra.chunking.validator import ChunkData
from src.infra.db.models.document import DocModel as DocEntity
from src.infra.db.vector_store.types import ChunkResult, ChunkQueryResult
from tests.api.mock_data import make_doc


def test_get_documents(mock_app_service, auth_client):
    """POST /api/kbs/documents/list 返回文档列表。"""
    mock_svc = mock_app_service
    mock_svc.get_documents = AsyncMock(return_value=[make_doc("doc-1", "report.pdf")])

    response = auth_client.post("/api/kbs/documents/list", json={"kb_id": "kb-1"})

    assert response.status_code == 200
    data = response.json()["data"]
    assert len(data) == 1
    assert data[0]["filename"] == "report.pdf"


def test_upload_document(mock_app_service, auth_client):
    """POST /api/kbs/documents/upload 返回 202 Accepted。"""
    mock_svc = mock_app_service
    mock_result = {
        "doc_id": "test_doc_id",
        "status": "processing",
        "filename": "test.pdf",
    }
    mock_svc.document.store_and_process = AsyncMock(return_value=mock_result)

    response = auth_client.post(
        "/api/kbs/documents/upload",
        data={"kb_id": "kb-1"},
        files={"file": ("test.pdf", b"%PDF-1.4 test content", "application/pdf")},
    )

    assert response.status_code == 202
    data = response.json()["data"]
    assert data["doc_id"] == "test_doc_id"
    assert data["status"] == "processing"


def test_document_status_processing(mock_app_service, auth_client):
    """POST /api/kbs/documents/status 返回文档处理状态。"""
    mock_svc = mock_app_service
    mock_svc._doc_repo = MagicMock()
    mock_svc._doc_repo.get_documents = AsyncMock(
        return_value=[
            DocEntity(
                id="doc-1",
                kb_id="kb-1",
                filename="test.pdf",
                status="processing",
                processing_progress=30,
                processing_state="extracting",
                processing_message="正在解析...",
            ),
        ]
    )

    response = auth_client.post(
        "/api/kbs/documents/status", json={"kb_id": "kb-1", "doc_id": "doc-1"}
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "processing"
    assert data["progress"] == 30


def test_document_status_not_found(mock_app_service, auth_client):
    """POST /api/kbs/documents/status 文档不存在返回 status=not_found。"""
    mock_svc = mock_app_service
    mock_svc._doc_repo = MagicMock()
    mock_svc._doc_repo.get_documents = AsyncMock(return_value=[])

    response = auth_client.post(
        "/api/kbs/documents/status", json={"kb_id": "kb-1", "doc_id": "missing"}
    )

    assert response.status_code == 200
    assert response.json()["data"]["status"] == "not_found"


def test_document_chunks_empty(mock_app_service, auth_client):
    """POST /api/kbs/documents/chunks 空文档返回空列表。"""
    mock_svc = mock_app_service
    mock_svc.get_chunks_paginated = AsyncMock(
        return_value=ChunkQueryResult(items=[], total=0, page=1, page_size=10)
    )

    response = auth_client.post(
        "/api/kbs/documents/chunks",
        json={
            "kb_id": "kb-1",
            "doc_id": "doc-1",
            "page": 1,
            "page_size": 10,
        },
    )

    assert response.status_code == 200
    assert response.json()["data"]["total"] == 0


def test_document_chunks_with_parent_dedup(mock_app_service, auth_client):
    """POST /api/kbs/documents/chunks parent_content 去重逻辑验证。"""
    mock_svc = mock_app_service
    mock_svc.get_chunks_paginated = AsyncMock(
        return_value=ChunkQueryResult(
            items=[
                ChunkResult(
                    id="c1",
                    content="2024年营收100亿",
                    metadata={
                        "page": 1,
                        "tokens": 10,
                        "block_type": "text",
                        "parent_content": "营收概述",
                    },
                ),
                ChunkResult(
                    id="c2",
                    content="2024年净利润20亿",
                    metadata={
                        "page": 1,
                        "tokens": 10,
                        "block_type": "text",
                        "parent_content": "营收概述",
                    },
                ),
                ChunkResult(
                    id="c3",
                    content="毛利率45%",
                    metadata={
                        "page": 2,
                        "tokens": 5,
                        "block_type": "text",
                        "parent_content": "财务指标",
                    },
                ),
            ],
            total=3,
            page=1,
            page_size=10,
        )
    )

    response = auth_client.post(
        "/api/kbs/documents/chunks",
        json={
            "kb_id": "kb-1",
            "doc_id": "doc-1",
            "page": 1,
            "page_size": 10,
        },
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["total"] == 3
    assert data["page"] == 1
    assert len(data["items"]) == 3
    assert data["items"][0]["parent_key"] == "p0"
    assert data["items"][1]["parent_key"] == "p0"
    assert data["items"][2]["parent_key"] == "p1"
    assert data["items"][0].get("parent_content") is None
    assert data["parent_map"]["p0"] == "营收概述"
    assert data["parent_map"]["p1"] == "财务指标"
    assert len(data["parent_map"]) == 2


def test_delete_document_success(mock_app_service, auth_client):
    """POST /api/kbs/documents/delete 成功返回 success=True。"""
    mock_svc = mock_app_service
    mock_svc.document._doc_repo = MagicMock()
    mock_svc.document._doc_repo.soft_delete_document = AsyncMock(return_value=True)
    mock_svc.vector_store = MagicMock()

    response = auth_client.post(
        "/api/kbs/documents/delete", json={"kb_id": "kb-1", "doc_id": "doc-1"}
    )

    assert response.status_code == 200
    assert response.json()["data"]["success"] is True


def test_delete_document_not_found(mock_app_service, auth_client):
    """POST /api/kbs/documents/delete 文档不存在返回 success=False。"""
    mock_svc = mock_app_service
    mock_svc.document._doc_repo = MagicMock()
    mock_svc.document._doc_repo.soft_delete_document = AsyncMock(return_value=False)

    response = auth_client.post(
        "/api/kbs/documents/delete", json={"kb_id": "kb-1", "doc_id": "missing"}
    )

    assert response.status_code == 200
    assert response.json()["data"]["success"] is False


# Tests for _merge_tiny_chunks


def test_merge_tiny_normal():
    """Normal merge: text chunk (256 tokens) + tiny (44 tokens) -> 1 chunk."""
    chunks = [
        ChunkData(
            content="A" * 512,
            metadata={"block_type": "text"},
            tokens=256,
        ),
        ChunkData(
            content="tiny tail",
            metadata={"block_type": "text"},
            tokens=44,
        ),
    ]
    result = _merge_tiny_chunks(chunks, strategy="parent_child")
    assert len(result) == 1
    assert result[0].tokens == 261  # (512 + 9 + 1("\n")) // 2 = 261


def test_merge_tiny_first_chunk():
    """First chunk is tiny: stays standalone."""
    chunks = [
        ChunkData(content="tiny first", metadata={"block_type": "text"}, tokens=5),
        ChunkData(content="B" * 600, metadata={"block_type": "text"}, tokens=300),
    ]
    result = _merge_tiny_chunks(chunks)
    assert len(result) == 2  # not merged


def test_merge_tiny_consecutive():
    """Multiple consecutive tiny chunks: all merged into predecessor."""
    chunks = [
        ChunkData(content="C" * 500, metadata={"block_type": "text"}, tokens=250),
        ChunkData(content="tiny1", metadata={"block_type": "text"}, tokens=10),
        ChunkData(content="tiny2", metadata={"block_type": "text"}, tokens=8),
        ChunkData(content="D" * 600, metadata={"block_type": "text"}, tokens=300),
    ]
    result = _merge_tiny_chunks(chunks, strategy="parent_child")
    assert len(result) == 2  # both tinies merge into chunk1, chunk4 stays
    assert "tiny1" in result[0].content
    assert "tiny2" in result[0].content


def test_merge_tiny_qa_skip():
    """QA strategy: passes through unchanged."""
    chunks = [
        ChunkData(
            content="问：你好？答：我很好。",
            metadata={"block_type": "text"},
            tokens=12,
        ),
    ]
    result = _merge_tiny_chunks(chunks, strategy="qa")
    assert len(result) == 1  # no merge


def test_merge_tiny_empty():
    """Empty list: returns empty."""
    result = _merge_tiny_chunks([])
    assert result == []
