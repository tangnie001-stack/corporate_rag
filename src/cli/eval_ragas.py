"""RAGAS 评估脚本 — 对 RAG 系统的检索和生成质量进行标准化评估.

本脚本使用 RAGAS 库计算四个核心指标：
  - faithfulness: 回答是否忠实于检索到的上下文（有无幻觉）
  - answer_relevancy: 回答与问题的相关性
  - context_recall: 检索到的上下文是否覆盖了参考答案所需的信息
  - context_precision: 检索到的上下文中有多少是真正有用的

运行方式：
  python -m src.cli.eval_ragas --kb-id <kb-uuid>                          # 评估（最新测试集）
  python -m src.cli.eval_ragas --kb-id <kb-uuid> --testset-version 4       # 评估（指定测试集版本）
  python -m src.cli.eval_ragas --kb-id <kb-uuid> --gate                    # 评估并检查质量门禁
  python -m src.cli.eval_ragas --kb-id <kb-uuid> --generate                # 生成测试集
  python -m src.cli.eval_ragas --kb-id <kb-uuid> --generate --size 30      # 生成 30 条
  python -m src.cli.eval_ragas --list-kbs                                  # 列出可用知识库
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
        "--kb-id",
        type=str,
        default=None,
        help="要评估的知识库 UUID（与 --list-kbs 互斥）",
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
        "--gate",
        action="store_true",
        help="评估后检查质量门禁指标，任一不达标则退出码为 1",
    )
    parser.add_argument(
        "--generate",
        action="store_true",
        help="生成测试集模式（从文档自动生成 QA 对）",
    )
    parser.add_argument(
        "--testset-version",
        type=int,
        default=None,
        help="指定测试集版本号（默认取最新版本）",
    )
    parser.add_argument(
        "--size",
        type=int,
        default=None,
        help="生成测试集的 QA 对数（默认: settings.RAGAS_TEST_SIZE）",
    )
    parser.add_argument(
        "--model",
        type=str,
        default=None,
        help="生成测试集用的 LLM 模型名（默认: RAGAS_LLM_MODEL 或 LLM_MODEL）",
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

    每个指标内部会多次调用 LLM（非简单的一次一问一答），
    因此评估耗时明显长于回答生成：
      - faithfulness: 将回答拆成陈述句后逐句判断，N+1 次 LLM
      - answer_relevancy: 从回答反向生成候选项问题，M 次 LLM
      - context_recall: 逐条判断上下文是否覆盖参考答案，P 次 LLM
      - context_precision: 逐条判断上下文是否与问题相关，Q 次 LLM
    综上，评估阶段 LLM 调用次数 = 指标内调用次数 × 指标数 × QA 对数。

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
        f"**Configuration:** TOP_K_RETRIEVAL={cfg_topk}, TOP_K_RERANK={cfg_rerank}"
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
    """主入口 — 路由到生成模式或评估模式。"""
    args = parse_args()

    # ---- list-kbs 模式 ----
    if args.list_kbs:
        asyncio.run(_list_knowledge_bases())
        return

    # ---- 解析 kb_id ----
    if not args.kb_id:
        print("error: --kb-id 是必填参数（使用 --list-kbs 查看可用知识库）")
        sys.exit(1)
    kb_id = args.kb_id

    # ---- generate 模式 ----
    if args.generate:
        from src.cli.eval_ragas_generate import run_generate

        size = args.size or settings.RAGAS_TEST_SIZE
        model = args.model or ""
        run_generate(kb_id, size, model)
        return

    # ---- 评估模式（原有流程改造）----
    session_id = args.session_id
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = args.output or f"{DEFAULT_OUTPUT_DIR}/ragas_eval_{timestamp}.csv"

    # 从 JSON 加载测试集
    from src.cli.eval_ragas_generate import _load_latest_testset

    try:
        questions, ground_truth = _load_latest_testset(kb_id, version=args.testset_version)
    except FileNotFoundError as e:
        print(f"error: {e}")
        sys.exit(1)

    logger.info("加载测试集: {} 条 QA 对", len(questions))
    logger.info("Evaluating KB '{}'", kb_id)

    # ---- 初始化 RAG 组件 ----
    from datasets import Dataset  # noqa: F401
    from ragas import evaluate  # noqa: F401
    from ragas.llms import LangchainLLMWrapper
    from ragas.embeddings import LangchainEmbeddingsWrapper
    from ragas.metrics import (  # noqa: F401
        faithfulness,
        answer_relevancy,
        context_recall,
        context_precision,
    )
    from langchain_openai import ChatOpenAI
    from src.models import get_embeddings
    from src.rag.chain import RAGChain

    logger.info("Initializing RAGChain...")
    rag_chain = RAGChain()

    logger.info("Checking KB vector store...")
    if rag_chain.vector_store.get_or_create_collection(kb_id).count() == 0:
        logger.error("Knowledge base '{}' vector store is empty", kb_id)
        print("Knowledge base is empty")
        sys.exit(1)

    if not settings.RAGAS_LLM_MODEL:
        logger.error("RAGAS_LLM_MODEL 未配置，评估需要使用非推理模型（如 qwen-plus 系列）")
        sys.exit(1)
    eval_model = settings.RAGAS_LLM_MODEL
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

    logger.info("Generating answers for {} questions...", len(questions))
    answers, contexts = generate_answers_and_contexts(
        rag_chain,
        kb_id,
        session_id,
        questions,
    )

    result = run_evaluation(
        questions,
        ground_truth,
        answers,
        contexts,
        llm_wrapper,
        embeddings_wrapper,
    )

    output_path = save_results_csv(result, questions, ground_truth, output_path)
    save_markdown_report(result, questions, output_path)

    # _save_eval_report 需要 questions 长度
    _save_eval_report(kb_id, result, len(questions), output_path)

    if args.gate:
        check_gate(result, questions)

    logger.info("Evaluation complete.")


def _save_eval_report(
    kb_id: str,
    result,
    qa_count: int,
    output_path: str,
) -> None:
    """将 RAGAS 评估结果持久化到 eval_report 表.

    Args:
        kb_id: 知识库 UUID
        result: RAGAS evaluate() 返回的结果对象
        qa_count: QA 对数
        output_path: CSV 报告文件路径
    """
    try:
        from src.services.app_service import AppService

        svc = AppService()

        df = result.to_pandas()
        metric_cols = [
            "faithfulness",
            "answer_relevancy",
            "context_precision",
            "context_recall",
        ]
        detail = []
        for i in range(qa_count):
            entry = {"q_index": i, "question": ""}
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
                    "qa_count": qa_count,
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
        logger.info("Eval report saved to eval_report table for KB '{}'", kb_id)
    except Exception as e:
        logger.warning("Failed to save eval report to database: {}", e)


async def _list_knowledge_bases() -> None:
    """列出 MySQL 中所有知识库的名称和文档数."""
    from src.services.app_service import AppService
    from src.config import RAGAS_USER_ID

    svc = AppService()

    # 获取所有知识库（含 doc_count）
    kbs = await svc.db.get_all_kb(RAGAS_USER_ID)
    if not kbs:
        print("No knowledge bases found.")
        return

    print("\nAvailable knowledge bases:")
    print("-" * 40)
    for kb in kbs:
        print(f"  {kb['name']:<30} ({kb['doc_count']} documents)")
    print()


if __name__ == "__main__":
    main()
