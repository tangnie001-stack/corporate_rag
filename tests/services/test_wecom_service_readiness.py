"""企微服务就绪语义单测：锚点口径、未就绪也可 stop、并行启动。

与 test_wecom_service.py 共用 tests/services/wecom_service_fakes.py 的替身。
"""

import asyncio

import pytest
from loguru import logger

from src.config import settings
from src.services import wecom_service
from tests.services.wecom_service_fakes import (
    _bots_env,
    _install_stub,
    _StubDriver,
)


@pytest.mark.asyncio
async def test_anchor_counts_only_authenticated_ready(monkeypatch):
    """锚点的 n= 只计**认证就绪**台数；未就绪台仍登记入 _drivers（供 stop 关闭）。"""
    monkeypatch.setattr(settings, "WECOM_BOT_ENABLED", True)
    monkeypatch.setattr(settings, "WECOM_BOT_MODE", "long_connection")
    _bots_env(monkeypatch, [("dev", "aibA", "s1"), ("support", "aibB", "s2")])
    _install_stub(monkeypatch, not_ready_ids={"aibB"})

    messages: list[str] = []
    sink_id = logger.add(lambda m: messages.append(m), level="INFO")
    try:
        await wecom_service.start()
    finally:
        logger.remove(sink_id)

    anchor = [m for m in messages if "bots connected" in m]
    assert len(anchor) == 1
    assert "n=1" in anchor[0]
    assert "total=2" in anchor[0]
    assert isinstance(wecom_service.get_driver("support"), _StubDriver)
    assert wecom_service.is_ready("support") is False
    assert wecom_service.is_ready("dev") is True


@pytest.mark.asyncio
async def test_not_ready_driver_is_closed_on_stop(monkeypatch):
    """未认证就绪者也要能被 stop() 关闭（防关机泄漏）。"""
    monkeypatch.setattr(settings, "WECOM_BOT_ENABLED", True)
    monkeypatch.setattr(settings, "WECOM_BOT_MODE", "long_connection")
    _bots_env(monkeypatch, [("dev", "aibA", "s1")])
    created = _install_stub(monkeypatch, not_ready_ids={"aibA"})

    await wecom_service.start()
    await wecom_service.stop()

    assert created[0].stopped is True


@pytest.mark.asyncio
async def test_drivers_start_concurrently(monkeypatch):
    """三台并行启动：用"同时在建连中的驱动数"判定（不靠墙钟，避免负载抖动）。"""
    monkeypatch.setattr(settings, "WECOM_BOT_ENABLED", True)
    monkeypatch.setattr(settings, "WECOM_BOT_MODE", "long_connection")
    _bots_env(
        monkeypatch,
        [("a", "aibA", "s1"), ("b", "aibB", "s2"), ("c", "aibC", "s3")],
    )
    tracker: dict[str, int] = {"in_flight": 0, "max_in_flight": 0}

    class _TrackingStub(_StubDriver):
        async def start(self) -> None:
            """记录同时在飞的建连数；让出控制权使并发真正重叠。"""
            tracker["in_flight"] += 1
            tracker["max_in_flight"] = max(
                tracker["max_in_flight"], tracker["in_flight"]
            )
            await asyncio.sleep(0)
            self.started = True
            tracker["in_flight"] -= 1

    def _factory(bot_id, secret, handler):
        return _TrackingStub(bot_id, secret, handler)

    monkeypatch.setattr(wecom_service, "LongConnectionDriver", _factory)

    await wecom_service.start()

    assert tracker["max_in_flight"] == 3  # 串行启动时恒为 1
