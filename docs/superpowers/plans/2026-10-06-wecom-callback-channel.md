# 企微智能机器人回调通道（最小接入）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增企微智能机器人「URL 回调」接入通道，打通"验证 URL → 收 @ 消息 → 解密 → 加密回复"链路（回复写死，不接业务），并抽象出可切换长连接的通道驱动接口。

**Architecture:** 新增顶层 `src/channels/` 通道包：`base.py` 放通用抽象（`InboundMessage`/`ReplySink`/`MessageHandler`/`ChannelDriver`），`wecom/parse.py` 做入站解析（回调与长连接共用），`wecom/crypto.py` 做加解密，`wecom/callback.py` 做回调传输（验签/解密/加密回包/HTTP 响应）。`services/wecom_service.py` 装配驱动并注入默认 handler；`api/wecom.py` 是极薄路由（只透传 `Request`，不声明类型化参数）。

**Tech Stack:** Python 3.12 / FastAPI 0.138 / Starlette / pycryptodome（新增）/ pytest。

**Spec:** `docs/superpowers/specs/2026-10-06-wecom-callback-channel-design.md`（本 plan 的每个"为什么"以它为准，执行者需同时读它）

## Global Constraints

- 分层：`api/` 不得 import `infra/`；本改动链路为 `api/wecom.py → services/wecom_service.py → channels/wecom/*`。`api/` 引 `config.const` 常量已有先例，允许。
- 注释/文档一律**中文**；`dataclass` 每个字段必须加**行内注释**；不用三元表达式（写完整 if/else）；类型不确定时用显式 `isinstance`，不用 `getattr(x, "a", default)`。
- 日志：事件消息用英文 `k=v` + `[层名]` 前缀（本改动用 `[wecom]`）。
- 单文件 ≤ 400 行；单函数 ≤ 80 行。
- **本 worktree 内禁用 `git add -A` / `git add .`**（`.venv` 是 symlink，`.gitignore` 的 `.venv/` 只匹配目录，会被误带）；一律显式路径。
- 测试命令（宿主侧、从 **worktree 根目录** 运行）：`POSTGRES_HOST=localhost pytest tests/ -v`。
- 新增运行时依赖 `pycryptodome`；**只加 `pyproject.toml`，不在本 plan 内构建镜像**（部署影响见 spec §6）。
- 回调 URL 对外地址固定为 `/api/wecom/callback`（Nginx 只转发 `/api/`）。
- 智能机器人场景 `ReceiveId` 恒为 `""`；PKCS#7 填充块恒为 **32**。

---

### Task 1: 通道抽象与入站解析（`channels/base.py` + `channels/wecom/parse.py`）

**Files:**
- Create: `src/channels/__init__.py`
- Create: `src/channels/base.py`
- Create: `src/channels/wecom/__init__.py`
- Create: `src/channels/wecom/parse.py`
- Create: `tests/channels/__init__.py`
- Test: `tests/channels/test_parse.py`

**Interfaces:**
- Consumes: 无
- Produces:
  - `InboundMessage`（frozen dataclass；字段 `msgid, aibotid, chatid, chattype, from_userid, msgtype, text, event_type, raw`）
  - `ReplySink`（Protocol，方法 `async reply_stream(content: str, finish: bool) -> None`）
  - `MessageHandler`（Protocol，`async __call__(msg: InboundMessage, sink: ReplySink) -> None`）
  - `ChannelDriver`（Protocol，属性 `name: str`，方法 `async start() -> None` / `async stop() -> None`）
  - `InboundParseError(ValueError)`
  - `parse_inbound(plain: dict) -> InboundMessage`

- [ ] **Step 1: 写失败测试**

创建 `tests/channels/__init__.py`（空文件）与 `tests/channels/test_parse.py`：

```python
"""入站解析单测：消息/事件分流与字段容错。"""

import pytest

from src.channels.wecom.parse import InboundParseError, parse_inbound


def test_text_message_extracts_content():
    plain = {
        "msgid": "M1",
        "aibotid": "BOT1",
        "chatid": "C1",
        "chattype": "group",
        "from": {"userid": "U1"},
        "msgtype": "text",
        "text": {"content": "@RobotA hello"},
    }
    msg = parse_inbound(plain)
    assert msg.msgid == "M1"
    assert msg.chattype == "group"
    assert msg.from_userid == "U1"
    assert msg.msgtype == "text"
    assert msg.text == "@RobotA hello"
    assert msg.event_type is None


def test_event_message_sets_event_type_and_no_text():
    plain = {
        "msgid": "M2",
        "aibotid": "BOT1",
        "chattype": "single",
        "from": {"userid": "U2"},
        "msgtype": "event",
        "event": {"eventtype": "enter_chat"},
    }
    msg = parse_inbound(plain)
    assert msg.msgtype == "event"
    assert msg.event_type == "enter_chat"
    assert msg.text is None
    assert msg.chatid is None


def test_non_text_message_has_no_text():
    plain = {
        "msgid": "M3",
        "chattype": "single",
        "from": {"userid": "U3"},
        "msgtype": "image",
        "image": {"url": "http://x/y"},
    }
    msg = parse_inbound(plain)
    assert msg.text is None
    assert msg.msgtype == "image"


def test_missing_msgid_raises():
    with pytest.raises(InboundParseError):
        parse_inbound({"msgtype": "text", "text": {"content": "hi"}})
```

- [ ] **Step 2: 运行测试确认失败**

Run: `POSTGRES_HOST=localhost pytest tests/channels/test_parse.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'src.channels'`）

- [ ] **Step 3: 实现**

创建 `src/channels/__init__.py`（空文件）、`src/channels/wecom/__init__.py`（空文件）、`src/channels/base.py`：

