"""RAGAS 评估脚本 — 对 RAG 系统的检索和生成质量进行标准化评估.

本脚本使用 RAGAS 库计算四个核心指标：
  - faithfulness: 回答是否忠实于检索到的上下文（有无幻觉）
  - answer_relevancy: 回答与问题的相关性
  - context_recall: 检索到的上下文是否覆盖了参考答案所需的信息
  - context_precision: 检索到的上下文中有多少是真正有用的

运行方式：
  python -m src.cli.eval_ragas --kb-name "我的知识库"  # 评估指定知识库
  python -m src.cli.eval_ragas --kb-name "我的知识库" --gate  # 评估并检查质量门禁
  python -m src.cli.eval_ragas --list-kbs              # 列出可用知识库
  python -m src.cli.eval_ragas --check                 # 检查 QA 对数
"""

import argparse
import os
import sys
from datetime import datetime
from typing import Any

import asyncio
from loguru import logger

from src.core.logging import setup_logging

from src.config import settings, DASHSCOPE_API_KEY, DASHSCOPE_BASE_URL
from src.config.qa_pairs import QUESTIONS, GROUND_TRUTH

setup_logging()


# 默认输出目录
DEFAULT_OUTPUT_DIR: str = "data/reports"

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
        default=None,
        help="要评估的知识库名称",
    )
    parser.add_argument(
        "--list-kbs",
        action="store_true",
        help="列出所有可用知识库后退出",
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
        help="检查 QA 对数 >= 50，不满足则退出码为 1",
    )
    parser.add_argument(
        "--gate",
        action="store_true",
        help="评估后检查质量门禁指标，任一不达标则退出码为 1",
    )
    return parser.parse_args()


def generate_answers_and_contexts(
    rag_chain: Any,
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
            logger.warning("Failed to generate answer for Q{}: {}", i + 1, e)
            answers.append(f"[ERROR] {e}")
            contexts.append([])

    return answers, contexts


def run_evaluation(
    questions: list[str],
    ground_truth: list[str],
    answers: list[str],
    contexts: list[list[str]],
    llm_wrapper: Any,
    embeddings_wrapper: Any,
) -> Any:
    """运行 RAGAS 四指标评估.

    Args:
        questions: 问题列表
        ground_truth: 参考答案列表
        answers: 系统生成的回答列表
        contexts: 检索到的上下文列表
        llm_wrapper: RAGAS 用 LLM 封装器（LangchainLLMWrapper 实例）
        embeddings_wrapper: RAGAS 用 Embeddings 封装器（LangchainEmbeddingsWrapper 实例）

    Returns:
        包含评估结果的 EvaluationResult 对象（可调用 .to_pandas() 转为 DataFrame）
    """
    from datasets import Dataset
    from ragas import evaluate
    from ragas.metrics import (
        faithfulness,
        answer_relevancy,
        context_recall,
        context_precision,
    )

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
    result: Any,
    questions: list[str],
    ground_truth: list[str],
    output_path: str,
) -> str:
    """将评估结果保存为 CSV 文件.

    Args:
        result: RAGAS evaluate() 返回的结果对象
        questions: 问题列表
        ground_truth: 参考答案列表
        output_path: 输出文件路径

    Returns:
        实际写入的文件路径
    """
    df = result.to_pandas()

    # 添加元信息列
    df.insert(0, "question", questions)
    df.insert(1, "ground_truth", ground_truth)

    # 确保输出目录存在
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    df.to_csv(output_path, index=True, encoding="utf-8-sig")
    logger.info("Results saved to: {}", output_path)
    return output_path


