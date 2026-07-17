# eval_ragas.py 精简重构 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 精简 `eval_ragas.py`，删除过时的 Benchmark 模式，新增评估 LLM 独立配置，调整参数为必填并提供知识库列表查询。

**Architecture:** 仅修改两个文件：`src/config/settings.py` 新增一个配置项；`src/cli/eval_ragas.py` 删除 Benchmark 模式相关代码和函数，调整参数系统，评估 LLM 从 `get_llm()` 改为独立创建。

**Tech Stack:** Python 3.11+, LangChain ChatOpenAI, RAGAS, FastAPI

## Global Constraints

- 评估 LLM temperature 固定为 0
- `RAGAS_LLM_MODEL` 为空时回退到 `LLM_MODEL`
- `--kb-name` 为必填参数，不传时报错
- `--check` 阈值提高到 50，不达标时输出引导信息
- `--list-kbs` 列完直接退出，不执行评估
- `compare_retrieval.py` 不受本次改动影响

---

## File Structure

| 操作 | 文件 | 说明 |
|------|------|------|
| 修改 | `src/config/settings.py` | 第 42 行之后新增 `RAGAS_LLM_MODEL` |
| 修改 | `src/cli/eval_ragas.py` | 删除 Benchmark 模式、调整参数、独立创建 LLM |

---

### Task 1: 配置项新增

**Files:**
- Modify: `src/config/settings.py:42`

**Interfaces:**
- Consumes: 无
- Produces: `settings.RAGAS_LLM_MODEL: str` — 从环境变量读取，默认 `""`

- [ ] **Step 1: 在 `LLM_TEMPERATURE` 之后新增配置**

在第 42 行 `LLM_TEMPERATURE` 后面插入：

```python
# RAGAS 评估专用模型（独立于生产 LLM，temperature 固定为 0）
# 为空时回退到 LLM_MODEL
RAGAS_LLM_MODEL: str = os.getenv("RAGAS_LLM_MODEL", "")
```

- [ ] **Step 2: 验证格式**

```bash
ruff check src/config/settings.py
```

预期输出：无错误。

- [ ] **Step 3: 提交**

```bash
git add src/config/settings.py
git commit -m "feat: add RAGAS_LLM_MODEL config for evaluation-only LLM"
```

---

### Task 2: eval_ragas.py — 删除 Benchmark 模式

**Files:**
- Modify: `src/cli/eval_ragas.py:1-629`

**Interfaces:**
- Consumes: `settings.RAGAS_LLM_MODEL` (from Task 1)
- Produces: 精简后的 `main()`，不再包含 benchmark 逻辑

- [ ] **Step 1: 修改 imports**

删除用不到的 import：
```python
# 删除这行（不再使用 uuid）
import uuid
# 删除这行（不再需要 Optional）
from typing import Optional
```

`from src.models import get_llm, get_embeddings` 改为：
```python
from src.models import get_embeddings
```

新增 import（在文件顶部现有 import 块中添加）：
```python
from langchain_openai import ChatOpenAI
```

更新 `from src.config import settings` 改为：
```python
from src.config import settings, DASHSCOPE_API_KEY, DASHSCOPE_BASE_URL
```

- [ ] **Step 2: Step 2: 删除 `TEMP_KB_PREFIX` 常量**

删除第 56-57 行：
```python
# (整行删除) TEMP_KB_PREFIX: str = "ragas_eval_temp"
```

- [ ] **Step 3: 修改 `parse_args()`**

删除 `--chunk-size` 参数，`--kb-name` 改为必填，新增 `--list-kbs`，更新 `--check` 的 help 文本：

```python
def parse_args() -> argparse.Namespace:
    """解析命令行参数."""
    parser = argparse.ArgumentParser(
        description="RAGAS 评估脚本 — 对 RAG 系统进行标准化评估",
    )
    parser.add_argument(
        "--kb-name",
        type=str,
        required=True,
        help="要评估的知识库名称（必填）",
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
```

- [ ] **Step 4: 删除 benchmark 辅助函数**

删除整个 `setup_benchmark_kb()` 函数（第 372-407 行）。

删除整个 `cleanup_benchmark_kb()` 函数（第 410-423 行）。

- [ ] **Step 5: 重写 `main()` 核心逻辑**

替换 `main()` 函数（第 426-536 行）为：

