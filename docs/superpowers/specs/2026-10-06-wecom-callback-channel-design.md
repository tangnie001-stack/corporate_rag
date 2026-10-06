# 企微智能机器人回调通道 — 设计提案

生成日期：2026-10-06
平台：企业微信「智能机器人」（AI Bot），**URL 回调**接入方式
范围：最小接入版（打通链路，不含业务）；长连接留待后续
配套调研：firecrawl 调研（官方协议文档 + 同类项目）+ grilling 三轮（决策已回写本文）

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
| **事件同 URL** | **同一回调 URL 既收消息、也收事件**：事件报文 `msgtype="event"`，细节在 `event.eventtype`（`enter_chat` / `template_card_event` / `feedback_event` 等） | 入站模型**必须能区分消息与事件** |
| 被动回复 | 明文 JSON → AES 加密 → `{"encrypt","msgsignature","timestamp","nonce"}`（回包字段是 `msgsignature`，无下划线） | 需实现加密回包 |
| 回复类型 | 普通消息回复只支持 `stream` / `template_card`；`text` **仅限 `enter_chat` 事件** | 消息用 `stream`；事件本轮回空包 |
| 同步窗口 | 消息回包须走 HTTP 同步响应（第三方实现按 **4 秒**兜底；**官方未明示秒数**） | 接业务（LLM 首 token）时须改走流式刷新 / `response_url` |
| ReceiveId | 企业内部智能机器人场景为 **空字符串 `""`** | 加解密传空 |
| 加密参数 | AES-256-CBC、**PKCS#7 块大小 32**、IV = AESKey 前 16 字节 | 易错点 |
| 消息体 | 长连接与回调**共用同一 body 结构**（`msgid/aibotid/chatid/chattype/from.userid/msgtype/...`） | 抽象可共用入站模型与解析 |
| 模式互斥 | 同一机器人同一时间只能一种接入方式（回调 / 长连接），切换即另一种失效 | 长连接后续切换需重配凭证 |

### 1.4 当前代码库现状

- 无任何企微接入代码；无 AES 加解密依赖（`pyproject.toml` 仅 `bcrypt`）。
- `api/` 不得直接 import `infra/`、`config/`，须经 `services/`。
- `auth_middleware` 对非 `kbs/chat/sessions/auth` 路径直接放行；`response_processor` 不改响应体。→ 回调路径天然免鉴权且可返回原始文本。
- `main.py` lifespan 已有"启动期校验、失败即抛"的先例（prompt 模板校验）。
- app 容器经 compose `env_file` 注入环境变量 → 新增 `WECOM_BOT_*` 放进 `.env` 即可，无需改 compose。

## 2. 目标与非目标

### 目标

1. 新增 `src/channels/` 通道包，定义「通道驱动」抽象（`ChannelDriver` / `InboundMessage` / `ReplySink` / `MessageHandler`），使回调与长连接成为同一接口的两个实现；**入站解析独立成模块**供两种驱动共用。
2. 实现回调驱动：验签、AES 解密/加密、加密回包，并挂载 `/api/wecom/callback`。
3. 最小业务：**消息** → 打日志 → 返回写死的 `stream` 回复（`finish=true`）；**事件** → 打日志 → 返回空包。
4. 用 `WECOM_BOT_ENABLED` 控制路由与驱动的挂载/启动。
5. 单测覆盖加解密往返、验签、事件/消息分流、路由 GET/POST。

### 非目标（YAGNI 明确排除）

- ❌ 长连接驱动实现（只留接口，不建占位文件）
- ❌ 流式刷新（企微"拉"）的缓冲与 Redis 存储
- ❌ 媒体文件 / 图片 / 语音的下载与解密；mixed 消息的正文抽取
- ❌ 模板卡片、欢迎语（`enter_chat` 也只回空包）
- ❌ userid 密文转明文（自建应用对接）
- ❌ 接入 RAG / agent 业务逻辑
- ❌ `msgid` 去重的持久化或进程内去重（本轮重复回调仅日志）
- ❌ `create_time` 等入站字段入模型（无消费者）

## 3. 设计

### 3.1 目录结构

