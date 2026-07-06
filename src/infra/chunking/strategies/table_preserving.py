import re
from src.infra.chunking.strategies.base import BaseChunker
from src.infra.chunking.strategies.parent_child import ParentChildChunker


class TablePreservingChunker(BaseChunker):
    chunk_strategy = "table_preserving"
    TABLE_PATTERN = re.compile(r'(^\|.+\|[\s\S]*?^\|.+\|)', re.MULTILINE)

    def chunk(self, text: str, metadata: dict) -> list[dict]:
        segments = self._split_by_table_boundary(text)
        parent_child = ParentChildChunker()
        result = []
        for seg in segments:
            is_table = bool(self.TABLE_PATTERN.search(seg))
            if is_table:
                result.append({
                    "content": self.inject_heading_prefix(seg, metadata.get("heading_path", "")),
                    "metadata": {
                        **metadata,
                        "parent_content": seg,
                        "tokens": self.count_tokens(seg),
                        "chunk_strategy": self.chunk_strategy,
                    },
                })
            else:
                text_chunks = parent_child.chunk(seg, metadata)
                for c in text_chunks:
                    c["metadata"]["chunk_strategy"] = self.chunk_strategy
                result.extend(text_chunks)
        return result

    @staticmethod
    def _split_by_table_boundary(text: str) -> list[str]:
        lines = text.split("\n")
        segments, current, in_table = [], [], False
        for line in lines:
            is_table_line = bool(re.match(r'^\|.*\|$', line.strip()))
            if is_table_line != in_table:
                if current:
                    segments.append("\n".join(current))
                    current = []
                in_table = is_table_line
            current.append(line)
        if current:
            segments.append("\n".join(current))
        return segments
