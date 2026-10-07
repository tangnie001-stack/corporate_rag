# 企微多机器人长连接 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把企微智能机器人接入从「单机器人 + URL 回调」改为「多机器人（当前 3 台）+ 长连接」，并按 `aibotid` 把入站消息分发到对应业务线（回复仍为占位、不接 RAG）。

**Architecture:** 配置由扁平单机器人变为 `WECOM_BOTS`（JSON 数组，单行）；配置解析独立到新模块 `src/config/wecom_bots.py`（因 `settings.py` 已 384/400 行）；`wecom_service` 由单例驱动改为**驱动注册表** `dict[bot_key, ChannelDriver]`，逐台装配/降级/关停；handler 用 `aibotid → bot_key` 反查表分发；回调保留为 legacy 单机器人（存保留键 `callback`）。

**Tech Stack:** Python 3.11+ / FastAPI / loguru / pytest（asyncio）/ 官方 `wecom-aibot-python-sdk`（导入名 `aibot`，已装）

**Spec:** `openspec/changes/wecom-multi-bot/`（`proposal.md` / `design.md` / `specs/wecom-channel/spec.md` / `tasks.md`）——执行者读本计划时请一并读该 spec 的 `design.md`（决策 D1–D13）。

## Global Constraints

- 注释、文档一律**中文**；`dataclass` 每个字段必须加**行内注释**。
- **禁用三元表达式**（`a if cond else b`），写完整 if/else。
- 类型不确定用显式 `isinstance`，不用 `getattr(x, "a", default)` 兜底。
- 单文件 ≤ 400 行；单函数 ≤ 80 行。
- 日志：`[层名]` 前缀 + 英文 `k=v`；动态值经 `src.core.logging.encode_value` 编码（token 安全才裸写）。
- 分层：`api/` 不得 import `infra/` 或 `config/`；本链路 `api/wecom.py → services/wecom_service.py → channels/wecom/*`。
- **本 worktree 内禁用 `git add -A` / `git add .`**（`.venv` 是 symlink）——提交一律用**显式文件路径**。
- 测试命令（worktree 根目录）：`POSTGRES_HOST=localhost pytest <path> -q`（宿主侧必须前置该环境变量）。
- 分派：长连接每台机器人同时只能一条连接；`WECOM_BOTS` **必须写在一行**。

---

### Task 1: 配置模块 `src/config/wecom_bots.py`

**Files:**
- Create: `src/config/wecom_bots.py`
- Test: `tests/config/test_wecom_bots.py`

**Interfaces:**
- Consumes: 无（读 `os.getenv("WECOM_BOTS")`）
- Produces:
  - `CALLBACK_BOT_KEY: str = "callback"` —— 回调 legacy 驱动的保留键
  - `@dataclass(frozen=True) class WeComBotConfig`，字段 `key: str` / `bot_id: str` / `secret: str`
  - `load_wecom_bots() -> list[WeComBotConfig]` —— 解析+校验；非法抛 `ValueError`；空值返回 `[]`

- [ ] **Step 1: 写失败测试**

创建 `tests/config/test_wecom_bots.py`：

```python
"""WECOM_BOTS 注册表解析与校验单测。"""

import json

import pytest

from src.config.wecom_bots import CALLBACK_BOT_KEY, WeComBotConfig, load_wecom_bots


def _dump(items: list[dict]) -> str:
    """紧凑单行 JSON（与 .env 里的实际写法一致）。"""
    return json.dumps(items, ensure_ascii=False, separators=(",", ":"))


def _bot(key: str, bot_id: str, secret: str) -> dict:
    return {"key": key, "bot_id": bot_id, "secret": secret}


def test_empty_returns_empty_list(monkeypatch):
    monkeypatch.setenv("WECOM_BOTS", "")
    assert load_wecom_bots() == []


def test_valid_three_bots(monkeypatch):
    monkeypatch.setenv(
        "WECOM_BOTS",
        _dump(
            [
                _bot("dev", "aibA", "s1"),
                _bot("support", "aibB", "s2"),
                _bot("finance", "aibC", "s3"),
            ]
        ),
    )
    bots = load_wecom_bots()
    assert [b.key for b in bots] == ["dev", "support", "finance"]
    assert bots[0] == WeComBotConfig(key="dev", bot_id="aibA", secret="s1")


def test_invalid_json_does_not_leak_secret(monkeypatch):
    """非法 JSON 的错误信息不得回显原值（否则三台 secret 一起泄露）。"""
    monkeypatch.setenv("WECOM_BOTS", '[{"key":"dev","secret":"TOPSECRET"')
    with pytest.raises(ValueError) as excinfo:
        load_wecom_bots()
    assert "TOPSECRET" not in str(excinfo.value)


def test_not_a_list(monkeypatch):
    monkeypatch.setenv("WECOM_BOTS", '{"key":"dev"}')
    with pytest.raises(ValueError):
        load_wecom_bots()


def test_missing_field(monkeypatch):
    monkeypatch.setenv("WECOM_BOTS", _dump([_bot("dev", "aibA", "")]))
    with pytest.raises(ValueError) as excinfo:
        load_wecom_bots()
    assert "secret" in str(excinfo.value)


def test_key_charset(monkeypatch):
    monkeypatch.setenv("WECOM_BOTS", _dump([_bot("Dev", "aibA", "s1")]))
    with pytest.raises(ValueError):
        load_wecom_bots()


def test_reserved_callback_key(monkeypatch):
    monkeypatch.setenv("WECOM_BOTS", _dump([_bot(CALLBACK_BOT_KEY, "aibA", "s1")]))
    with pytest.raises(ValueError):
        load_wecom_bots()


def test_duplicate_key(monkeypatch):
    monkeypatch.setenv(
        "WECOM_BOTS", _dump([_bot("dev", "aibA", "s1"), _bot("dev", "aibB", "s2")])
    )
    with pytest.raises(ValueError):
        load_wecom_bots()


def test_duplicate_bot_id(monkeypatch):
    monkeypatch.setenv(
        "WECOM_BOTS",
        _dump([_bot("dev", "aibA", "s1"), _bot("support", "aibA", "s2")]),
    )
    with pytest.raises(ValueError):
        load_wecom_bots()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `POSTGRES_HOST=localhost pytest tests/config/test_wecom_bots.py -q`
Expected: FAIL —— `ModuleNotFoundError: No module named 'src.config.wecom_bots'`

- [ ] **Step 3: 实现**

创建 `src/config/wecom_bots.py`：

```python
"""企业微信多机器人配置：从 WECOM_BOTS（JSON 数组）解析并校验每台机器人。

独立于 settings.py（后者已接近 400 行红线）。解析与校验合并在
load_wecom_bots()，由 wecom_service.start() 调用（受 WECOM_BOT_ENABLED 门控）。

错误信息只定位到下标或 key，绝不回显 WECOM_BOTS 原值或任何 secret——
三台凭证同处一个 env 变量，回显原值会批量泄露。
"""

