# 企微接入 Agent 管线 — 阶段 4a（驱动可靠性：认证等待与注册表语义）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让驱动**以「认证就绪」为准**判定可用（而非 `connect()` 返回即成功），并把凭证类认证失败与"被顶"如实反映到注册表与启动锚点，消除"死代码式 try/except + 锚点恒真"这一失真。

**Architecture:** 改动集中在两处：`channels/wecom/long_connection.py` 增加**认证等待**（订阅 SDK 的 `authenticated` / `error` 事件）与**就绪状态**（`is_ready`：未认证超时 / 凭证类失败 / 被顶 ⇒ 非就绪）；`services/wecom_service.py` 把逐台**并行启动**、**注册表收全部已启动者**（含未就绪，供 `stop()` 关闭）、**锚点只计认证就绪台数**。业务 handler、投影层、SDK 封装均不动。

**Tech Stack:** Python 3.11+ / asyncio（`asyncio.wait_for` + `Future`）/ loguru / pytest（`@pytest.mark.asyncio`）/ 官方 `aibot` SDK 的 `WSClient` 事件。

**Spec:** `openspec/changes/wecom-agent-bridge/design.md` 的 **D11**（认证等待 + 被顶处置 + 注册表语义拆分 + 并行启动）与 `tasks.md` 的 **5.1**；**SDK 行为以 `docs/agents/wecom-sdk-facts.md` 的 E3/E4 实测为准**（`connect()` 不等认证；失败只走 `error`、`errcode=853000`、**不抛不断连也不重连**；被顶走 `event.disconnected_event`）。

**Worktree:** `/root/code/corporate_rag-wecom-stage4`（分支 `feat/wecom-agent-bridge-stage4`，基线 `3a828ae`）。

## Global Constraints

以下为项目级硬约束，**每个任务的要求都隐含包含本节**；值一律照抄。

- **层间规则**：`channels/` 不得 import `api/`；本计划只动 `channels/wecom/long_connection.py` 与 `services/wecom_service.py` 及其测试。
- **文件与函数红线**：单文件 ≤ 400 行；单函数 ≤ 80 行。
- **代码风格**：**不用三元表达式**（写完整 if/else）；类型不确定处用显式 `isinstance` / `is not None`，**不得**用 `getattr(x, "attr", default)` 兜底。
- **日志**：英文 `k=v` + `[wecom]` 前缀；**不得 `print`**；对不可信值用 `encode_value`。
- **不用 `asyncio.gather` 的裸 `await` 返回值**：必须 `return_exceptions=True` 并逐项判 `isinstance(result, BaseException)`（逐台降级语义）。
- **不阻塞应用启动**：认证等待 SHALL 有上界（**5 秒**），且三台 SHALL 并行启动（设计 D11）。
- **测试**：宿主侧一律 `POSTGRES_HOST=localhost pytest ...`；异步用例用 `@pytest.mark.asyncio`。**不得削弱既有断言**：本计划允许对"锚点口径"这类**语义确已变更**的断言做等价更新，但必须**同时补一条反向用例**（未就绪台 ⇒ 不计入 `n`）。
- **worktree 环境既有事实**：缺 gitignored 夹具 `data/test_docs/*` → `tests/parsers/` 的 `FileNotFoundError` 属**环境性**，别修；`.venv` / `.env` 已软链到主仓。
- **提交命令形状**：先单独 `git add <文件>` → `git diff --cached --name-only` 核对 → 再 `git commit`。**不要**写 `git add … && git commit … > log`（`add` 失败会短路，连日志都不生成）。**注意 `openspec/` 是软链**：改 change 工件必须用真实路径 `docs/openspec/...`。
- **SDK 实测约束（不得凭猜测编码）**：`connect()` **不等认证**（实测差 ~0.17s）；认证失败**只发 `error` 事件**，内容含 `Authentication failed: … (code: 853000)`，**SDK 不抛、不断连、不重连**；被顶先收 `event.disconnected_event`、随后服务端才关连接并触发 SDK 重连（已被阶段 A 的"主动断开不抢回"处置）。

---

## 阶段地图

