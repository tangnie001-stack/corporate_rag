# Iter 6 — 评估与收尾 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (\`- [ ] \`) syntax for tracking.

**Goal:** 搭建 RAGAS 评估流水线，完成 chunk_size benchmark 对比，完善项目文档，为 MVP 交付画上句号。

**Architecture:** 新增 eval_ragas.py 评估脚本（基于 ragas 库），从 qa_pairs.py 读取测试 QA 对，调用 RAGChain 生成答案，用 Qwen-max 评估四个核心指标，结果输出到 CSV。--chunk-size 参数支持自动创建临时 KB 做 benchmark 对比。README.md 补充完整项目文档。

**Tech Stack:** Python 3.11, RAGAS, Datasets (HuggingFace), Qwen-max (evaluator LLM), loguru

---

## 文件结构 Iter 6 创建/修改清单

```
src/
├── config/
│   └── qa_pairs.py         (新建) RAGAS 评估用测试 QA 对数据集
├── eval_ragas.py            (新建) RAGAS 评估脚本（含 benchmark 支持）

tests/
├── test_eval_ragas.py       (新建) 评估脚本单元测试

README.md                    (修改) 完整项目文档

outputs/                     (新建，gitignore) CSV 输出目录
```

---


### Task 1: 测试 QA 对数据集

**Files:**
- Create: `src/config/qa_pairs.py`

本模块存放 RAGAS 评估使用的测试 QA 对（question + ground_truth），基于 test_docs/sample.txt（贵州茅台 2024 年年报摘要）构造。

- [ ] **Step 1: 编写 `src/config/qa_pairs.py`**

```python
"""RAGAS 评估用测试 QA 对数据集。

本模块存放用于 RAGAS 评估的测试问题及其参考答案（ground truth），
覆盖贵州茅台 2024 年年报摘要中的核心财务数据。

与测试文档的对应关系：
  test_docs/sample.txt — 贵州茅台 2024 年年报摘要
  qa_pairs.py — 基于上述文档构造的 7 个 QA 对

QA 对覆盖维度：
  - 收入与增长（Q1, Q2）
  - 每股收益与利润分配（Q3, Q4）
  - 主营业务分析（Q5）
  - 股东信息（Q6）
  - 公司基本信息（Q7）
"""

# 测试问题列表
QUESTIONS: list[str] = [
    "贵州茅台2024年的营业总收入是多少？同比增长多少？",
    "贵州茅台2024年归属于上市公司股东的净利润是多少？同比增长多少？",
    "贵州茅台2024年的基本每股收益是多少？",
    "贵州茅台2024年的利润分配预案是什么？每10股派发现金红利多少？",
    "贵州茅台的主要业务是什么？2024年茅台酒和系列酒的收入分别是多少？",
    "贵州茅台2024年末的前十大股东中，持股比例最大的股东是谁？持多少？",
    "贵州茅台的股票代码和上市交易所是什么？",
]

# 参考答案（从 sample.txt 中提取）
GROUND_TRUTH: list[str] = [
    "2024年公司实现营业总收入1,741亿元，同比增长15.66%。",
    "2024年归属于上市公司股东的净利润857亿元，同比增长14.67%。",
    "基本每股收益68.24元，同比增长14.67%。",
    "以总股本185,391,680股为基数，向全体股东每10股派发现金红利1元（含税），不送红股，不以公积金转增股本。",
    "公司主要从事茅台酒及系列酒的生产与销售。茅台酒营业收入1,458亿元，系列酒营业收入246亿元。",
    "中国贵州茅台酒厂（集团）有限责任公司持股54.00%。",
    "证券代码600519，上市交易所为上海证券交易所。",
]
```

- [ ] **Step 2: 验证 QA 对可导入且数量一致**

```bash
python3 -c "from src.config.qa_pairs import QUESTIONS, GROUND_TRUTH; print(f'{len(QUESTIONS)} QA pairs loaded'); assert len(QUESTIONS) == len(GROUND_TRUTH)"
# 预期: 7 QA pairs loaded
```

