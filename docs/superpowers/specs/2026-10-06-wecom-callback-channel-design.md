# 企微智能机器人回调通道 — 设计提案

生成日期：2026-10-06
平台：企业微信「智能机器人」（AI Bot），**URL 回调**接入方式
范围：最小接入版（打通链路，不含业务）；长连接留待后续
配套调研：本轮 firecrawl 调研（官方协议文档 + 同类项目）结论见对话记录

## 1. 背景与问题

### 1.1 目标场景

把本项目的检索问答能力，通过企业微信「智能机器人」在单聊 / 群聊（@机器人）中赋能。首批接入方式选 **URL 回调**；长连接作为后续可切换的实现，本轮只抽象接口、不实现。

### 1.2 本轮只验证"接入链路"

不做业务。要证明的是：**URL 有效性验证通过 + 能收到 @ 消息 → 解密 → 返回一条回复**。回复内容可以是写死的。

### 1.3 关键外部约束（已核实，来自官方文档）

| 约束 | 内容 | 影响 |
|---|---|---|
| 网关 | Nginx 只把 `/api/`、`/docs`、`/openapi.json` 转发到 app | 回调路由**必须挂 `/api/` 下** |
| URL 验证 | 保存时企微发 `GET`，带 `msg_signature/timestamp/nonce/echostr` | 需验签 + 解密，**1 秒内**返回明文 |
| 接收回调 | `POST` body = `{"encrypt": "..."}` | 需验签 + AES 解密为 JSON 明文 |
| 被动回复 | 明文 JSON → AES 加密 → `{"encrypt","msgsignature","timestamp","nonce"}` | 需实现加密回包 |
| ReceiveId | 企业内部智能机器人场景为 **空字符串 `""`** | 加解密传空 |
| 加密参数 | AES-256-CBC、**PKCS#7 块大小 32**、IV = AESKey 前 16 字节 | 易错点 |
| 消息体 | 长连接与回调**共用同一 body 结构**（`msgid/aibotid/chatid/chattype/from.userid/msgtype/text.content`） | 抽象可共用入站模型 |
| 模式互斥 | 同一机器人同一时间只能一种接入方式（回调 / 长连接），切换即另一种失效 | 长连接后续切换需重配凭证 |
| 回复类型 | 普通消息回复只支持 `stream` / `template_card`；`text` 仅限 `enter_chat` 事件 | 写死回复用 `stream` |

### 1.4 当前代码库现状

- 无任何企微接入代码；无 AES 加解密依赖（`pyproject.toml` 仅 `bcrypt`）。
- `api/` 不得直接 import `infra/`、`config/`，须经 `services/`。
- `auth_middleware` 对非 `kbs/chat/sessions/auth` 路径直接放行；`response_processor` 不改响应体。→ 回调路径天然免鉴权且可返回原始文本。

## 2. 目标与非目标

### 目标

1. 新增 `src/channels/` 通道包，定义「通道驱动」抽象（`ChannelDriver` / `InboundMessage` / `ReplySink` / `MessageHandler`），使回调与长连接成为同一接口的两个实现。
2. 实现回调驱动：验签、AES 解密/加密、解析、加密回包，并挂载 `/api/wecom/callback`。
3. 最小业务：收到消息 → 打日志（`msgtype` + 正文）→ 返回写死的 `stream` 回复（`finish=true`）。
4. 用 `WECOM_BOT_ENABLED` 控制路由是否挂载。
5. 单测覆盖加解密往返、验签、路由 GET/POST。

### 非目标（YAGNI 明确排除）

- ❌ 长连接驱动实现（只留接口，不建占位文件）
- ❌ 流式刷新（企微"拉"）的缓冲与 Redis 存储
- ❌ 媒体文件 / 图片解密下载
- ❌ 模板卡片
- ❌ userid 密文转明文（自建应用对接）
- ❌ 接入 RAG / agent 业务逻辑
- ❌ `msgid` 去重的持久化（本轮仅日志）

## 3. 设计

### 3.1 目录结构

```
src/channels/
  __init__.py
  base.py            # 通用抽象：InboundMessage / ReplySink / MessageHandler / ChannelDriver
  wecom/
    __init__.py
    crypto.py        # WeComCrypto：验签 / 解密 / 加密（纯函数）
    callback.py      # CallbackDriver：实现 ChannelDriver + 回调专属 HTTP 入口
src/services/wecom_service.py   # 装配 driver + 注入 handler
src/api/wecom.py                # GET/POST /api/wecom/callback
```

分层合规：`api/wecom.py → services/wecom_service.py → channels/wecom/*`；`api/` 不 import `channels/` 的加解密细节。

### 3.2 抽象接口（`channels/base.py`）