| 阶段 | 范围 | 状态 |
|---|---|---|
| 0 / 1 / 2 / 3 | Spike / 编排下沉 / 投影层 / 桥接 handler | ✅ 已并入 `dev-wsl`（确认点 A、B 已过） |
| 阶段 A | 组 5.2 被顶处置（订阅 `disconnected_event`） | ✅ 已并入 |
| **4a（本文件）** | **组 5.1：认证等待 + 注册表语义 + 并行启动 + 锚点口径** | 本次实施 |
| 4b | 组 6 + 1.5：澄清**呈现**与**仅触发者**回填、答案下沉（+ 可选卡片二期） | 待写 plan（见文末草案） |
| 5 | 组 7：灰度 E2E / 鲁棒验收 / 群聊验收 / 文档 / 发布 runbook | 未开始 |

**4a 的验收面**：单测覆盖三类就绪结局（认证成功 / 等待超时 / 凭证类失败）与非就绪口径；`wecom_service` 并行启动、注册表含未就绪者、锚点只计就绪台数。**不需要真实连网**（凭证类失败路径已有 E3 实测帧形状可 mock）。

---

## File Structure

| 文件 | 责任 | 动作 |
|---|---|---|
| `src/channels/wecom/long_connection.py` | 加认证等待、就绪状态、凭证类失败判据 | 修改 |
| `src/services/wecom_service.py` | 并行启动、注册表语义拆分、锚点口径、`is_ready` 查询 | 修改 |
| `tests/channels/test_long_connection_driver.py` | 三类就绪结局 + 凭证判据用例 | 修改 |
| `tests/services/test_wecom_service.py` | 并行启动 / 注册表 / 锚点口径用例（含 stub 加 `is_ready`） | 修改 |
| `docs/agents/logging-rules.md` | 锚点口径注记（`n=` 改为"认证就绪台数"） | 修改（Task 4） |
| `docs/agents/code-map.md` | 驱动**就绪语义**与"被顶即非就绪"落点 | 修改（Task 4） |
| `docs/agents/defensive-patterns.md` | 新增一条防复发规则（不得以 `connect()` 返回值判定就绪） | 修改（Task 4） |

---

### Task 1: 驱动「等认证 + 就绪状态」

**Files:**
- Modify: `src/channels/wecom/long_connection.py`
- Modify: `tests/channels/test_long_connection_driver.py`

**Interfaces:**
- Consumes: 既有 `_FakeClient`（测试替身，已支持 `on` / `connect` / `disconnect`）与阶段 A 的 `_DISPLACED_EVENT` 处理。
- Produces:
  - `long_connection` 模块级常量 `_AUTH_TIMEOUT_SECONDS: float = 5.0`（测试用 `monkeypatch.setattr(lc, "_AUTH_TIMEOUT_SECONDS", …)` 覆盖）
  - `LongConnectionDriver.is_ready -> bool`（只读属性）
  - `start()` 返回时已完成"至多 5s 的认证等待"；`stop()` 后 `is_ready` 为 False

- [ ] **Step 1: 写失败测试（追加到 `tests/channels/test_long_connection_driver.py`）**

