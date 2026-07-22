import re
from loguru import logger
from src.infra.chunking.strategies.base import BaseChunker
from src.infra.chunking.strategies.parent_child import ParentChildChunker
from src.config import CROSS_PAGE_TABLE_MERGE_THRESHOLD, ORPHAN_THRESHOLD_CHARS


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
    def _merge_orphan_texts(segments: list[str]) -> list[str]:
        """将小于 ORPHAN_THRESHOLD_CHARS 的孤立短文本合并到相邻 TABLE segment.

        扫描 text segment，< ORPHAN_THRESHOLD_CHARS 且与 TABLE 相邻时：
          - 优先向后合并（粘到后一个表格开头）
          - 其次向前合并（粘到前一个表格末尾）
        迭代扫描直到没有新的合并。
        """
        result = list(segments)
        changed = True
        while changed:
            changed = False
            i = 0
            while i < len(result):
                is_table = bool(TablePreservingChunker.TABLE_PATTERN.search(result[i]))
                is_short = not is_table and len(result[i]) < ORPHAN_THRESHOLD_CHARS

                if is_short:
                    # 向后合并：粘到后一个 TABLE 开头
                    if i + 1 < len(
                        result
                    ) and TablePreservingChunker.TABLE_PATTERN.search(result[i + 1]):
                        result[i + 1] = result[i] + "\n" + result[i + 1]
                        result.pop(i)
                        changed = True
                        continue

                    # 向前合并：粘到前一个 TABLE 末尾
                    if i > 0 and TablePreservingChunker.TABLE_PATTERN.search(
                        result[i - 1]
                    ):
                        result[i - 1] = result[i - 1] + "\n" + result[i]
                        result.pop(i)
                        changed = True
                        continue
                i += 1
        return result

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
                # removed size limit — merge regardless of combined size
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
                    # removed size limit — merge regardless of combined size
                ):
                    merged[-1] += "\n" + segments[i] + "\n" + segments[i + 1]
                    merge_count += 1
                    i += 2
            else:
                merged.append(segments[i])
                i += 1
        return merged, merge_count