- [ ] **Step 3: Commit**

```bash
git add src/config/qa_pairs.py
git commit -m "feat: add RAGAS evaluation QA pairs for sample.txt"
```

---


### Task 2: RAGAS 评估脚本

**Files:**
- Create: `src/eval_ragas.py`
- Create: `outputs/` 目录（CSV 输出目录）

主评估脚本，支撑两种运行模式：
  1. **评估模式（默认）** — 基于现有知识库运行 RAGAS 评估
  2. **Benchmark 模式** — 指定 --chunk-size，自动创建临时 KB 做对比测试

- [ ] **Step 1: 创建 outputs/ 目录**

```bash
mkdir -p outputs
echo "outputs/" >> .gitignore
```

- [ ] **Step 2: 编写 `src/eval_ragas.py`**

```python
\"\"\"RAGAS 评估脚本 — 对 RAG 系统的检索和生成质量进行标准化评估。

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
  python src/eval_ragas.py --kb-name \"我的知识库\"   # 指定知识库名
  python src/eval_ragas.py --output ./results.csv   # 指定输出路径
\"\"\"
import argparse
import os
import sys
import uuid
from datetime import datetime
from typing import Optional

from loguru import logger

# ---- RAGAS 评估库 ----
from datasets import Dataset
from ragas import evaluate
from ragas.llms import LangchainLLMWrapper
from ragas.embeddings import LangchainEmbeddingsWrapper
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


# 默认输出目录
DEFAULT_OUTPUT_DIR: str = \"outputs\"
# 临时知识库名称前缀（benchmark 模式使用）
TEMP_KB_PREFIX: str = \"ragas_eval_temp\"


def parse_args() -> argparse.Namespace:
    \"\"\"解析命令行参数。\"\"\"
    parser = argparse.ArgumentParser(
        description=\"RAGAS 评估脚本 — 对 RAG 系统进行标准化评估\",
    )
    parser.add_argument(
        \"--kb-name\",
        type=str,
        default=\"rag_eval\",
        help=\"要评估的知识库名称（默认: rag_eval）\",
    )
    parser.add_argument(
        \"--chunk-size\",
        type=int,
        default=None,
        help=\"指定 chunk_size 运行 benchmark 模式（如 512/768/1024）\",
    )
    parser.add_argument(
        \"--output\",
        type=str,
        default=None,
        help=\"CSV 输出路径（默认: outputs/ragas_eval_<timestamp>.csv）\",
    )
    parser.add_argument(
        \"--session-id\",
        type=str,
        default=\"ragas_eval_session\",
        help=\"评估用的会话 ID（默认: ragas_eval_session）\",
    )
    return parser.parse_args()


def generate_answers_and_contexts(
    rag_chain: RAGChain,
    kb_name: str,
    session_id: str,
    questions: list[str],
) -> tuple[list[str], list[list[str]]]:
    \"\"\"对每个问题生成回答，并收集检索到的上下文。

    Args:
        rag_chain: RAGChain 实例
        kb_name: 知识库名称
        session_id: 会话 ID
        questions: 问题列表

    Returns:
        (answers, contexts) 元组：
        - answers: 每个问题的完整回答文本列表
        - contexts: 每个问题对应的检索上下文列表（每个元素是文档片段列表）
    \"\"\"
    answers: list[str] = []
    contexts: list[list[str]] = []

    for i, q in enumerate(questions):
        logger.info(\"Generating answer for Q{}: {}...\", i + 1, q[:40])

        try:
            token_gen, citations = rag_chain.chat_with_citations(kb_name, session_id, q)
            full_answer = \"\".join([t for t in token_gen])
            answers.append(full_answer)

            # 提取上下文字段列表（用于 context_recall / context_precision 评估）
            ctx_list = [c.content for c in citations]
            contexts.append(ctx_list)

            logger.info(\"  Answer length: {} chars, contexts: {}\", len(full_answer), len(ctx_list))

        except Exception as e:
            logger.error(\"Failed to generate answer for Q{}: {}\", i + 1, e)
            answers.append(\"[ERROR] {e}\")
            contexts.append([])

    return answers, contexts


def run_evaluation(
    questions: list[str],
    ground_truth: list[str],
    answers: list[str],
    contexts: list[list[str]],
    llm_wrapper: LangchainLLMWrapper,
    embeddings_wrapper: LangchainEmbeddingsWrapper,
) -> dict:
    \"\"\"运行 RAGAS 四指标评估。

    Args:
        questions: 问题列表
        ground_truth: 参考答案列表
        answers: 系统生成的回答列表
        contexts: 检索到的上下文列表
        llm_wrapper: RAGAS 用 LLM 封装器
        embeddings_wrapper: RAGAS 用 Embeddings 封装器

    Returns:
        包含评估结果的 dict（可转为 pandas DataFrame）
    \"\"\"
    data = {
        \"question\": questions,
        \"answer\": answers,
        \"contexts\": contexts,
        \"ground_truth\": ground_truth,
    }
    dataset = Dataset.from_dict(data)

    logger.info(\"Starting RAGAS evaluation with {} samples...\", len(questions))
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
    logger.info(\"Evaluation completed. Metrics:\")
    for col in df.columns:
        if col in [\"faithfulness\", \"answer_relevancy\", \"context_recall\", \"context_precision\"]:
            logger.info(\"  {}: {:.4f}\", col, df[col].mean())

    return result


def save_results_csv(
    result, questions: list[str], ground_truth: list[str],
    chunk_size: Optional[int], output_path: str,
) -> str:
    \"\"\"将评估结果保存为 CSV 文件。

    Args:
        result: RAGAS evaluate() 返回的结果对象
        questions: 问题列表
        ground_truth: 参考答案列表
        chunk_size: 使用的 chunk_size（benchmark 模式）
        output_path: 输出文件路径

    Returns:
        实际写入的文件路径
    \"\"\"
    df = result.to_pandas()

    # 添加元信息列
    df.insert(0, \"question\", questions)
    df.insert(1, \"ground_truth\", ground_truth)
    df.insert(2, \"chunk_size\", chunk_size if chunk_size else settings.CHUNK_SIZE)

    # 确保输出目录存在
    os.makedirs(os.path.dirname(output_path) or \".\", exist_ok=True)

    df.to_csv(output_path, index=True, encoding=\"utf-8-sig\")
    logger.info(\"Results saved to: {}\", output_path)
    return output_path


def setup_benchmark_kb(kb_name: str) -> tuple[str, str]:
    \"\"\"为 benchmark 模式创建临时知识库并上传测试文档。

    流程：
      1. 创建知识库（如已存在则复用）
      2. 上传 test_docs/sample.txt
      3. 完成解析和向量化

    Args:
        kb_name: 知识库名称

    Returns:
        (kb_id, session_id) 元组
    \"\"\"
    from src.app_service import AppService

    svc = AppService()
    session_id = f\"ragas_eval_{uuid.uuid4().hex[:8]}\"

    # 创建或获取知识库
    kb_id, is_new = svc.create_knowledge_base(kb_name)
    logger.info(\"KB '{}': id={}, new={}\", kb_name, kb_id, is_new)

    # 上传测试文档
    test_doc_path = \"test_docs/sample.txt\"
    if not os.path.exists(test_doc_path):
        logger.error(\"Test document not found: {}\", test_doc_path)
        raise FileNotFoundError(f\"Test document not found: {test_doc_path}\")

    result = svc.upload_and_process(kb_name, test_doc_path, \"sample.txt\")
    if result[\"success\"]:
        logger.info(\"Test document uploaded: {} chunks\", result[\"chunk_count\"])
    else:
        logger.warning(\"Test document upload issue: {}\", result.get(\"error\"))

    return kb_id, session_id


def cleanup_benchmark_kb(kb_name: str) -> None:
    \"\"\"清理 benchmark 模式创建的临时知识库。

    Args:
        kb_name: 知识库名称
    \"\"\"
    from src.app_service import AppService

    svc = AppService()
    kb_id = svc.db.get_kb_by_name(kb_name)
    if kb_id:
        svc.delete_knowledge_base(kb_id)
        logger.info(\"Cleaned up temp KB: {} ({})\", kb_name, kb_id)


def main() -> None:
    \"\"\"主入口 — 解析参数、运行评估、保存结果。\"\"\"
    args = parse_args()
    chunk_size = args.chunk_size
    kb_name = args.kb_name
    session_id = args.session_id

    # 生成输出路径
    timestamp = datetime.now().strftime(\"%Y%m%d_%H%M%S\")
    chunk_tag = f\"_chunk{chunk_size}\" if chunk_size else \"\"
    output_path = args.output or f\"{DEFAULT_OUTPUT_DIR}/ragas_eval_{timestamp}{chunk_tag}.csv\"

    # ---- Benchmark 模式：临时覆盖 chunk_size ----
    if chunk_size is not None:
        logger.info(\"Benchmark mode: chunk_size = {}\", chunk_size)
        original_chunk_size = settings.CHUNK_SIZE
        settings.CHUNK_SIZE = chunk_size

        # 使用带 chunk_size 标记的临时 KB 名称
        temp_kb_name = f\"{TEMP_KB_PREFIX}_chunk{chunk_size}\"

        try:
            kb_id, session_id = setup_benchmark_kb(temp_kb_name)
            kb_name = temp_kb_name
        except Exception as e:
            logger.error(\"Benchmark KB setup failed: {}\", e)
            settings.CHUNK_SIZE = original_chunk_size
            sys.exit(1)
    else:
        logger.info(\"Standard mode: evaluating KB '{}'\", kb_name)

    # ---- 初始化 RAG 组件 ----
    logger.info(\"Initializing RAGChain...\")
    rag_chain = RAGChain()

    # ---- 初始化 RAGAS 评估器 ----
    logger.info(\"Initializing RAGAS evaluator (Qwen-max)...\")
    llm = get_llm()
    embeddings = get_embeddings()
    llm_wrapper = LangchainLLMWrapper(llm)
    embeddings_wrapper = LangchainEmbeddingsWrapper(embeddings)

    # ---- 生成答案和上下文 ----
    logger.info(\"Generating answers for {} questions...\", len(QUESTIONS))
    answers, contexts = generate_answers_and_contexts(
        rag_chain, kb_name, session_id, QUESTIONS,
    )

    # ---- 运行评估 ----
    result = run_evaluation(
        QUESTIONS, GROUND_TRUTH, answers, contexts,
        llm_wrapper, embeddings_wrapper,
    )

    # ---- 保存结果 ----
    save_results_csv(result, QUESTIONS, GROUND_TRUTH, chunk_size, output_path)

    # ---- 清理（benchmark 模式） ----
    if chunk_size is not None:
        cleanup_benchmark_kb(temp_kb_name)
        settings.CHUNK_SIZE = original_chunk_size
        logger.info(\"Restored chunk_size to {}\", original_chunk_size)

    logger.info(\"Evaluation complete.\")


if __name__ == \"__main__\":
    main()
```

