import re
from loguru import logger
from src.infra.chunking.strategies.base import BaseChunker
from src.infra.chunking.strategies.parent_child import ParentChildChunker
from src.config import CROSS_PAGE_TABLE_MERGE_THRESHOLD, MAX_TABLE_TOKENS


class TablePreservingChunker(BaseChunker):
    chunk_strategy = "table_preserving"
    TABLE_PATTERN = re.compile(r"(^\|.+\|[\s\S]*?^\|.+\|)", re.MULTILINE)

    def chunk(self, text: str, metadata: dict) -> list[dict]:
        segments, merge_count = self._split_by_table_boundary(text)
        parent_child = ParentChildChunker()
        result = []
        for seg in segments:
            is_table = bool(self.TABLE_PATTERN.search(seg))
            if is_table:
                result.append(
                    {
                        "content": self.inject_heading_prefix(
                            seg, metadata.get("heading_path", "")
                        ),
                        "metadata": {
                            **metadata,
                            "block_type": "table",
                            "tokens": self.count_tokens(seg),
                            "chunk_strategy": self.chunk_strategy,
                        },
                    }
                )
            else:
                text_chunks = parent_child.chunk(seg, metadata)
                for c in text_chunks:
                    c["metadata"]["chunk_strategy"] = self.chunk_strategy
                result.extend(text_chunks)
        table_segments = sum(1 for s in segments if self.TABLE_PATTERN.search(s))
        text_segments = len(segments) - table_segments
        logger.info(
            "[table_preserving] chunks={} (table={} text={}) "
            "segments={} tables={} texts={} merges={} tokens={}",
            len(result),
            sum(1 for c in result if c["metadata"].get("block_type") == "table"),
            sum(1 for c in result if c["metadata"].get("block_type") != "table"),
            len(segments),
            table_segments,
            text_segments,
            merge_count,
            sum(c["metadata"]["tokens"] for c in result),
        )
        return result

    @staticmethod
    def _same_table_structure(seg_a: str, seg_b: str) -> bool:
        """判断两个表格段是否结构相同（列数一致）"""

        def _col_count(seg: str) -> int:
            for line in seg.split("\n"):
                if line.strip().startswith("|") and not line.strip().startswith("|---"):
                    return line.count("|")
            return 0

        return _col_count(seg_a) == _col_count(seg_b) > 0

    @staticmethod
    def _split_by_table_boundary(text: str) -> tuple[list[str], int]:
        lines = text.split("\n")
        segments, current, in_table = [], [], False
        for line in lines:
            is_table_line = bool(re.match(r"^\|.*\|$", line.strip()))
            if is_table_line != in_table:
                if current:
                    segments.append("\n".join(current))
                    current = []
                in_table = is_table_line
            current.append(line)
        if current:
            segments.append("\n".join(current))

        # 合并跨页表格：列数相同 + 中间短文本（< N 字）→ 同一张表，合并
        # MAX_TABLE_TOKENS 是 token 数，*2 转字符数（中文 1 token ≈ 2 字符）
        MAX_TABLE_CHARS = MAX_TABLE_TOKENS * 2
        merged = []
        merge_count = 0
        i = 0
        while i < len(segments):
            if (
                i + 2 < len(segments)
                and TablePreservingChunker.TABLE_PATTERN.search(segments[i])
                and TablePreservingChunker.TABLE_PATTERN.search(segments[i + 2])
                and TablePreservingChunker._same_table_structure(
                    segments[i], segments[i + 2]
                )
                and len(segments[i + 1]) < CROSS_PAGE_TABLE_MERGE_THRESHOLD
                and len(segments[i]) + len(segments[i + 1]) + len(segments[i + 2])
                <= MAX_TABLE_CHARS
            ):
                merge_count += 1
                merged.append(
                    segments[i] + "\n" + segments[i + 1] + "\n" + segments[i + 2]
                )
                i += 3
                # 链式合并：继续检查下一个 TABLE 段
                while (
                    i + 1 < len(segments)
                    and TablePreservingChunker.TABLE_PATTERN.search(segments[i + 1])
                    and TablePreservingChunker._same_table_structure(
                        merged[-1], segments[i + 1]
                    )
                    and len(segments[i]) < CROSS_PAGE_TABLE_MERGE_THRESHOLD
                    and len(merged[-1]) + len(segments[i]) + len(segments[i + 1])
                    <= MAX_TABLE_CHARS
                ):
                    merged[-1] += "\n" + segments[i] + "\n" + segments[i + 1]
                    merge_count += 1
                    i += 2
            else:
                merged.append(segments[i])
                i += 1
        return merged, merge_count
