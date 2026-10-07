"""守卫：长连接驱动不得调用官方 SDK 的 run()（它自建事件循环，与 FastAPI 冲突）。"""

from pathlib import Path

_DRIVER_SOURCE = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "channels"
    / "wecom"
    / "long_connection.py"
)


def test_driver_source_does_not_call_run():
    source = _DRIVER_SOURCE.read_text(encoding="utf-8")
    assert ".run(" not in source
