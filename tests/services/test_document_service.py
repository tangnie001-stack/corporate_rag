"""DocumentService 实体注入测试。

覆盖 Task 7：process_document 接入实体抽取，
实体注入每个 chunk.metadata + meta_info["entities"] 聚合。
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.chunking.validator import ChunkData
from src.services.document_service import DocumentService


def _make_service() -> tuple[DocumentService, AsyncMock]:
    """构造最小 DocumentService（repo 用 AsyncMock，不触数据库）。"""
    doc_repo = AsyncMock()
    svc = DocumentService(doc_repo, vector_store=MagicMock(), router=MagicMock())
    return svc, doc_repo


class TestEntityInjection:
    @pytest.mark.asyncio
    async def test_entities_injected_into_each_chunk(self):
        """实体注入每个 chunk.metadata，并聚合到 meta_info。"""
        svc, doc_repo = _make_service()
        chunks = [
            ChunkData(content="内容A", metadata={"page": 1}),
            ChunkData(content="内容B", metadata={"page": 2}),
        ]
        entities = {
            "company": "东软集团",
            "report_period": "2025年第一季度",
            "sec_code": "600718",
        }
        with (
            patch(
                "src.infra.search.document_entity_extractor."
                "DocumentEntityExtractor.extract",
                return_value=entities,
            ),
            patch("src.services.document_service.get_classify_llm") as mock_llm,
        ):
            await svc._inject_document_entities(
                doc_id="doc-1",
                filename="neusoft_2025_q1.pdf",
                file_type="pdf",
                heading_tree=[(1, "主要财务数据")],
                full_text="内容A\n\n内容B",
                chunks=chunks,
            )

        mock_llm.assert_called_once()
        for c in chunks:
            assert c.metadata["company"] == "东软集团"
            assert c.metadata["report_period"] == "2025年第一季度"
            assert c.metadata["sec_code"] == "600718"
        doc_repo.update_document_meta_info.assert_awaited_once_with(
            "doc-1", {"entities": entities}
        )

    @pytest.mark.asyncio
    async def test_empty_entities_skip_injection(self):
        """抽取结果为空时不注入、不写 meta_info。"""
        svc, doc_repo = _make_service()
        chunks = [ChunkData(content="内容A", metadata={"page": 1})]
        with (
            patch(
                "src.infra.search.document_entity_extractor."
                "DocumentEntityExtractor.extract",
                return_value={},
            ),
            patch("src.services.document_service.get_classify_llm"),
        ):
            await svc._inject_document_entities(
                doc_id="doc-1",
                filename="report.pdf",
                file_type="txt",
                heading_tree=[],
                full_text="内容A",
                chunks=chunks,
            )

        assert chunks[0].metadata == {"page": 1}
        doc_repo.update_document_meta_info.assert_not_awaited()