```python
"""通道抽象：屏蔽接入方式（URL 回调 / 长连接）的传输差异。

业务代码只依赖本模块的 InboundMessage / ReplySink / MessageHandler，
切换接入方式不改业务。
"""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class InboundMessage:
    """统一入站事件——消息与事件共用同一结构。"""

    msgid: str  # 本次回调唯一标志，用于排重
    aibotid: str  # 智能机器人 id
    chatid: str | None  # 群聊会话 id；单聊为 None
    chattype: str  # 会话类型："single" | "group"
    from_userid: str  # 触发者 userid（非超管场景为密文）
    msgtype: str  # text/image/mixed/voice/file/video，或 "event"
    text: str | None  # 仅文本消息取 text.content；其它为 None
    event_type: str | None  # 事件类型（msgtype == "event" 时非空）
    raw: dict  # 原始明文兜底；只读约定，不深拷贝


class ReplySink(Protocol):
    """一条入站消息对应的回复出口；由驱动决定落到 HTTP 响应还是 WS 帧。"""

    async def reply_stream(self, content: str, finish: bool) -> None:
        """回复流式消息；finish=True 表示结束。"""
        ...


class MessageHandler(Protocol):
    """业务处理：吃入站事件，用 sink 回复。"""

    async def __call__(self, msg: InboundMessage, sink: ReplySink) -> None: ...


class ChannelDriver(Protocol):
    """接入通道驱动：回调为 no-op 生命周期，长连接在 start/stop 建连/断开。"""

    name: str  # 驱动标识

    async def start(self) -> None:
        """启动通道（回调 no-op）。"""
        ...

    async def stop(self) -> None:
        """停止通道（回调 no-op）。"""
        ...
```

创建 `src/channels/wecom/parse.py`：

```python
"""入站解析：解密后的 JSON 明文 → InboundMessage（回调 / 长连接共用）。"""

from src.channels.base import InboundMessage


class InboundParseError(ValueError):
    """入站报文缺失必填字段或结构非法。"""


def parse_inbound(plain: dict) -> InboundMessage:
    """把企微回调明文解析为统一入站事件。

    Args:
        plain: 解密后的 JSON 明文 dict

    Returns:
        InboundMessage

    Raises:
        InboundParseError: 缺少 msgid 等必填字段
    """
    msgid = plain.get("msgid")
    if not msgid:
        raise InboundParseError("missing msgid")

    msgtype = plain.get("msgtype", "")
    text: str | None = None
    event_type: str | None = None
    if msgtype == "event":
        event = plain.get("event") or {}
        event_type = event.get("eventtype")
    elif msgtype == "text":
        text_obj = plain.get("text") or {}
        text = text_obj.get("content")

    from_obj = plain.get("from") or {}
    return InboundMessage(
        msgid=msgid,
        aibotid=plain.get("aibotid", ""),
        chatid=plain.get("chatid"),
        chattype=plain.get("chattype", ""),
        from_userid=from_obj.get("userid", ""),
        msgtype=msgtype,
        text=text,
        event_type=event_type,
        raw=plain,
    )
```

- [ ] **Step 4: 运行测试确认通过**

Run: `POSTGRES_HOST=localhost pytest tests/channels/test_parse.py -v`
Expected: PASS（4 passed）

- [ ] **Step 5: 提交**

```bash
git add src/channels/__init__.py src/channels/base.py src/channels/wecom/__init__.py src/channels/wecom/parse.py tests/channels/__init__.py tests/channels/test_parse.py
git commit -m "feat(wecom): 新增通道抽象与入站解析"
```

---

### Task 2: WeCom 加解密（`channels/wecom/crypto.py`）

**Files:**
- Modify: `pyproject.toml`（`dependencies` 增加 `pycryptodome`）
- Create: `src/channels/wecom/crypto.py`
- Test: `tests/channels/test_crypto.py`

**Interfaces:**
- Consumes: 无
- Produces:
  - `PKCS7_BLOCK_SIZE: int = 32`
  - `WeComCrypto(token: str, encoding_aes_key: str, receive_id: str = "")`
  - `WeComCrypto.signature(timestamp: str, nonce: str, encrypt: str) -> str`
  - `WeComCrypto.decrypt(encrypt_b64: str) -> str`
  - `WeComCrypto.encrypt(msg: str) -> str`

- [ ] **Step 1: 加依赖并安装**

在 `pyproject.toml` 的 `dependencies` 列表中（`"bcrypt==5.0.0",` 之后）加入：

```toml
    # 企微回调加解密：AES-256-CBC + PKCS#7（块 32），官方 WXBizMsgCrypt 亦用 PyCryptodome
    "pycryptodome>=3.20,<4.0",
```

Run: `.venv/bin/pip install 'pycryptodome>=3.20,<4.0'`
Expected: Successfully installed pycryptodome-*

- [ ] **Step 2: 写失败测试（含官方已知答案向量）**

创建 `tests/channels/test_crypto.py`：