```python
@dataclass(frozen=True)
class InboundMessage:
    """统一入站事件——回调与长连接共用同一 body 结构，业务只认它。"""
    msgid: str            # 本次回调唯一标志，用于排重
    aibotid: str          # 智能机器人 id
    chatid: str | None    # 群聊会话 id；单聊为 None
    chattype: str         # "single" | "group"
    from_userid: str      # 触发者 userid（非超管场景为密文）
    msgtype: str          # text/image/mixed/voice/file/video
    text: str | None      # 文本正文（含 @）；非文本消息为 None
    raw: dict             # 原始明文，兜底


class ReplySink(Protocol):
    """一条入站消息对应的回复出口；由驱动决定落到 HTTP 响应还是 WS 帧。"""
    async def reply_text(self, content: str) -> None: ...
    async def reply_stream(self, content: str, finish: bool) -> None: ...


class MessageHandler(Protocol):
    """业务处理：吃入站事件，用 sink 回复。本轮实现=打日志+写死回复。"""
    async def __call__(self, msg: InboundMessage, sink: ReplySink) -> None: ...


class ChannelDriver(Protocol):
    """接入通道驱动：屏蔽长连接 / 回调的传输差异。

    handler 在构造时注入（见 §3.4）；start/stop 只管生命周期——
    回调为 no-op，长连接在 start 里建连、stop 里断开。
    """
    name: str
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
```

**为什么这样切**：两种模式入站 body 一致、差异全在"回复怎么出去"——
- 回调：首包必须**同步**写进 HTTP 响应；后续流式更新是企微来"拉"（本轮不实现）。
- 长连接：主动推 WS 帧。

`ReplySink` 把这层差异封在 driver 内；业务代码只依赖 `InboundMessage` / `ReplySink`，切换模式不改业务。

### 3.3 加解密（`channels/wecom/crypto.py`）

```python
class WeComCrypto:
    def __init__(self, token: str, encoding_aes_key: str, receive_id: str = ""): ...
    def signature(self, timestamp: str, nonce: str, encrypt: str) -> str: ...
    def decrypt(self, encrypt_b64: str) -> str: ...   # 返回明文 JSON str
    def encrypt(self, msg: str) -> str: ...           # 返回 base64 密文
```

规格（官方）：
- `key = base64.b64decode(encoding_aes_key + "=")`（43 位 key → 32 字节）；`iv = key[:16]`。
- AES-256-CBC；**PKCS#7 填充块大小 = 32**（非 AES 的 16）。
- 明文结构：`random(16) + struct.pack("!I", len(msg)) + msg + receive_id`；`receive_id=""`。

### 3.4 回调驱动（`channels/wecom/callback.py`）

```python
class CallbackDriver:
    name = "wecom_callback"
    def __init__(self, crypto: WeComCrypto, handler: MessageHandler): ...  # handler 构造注入
    async def start(self) -> None: ...    # no-op（回调无需建连），为长连接对称而留
    async def stop(self) -> None: ...     # no-op
    def verify(self, query: Mapping[str, str]) -> str:           # GET：验签+解密 echostr
    async def handle_message(self, body: dict, query: Mapping[str, str]) -> dict:  # POST
```

- `handle_message` 流程：验签 → `crypto.decrypt(body["encrypt"])` → `json.loads` → 解析为 `InboundMessage` → 调 `self._handler(msg, sink)` → `_CallbackSink` 收集待回包 → 加密封装为响应 dict。
- `_CallbackSink`：把回复收敛为一条 `OutboundReply`（frozen dataclass：`kind: "stream" | "text"`、`content: str`、`finish: bool`）；本轮只支持"首次同步回包"，不支持后续推流。
- 排重：`msgid` 仅写日志（不落存储）。

### 3.5 编排（`services/wecom_service.py`）

- 默认 handler：打日志（`msgtype` + `text`）→ `sink.reply_stream("已收到，稍后接入检索…", finish=True)`。
- 构造单例 `CallbackDriver(WeComCrypto(...), handler)`；对外暴露 `get_driver()`、`start()`、`stop()`。
- 后续在此处替换 handler 为"调 agent 跑 RAG"。

### 3.6 配置（`config/settings.py`、`config/const.py`）

`settings.py`（沿用 `os.getenv` 模块级常量风格）：

| 变量 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `WECOM_BOT_ENABLED` | bool | `false` | 是否挂载回调路由 |
| `WECOM_BOT_TOKEN` | str | `""` | 回调 Token（3~32 位） |
| `WECOM_BOT_ENCODING_AES_KEY` | str | `""` | 回调 EncodingAESKey（43 位） |
| `WECOM_BOT_RECEIVE_ID` | str | `""` | 智能机器人场景为空串 |

`const.py`：`WECOM_CALLBACK_PATH = "/wecom/callback"`（**router 相对路径**，与项目惯例一致；对外完整地址 = `/api` + 该值 = `/api/wecom/callback`）。

### 3.7 路由与装配（`api/wecom.py`、`main.py`）

```python
# 路由用相对路径；最终对外地址由 include_router(prefix="/api") 拼成 /api/wecom/callback
@router.get(WECOM_CALLBACK_PATH)   # 验签失败→403；成功→PlainTextResponse(明文)
@router.post(WECOM_CALLBACK_PATH)  # 验签失败→403；解密失败→400；成功→加密 JSON
```

