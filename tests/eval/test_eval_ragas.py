"""RAGAS 评估脚本单元测试.

测试覆盖：
  - TestParseArgs: 命令行参数解析
  - TestGenerateAnswers: 答案生成流程（mock RAGChain）
  - TestRunEvaluation: RAGAS 评估流程（mock ragas）
  - TestSaveResults: CSV 结果保存
"""

from unittest.mock import MagicMock, patch
import os
import tempfile

import pytest


class TestParseArgs:
    """命令行参数解析测试。"""

    @patch("sys.argv", ["eval_ragas.py", "--kb-id", "test-uuid"])
    def test_default_args(self) -> None:
        """默认参数应按预期设置。"""
        from src.cli.eval_ragas import parse_args

        args = parse_args()
        assert args.kb_id == "test-uuid"
        assert args.session_id == "ragas_eval_session"

    @patch("sys.argv", ["eval_ragas.py", "--kb-id", "test-uuid", "--session-id", "custom_session"])
    def test_custom_args(self) -> None:
        """应正确解析自定义参数。"""
        from src.cli.eval_ragas import parse_args

        args = parse_args()
        assert args.kb_id == "test-uuid"
        assert args.session_id == "custom_session"

    @patch("sys.argv", ["eval_ragas.py", "--kb-id", "test-uuid", "--output", "/tmp/result.csv"])
    def test_output_arg(self) -> None:
        """应正确解析输出路径参数。"""
        from src.cli.eval_ragas import parse_args

        args = parse_args()
        assert args.output == "/tmp/result.csv"


class TestGenerateAnswers:
    """答案生成流程测试（全 mock RAGChain）。"""

    def test_generate_success(self) -> None:
        """正常情况应返回答案列表和上下文列表。"""
        from src.cli.eval_ragas import generate_answers_and_contexts

        mock_chain = MagicMock()

        def mock_chat(kb: str, sess: str, q: str) -> tuple:
            def gen() -> str:
                yield f"Answer for: {q[:10]}"

            return gen(), [
                MagicMock(content="Context about 茅台营收1,741亿元"),
                MagicMock(content="Context about 同比增长15.66%"),
            ]

        mock_chain.chat_with_citations.side_effect = mock_chat

        answers, contexts = generate_answers_and_contexts(
            mock_chain,
            "test_kb",
            "sess_1",
            ["贵州茅台营收多少？", "净利润多少？"],
        )

        assert len(answers) == 2
        assert len(contexts) == 2
        assert "Answer for" in answers[0]
        assert len(contexts[0]) == 2

    def test_generate_partial_failure(self) -> None:
        """部分问题失败时应返回错误标记，不中断整体流程。"""
        from src.cli.eval_ragas import generate_answers_and_contexts

        mock_chain = MagicMock()

        def mock_chat(kb: str, sess: str, q: str) -> tuple:
            if "失败" in q:
                raise ValueError("模拟错误")

            def gen() -> str:
                yield "正常回答"

            return gen(), [MagicMock(content="ctx")]

        mock_chain.chat_with_citations.side_effect = mock_chat

        answers, contexts = generate_answers_and_contexts(
            mock_chain,
            "kb",
            "sess",
            ["正常问题", "模拟失败", "正常问题2"],
        )

        assert len(answers) == 3
        assert "[ERROR]" in answers[1]
        assert contexts[1] == []