```python
"""企微加解密单测。

含官方《加解密方案说明》样例的**已知答案向量**——这是唯一能证伪
"对称性错误"（IV/填充块/base64 处理错）的外部基准，往返自测做不到。
"""

from src.channels.wecom.crypto import PKCS7_BLOCK_SIZE, WeComCrypto

# 官方样例：https://developer.work.weixin.qq.com/document/path/90968 「举例说明」
_SAMPLE_TOKEN = "QDG6eK"
_SAMPLE_AES_KEY = "jWmYm7qr5nMoAUwZRjGtBxmz3KA1tkAj3ykkR6q2B2C"
_SAMPLE_RECEIVE_ID = "wx5823bf96d3bd56c7"
_SAMPLE_TIMESTAMP = "1409659813"
_SAMPLE_NONCE = "1372623149"
_SAMPLE_SIGNATURE = "477715d11cdb4164915debcba66cb864d751f3e6"
_SAMPLE_ENCRYPT = (
    "RypEvHKD8QQKFhvQ6QleEB4J58tiPdvo+rtK1I9qca6aM/wvqnLSV5zEPeusUiX5L5X/0lWfrf0QAD"
    "HHhGd3QczcdCUpj911L3vg3W/sYYvuJTs3TUUkSUXxaccAS0qhxchrRYt66wiSpGLYL42aM6A8dTT+6k"
    "4aSknmPj48kzJs8qLjvd4Xgpue06DOdnLxAUHzM6+kDZ+HMZfJYuR+LtwGc2hgf5gsijff0ekUNXZiq"
    "ATP7PF5mZxZ3Izoun1s4zG4LUMnvw2r+KqCKIw+3IQH03v+BCA9nMELNqbSf6tiWSrXJB3LAVGUcall"
    "crw8V2t9EL4EhzJWrQUax5wLVMNS0+rUPA3k22Ncx4XXZS9o0MBH27Bo6BpNelZpS+/uh9KsNlY6bHCm"
    "JU9p8g7m3fVKn28H3KDYA5Pl/T8Z1ptDAVe0lXdQ2YoyyH2uyPIGHBZZIs2pDBS8R07+qN+E7Q=="
)


def _sample_crypto() -> WeComCrypto:
    return WeComCrypto(_SAMPLE_TOKEN, _SAMPLE_AES_KEY, _SAMPLE_RECEIVE_ID)


def test_pkcs7_block_size_is_32():
    assert PKCS7_BLOCK_SIZE == 32


def test_signature_matches_official_sample():
    c = _sample_crypto()
    assert (
        c.signature(_SAMPLE_TIMESTAMP, _SAMPLE_NONCE, _SAMPLE_ENCRYPT)
        == _SAMPLE_SIGNATURE
    )


def test_decrypt_matches_official_sample():
    c = _sample_crypto()
    plain = c.decrypt(_SAMPLE_ENCRYPT)
    assert "<MsgType><![CDATA[text]]></MsgType>" in plain
    assert "<Content><![CDATA[hello]]></Content>" in plain
    assert "<ToUserName><![CDATA[wx5823bf96d3bd56c7]]></ToUserName>" in plain


def test_roundtrip_with_empty_receive_id():
    # receive_id="" 是智能机器人的场景；往返验证加解密自洽
    c = WeComCrypto("token123", "a" * 43, "")
    assert c.decrypt(c.encrypt("hello 世界")) == "hello 世界"
```

- [ ] **Step 3: 运行测试确认失败**

Run: `POSTGRES_HOST=localhost pytest tests/channels/test_crypto.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'src.channels.wecom.crypto'`）

- [ ] **Step 4: 实现**

创建 `src/channels/wecom/crypto.py`：

```python
"""企业微信回调加解密：AES-256-CBC + PKCS#7（块 32）+ SHA1 签名。

规格（官方《回调和回复的加解密方案》）：
  AESKey = Base64_Decode(EncodingAESKey + "=")   # 32 字节
  IV     = AESKey[:16]
  明文   = random(16) + uint32_be(len(msg)) + msg + receive_id
填充用 PKCS#7，块大小固定 32（不是 AES 的 16）。
"""

import base64
import hashlib
import random
import struct

from Crypto.Cipher import AES

# PKCS#7 填充块大小；企微固定 32，误用 AES 的 16 会解密失败
PKCS7_BLOCK_SIZE: int = 32


def _pkcs7_pad(data: bytes) -> bytes:
    """按块大小 32 做 PKCS#7 填充。"""
    pad_len = PKCS7_BLOCK_SIZE - (len(data) % PKCS7_BLOCK_SIZE)
    return data + bytes([pad_len]) * pad_len


def _pkcs7_unpad(data: bytes) -> bytes:
    """去掉 PKCS#7 填充。"""
    pad_len = data[-1]
    if pad_len < 1 or pad_len > PKCS7_BLOCK_SIZE:
        raise ValueError("invalid pkcs7 padding")
    return data[:-pad_len]


class WeComCrypto:
    """企微回调的签名与加解密。"""

    def __init__(self, token: str, encoding_aes_key: str, receive_id: str = ""):
        """初始化。

        Args:
            token: 回调 Token（3~32 位）
            encoding_aes_key: 回调 EncodingAESKey（43 位）
            receive_id: 智能机器人场景传空字符串
        """
        self.token = token
        self.key = base64.b64decode(encoding_aes_key + "=")
        self.iv = self.key[:16]
        self.receive_id = receive_id

    def signature(self, timestamp: str, nonce: str, encrypt: str) -> str:
        """计算 msg_signature：四参数字典序排序后拼接取 sha1。"""
        items = sorted([self.token, timestamp, nonce, encrypt])
        return hashlib.sha1("".join(items).encode("utf-8")).hexdigest()

    def decrypt(self, encrypt_b64: str) -> str:
        """解密 encrypt 字段，返回明文（JSON 字符串）。"""
        cipher = AES.new(self.key, AES.MODE_CBC, self.iv)
        plain = _pkcs7_unpad(cipher.decrypt(base64.b64decode(encrypt_b64)))
        msg_len = struct.unpack("!I", plain[16:20])[0]
        # 不校验尾部 receive_id：智能机器人场景恒为空串，校验无意义
        return plain[20 : 20 + msg_len].decode("utf-8")

    def encrypt(self, msg: str) -> str:
        """加密明文，返回 encrypt 字段（base64）。"""
        raw = msg.encode("utf-8")
        plain = (
            bytes(random.choices(range(256), k=16))
            + struct.pack("!I", len(raw))
            + raw
            + self.receive_id.encode("utf-8")
        )
        cipher = AES.new(self.key, AES.MODE_CBC, self.iv)
        return base64.b64encode(cipher.encrypt(_pkcs7_pad(plain))).decode("utf-8")
```

- [ ] **Step 5: 运行测试确认通过**

Run: `POSTGRES_HOST=localhost pytest tests/channels/test_crypto.py -v`
Expected: PASS（4 passed）

- [ ] **Step 6: 提交**

```bash
git add pyproject.toml src/channels/wecom/crypto.py tests/channels/test_crypto.py
git commit -m "feat(wecom): 新增回调加解密（含官方已知答案向量单测）"
```

---

### Task 3: 回调驱动（`channels/wecom/callback.py`）

**Files:**
- Create: `src/channels/wecom/callback.py`
- Test: `tests/channels/test_callback_driver.py`

**Interfaces:**
- Consumes: `WeComCrypto`（Task 2）、`parse_inbound` / `InboundParseError`（Task 1）、`InboundMessage` / `MessageHandler`（Task 1）
- Produces:
  - `OutboundReply`（frozen dataclass：`kind: str`、`content: str`、`finish: bool`）
  - `CallbackDriver(crypto: WeComCrypto, handler: MessageHandler)`
  - `CallbackDriver.verify(raw_query: str) -> starlette Response`
  - `CallbackDriver.handle_message(raw_query: str, raw_body: bytes) -> starlette Response`
  - 模块级 `_parse_query(raw_query: str) -> dict[str, str]`

