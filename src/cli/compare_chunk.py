#!/usr/bin/env python3
"""对比不同分块大小下的 RAGAS 评估指标。

遍历多个 chunk_size 分别运行 eval_ragas，输出 Markdown 对比报告。
用于实验确定当前知识库的最佳分块大小。

Usage:
    python -m src.cli.compare_chunk --kb-name rag_eval --chunk-sizes 512 768 1024
"""

import argparse
import re
import subprocess
from datetime import datetime
from pathlib import Path


def parse_metrics_from_stdout(stdout: str) -> dict[str, float]:
    """从 eval_ragas 的标准输出中解析 RAGAS 指标均值。

    逐行扫描已知指标名（faithfulness, answer_relevancy,
    context_precision, context_recall），提取行中的首个浮点数。

    Args:
        stdout: eval_ragas 的原始标准输出。

    Returns:
        指标名到浮点值的映射，缺失指标不包含在内。
    """
    metrics: dict[str, float] = {}
    metric_names = [
        "faithfulness",
        "answer_relevancy",
        "context_precision",
        "context_recall",
    ]
    for line in stdout.splitlines():
        for metric in metric_names:
            if metric in line.lower():
                # 取冒号后的最后一个片段，若无冒号则用整行
                segment = line.split(":")[-1] if ":" in line else line
                match = re.search(r"[\d.]+", segment)
                if match:
                    metrics[metric] = float(match.group(0))
    return metrics


def run_eval(chunk_size: int, kb_name: str) -> dict:
    """通过子进程运行指定 chunk_size 的 eval_ragas。

    Args:
        chunk_size: 要评估的分块大小。
        kb_name: 待评估的知识库名称。

    Returns:
        包含 'chunk_size', 'stdout', 'returncode' 的字典。
    """
    output_path = Path(
        f"data/reports/ragas_eval_{datetime.now().strftime('%Y%m%d')}_{chunk_size}.csv"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            "python",
            "-m",
            "src.eval_ragas",
            "--kb-name",
            kb_name,
            "--chunk-size",
            str(chunk_size),
            "--output",
            str(output_path),
        ],
        capture_output=True,
        text=True,
    )
    return {
        "chunk_size": chunk_size,
        "stdout": result.stdout,
        "returncode": result.returncode,
    }


def generate_report(results: list[dict], output_path: Path) -> None:
    """生成并保存 Markdown 格式的对比报告。

    构建一个按 chunk_size 分行的 RAGAS 指标表格，写入 output_path。

    Args:
        results: run_eval() 返回的结果列表。
        output_path: Markdown 文件保存路径。
    """
    lines = [
        "# Chunk Size Comparison Report",
        f"**Date:** {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
        "## Per-Metric Scores",
        "",
        "| Chunk Size | faithfulness | answer_relevancy | context_precision | context_recall |",
        "|---|---|---|---|---|",
    ]
    for r in results:
        m = parse_metrics_from_stdout(r["stdout"])
        lines.append(
            f"| {r['chunk_size']} | {m.get('faithfulness', 'N/A')} | "
            f"{m.get('answer_relevancy', 'N/A')} | "
            f"{m.get('context_precision', 'N/A')} | "
            f"{m.get('context_recall', 'N/A')} |"
        )
    lines.extend(["", "## Summary", ""])
    output_path.write_text("\n".join(lines) + "\n")
    print(f"Report saved to {output_path}")


def main() -> None:
    """入口：解析参数、运行评估、生成报告。"""
    parser = argparse.ArgumentParser(description="对比不同分块大小的 RAGAS 指标")
    parser.add_argument("--kb-name", default="rag_eval", help="知识库名称")
    parser.add_argument(
        "--chunk-sizes",
        nargs="+",
        type=int,
        default=[512, 768, 1024],
        help="待对比的分块大小列表",
    )
    args = parser.parse_args()

    output_path = Path("data/reports/chunk_comparison.md")
    results = [run_eval(cs, args.kb_name) for cs in args.chunk_sizes]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    generate_report(results, output_path)


if __name__ == "__main__":
    main()