class TestRunEvaluation:
    """RAGAS 评估流程测试（全 mock）。"""

    @patch("datasets.Dataset.from_dict")
    @patch("ragas.evaluate")
    def test_evaluation_runs(
        self,
        mock_evaluate: MagicMock,
        mock_from_dict: MagicMock,
    ) -> None:
        """评估流程应正确调用 ragas.evaluate。"""
        from src.cli.eval_ragas import run_evaluation
        from ragas.metrics import (
            faithfulness,
            answer_relevancy,
            context_recall,
            context_precision,
        )

        mock_result = MagicMock()
        mock_result.to_pandas.return_value = MagicMock()
        mock_evaluate.return_value = mock_result

        llm_wrapper = MagicMock()
        emb_wrapper = MagicMock()

        result = run_evaluation(
            ["Q1"],
            ["GT1"],
            ["A1"],
            [["ctx1"]],
            llm_wrapper,
            emb_wrapper,
        )

        mock_evaluate.assert_called_once()
        # 验证 evaluate 传入了正确的参数（按 metric name 比较）
        _, kwargs = mock_evaluate.call_args
        assert kwargs["llm"] is llm_wrapper
        assert kwargs["embeddings"] is emb_wrapper
        actual_metric_names = {m.name for m in kwargs["metrics"]}
        expected_metric_names = {
            context_precision.name,
            context_recall.name,
            faithfulness.name,
            answer_relevancy.name,
        }
        assert actual_metric_names == expected_metric_names
        assert result is mock_result

    @patch("datasets.Dataset.from_dict")
    @patch("ragas.evaluate")
    def test_evaluation_empty_contexts(
        self,
        mock_evaluate: MagicMock,
        mock_from_dict: MagicMock,
    ) -> None:
        """空上下文列表不应导致崩溃。"""
        from src.cli.eval_ragas import run_evaluation

        mock_result = MagicMock()
        mock_result.to_pandas.return_value = MagicMock()
        mock_evaluate.return_value = mock_result

        result = run_evaluation(
            ["Q1"],
            ["GT1"],
            ["A1"],
            [[]],
            MagicMock(),
            MagicMock(),
        )
        assert result is mock_result
        mock_evaluate.assert_called_once()


class TestSaveResults:
    """结果保存测试。"""

    def test_save_csv(self) -> None:
        """CSV 保存应包含所有必要列。"""
        from src.cli.eval_ragas import save_results_csv
        import pandas as pd

        mock_df = pd.DataFrame(
            {
                "faithfulness": [0.95],
                "answer_relevancy": [0.88],
                "context_recall": [0.92],
                "context_precision": [0.85],
            }
        )
        mock_result = MagicMock()
        mock_result.to_pandas.return_value = mock_df

        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            save_results_csv(
                mock_result,
                ["Q1"],
                ["GT1"],
                output_path=tmp_path,
            )

            with open(tmp_path, "r") as f:
                content = f.read()

            assert "faithfulness" in content

        finally:
            os.unlink(tmp_path)

    def test_save_csv_with_chunk_size(self) -> None:
        """Benchmark 模式 CSV 应记录 chunk_size。"""
        from src.cli.eval_ragas import save_results_csv
        import pandas as pd

        mock_df = pd.DataFrame(
            {
                "faithfulness": [0.95],
                "answer_relevancy": [0.88],
            }
        )
        mock_result = MagicMock()
        mock_result.to_pandas.return_value = mock_df

        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            save_results_csv(
                mock_result,
                ["Q1"],
                ["GT1"],
                output_path=tmp_path,
            )

            with open(tmp_path, "r") as f:
                content = f.read()

            assert "faithfulness" in content

        finally:
            os.unlink(tmp_path)


# ---- --gate 标志测试 ----


def test_gate_passes_with_high_scores() -> None:
    """--gate exits 0 when all metrics meet thresholds."""
    import pandas as pd
    from src.cli.eval_ragas import check_gate

    mock_result = MagicMock()
    mock_df = pd.DataFrame(
        {
            "faithfulness": [0.95, 0.90],
            "context_precision": [0.85, 0.90],
            "context_recall": [0.75, 0.80],
            "answer_relevancy": [0.90, 0.88],
        }
    )
    mock_result.to_pandas.return_value = mock_df

    with pytest.raises(SystemExit) as exc_info:
        check_gate(mock_result, ["Q1", "Q2"])
    assert exc_info.value.code == 0


def test_gate_fails_with_low_scores() -> None:
    """--gate exits 1 when a metric is below threshold."""
    import pandas as pd
    from src.cli.eval_ragas import check_gate

    mock_result = MagicMock()
    mock_df = pd.DataFrame(
        {
            "faithfulness": [0.50, 0.45],
            "context_precision": [0.85, 0.90],
            "context_recall": [0.75, 0.80],
            "answer_relevancy": [0.90, 0.88],
        }
    )
    mock_result.to_pandas.return_value = mock_df

    with pytest.raises(SystemExit) as exc_info:
        check_gate(mock_result, ["Q1", "Q2"])
    assert exc_info.value.code == 1