- [ ] **Step 1: 写失败测试**

创建 `tests/channels/test_callback_driver.py`：

```python
"""回调驱动单测：URL 验证、消息/事件分流、空包、错误码、query 解码。"""

import json

import pytest

from src.channels.base import InboundMessage, ReplySink
from src.channels.wecom.callback import CallbackDriver, _parse_query
from src.channels.wecom.crypto import WeComCrypto

_TOKEN = "token123"
_AES_KEY = "a" * 43


def _crypto() -> WeComCrypto:
    return WeComCrypto(_TOKEN, _AES_KEY, "")


def _query(msg_signature: str, timestamp: str, nonce: str, **extra: str) -> str:
    parts = [
        f"msg_signature={msg_signature}",
        f"timestamp={timestamp}",
        f"nonce={nonce}",
    ]
    parts.extend(f"{k}={v}" for k, v in extra.items())
    return "&".join(parts)


class _Recorder:
    """记录 handler 写入的回复。"""

    def __init__(self) -> None:
        self.replies: list[tuple[str, bool]] = []

    async def reply_stream(self, content: str, finish: bool) -> None:
        self.replies.append((content, finish))


async def _handler_reply(msg: InboundMessage, sink: ReplySink) -> None:
    if msg.msgtype == "event":
        return
    await sink.reply_stream("已收到", finish=True)


def test_parse_query_keeps_plus():
    # 裸 '+' 必须保留，不能被当成空格（echostr 是 base64）
    q = _parse_query("echostr=ab+cd%2Bef&timestamp=1")
    assert q["echostr"] == "ab+cd+ef"
    assert q["timestamp"] == "1"


def test_verify_returns_plaintext():
    c = _crypto()
    ts, nonce = "100", "200"
    echostr = c.encrypt("echo-123")
    driver = CallbackDriver(c, _handler_reply)
    resp = driver.verify(_query(c.signature(ts, nonce, echostr), ts, nonce, echostr=echostr))
    assert resp.status_code == 200
    assert resp.body.decode("utf-8") == "echo-123"


def test_verify_bad_signature_returns_403():
    c = _crypto()
    driver = CallbackDriver(c, _handler_reply)
    resp = driver.verify(_query("deadbeef", "100", "200", echostr="x"))
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_handle_message_replies_and_is_verifiable():
    c = _crypto()
    ts, nonce = "100", "200"
    payload = {
        "msgid": "M1",
        "aibotid": "B1",
        "chattype": "single",
        "from": {"userid": "U1"},
        "msgtype": "text",
        "text": {"content": "hi"},
    }
    encrypt = c.encrypt(json.dumps(payload, ensure_ascii=False))
    body = json.dumps({"encrypt": encrypt}).encode("utf-8")
    driver = CallbackDriver(c, _handler_reply)

    resp = await driver.handle_message(_query(c.signature(ts, nonce, encrypt), ts, nonce), body)
    assert resp.status_code == 200
    out = json.loads(resp.body)
    # 回包可被独立按规则验签
    assert out["msgsignature"] == c.signature(ts, nonce, out["encrypt"])
    assert out["timestamp"] == int(ts) and out["nonce"] == nonce
    reply_plain = json.loads(c.decrypt(out["encrypt"]))
    assert reply_plain["msgtype"] == "stream"
    assert reply_plain["stream"]["finish"] is True
    assert reply_plain["stream"]["content"] == "已收到"


@pytest.mark.asyncio
async def test_handle_message_event_returns_empty_body():
    c = _crypto()
    ts, nonce = "100", "200"
    payload = {
        "msgid": "M2",
        "chattype": "single",
        "from": {"userid": "U1"},
        "msgtype": "event",
        "event": {"eventtype": "enter_chat"},
    }
    encrypt = c.encrypt(json.dumps(payload, ensure_ascii=False))
    body = json.dumps({"encrypt": encrypt}).encode("utf-8")
    driver = CallbackDriver(c, _handler_reply)

    resp = await driver.handle_message(_query(c.signature(ts, nonce, encrypt), ts, nonce), body)
    assert resp.status_code == 200
    assert resp.body == b""


@pytest.mark.asyncio
async def test_handle_message_bad_json_returns_400():
    c = _crypto()
    driver = CallbackDriver(c, _handler_reply)
    resp = await driver.handle_message(_query("deadbeef", "100", "200"), b"not-json")
    assert resp.status_code == 400
```

- [ ] **Step 2: 运行测试确认失败**

Run: `POSTGRES_HOST=localhost pytest tests/channels/test_callback_driver.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'src.channels.wecom.callback'`）

- [ ] **Step 3: 实现**

创建 `src/channels/wecom/callback.py`：