def save_markdown_report(
    result: Any,
    questions: list[str],
    output_path: str,
) -> str:
    """将评估结果保存为 Markdown 摘要报告，与 CSV 同路径（.md 后缀）.

    Args:
        result: RAGAS evaluate() 返回的结果对象
        questions: 问题列表
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

    cfg_topk = settings.TOP_K_RETRIEVAL
    cfg_rerank = settings.TOP_K_RERANK

    lines: list[str] = []
    lines.append("# RAGAS Evaluation Report")
    lines.append("")
    lines.append(f"**Date:** {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(
        f"**Configuration:** "
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
    """检查 QA 对数是否 >= 50，不满足时退出码为 1.

    Args:
        questions: 问题列表
    """
    count = len(questions)
    MIN_QA = 50
    if count < MIN_QA:
        print(f"QA pairs only {count} (< {MIN_QA}). "
              f"Add more questions and ground_truth to src/config/qa_pairs.py.")
        print("建议覆盖以下类型：事实查询、推理查询、多上下文查询、边界案例。")
        sys.exit(1)
    print(f"QA pair count: {count} (OK)")
    sys.exit(0)


def check_gate(result: Any, questions: list[str]) -> None:
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


def main() -> None:
    """主入口 — 解析参数、运行评估、保存结果."""
    args = parse_args()

    # ---- list-kbs 模式：列出知识库后退出 ----
    if args.list_kbs:
        _list_knowledge_bases()
        return

    # ---- check 独立模式：只检查 QA 对数，不执行评估 ----
    if args.check:
        check_qa_count(QUESTIONS)

    # ---- 执行评估需要 kb-name ----
    if not args.kb_name:
        print("error: --kb-name is required for evaluation")
        print("Use --list-kbs to see available knowledge bases")
        sys.exit(1)

    kb_name = args.kb_name
    session_id = args.session_id

    # 生成输出路径
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = (
        args.output or f"{DEFAULT_OUTPUT_DIR}/ragas_eval_{timestamp}.csv"
    )

    # ---- 到此为止不需要重依赖，以下开始懒加载 ----
    from datasets import Dataset  # noqa: F401
    from ragas import evaluate  # noqa: F401
    from ragas.llms import LangchainLLMWrapper  # noqa: F401
    from ragas.embeddings import LangchainEmbeddingsWrapper  # noqa: F401
    from ragas.metrics import (  # noqa: F401
        faithfulness,
        answer_relevancy,
        context_recall,
        context_precision,
    )
    from langchain_openai import ChatOpenAI
    from src.models import get_embeddings
    from src.rag.chain import RAGChain

    logger.info("Evaluating KB '{}'", kb_name)

    try:
        # ---- 初始化 RAG 组件 ----
        logger.info("Initializing RAGChain...")
        rag_chain = RAGChain()

        # ---- 从名称查找 kb_id ----
        from src.services.app_service import AppService

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
        eval_model = settings.RAGAS_LLM_MODEL or settings.LLM_MODEL
        logger.info("Initializing RAGAS evaluator ({})...", eval_model)
        llm = ChatOpenAI(
            model=eval_model,
            temperature=0,
            api_key=DASHSCOPE_API_KEY,
            base_url=DASHSCOPE_BASE_URL,
        )
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
            output_path,
        )
        save_markdown_report(result, QUESTIONS, output_path)

        # ---- 写入 eval_report 表 ----
        _save_eval_report(kb_name, result, QUESTIONS, output_path)

        # ---- gate 模式：检查评估结果是否通过质量门禁 ----
        if args.gate:
            check_gate(result, QUESTIONS)
    finally:
        pass  # 不再需要 benchmark 清理逻辑

    logger.info("Evaluation complete.")


def _save_eval_report(
    kb_name: str,
    result,
    questions: list[str],
    output_path: str,
) -> None:
    """将 RAGAS 评估结果持久化到 eval_report 表.

    Args:
        kb_name: 知识库名称
        result: RAGAS evaluate() 返回的结果对象
        questions: 问题列表
        output_path: CSV 报告文件路径
    """
    try:
        from src.services.app_service import AppService

        svc = AppService()
        kb_id = svc.db.get_kb_by_name(kb_name)
        if not kb_id:
            logger.warning("KB '{}' not found, skipping eval_report write", kb_name)
            return

        df = result.to_pandas()
        metric_cols = [
            "faithfulness",
            "answer_relevancy",
            "context_precision",
            "context_recall",
        ]
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

        weights = {
            "faithfulness": 0.3,
            "context_recall": 0.3,
            "context_precision": 0.2,
            "answer_relevancy": 0.2,
        }
        weighted_sum = 0.0
        total_w = 0.0
        for k, w in weights.items():
            v = avg.get(k)
            if v is not None:
                weighted_sum += v * w
                total_w += w
        overall = weighted_sum / total_w if total_w > 0 else None

        async def _do_insert():
            await svc.db.insert_eval_report(
                {
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
                }
            )

        asyncio.run(_do_insert())
        logger.info("Eval report saved to eval_report table for KB '{}'", kb_name)
    except Exception as e:
        logger.warning("Failed to save eval report to database: {}", e)


def _list_knowledge_bases() -> None:
    """列出 MySQL 中所有知识库的名称和文档数."""
    from src.services.app_service import AppService

    svc = AppService()

    async def _do_list():
        # 获取所有知识库
        kbs = await svc.db.get_all_knowledge_bases()
        if not kbs:
            print("No knowledge bases found.")
            return

        print("\nAvailable knowledge bases:")
        print("-" * 40)
        for kb in kbs:
            kb_id = kb["id"]
            kb_name = kb["name"]
            # 统计文档数
            docs = await svc.db.get_documents(kb_id)
            doc_count = len(docs) if docs else 0
            print(f"  {kb_name:<30} ({doc_count} documents)")
        print()

    asyncio.run(_do_list())


if __name__ == "__main__":
    main()