import json
import os
import re
from dataclasses import dataclass

# 保留 key：回调 legacy 驱动占用，长连接侧不得使用
CALLBACK_BOT_KEY: str = "callback"

# key 允许的字符集：保证日志可裸写、可作 dict key
_KEY_PATTERN = re.compile(r"^[a-z0-9_-]+$")


@dataclass(frozen=True)
class WeComBotConfig:
    """单台智能机器人的长连接配置。"""

    key: str  # 业务线标识；唯一、匹配 [a-z0-9_-]+、不得为保留字 callback
    bot_id: str  # 智能机器人 BotID（= 入站报文的 aibotid）
    secret: str  # 长连接专用 Secret


def load_wecom_bots() -> list[WeComBotConfig]:
    """解析并校验 WECOM_BOTS；任何非法立即抛 ValueError（fail-fast）。

    空值返回空列表（"长连接模式下注册表不得为空"由调用方按 mode 判定）。
    """
    raw = os.getenv("WECOM_BOTS", "")
    if raw.strip() == "":
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # 不回显 raw：它含三台 secret
        raise ValueError("WECOM_BOTS 不是合法 JSON") from None
    if not isinstance(data, list):
        raise ValueError("WECOM_BOTS 必须是 JSON 数组")

    bots: list[WeComBotConfig] = []
    seen_keys: set[str] = set()
    seen_ids: set[str] = set()
    for index, item in enumerate(data):
        if not isinstance(item, dict):
            raise ValueError(f"WECOM_BOTS[{index}] 必须是对象")
        key = item.get("key")
        bot_id = item.get("bot_id")
        secret = item.get("secret")
        if not isinstance(key, str) or key == "":
            raise ValueError(f"WECOM_BOTS[{index}].key 为空")
        if not isinstance(bot_id, str) or bot_id == "":
            raise ValueError(f"WECOM_BOTS[{index}].bot_id 为空")
        if not isinstance(secret, str) or secret == "":
            raise ValueError(f"WECOM_BOTS[{index}].secret 为空")
        if _KEY_PATTERN.fullmatch(key) is None:
            raise ValueError(f"WECOM_BOTS[{index}].key 只允许 [a-z0-9_-]")
        if key == CALLBACK_BOT_KEY:
            raise ValueError(f"WECOM_BOTS[{index}].key 为保留字 {CALLBACK_BOT_KEY}")
        if key in seen_keys:
            raise ValueError(f"WECOM_BOTS[{index}].key 重复")
        if bot_id in seen_ids:
            raise ValueError(f"WECOM_BOTS[{index}].bot_id 重复")
        seen_keys.add(key)
        seen_ids.add(bot_id)
        bots.append(WeComBotConfig(key=key, bot_id=bot_id, secret=secret))
    return bots
