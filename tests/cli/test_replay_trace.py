"""replay_trace CLI 单测 — 日志解析（mock 检索，不发真实网络）。"""

from src.cli.replay_trace import parse_log_line, parse_trace_logs


def test_parse_log_line_extracts_replay_fields():
    line = (
        "2026-09-03 12:00:00.000 | INFO    | trace_abc                  "
        "| sess_1                     | src.agents.tools.rag_tools:200 - "
        '[retrieval] retrieve replay query="腾讯2024年报 营收" query_len=10 '
        "kb_id=k1 iteration=2 top_k=8 dedup_max_per_doc=1 hybrid=true rerank=true"
    )
    fields = parse_log_line(line)
    assert fields is not None
    assert fields["query"] == "腾讯2024年报 营收"
    assert fields["query_len"] == 10
    assert fields["kb_id"] == "k1"
    assert fields["iteration"] == 2
    assert fields["dedup_max_per_doc"] == 1


def test_parse_log_line_segment_agnostic():
    # 旧 5 段格式（无 session 段）也应能解析 —— 按 trace 子串 + " - " 切分
    line = (
        "2026-09-02 12:00:00.000 | INFO    | trace_abc                  "
        "| src.agents.tools.rag_tools:200 - "
        '[retrieval] retrieve replay query="腾讯" query_len=2 kb_id=k1 iteration=1 '
        "top_k=8 dedup_max_per_doc=1 hybrid=false rerank=true"
    )
    fields = parse_log_line(line)
    assert fields is not None
    assert fields["query"] == "腾讯"


def test_parse_trace_logs_filters_by_trace(tmp_path):
    other = (
        "2026-09-03 12:00:00.000 | INFO    | trace_zzz                  | "
        'src.a:1 - [retrieval] retrieve replay query="a" query_len=1 kb_id=k1 '
        "iteration=1 top_k=8 dedup_max_per_doc=1 hybrid=false rerank=true"
    )
    (tmp_path / "app_2026-09-03.log").write_text(
        "2026-09-03 12:00:00.000 | INFO    | trace_abc                  | "
        'src.a:1 - [retrieval] retrieve replay query="腾讯" query_len=2 kb_id=k1 '
        "iteration=1 top_k=8 dedup_max_per_doc=1 hybrid=false rerank=true\n" + other,
        encoding="utf-8",
    )
    rows = parse_trace_logs(str(tmp_path), "trace_abc")
    assert len(rows) == 1
    assert rows[0]["kb_id"] == "k1"
