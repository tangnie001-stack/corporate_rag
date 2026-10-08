"""ReplySink 的可选 feedback 参数：长连接透传、回调忽略。"""

import pytest

from src.channels.wecom.callback import _CallbackSink
from src.channels.wecom.long_connection import _WsSink


class _FakeSdkClient:
    """伪 SDK 客户端：记录 reply_stream 的全部参数。"""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def reply_stream(
        self,
        frame: dict,
        stream_id: str,
        content: str,
        finish: bool = False,
        feedback: dict | None = None,
    ) -> dict:
        self.calls.append(
            {
                "frame": frame,
                "stream_id": stream_id,
                "content": content,
                "finish": finish,
                "feedback": feedback,
            }
        )
        return {}


@pytest.mark.asyncio
async def test_ws_sink_passes_feedback_to_sdk():
    client = _FakeSdkClient()
    sink = _WsSink(client, {"body": {}}, "stream-1")  # type: ignore[arg-type]

    await sink.reply_stream("甲", False, feedback={"id": "trace_1"})

    assert client.calls[0]["feedback"] == {"id": "trace_1"}


@pytest.mark.asyncio
async def test_ws_sink_without_feedback_sends_none():
    client = _FakeSdkClient()
    sink = _WsSink(client, {"body": {}}, "stream-1")  # type: ignore[arg-type]

    await sink.reply_stream("甲", False)

    assert client.calls[0]["feedback"] is None


@pytest.mark.asyncio
async def test_callback_sink_accepts_and_ignores_feedback():
    sink = _CallbackSink()

    await sink.reply_stream("甲", True, feedback={"id": "trace_1"})

    assert sink.reply is not None
    assert sink.reply.content == "甲"
    assert sink.reply.finish is True