```

- [ ] **Step 4: 跑测试确认通过**

Run: `POSTGRES_HOST=localhost pytest tests/config/test_wecom_bots.py -q`
Expected: PASS（9 passed）

- [ ] **Step 5: 提交**

```bash
git add src/config/wecom_bots.py tests/config/test_wecom_bots.py
git commit -m "feat(wecom): 新增多机器人配置模块并做启动期校验"
```

---

### Task 2: 驱动注册表与生命周期（`wecom_service`）+ settings 调整

**Files:**
- Modify: `src/config/settings.py`（WECOM 段）
- Modify: `src/services/wecom_service.py`（整体重写）
- Test: `tests/services/test_wecom_service.py`（整体重写）

**Interfaces:**
- Consumes: Task 1 的 `CALLBACK_BOT_KEY` / `load_wecom_bots`；既有 `CallbackDriver` / `WeComCrypto` / `LongConnectionDriver`；`src.core.logging.encode_value`
- Produces:
  - 模块级 `_drivers: dict[str, ChannelDriver]`（键 = `bot_key` 或保留键 `callback`）
  - 模块级 `_bot_key_by_aibotid: dict[str, str]`（Task 3 的反查表）
  - `async start() -> None` / `async stop() -> None`
  - `get_driver(bot_key: str) -> ChannelDriver`（未命中抛 `RuntimeError`）
  - `get_callback_driver() -> CallbackDriver`
  - `_default_handler` **保持不变**（Task 3 才改）

- [ ] **Step 1: 改 `settings.py`**

把 WECOM 段的 `WECOM_BOT_MODE` 默认值改为 `long_connection`，删除 `WECOM_BOT_ID` / `WECOM_BOT_SECRET` 两行，并给回调三字段加注释。修改后该段为：

```python
# ── 企业微信智能机器人（多机器人长连接 / URL 回调 legacy）──
WECOM_BOT_ENABLED: bool = os.getenv("WECOM_BOT_ENABLED", "false").lower() in (
    "1",
    "true",
    "yes",
)
# 接入模式：long_connection=多机器人长连接（默认）；callback=URL 回调（legacy 单机器人）
WECOM_BOT_MODE: str = os.getenv("WECOM_BOT_MODE", "long_connection")
# 多机器人长连接凭证：JSON 数组，形如 [{"key","bot_id","secret"}]，必须单行
# 解析见 src/config/wecom_bots.py 的 load_wecom_bots()
WECOM_BOTS: str = os.getenv("WECOM_BOTS", "")
# 以下三个仅 callback 模式使用（legacy）
WECOM_BOT_TOKEN: str = os.getenv("WECOM_BOT_TOKEN", "")
WECOM_BOT_ENCODING_AES_KEY: str = os.getenv("WECOM_BOT_ENCODING_AES_KEY", "")
WECOM_BOT_RECEIVE_ID: str = os.getenv("WECOM_BOT_RECEIVE_ID", "")
WECOM_BOT_LOG_CONTENT: bool = os.getenv("WECOM_BOT_LOG_CONTENT", "false").lower() in (
    "1",
    "true",
    "yes",
)
```

- [ ] **Step 2: 重写 `tests/services/test_wecom_service.py`（先写失败测试）**

整体替换为：

```python
"""企微编排单测：驱动注册表启停、逐台降级、fail-fast、callback legacy。"""

import json

import pytest
from loguru import logger

from src.config import settings
from src.config.wecom_bots import CALLBACK_BOT_KEY
from src.services import wecom_service


def _bots_env(monkeypatch, items: list[tuple[str, str, str]]) -> None:
    """把 (key, bot_id, secret) 列表写进 WECOM_BOTS 环境变量（单行 JSON）。"""
    data = [{"key": k, "bot_id": i, "secret": s} for (k, i, s) in items]
    monkeypatch.setenv("WECOM_BOTS", json.dumps(data, separators=(",", ":")))


class _StubDriver:
    name = "wecom_long_connection"

    def __init__(self, bot_id: str, secret: str, handler: object, *, fail: bool = False):
        self.bot_id = bot_id
        self.secret = secret
        self.handler = handler
        self.started = False
        self.stopped = False
        self._fail = fail

    async def start(self) -> None:
        if self._fail:
            raise RuntimeError("connect failed")
        self.started = True

    async def stop(self) -> None:
        self.stopped = True


class _StopFailingDriver(_StubDriver):
    async def stop(self) -> None:
        raise RuntimeError("disconnect failed")


def _install_stub(monkeypatch, *, fail_ids: set[str] | None = None):
    """把 LongConnectionDriver 换成 stub 工厂；返回创建记录列表。"""
    created: list[_StubDriver] = []
    fail_ids = fail_ids or set()

    def _factory(bot_id, secret, handler):
        driver = _StubDriver(bot_id, secret, handler, fail=bot_id in fail_ids)
        created.append(driver)
        return driver

    monkeypatch.setattr(wecom_service, "LongConnectionDriver", _factory)
    return created


@pytest.fixture(autouse=True)
def _reset_state():
    wecom_service._drivers = {}
    wecom_service._bot_key_by_aibotid = {}
    yield
    wecom_service._drivers = {}
    wecom_service._bot_key_by_aibotid = {}


@pytest.mark.asyncio
async def test_disabled_start_is_noop(monkeypatch):
    monkeypatch.setattr(settings, "WECOM_BOT_ENABLED", False)
    await wecom_service.start()
    with pytest.raises(RuntimeError):
        wecom_service.get_driver("dev")


@pytest.mark.asyncio
async def test_callback_mode_builds_driver(monkeypatch):
    monkeypatch.setattr(settings, "WECOM_BOT_ENABLED", True)
    monkeypatch.setattr(settings, "WECOM_BOT_MODE", "callback")
    monkeypatch.setattr(settings, "WECOM_BOT_TOKEN", "tok")
    monkeypatch.setattr(settings, "WECOM_BOT_ENCODING_AES_KEY", "a" * 43)
    await wecom_service.start()
    assert wecom_service.get_callback_driver().name == "wecom_callback"
    assert wecom_service.get_driver(CALLBACK_BOT_KEY).name == "wecom_callback"