```python
class _FakeClientWithAuth(_FakeClient):
    """可手动触发 authenticated / error 的伪 client。"""

    def __init__(self, options: Any, *, emit_auth: bool = True):
        super().__init__(options)
        self.disconnect_calls = 0
        self._emit_auth = emit_auth

    async def connect(self) -> "_FakeClientWithAuth":
        self.connected = True
        if self._emit_auth:
            self.emit("authenticated")
        return self

    def emit(self, event: str, payload: Any = None) -> None:
        """同步触发已注册处理器（对标 pyee 的行为）。"""
        handler = self.handlers.get(event)
        if handler is None:
            return
        if payload is None:
            handler()
            return
        handler(payload)

    def disconnect(self) -> None:
        self.disconnect_calls += 1
        self.connected = False


def _auth_patch(monkeypatch, *, emit_auth: bool):
    holder: dict[str, _FakeClientWithAuth] = {}

    def _factory(options):
        client = _FakeClientWithAuth(options, emit_auth=emit_auth)
        holder["client"] = client
        return client

    monkeypatch.setattr(lc, "WSClient", _factory)
    return holder


@pytest.mark.asyncio
async def test_start_marks_ready_when_authenticated(monkeypatch):
    holder = _auth_patch(monkeypatch, emit_auth=True)

    async def _handler(msg: InboundMessage, sink: ReplySink) -> None:
        return

    driver = lc.LongConnectionDriver("BOTID", "SECRET", _handler)
    await driver.start()

    assert holder["client"].connected is True
    assert driver.is_ready is True


@pytest.mark.asyncio
async def test_auth_wait_timeout_degrades_without_disconnect(monkeypatch):
    """超时只降级：不计就绪、**不断开**（交 SDK 自愈），但连接已建立仍须登记。"""
    holder = _auth_patch(monkeypatch, emit_auth=False)
    monkeypatch.setattr(lc, "_AUTH_TIMEOUT_SECONDS", 0.01)

    async def _handler(msg: InboundMessage, sink: ReplySink) -> None:
        return

    driver = lc.LongConnectionDriver("BOTID", "SECRET", _handler)
    await driver.start()

    assert driver.is_ready is False
    assert holder["client"].connected is True  # 未断开
    assert holder["client"].disconnect_calls == 0


@pytest.mark.asyncio
async def test_late_authenticated_still_marks_ready(monkeypatch):
    """超时后认证才到达：仍应转为就绪（状态要如实）。"""
    holder = _auth_patch(monkeypatch, emit_auth=False)
    monkeypatch.setattr(lc, "_AUTH_TIMEOUT_SECONDS", 0.01)

    async def _handler(msg: InboundMessage, sink: ReplySink) -> None:
        return

    driver = lc.LongConnectionDriver("BOTID", "SECRET", _handler)
    await driver.start()
    assert driver.is_ready is False

    holder["client"].emit("authenticated")
    assert driver.is_ready is True


@pytest.mark.asyncio
async def test_displaced_driver_is_not_ready(monkeypatch):
    holder = _auth_patch(monkeypatch, emit_auth=True)

    async def _handler(msg: InboundMessage, sink: ReplySink) -> None:
        return

    driver = lc.LongConnectionDriver("BOTID", "SECRET", _handler)
    await driver.start()
    assert driver.is_ready is True

    await holder["client"].handlers["event.disconnected_event"](
        {
            "cmd": "aibot_event_callback",
            "body": {"msgid": "D1", "msgtype": "event",
                     "event": {"eventtype": "disconnected_event"}},
        }
    )
    assert driver.is_ready is False


@pytest.mark.asyncio
async def test_stop_resets_ready(monkeypatch):
    holder = _auth_patch(monkeypatch, emit_auth=True)

    async def _handler(msg: InboundMessage, sink: ReplySink) -> None:
        return

    driver = lc.LongConnectionDriver("BOTID", "SECRET", _handler)
    await driver.start()
    await driver.stop()

    assert driver.is_ready is False
```

- [ ] **Step 2: 跑测试确认失败**

Run: `POSTGRES_HOST=localhost pytest tests/channels/test_long_connection_driver.py -q`
Expected: FAIL —— `AttributeError: 'LongConnectionDriver' object has no attribute 'is_ready'`。

- [ ] **Step 3: 实现（`src/channels/wecom/long_connection.py`）**

模块级常量（放在 `_DISPLACED_EVENT` 附近）：

```python
# 认证等待上界（秒）：SDK 的 connect() 不等认证（实测差 ~0.17s），故由驱动等
# `authenticated` 事件；超时只降级、不断开（交 SDK 自愈），见 design D11。
_AUTH_TIMEOUT_SECONDS: float = 5.0
```

`__init__` 追加状态字段：

```python
        self._ready: bool = False
```

新增属性（紧邻既有 `is_displaced`）：

```python
    @property
    def is_ready(self) -> bool:
        """该台是否已认证就绪。

        未认证（等待超时）／凭证类失败／被更新的连接顶替后均为 False。
        """
        if self._displaced:
            return False
        return self._ready
```

`start()` 改为"先注册认证处理器 → 连接 → 等认证"：

```python
    async def start(self) -> None:
        """建立长连接、等认证（上界 `_AUTH_TIMEOUT_SECONDS`）并注册事件处理器。"""
        # 已启动则直接返回：重复构造会覆盖 self._client，旧连接的 WebSocket
        # 与收帧循环不会 disconnect，仍会向同一 handler 派帧（重复处理 + 资源泄漏）。
        if self._client is not None:
            return
        client = WSClient(WSClientOptions(bot_id=self._bot_id, secret=self._secret))
        auth_result: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        client.on("authenticated", lambda: self._on_authenticated(auth_result))
        client.on("error", lambda error: self._on_error(error, auth_result, client))
        handler = self._make_handler(client)
        for event in _MESSAGE_EVENTS + _EVENT_EVENTS:
            client.on(event, handler)
        client.on(_DISPLACED_EVENT, self._make_displaced_handler(client))
        await client.connect()
        self._client = client
        await self._await_auth(auth_result)
```

