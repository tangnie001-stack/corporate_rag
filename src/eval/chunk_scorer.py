"""Chunk quality scorer — 轻量级分块质量评估指标。

本模块提供分块质量评分函数：
  1. 结构完整性（structure integrity）：表格/标题/条款在跨分块时的连续性
  2. 语义断裂率（SBR）：相邻分块之间的余弦相似度
  3. 粒度变异系数（Granularity CV）：分块 token 数的变异系数

所有指标归一化到 0-1 区间，1.0 表示最佳质量。
"""

import re
import statistics

import numpy as np

from src.models import get_embeddings

# 标题检测模式（面向金融文档）
HEADING_PATTERNS = [
    re.compile(r"^[一二三四五六七八九十]+、"),  # 一、二、三、
    re.compile(r"^（[一二三四五六七八九十]+）"),  # （一）（二）（三）
    re.compile(r"^\d+[\.、]"),  # 1. 2. 3、
    re.compile(r"^第[一二三四五六七八九十]+条"),  # 第一条、第二条
]

# 条款/列表项模式
CLAUSE_PATTERNS = [
    re.compile(r"^\d+[\.、]"),
    re.compile(r"^[（(]\d+[）)]"),  # (1) （1）
    re.compile(r"^[•·\-]\s"),  # bullet points
    re.compile(r"^[①②③④⑤⑥⑦⑧⑨⑩]"),  # circled numbers
]

# 表格行模式：以 | 开头和结尾
TABLE_LINE = re.compile(r"^\|.+\|$")


def _detect_headings(lines: list[str]) -> list[int]:
    """检测文本行中匹配标题模式的行号。

    Args:
        lines: 文本行列表

    Returns:
        匹配标题模式的行号列表
    """
    indices = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        for pat in HEADING_PATTERNS:
            if pat.match(stripped):
                indices.append(i)
                break
    return indices


def _detect_clauses(lines: list[str]) -> list[int]:
    """检测文本行中匹配条款/列表模式的行号。

    Args:
        lines: 文本行列表

    Returns:
        匹配条款模式的行号列表
    """
    indices = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        for pat in CLAUSE_PATTERNS:
            if pat.match(stripped):
                indices.append(i)
                break
    return indices


def _detect_tables(chunks: list[dict]) -> list[list[dict]]:
    """将跨分块的连续 |...| 行分组为逻辑表格。

    Args:
        chunks: 分块列表，每个分块包含 "content" 和 "metadata" 键

    Returns:
        表格列表，每个表格由 dict 列表组成：
        {"chunk_index": int, "line_index": int, "text": str}
    """
    tables = []
    current_table = []
    in_table = False

    for ci, chunk in enumerate(chunks):
        lines = chunk["content"].split("\n")
        for li, line in enumerate(lines):
            stripped = line.strip()
            if TABLE_LINE.match(stripped):
                # Include all |...| lines (including separators) in the current table
                current_table.append(
                    {
                        "chunk_index": ci,
                        "line_index": li,
                        "text": stripped,
                    }
                )
                in_table = True
            else:
                if in_table and current_table:
                    tables.append(current_table)
                    current_table = []
                in_table = False

    if in_table and current_table:
        tables.append(current_table)
    return tables