@pytest.mark.asyncio
async def test_callback_bad_aes_key_is_fail_fast(monkeypatch):
    monkeypatch.setattr(settings, "WECOM_BOT_ENABLED", True)
    monkeypatch.setattr(settings, "WECOM_BOT_MODE", "callback")
    monkeypatch.setattr(settings, "WECOM_BOT_TOKEN", "tok")
    monkeypatch.setattr(settings, "WECOM_BOT_ENCODING_AES_KEY", "short")
    with pytest.raises(RuntimeError):
        await wecom_service.start()


@pytest.mark.asyncio
async def test_long_connection_builds_all_bots(monkeypatch):
    monkeypatch.setattr(settings, "WECOM_BOT_ENABLED", True)
    monkeypatch.setattr(settings, "WECOM_BOT_MODE", "long_connection")
    _bots_env(
        monkeypatch,
        [("dev", "aibA", "s1"), ("support", "aibB", "s2")],
    )
    created = _install_stub(monkeypatch)

    await wecom_service.start()

    assert len(created) == 2
    assert all(d.started for d in created)
    assert wecom_service.get_driver("dev").bot_id == "aibA"
    assert wecom_service.get_driver("support").bot_id == "aibB"
    # 反查表已建立
    assert wecom_service._bot_key_by_aibotid == {"aibA": "dev", "aibB": "support"}


@pytest.mark.asyncio
async def test_long_connection_single_failure_degrades_others(monkeypatch):
    monkeypatch.setattr(settings, "WECOM_BOT_ENABLED", True)
    monkeypatch.setattr(settings, "WECOM_BOT_MODE", "long_connection")
    _bots_env(
        monkeypatch,
        [("dev", "aibA", "s1"), ("bad", "aibB", "s2")],
    )
    _install_stub(monkeypatch, fail_ids={"aibB"})

    # 单台失败不向外抛
    await wecom_service.start()

    assert wecom_service.get_driver("dev").bot_id == "aibA"
    with pytest.raises(RuntimeError):
        wecom_service.get_driver("bad")


@pytest.mark.asyncio
async def test_long_connection_empty_registry_is_fail_fast(monkeypatch):
    monkeypatch.setattr(settings, "WECOM_BOT_ENABLED", True)
    monkeypatch.setattr(settings, "WECOM_BOT_MODE", "long_connection")
    monkeypatch.setenv("WECOM_BOTS", "")
    with pytest.raises(RuntimeError):
        await wecom_service.start()


@pytest.mark.asyncio
async def test_long_connection_bad_json_is_fail_fast(monkeypatch):
    """配置错误必须 fail-fast，不得被逐台降级吞掉。"""
    monkeypatch.setattr(settings, "WECOM_BOT_ENABLED", True)
    monkeypatch.setattr(settings, "WECOM_BOT_MODE", "long_connection")
    monkeypatch.setenv("WECOM_BOTS", "[{")
    with pytest.raises(ValueError):
        await wecom_service.start()


@pytest.mark.asyncio
async def test_unknown_mode_is_fail_fast(monkeypatch):
    monkeypatch.setattr(settings, "WECOM_BOT_ENABLED", True)
    monkeypatch.setattr(settings, "WECOM_BOT_MODE", "nonsense")
    with pytest.raises(RuntimeError):
        await wecom_service.start()


@pytest.mark.asyncio
async def test_long_connection_logs_anchor(monkeypatch):
    """启动锚点日志：成功台数 / 总台数（供冒烟区分"端点不可达"与"未切模式"）。"""
    monkeypatch.setattr(settings, "WECOM_BOT_ENABLED", True)
    monkeypatch.setattr(settings, "WECOM_BOT_MODE", "long_connection")
    _bots_env(monkeypatch, [("dev", "aibA", "s1"), ("support", "aibB", "s2")])
    _install_stub(monkeypatch, fail_ids={"aibB"})

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


@pytest.mark.asyncio
async def test_stop_disconnects_all_and_is_idempotent(monkeypatch):
    monkeypatch.setattr(settings, "WECOM_BOT_ENABLED", True)
    monkeypatch.setattr(settings, "WECOM_BOT_MODE", "long_connection")
    _bots_env(monkeypatch, [("dev", "aibA", "s1")])
    created = _install_stub(monkeypatch)

    await wecom_service.start()
    await wecom_service.stop()
    assert created[0].stopped is True
    assert wecom_service._drivers == {}
    # 幂等：再次 stop 不报错
    await wecom_service.stop()


@pytest.mark.asyncio
async def test_stop_tolerates_single_failure(monkeypatch):
    monkeypatch.setattr(settings, "WECOM_BOT_ENABLED", True)
    monkeypatch.setattr(settings, "WECOM_BOT_MODE", "long_connection")
    _bots_env(monkeypatch, [("dev", "aibA", "s1")])
    monkeypatch.setattr(
        wecom_service, "LongConnectionDriver", lambda *a, **k: _StopFailingDriver(*a, **k)
    )

    await wecom_service.start()
    await wecom_service.stop()  # 不抛
    assert wecom_service._drivers == {}


@pytest.mark.asyncio
async def test_get_callback_driver_rejects_non_callback_mode(monkeypatch):
    monkeypatch.setattr(settings, "WECOM_BOT_ENABLED", True)
    monkeypatch.setattr(settings, "WECOM_BOT_MODE", "long_connection")
    _bots_env(monkeypatch, [("dev", "aibA", "s1")])
    _install_stub(monkeypatch)
    await wecom_service.start()
    with pytest.raises(RuntimeError):
        wecom_service.get_callback_driver()


