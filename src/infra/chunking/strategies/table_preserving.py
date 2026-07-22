import re
from loguru import logger
from src.infra.chunking.strategies.base import BaseChunker
from src.infra.chunking.strategies.parent_child import ParentChildChunker
from src.config import (
    CROSS_PAGE_TABLE_MERGE_THRESHOLD,
    ORPHAN_THRESHOLD_CHARS,
    TABLE_ROW_CHUNK_CHARS,
)


class TablePreservingChunker(BaseChunker):
    chunk_strategy = "table_preserving"
    TABLE_PATTERN = re.compile(r"(^\|.+\|[\s\S]*?^\|.+\|)", re.MULTILINE)

    def chunk(self, text: str, metadata: dict) -> list[dict]:
        segments, merge_count = self._split_by_table_boundary(text)
        segments = self._merge_orphan_texts(segments)  # 阶段 2
        segments = self._split_large_tables(segments)  # 阶段 3
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
    def _split_large_tables(segments: list[str]) -> list[str]:
        """将超过 TABLE_ROW_CHUNK_CHARS 的大表格按行切分，每段复制表头.

        表格以 Markdown pipe 格式：
          | 项目 | 2025年 | 2024年 |
          |---|---|---|
          | 收入 | 100 | 90 |
          ...

        切分策略：
          - 提取表头行（第一行 |...|）和分隔行（|---|）
          - 数据行贪心分组（每组 ~TABLE_ROW_CHUNK_CHARS 字符）
          - 每组前复制表头+分隔行
          - 无分隔行时：首行当表头，其余当数据行
        """
        result = []
        for seg in segments:
            is_table = bool(TablePreservingChunker.TABLE_PATTERN.search(seg))
            if not is_table or len(seg) <= TABLE_ROW_CHUNK_CHARS:
                result.append(seg)
                continue

            lines = seg.split("\n")
            # 定位表头行和分隔行
            header_idx = -1
            sep_idx = -1
            for i, line in enumerate(lines):
                stripped = line.strip()
                if stripped.startswith("|") and not stripped.startswith("|---"):
                    if header_idx == -1:
                        header_idx = i
                if stripped.startswith("|---"):
                    if sep_idx == -1:
                        sep_idx = i

            if header_idx == -1:
                result.append(seg)
                continue

            header = lines[header_idx]
            separator = lines[sep_idx] if sep_idx >= 0 else ""

            # 数据行 = 不以 |---| 开头且不是表头的 |...| 行
            data_rows = [
                line
                for line in lines
                if line.strip().startswith("|")
                and not line.strip().startswith("|---")
                and line != header
            ]

            if not data_rows:
                result.append(seg)
                continue

            # 贪心分组：累计到 TABLE_ROW_CHUNK_CHARS 就切
            current_group: list[str] = []
            current_chars = 0
            header_sep_chars = len(header) + len(separator) + 2  # 2 换行

            def _flush():
                if current_group:
                    result.append(
                        header
                        + ("\n" + separator if separator else "")
                        + "\n"
                        + "\n".join(current_group)
                    )

            for row in data_rows:
                row_chars = len(row) + 1
                limit = TABLE_ROW_CHUNK_CHARS - header_sep_chars
                if current_chars + row_chars > limit and current_group:
                    _flush()
                    current_group = []
                    current_chars = 0
                current_group.append(row)
                current_chars += row_chars
            _flush()

            logger.debug(
                "[table_preserving] split large table: {} chars -> {} sub-tables",
                len(seg),
                (len(seg) // TABLE_ROW_CHUNK_CHARS) + 1,
            )

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
