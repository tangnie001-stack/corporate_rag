"""企微编排单测的共享替身与工厂。

集中管理驱动替身与 `WECOM_BOTS` 环境装配，供 `tests/services/` 下多个测试模块
通过绝对导入引用（避免跨模块重复定义，见 tests/api/mock_data.py 惯例）。

命名沿用与 test_wecom_service.py 一致的私有前缀（`_`），保证既有引用无需改名。
"""

import json

from src.services import wecom_service


def _bots_env(monkeypatch, items: list[tuple[str, str, str]]) -> None:
    """把 (key, bot_id, secret) 列表写进 WECOM_BOTS 环境变量（单行 JSON）。"""
    data = [{"key": k, "bot_id": i, "secret": s} for (k, i, s) in items]
    monkeypatch.setenv("WECOM_BOTS", json.dumps(data, separators=(",", ":")))


class _StubDriver:
    """最小驱动替身：start/stop 只置位，is_ready 由构造参数决定。"""

    name = "wecom_long_connection"

    def __init__(
        self,
        bot_id: str,
        secret: str,
        handler: object,
        *,
        fail: bool = False,
        ready: bool = True,
    ):
        self.bot_id = bot_id
        self.secret = secret
        self.handler = handler
        self.started = False
        self.stopped = False
        self.is_ready = ready
        self._fail = fail

    async def start(self) -> None:
        """模拟建连：fail=True 时抛错（连接失败），否则置 started。"""
        if self._fail:
            raise RuntimeError("connect failed")
        self.started = True

    async def stop(self) -> None:
        """模拟断开：置 stopped。"""
        self.stopped = True


class _StopFailingDriver(_StubDriver):
    """stop() 恒抛错的替身。"""

    async def stop(self) -> None:
        """模拟断开失败，用于断言 stop() 容错。"""
        raise RuntimeError("disconnect failed")


class _StopFailingByBotIdDriver(_StubDriver):
    """stop() 按 bot_id 决定是否抛错：仅指定台失败，其余台正常断开。"""

    def __init__(self, bot_id: str, secret: str, handler: object, *, failing_id: str):
        super().__init__(bot_id, secret, handler)
        self._failing_id = failing_id

    async def stop(self) -> None:
        """仅 failing_id 台抛错，其余台正常置 stopped。"""
        if self.bot_id == self._failing_id:
            raise RuntimeError("disconnect failed")
        self.stopped = True


def _install_stub(
    monkeypatch,
    *,
    fail_ids: set[str] | None = None,
    not_ready_ids: set[str] | None = None,
):
    """把 LongConnectionDriver 换成 stub 工厂；返回创建记录列表。

    Args:
        fail_ids: 这些 bot_id 的 start() 抛错（模拟连接失败）
        not_ready_ids: 这些 bot_id 连接成功但**未认证就绪**（is_ready=False）
    """
    created: list[_StubDriver] = []
    fail_ids = fail_ids or set()
    not_ready_ids = not_ready_ids or set()

    def _factory(bot_id, secret, handler):
        driver = _StubDriver(
            bot_id,
            secret,
            handler,
            fail=bot_id in fail_ids,
            ready=bot_id not in not_ready_ids,
        )
        created.append(driver)
        return driver

    monkeypatch.setattr(wecom_service, "LongConnectionDriver", _factory)
    return created