新增三个方法：

```python
    def _on_authenticated(self, auth_result: asyncio.Future[bool]) -> None:
        """SDK 认证成功：置就绪并唤醒等待（迟到的认证同样置就绪——状态要如实）。"""
        self._ready = True
        logger.info("[wecom] bot authenticated bot_id={}", encode_value(self._bot_id))
        if not auth_result.done():
            auth_result.set_result(True)

    def _on_error(
        self, error: BaseException, auth_result: asyncio.Future[bool], client: WSClient
    ) -> None:
        """SDK 连接期错误：凭证类失败交 Task 2 处置；其余只记日志。

        本方法在 Task 2 扩展为"识别 Authentication failed → 断开并置致命"。
        """
        logger.warning(
            "[wecom] connect error bot_id={} err={}", encode_value(self._bot_id), error
        )

    async def _await_auth(self, auth_result: asyncio.Future[bool]) -> None:
        """等认证结果，上界 `_AUTH_TIMEOUT_SECONDS`；超时只降级（不计就绪、不断开）。"""
        try:
            await asyncio.wait_for(asyncio.shield(auth_result), _AUTH_TIMEOUT_SECONDS)
        except TimeoutError:
            logger.warning(
                "[wecom] auth wait timeout bot_id={} timeout={}s",
                encode_value(self._bot_id),
                _AUTH_TIMEOUT_SECONDS,
            )
```

`_make_displaced_handler` 里在置 `_displaced` 后补一行置非就绪；`stop()` 里补 `self._ready = False`。

（`asyncio` 已在文件顶部 import；`logger` / `encode_value` 亦然——若缺则补。）

- [ ] **Step 4: 跑测试确认通过**

Run: `POSTGRES_HOST=localhost pytest tests/channels/test_long_connection_driver.py -q`
Expected: 全 passed（既有用例 + 新增 5 条）。

- [ ] **Step 5: 提交**

```bash
git add src/channels/wecom/long_connection.py tests/channels/test_long_connection_driver.py
git commit -m "feat(channels): 驱动以 authenticated 事件判定就绪（等待上界 5s，超时只降级不断开）"
```

---

### Task 2: 驱动「凭证类失败判据」

**Files:**
- Modify: `src/channels/wecom/long_connection.py`
- Modify: `tests/channels/test_long_connection_driver.py`

**Interfaces:**
- Consumes: Task 1 的 `_on_error(error, auth_result, client)`、`_ready`、`_fatal`（本任务新增）。
- Produces: `LongConnectionDriver._fatal: bool`；凭证类失败时该驱动 `is_ready is False` 且 `client.disconnect()` 已被调用。

- [ ] **Step 1: 写失败测试（追加）**

```python
@pytest.mark.asyncio
async def test_credential_failure_disconnects_and_never_ready(monkeypatch):
    """凭证类失败（实测 errcode=853000）：主动断开、永不就绪；SDK 不抛不重连。"""
    holder = _auth_patch(monkeypatch, emit_auth=False)
    monkeypatch.setattr(lc, "_AUTH_TIMEOUT_SECONDS", 0.01)

    async def _handler(msg: InboundMessage, sink: ReplySink) -> None:
        return

    driver = lc.LongConnectionDriver("BOTID", "SECRET", _handler)
    await driver.start()
    client = holder["client"]

    client.emit(
        "error",
        Exception(
            "Authentication failed: invalid bot_id or secret "
            "(code: 853000)"
        ),
    )

    assert client.disconnect_calls == 1
    assert driver.is_ready is False
    assert driver._fatal is True

    # 迟到的 authenticated 不得把它翻回就绪
    client.emit("authenticated")
    assert driver.is_ready is False


@pytest.mark.asyncio
async def test_non_credential_error_does_not_disconnect(monkeypatch):
    """普通连接/接收错误不构成凭证类：不得断开、不得置致命，仍可认证就绪。"""
    holder = _auth_patch(monkeypatch, emit_auth=False)
    monkeypatch.setattr(lc, "_AUTH_TIMEOUT_SECONDS", 0.05)

    async def _handler(msg: InboundMessage, sink: ReplySink) -> None:
        return

    driver = lc.LongConnectionDriver("BOTID", "SECRET", _handler)
    await driver.start()
    client = holder["client"]

    client.emit("error", Exception("websocket keepalive timeout"))

    assert client.disconnect_calls == 0
    assert driver._fatal is False

    client.emit("authenticated")
    assert driver.is_ready is True
```

