"""长连接驱动单测的共享替身：mock 官方 aibot.WSClient，不发真实网络。

以普通模块（非 `test_` 前缀）承载，pytest 不收集；供「连接/派发/被顶」与
「认证/就绪/错误判据」两组测试 import 复用，避免替身定义重复。调用方只需传入
`monkeypatch`，替换 `lc.WSClient` 的动作由本模块完成。
"""

from typing import Any

from src.channels.wecom import long_connection as lc


class _FakeClient:
    """伪 WSClient：记录构造参数、注册的处理器与回复调用。

    默认模拟"健康且已认证"：`connect()` 建立连接后同步 `emit("authenticated")`，
    以对标真实 SDK 认证成功的默认路径（驱动在 connect() 之前已注册该处理器）。
    """

    def __init__(self, options: Any):
        """初始化，仅登记构造参数与空记录结构。

        Args:
            options: 驱动传入的 `WSClientOptions`（原样保留供断言）
        """
        self.options = options
        self.handlers: dict[str, Any] = {}
        self.connected = False
        self.disconnect_calls = 0
        self.replies: list[tuple[Any, str, str, bool]] = []

    def on(self, event: str, f: Any = None) -> Any:
        """登记事件处理器并原样返回（对标 pyee 的 `on`）。"""
        self.handlers[event] = f
        return f

    def emit(self, event: str, payload: Any = None) -> None:
        """同步触发已注册处理器（对标 pyee 的行为）。"""
        handler = self.handlers.get(event)
        if handler is None:
            return
        if payload is None:
            handler()
            return
        handler(payload)

    async def connect(self) -> "_FakeClient":
        """建立连接并默认发一次 `authenticated`（已认证的默认路径）。"""
        self.connected = True
        self.emit("authenticated")
        return self

    def disconnect(self) -> None:
        """记录一次断开调用并置连接状态为 False。"""
        self.disconnect_calls += 1
        self.connected = False

    async def reply_stream(
        self,
        frame: dict,
        stream_id: str,
        content: str,
        finish: bool = False,
        feedback: dict | None = None,
    ) -> dict:
        """记录流式回复调用并返回空结果（不透传 feedback）。"""
        self.replies.append((frame, stream_id, content, finish))
        return {}


def _patch_client(monkeypatch):
    """把 `lc.WSClient` 换成默认（已认证）的伪 client 工厂。

    Args:
        monkeypatch: pytest 的 monkeypatch 夹具

    Returns:
        持有本次构造的唯一 client 的 holder（键 `"client"`）
    """
    holder: dict[str, _FakeClient] = {}

    def _factory(options):
        client = _FakeClient(options)
        holder["client"] = client
        return client

    monkeypatch.setattr(lc, "WSClient", _factory)
    return holder


class _FakeClientWithAuth(_FakeClient):
    """薄子类：仅当 `emit_auth=False` 时抑制 connect 时的认证事件。

    其余（emit / disconnect_calls / disconnect）均继承基类；`emit_auth=False`
    变体供超时与凭证失败用例复用。
    """

    def __init__(self, options: Any, *, emit_auth: bool = True):
        """初始化。

        Args:
            options: 驱动传入的 `WSClientOptions`
            emit_auth: `connect()` 时是否发 `authenticated`（默认 True）
        """
        super().__init__(options)
        self._emit_auth = emit_auth

    async def connect(self) -> "_FakeClientWithAuth":
        """建立连接，并按 `emit_auth` 决定是否发一次 `authenticated`。"""
        self.connected = True
        if self._emit_auth:
            self.emit("authenticated")
        return self


def _auth_patch(monkeypatch, *, emit_auth: bool):
    """把 `lc.WSClient` 换成可控制认证事件的伪 client 工厂。

    Args:
        monkeypatch: pytest 的 monkeypatch 夹具
        emit_auth: 传给伪 client 的 `connect()` 认证开关

    Returns:
        持有本次构造的唯一 client 的 holder（键 `"client"`）
    """
    holder: dict[str, _FakeClientWithAuth] = {}

    def _factory(options):
        client = _FakeClientWithAuth(options, emit_auth=emit_auth)
        holder["client"] = client
        return client

    monkeypatch.setattr(lc, "WSClient", _factory)
    return holder