```python
"""企微智能机器人「URL 回调」驱动：验签 / 解密 / 解析 / 加密回包。

传输层独占职责——路由只透传原始 query 与 body，本驱动返回最终 Response。
"""

import json
import uuid
from dataclasses import dataclass
from urllib.parse import unquote

from loguru import logger
from starlette.responses import JSONResponse, PlainTextResponse, Response

from src.channels.base import MessageHandler
from src.channels.wecom.crypto import WeComCrypto
from src.channels.wecom.parse import InboundParseError, parse_inbound


@dataclass(frozen=True)
class OutboundReply:
    """一条待发送的被动回复。"""

    kind: str  # 回复类型；目前仅 "stream"
    content: str  # 回复正文（支持 markdown）
    finish: bool  # 流式是否结束


def _parse_query(raw_query: str) -> dict[str, str]:
    """解析原始 query 字符串，**保留 '+'**。

    不能用 urllib.parse.parse_qsl / unquote_plus —— 它们把裸 '+' 解成空格，
    而 echostr 是 base64、可能含 '+'，会导致解密失败。unquote 只做百分号解码。
    """
    result: dict[str, str] = {}
    for pair in raw_query.split("&"):
        if not pair:
            continue
        key, _, value = pair.partition("=")
        result[unquote(key)] = unquote(value)
    return result


class _CallbackSink:
    """收集本轮回复；回调模式只支持首次同步回包。"""

    def __init__(self) -> None:
        self.reply: OutboundReply | None = None

    async def reply_stream(self, content: str, finish: bool) -> None:
        """记录一条流式回复。"""
        self.reply = OutboundReply(kind="stream", content=content, finish=finish)


class CallbackDriver:
    """企微回调驱动。"""

    name = "wecom_callback"

    def __init__(self, crypto: WeComCrypto, handler: MessageHandler):
        """初始化。

        Args:
            crypto: 加解密器
            handler: 业务处理（吃入站事件，用 sink 回复）
        """
        self._crypto = crypto
        self._handler = handler

    async def start(self) -> None:
        """回调无需建连，no-op（为长连接对称而留）。"""

    async def stop(self) -> None:
        """回调无需断连，no-op。"""

    def verify(self, raw_query: str) -> Response:
        """处理 URL 有效性验证（GET）。"""
        q = _parse_query(raw_query)
        timestamp = q.get("timestamp", "")
        nonce = q.get("nonce", "")
        echostr = q.get("echostr", "")
        if self._crypto.signature(timestamp, nonce, echostr) != q.get("msg_signature", ""):
            return PlainTextResponse("invalid signature", status_code=403)
        return PlainTextResponse(self._crypto.decrypt(echostr))

    async def handle_message(self, raw_query: str, raw_body: bytes) -> Response:
        """处理消息/事件回调（POST）。"""
        q = _parse_query(raw_query)
        timestamp = q.get("timestamp", "")
        nonce = q.get("nonce", "")

        try:
            body = json.loads(raw_body)
            encrypt = body["encrypt"]
        except (ValueError, KeyError, TypeError):
            return PlainTextResponse("bad request", status_code=400)

        if self._crypto.signature(timestamp, nonce, encrypt) != q.get("msg_signature", ""):
            return PlainTextResponse("invalid signature", status_code=403)

        try:
            plain = json.loads(self._crypto.decrypt(encrypt))
            msg = parse_inbound(plain)
        except (ValueError, InboundParseError):
            return PlainTextResponse("bad request", status_code=400)

        sink = _CallbackSink()
        try:
            await self._handler(msg, sink)
        except Exception as e:  # noqa: BLE001
            # 不向企微抛错：返回空体 200，避免重试风暴
            logger.error("[wecom] handler failed msgid={} err={}", msg.msgid, e)
            return Response(status_code=200)

        if sink.reply is None:
            return Response(status_code=200)
        return self._build_reply(sink.reply, timestamp, nonce)

    def _build_reply(self, reply: OutboundReply, timestamp: str, nonce: str) -> Response:
        """把 OutboundReply 加密为企微要求的回包体。"""
        stream_id = uuid.uuid4().hex
        plain = json.dumps(
            {
                "msgtype": "stream",
                "stream": {
                    "id": stream_id,
                    "content": reply.content,
                    "finish": reply.finish,
                },
            },
            ensure_ascii=False,
        )
        encrypt = self._crypto.encrypt(plain)
        # 复用请求的 timestamp/nonce，对回包密文签名
        return JSONResponse(
            {
                "encrypt": encrypt,
                "msgsignature": self._crypto.signature(timestamp, nonce, encrypt),
                "timestamp": int(timestamp),
                "nonce": nonce,
            }
        )
```

- [ ] **Step 4: 运行测试确认通过**

Run: `POSTGRES_HOST=localhost pytest tests/channels/test_callback_driver.py -v`
Expected: PASS（6 passed）

- [ ] **Step 5: 提交**

```bash
git add src/channels/wecom/callback.py tests/channels/test_callback_driver.py
git commit -m "feat(wecom): 新增回调驱动（验签/解密/加密回包/事件空包）"
```

---

### Task 4: 编排与配置（`services/wecom_service.py` + settings/const）

**Files:**
- Create: `src/services/wecom_service.py`
- Modify: `src/config/settings.py`（新增 5 个环境变量）
- Modify: `src/config/const.py`（新增 `WECOM_CALLBACK_PATH`）
- Test: `tests/services/test_wecom_service.py`

**Interfaces:**
- Consumes: `CallbackDriver`（Task 3）、`WeComCrypto`（Task 2）、`InboundMessage` / `ReplySink`（Task 1）
- Produces:
  - `wecom_service.get_driver() -> CallbackDriver`（未启动时抛 `RuntimeError`）
  - `wecom_service.start() -> None`
  - `wecom_service.stop() -> None`
  - `settings.WECOM_BOT_ENABLED / WECOM_BOT_TOKEN / WECOM_BOT_ENCODING_AES_KEY / WECOM_BOT_RECEIVE_ID / WECOM_BOT_LOG_CONTENT`
  - `const.WECOM_CALLBACK_PATH = "/wecom/callback"`

- [ ] **Step 1: 写失败测试**

创建 `tests/services/test_wecom_service.py`：