def test_get_callback_driver_rejects_when_not_started(monkeypatch):
    monkeypatch.setattr(settings, "WECOM_BOT_MODE", "callback")
    with pytest.raises(RuntimeError):
        wecom_service.get_callback_driver()


def test_get_callback_driver_rejects_wrong_driver_type(monkeypatch):
    monkeypatch.setattr(settings, "WECOM_BOT_MODE", "callback")

    class _WrongDriver:
        name = "wecom_long_connection"

        async def start(self) -> None:
            return

        async def stop(self) -> None:
            return

    wecom_service._drivers[CALLBACK_BOT_KEY] = _WrongDriver()
    with pytest.raises(RuntimeError):
        wecom_service.get_callback_driver()
```

- [ ] **Step 3: 跑测试确认失败**

Run: `POSTGRES_HOST=localhost pytest tests/services/test_wecom_service.py -q`
Expected: FAIL —— 多数用例因 `wecom_service` 仍是单例 `_driver` 而报 `AttributeError`/`RuntimeError`

- [ ] **Step 4: 重写 `src/services/wecom_service.py`**

整体替换为：

```python
"""企微智能机器人通道的编排：按 WECOM_BOT_MODE 装配驱动 + 注入默认 handler。

多机器人（长连接）：每台一个驱动，集中存放于驱动注册表；逐台装配/降级/关停。
回调保留为 legacy 单机器人（存于保留键 `callback`）。
本轮 handler 只打日志并回写死回复；后续替换为「调 agent 跑 RAG」。
"""

from loguru import logger

from src.channels.base import ChannelDriver, InboundMessage, ReplySink
from src.channels.wecom.callback import CallbackDriver
from src.channels.wecom.crypto import WeComCrypto
from src.channels.wecom.long_connection import LongConnectionDriver
from src.config import settings
from src.config.const import WECOM_REPLY_PLACEHOLDER
from src.config.wecom_bots import CALLBACK_BOT_KEY, load_wecom_bots
from src.core.logging import encode_value

# 接入模式取值
_MODE_CALLBACK = "callback"
_MODE_LONG_CONNECTION = "long_connection"

# 驱动注册表：键 = bot_key（长连接）或保留键 callback（回调 legacy）
_drivers: dict[str, ChannelDriver] = {}
# 反查表：aibotid(BotID) → bot_key；由 start() 一次性构建（handler 只读）
_bot_key_by_aibotid: dict[str, str] = {}


async def _default_handler(msg: InboundMessage, sink: ReplySink) -> None:
    """默认 handler：打结构性日志；消息回写死流式回复，事件不回包。"""
    text_len = 0
    if msg.text:
        text_len = len(msg.text)
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
    await sink.reply_stream(WECOM_REPLY_PLACEHOLDER, finish=True)


def _validate_credentials() -> None:
    """callback 模式启动期校验：凭证非法即抛，不留请求期降级。"""
    if not settings.WECOM_BOT_TOKEN:
        raise RuntimeError("WECOM_BOT_TOKEN 未配置")
    if len(settings.WECOM_BOT_ENCODING_AES_KEY) != 43:
        raise RuntimeError("WECOM_BOT_ENCODING_AES_KEY 必须为 43 位")


async def start() -> None:
    """启动通道：enabled 时按 mode 装配驱动，否则 no-op。"""
    global _drivers, _bot_key_by_aibotid
    if not settings.WECOM_BOT_ENABLED:
        return
    mode = settings.WECOM_BOT_MODE

    if mode == _MODE_CALLBACK:
        _validate_credentials()
        crypto = WeComCrypto(
            settings.WECOM_BOT_TOKEN,
            settings.WECOM_BOT_ENCODING_AES_KEY,
            settings.WECOM_BOT_RECEIVE_ID,
        )
        _drivers = {CALLBACK_BOT_KEY: CallbackDriver(crypto, _default_handler)}
        _bot_key_by_aibotid = {}
        return

    if mode != _MODE_LONG_CONNECTION:
        raise RuntimeError(f"未知 WECOM_BOT_MODE: {mode}")

    # 解析/校验置于逐台 try/except 之外：配置错误必须 fail-fast，不得被降级吞掉
    bots = load_wecom_bots()
    if not bots:
        raise RuntimeError("WECOM_BOT_MODE=long_connection 但 WECOM_BOTS 为空")

    _bot_key_by_aibotid = {bot.bot_id: bot.key for bot in bots}
    _drivers = {}
    for bot in bots:
        driver = LongConnectionDriver(bot.bot_id, bot.secret, _default_handler)
        try:
            await driver.start()
        except Exception as e:  # noqa: BLE001
            # 通道可选：单台连接失败不拖垮其余台，也不阻塞应用启动
            logger.warning(
                "[wecom] bot connect failed, skipped bot_key={} err={}",
                encode_value(bot.key),
                e,
            )
            continue
        _drivers[bot.key] = driver

    logger.info("[wecom] bots connected n={} total={}", len(_drivers), len(bots))