- [ ] **Step 2: 跑测试确认失败**

Run: `POSTGRES_HOST=localhost pytest tests/channels/test_long_connection_driver.py -q`
Expected: FAIL —— `disconnect_calls == 1` 不成立（当前 `_on_error` 只记日志）；`driver._fatal` 不存在。

- [ ] **Step 3: 实现（改 `_on_error` + `_on_authenticated` + `__init__` + `is_ready`）**

模块级常量：

```python
# 凭证类失败的判据：SDK 对 SUBSCRIBE 响应 errcode≠0 抛出的错误以本串开头
# （实测错误凭证为 errcode=853000 / invalid bot_id or secret）。**普通 on_error 不构成凭证类**。
_AUTH_FAILED_PREFIX: str = "Authentication failed"
```

`__init__` 追加：

```python
        self._fatal: bool = False
```

`is_ready` 增加致命判断：

```python
        if self._displaced or self._fatal:
            return False
        return self._ready
```

`_on_authenticated` 增加致命守卫（第一行）：

```python
        if self._fatal:
            return
```

`_on_error` 改为：

```python
    def _on_error(
        self, error: BaseException, auth_result: asyncio.Future[bool], client: WSClient
    ) -> None:
        """SDK 连接期错误：凭证类失败 → 主动断开并置致命；其余只记 warning。

        Args:
            error: SDK 抛出的异常（凭证类形如 `Authentication failed: … (code: 853000)`）
            auth_result: 认证等待 future（凭证类失败时以 False 唤醒）
            client: 本驱动当前使用的客户端（用于主动断开）
        """
        message = str(error)
        if not message.startswith(_AUTH_FAILED_PREFIX):
            logger.warning(
                "[wecom] connect error bot_id={} err={}",
                encode_value(self._bot_id),
                message,
            )
            return
        self._fatal = True
        logger.warning(
            "[wecom] auth failed fatal bot_id={} err={}",
            encode_value(self._bot_id),
            message,
        )
        client.disconnect()
        if self._client is client:
            self._client = None
        if not auth_result.done():
            auth_result.set_result(False)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `POSTGRES_HOST=localhost pytest tests/channels/test_long_connection_driver.py -q`
Expected: 全 passed（既有 + Task 1 的 5 条 + 本任务的 2 条）。

- [ ] **Step 5: 提交**

```bash
git add src/channels/wecom/long_connection.py tests/channels/test_long_connection_driver.py
git commit -m "feat(channels): 凭证类认证失败主动断开并置致命（判据仅 Authentication failed，普通 on_error 不受影响）"
```

---

### Task 3: 服务「并行启动 + 注册表语义 + 锚点口径 + `is_ready` 查询」

**Files:**
- Modify: `src/services/wecom_service.py`
- Modify: `tests/services/test_wecom_service.py`

**Interfaces:**
- Consumes: Task 1/2 的 `LongConnectionDriver.is_ready`。
- Produces:
  - `wecom_service.is_ready(bot_key: str) -> bool`（长连接按驱动就绪；callback 模式恒 True）
  - 长连接分支：**并行启动**、`_drivers` 收**所有已启动**驱动、锚点 `n=` = 认证就绪台数

- [ ] **Step 1: 写失败测试（`_StubDriver` 加 `is_ready`，并追加用例）**

先给既有 `_StubDriver` 加构造参数与属性（就地改，不新增类）：

```python
class _StubDriver:
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
        if self._fail:
            raise RuntimeError("connect failed")
        self.started = True

    async def stop(self) -> None:
        self.stopped = True
```

`_install_stub` 增加 `not_ready_ids` 参数：

```python
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
```

追加用例：

```python
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
            tracker["in_flight"] += 1
            if tracker["in_flight"] > tracker["max_in_flight"]:
                tracker["max_in_flight"] = tracker["in_flight"]
            await asyncio.sleep(0)  # 让出控制权，使并发真正重叠
            self.started = True
            tracker["in_flight"] -= 1

    def _factory(bot_id, secret, handler):
        return _TrackingStub(bot_id, secret, handler)

    monkeypatch.setattr(wecom_service, "LongConnectionDriver", _factory)

    await wecom_service.start()

    assert tracker["max_in_flight"] == 3  # 串行启动时恒为 1