```python
def main() -> None:
    """主入口 — 解析参数、运行评估、保存结果."""
    args = parse_args()

    # ---- list-kbs 模式：列出知识库后退出 ----
    if args.list_kbs:
        _list_knowledge_bases()
        return

    kb_name = args.kb_name
    session_id = args.session_id

    # ---- check 独立模式：只检查 QA 对数，不执行评估 ----
    if args.check:
        check_qa_count(QUESTIONS)

    # 生成输出路径
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = (
        args.output or f"{DEFAULT_OUTPUT_DIR}/ragas_eval_{timestamp}.csv"
    )

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
```

注意：
- `_list_knowledge_bases()` 是新增函数（在 Task 3 中实现）
- `save_results_csv()` 签名变了（去掉了 `chunk_size`）
- `save_markdown_report()` 签名变了（去掉了 `chunk_size`）
- `_save_eval_report()` 签名变了（去掉了 `chunk_size`）

- [ ] **Step 6: 更新 `check_qa_count()`**

将阈值为 20 → 50，输出带引导信息：

```python
def check_qa_count(questions: list[str]) -> None:
    """检查 QA 对数是否 >= 50，不满足时退出码为 1.

    Args:
        questions: 问题列表
    """
    count = len(questions)
    MIN_QA = 50
    if count < MIN_QA:
        print(f"QA pairs only {count} (< {MIN_QA}). "
              f"Add more questions and ground_truth to src/config/ragas_pairs.py.")
        print("建议覆盖以下类型：事实查询、推理查询、多上下文查询、边界案例。")
        sys.exit(1)
    print(f"QA pair count: {count} (OK)")
    sys.exit(0)
```

- [ ] **Step 7: 更新 `save_results_csv()`**

删除 `chunk_size` 参数：

```python
def save_results_csv(
    result: EvaluationResult,
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
```

- [ ] **Step 8: 更新 `save_markdown_report()`**

删除 `chunk_size` 参数，从配置行中移除 `chunk_size`：

```python
def save_markdown_report(
    result: EvaluationResult,
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
```

- [ ] **Step 9: 更新 `_save_eval_report()`**

删除 `chunk_size` 参数，其余逻辑不变：

```python
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
    # 函数体不变（删掉 chunk_size 参数声明即可）
    ...
```

注意：函数体内部逻辑不变，只需在 `main()` 中调用处也不传 `chunk_size`。

- [ ] **Step 10: 更新文件 docstring**

将文件顶部 docstring 中的"支持两种运行模式"改为仅标准模式：

```python
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
```

- [ ] **Step 11: 提交**

```bash
git add src/cli/eval_ragas.py
git commit -m "refactor: remove benchmark mode, simplify eval_ragas.py"
```

---

### Task 3: 实现 `--list-kbs` 功能

**Files:**
- Modify: `src/cli/eval_ragas.py`（新增 `_list_knowledge_bases()` 函数）

**Interfaces:**
- Consumes: `AppService.db.get_kb_by_name` 模式（使用 `AppService` 构造器获取 db 实例）
- Produces: 控制台输出知识库列表，退出不评估

- [ ] **Step 1: 新增 `_list_knowledge_bases()` 函数**

在 `_save_eval_report()` 函数之后、`if __name__ == "__main__"` 之前插入：

```python
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
```

- [ ] **Step 2: 验证 ruff**

```bash
ruff check src/cli/eval_ragas.py
```

预期输出：无错误。

- [ ] **Step 3: 提交**

```bash
git add src/cli/eval_ragas.py
git commit -m "feat: add --list-kbs to show available knowledge bases"
```

---

### Task 4: 验证

**Files:**
- 无文件修改

- [ ] **Step 1: ruff 检查**

```bash
ruff check src/
```

预期输出：无错误。

- [ ] **Step 2: 运行测试**

```bash
pytest tests/ -v
```

预期输出：全部通过。

- [ ] **Step 3: 手动验证 `--list-kbs`**

```bash
python -m src.cli.eval_ragas --list-kbs
```

预期输出：知识库名称和文档数列表。

- [ ] **Step 4: 手动验证 `--kb-name` 必填**

```bash
python -m src.cli.eval_ragas
```

预期输出：argparse 错误信息 `error: the following arguments are required: --kb-name`

- [ ] **Step 5: 提交最终验证**

```bash
git commit --allow-empty -m "chore: verify eval_ragas refactor"
```
