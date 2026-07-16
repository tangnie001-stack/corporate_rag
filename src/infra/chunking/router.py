import re
from src.infra.chunking.strategies.base import BaseChunker
from src.infra.chunking.strategies.parent_child import ParentChildChunker
from src.infra.chunking.strategies.qa import QAChunker
from src.infra.chunking.strategies.table_preserving import TablePreservingChunker


class ChunkRouter:
    QA_THRESHOLD = 0.20

    @staticmethod
    def detect_strategy(full_text: str, parsed_chunks: list) -> str:
        for chunk in parsed_chunks:
            if chunk.metadata.get("block_type") == "table":
                return "table_preserving"
        if ChunkRouter._is_qa_document(full_text):
            return "qa"
        return "parent_child"

    @staticmethod
    def get_chunker(strategy: str) -> BaseChunker:
        return {
            "qa": QAChunker,
            "table_preserving": TablePreservingChunker,
            "parent_child": ParentChildChunker,
        }.get(strategy, ParentChildChunker)()

    @staticmethod
    def _is_qa_document(text: str) -> bool:
        if not text.strip():
            return False
        sentences = [s.strip() for s in re.split(r"[。！\n]", text) if s.strip()]
        if not sentences:
            return False
        q_count = sum(1 for s in sentences if s.rstrip().endswith(("？", "?")))
        return (q_count / len(sentences)) > ChunkRouter.QA_THRESHOLD