```
src/channels/
  __init__.py
  base.py            # 通用抽象：InboundMessage / ReplySink / MessageHandler / ChannelDriver
  wecom/
    __init__.py
    parse.py         # 入站解析：JSON 明文 dict → InboundMessage（回调/长连接共用）
    crypto.py        # WeComCrypto：验签 / 解密 / 加密（纯函数，仅回调需要）
    callback.py      # CallbackDriver：传输层（验签/解密/加密回包/HTTP 入口）
src/services/wecom_service.py   # 装配 driver + 注入 handler
src/api/wecom.py                # GET/POST /api/wecom/callback
```

分层合规：`api/wecom.py → services/wecom_service.py → channels/wecom/*`；`api/` 不 import `channels/` 的加解密细节。

**为什么解析独立**：回调与长连接的入站 body 结构一致，解析逻辑必须共用，否则长连接实现时要复制。`callback.py` 只负责传输（验签/解密/加密/HTTP）。

### 3.2 抽象接口（`channels/base.py`）

```python
@dataclass(frozen=True)
class InboundMessage:
    """统一入站事件——消息与事件共用；回调与长连接共用同一 body 结构，业务只认它。"""
    msgid: str            # 本次回调唯一标志，用于排重
    aibotid: str          # 智能机器人 id
    chatid: str | None    # 群聊会话 id；单聊为 None
    chattype: str         # "single" | "group"
    from_userid: str      # 触发者 userid（非超管场景为密文）
    msgtype: str          # text/image/mixed/voice/file/video，或 "event"
    text: str | None      # 仅文本消息取 text.content；其它一律 None
    event_type: str | None  # 事件类型（msgtype == "event" 时非空），如 "enter_chat"
    raw: dict             # 原始明文，兜底；**只读约定，不深拷贝**


class ReplySink(Protocol):
    """一条入站消息对应的回复出口；由驱动决定落到 HTTP 响应还是 WS 帧。

    reply_text 仅对 enter_chat 事件合法（本轮不调用，保留以固定接口面）。
    reply_stream 的 stream.id 由驱动内部生成并持有，业务不感知。
    """
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

**事件判据**：不设冗余布尔，`msgtype == "event"` 即事件，此时 `event_type` 非空。

### 3.3 入站解析（`channels/wecom/parse.py`）

`parse_inbound(plain: dict) -> InboundMessage`：从解密后的 JSON 明文构造统一事件。
- 文本消息：`text = plain["text"]["content"]`。
- 非文本（image/mixed/voice/file/video）：`text = None`，仅保留 `msgtype`。
- 事件：`msgtype == "event"`，`event_type = plain["event"]["eventtype"]`。
- `create_time` 等无消费字段不纳入模型。

### 3.4 加解密（`channels/wecom/crypto.py`）

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
- **不校验尾部 receive_id**（智能机器人恒为空串），仅注释说明。

### 3.5 回调驱动（`channels/wecom/callback.py`）

```python
class CallbackDriver:
    name = "wecom_callback"
    def __init__(self, crypto: WeComCrypto, handler: MessageHandler): ...  # handler 构造注入
    async def start(self) -> None: ...    # no-op（回调无需建连），为长连接对称而留
    async def stop(self) -> None: ...     # no-op
    def verify(self, query: Mapping[str, str]) -> str:           # GET：验签+解密 echostr
    async def handle_message(self, body: dict, query: Mapping[str, str]) -> dict:  # POST