- [ ] **Step 3: 运行冒烟测试 — 验证导入和参数解析**

```bash
python3 -c "from src.eval_ragas import parse_args, run_evaluation; print('eval_ragas imports OK')"
# 预期: eval_ragas imports OK
```

- [ ] **Step 4: 验证参数解析**

```bash
python3 -m src.eval_ragas --help
# 预期: 显示 usage 信息，含 --kb-name / --chunk-size / --output / --session-id
```

- [ ] **Step 5: Commit**

```bash
git add src/eval_ragas.py .gitignore
git commit -m "feat: add RAGAS evaluation script with benchmark mode"
```

---


### Task 3: RAGAS 评估脚本单元测试

**Files:**
- Create: `tests/test_eval_ragas.py`

测试覆盖：参数解析、答案生成（mock RAGChain）、指标计算（mock ragas）。

- [ ] **Step 1: 编写 `tests/test_eval_ragas.py`**

```python
"""RAGAS 评估脚本单元测试.

测试覆盖：
  - TestParseArgs: 命令行参数解析
  - TestGenerateAnswers: 答案生成流程（mock RAGChain）
  - TestRunEvaluation: RAGAS 评估流程（mock ragas）
  - TestSaveResults: CSV 结果保存
"""
from unittest.mock import MagicMock, patch
import pytest
import os
import tempfile
import csv


class TestParseArgs:
    """命令行参数解析测试。"""

    @patch("sys.argv", ["eval_ragas.py"])
    def test_default_args(self):
        """默认参数应使用 rag_eval 作为知识库名。"""
        from src.eval_ragas import parse_args
        args = parse_args()
        assert args.kb_name == "rag_eval"
        assert args.chunk_size is None
        assert args.session_id == "ragas_eval_session"

    @patch("sys.argv", ["eval_ragas.py", "--kb-name", "测试库", "--chunk-size", "768"])
    def test_custom_args(self):
        """应正确解析自定义参数。"""
        from src.eval_ragas import parse_args
        args = parse_args()
        assert args.kb_name == "测试库"
        assert args.chunk_size == 768

    @patch("sys.argv", ["eval_ragas.py", "--output", "/tmp/result.csv"])
    def test_output_arg(self):
        """应正确解析输出路径参数。"""
        from src.eval_ragas import parse_args
        args = parse_args()
        assert args.output == "/tmp/result.csv"


class TestGenerateAnswers:
    """答案生成流程测试（全 mock RAGChain）。"""

    @patch("src.eval_ragas.RAGChain")
    def test_generate_success(self, mock_rag_chain_cls):
        """正常情况应返回答案列表和上下文列表。"""
        from src.eval_ragas import generate_answers_and_contexts

        mock_chain = MagicMock()
        mock_rag_chain_cls.return_value = mock_chain

        def mock_chat(kb, sess, q):
            def gen():
                yield f"Answer for: {q[:10]}"
            return gen(), [
                MagicMock(content="Context about 茅台营收1,741亿元"),
                MagicMock(content="Context about 同比增长15.66%"),
            ]

        mock_chain.chat_with_citations.side_effect = mock_chat

        answers, contexts = generate_answers_and_contexts(
            mock_chain, "test_kb", "sess_1",
            ["贵州茅台营收多少？", "净利润多少？"],
        )

        assert len(answers) == 2
        assert len(contexts) == 2
        assert "Answer for" in answers[0]
        assert len(contexts[0]) == 2

    @patch("src.eval_ragas.RAGChain")
    def test_generate_partial_failure(self, mock_rag_chain_cls):
        """部分问题失败时应返回错误标记，不中断整体流程。"""
        from src.eval_ragas import generate_answers_and_contexts

        mock_chain = MagicMock()
        mock_rag_chain_cls.return_value = mock_chain

        def mock_chat(kb, sess, q):
            if "失败" in q:
                raise ValueError("模拟错误")

            def gen():
                yield "正常回答"

            return gen(), [MagicMock(content="ctx")]

        mock_chain.chat_with_citations.side_effect = mock_chat

        answers, contexts = generate_answers_and_contexts(
            mock_chain, "kb", "sess", ["正常问题", "模拟失败", "正常问题2"],
        )

        assert len(answers) == 3
        assert "[ERROR]" in answers[1]
        assert contexts[1] == []


class TestRunEvaluation:
    """RAGAS 评估流程测试（全 mock）。"""

    @patch("src.eval_ragas.Dataset.from_dict")
    @patch("src.eval_ragas.evaluate")
    def test_evaluation_runs(self, mock_evaluate, mock_from_dict):
        """评估流程应正确调用 ragas.evaluate。"""
        from src.eval_ragas import run_evaluation

        mock_result = MagicMock()
        mock_result.to_pandas.return_value = MagicMock()
        mock_evaluate.return_value = mock_result

        llm_wrapper = MagicMock()
        emb_wrapper = MagicMock()

        result = run_evaluation(
            ["Q1"], ["GT1"], ["A1"], [["ctx1"]],
            llm_wrapper, emb_wrapper,
        )

        mock_evaluate.assert_called_once()
        assert result is mock_result

    @patch("src.eval_ragas.Dataset.from_dict")
    @patch("src.eval_ragas.evaluate")
    def test_evaluation_empty_contexts(self, mock_evaluate, mock_from_dict):
        """空上下文列表不应导致崩溃。"""
        from src.eval_ragas import run_evaluation

        mock_result = MagicMock()
        mock_result.to_pandas.return_value = MagicMock()
        mock_evaluate.return_value = mock_result

        result = run_evaluation(
            ["Q1"], ["GT1"], ["A1"], [[]],
            MagicMock(), MagicMock(),
        )
        assert result is mock_result


class TestSaveResults:
    """结果保存测试。"""

    def test_save_csv(self):
        """CSV 保存应包含所有必要列。"""
        from src.eval_ragas import save_results_csv
        import pandas as pd

        mock_df = pd.DataFrame({
            "faithfulness": [0.95],
            "answer_relevancy": [0.88],
            "context_recall": [0.92],
            "context_precision": [0.85],
        })
        mock_result = MagicMock()
        mock_result.to_pandas.return_value = mock_df

        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            with patch("src.config.settings") as mock_settings:
                mock_settings.CHUNK_SIZE = 512
                save_results_csv(
                    mock_result, ["Q1"], ["GT1"],
                    chunk_size=None, output_path=tmp_path,
                )

            with open(tmp_path, "r") as f:
                content = f.read()

            assert "faithfulness" in content

        finally:
            os.unlink(tmp_path)

    def test_save_csv_with_chunk_size(self):
        """Benchmark 模式 CSV 应记录 chunk_size。"""
        from src.eval_ragas import save_results_csv
        import pandas as pd

        mock_df = pd.DataFrame({
            "faithfulness": [0.95],
            "answer_relevancy": [0.88],
        })
        mock_result = MagicMock()
        mock_result.to_pandas.return_value = mock_df

        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            with patch("src.config.settings") as mock_settings:
                mock_settings.CHUNK_SIZE = 512
                save_results_csv(
                    mock_result, ["Q1"], ["GT1"],
                    chunk_size=768, output_path=tmp_path,
                )

            with open(tmp_path, "r") as f:
                content = f.read()

            assert "768" in content

        finally:
            os.unlink(tmp_path)
```

