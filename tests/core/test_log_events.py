"""事件定义层单测 — 注册表一致性 + import 校验。"""

import pytest

import src.core.log_events as le


def test_core_events_registered():
    # 每个 Event 成员必须有 EventSpec，且 spec.name 与成员值一致
    for member in le.Event:
        spec = le.EVENT_SPECS[member.value]
        assert spec.name == member.value
        assert spec.prefix in le.LOG_PREFIXES
        assert spec.level in {"info", "warning", "error"}


def test_signal_enum_covers_six():
    assert {s.value for s in le.Signal} == {
        "reretrieve",
        "to_web",
        "abstain_after_retrieve",
        "unsupported",
        "cited",
        "empty_result",
    }


def test_replay_event_fields():
    r = le.ReplayEvent(
        query="腾讯2024年报",
        query_len=6,
        kb_id="k1",
        iteration=2,
        top_k=8,
        dedup_max_per_doc=1,
        hybrid=True,
        rerank=True,
    )
    assert r.query_len == 6 and r.hybrid is True


def test_registry_import_validation_detects_drift():
    # 模拟 Event 新增成员但未登记 spec → import 校验必须抛
    # 直接测校验函数（避免真的改类）
    with pytest.raises(AssertionError):
        le._validate_registry(events={"a"}, specs={})
