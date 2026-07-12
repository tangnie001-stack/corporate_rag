"""RAGAS 评估脚本 — 对 RAG 系统的检索和生成质量进行标准化评估.

本脚本使用 RAGAS 库计算四个核心指标：
  - faithfulness: 回答是否忠实于检索到的上下文（有无幻觉）
  - answer_relevancy: 回答与问题的相关性
  - context_recall: 检索到的上下文是否覆盖了参考答案所需的信息
  - context_precision: 检索到的上下文中有多少是真正有用的

支持两种运行模式：
  1. 默认模式：对指定知识库运行评估，需提前上传文档
  2. Benchmark 模式：python eval_ragas.py --chunk-size 768
     自动创建临时 KB，以指定 chunk_size 解析 sample.txt 后评估

使用方式：
  python src/eval_ragas.py                          # 默认模式（需先创建 rag_eval KB 并上传）
  python src/eval_ragas.py --chunk-size 768         # Benchmark 模式
  python src/eval_ragas.py --kb-name "我的知识库"   # 指定知识库名
  python src/eval_ragas.py --output ./results.csv   # 指定输出路径
"""

import argparse
import os
import sys
import uuid
from datetime import datetime
from typing import Optional

import asyncio
from loguru import logger

from src.core.logging import setup_logging

# ---- RAGAS 评估库 ----
from datasets import Dataset
from ragas import evaluate
from ragas.llms import LangchainLLMWrapper
from ragas.embeddings import LangchainEmbeddingsWrapper
from ragas.dataset_schema import EvaluationResult
from ragas.metrics import (
    faithfulness,
    answer_relevancy,
    context_recall,
    context_precision,
)

from src.config import settings
from src.config.qa_pairs import QUESTIONS, GROUND_TRUTH
from src.models import get_llm, get_embeddings
from src.rag_chain import RAGChain

setup_logging()


# 默认输出目录
DEFAULT_OUTPUT_DIR: str = "data/reports"
# 临时知识库名称前缀（benchmark 模式使用）
TEMP_KB_PREFIX: str = "ragas_eval_temp"

# Quality gate thresholds
GATE_THRESHOLDS: dict[str, float] = {
    "faithfulness": 0.85,
    "context_precision": 0.80,
    "context_recall": 0.70,
    "answer_relevancy": 0.85,
}


def parse_args() -> argparse.Namespace:
    """解析命令行参数."""
    parser = argparse.ArgumentParser(
        description="RAGAS 评估脚本 — 对 RAG 系统进行标准化评估",
    )
    parser.add_argument(
        "--kb-name",
        type=str,
        default="rag_eval",
        help="要评估的知识库名称（默认: rag_eval）",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=None,
        help="指定 chunk_size 运行 benchmark 模式（如 512/768/1024）",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="CSV 输出路径（默认: data/reports/ragas_eval_<timestamp>.csv）",
    )
    parser.add_argument(
        "--session-id",
        type=str,
        default="ragas_eval_session",
        help="评估用的会话 ID（默认: ragas_eval_session）",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="检查 QA 对数 >= 20，不满足则退出码为 1",
    )
    parser.add_argument(
        "--gate",
        action="store_true",
        help="评估后检查质量门禁指标，任一不达标则退出码为 1",
    )
    return parser.parse_args()


