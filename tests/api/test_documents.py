"""文档 API 端点测试 — list / upload / status / chunks / delete。"""

from unittest.mock import AsyncMock, MagicMock, patch

from tests.api.mock_data import make_doc, make_chunk


@patch("src.api.documents._get_service")
def test_get_documents(mock_get_service, auth_client):
    """POST /api/kbs/documents/list 返回文档列表。"""
    mock_svc = mock_get_service.return_value
    mock_svc.get_documents = AsyncMock(return_value=[make_doc("doc-1", "report.pdf")])

    response = auth_client.post("/api/kbs/documents/list", json={"kb_id": "kb-1"})

    assert response.status_code == 200
    data = response.json()["data"]
    assert len(data) == 1
    assert data[0]["filename"] == "report.pdf"


@patch("src.api.documents.asyncio.create_task", new_callable=MagicMock)
@patch("src.api.documents.FileStore")
@patch("src.api.documents._get_service")
def test_upload_document(mock_get_service, mock_file_store_cls, mock_create_task, auth_client):
    """POST /api/kbs/documents/upload 返回 202 Accepted。"""
    mock_svc = mock_get_service.return_value
    mock_svc.db = MagicMock()
    mock_svc.db.get_documents = AsyncMock(return_value=[])
    mock_svc.db.add_document = AsyncMock(return_value="test-doc-uuid")

    mock_file_store_cls.build_path.return_value = "test/path.pdf"
    mock_fs = MagicMock()
    mock_fs.upload.return_value = True
    mock_file_store_cls.return_value = mock_fs

    response = auth_client.post(
        "/api/kbs/documents/upload",
        data={"kb_id": "kb-1"},
        files={"file": ("test.pdf", b"%PDF-1.4 test content", "application/pdf")},
    )

    assert response.status_code == 202


@patch("src.api.documents._get_service")
def test_document_status_processing(mock_get_service, auth_client):
    """POST /api/kbs/documents/status ��回文档处理状态。"""
    mock_svc = mock_get_service.return_value
    mock_svc.db.get_documents = AsyncMock(return_value=[
        make_doc("doc-1", status="processing",
                 processing_progress=30, processing_state="extracting",
                 processing_message="正在解析..."),
    ])

    response = auth_client.post(
        "/api/kbs/documents/status", json={"kb_id": "kb-1", "doc_id": "doc-1"}
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "processing"
    assert data["progress"] == 30


@patch("src.api.documents._get_service")
def test_document_status_not_found(mock_get_service, auth_client):
    """POST /api/kbs/documents/status 文档不存在返回 status=not_found。"""
    mock_svc = mock_get_service.return_value
    mock_svc.db.get_documents = AsyncMock(return_value=[])

    response = auth_client.post(
        "/api/kbs/documents/status", json={"kb_id": "kb-1", "doc_id": "missing"}
    )

    assert response.status_code == 200
    assert response.json()["data"]["status"] == "not_found"


@patch("src.api.documents._get_service")
def test_document_chunks_empty(mock_get_service, auth_client):
    """POST /api/kbs/documents/chunks 空文档返回空列表。"""
    mock_svc = mock_get_service.return_value
    mock_vs = MagicMock()
    mock_vs.get_chunks_paginated.return_value = {
        "items": [], "total": 0, "page": 1, "page_size": 10,
    }
    mock_svc.vector_store = mock_vs

    response = auth_client.post("/api/kbs/documents/chunks", json={
        "kb_id": "kb-1", "doc_id": "doc-1", "page": 1, "page_size": 10,
    })

    assert response.status_code == 200
    assert response.json()["data"]["total"] == 0


@patch("src.api.documents._get_service")
def test_document_chunks_with_parent_dedup(mock_get_service, auth_client):
    """POST /api/kbs/documents/chunks parent_content 去重逻辑验证。"""
    mock_svc = mock_get_service.return_value
    mock_vs = MagicMock()
    mock_vs.get_chunks_paginated.return_value = {
        "items": [
            make_chunk("c1", "2024年营收100亿", page=1, parent_content="营收概述"),
            make_chunk("c2", "2024年净利润20亿", page=1, parent_content="营收概述"),
            make_chunk("c3", "毛利率45%", page=2, parent_content="财务指标"),
        ],
        "total": 3, "page": 1, "page_size": 10,
    }
    mock_svc.vector_store = mock_vs

    response = auth_client.post("/api/kbs/documents/chunks", json={
        "kb_id": "kb-1", "doc_id": "doc-1", "page": 1, "page_size": 10,
    })

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


@patch("src.api.documents._get_service")
def test_delete_document_success(mock_get_service, auth_client):
    """POST /api/kbs/documents/delete 成功返回 success=True。"""
    mock_svc = mock_get_service.return_value
    mock_svc.db.soft_delete_document = AsyncMock(return_value=True)
    mock_svc.vector_store = MagicMock()

    response = auth_client.post(
        "/api/kbs/documents/delete", json={"kb_id": "kb-1", "doc_id": "doc-1"}
    )

    assert response.status_code == 200
    assert response.json()["data"]["success"] is True


@patch("src.api.documents._get_service")
def test_delete_document_not_found(mock_get_service, auth_client):
    """POST /api/kbs/documents/delete 文档不存在返回 success=False。"""
    mock_svc = mock_get_service.return_value
    mock_svc.db.soft_delete_document = AsyncMock(return_value=False)

    response = auth_client.post(
        "/api/kbs/documents/delete", json={"kb_id": "kb-1", "doc_id": "missing"}
    )

    assert response.status_code == 200
    assert response.json()["data"]["success"] is False
