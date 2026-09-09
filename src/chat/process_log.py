"""过程事件日志分拣——把生成期采集的原始事件序列整理为可回放的过程元素。

分拣规则（design.md D1/D2）：
- token 帧累积为待定区，遇非 token 事件固化为 preamble（旁白）元素；
  日志末次的待定区是末轮 answer 的正文流，丢弃（正文由 answer 列承载）。
- model_info / abstention / done / error / citation 不入 process：
  模型名与拒答语义由既有列承载，引用由 sources 列承载，终态非可见元素。
"""

import json
from typing import Any

PROCESS_FORMAT_VERSION = 1

_EXCLUDED_TYPES = frozenset({"model_info", "abstention", "done", "error", "citation"})


def build_process_events(events_log: list[dict[str, Any]]) -> dict[str, Any]:
    """把采集的原始事件序列分拣为过程元素序列。

    Args:
        events_log: 采集的事件列表，元素为 {"type": str, "payload": dict}（按到达顺序）

    Returns:
        {"format_version": 1, "events": [{"seq", "type", "payload"}, ...]}，
        seq 从 1 递增；answer 段 token 帧与排除类型不出现在结果中
    """
    events: list[dict[str, Any]] = []
    pending_tokens: list[str] = []

    for ev in events_log:
        ev_type = ev["type"]
        if ev_type in _EXCLUDED_TYPES:
            # 排除类型截断待定区：其后的 token 属于正文段（answer 列承载）
            pending_tokens = []
            continue
        if ev_type == "token":
            pending_tokens.append(ev["payload"].get("token", ""))
            continue
        # 非 token 事件到达：待定 token 固化为旁白块（若非空）
        if pending_tokens:
            events.append(
                {"type": "preamble", "payload": {"text": "".join(pending_tokens)}}
            )
            pending_tokens = []
        events.append({"type": ev_type, "payload": ev["payload"]})

    # 循环结束仍持有的待定区 = 末轮 answer 正文流 → 丢弃，不入 process
    for seq, event in enumerate(events, start=1):
        event["seq"] = seq
    return {"format_version": PROCESS_FORMAT_VERSION, "events": events}


def serialize_process(events_log: list[dict[str, Any]]) -> str:
    """序列化 process 列内容（落库用），供调用方直传。"""
    return json.dumps(build_process_events(events_log), ensure_ascii=False)
