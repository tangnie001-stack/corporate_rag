#!/usr/bin/env python3
"""分块质量报告 CLI — 对文档分块结果进行质量评估和统计。

用法：
    python -m src.cli.check_chunks <file_path>

输出 6 项质量指标：
  1. 总分块数
  2. 平均分块长度（字符数）
  3. 分块长度分布（P10 / P50 / P90 百分位）
  4. 实际重叠比例（暂未实现，保留为 0）
  5. 表格切断数（包含被切断的表格行的分块数量）
  6. 预览：前 5 个分块（各取前 100 字符）

使用场景：
  - 评估不同 CHUNK_SIZE / CHUNK_OVERLAP 参数对分块质量的影响
  - 检测表格文档是否被不恰当地切断
  - 在 Iter 2 文档处理流水线中作为质量校验工具
"""

import statistics
import sys
from pathlib import Path

# 将项目根目录加入 sys.path，确保 src 模块可以被正确导入
# 本文件位于 src/cli/check_chunks.py，需上两级到项目根
_project_root = str(Path(__file__).resolve().parent.parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from src.parsers.router import DocRouter


def check_table_integrity(chunks: list) -> list[int]:
    """检测可能存在表格切断的分块（启发式方法）。

    启发式规则：
      - 分块的首行以 "|" 开头 → 可能是表格中间被切断
      - 分块的末行以 "|" 结尾或包含多个 "|" → 可能是不完整的表格行

    Args:
        chunks: ChunkData 列表（每个 chunk 有 content 属性）

    Returns:
        疑似被切断的分块索引列表
    """
    cut_indices = []
    for i, chunk in enumerate(chunks):
        lines = chunk.content.strip().split("\n")
        # 统计含管道符 "|" 的行数（表格行的特征）
        pipe_lines = [l for l in lines if "|" in l]
        if not pipe_lines:
            continue
        # 检查首行是否以 | 开头（表格从中间开始 → 被切断）
        first_line = next((l for l in lines if l.strip()), "")
        if first_line.strip().startswith("|"):
            cut_indices.append(i)
            continue
        # 检查末行是否以 | 结尾或含多个 |（表格不完整 → 被切断）
        last_line = next((l for l in reversed(lines) if l.strip()), "")
        if last_line.strip().endswith("|") or last_line.strip().count("|") > 2:
            cut_indices.append(i)
    return cut_indices


def generate_report(file_path: str) -> dict:
    """为指定文档生成分块质量报告。

    流程：
      1. 通过 DocRouter 解析文档并分块
      2. 统计分块长度分布（min/max/mean/P10/P50/P90）
      3. 检测表格切断情况
      4. 生成前 5 个分块的预览

    Args:
        file_path: 文档文件路径

    Returns:
        包含所有质量指标的字典
    """
    router = DocRouter()
    result = router.parse(file_path)
    chunks = result.chunks

    # 空文档处理
    if not chunks:
        return {
            "file": file_path,
            "total_chunks": 0,
            "avg_length": 0,
            "min_length": 0,
            "max_length": 0,
            "p10": 0,
            "p50": 0,
            "p90": 0,
            "overlap_ratio": 0,
            "table_cut_count": 0,
            "table_cut_indices": [],
            "total_chars": 0,
            "total_pages": result.total_pages,
            "file_type": result.file_type,
            "preview": [],
        }

    # 计算每个分块的字符长度
    lengths = [len(c.content) for c in chunks]
    sorted_lengths = sorted(lengths)

    def percentile(data, p):
        """计算百分位数（简单线性索引法）。"""
        idx = max(0, min(len(data) - 1, int(len(data) * p / 100)))
        return data[idx]

    # 表格完整性检测
    cut_indices = check_table_integrity(chunks)
    # 前 5 个分块的预览（各取前 100 字符）
    preview = [
        {"index": i, "content": c.content[:100], "length": len(c.content)}
        for i, c in enumerate(chunks[:5])
    ]

    report = {
        "file": file_path,
        "total_chunks": len(chunks),
        "avg_length": round(statistics.mean(lengths), 1),
        "min_length": min(lengths),
        "max_length": max(lengths),
        "p10": percentile(sorted_lengths, 10),
        "p50": percentile(sorted_lengths, 50),
        "p90": percentile(sorted_lengths, 90),
        "overlap_ratio": 0,  # 暂未实现重叠率计算
        "table_cut_count": len(cut_indices),
        "table_cut_indices": cut_indices,
        "total_chars": sum(lengths),
        "total_pages": result.total_pages,
        "file_type": result.file_type,
        "preview": preview,
    }
    return report


def print_report(report: dict) -> None:
    """格式化打印质量报告到终端。

    Args:
        report: generate_report() 返回的报告字典
    """
    print("=" * 60)
    print("  Document Chunk Quality Report")
    print("=" * 60)
    print(f"  File:        {report['file']}")
    print(f"  Type:        {report['file_type']}")
    print(f"  Pages:       {report['total_pages']}")
    print(f"  Total chars: {report['total_chars']:,}")
    print("-" * 60)
    print("  Chunk Statistics")
    print(f"  Total chunks:  {report['total_chunks']}")
    print(f"  Average length: {report['avg_length']:.1f} chars")
    print(f"  Min length:    {report['min_length']}")
    print(f"  Max length:    {report['max_length']}")
    print(f"  P10:           {report['p10']}")
    print(f"  P50:           {report['p50']}")
    print(f"  P90:           {report['p90']}")
    print("-" * 60)
    if report["table_cut_count"] > 0:
        print(f"  WARNING: Table cuts detected: {report['table_cut_count']}")
        print(f"    Positions: chunk #{report['table_cut_indices']}")
    else:
        print("  Table integrity: No cuts detected")
    print("-" * 60)
    print(f"  Preview (first {len(report['preview'])} chunks)")
    for p in report["preview"]:
        print(f"  [{p['index']}] ({p['length']} chars) {p['content']}...")
    print("=" * 60)


def main() -> None:
    """CLI 入口函数 — 读取命令行参数并生成报告。"""
    if len(sys.argv) < 2:
        print("Usage: python -m src.cli.check_chunks <file_path>")
        sys.exit(1)

    file_path = sys.argv[1]
    report = generate_report(file_path)
    print_report(report)


if __name__ == "__main__":
    main()