- [ ] **Step 2: 运行测试确认通过**

```bash
python3 -m pytest tests/test_eval_ragas.py -v 2>&1 | tail -15
# 预期: 8 passed (全部 mock，不需要 RAGAS 库或 API Key)
```

- [ ] **Step 3: Commit**

```bash
git add tests/test_eval_ragas.py
git commit -m "test: add unit tests for RAGAS evaluation script"
```

---

### Task 4: README.md 完整项目文档

**Files:**
- Modify: `README.md`

完善 README.md，包含项目简介、技术栈、架构说明、快速启动、使用指南、项目结构、配置说明、已知限制。

- [ ] **Step 1: 编写完整 `README.md`**

```markdown
# Financial QA MVP

**金融文档智能问答助手** — 基于 RAG（Retrieval-Augmented Generation）的财报问答系统。
上传 PDF/DOCX/TXT 格式的财报文档，即可用自然语言提问，
系统从文档中检索相关片段，由大语言模型生成带引用来源的回答。

## 功能特性

- 支持 PDF / DOCX / TXT 三种文档格式
- 知识库管理：创建、选择、删除多个知识库
- 语义检索：ChromaDB 向量数据库 + DashScope Embedding
- 重排序优化：DashScope gte-rerank-v2 精排检索结果
- 流式输出：Qwen-max 生成回答逐 token 显示
- 引用溯源：每个回答附带来源文档和页码
- 对话历史：Redis 缓存最近多轮对话（自动降级到内存）
- Docker 一键部署

## 技术栈

| 层 | 技术 |
|-----|--------|
| 前端 | Gradio 5.x |
| 后端 | Python 3.11, LangChain |
| LLM | DashScope Qwen-max, text-embedding-v3, gte-rerank-v2 |
| 向量库 | ChromaDB 0.5+ |
| 元数据库 | MySQL 8.0 |
| 缓存 | Redis 7 |
| 文档解析 | PyMuPDF (PDF), python-docx (DOCX) |
| 评价 | RAGAS (faithfulness, answer_relevancy, context_recall, context_precision) |
| 部署 | Docker Compose |

## 快速启动

### 前置条件

- Docker & Docker Compose
- DashScope API Key（阿里云百炼平台）

### 启动步骤

```bash
# 1. 克隆项目
git clone <repo-url>
cd financial-qa-mvp