```python
"""企微编排单测：启停、凭证 fail-fast、默认 handler 行为。"""

import pytest

from src.channels.base import InboundMessage
from src.config import settings
from src.services import wecom_service


def _msg(msgtype: str, text: str | None, event_type: str | None = None) -> InboundMessage:
    return InboundMessage(
        msgid="M1",
        aibotid="B1",
        chatid=None,
        chattype="single",
        from_userid="U1",
        msgtype=msgtype,
        text=text,
        event_type=event_type,
        raw={},
    )


class _Sink:
    def __init__(self) -> None:
        self.replies: list[tuple[str, bool]] = []

    async def reply_stream(self, content: str, finish: bool) -> None:
        self.replies.append((content, finish))


@pytest.fixture(autouse=True)
def _reset_driver():
    wecom_service._driver = None
    yield
    wecom_service._driver = None


@pytest.mark.asyncio
async def test_disabled_start_is_noop(monkeypatch):
    monkeypatch.setattr(settings, "WECOM_BOT_ENABLED", False)
    await wecom_service.start()
    with pytest.raises(RuntimeError):
        wecom_service.get_driver()


@pytest.mark.asyncio
async def test_enabled_but_bad_aes_key_is_fail_fast(monkeypatch):
    monkeypatch.setattr(settings, "WECOM_BOT_ENABLED", True)
    monkeypatch.setattr(settings, "WECOM_BOT_TOKEN", "tok")
    monkeypatch.setattr(settings, "WECOM_BOT_ENCODING_AES_KEY", "short")
    with pytest.raises(RuntimeError):
        await wecom_service.start()


@pytest.mark.asyncio
async def test_enabled_start_builds_driver(monkeypatch):
    monkeypatch.setattr(settings, "WECOM_BOT_ENABLED", True)
    monkeypatch.setattr(settings, "WECOM_BOT_TOKEN", "tok")
    monkeypatch.setattr(settings, "WECOM_BOT_ENCODING_AES_KEY", "a" * 43)
    await wecom_service.start()
    driver = wecom_service.get_driver()
    assert driver.name == "wecom_callback"


@pytest.mark.asyncio
async def test_default_handler_replies_for_message(monkeypatch):
    monkeypatch.setattr(settings, "WECOM_BOT_LOG_CONTENT", False)
    sink = _Sink()
    await wecom_service._default_handler(_msg("text", "hi"), sink)
    assert len(sink.replies) == 1
    assert sink.replies[0][1] is True


@pytest.mark.asyncio
async def test_default_handler_silent_for_event(monkeypatch):
    monkeypatch.setattr(settings, "WECOM_BOT_LOG_CONTENT", False)
    sink = _Sink()
    await wecom_service._default_handler(_msg("event", None, "enter_chat"), sink)
    assert sink.replies == []
```

- [ ] **Step 2: 运行测试确认失败**

Run: `POSTGRES_HOST=localhost pytest tests/services/test_wecom_service.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'src.services.wecom_service'`）

- [ ] **Step 3: 改配置**

在 `src/config/settings.py` 末尾追加（沿用模块级 `os.getenv` 风格）：

```python
# ── 企业微信智能机器人（URL 回调）──
WECOM_BOT_ENABLED: bool = os.getenv("WECOM_BOT_ENABLED", "false").lower() in (
    "1",
    "true",
    "yes",
)
WECOM_BOT_TOKEN: str = os.getenv("WECOM_BOT_TOKEN", "")
WECOM_BOT_ENCODING_AES_KEY: str = os.getenv("WECOM_BOT_ENCODING_AES_KEY", "")
WECOM_BOT_RECEIVE_ID: str = os.getenv("WECOM_BOT_RECEIVE_ID", "")
WECOM_BOT_LOG_CONTENT: bool = os.getenv("WECOM_BOT_LOG_CONTENT", "false").lower() in (
    "1",
    "true",
    "yes",
)
```

在 `src/config/const.py` 追加：

```python
# 企微智能机器人回调路由（router 相对路径；对外完整地址为 /api + 本值）
WECOM_CALLBACK_PATH: str = "/wecom/callback"
```

- [ ] **Step 4: 实现编排**

创建 `src/services/wecom_service.py`：

```python
"""企微智能机器人回调通道的编排：装配驱动 + 注入默认 handler。

本轮 handler 只打日志并回写死回复；后续替换为「调 agent 跑 RAG」。
"""

from loguru import logger

from src.channels.base import InboundMessage, ReplySink
from src.channels.wecom.callback import CallbackDriver
from src.channels.wecom.crypto import WeComCrypto
from src.config import settings

# 单例驱动；由 start() 在 enabled 时构造
_driver: CallbackDriver | None = None


async def _default_handler(msg: InboundMessage, sink: ReplySink) -> None:
    """默认 handler：打结构性日志；消息回写死流式回复，事件不回包。"""
    text_len = len(msg.text) if msg.text else 0
    logger.info(
        "[wecom] inbound msgid={} chattype={} msgtype={} event_type={} text_len={}",
        msg.msgid,
        msg.chattype,
        msg.msgtype,
        msg.event_type,
        text_len,
    )
    if settings.WECOM_BOT_LOG_CONTENT and msg.text:
        logger.debug("[wecom] content={}", msg.text)

    if msg.msgtype == "event":
        return
    await sink.reply_stream("已收到，稍后接入检索…", finish=True)


def _validate_credentials() -> None:
    """enabled 时的启动期校验：凭证非法即抛，不留请求期降级。"""
    if not settings.WECOM_BOT_TOKEN:
        raise RuntimeError("WECOM_BOT_TOKEN 未配置")
    if len(settings.WECOM_BOT_ENCODING_AES_KEY) != 43:
        raise RuntimeError("WECOM_BOT_ENCODING_AES_KEY 必须为 43 位")


async def start() -> None:
    """启动通道：enabled 时构造并校验驱动，否则 no-op。"""
    global _driver
    if not settings.WECOM_BOT_ENABLED:
        return
    _validate_credentials()
    crypto = WeComCrypto(
        settings.WECOM_BOT_TOKEN,
        settings.WECOM_BOT_ENCODING_AES_KEY,
        settings.WECOM_BOT_RECEIVE_ID,
    )
    _driver = CallbackDriver(crypto, _default_handler)


async def stop() -> None:
    """停止通道。"""
    global _driver
    _driver = None


def get_driver() -> CallbackDriver:
    """取当前驱动；未启动时抛 RuntimeError。"""
    if _driver is None:
        raise RuntimeError("wecom driver 未启动")
    return _driver
```

- [ ] **Step 5: 运行测试确认通过**

Run: `POSTGRES_HOST=localhost pytest tests/services/test_wecom_service.py -v`
Expected: PASS（5 passed）

- [ ] **Step 6: 提交**

```bash
git add src/services/wecom_service.py src/config/settings.py src/config/const.py tests/services/test_wecom_service.py
git commit -m "feat(wecom): 新增回调编排与配置（fail-fast 凭证校验）"
```

---

### Task 5: 路由与装配（`api/wecom.py` + `main.py`）

**Files:**
- Create: `src/api/wecom.py`
- Modify: `src/main.py`（import + `include_router` + lifespan `start/stop`）
- Test: `tests/api/test_wecom.py`

