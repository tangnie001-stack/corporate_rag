# 企微智能机器人长连接驱动 — 设计提案

生成日期：2026-10-06
平台：企业微信「智能机器人」（AI Bot），**长连接（WebSocket）**接入方式
范围：新增长连接驱动（复用官方 SDK），与既有回调驱动**并存、配置切换**；仍不接业务
前置：`2026-10-06-wecom-callback-channel-design.md`（通道抽象与回调驱动已实现）

## 1. 背景与目标

回调驱动已实现并终审通过。回调模式的**外部前置成本高**（需公网可达；已认证企业还要求备案主体一致的域名 + 企业可信域名/IP），且官方对回调只提供"加解密库"，HTTP server / 消息分发 / 流式拉取全要自建。

长连接相反：**官方提供完整客户端 SDK**（`wecom-aibot-python-sdk`），且**无需公网域名/IP、无需加解密**。本提案新增长连接驱动，与回调驱动并存，由配置切换——**同一机器人同一时间只能用一种**，故运行时只激活其一。

## 2. 已核实的外部事实（实测，非文档推断）

| 项 | 事实 |
|---|---|
| 包 | PyPI `wecom-aibot-python-sdk` **1.0.2**（2026-03-23，MIT，Python>=3.8）；导入名 `aibot` |
| 依赖 | `websockets>=12`、`aiohttp>=3.9`、`pyee>=11`、`cryptography>=42`、`certifi>=2023`（装后实测拉入 `pyee`） |
| 配置 | `WSClientOptions(bot_id, secret, reconnect_interval, max_reconnect_attempts, heartbeat_interval, request_timeout, ws_url, logger)` |
| 生命周期 | `await connect()`（**协程**）、`disconnect()`（同步）；`run()` 自建事件循环，**FastAPI 内不可用** |
| 收发 | `await reply_stream(frame, stream_id, content, finish=False, ...)`、`await reply_welcome(frame, body)`、`await send_message(chatid, body)` 等**均为协程** |
| `frame` | 类型别名 `Dict[str, Any]`；`frame["body"]` 即入站明文 dict（与回调**同一 body 结构**） |
| 事件名 | `message.text/image/mixed/voice/file`；`event.enter_chat/template_card_event/feedback_event` |
| 单连接约束 | 每机器人同一时间仅一条有效长连接，新连接**踢掉**旧连接（官方） |

## 3. 目标与非目标

### 目标

1. 新增 `LongConnectionDriver`，实现既有 `ChannelDriver` 接口，包住官方 SDK。
2. **复用**既有 `parse_inbound`、`InboundMessage`、`ReplySink`、`MessageHandler`、`_default_handler`、`main.py` lifespan（**不改**）。
3. 引入 `WECOM_BOT_MODE`（`callback` | `long_connection`，默认 `callback`）切换驱动；`WECOM_BOT_ENABLED` 仍是总开关。
4. 回调路由在 `mode != callback` 或未启用时返回 404。
5. 单连接的互斥**只做配置约定 + 文档**（本轮不写代码级锁）。
6. 单测：mock `aibot.WSClient`，覆盖"入站帧→handler"与"回复→SDK 调用"。

### 非目标（YAGNI）

- ❌ 不实现业务（仍写死回复）
- ❌ 不加 Redis 单例租约（仅文档约定；留待需要时另立）
- ❌ 不改 `crypto.py` / `callback.py`（保留，二者互斥不冲突）
- ❌ 不做媒体下载解密、模板卡片、`userid` 转明文、去重
- ❌ 不改 `main.py`（lifespan 已 `start/stop`，驱动无关）

## 4. 设计

### 4.1 目录

```
src/channels/wecom/
  parse.py            （既有，复用）
  crypto.py           （既有，回调专用，不动）
  callback.py         （既有，不动）
  long_connection.py  ★新增：LongConnectionDriver + _WsSink
src/services/wecom_service.py  ★改：按 mode 构造驱动；暴露 get_callback_driver()
src/api/wecom.py               ★改：mode 守卫
src/config/settings.py         ★改：+WECOM_BOT_MODE/ID/SECRET
```

### 4.2 `LongConnectionDriver`（`channels/wecom/long_connection.py`）

```python
class LongConnectionDriver:
    name = "wecom_long_connection"

    def __init__(self, bot_id: str, secret: str, handler: MessageHandler): ...

    async def start(self) -> None:
        # 构造 WSClient(WSClientOptions(bot_id=..., secret=...))
        # 注册 message.text/image/mixed/voice/file 与 event.enter_chat/...
        # await client.connect()（不调 run()）
    async def stop(self) -> None:
        # client.disconnect()

class _WsSink:                       # 实现 ReplySink
    async def reply_stream(self, content: str, finish: bool) -> None:
        # await client.reply_stream(self._frame, self._stream_id, content, finish)
```

- 入站：SDK 处理器收到 `frame: Dict[str, Any]` → `parse_inbound(frame["body"])` → `InboundMessage` → 调 `handler(msg, sink)`。
- `_WsSink` 每条消息生成**一次** `stream_id`（`generate_req_id("stream")`），供该消息的多次 `reply_stream` 复用。
- 事件（`event.*`）：同样解析并交给 handler；本轮 `_default_handler` 对事件不回包。
- 异常：处理器内 try/except 记 `[wecom]` error 日志，不向外抛（不打断 SDK 循环）。

### 4.3 模式切换（`services/wecom_service.py`）

