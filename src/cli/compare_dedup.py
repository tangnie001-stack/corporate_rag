"""对比 RETRIEVAL_MAX_PER_DOC 取值的 RAGAS 指标（dedup 多样性 A/B）。

遍历 max_per_doc ∈ {1, 2, 3}，在子进程覆盖 RETRIEVAL_MAX_PER_DOC 后
运行 eval_ragas，解析指标均值，打印并排对照。

前置条件（真实运行）：
  - 目标知识库真实存在且有测试集（data/ragas/testset/），否则 eval_ragas 直接退出；
  - 环境变量覆盖能传导到子进程：RETRIEVAL_MAX_PER_DOC 在 settings 导入时读取。

Usage:
    python -m src.cli.compare_dedup --kb-name rag_eval
"""

import argparse
import asyncio
import os
import subprocess
import sys

# 复用 compare_retrieval 的 _resolve_kb_id（名称→UUID）/ parse_metrics（stdout→指标）
from src.cli.compare_retrieval import _resolve_kb_id, parse_metrics

# 每文档保留条数候选值（实验网格）
CANDIDATES = [1, 2, 3]


def run_eval_with_dedup(max_per_doc: int, kb_id: str) -> dict[str, float]:
    """通过环境变量覆盖 RETRIEVAL_MAX_PER_DOC 后运行 eval_ragas。

    在子进程环境中设置 RETRIEVAL_MAX_PER_DOC，从而在不修改 .env 文件的
    情况下让应用读取覆盖后的值。子进程超时或非零退出时不阻断整轮实验，
    返回空指标 dict（表内对应单元格显示 nan）。

    Args:
        max_per_doc: 每文档保留条数（本轮实验值）
        kb_id: 目标知识库 id

    Returns:
        解析后的 RAGAS 指标均值 dict（指标名 → 浮点值，缺失键不包含）
    """
    env = os.environ.copy()
    env["RETRIEVAL_MAX_PER_DOC"] = str(max_per_doc)
    cmd = [sys.executable, "-m", "src.cli.eval_ragas", "--kb-id", kb_id]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            env=env,
            timeout=1200,
            check=False,
        )
    except subprocess.TimeoutExpired:
        print(
            f"  [error] eval_ragas timeout for max_per_doc={max_per_doc}",
            file=sys.stderr,
        )
        return {}
    if result.returncode != 0:
        # 非零退出（如 KB 无向量 / 测试集缺失 / --gate 未达标）时 stdout 无指标行，
        # 把 stderr 尾部打出来便于定位，指标按空 dict 处理
        stderr_tail = result.stderr.strip().splitlines()[-3:]
        print(
            f"  [warning] eval_ragas exit={result.returncode} for max_per_doc={max_per_doc}",
            file=sys.stderr,
        )
        for line in stderr_tail:
            print(f"    {line}", file=sys.stderr)
    return parse_metrics(result.stdout)


def main() -> None:
    """跑 N=1/2/3 对照并打印表格。"""
    parser = argparse.ArgumentParser(
        description="对比 RETRIEVAL_MAX_PER_DOC 取值的 RAGAS 指标（dedup 多样性 A/B）",
    )
    parser.add_argument("--kb-name", required=True, help="知识库名称（如 rag_eval）")
    args = parser.parse_args()

    # 名称 → UUID（eval_ragas 只接受 --kb-id）
    try:
        kb_id = asyncio.run(_resolve_kb_id(args.kb_name))
    except ValueError as e:
        print(f"error: {e}")
        sys.exit(1)

    # 表头
    print(
        f"{'max_per_doc':<12} {'context_recall':<14} {'context_precision':<18} "
        f"{'faithfulness':<14}"
    )
    print("-" * 60)

    for n in CANDIDATES:
        m = run_eval_with_dedup(n, kb_id)
        print(
            f"{n:<12} {m.get('context_recall', float('nan')):<14.4f} "
            f"{m.get('context_precision', float('nan')):<18.4f} "
            f"{m.get('faithfulness', float('nan')):<14.4f}"
        )


if __name__ == "__main__":
    main()