def generate_answers_and_contexts(
    rag_chain: RAGChain,
    kb_id: str,
    session_id: str,
    questions: list[str],
) -> tuple[list[str], list[list[str]]]:
    """对每个问题生成回答，并收集检索到的上下文.

    Args:
        rag_chain: RAGChain 实例
        kb_id: 知识库 UUID
        session_id: 会话 ID
        questions: 问题列表

    Returns:
        (answers, contexts) 元组：
        - answers: 每个问题的完整回答文本列表
        - contexts: 每个问题对应的检索上下文列表（每个元素是文档片段列表）
    """
    answers: list[str] = []
    contexts: list[list[str]] = []

    for i, q in enumerate(questions):
        logger.info("Generating answer for Q{}: {}...", i + 1, q[:40])

        try:
            token_gen, citations = rag_chain.chat_with_citations(kb_id, session_id, q)
            full_answer = "".join([t for t in token_gen])
            answers.append(full_answer)

            # 提取上下文字段列表（用于 context_recall / context_precision 评估）
            ctx_list = [c.content for c in citations]
            contexts.append(ctx_list)

            logger.info(
                "  Answer length: {} chars, contexts: {}",
                len(full_answer),
                len(ctx_list),
            )

        except Exception as e:
            logger.error("Failed to generate answer for Q{}: {}", i + 1, e)
            answers.append(f"[ERROR] {e}")
            contexts.append([])

    return answers, contexts


def run_evaluation(
    questions: list[str],
    ground_truth: list[str],
    answers: list[str],
    contexts: list[list[str]],
    llm_wrapper: LangchainLLMWrapper,
    embeddings_wrapper: LangchainEmbeddingsWrapper,
) -> EvaluationResult:
    """运行 RAGAS 四指标评估.

    Args:
        questions: 问题列表
        ground_truth: 参考答案列表
        answers: 系统生成的回答列表
        contexts: 检索到的上下文列表
        llm_wrapper: RAGAS 用 LLM 封装器
        embeddings_wrapper: RAGAS 用 Embeddings 封装器

    Returns:
        包含评估结果的 EvaluationResult 对象（可调用 .to_pandas() 转为 DataFrame）
    """
    data = {
        "question": questions,
        "answer": answers,
        "contexts": contexts,
        "ground_truth": ground_truth,
    }
    dataset = Dataset.from_dict(data)

    logger.info("Starting RAGAS evaluation with {} samples...", len(questions))
    result = evaluate(
        dataset=dataset,
        llm=llm_wrapper,
        embeddings=embeddings_wrapper,
        metrics=[
            context_precision,
            context_recall,
            faithfulness,
            answer_relevancy,
        ],
        raise_exceptions=False,
    )

    df = result.to_pandas()
    logger.info("Evaluation completed. Metrics:")
    for col in df.columns:
        if col in [
            "faithfulness",
            "answer_relevancy",
            "context_recall",
            "context_precision",
        ]:
            logger.info("  {}: {:.4f}", col, df[col].mean())

    return result


def save_results_csv(
    result: EvaluationResult,
    questions: list[str],
    ground_truth: list[str],
    chunk_size: Optional[int],
    output_path: str,
) -> str:
    """将评估结果保存为 CSV 文件.

    Args:
        result: RAGAS evaluate() 返回的结果对象
        questions: 问题列表
        ground_truth: 参考答案列表
        chunk_size: 使用的 chunk_size（benchmark 模式）
        output_path: 输出文件路径

    Returns:
        实际写入的文件路径
    """
    df = result.to_pandas()

    # 添加元信息列
    df.insert(0, "question", questions)
    df.insert(1, "ground_truth", ground_truth)
    df.insert(2, "chunk_size", chunk_size if chunk_size else settings.CHUNK_SIZE)

    # 确保输出目录存在；os.path.dirname 对裸文件名返回空字符串，用 or "." 兜底
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    df.to_csv(output_path, index=True, encoding="utf-8-sig")
    logger.info("Results saved to: {}", output_path)
    return output_path


