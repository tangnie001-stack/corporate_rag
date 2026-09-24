"""事件定义层单测 — 注册表一致性 + import 校验。"""

import dataclasses

import pytest

import src.core.log_events as le
from src.config import settings


def test_core_events_registered():
    # 每个 Event 成员必须有 EventSpec，且 spec.name 与成员值一致
    for member in le.Event:
        spec = le.EVENT_SPECS[member.value]
        assert spec.name == member.value
        assert spec.prefix in le.LOG_PREFIXES
        assert spec.level in {"info", "warning", "error"}


def test_signal_enum_covers_all():
    assert {s.value for s in le.Signal} == {
        "reretrieve",
        "to_web",
        "abstain_after_retrieve",
        "cited",
        "empty_result",
        "invalid_citation",
    }


def test_settings_removed_retrieval_max_per_doc():
    # Task 3 钉桩：RETRIEVAL_MAX_PER_DOC 已删除，settings 上不应再有该属性
    assert not hasattr(settings, "RETRIEVAL_MAX_PER_DOC")


def test_replay_event_removed_dedup_max_per_doc_field():
    # Task 3 钉桩：ReplayEvent 字段集不再包含 dedup_max_per_doc
    field_names = {f.name for f in dataclasses.fields(le.ReplayEvent)}
    assert "dedup_max_per_doc" not in field_names


def test_replay_event_fields():
    r = le.ReplayEvent(
        query="腾讯2024年报",
        query_len=6,
        kb_id="k1",
        iteration=2,
        top_k=8,
        hybrid=True,
        rerank=True,
    )
    assert r.query_len == 6 and r.hybrid is True


def test_registry_import_validation_detects_drift():
    # 模拟 Event 新增成员但未登记 spec → import 校验必须抛
    # 直接测校验函数（避免真的改类）
    with pytest.raises(AssertionError):
        le._validate_registry(events={"a"}, specs={})


def test_turn_provenance_events_registered():
    """来源与观测事件两处同名登记，且前缀/级别合法（design D11）。"""
    expected = {
        "agent resolved": ("session", "info"),
        "prompt assembled": ("llm", "info"),
        "prompt messages": ("agent", "info"),
        "skill injected": ("session", "info"),
        "skill dispatch": ("session", "info"),
    }
    for name, (prefix, level) in expected.items():
        member = le.Event(name)
        assert member.value == name
        spec = le.EVENT_SPECS[name]
        assert spec.prefix == prefix
        assert spec.level == level


def test_model_turn_spec_includes_temperature_fields():
    """model turn 扩 temperature / temp_source / kb_bound（design D11 #1）。"""
    fields = le.EVENT_SPECS["model turn"].fields
    assert "temperature" in fields
    assert "temp_source" in fields
    assert "kb_bound" in fields