- 在 `main.py` 中 **仅当 `settings.WECOM_BOT_ENABLED` 为真**时 `include_router(..., prefix="/api")`。
- `main.py` 的 lifespan：启用时 `await wecom_service.start()`，关闭时 `await wecom_service.stop()`（回调下为 no-op，但这是长连接 `start()` 的挂载点）。
- 中间件兼容性（已核实）：`auth_middleware` 对该路径放行；`response_processor` 不改响应体；两个路由返回原始文本 / dict。

### 3.8 错误处理

| 场景 | 行为 |
|---|---|
| GET 验签失败 | `403`（明文 body，不套统一信封） |
| POST 验签失败 | `403` |
| 解密失败 / body 非 JSON | `400` |
| handler 抛异常 | 记 `error` 日志，返回空包（`200`），避免企微重试风暴 |
| 未配置凭证 | 路由不挂载（`WECOM_BOT_ENABLED=false`），其它部署零影响 |

## 4. 影响面

| 文件 | 改动 |
|---|---|
| `src/channels/__init__.py` | 新建 |
| `src/channels/base.py` | 新建：`InboundMessage` / `ReplySink` / `MessageHandler` / `ChannelDriver` |
| `src/channels/wecom/__init__.py` | 新建 |
| `src/channels/wecom/crypto.py` | 新建：`WeComCrypto` |
| `src/channels/wecom/callback.py` | 新建：`CallbackDriver` |
| `src/services/wecom_service.py` | 新建：装配 driver + 默认 handler |
| `src/api/wecom.py` | 新建：GET/POST 路由 |
| `src/main.py` | 条件 `include_router` + lifespan 中 `start()/stop()` |
| `src/config/settings.py` | 新增 4 个环境变量 |
| `src/config/const.py` | 新增 `WECOM_CALLBACK_PATH` |
| `pyproject.toml` | 新增依赖 `pycryptodome`（项目当前无 AES 库） |
| `tests/channels/` | 新建：crypto 与 driver 单测 |
| `tests/api/test_wecom.py` | 新建：路由 TestClient 测试 |
| `docs/agents/code-map.md` | 登记 `src/channels/`（新顶层包） |

## 5. 验证方案

1. **单测（crypto）**：加解密往返一致；`signature` 与手工 SHA1 一致；PKCS#7 块 = 32；`receive_id=""`。
2. **单测（driver）**：构造加密 GET 查询 → `verify` 返回明文；构造加密 POST → 断言返回 `{"encrypt",...}` 且可解密还原。
3. **单测（路由）**：TestClient GET/POST；`WECOM_BOT_ENABLED=false` 时路由 404。
4. **本地自测**：`python -m src.channels.wecom.crypto` 类的往返自测（不起服务）。
5. **手工冒烟（需企微后台）**：回调 URL 填 `http://<公网IP或域名>/api/wecom/callback` + Token/AESKey，保存应验证通过；再 @机器人 应收到写死回复，后端日志可见 `msgtype` 与正文。
6. **质量门禁**：`POSTGRES_HOST=localhost pytest tests/ -v`、`ruff check .`、`pyright src/` 不新增 error。

## 6. 风险与取舍

| 风险 | 应对 |
|---|---|
| **PKCS#7 块大小误用 16** → 解密失败 | 单测显式断言块 = 32；常量 `PKCS7_BLOCK_SIZE = 32` |
| **回调 URL 必须公网可达**（企微要求企业主体域名或 IP） | 本轮为开发验证，先用 IP；生产需备案域名（属外部事项，不阻塞开发） |
| **模式切换互斥**：将来切长连接会使回调配置失效 | 已用 `ChannelDriver` 抽象隔离，切换=新增 driver + 换凭证；本轮不触发 |
| **回调流式是"拉"**，`ReplySink` 无法主动推后续更新 | 本轮只做首次 `finish=true`；刷新缓冲留待业务阶段，接口位置已留 |
| **新增运行时依赖** `pycryptodome` | 官方加解密库即用 PyCryptodome；已在 `pyproject.toml` 显式声明 |
| **企微重试导致重复处理** | `msgid` 记日志；幂等/去重留待业务阶段 |

## 7. 未覆盖 / 后续（需另行立项）

1. **长连接驱动**：实现同一 `ChannelDriver`，走 `wss://openws.work.weixin.qq.com`，无加解密、主动推流。
2. **流式刷新（回调"拉"）**：`stream.id` → Redis 缓冲累计文本，供后续刷新事件返回。
3. **业务接线**：把默认 handler 换成"调 agent 跑 RAG"，复用现有 `agent_service`。
4. **媒体入站**：图片/文件/语音下载与解密（`aeskey` 每 URL 唯一）。
5. **认证 / 备案域名**：生产回调地址的合规准备。
6. **多机器人 / 多凭证**：当前为单机器人的单套凭证。