# 2. 配置环境变量
cp .env.template .env
# 编辑 .env，填入 DASHSCOPE_API_KEY

# 3. 启动所有服务
docker compose up --build -d

# 4. 访问 Web 界面
# 打开浏览器访问 http://localhost:7860
```

### 使用流程

1. 创建知识库（如 "2024年年报"）
2. 上传财报文档（PDF / DOCX / TXT）
3. 在对话框中输入问题
4. 查看 AI 回答和引用来源

## 项目结构

```
├── src/
│   ├── app.py                 # Gradio UI 入口
│   ├── app_service.py         # 业务逻辑层
│   ├── rag_chain.py           # RAG 问答链路
│   ├── chat_manager.py        # 对话缓存管理
│   ├── models.py              # LLM/Embedding/Rerank 工厂
│   ├── vector_store.py        # ChromaDB 向量存储
│   ├── mysql_db.py            # MySQL CRUD
│   ├── document_loader.py     # 文档加载入口
│   ├── eval_ragas.py          # RAGAS 评估脚本
│   ├── config/
│   │   ├── settings.py        # 环境配置
│   │   ├── prompts.py         # LLM 提示词
│   │   ├── queries.py         # SQL 语句
│   │   └── qa_pairs.py        # RAGAS 测试 QA 对
│   ├── parsers/
│   │   ├── router.py          # 文档路由
│   │   ├── base.py            # 解析器基类
│   │   ├── pymupdf_parser.py  # PDF 解析
│   │   ├── docx_parser.py     # DOCX 解析
│   │   └── txt_parser.py      # TXT 解析
│   └── cli/
│       ├── check_chunks.py    # 分块质量报告
│       └── check_retrieval.py # 检索质量检测
├── tests/                     # 单元测试
├── deploy/                    # Docker 部署配置
├── docker-compose.yml         # Docker 编排
├── Dockerfile                 # 容器构建
└── outputs/                   # RAGAS 评估 CSV 输出
```

## 配置说明

通过 `.env` 文件配置所有参数，主要配置项：

| 配置项 | 说明 | 默认值 |
|---------|------|--------|
| DASHSCOPE_API_KEY | DashScope API Key | （必填） |
| LLM_MODEL | 大语言模型 | qwen-max |
| EMBEDDING_MODEL | 向量化模型 | text-embedding-v3 |
| CHUNK_SIZE | 分块大小 | 512 |
| TOP_K_RETRIEVAL | 检索召回数 | 8 |
| TOP_K_RERANK | 重排序保留数 | 5 |
| MEMORY_WINDOW | 对话窗口大小 | 6 |

## RAGAS 评估

```bash
# 安装开发依赖
pip install -e ".[dev]"

