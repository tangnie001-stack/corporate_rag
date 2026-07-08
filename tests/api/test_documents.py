"""文档上传与列表接口的集成测试。

测试范围:
  - POST /api/kbs/documents/list: 返回文档列表
  - POST /api/kbs/documents/upload: 上传 PDF 文件并返回 202 Accepted
"""

from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from src.main import app

client = TestClient(app)


def _setup_auth():
    """为请求设置认证 cookie，绕过中间件的 token 验证。"""
    client.cookies.set("token", "test-token")
    p = patch("src.middleware.auth.UserAuth.get_user_id_from_token_async",
              new_callable=AsyncMock, return_value="test-user-id")
    p.start()
    return p


@patch("src.api.routes.documents._get_service")
def test_get_documents(mock_get_service):
    """POST /api/kbs/documents/list 返回文档列表。"""
    auth_patcher = _setup_auth()
    try:
        mock_svc = mock_get_service.return_value
        mock_svc.get_documents = AsyncMock(return_value=[
            {"id": "doc-1", "filename": "report.pdf", "status": "ready"},
        ])

        response = client.post("/api/kbs/documents/list", json={"kb_id": "kb-1"})

        assert response.status_code == 200
        data = response.json()["data"]
        assert len(data) == 1
        assert data[0]["filename"] == "report.pdf"
    finally:
        auth_patcher.stop()
        client.cookies.clear()


@patch("src.api.routes.documents.FileStore")
@patch("src.api.routes.documents._get_service")
def test_upload_document(mock_get_service, mock_file_store_cls):
    """POST /api/kbs/documents/upload 返回 202 Accepted。"""
    auth_patcher = _setup_auth()
    try:
        mock_svc = mock_get_service.return_value
        # Set up svc.db mock — route calls db.get_documents (dedup) and db.add_document
        mock_svc.db = MagicMock()
        mock_svc.db.get_documents = AsyncMock(return_value=[])
        mock_svc.db.add_document = AsyncMock(return_value="test-doc-uuid")

        # FileStore mock: build_path (static) and fs.upload (instance method)
        mock_file_store_cls.build_path.return_value = "test/path.pdf"
        mock_fs = MagicMock()
        mock_fs.upload.return_value = True
        mock_file_store_cls.return_value = mock_fs

        response = client.post(
            "/api/kbs/documents/upload",
            data={"kb_id": "kb-1"},
            files={"file": ("test.pdf", b"%PDF-1.4 test content", "application/pdf")},
        )

        assert response.status_code == 202
    finally:
        auth_patcher.stop()
        client.cookies.clear()