def save_markdown_report(
    result: EvaluationResult,
    questions: list[str],
    chunk_size: Optional[int],
    output_path: str,
) -> str:
    """将评估结果保存为 Markdown 摘要报告，与 CSV 同路径（.md 后缀）.

    Args:
        result: RAGAS evaluate() 返回的结果对象
        questions: 问题列表
        chunk_size: 使用的 chunk_size（benchmark 模式）
        output_path: CSV 输出路径（用于推导 .md 路径）

    Returns:
        实际写入的 .md 文件路径
    """
    md_path = output_path.rsplit(".", 1)[0] + ".md"
    df = result.to_pandas()

    metric_cols = [
        "faithfulness",
        "answer_relevancy",
        "context_precision",
        "context_recall",
    ]
    actual_metrics = [c for c in metric_cols if c in df.columns]

    cfg_chunk = chunk_size if chunk_size else settings.CHUNK_SIZE
    cfg_topk = settings.TOP_K_RETRIEVAL
    cfg_rerank = settings.TOP_K_RERANK

    lines: list[str] = []
    lines.append("# RAGAS Evaluation Report")
    lines.append("")
    lines.append(f"**Date:** {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(
        f"**Configuration:** chunk_size={cfg_chunk}, "
        f"TOP_K_RETRIEVAL={cfg_topk}, TOP_K_RERANK={cfg_rerank}"
    )
    lines.append(f"**QA Pairs:** {len(questions)}")
    lines.append("")

    # 表头
    header = ["Question"] + actual_metrics
    lines.append("| " + " | ".join(header) + " |")
    lines.append("|" + "|".join("---" for _ in header) + "|")

    # 每行数据
    for i in range(len(df)):
        row_vals = [f"Q{i + 1}"] + [f"{df[m].iloc[i]:.4f}" for m in actual_metrics]
        lines.append("| " + " | ".join(row_vals) + " |")

    lines.append("")

    # 平均值
    avg_parts = ", ".join(f"{m}={df[m].mean():.4f}" for m in actual_metrics)
    lines.append(f"**Averages:** {avg_parts}")
    lines.append("")

    os.makedirs(os.path.dirname(md_path) or ".", exist_ok=True)
    content = "\n".join(lines)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(content)
    logger.info("Markdown report saved to: {}", md_path)
    return md_path


def check_qa_count(questions: list[str]) -> None:
    """检查 QA 对数是否 >= 20，不满足时退出码为 1.

    Args:
        questions: 问题列表
    """
    count = len(questions)
    if count < 20:
        print(f"QA pair count: {count} (below minimum 20)")
        sys.exit(1)
    print(f"QA pair count: {count} (OK)")
    sys.exit(0)


def check_gate(result: EvaluationResult, questions: list[str]) -> None:
    """检查评估结果是否通过质量门禁阈值.

    对 GATE_THRESHOLDS 中每个指标打印 PASS/FAIL，
    任一指标不达标时列出未通过的问题并退出码为 1。

    Args:
        result: RAGAS evaluate() 返回的结果对象
        questions: 问题列表
    """
    df = result.to_pandas()
    failing_indices: list[int] = []
    all_pass = True

    for metric, threshold in GATE_THRESHOLDS.items():
        if metric not in df.columns:
            print(f"  {metric}: N/A (metric not in evaluation results)")
            all_pass = False
            continue

        score = df[metric].mean()
        passed = score >= threshold
        status = "PASS" if passed else "FAIL"
        print(f"  {metric}: {score:.4f} {status}")

        if not passed:
            all_pass = False
            for i in range(len(df)):
                if df[metric].iloc[i] < threshold and i not in failing_indices:
                    failing_indices.append(i)

    if not all_pass:
        if failing_indices:
            print(f"\nFailing questions ({len(failing_indices)} total):")
            for idx in failing_indices:
                print(f"  Q{idx + 1}: {questions[idx][:80]}")
        sys.exit(1)

    sys.exit(0)