def _check_structure_integrity(chunks: list[dict]) -> dict:
    """检查分块的结构完整性：表格/标题/条款的连续性。

    Args:
        chunks: 包含 "content" 和 "metadata" 键的字典列表

    Returns:
        包含整体得分和各子维度结果的字典
    """
    # ---- 表格完整性 ----
    tables = _detect_tables(chunks)
    broken_tables = []
    for table in tables:
        chunk_indices = set(row["chunk_index"] for row in table)
        if len(chunk_indices) > 1:
            sorted_indices = sorted(chunk_indices)
            pages = []
            for ci in sorted_indices:
                p = chunks[ci].get("metadata", {}).get("page")
                pages.append(p if p is not None else "?")

            # 统计各 chunk 中的行数
            rows_per_chunk = {}
            for row in table:
                rows_per_chunk.setdefault(row["chunk_index"], []).append(row)
            break_parts = [f"chunk {ci}: {len(rows_per_chunk[ci])} 行" for ci in sorted_indices]

            broken_tables.append(
                {
                    "index": len(broken_tables),
                    "chunks": sorted_indices,
                    "pages": list(dict.fromkeys(pages)),
                    "break_position": " / ".join(break_parts),
                    "preview": table[0]["text"][:50],
                }
            )

    table_total = len(tables)
    if table_total > 0:
        table_score = 1.0 - (len(broken_tables) / table_total)
    else:
        table_score = None

    # ---- 标题完整性 ----
    all_lines = []
    line_to_chunk = []
    for ci, chunk in enumerate(chunks):
        for line in chunk["content"].split("\n"):
            all_lines.append(line)
            line_to_chunk.append(ci)

    heading_indices = _detect_headings(all_lines)
    broken_headings = []
    for idx in heading_indices:
        ci = line_to_chunk[idx]
        if idx + 1 < len(all_lines):
            next_ci = line_to_chunk[idx + 1]
            if next_ci != ci:
                broken_headings.append(
                    {
                        "index": len(broken_headings),
                        "text": all_lines[idx][:50],
                        "page": chunks[ci].get("metadata", {}).get("page"),
                    }
                )

    heading_total = len(heading_indices)
    if heading_total > 0:
        heading_score = 1.0 - (len(broken_headings) / heading_total)
    else:
        heading_score = None

    # ---- 条款完整性 ----
    clause_indices = _detect_clauses(all_lines)
    broken_clauses = []
    for idx in clause_indices:
        ci = line_to_chunk[idx]
        if idx + 1 < len(all_lines):
            next_ci = line_to_chunk[idx + 1]
            if next_ci != ci:
                broken_clauses.append(
                    {
                        "index": len(broken_clauses),
                        "text": all_lines[idx][:50],
                        "page": chunks[ci].get("metadata", {}).get("page"),
                    }
                )

    clause_total = len(clause_indices)
    if clause_total > 0:
        clause_score = 1.0 - (len(broken_clauses) / clause_total)
    else:
        clause_score = None

    # ---- 综合得分（跳过 total=0 的子维度） ----
    available = [s for s in [table_score, heading_score, clause_score] if s is not None]
    overall = sum(available) / len(available) if available else 0.0

    return {
        "score": round(overall, 4),
        "table": {
            "score": table_score,
            "total": table_total,
            "broken": broken_tables,
        },
        "heading": {
            "score": heading_score,
            "total": heading_total,
            "broken": broken_headings,
        },
        "clause": {
            "score": clause_score,
            "total": clause_total,
            "broken": broken_clauses,
        },
    }


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """计算两个向量之间的余弦相似度。

    Args:
        a: 第一个向量
        b: 第二个向量

    Returns:
        余弦相似度值，0-1 之间
    """
    a_np = np.array(a)
    b_np = np.array(b)
    norm_a = np.linalg.norm(a_np)
    norm_b = np.linalg.norm(b_np)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(np.dot(a_np, b_np) / (norm_a * norm_b))


def _calc_sbr(embeddings: list[list[float]], threshold: float = 0.35,
              block_types: list[str] | None = None) -> dict:
    """计算语义断裂率（SBR）。

    比较相邻分块的 embedding 向量余弦相似度，
    低于 threshold 的视为语义断裂。

    跨类型边界（如 table↔text）跳过不计入评分，
    因为表格和文本的 embedding 天然差异大，不代表分块质量有问题。

    Args:
        embeddings: 每个分块的 embedding 向量列表
        threshold: 余弦相似度阈值，低于此值视为断裂
        block_types: 每个分块的 block_type 列表，用于跳过跨类型边界

    Returns:
        dict: 包含 score, total_boundaries, broken_boundaries
    """
    if len(embeddings) < 2:
        return {"score": 1.0, "total_boundaries": 0, "broken_boundaries": []}

    broken = []
    total = 0
    for i in range(len(embeddings) - 1):
        # 跳过跨类型边界（如 table↔text），这种差异是内容切换而非分块断裂
        if block_types and block_types[i] and block_types[i + 1] \
                and block_types[i] != block_types[i + 1]:
            continue
        sim = _cosine_similarity(embeddings[i], embeddings[i + 1])
        total += 1
        if sim < threshold:
            broken.append({"index": i, "similarity": round(sim, 4)})

    score = 1.0 - (len(broken) / total) if total > 0 else 1.0
    return {
        "score": round(score, 4),
        "total_boundaries": total,
        "broken_boundaries": broken,
    }