**Interfaces:**
- Consumes: `wecom_service`（Task 4）、`WECOM_CALLBACK_PATH`（Task 4）、`settings.WECOM_BOT_ENABLED`（Task 4）
- Produces: `src.api.wecom.router`（`GET/POST /wecom/callback`）

- [ ] **Step 1: 写失败测试**

创建 `tests/api/test_wecom.py`（用**最小 app** 挂路由，避免拉起全量 DB/Redis）：

```python
"""企微回调路由单测：透传、状态码、query 解码。"""

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api import wecom as wecom_routes
from src.channels.base import InboundMessage, ReplySink
from src.channels.wecom.callback import CallbackDriver
from src.channels.wecom.crypto import WeComCrypto
from src.config import settings
from src.services import wecom_service

_TOKEN = "token123"
_AES_KEY = "a" * 43


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(wecom_routes.router, prefix="/api")
    return app


@pytest.fixture
def client(monkeypatch):
    crypto = WeComCrypto(_TOKEN, _AES_KEY, "")

    async def _handler(msg: InboundMessage, sink: ReplySink) -> None:
        await sink.reply_stream("已收到", finish=True)

    monkeypatch.setattr(wecom_service, "get_driver", lambda: CallbackDriver(crypto, _handler))
    return TestClient(_app()), crypto


def test_disabled_returns_404(monkeypatch):
    monkeypatch.setattr(settings, "WECOM_BOT_ENABLED", False)
    resp = TestClient(_app()).get("/api/wecom/callback?msg_signature=x&timestamp=1&nonce=2&echostr=z")
    assert resp.status_code == 404


def test_verify_ok(monkeypatch, client):
    test_client, crypto = client
    monkeypatch.setattr(settings, "WECOM_BOT_ENABLED", True)
    ts, nonce = "100", "200"
    echostr = crypto.encrypt("echo-xyz")
    sig = crypto.signature(ts, nonce, echostr)
    resp = test_client.get(
        f"/api/wecom/callback?msg_signature={sig}&timestamp={ts}&nonce={nonce}&echostr={echostr}"
    )
    assert resp.status_code == 200
    assert resp.text == "echo-xyz"


def test_receive_message_ok(monkeypatch, client):
    test_client, crypto = client
    monkeypatch.setattr(settings, "WECOM_BOT_ENABLED", True)
    ts, nonce = "100", "200"
    payload = {
        "msgid": "M1",
        "chattype": "single",
        "from": {"userid": "U1"},
        "msgtype": "text",
        "text": {"content": "hi"},
    }
    encrypt = crypto.encrypt(json.dumps(payload, ensure_ascii=False))
    sig = crypto.signature(ts, nonce, encrypt)
    resp = test_client.post(
        f"/api/wecom/callback?msg_signature={sig}&timestamp={ts}&nonce={nonce}",
        json={"encrypt": encrypt},
    )
    assert resp.status_code == 200
    out = resp.json()
    assert out["msgsignature"] == crypto.signature(ts, nonce, out["encrypt"])


def test_receive_bad_signature_403(monkeypatch, client):
    test_client, _ = client
    monkeypatch.setattr(settings, "WECOM_BOT_ENABLED", True)
    resp = test_client.post("/api/wecom/callback?msg_signature=x&timestamp=1&nonce=2", json={"encrypt": "y"})
    assert resp.status_code == 403
```

- [ ] **Step 2: 运行测试确认失败**

Run: `POSTGRES_HOST=localhost pytest tests/api/test_wecom.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'src.api.wecom'`）

- [ ] **Step 3: 实现路由**

创建 `src/api/wecom.py`：

```python
"""企微智能机器人回调路由（GET 验证 URL / POST 收消息）。

极薄：只判 enabled 并把原始 query/body 透传给驱动；不声明类型化参数、
不使用 Pydantic —— 避免框架自动校验把 400/403 改写成 422/500 统一信封。
"""

from fastapi import APIRouter, Request
from starlette.responses import Response

from src.config import settings
from src.config.const import WECOM_CALLBACK_PATH
from src.services import wecom_service

router = APIRouter()


@router.get(WECOM_CALLBACK_PATH)
async def wecom_verify(request: Request) -> Response:
    """URL 有效性验证：验签 + 解密 echostr，返回明文。"""
    if not settings.WECOM_BOT_ENABLED:
        return Response(status_code=404)
    return wecom_service.get_driver().verify(request.url.query)


@router.post(WECOM_CALLBACK_PATH)
async def wecom_receive(request: Request) -> Response:
    """接收消息/事件回调。"""
    if not settings.WECOM_BOT_ENABLED:
        return Response(status_code=404)
    return await wecom_service.get_driver().handle_message(
        request.url.query, await request.body()
    )
```

- [ ] **Step 4: 运行测试确认通过**

Run: `POSTGRES_HOST=localhost pytest tests/api/test_wecom.py -v`
Expected: PASS（4 passed）

- [ ] **Step 5: 装配到 main.py**

修改 `src/main.py`：

1) 在路由 import 区加入（与 `from src.api import auth as auth_routes` 同组）：

```python
from src.api import wecom as wecom_routes
from src.services import wecom_service
```

2) 在 lifespan 内、`yield` 之前（`configure_tracing()` 之后）加：

```python
    await wecom_service.start()
```

3) 在 lifespan 内、`yield` 之后（`flush_tracing()` 之前）加：

```python
    await wecom_service.stop()
```

4) 在 `include_router` 区末尾加：

```python
app.include_router(wecom_routes.router, prefix="/api", tags=["wecom"])
```

- [ ] **Step 6: 校验装配未破坏启动**

Run: `POSTGRES_HOST=localhost pytest tests/api/test_wecom.py tests/test_dependencies.py -v`
Expected: PASS

- [ ] **Step 7: 提交**

```bash
git add src/api/wecom.py src/main.py tests/api/test_wecom.py
git commit -m "feat(wecom): 挂载回调路由并接入 lifespan"
```

---

### Task 6: 文档登记（一事一档）