def setup_benchmark_kb(kb_name: str) -> tuple[str, str]:
    """为 benchmark 模式创建临时知识库并上传测试文档.

    流程：
      1. 创建知识库（如已存在则复用）
      2. 上传 test_docs/sample.txt
      3. 完成解析和向量化

    Args:
        kb_name: 知识库名称

    Returns:
        (kb_id, session_id) 元组
    """
    from src.app_service import AppService

    svc = AppService()
    session_id = f"ragas_eval_{uuid.uuid4().hex[:8]}"

    # 创建或获取知识库
    kb_id, is_new = svc.create_knowledge_base(kb_name)
    logger.info("KB '{}': id={}, new={}", kb_name, kb_id, is_new)

    # 上传测试文档
    test_doc_path = "test_docs/sample.txt"
    if not os.path.exists(test_doc_path):
        logger.error("Test document not found: {}", test_doc_path)
        raise FileNotFoundError(f"Test document not found: {test_doc_path}")

    result = svc.upload_and_process(kb_id, test_doc_path, "sample.txt")
    if result["success"]:
        logger.info("Test document uploaded: {} chunks", result["chunk_count"])
    else:
        logger.warning("Test document upload issue: {}", result.get("error"))

    return kb_id, session_id


def cleanup_benchmark_kb(kb_name: str) -> None:
    """清理 benchmark 模式创建的临时知识库.

    Args:
        kb_name: 知识库名称
    """
    from src.app_service import AppService

    svc = AppService()
    # get_kb_by_name 返回 kb_id（UUID 字符串）
    kb_id = svc.db.get_kb_by_name(kb_name)
    if kb_id:
        svc.delete_knowledge_base(kb_id)
        logger.info("Cleaned up temp KB: {} ({})", kb_name, kb_id)


def main() -> None:
    """主入口 — 解析参数、运行评估、保存结果."""
    args = parse_args()
    chunk_size = args.chunk_size
    kb_name = args.kb_name
    session_id = args.session_id

    # ---- check 模式：独立检查 QA 对数，不执行评估 ----
    if args.check:
        check_qa_count(QUESTIONS)

    # 生成输出路径
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    chunk_tag = f"_chunk{chunk_size}" if chunk_size else ""
    output_path = (
        args.output or f"{DEFAULT_OUTPUT_DIR}/ragas_eval_{timestamp}{chunk_tag}.csv"
    )

    # ---- Benchmark 模式：临时覆盖 chunk_size ----
    temp_kb_name: Optional[str] = None
    original_chunk_size: Optional[int] = None
    if chunk_size is not None:
        logger.info("Benchmark mode: chunk_size = {}", chunk_size)
        original_chunk_size = settings.CHUNK_SIZE
        settings.CHUNK_SIZE = chunk_size

        # 使用带 chunk_size 标记的临时 KB 名称
        temp_kb_name = f"{TEMP_KB_PREFIX}_chunk{chunk_size}"

        try:
            kb_id, session_id = setup_benchmark_kb(temp_kb_name)
            kb_name = temp_kb_name
        except Exception as e:
            logger.error("Benchmark KB setup failed: {}", e)
            settings.CHUNK_SIZE = original_chunk_size
            sys.exit(1)
    else:
        logger.info("Standard mode: evaluating KB '{}'", kb_name)

    try:
        # ---- 初始化 RAG 组件 ----
        logger.info("Initializing RAGChain...")
        rag_chain = RAGChain()

        # ---- 标准模式：从名称查找 kb_id ----
        if temp_kb_name is None:
            from src.app_service import AppService

            svc = AppService()
            kb_id = svc.db.get_kb_by_name(kb_name)
            if not kb_id:
                logger.error("Knowledge base '{}' not found", kb_name)
                sys.exit(1)

        # ---- 检查知识库是否为空 ----
        logger.info("Checking KB vector store...")
        if rag_chain.vector_store._collection.count() == 0:
            logger.error("Knowledge base '{}' vector store is empty", kb_name)
            print("Knowledge base is empty")
            sys.exit(1)

        # ---- 初始化 RAGAS 评估器 ----
        logger.info("Initializing RAGAS evaluator (Qwen-max)...")
        llm = get_llm()
        embeddings = get_embeddings()
        llm_wrapper = LangchainLLMWrapper(llm)
        embeddings_wrapper = LangchainEmbeddingsWrapper(embeddings)

        # ---- 生成答案和上下文 ----
        logger.info("Generating answers for {} questions...", len(QUESTIONS))
        answers, contexts = generate_answers_and_contexts(
            rag_chain,
            kb_id,
            session_id,
            QUESTIONS,
        )

        # ---- 运行评估 ----
        result = run_evaluation(
            QUESTIONS,
            GROUND_TRUTH,
            answers,
            contexts,
            llm_wrapper,
            embeddings_wrapper,
        )

        # ---- 保存结果 ----
        output_path = save_results_csv(
            result,
            QUESTIONS,
            GROUND_TRUTH,
            chunk_size,
            output_path,
        )
        save_markdown_report(result, QUESTIONS, chunk_size, output_path)

        # ---- 写入 eval_report 表 ----
        _save_eval_report(kb_name, result, QUESTIONS, output_path, chunk_size)

        # ---- gate 模式：检查评估结果是否通过质量门禁 ----
        if args.gate:
            check_gate(result, QUESTIONS)
    finally:
        # ---- 清理（benchmark 模式）：即使评估过程出异常也保证执行 ----
        if temp_kb_name is not None:
            cleanup_benchmark_kb(temp_kb_name)
            settings.CHUNK_SIZE = original_chunk_size  # type: ignore[arg-type]
            logger.info("Restored chunk_size to {}", original_chunk_size)

    logger.info("Evaluation complete.")