async def stop() -> None:
    """停止通道：逐台断开，单台失败不阻塞其余，幂等。"""
    global _drivers, _bot_key_by_aibotid
    for bot_key, driver in list(_drivers.items()):
        try:
            await driver.stop()
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "[wecom] bot stop failed bot_key={} err={}",
                encode_value(bot_key),
                e,
            )
    _drivers = {}
    _bot_key_by_aibotid = {}


def get_driver(bot_key: str) -> ChannelDriver:
    """取指定机器人的驱动；未命中抛 RuntimeError。"""
    driver = _drivers.get(bot_key)
    if driver is None:
        raise RuntimeError(f"wecom driver 未启动或不存在: {bot_key}")
    return driver


def get_callback_driver() -> CallbackDriver:
    """取回调驱动；非 callback 模式或未启动时抛 RuntimeError。"""
    if settings.WECOM_BOT_MODE != _MODE_CALLBACK:
        raise RuntimeError("当前非 callback 模式")
    driver = _drivers.get(CALLBACK_BOT_KEY)
    if driver is None:
        raise RuntimeError("wecom driver 未启动")
    if not isinstance(driver, CallbackDriver):
        # 契约要求统一抛 RuntimeError（路由按此捕获），故不换成 TypeError
        raise RuntimeError("当前驱动不是 CallbackDriver")  # noqa: TRY004
    return driver
```

- [ ] **Step 5: 跑测试确认通过**

Run: `POSTGRES_HOST=localhost pytest tests/services/test_wecom_service.py -q`
Expected: PASS（14 passed）

- [ ] **Step 6: 提交**

```bash
git add src/config/settings.py src/services/wecom_service.py tests/services/test_wecom_service.py
git commit -m "feat(wecom): 编排层改为多机器人驱动注册表并逐台降级"
```

---

### Task 3: 入站分发与日志 + `run()` 守卫

**Files:**
- Modify: `src/services/wecom_service.py`（`_default_handler` + 新增 `_resolve_bot_key`）
- Modify: `src/channels/wecom/long_connection.py`（错误日志加 `bot_id`）
- Modify: `docs/agents/logging-rules.md`（`[wecom]` 前缀描述）
- Test: `tests/services/test_wecom_service.py`（追加分发用例）
- Test: Create `tests/channels/test_long_connection_no_run.py`

**Interfaces:**
- Consumes: Task 2 的 `_bot_key_by_aibotid` / `_drivers`；`CALLBACK_BOT_KEY`；`encode_value`
- Produces: `_resolve_bot_key(aibotid: str) -> str | None`

- [ ] **Step 1: 追加失败测试**

先在 `tests/services/test_wecom_service.py` 顶部导入区加入 `from src.channels.base import InboundMessage`，再在文件末尾追加：

```python
class _RecorderSink:
    def __init__(self) -> None:
        self.replies: list[tuple[str, bool]] = []

    async def reply_stream(self, content: str, finish: bool) -> None:
        self.replies.append((content, finish))


def _inbound(aibotid: str, msgtype: str = "text") -> InboundMessage:
    return InboundMessage(
        msgid="M1",
        aibotid=aibotid,
        chatid=None,
        chattype="single",
        from_userid="U1",
        msgtype=msgtype,
        text="hi",
        event_type=None,
        raw={},
    )


def test_resolve_bot_key_callback_mode_returns_reserved_key(monkeypatch):
    monkeypatch.setattr(settings, "WECOM_BOT_MODE", "callback")
    assert wecom_service._resolve_bot_key("anything") == CALLBACK_BOT_KEY


def test_resolve_bot_key_long_connection_lookup(monkeypatch):
    monkeypatch.setattr(settings, "WECOM_BOT_MODE", "long_connection")
    wecom_service._bot_key_by_aibotid = {"aibA": "dev"}
    assert wecom_service._resolve_bot_key("aibA") == "dev"
    assert wecom_service._resolve_bot_key("unknown") is None


@pytest.mark.asyncio
async def test_handler_replies_for_known_bot(monkeypatch):
    monkeypatch.setattr(settings, "WECOM_BOT_MODE", "long_connection")
    monkeypatch.setattr(settings, "WECOM_BOT_LOG_CONTENT", False)
    wecom_service._bot_key_by_aibotid = {"aibA": "dev"}
    sink = _RecorderSink()
    await wecom_service._default_handler(_inbound("aibA"), sink)
    assert len(sink.replies) == 1
    assert sink.replies[0][1] is True


@pytest.mark.asyncio
async def test_handler_silent_for_unknown_bot(monkeypatch):
    monkeypatch.setattr(settings, "WECOM_BOT_MODE", "long_connection")
    monkeypatch.setattr(settings, "WECOM_BOT_LOG_CONTENT", False)
    wecom_service._bot_key_by_aibotid = {"aibA": "dev"}
    sink = _RecorderSink()
    await wecom_service._default_handler(_inbound("unknown"), sink)
    assert sink.replies == []


@pytest.mark.asyncio
async def test_handler_silent_for_event(monkeypatch):
    monkeypatch.setattr(settings, "WECOM_BOT_MODE", "long_connection")
    monkeypatch.setattr(settings, "WECOM_BOT_LOG_CONTENT", False)
    wecom_service._bot_key_by_aibotid = {"aibA": "dev"}
    sink = _RecorderSink()
    await wecom_service._default_handler(_inbound("aibA", msgtype="event"), sink)
    assert sink.replies == []
