"""PromptManager 当前日期注入测试。"""

from datetime import UTC, datetime

from src.infra.llm.prompt_manager import PromptManager


def test_system_prompt_contains_today_date():
    """get_system_prompt() 返回的 prompt 应包含今日日期行。"""
    pm = PromptManager(cache_ttl=0)  # 关缓存避免跨天干扰
    prompt = pm.get_system_prompt()
    today = datetime.now(UTC).date()
    expected = f"{today.year}年{today.month}月{today.day}日"
    assert expected in prompt