- `start()`：`WECOM_BOT_ENABLED` 为假 → no-op；为真则按 `WECOM_BOT_MODE` 校验凭证并构造对应驱动：
  - `callback`：`WECOM_BOT_TOKEN` 非空 且 `WECOM_BOT_ENCODING_AES_KEY` 长度 == 43，否则 `RuntimeError`（fail-fast，沿用现状）。
  - `long_connection`：`WECOM_BOT_ID` 与 `WECOM_BOT_SECRET` 均非空，否则 `RuntimeError`。
  - 其它取值 → `RuntimeError`（配置错误）。
- `get_callback_driver() -> CallbackDriver`：当前 mode 非 `callback` 或未启动时抛 `RuntimeError`；供回调路由使用。
- `stop()`：对当前驱动调 `stop()`；两种驱动都有该接口。

### 4.4 路由守卫（`api/wecom.py`）

GET/POST 均先判：`WECOM_BOT_ENABLED` 为假 **或** `WECOM_BOT_MODE != "callback"` → `404`；否则走 `wecom_service.get_callback_driver()`。

### 4.5 配置（`config/settings.py`）

| 变量 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `WECOM_BOT_MODE` | str | `"callback"` | `callback` \| `long_connection` |
| `WECOM_BOT_ID` | str | `""` | 长连接：机器人 BotID |
| `WECOM_BOT_SECRET` | str | `""` | 长连接：机器人 Secret |

（回调的 5 个变量与 `WECOM_BOT_ENABLED` 保持不变。）

### 4.6 单连接互斥（仅约定）

`mode=long_connection` 时，**仅在一台实例**开启 `WECOM_BOT_ENABLED=true`，其余实例置 `false`（否则新连接踢旧连接、连接互踢）。写进 `.env.example/.env.template` 注释与 cookbook。**本轮不做代码级锁**（记为 §7 后续项）。

## 5. 影响面

| 文件 | 改动 |
|---|---|
| `src/channels/wecom/long_connection.py` | 新建：`LongConnectionDriver` / `_WsSink` |
| `src/services/wecom_service.py` | 改：mode 选路 + 分模式凭证校验 + `get_callback_driver()` |
| `src/api/wecom.py` | 改：mode 守卫（404）+ 改用 `get_callback_driver()` |
| `src/config/settings.py` | 改：+3 变量 |
| `pyproject.toml` | 改：+`wecom-aibot-python-sdk==1.0.2` |
| `tests/channels/test_long_connection_driver.py` | 新建：mock `aibot.WSClient` |
| `tests/services/test_wecom_service.py` | 改：mode 切换与分模式 fail-fast |
| `tests/api/test_wecom.py` | 改：long_connection 模式下路由 404 |
| `docs/agents/code-map.md`、`.env.example`、`.env.template`、`docs/agents/glossary.md`、`docs/agents/api_contract.md`、`docs/agents/cookbook.md` | 登记长连接驱动、新变量、互斥约定与操作 |

## 6. 验证方案

1. 单测（driver）：mock `aibot.WSClient` —— `start()` 以 `bot_id/secret` 构造并 `connect()`；投递一个 `message.text` 帧 → handler 收到解析后的 `InboundMessage`；`sink.reply_stream(...)` → `client.reply_stream` 被以 `(frame, stream_id, content, finish)` 调用。
2. 单测（service）：`mode=callback` 与 `mode=long_connection` 各自构造正确驱动；分模式 fail-fast；未知 mode 抛错；`get_callback_driver()` 在非 callback 模式抛错。
3. 单测（route）：`mode=long_connection` 时 GET/POST 均 404；`mode=callback` 行为不变。
4. 质量门禁：`POSTGRES_HOST=localhost pytest tests/ -v`、`ruff check .`、`pyright src/` 不新增 error。
5. 手工冒烟（需企微后台，可选）：`mode=long_connection` 且填 BotID/Secret，进程日志出现连接/认证成功，@机器人 收到写死回复。

## 7. 风险与取舍

| 风险 | 应对 |
|---|---|
| **单连接互踢**（多实例） | 仅约定"只在一台开 enabled"（§4.6）；本轮不写锁，记后续项 |
| **新增依赖**（`aibot` + `websockets`/`aiohttp`/`pyee`） | 需重建 app 镜像；云效 PyPI 代理仓 `repo-okxha` 预热 |
| **SDK 维护弱**（1.0.2 / 3 次发布 / Beta） | 只依赖其公开接口（`WSClient`/`WSClientOptions`/`reply_stream`）；驱动层做薄封装，必要时可替换 |
| **`run()` 与 FastAPI 事件循环冲突** | 只用 `await connect()` / `disconnect()`，禁用 `run()` |
| **默认 Logger 输出不受控** | 通过 `WSClientOptions(logger=...)` 注入适配器，或接受其默认（实现时确认） |
| **事件被误回包** | 复用既有 `_default_handler`（事件不回包） |
| **connect() 可能阻塞启动**（端点不可达时 SDK 内部退避重试，最多 10 次/数分钟） | 属 SDK 设计行为；部署时保证该实例可访问企微，并接受启动可能变慢（必要时后续用 max_reconnect_attempts 或超时策略收敛） |

## 8. 未覆盖 / 后续（另行立项）

1. **Redis 单例租约**：多实例部署下自动选主、防互踢（本轮仅文档约定）。
2. **业务接线**：把 `_default_handler` 换成调 agent 跑 RAG。
3. **媒体入站**：`download_file(url, aes_key)` 的下载与解密。
4. **模板卡片 / 欢迎语**：`reply_welcome` / `reply_template_card`。
5. **凭证轮换与多机器人**：当前单机器人单套凭证。