```

- `handle_message` 流程：验签 → `crypto.decrypt(body["encrypt"])` → `json.loads` → `parse_inbound` → 调 `self._handler(msg, sink)` → `_CallbackSink` 收集 → 加密封装为响应 dict。
- `_CallbackSink`：把回复收敛为一条 `OutboundReply`（frozen dataclass：`kind: "stream" | "text"`、`content: str`、`finish: bool`），`stream.id` 由 driver 生成。本轮只支持"首次同步回包"。
- **事件**：`msgtype == "event"` → 本轮不回包（handler 自然产出空）。
- **无回包**：返回**空体 `""` + 200**（对应官方"直接回复空包"）。
- 排重：`msgid` 仅写日志（不做去重）。

### 3.6 编排（`services/wecom_service.py`）

- 默认 handler：
  - 记录**结构性字段**（`msgid` / `chattype` / `msgtype` / `event_type` / 正文长度）；
  - **正文打印挂在开关后**（默认不落用户原文，调试时开启）；
  - 消息 → `sink.reply_stream("已收到，稍后接入检索…", finish=True)`；事件 → 不回包。
- 构造单例 `CallbackDriver(WeComCrypto(...), handler)`；对外暴露 `get_driver()`、`start()`、`stop()`。
- **构造与凭证校验都在 enabled 分支内**：`WECOM_BOT_ENABLED=true` 但 Token 为空 / AESKey 非 43 位 → **启动失败**（对齐 lifespan fail-fast），不留请求期降级。
- 后续在此处替换 handler 为"调 agent 跑 RAG"。

### 3.7 配置（`config/settings.py`、`config/const.py`）

`settings.py`（沿用 `os.getenv` 模块级常量风格）：

| 变量 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `WECOM_BOT_ENABLED` | bool | `false` | 是否挂载回调路由并启动驱动 |
| `WECOM_BOT_TOKEN` | str | `""` | 回调 Token（3~32 位） |
| `WECOM_BOT_ENCODING_AES_KEY` | str | `""` | 回调 EncodingAESKey（43 位） |
| `WECOM_BOT_RECEIVE_ID` | str | `""` | 智能机器人场景为空串 |
| `WECOM_BOT_LOG_CONTENT` | bool | `false` | 是否打印用户正文（默认关） |

`const.py`：`WECOM_CALLBACK_PATH = "/wecom/callback"`（**router 相对路径**，与项目惯例一致；对外完整地址 = `/api` + 该值 = `/api/wecom/callback`）。

### 3.8 路由与装配（`api/wecom.py`、`main.py`）

```python
# 路由用相对路径；最终对外地址由 include_router(prefix="/api") 拼成 /api/wecom/callback
@router.get(WECOM_CALLBACK_PATH)   # 验签失败→403；成功→PlainTextResponse(明文)
@router.post(WECOM_CALLBACK_PATH)  # 验签失败→403；解密失败→400；成功→加密 JSON 或空体
```

- 在 `main.py` 中 **仅当 `settings.WECOM_BOT_ENABLED` 为真**时 `include_router(..., prefix="/api")`。
- `main.py` 的 lifespan：启用时 `await wecom_service.start()`（内部构造 + 校验凭证），关闭时 `await wecom_service.stop()`。
- 路由 handler 通过 `wecom_service.get_driver()` 取驱动。
- 中间件兼容性（已核实）：`auth_middleware` 放行；`response_processor` 不改响应体，且**保留** POST 回调的那条 `[API]` 日志（有助确认回调到达）。
- query 参数解码由 FastAPI 自动完成（官方要求 urldecode）。

### 3.9 错误处理

| 场景 | 行为 |
|---|---|
| GET 验签失败 | `403`（明文 body，直接 return，不 raise、不套统一信封） |
| POST 验签失败 | `403`（同上） |
| 解密失败 / body 非 JSON | `400` |
| handler 抛异常 | 记 `error` 日志，返回空体 `""` + `200`，避免企微重试风暴 |
| handler 不回包（含事件） | 空体 `""` + `200` |
| `WECOM_BOT_ENABLED=true` 但凭证非法 | 启动失败（见 §3.6） |
| `WECOM_BOT_ENABLED=false` | 路由不挂载，其它部署零影响 |

## 4. 影响面

| 文件 | 改动 |
|---|---|
| `src/channels/__init__.py` | 新建 |
| `src/channels/base.py` | 新建：`InboundMessage` / `ReplySink` / `MessageHandler` / `ChannelDriver` |
| `src/channels/wecom/__init__.py` | 新建 |
| `src/channels/wecom/parse.py` | 新建：`parse_inbound` |
| `src/channels/wecom/crypto.py` | 新建：`WeComCrypto` |
| `src/channels/wecom/callback.py` | 新建：`CallbackDriver` / `_CallbackSink` / `OutboundReply` |
| `src/services/wecom_service.py` | 新建：装配 driver + 默认 handler + `get_driver/start/stop` |
| `src/api/wecom.py` | 新建：GET/POST 路由 |
| `src/main.py` | 条件 `include_router` + lifespan `start()/stop()` |
| `src/config/settings.py` | 新增 5 个环境变量 |
| `src/config/const.py` | 新增 `WECOM_CALLBACK_PATH` |
| `pyproject.toml` | 新增依赖 `pycryptodome`（项目当前无 AES 库） |
| `tests/channels/test_crypto.py` | 新建：加解密 / 验签单测 |
| `tests/channels/test_parse.py` | 新建：消息/事件解析单测 |
| `tests/channels/test_callback_driver.py` | 新建：driver GET/POST 单测 |
| `tests/api/test_wecom.py` | 新建：路由 TestClient 测试（含 enabled/disabled 挂载） |
| `docs/agents/code-map.md` | 登记 `src/channels/`（新顶层包）+ `src/api/wecom.py` |
| `src/api/README.md` | 路由清单补 `wecom.py` 一行（**必补**） |
| `.env.example`、`.env.template` | 登记 5 个 `WECOM_BOT_*` 变量 |
| `docs/agents/glossary.md` | 新增术语：通道驱动 / 回调驱动 / `ReplySink` |
| `docs/agents/api_contract.md` | 记录企微回调端点契约（方法/入参/加密与空包语义） |
| `docs/agents/cookbook.md` | 记录"企微回调接入"操作步骤 |

## 5. 验证方案

1. **单测（crypto）**：加解密往返一致；`signature` 与手工 SHA1 一致；PKCS#7 块 = 32；`receive_id=""` 不校验。
2. **单测（parse）**：文本消息取到 `text`；非文本 `text is None`；事件 `msgtype=="event"` 且 `event_type` 正确。
3. **单测（driver）**：加密 GET 查询 → `verify` 回明文；加密 POST（消息）→ 返回加密 JSON 且可解密还原、`kind=="stream"`；加密 POST（事件）→ 空体。
4. **单测（路由）**：TestClient GET/POST；`WECOM_BOT_ENABLED=false` 时该路径 404。
5. **本地自测**：crypto 模块内往返自测（不起服务）。
6. **手工冒烟（需企微后台）**：回调 URL 填 `http://<公网IP或域名>/api/wecom/callback` + Token/AESKey，保存应验证通过；再 @机器人 应收到写死回复，后端日志可见 `msgtype` 与事件分流。
7. **质量门禁**：`POSTGRES_HOST=localhost pytest tests/ -v`、`ruff check .`、`pyright src/` 不新增 error。

