"""replay_trace CLI 单测 — 日志解析（mock 检索，不发真实网络）。"""

from src.cli.replay_trace import _print_snippets, parse_log_line, parse_trace_logs
from src.config import settings


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
    assert fields["kb_id"] == "k1"


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


def test_parse_log_line_strips_trailing_newline_from_last_bool():
    # 从文件逐行读取时末字段带 \n（int 字段因 int() 容白不受影响，
    # 裸 bool 必须剥尾再归一化，否则 rerank 解析成 "true\n" 触发假 drift）
    line = (
        "2026-09-03 12:00:00.000 | INFO    | trace_abc                  "
        "| src.agents.tools.rag_tools:200 - "
        '[retrieval] retrieve replay query="腾讯" query_len=2 kb_id=k1 iteration=1 '
        "top_k=8 dedup_max_per_doc=1 hybrid=false rerank=true\n"
    )
    fields = parse_log_line(line)
    assert fields is not None
    assert fields["rerank"] is True
    assert fields["hybrid"] is False


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


def test_print_snippets_drift_markers(capsys, monkeypatch):
    """_print_snippets 对照"当时值 vs 本次配置"：不同标 drift，一致不标。"""
    fields = {
        "query": "腾讯2024年报",
        "query_len": 6,
        "kb_id": "k1",
        "iteration": 2,
        "top_k": 8,
        "dedup_max_per_doc": 1,
        "hybrid": True,
        "rerank": True,
    }
    # 阶段一：当前配置与行"当时值"不同 → 每个差异参数各出一行 drift
    monkeypatch.setattr(settings, "HYBRID_SEARCH_ENABLED", False)
    monkeypatch.setattr("src.cli.replay_trace.TOP_K_RERANK", 3)
    _print_snippets(fields, [])
    out = capsys.readouterr().out
    assert "drift: row top_k=8 → 本次 top_k=3" in out
    assert "drift: row hybrid=True → 本次 hybrid=False" in out
    # rerank 行值（True）与本次一致，不产生 drift 行
    assert "row rerank" not in out
    # 阶段二：当前配置与行值一致 → 无任何 drift 行
    monkeypatch.setattr(settings, "HYBRID_SEARCH_ENABLED", True)
    monkeypatch.setattr("src.cli.replay_trace.TOP_K_RERANK", 8)
    _print_snippets(fields, [])
    assert "drift:" not in capsys.readouterr().out
