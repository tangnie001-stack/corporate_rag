"""Gradio UI 基本冒烟测试 — 验证事件处理函数。"""

from unittest.mock import MagicMock, patch


class TestUIHelpers:
    """测试 UI 辅助函数（不依赖 Gradio 渲染）。"""

    @patch("src.app.get_service")
    def test_refresh_kb_dropdown(self, mock_get_svc):
        """下拉框包含"所有知识库"选项，其后为各知识库。"""
        from src.app import refresh_kb_dropdown

        svc = MagicMock()
        svc.list_knowledge_bases.return_value = [("id1", "KB1"), ("id2", "KB2")]
        mock_get_svc.return_value = svc
        choices = refresh_kb_dropdown()
        assert choices[0] == ("所有知识库", "")
        assert ("KB1", "id1") in choices
        assert ("KB2", "id2") in choices
        assert len(choices) == 3

    @patch("src.app.get_service")
    def test_handle_create_kb_success(self, mock_get_svc):
        """创建知识库成功应返回成功消息。"""
        from src.app import handle_create_kb

        svc = MagicMock()
        svc.create_knowledge_base.return_value = ("new_id", True)
        mock_get_svc.return_value = svc
        msg, choices = handle_create_kb("测试库")
        assert "创建成功" in msg

    @patch("src.app.get_service")
    def test_handle_create_kb_existing(self, mock_get_svc):
        """创建已存在的知识库应提示已存在。"""
        from src.app import handle_create_kb

        svc = MagicMock()
        svc.create_knowledge_base.return_value = ("existing_id", False)
        mock_get_svc.return_value = svc
        msg, choices = handle_create_kb("已存在")
        assert "已存在" in msg

    @patch("src.app.get_service")
    def test_handle_create_kb_empty_name(self, mock_get_svc):
        """空名称应返回提示。"""
        from src.app import handle_create_kb

        msg, choices = handle_create_kb("")
        assert "请输入" in msg

    @patch("src.app.get_service")
    def test_handle_delete_kb(self, mock_get_svc):
        """删除知识库应调用 service.delete_knowledge_base。"""
        from src.app import handle_delete_kb

        svc = MagicMock()
        svc.delete_knowledge_base.return_value = (True, "已删除")
        mock_get_svc.return_value = svc
        msg, dropdown, docs = handle_delete_kb("test-uuid")
        assert "已删除" in msg
        svc.delete_knowledge_base.assert_called_once_with("test-uuid")

    @patch("src.app.get_service")
    def test_handle_delete_kb_no_selection(self, mock_get_svc):
        """未选择知识库时删除应提示。"""
        from src.app import handle_delete_kb

        msg, dropdown, docs = handle_delete_kb("")
        assert "先选择" in msg

    @patch("src.app.get_service")
    def test_handle_select_kb_with_id(self, mock_get_svc):
        """选择知识库应返回文档列表。"""
        from src.app import handle_select_kb

        svc = MagicMock()
        svc.get_documents.return_value = [
            {
                "filename": "test.pdf",
                "file_type": "pdf",
                "file_size": 2048,
                "status": "ready",
                "chunk_count": 5,
            },
        ]
        mock_get_svc.return_value = svc
        docs, status = handle_select_kb("test-uuid")
        assert len(docs) > 0
        assert "已选择" in status

    @patch("src.app.get_service")
    def test_handle_select_kb_empty(self, mock_get_svc):
        """未选择知识库时应返回空状态。"""
        from src.app import handle_select_kb

        docs, status = handle_select_kb("")
        assert docs == []

    @patch("src.app.get_service")
    def test_handle_select_kb_all(self, mock_get_svc):
        """选择"所有知识库"时文档列表为空，状态消息提示用户。"""
        from src.app import handle_select_kb

        docs, status = handle_select_kb("")
        assert docs == []
        assert "选择" in status or "知识库" in status

    @patch("src.app.get_service")
    def test_format_docs_for_display(self, mock_get_svc):
        """文档格式化应生成正确表格行。"""
        from src.app import format_docs_for_display

        docs = [
            {
                "filename": "a.pdf",
                "file_type": "pdf",
                "file_size": 1024,
                "status": "ready",
                "chunk_count": 5,
            },
            {
                "filename": "b.txt",
                "file_type": "txt",
                "file_size": 512,
                "status": "pending",
                "chunk_count": 0,
            },
        ]
        rows = format_docs_for_display(docs)
        assert len(rows) == 2
        assert rows[0][0] == "a.pdf"
        assert "✅" in rows[0][3]

    def test_format_docs_empty(self):
        """空文档列表应返回空列表。"""
        from src.app import format_docs_for_display

        assert format_docs_for_display([]) == []

    @patch("src.app.get_service")
    def test_handle_upload_no_kb(self, mock_get_svc):
        """未选择知识库时上传应提示。"""
        from src.app import handle_upload

        msg, docs = handle_upload("", [MagicMock()])
        assert "先选择知识库" in msg
        assert docs == []

    @patch("src.app.get_service")
    def test_handle_upload_no_files(self, mock_get_svc):
        """未选择文件时上传应提示。"""
        from src.app import handle_upload

        msg, docs = handle_upload("KB", [])
        assert "选择要上传" in msg