def _save_eval_report(
    kb_name: str,
    result,
    questions: list[str],
    output_path: str,
    chunk_size: int | None,
) -> None:
    """将 RAGAS 评估结果持久化到 eval_report 表。

    Args:
        kb_name: 知识库名称
        result: RAGAS evaluate() 返回的结果对象
        questions: 问题列表
        output_path: CSV 报告文件路径
        chunk_size: 使用的 chunk_size
    """
    try:
        from src.app_service import AppService

        svc = AppService()
        kb_id = svc.db.get_kb_by_name(kb_name)
        if not kb_id:
            logger.warning("KB '{}' not found, skipping eval_report write", kb_name)
            return

        df = result.to_pandas()
        metric_cols = ["faithfulness", "answer_relevancy", "context_precision", "context_recall"]
        detail = []
        for i, q in enumerate(questions):
            entry = {"q_index": i, "question": q[:100]}
            for col in metric_cols:
                if col in df.columns:
                    entry[col] = float(df[col].iloc[i])
            detail.append(entry)

        avg = {}
        for col in metric_cols:
            if col in df.columns:
                avg[col] = float(df[col].mean())

        faith = avg.get("faithfulness")
        recall = avg.get("context_recall")
        precision = avg.get("context_precision")
        relevancy = avg.get("answer_relevancy")

        weights = {"faithfulness": 0.3, "context_recall": 0.3,
                   "context_precision": 0.2, "answer_relevancy": 0.2}
        weighted_sum = 0.0
        total_w = 0.0
        for k, w in weights.items():
            v = avg.get(k)
            if v is not None:
                weighted_sum += v * w
                total_w += w
        overall = weighted_sum / total_w if total_w > 0 else None

        async def _do_insert():
            await svc.db.insert_eval_report({
                "kb_id": kb_id,
                "run_type": "manual",
                "qa_count": len(questions),
                "faithfulness": faith,
                "answer_relevancy": relevancy,
                "context_precision": precision,
                "context_recall": recall,
                "overall_score": overall,
                "passed": overall >= 0.70 if overall is not None else False,
                "report_path": output_path,
                "triggered_by": None,
                "detail_json": detail,
            })

        asyncio.run(_do_insert())
        logger.info("Eval report saved to eval_report table for KB '{}'", kb_name)
    except Exception as e:
        logger.warning("Failed to save eval report to database: {}", e)


if __name__ == "__main__":
    main()