```

文件顶部 import 需补 `import asyncio`（若缺；并发用例已改为不依赖墙钟，**不要**引入 `time`）。

- [ ] **Step 2: 跑测试确认失败**

Run: `POSTGRES_HOST=localhost pytest tests/services/test_wecom_service.py -q`
Expected: FAIL —— `wecom_service` 无 `is_ready`；锚点断言不成立（当前 `n=` 计的是"start 未抛错"的台数）；并行用例超时红。

- [ ] **Step 3: 实现（`src/services/wecom_service.py`）**

长连接分支改为"先建全部驱动 → 并行 start → 逐台按结果登记 → 锚点按就绪台数计"：

```python
    _bot_key_by_aibotid = {bot.bot_id: bot.key for bot in bots}
    _drivers.clear()
    bridge_handler = _build_bridge_handler()
    built: list[tuple[str, LongConnectionDriver]] = []
    for bot in bots:
        driver = LongConnectionDriver(bot.bot_id, bot.secret, bridge_handler)
        built.append((bot.key, driver))
    results = await asyncio.gather(
        *(driver.start() for _key, driver in built), return_exceptions=True
    )
    for (bot_key, driver), result in zip(built, results, strict=True):
        if isinstance(result, BaseException):
            logger.warning(
                "[wecom] bot connect failed bot_key={} err={}",
                encode_value(bot_key),
                result,
            )
            continue
        # 已建立连接的驱动一律登记（含未认证就绪者），否则 stop() 无法关闭它
        _drivers[bot_key] = driver
    ready_count = 0
    for _bot_key, driver in built:
        if driver.is_ready:
            ready_count += 1
    logger.info(
        "[wecom] bots connected n={} total={}", ready_count, len(bots)
    )
```

新增查询函数（放在 `get_driver` 附近）：

```python
def is_ready(bot_key: str) -> bool:
    """该台是否可用。

    长连接按驱动就绪判定（未认证超时／凭证类失败／被顶后为 False）；
    callback 模式无握手，装配成功即视为可用。

    Args:
        bot_key: 机器人别名

    Returns:
        True 表示该台可用；未注册或未就绪为 False
    """
    driver = _drivers.get(bot_key)
    if driver is None:
        return False
    if isinstance(driver, LongConnectionDriver):
        return driver.is_ready
    return True
```

（`asyncio` 若未 import 则补。）

- [ ] **Step 4: 跑全量确认通过**

Run: `POSTGRES_HOST=localhost pytest tests/ -q`
Expected: 与基线一致（唯一允许失败 = `tests/parsers/` 环境性缺夹具）；`tests/services/` 与 `tests/channels/` 全绿。

- [ ] **Step 5: 提交**

```bash
git add src/services/wecom_service.py tests/services/test_wecom_service.py
git commit -m "feat(services): 长连接并行启动、注册表收全部已启动者、锚点只计认证就绪台数（+is_ready 查询）"
```

---

### Task 4: 文档登记（一事一档）

**Files:**
- Modify: `docs/agents/logging-rules.md`
- Modify: `docs/agents/code-map.md`
- Modify: `docs/agents/defensive-patterns.md`

**Interfaces:**
- Consumes: Task 1–3 的行为变更（就绪语义、锚点口径、并行启动）。
- Produces: 三处文档与代码一致；无代码改动。

- [ ] **Step 1: 改 `logging-rules.md` 的锚点说明**

在 `[wecom]` 前缀行的归属里补一句口径（保持"开放登记制"格式，不新增前缀）：

```
| `[wecom]` | 企业微信智能机器人通道（services/wecom_service + channels/wecom/{callback,long_connection,presenter,handler,session,bounded_map}）；启动锚点 `bots connected n=<认证就绪台数> total=<配置台数>` |
```

- [ ] **Step 2: 改 `code-map.md` 的通道层落点**

在企微落点处补两行：

```
> - `src/channels/wecom/long_connection.py`：长连接驱动；**就绪 = 收到 SDK `authenticated` 事件**（等待上界 5s，超时只降级不断开）；凭证类失败与"被顶"均置非就绪
> - `src/services/wecom_service.py`：驱动注册表（收全部已启动者，供 stop 关闭）+ `is_ready(bot_key)` 查询；锚点 `n=` 只计认证就绪台数
```

- [ ] **Step 3: 改 `defensive-patterns.md`（新增一条防复发规则，按该文件既有格式：`## 分区` → `### 标题` → `**现象**` / `**规则**`）**