**Files:**
- Modify: `docs/agents/code-map.md`（登记 `src/channels/` + `src/api/wecom.py`）
- Modify: `src/api/README.md`（路由清单补 `wecom.py`）
- Modify: `.env.example`、`.env.template`（登记 5 个 `WECOM_BOT_*`）
- Modify: `docs/agents/glossary.md`（新增术语）
- Modify: `docs/agents/api_contract.md`（回调端点契约）
- Modify: `docs/agents/cookbook.md`（企微回调接入操作）

**Interfaces:**
- Consumes: 前 5 个任务的产物
- Produces: 文档（无代码接口）

- [ ] **Step 1: 登记代码地图**

在 `docs/agents/code-map.md`「二、后端 `src/` 分层」的代码块中，`tools/` 行之后加入：

```
channels/          接入通道：base(通用抽象) / wecom(parse 入站解析 / crypto 加解密 / callback 回调驱动)
```

在同文「八、常见改动落点速查」表末追加一行：

```
| **加/改接入通道（企微回调/长连接）** | `src/channels/`（`base.py` 抽象 + `wecom/`）；路由 `src/api/wecom.py`；装配 `src/services/wecom_service.py` |
```

- [ ] **Step 2: 补路由清单**

在 `src/api/README.md`「文件说明」表中，`capabilities.py` 行之后加入：

```
| `wecom.py` | 企微智能机器人回调 `GET /api/wecom/callback`（URL 验证）、`POST /api/wecom/callback`（收消息/事件）；受 `WECOM_BOT_ENABLED` 控制，关闭时 404 |
```

- [ ] **Step 3: 登记环境变量**

在 `.env.example` 与 `.env.template` 各追加（值留空/默认）：

```
# ── 企业微信智能机器人（URL 回调）──
# 启用回调端点；Token/AESKey 与企微后台一致，AESKey 必须 43 位
WECOM_BOT_ENABLED=false
WECOM_BOT_TOKEN=
WECOM_BOT_ENCODING_AES_KEY=
WECOM_BOT_RECEIVE_ID=
# 是否打印用户消息正文（默认关，防泄露）
WECOM_BOT_LOG_CONTENT=false
```

- [ ] **Step 4: 登记术语**

在 `docs/agents/glossary.md` 追加（按该文件既有条目格式）：

```markdown
- **接入通道 / 通道驱动（ChannelDriver）**：屏蔽企业微信「URL 回调」与「长连接」两种接入方式的传输差异的抽象；业务只依赖其入站事件（`InboundMessage`）与回复出口（`ReplySink`）。见 `src/channels/base.py`。
- **回调驱动（CallbackDriver）**：接入通道的实现之一，负责企微 URL 回调的验签、AES 加解密、入站解析与加密回包；智能机器人场景 `ReceiveId` 为 `""`。见 `src/channels/wecom/callback.py`。
```

- [ ] **Step 5: 登记接口契约**

在 `docs/agents/api_contract.md` 追加一节（贴合该文件既有章节格式）：

```markdown
## 企微智能机器人回调（`/api/wecom/callback`）

外部平台回调端点，非前端消费；受 `WECOM_BOT_ENABLED` 控制（关闭时 `404`）。

| 方法 | 入参 | 成功响应 | 失败 |
|---|---|---|---|
| `GET` | query `msg_signature`/`timestamp`/`nonce`/`echostr` | `text/plain`：解密后的 echostr 明文（1 秒内） | 验签失败 `403` |
| `POST` | query `msg_signature`/`timestamp`/`nonce`；body `{"encrypt":"..."}` | `application/json`：`{"encrypt","msgsignature","timestamp","nonce"}`；**无回包时 0 字节空体 + 200** | 验签失败 `403`；body 非 JSON / 解密或解析失败 `400` |

约定：`msgsignature = sha1(sort(token, 请求timestamp, 请求nonce, 回包encrypt))`；普通消息只支持 `stream`/`template_card` 回复。
```

- [ ] **Step 6: 登记操作流程**

在 `docs/agents/cookbook.md` 追加一节：

```markdown
## 企微智能机器人回调接入（开发期）

1. 在企微管理后台「安全与管理 → 管理工具 → 智能机器人」创建机器人，选「API 模式创建」。
2. 生成并记录 `Token` 与 `EncodingAESKey`，两者填入 `.env` 的 `WECOM_BOT_TOKEN` / `WECOM_BOT_ENCODING_AES_KEY`，并设 `WECOM_BOT_ENABLED=true`。
3. 回调 URL 填 `http(s)://<公网地址>/api/wecom/callback`（Nginx 只转发 `/api/`，故必须挂其下）。
4. `docker compose up -d --force-recreate app` 使新环境变量生效（改 `.env` 后 restart 不吃）。
5. 保存时应通过 URL 验证；在群里 @机器人 应收到写死回复，容器日志可见 `[wecom] inbound ...`。
6. **重复回复属预期**（企微重试 + Nginx `proxy_next_upstream` 叠加），非 bug。
```

- [ ] **Step 7: 提交**

```bash
git add docs/agents/code-map.md src/api/README.md .env.example .env.template docs/agents/glossary.md docs/agents/api_contract.md docs/agents/cookbook.md
git commit -m "docs(wecom): 登记回调通道的代码地图/路由/环境变量/术语/契约/操作"
```

---

## 收尾验收（人工，不属任何单任务）

- [ ] 全量门禁：`POSTGRES_HOST=localhost pytest tests/ -v` 全绿；`ruff check .` 无错；`pyright src/` 不新增 error。
- [ ] 无遗留 `print()` / TODO / 调试代码。
- [ ] 手工冒烟（需企微后台，见 Task 6 Step 6）——若暂无企业主体域名/IP，标记为「待冒烟」，不阻塞合并。
- [ ] 合并前用 `verification-before-completion` 取证，再 `requesting-code-review`。

## 未覆盖（本 plan 之外）

- 长连接驱动、流式刷新缓冲（`stream.id`→Redis）、业务接线（调 agent 跑 RAG）、媒体入站解密、欢迎语/模板卡片、去重幂等、认证/备案域名准备、多机器人多凭证 —— 见 spec §7。
- `pycryptodome` 的镜像重建与云效 PyPI 代理预热（属部署动作，见 spec §6）。
