"""prompt_manager 模块日期注入测试。"""

from datetime import UTC, datetime
from unittest.mock import patch

import src.infra.llm.prompt_manager as pm_module

# 固定时刻：UTC 2026-03-04 17:30 == 北京时间 2026-03-05 01:30。
# 若误用 UTC 日期，会得到 2026年3月4日（落后一天）。
_FIXED_UTC = datetime(2026, 3, 4, 17, 30, tzinfo=UTC)
_BEIJING_DATE_LINE = "\n今天是 2026年3月5日。\n"
_UTC_DATE_LINE = "\n今天是 2026年3月4日。\n"


class _FakeDatetime:
    """datetime 替身：now(tz) 返回固定时刻在指定时区下的表示。"""

    @staticmethod
    def now(tz=None):
        return _FIXED_UTC.astimezone(tz) if tz is not None else _FIXED_UTC


def _freeze_datetime():
    """冻结 prompt_manager 模块内的 datetime，使日期注入结果确定。"""
    return patch.object(pm_module, "datetime", _FakeDatetime)


def test_with_current_date_injects_beijing_date_line() -> None:
    """_with_current_date 注入北京时间日期行，而非 UTC 日期。"""
    from src.infra.llm.prompt_manager import _with_current_date

    with _freeze_datetime():
        prompt = _with_current_date("正文")
    assert _BEIJING_DATE_LINE in prompt
    assert _UTC_DATE_LINE not in prompt


def test_with_current_date_is_idempotent() -> None:
    """重复调用日期行只出现一次。"""
    from src.infra.llm.prompt_manager import _with_current_date

    with _freeze_datetime():
        once = _with_current_date("正文")
        twice = _with_current_date(once)
    assert once == twice
    assert once.count(_BEIJING_DATE_LINE) == 1