在文件末尾新增分区（该文件现无"接入通道"分区）：

```
## 接入通道

### SDK `connect()` 不等认证——不得以返回值判定就绪

**现象**：`WSClient.connect()` 返回只代表 WebSocket 已建连并发出认证帧。实测它与 SDK 的 `authenticated` 事件相差约 0.17s，且**认证失败时它照样正常返回**（失败只以 `error` 事件呈现，`errcode=853000`）。

**规则**：就绪一律以 SDK 的 `authenticated` 事件为准（`LongConnectionDriver.is_ready`，见 `src/channels/wecom/long_connection.py`），等待加上界 5s；超时只降级、**不主动断开**（交 SDK 自愈）；凭证类失败（错误信息以 `Authentication failed` 开头）才主动 `disconnect()` 并置致命。反面代价：以返回值判定会让"逐台 try/except"变成死代码、启动锚点 `n=total` **恒真**——排障时无法区分"三台都在线"与"三台都认证失败"。
```

- [ ] **Step 4: 跑文档闸门并提交**

Run: `POSTGRES_HOST=localhost pytest tests/ -q`（确认无测试因文档改动而红）+ 提交时 pre-commit 会跑 `doc anti-rot` / `check-adr`。
Then:
```bash
git add docs/agents/logging-rules.md docs/agents/code-map.md docs/agents/defensive-patterns.md
git commit -m "docs(agents): 登记驱动就绪语义与锚点口径，新增「connect() 不代表认证就绪」防复发规则"
```

---

## 本阶段覆盖对照（自我检查用，不入提交）

| 规范条目（tasks 5.1 / design D11） | 落点 |
|---|---|
| 等 `authenticated` 事件才算就绪 + 上界 ≤5s | Task 1 |
| 等待超时→只降级（不计就绪、不断开、不停重连） | Task 1 |
| 凭证类失败判据（`Authentication failed` / `errcode=853000`）→ 主动断开 + 停重连 | Task 2 |
| 普通 `on_error` 不构成凭证类 | Task 2 |
| 被顶 ⇒ 不就绪（消费 `is_displaced`） | Task 1（`is_ready`） |
| `_drivers` 收所有已启动者（防关机泄漏）；锚点只计就绪台数 | Task 3 |
| 逐台并行启动、不阻塞应用启动 | Task 3 |
| 文档一致（锚点口径 / 就绪语义 / 防复发规则） | Task 4 |

## 不在 4a 范围（留给 4b 与阶段 5）

- **组 6 + 1.5（4b）**：澄清**呈现**（把 `ask_user` 的问题渲染成企微文本）、**仅触发者**回填（用 3.7 的 `_triggers` 校验）、`clarify_answer` 从 `src/api/clarify.py:59` **下沉到 services** 并双写、超时/无效回复文案；（可选）卡片二期用 `task_id` + `event_key`。
- **阶段 5**：灰度 E2E、鲁棒验收、群聊验收、runbook。
- 健康检查端点暴露 `is_ready`（本阶段只提供查询函数，未接 HTTP）。

## 4b 的切分草案（待你确认后再写 plan）

侦察结论（`src/api/clarify.py:59` / `ask_tools.py:90` / `request_context.py:106` / `chat.html:1903`）提示 4b 至少含：
1. **下沉** `clarify_answer` 的编排到 `services`（现属 api 层、直接操作 `pending_asks` + `AppService`），api 变薄路由；
2. **通道侧呈现**：`_tap` 拦截 `SSEAskUserEvent` → 渲染成企微文本（含选项与有效期）→ 发终态帧；
3. **仅触发者回填**：入站文本 vs `_triggers.get(session_id)` 校验，非触发者回复给提示、不消耗挂起；
4. **答案注入**：把文本答案转成 `answers` 结构调 servicse 层 `resolve_clarify_answer`（含 multi_select / options 的解析与"无效答案"文案）；
5. （可选）卡片二期：`task_id` 关联 + `event_key` 作选项，点击回调走同一 `resolve` 路径。