def _count_tokens(text: str) -> int:
    """估算 token 数量（中文约 2 字符/token）。

    Args:
        text: 文本内容

    Returns:
        token 数量，至少为 1
    """
    return max(1, len(text) // 2)


def _calc_granularity_cv(chunks: list[dict]) -> dict:
    """计算粒度变异系数（CV）并检测极端分块。

    Args:
        chunks: 分块列表，每个分块包含 "content" 键

    Returns:
        dict: 包含 score, cv, min_tokens, max_tokens, extreme_chunks
    """
    if not chunks:
        return {
            "score": None,
            "cv": None,
            "min_tokens": 0,
            "max_tokens": 0,
            "extreme_chunks": [],
        }

    token_counts = [_count_tokens(c["content"]) for c in chunks]
    mean = statistics.mean(token_counts)
    cv = statistics.stdev(token_counts) / mean if mean > 0 else 0.0

    extreme = []
    for i, t in enumerate(token_counts):
        if t < 50:
            extreme.append({"index": i, "tokens": t, "type": "tiny"})
        elif t > 2 * mean:
            extreme.append({"index": i, "tokens": t, "type": "oversized"})

    score = 1.0 - min(cv / 2.0, 1.0)
    return {
        "score": round(score, 4),
        "cv": round(cv, 4),
        "min_tokens": min(token_counts),
        "max_tokens": max(token_counts),
        "extreme_chunks": extreme,
    }


class ChunkQualityScorer:
    """分块质量评估器，聚合 3 个轻量级指标。

    Usage:
        scorer = ChunkQualityScorer()
        result = scorer.evaluate(chunks, "filename.pdf")
    """

    SBR_THRESHOLD = 0.35

    def evaluate(self, chunks: list[dict], source: str, strategy: str = "") -> dict:
        """运行全部 3 个指标并返回完整评估 JSON。

        Args:
            chunks: 分块列表，每个分块包含 "content" 和 "metadata" 键
            source: 源文件名，用于日志
            strategy: 分块策略（当前未使用，保留兼容）

        Returns:
            dict: 包含 version, enabled, overall_score, passed 及各维度结果
        """
        if not chunks:
            return {
                "version": 1,
                "enabled": True,
                "overall_score": None,
                "passed": False,
                "structure_integrity": self._safe_call("structure_integrity", _check_structure_integrity, chunks),
                "sbr": self._safe_call("sbr", self._calc_sbr, chunks),
                "granularity_cv": self._safe_call("granularity_cv", _calc_granularity_cv, chunks),
            }

        structure = self._safe_call("structure_integrity", _check_structure_integrity, chunks)
        sbr_result = self._safe_call("sbr", self._calc_sbr, chunks)
        cv_result = self._safe_call("granularity_cv", _calc_granularity_cv, chunks)

        # 从成功的指标计算综合得分
        scores = []
        weights = []
        metric_weights = {
            "structure_integrity": 0.45,
            "sbr": 0.45,
            "granularity_cv": 0.10,
        }

        for key, result in [("structure_integrity", structure), ("sbr", sbr_result), ("granularity_cv", cv_result)]:
            if result.get("score") is not None:
                scores.append(result["score"])
                weights.append(metric_weights[key])

        overall = None
        passed = False
        if scores and sum(weights) > 0:
            normalized_weights = [w / sum(weights) for w in weights]
            overall = sum(s * w for s, w in zip(scores, normalized_weights))
            passed = overall >= 0.70

        return {
            "version": 1,
            "enabled": True,
            "overall_score": round(overall, 4) if overall is not None else None,
            "passed": passed,
            "structure_integrity": structure,
            "sbr": sbr_result,
            "granularity_cv": cv_result,
        }

    def _calc_sbr(self, chunks: list[dict]) -> dict:
        """对全部分块批量 embedding 后计算 SBR。

        Args:
            chunks: 分块列表

        Returns:
            dict: SBR 结果，broken_boundaries 含 preview 字段
        """
        if len(chunks) < 2:
            return {"score": 1.0, "total_boundaries": 0, "broken_boundaries": []}

        texts = [c["content"] for c in chunks]
        embedder = get_embeddings()
        embeddings = embedder.embed_documents(texts)

        # 提取 block_types，用于跳过跨类型边界（如 table↔text）。
        # table 块有 "table"，text 块可能无此字段，统一归一化为 "text"
        block_types = []
        for c in chunks:
            bt = c.get("metadata", {}).get("block_type")
            block_types.append(bt if bt else "text")

        result = _calc_sbr(embeddings, self.SBR_THRESHOLD, block_types)
        for b in result["broken_boundaries"]:
            idx = b["index"]
            b["preview_before"] = texts[idx][:50]
            b["preview_after"] = texts[idx + 1][:50]
            meta_before = chunks[idx].get("metadata", {})
            meta_after = chunks[idx + 1].get("metadata", {})
            b["page_before"] = meta_before.get("page")
            b["page_after"] = meta_after.get("page")

        return result

    @staticmethod
    def _safe_call(name: str, func, *args, **kwargs) -> dict:
        """安全调用指标函数，失败时返回错误 dict。

        Args:
            name: 指标名称，用于错误信息
            func: 调用的函数
            *args: 传递给 func 的位置参数
            **kwargs: 传递给 func 的关键字参数

        Returns:
            dict: 函数正常返回的结果，或包含 error 信息的 dict
        """
        try:
            result = func(*args, **kwargs)
            return result
        except Exception as e:
            return {"score": None, "error": f"{name} failed: {e}"}