```

- [ ] **Step 2: 跑测试确认失败**

Run: `POSTGRES_HOST=localhost pytest tests/services/test_wecom_service.py -q -k "resolve or handler"`
Expected: FAIL —— `AttributeError: module 'src.services.wecom_service' has no attribute '_resolve_bot_key'`

- [ ] **Step 3: 实现分发（改 `wecom_service.py`）**

在 `_default_handler` **之前**新增 `_resolve_bot_key`，并把 `_default_handler` 整体替换为：

```python
def _resolve_bot_key(aibotid: str) -> str | None:
    """把入站 aibotid 映射到 bot_key；未知返回 None。

    回调 legacy 模式下唯一驱动即回调驱动，固定返回保留键。
    """
    if settings.WECOM_BOT_MODE == _MODE_CALLBACK:
        return CALLBACK_BOT_KEY
    return _bot_key_by_aibotid.get(aibotid)


async def _default_handler(msg: InboundMessage, sink: ReplySink) -> None:
    """默认 handler：按 bot_key 分发；消息回写死流式回复，事件不回包。"""
    bot_key = _resolve_bot_key(msg.aibotid)
    if bot_key is None:
        logger.warning(
            "[wecom] inbound from unknown bot aibotid={}", encode_value(msg.aibotid)
        )
        return

    text_len = 0
    if msg.text:
        text_len = len(msg.text)
    logger.info(
        "[wecom] inbound bot_key={} msgid={} chattype={} msgtype={} event_type={} text_len={}",
        encode_value(bot_key),
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
    await sink.reply_stream(WECOM_REPLY_PLACEHOLDER, finish=True)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `POSTGRES_HOST=localhost pytest tests/services/test_wecom_service.py -q`
Expected: PASS（19 passed）

- [ ] **Step 5: 改 `long_connection.py` 的两条错误日志**

把 `_make_handler` 内两条 `logger.error` 改为带 `bot_id`（并保留中文注释），`_handle` 改为：

```python
        async def _handle(frame: dict[str, Any]) -> None:
            try:
                msg = parse_inbound(frame["body"])
            except Exception as e:  # noqa: BLE001
                logger.error(
                    "[wecom] parse inbound failed bot_id={} err={}",
                    encode_value(self._bot_id),
                    e,
                )
                return
            sink = _WsSink(client, frame, generate_req_id("stream"))
            try:
                await self._handler(msg, sink)
            except Exception as e:  # noqa: BLE001
                logger.error(
                    "[wecom] handler failed bot_id={} msgid={} err={}",
                    encode_value(self._bot_id),
                    msg.msgid,
                    e,
                )
```

并在文件顶部导入区加入：

```python
from src.core.logging import encode_value
```

- [ ] **Step 6: 写 `run()` 守卫测试**

创建 `tests/channels/test_long_connection_no_run.py`：

```python
"""守卫：长连接驱动不得调用官方 SDK 的 run()（它自建事件循环，与 FastAPI 冲突）。"""

from pathlib import Path

_DRIVER_SOURCE = (
    Path(__file__).resolve().parents[2] / "src" / "channels" / "wecom" / "long_connection.py"
)


def test_driver_source_does_not_call_run():
    source = _DRIVER_SOURCE.read_text(encoding="utf-8")
    assert ".run(" not in source
```

- [ ] **Step 7: 跑新增测试**

Run: `POSTGRES_HOST=localhost pytest tests/channels/test_long_connection_no_run.py tests/channels/test_long_connection_driver.py -q`
Expected: PASS

- [ ] **Step 8: 更新 `docs/agents/logging-rules.md` 前缀主表**

把 `[wecom]` 那一行改为：

```markdown
| `[wecom]` | 企业微信智能机器人通道（services/wecom_service + channels/wecom/{callback,long_connection}） |
```

- [ ] **Step 9: 提交**

```bash
git add src/services/wecom_service.py src/channels/wecom/long_connection.py tests/services/test_wecom_service.py tests/channels/test_long_connection_no_run.py docs/agents/logging-rules.md
git commit -m "feat(wecom): 入站按 aibotid 分发并补 run() 守卫"
```

---

### Task 4: 文档登记与官方 SDK 说明

**Files:**
- Modify: `.env.example`、`.env.template`
- Modify: `docs/agents/code-map.md`、`docs/agents/glossary.md`、`docs/agents/cookbook.md`
- Modify: `docs/agents/api_contract.md`、`src/api/README.md`
- Modify: `docs/agents/deploy-runbook.md`

**Interfaces:**
- Consumes: 前述任务的配置形状（`WECOM_BOTS` 单行）、`src/config/wecom_bots.py`、`wecom-aibot-python-sdk`
- Produces: 文档（无代码接口）

- [ ] **Step 1: 改 `.env.example` 与 `.env.template` 的企微块**

两文件均把原企微段整体替换为（注意 `.env.template` 同形，仅套用其既有风格）：

```ini
# ── 企业微信智能机器人（多机器人长连接）──
# 启用通道（关闭时回调端点 404、不解析 WECOM_BOTS）
WECOM_BOT_ENABLED=false
# 接入方式：long_connection（多机器人长连接）| callback（URL 回调，legacy 单机器人）
WECOM_BOT_MODE=long_connection
# 多机器人配置：JSON 数组，★必须写在一行★（多行会导致解析失败）
#   每台：key=业务线标识（[a-z0-9_-]，唯一，不得用保留字 callback）
#         bot_id=智能机器人 BotID（= 入站报文的 aibotid）
#         secret=长连接专用 Secret
# 示例（占位值，勿直接使用）：
# WECOM_BOTS=[{"key":"dev","bot_id":"<BotID>","secret":"<Secret>"},{"key":"support","bot_id":"<BotID>","secret":"<Secret>"}]
WECOM_BOTS=
# 是否打印用户消息正文（默认关，防泄露）
WECOM_BOT_LOG_CONTENT=false
# ── 以下仅 WECOM_BOT_MODE=callback（legacy）使用 ──
WECOM_BOT_TOKEN=
WECOM_BOT_ENCODING_AES_KEY=
WECOM_BOT_RECEIVE_ID=
# ⚠ 长连接：每机器人同时只允许一条连接，新连接会踢掉旧连接 ——
#   多实例部署时，仅在一台实例开启 WECOM_BOT_ENABLED=true，其余置 false
```

- [ ] **Step 2: `code-map.md` 登记多机器人与 SDK 说明**

- 在 `src/` 结构块里，`channels/` 行后补 `config/` 说明一行（若已有 `config/` 行，则在其描述末尾追加 `wecom_bots(多机器人配置解析/校验)`）。
- 在「常见改动落点速查」表中，把「加/改接入通道（企微回调/长连接）」那行的第 3 列补上 `src/config/wecom_bots.py`。
- 在 `channels/` 相关段落之后，新增一段「官方 aibot SDK 用法与约束」：

```markdown
> **官方 `aibot` SDK（长连接）用法与约束** —— 依赖 `wecom-aibot-python-sdk`（导入名 `aibot`）。
> SDK 承担：连接认证（`aibot_subscribe`）、心跳保活、断线重连、事件分发、流式回复、模板卡片、文件解密。
> 本仓库**只**使用其公开接口：`connect()` / `disconnect()` / `on()` / `reply_stream()`。
> **禁止调用 `run()`** —— 它自建事件循环，与 FastAPI 冲突（有守卫测试 `tests/channels/test_long_connection_no_run.py`）。
> 契约：每机器人同时只能一条连接（新连接踢旧连接）。依赖与部署影响见 `deploy-runbook.md`。
```

- [ ] **Step 3: `glossary.md` 补术语**

在「接入通道（channel）」小节内追加：

```markdown
- **多机器人配置（`WECOM_BOTS`）**：企微智能机器人的多台配置，JSON 数组（**必须单行**），每台 `{key, bot_id, secret}`；解析与校验见 `src/config/wecom_bots.py`，由 `wecom_service.start()` 在受 `WECOM_BOT_ENABLED` 门控下调用。
- **保留键 `callback`**：`callback` 是长连接机器人 `key` 的保留字；回调 legacy 驱动以该键存入驱动注册表。
```

- [ ] **Step 4: `cookbook.md` 长连接条目补多机器人**

在既有「企微智能机器人长连接接入（开发期）」条目的 `**步骤**` 中，把"填 `.env`"一步改为写明：填 `WECOM_BOTS`（**单行 JSON**，三台）、`WECOM_BOT_MODE=long_connection`；并在 `**注意事项**` 补：逐台降级（某台连不上只记 warning）、启动锚点日志 `[wecom] bots connected n=<ok> total=<all>` 用于区分"端点不可达"与"未切长连接模式"、目标机 `.env` 须人工同步。

- [ ] **Step 5: `api_contract.md` 与 `src/api/README.md`**

核对并（若未写明）补充：`/api/wecom/callback` 仅在 `WECOM_BOT_MODE=callback` 时可用；`long_connection` 模式下返回 404。

- [ ] **Step 6: `deploy-runbook.md` 两处**

- §1.4：`.env` 示例改为含 `WECOM_BOTS`（单行）的形态，并注明"必须单行"。
- 新增（或并入既有依赖说明）一段：本通道新增依赖 `wecom-aibot-python-sdk`（传递依赖 `websockets`/`aiohttp`/`pyee`/`cryptography`/`certifi`）与 `pycryptodome` ⇒ 合并后须**重建 app 镜像**并**预热云效 PyPI 代理仓 `repo-okxha`**。

- [ ] **Step 7: 跑文档闸门并提交**

Run: `POSTGRES_HOST=localhost python -m src.cli.check_docs`
Expected: `0 error`（warn 数量与改动前同量级即可）

```bash
git add .env.example .env.template docs/agents/code-map.md docs/agents/glossary.md docs/agents/cookbook.md docs/agents/api_contract.md src/api/README.md docs/agents/deploy-runbook.md
git commit -m "docs(wecom): 登记多机器人配置与官方 aibot SDK 说明"
```

---

## 收尾（全量门禁 + 冒烟）

- [ ] 全量测试：`POSTGRES_HOST=localhost pytest tests/ -q` → 全绿（`tests/parsers/*` 若因 worktree 缺 `data/test_docs` 夹具失败，先确认软链 `data/test_docs` 存在）
- [ ] `ruff check .` 无错误；`pyright src/` 不新增 error
- [ ] 冒烟（需人工在企微后台把三台机器人改为「API 模式 → 使用长连接」）：@ 每台机器人 → 收到占位文案；日志出现 `bot_key=<key>`；启动锚点 `n=3 total=3`
