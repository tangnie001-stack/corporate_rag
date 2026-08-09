"""PromptManager 当前日期注入测试。"""

from datetime import UTC, datetime
from unittest.mock import patch

import src.infra.llm.prompt_manager as pm_module
from src.infra.llm.prompt_manager import PromptManager

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


def test_system_prompt_injects_beijing_date_line():
    """get_system_prompt() 应注入北京时间日期行，而非 UTC 日期。"""
    pm = PromptManager(cache_ttl=0)  # 关缓存避免跨天干扰
    with _freeze_datetime():
        prompt = pm.get_system_prompt()
    assert _BEIJING_DATE_LINE in prompt


def test_system_prompt_not_using_utc_date():
    """固定时刻 UTC 日期落后一天，prompt 不应出现 UTC 日期行。"""
    pm = PromptManager(cache_ttl=0)
    with _freeze_datetime():
        prompt = pm.get_system_prompt()
    assert _UTC_DATE_LINE not in prompt


def test_system_prompt_date_line_idempotent():
    """连续调用两次 get_system_prompt()，日期行只出现一次。"""
    pm = PromptManager(cache_ttl=60)
    with _freeze_datetime():
        p1 = pm.get_system_prompt()
        p2 = pm.get_system_prompt()
    assert p1 == p2
    assert p1.count(_BEIJING_DATE_LINE) == 1