# 运行评估（需先创建知识库并上传测试文档）
python src/eval_ragas.py

# Benchmark 对比不同 chunk_size
python src/eval_ragas.py --chunk-size 512
python src/eval_ragas.py --chunk-size 768
python src/eval_ragas.py --chunk-size 1024
```

## 已知限制

- 扫描件 PDF 暂不支持（无 OCR 能力）
- 表格/数字可能因分块被切断（MVP 只检测不保护）
- RAGAS 评估使用 Qwen-max 作为 judge LLM，存在 self-bias 风险
- 当前只支持单文档格式知识库（不支持多格式混合检索优化）

## 许可证

MIT
```

- [ ] **Step 2: Verify README renders correctly**

```bash
head -30 README.md
# 预期: 项目名称、简介、功能特性等
```

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: complete README with project docs, quick start, and architecture"
```

---

### Task 5: 集成验证与 Benchmark

**Files:** （无创建，仅运行命令）

全流程验证：RAGAS 评估运行、chunk_size benchmark、容器端到端验证。

- [ ] **Step 1: 运行全部单元测试**

```bash
python3 -m pytest tests/ -v --tb=short 2>&1 | tail -20
# 预期: 全部测试通过
```

- [ ] **Step 2: 创建知识库并上传测试文档**

```bash
python3 -c "
from src.app_service import AppService
svc = AppService()
kid, is_new = svc.create_knowledge_base("rag_eval")
print(f"KB created: {kid}, new={is_new}")
result = svc.upload_and_process("rag_eval", "test_docs/sample.txt", "sample.txt")
print(f"Upload: {result}")
"
# 预期: KB 创建成功，文档处理成功
```

- [ ] **Step 3: 运行 RAGAS 评估**

```bash
python3 -m src.eval_ragas --kb-name "rag_eval" --session-id "eval_verify"
# 预期: 四个指标输出，CSV 保存到 outputs/
```

- [ ] **Step 4: 运行 RAGAS benchmark（chunk_size 对比）**

```bash
# 分别测试 512 / 768 / 1024
python3 -m src.eval_ragas --chunk-size 512
python3 -m src.eval_ragas --chunk-size 768
python3 -m src.eval_ragas --chunk-size 1024
# 预期: 三组 CSV 输出到 outputs/ 目录
```

- [ ] **Step 5: 查看评估结果摘要**

```bash
ls -la outputs/
cat outputs/ragas_eval_*.csv | head -3
# 预期: 看到各指标数值和 chunk_size 标记
```

- [ ] **Step 6: 清理临时数据**

```bash
python3 -c "
from src.app_service import AppService
svc = AppService()
for name in ["rag_eval", "ragas_eval_temp_chunk512", "ragas_eval_temp_chunk768", "ragas_eval_temp_chunk1024"]:
    kid = svc.db.get_kb_by_name(name)
    if kid:
        svc.delete_knowledge_base(kid)
        print(f"Cleaned up: {name}")
"
```

- [ ] **Step 7: Iter 6 完成——手工确认，不自动提交**

```bash
git status
# 确认以下文件已修改/创建：
#   src/config/qa_pairs.py
#   src/eval_ragas.py
#   tests/test_eval_ragas.py
#   README.md
#   outputs/ (CSV results)
```

**迭代总结:**
- RAGAS 评估流水线就绪（faithfulness, answer_relevancy, context_recall, context_precision）
- chunk_size benchmark 脚本支持自动创建临时 KB 做对比
- 7 个 QA 对覆盖营收、利润、每股收益、分红、主营业务、股东信息
- README 完善：项目简介、技术栈、快速启动、使用指南、项目结构
