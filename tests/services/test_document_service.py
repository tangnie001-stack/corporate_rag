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


class TestEntityInjectionFailure:
    @pytest.mark.asyncio
    async def test_entity_failure_does_not_interrupt_ingest(self):
        """实体抽取异常时降级，入库流程继续（文档仍标记 ready）。"""
        svc, doc_repo = _make_service()
        # parse_result：只需流水线读到的字段
        parse_result = MagicMock()
        parse_result.file_type = "pdf"
        parse_result.total_pages = 1
        parse_result.total_chars = 0
        parse_result.is_scanned = False
        parse_result.encoding = "utf-8"
        parse_result.heading_tree = []
        parse_result.chunks = [ChunkData(content="内容A", metadata={"page": 1})]
        svc.router.parse.return_value = parse_result

        tmp = MagicMock()
        tmp.name = "/tmp/fake.pdf"
        tmp.write = MagicMock()
        tmp.close = MagicMock()

        chunks = [ChunkData(content="内容A", metadata={"page": 1})]

        with (
            patch("src.services.document_service.FileStore") as mock_store,
            patch("src.services.document_service.tempfile") as mock_tempfile,
            patch("src.services.document_service.os.unlink"),
            patch.object(svc, "_enrich_chunk_metadata"),
            patch(
                "src.services.document_service.ChunkRouter"
            ) as mock_router,
            patch(
                "src.services.document_service.build_heading_segments",
                return_value=[],
            ),
            patch(
                "src.services.document_service.validate_chunks"
            ) as mock_validate,
            patch(
                "src.services.document_service.CHUNK_EVAL_ENABLED", False
            ),
            patch.object(
                svc,
                "_inject_document_entities",
                side_effect=RuntimeError("LLM 构造失败"),
            ),
        ):
            mock_store().download.return_value = b"fake contents"
            mock_tempfile.NamedTemporaryFile.return_value = tmp
            mock_router.detect_strategy.return_value = "qa"
            mock_router.get_chunker.return_value = MagicMock(
                chunk=MagicMock(return_value=chunks)
            )
            mock_validate.return_value = MagicMock(
                tiny_chunks=[], garbled_chunks=[]
            )
            svc.vector_store.add_chunks.return_value = 1

            await svc.process_document(
                "kb-1", "doc-1", "minio/fake.pdf", "fake.pdf", ".pdf"
            )

        # 入库流程不中断：add_chunks 被调用、文档标记 ready
        svc.vector_store.add_chunks.assert_called_once()
        ready_calls = [
            c
            for c in doc_repo.update_document_status.call_args_list
            if len(c.args) > 1 and c.args[1] == "ready"
        ]
        assert ready_calls