## 6. 风险与取舍

| 风险 | 应对 |
|---|---|
| **PKCS#7 块大小误用 16** → 解密失败 | 单测显式断言块 = 32；常量 `PKCS7_BLOCK_SIZE = 32` |
| **回调 URL 必须公网可达**（企微要求企业主体域名或 IP） | 本轮为开发验证，先用 IP；生产需备案域名（属外部事项，不阻塞开发） |
| **新增依赖 `pycryptodome`** | 需**重建 app 镜像**；云效发布走 PyPI 代理仓 `repo-okxha`（懒加载）→ **构建前需预热**，否则卡构建或拉不到包 |
| **模式切换互斥**：将来切长连接会使回调配置失效 | 已用 `ChannelDriver` 抽象隔离，切换=新增 driver + 换凭证；本轮不触发 |
| **回调流式是"拉"**，`ReplySink` 无法主动推后续更新 | 本轮只做首次 `finish=true`；同步窗口（§1.3）在接业务时须处理 |
| **企微重试导致重复回复** | 本轮接受；去重留待业务阶段 |
| **日志泄露用户正文** | 默认只记结构性字段，正文打印挂 `WECOM_BOT_LOG_CONTENT` 开关 |

## 7. 未覆盖 / 后续（需另行立项）

1. **长连接驱动**：实现同一 `ChannelDriver`，走 `wss://openws.work.weixin.qq.com`，无加解密、主动推流；复用 `channels/wecom/parse.py`。
2. **流式刷新（回调"拉"）+ 同步窗口**：`stream.id` → Redis 缓冲累计文本，供刷新事件返回；应对 §1.3 的 4s 同步窗口。
3. **业务接线**：把默认 handler 换成"调 agent 跑 RAG"，复用现有 `agent_service`。
4. **媒体入站**：图片/文件/语音下载与解密（`aeskey` 每 URL 唯一）；mixed 正文抽取。
5. **模板卡片 / 欢迎语**：启用 `reply_text` 与卡片回复。
6. **去重幂等**：`msgid` 进程内或持久化去重。
7. **认证 / 备案域名**：生产回调地址的合规准备。
8. **多机器人 / 多凭证**：当前为单机器人的单套凭证。
