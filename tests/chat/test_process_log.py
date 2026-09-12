"""process_log 分拣函数测试——fixture 源自 trace_3157b559 真实帧序裁剪。"""

import json
from pathlib import Path

from src.chat.process_log import build_process_events, serialize_process

FIXTURE = Path(__file__).parent.parent / "fixtures" / "process_frames_sample.json"


def _load_events() -> list[dict]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


class TestBuildProcessEvents:
    def test_structure_and_version(self):
        result, _purified = build_process_events(_load_events())
        assert result["format_version"] == 1
        assert all(
            "seq" in e and "type" in e and "payload" in e for e in result["events"]
        )
        assert [e["seq"] for e in result["events"]] == list(
            range(1, len(result["events"]) + 1)
        )

    def test_answer_tokens_excluded(self):
        result, _purified = build_process_events(_load_events())
        text = json.dumps(result, ensure_ascii=False)
        # 末轮 answer 的 token 不入 process（answer 列承载），避免历史回放正文重复
        assert "详细计算和分析" not in text
        assert "腾讯2025年毛利率、净利率、ROE计算与趋势解读" not in text

    def test_preamble_tokens_kept(self):
        result, _purified = build_process_events(_load_events())
        preambles = [e for e in result["events"] if e["type"] == "preamble"]
        assert len(preambles) == 2
        assert "参考文档为空" in preambles[0]["payload"]["text"]
        assert "知识库中只有2024年的数据" in preambles[1]["payload"]["text"]

    def test_purified_answer_is_last_pending(self):
        # 净化正文 = 末次待定区（末轮 answer 正文流），不含旁白文本
        _result, purified = build_process_events(_load_events())
        assert "详细计算和分析" in purified
        assert "参考文档为空" not in purified
        assert "知识库中只有2024年的数据" not in purified

    def test_excluded_types_dropped(self):
        result, _purified = build_process_events(_load_events())
        types = {e["type"] for e in result["events"]}
        assert not types & {
            "model_info",
            "abstention",
            "done",
            "error",
            "citation",
            "token",
        }

    def test_status_events_kept_in_order(self):
        result, _purified = build_process_events(_load_events())
        statuses = [e for e in result["events"] if e["type"] == "status"]
        assert len(statuses) == 7
        assert statuses[1]["payload"]["detail"].startswith("query=腾讯2025年毛利率")

    def test_empty_log(self):
        assert build_process_events([]) == ({"format_version": 1, "events": []}, "")

    def test_chitchat_no_tool_all_tokens_dropped(self):
        # 纯闲聊：无工具调用，全部 token 为正文 → process 为空且净化正文为全文
        events = [{"type": "token", "payload": {"token": "今天天气不错"}}]
        result, purified = build_process_events(events)
        assert result["events"] == []
        assert purified == "今天天气不错"


def test_turn_provenance_statuses_survive_exclusion():
    """回归：来源声明（stage=turn_agent/turn_skill）不被 _EXCLUDED_TYPES 吞掉。

    design D6/Risks —— 若后续改动排除集，此断言必须变红。
    """
    events = [
        {
            "type": "status",
            "payload": {"stage": "turn_agent", "message": "当前使用了 财务专家"},
        },
        {
            "type": "status",
            "payload": {
                "stage": "turn_skill",
                "message": "成功加载 skills：finance-qa",
            },
        },
        {"type": "done", "payload": {}},
    ]
    result, _purified = build_process_events(events)
    kept = [e for e in result["events"] if e["type"] == "status"]
    assert [e["payload"]["stage"] for e in kept] == ["turn_agent", "turn_skill"]


class TestSerializeProcess:
    def test_returns_json_and_purified(self):
        process_json, purified = serialize_process(_load_events())
        parsed = json.loads(process_json)
        assert parsed["format_version"] == 1
        assert "详细计算和分析" in purified
        assert "参考文档为空" not in purified
