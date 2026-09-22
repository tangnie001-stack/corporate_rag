"""症状指标聚合的单元测试：只喂合成日志行，不读真实日志目录。

三个指标的口径见 design.md D5「观测」层；日志行格式见 src/core/logging.py:38。
"""

from collections import Counter

from src.cli.symptom_metrics import SymptomStats, collect, format_report


def _line(trace_id: str, message: str, level: str = "INFO") -> str:
    """构造一行合法日志（四段 + module:func:line - message）。"""
    ts = "2026-09-22 10:00:00.000"
    return f"{ts}|{level}|{trace_id}|sess-1|agent_node:make_agent_model_node:259 - {message}\n"


def test_counts_iteration_limit_per_trace(tmp_path):
    """触顶率按 trace 计：一个 trace 出现多条触顶也只算一次。

    分母口径 = **有 agent 行为的 trace 数**（触顶 ∪ 检索 ∪ 完整性检查），不是"全部请求数"
    —— 日志里没有"请求开始"的统一锚点。所以不匹配任一已知事件的行（t4）不计入分母。
    """
    log = tmp_path / "app_2026-09-22.log"
    log.write_text(
        _line("t1", "[agent] iteration limit query=q1 iteration=8", "WARNING")
        + _line("t1", "[agent] iteration limit query=q1 iteration=9", "WARNING")
        + _line("t2", "[agent] iteration limit query=q2 iteration=8", "WARNING")
        + _line("t3", "[agent] iteration limit query=q3 iteration=10", "WARNING")
        + _line("t4", "[agent] some other event query=q4"),
        encoding="utf-8",
    )
    stats = collect(str(tmp_path))
    assert stats.traces_total == 3
    assert stats.traces_iteration_limit == 3


def test_counts_retrieve_calls_per_trace(tmp_path):
    """每请求 retrieve_kb 次数 = 该 trace 下 retrieve done 的行数。

    ⚠ `retrieve_counts` 是"每请求一个样本"的列表（长度 = 参与统计的 trace 数），
    不是"次数 → 请求数"的映射，所以断言必须走 Counter 或排序，**不能按下标取值**。
    t3 只出现 iteration limit（无检索）→ 计入 seen_traces 但检索次数为 0。
    """
    log = tmp_path / "app_2026-09-22.log"
    log.write_text(
        _line(
            "t1",
            "[retrieval] retrieve done iteration=1 query=q1 result_count=3 latency_ms=42",
        )
        + _line(
            "t1",
            "[retrieval] retrieve done iteration=2 query=q1' result_count=0 latency_ms=38",
        )
        + _line(
            "t2",
            "[retrieval] retrieve done iteration=1 query=q2 result_count=5 latency_ms=51",
        )
        + _line("t3", "[agent] iteration limit query=q3 iteration=8", "WARNING"),
        encoding="utf-8",
    )
    stats = collect(str(tmp_path))
    assert Counter(stats.retrieve_counts) == {2: 1, 1: 1, 0: 1}


def test_counts_empty_answers_over_completeness_checks(tmp_path):
    """answer_len=0 占比的分母是 completeness check 的条数，不是 trace 数。"""
    log = tmp_path / "app_2026-09-22.log"
    log.write_text(
        _line(
            "t1",
            "[verify] completeness check kb_id=kb1 required=2023 missing=2023 answer_len=0",
        )
        + _line(
            "t2",
            "[verify] completeness check kb_id=kb1 required= missing= answer_len=412",
        )
        + _line(
            "t3",
            "[verify] completeness check kb_id=kb1 required=2022 missing= answer_len=0",
        ),
        encoding="utf-8",
    )
    stats = collect(str(tmp_path))
    assert stats.completeness_checks == 3
    assert stats.empty_answers == 2


def test_ignores_malformed_lines(tmp_path):
    """格式不符的行不得让整个脚本崩，也不得污染计数。"""
    log = tmp_path / "app_2026-09-22.log"
    log.write_text(
        "this is not a log line\n"
        + "\n"
        + _line(
            "t1",
            "[retrieval] retrieve done iteration=1 query=q1 result_count=1 latency_ms=9",
        ),
        encoding="utf-8",
    )
    stats = collect(str(tmp_path))
    assert stats.traces_total == 1
    assert stats.retrieve_counts == [1]


def test_merges_multiple_log_files(tmp_path):
    """跨日期的多个 app_*.log 合并统计。"""
    (tmp_path / "app_2026-09-21.log").write_text(
        _line(
            "t1",
            "[retrieval] retrieve done iteration=1 query=q1 result_count=1 latency_ms=9",
        ),
        encoding="utf-8",
    )
    (tmp_path / "app_2026-09-22.log").write_text(
        _line(
            "t1",
            "[retrieval] retrieve done iteration=2 query=q1' result_count=1 latency_ms=9",
        )
        + _line(
            "t2",
            "[verify] completeness check kb_id=kb1 required= missing= answer_len=0",
        ),
        encoding="utf-8",
    )
    stats = collect(str(tmp_path))
    assert Counter(stats.retrieve_counts) == {2: 1, 0: 1}
    assert stats.completeness_checks == 1
    assert stats.empty_answers == 1


def test_format_report_reports_all_three_metrics():
    """报告必须同时含三个指标，且触顶率/空答率以百分比呈现。"""
    stats = SymptomStats(
        traces_total=4,
        traces_iteration_limit=1,
        retrieve_counts=[0, 1, 1, 2],
        completeness_checks=4,
        empty_answers=1,
    )
    text = format_report(stats)
    assert "iteration limit 触顶率：25.0%（1/4）" in text
    assert "answer_len=0 占比：25.0%（1/4）" in text
    assert "每请求 retrieve_kb 调用次数分布" in text
    assert "  0: 1" in text  # 分布里"调用 0 次的请求数 = 1"


def test_format_report_handles_empty_input():
    """零日志时不得除零崩溃。"""
    text = format_report(SymptomStats())
    assert "0" in text
