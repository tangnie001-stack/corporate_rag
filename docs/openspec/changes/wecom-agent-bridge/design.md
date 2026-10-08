## Context

- **现状**：`wecom-multi-bot` 已归档（主规格 `wecom-channel`），三台长连接机器人上线，但 `wecom_service` 的 handler 仅回占位文案。站点侧 Agent 管线（`agent_service.stream_chat` + `_run_generation`，`create_agent` 装配，工具 `retrieve_kb` / `search_web` / `ask_user` / `delegate_task`）在企微不可达。
- **参考项目实证**（详见 `docs/agents/reference-projects.md` 域 5，本 change 的结论全部据此）：
  - **快照语义**：企微每次刷新以整段替换气泡，每帧须发累积全文（LangBot `libs/wecom_ai_bot_api/ws_client.py:718` 注释原文 + `push_stream_chunk:649`）。
  - **节流**：≤1 推/100ms 且内容未变跳过（CowAgent `wecom_bot_channel.py:685`）；单流内容上限约 200k 字符、保留尾部（LangBot `ws_client.py:65`）。
  - **收尾时限**：首帧起 6 分钟；4min 保活（openakita `wework_ws.py:1977`）。
  - **中间帧上限**：`MAX_INTERMEDIATE_STREAM_MSGS=85`（openakita `:101`）。
  - **认证等待**：LangBot `_wait_for_auth:794` 等 10s 并校验 `errcode==0`；被顶号 `_displaced=True` → 停重连（openakita）。
  - **去重**：`_seen_msg_ids` 10min TTL + 500 上限双淘汰（openakita `:586`）。
  - **投影层抽象**：`StreamPresenter` 三段式 `start → update → finalize`（openakita `channels/stream_presenter.py`）。
  - **能力位**：声明式 `capabilities` 表（openakita `channels/base.py:195`）——**本 change 未采用**，理由见 D13。
- **本仓约束（已核）**：
  - 本项目用**官方 `aibot` SDK**（非自研客户端），上列结论跨实现通用、但 SDK 层行为须由 Spike 实证。
  - **`session_id` 列宽 `String(36)`**（`sessions` / `conversation_history` / `feedback` 三表，`models/chat.py:39`、`models/feedback.py:17`）⇒ 会话标识必须 ≤36 字符。
  - **回调 sink 只保留最后一次回包**且 handler 在 HTTP 请求内同步 `await`（`channels/wecom/callback.py`）⇒ 回调模式跑不了几十秒的 Agent 回合。
  - `channels` 不得 import `api`；单文件 400 行、单函数 80 行红线；单 worker 进程内状态。
  - trace_id 有统一入口 `new_trace_id()`（`infra/llm/tracing.py`，`trace_<uuid>`，校验 `^[A-Za-z0-9_-]{1,120}$`）；`stream_chat(kb_id="")` 表示不检索；`deep_thinking` 默认 False。
- **已锁定的产品决策**：群聊粒度按群；默认 `kb` 传空（与站点逻辑一致）；`ask_user` 保持站点同款（`ASK_USER_MODE_DSH` 默认 true = dash）；trace_id 走 feedback + 日志 + 可见 footer；人设/KB 绑定留后续 change；**桥接仅长连接**。

## Goals / Non-Goals

**Goals:**
- 企微三台机器人走**站点同款 Agent 管线**，Agent 行为（检索/联网/澄清/委派/引用）100% 继承。
- 输出投影：把 Agent 事件流保真地投影进企微能渲染的子集，长流不断、不重复、可追溯。
- 通道侧可靠性：连接就绪可判定、被顶不互踢、长流不被时限断开。

**Non-Goals:**
- 人设/知识库按机器人绑定（后续 change）。
- 澄清卡片（本轮以文本回填为主）。
- **回调模式的桥接**（保持 legacy 占位；其流式需"response_url 轮询"，另开 change）。
- 企微侧取消能力（无按钮；后续以卡片补）。
- 多实例主备、长期记忆、站点前端与响应契约改造。

## Decisions

### D1 定位 = 通道适配 + 输出投影，复用站点 Agent 管线
企微不新建"RAG 通路"，而是把入站消息喂进 `agent_service` 同一条管线；是否检索/联网/澄清由 Agent 决定。
- **备选（否）**：通道侧自建"检索问答"短路 → 会分叉出第二套 Agent 行为，与站点不一致，违背"同款体验"。

### D2 会话标识：确定性派生 36 字符（UUIDv5）
`session_id = uuid5(WECOM_NS, "wecom|{bot_key}|{group|single}|{id}")`，其中群用 `chatid`、单聊用 `from_userid`；同群/同人**稳定**。可读信息（`bot_key`/`chatid`/`userid`）走**日志与 `sessions.title`**，不放进 key。
- **`WECOM_NS` 必须是固定字面常量**（建议入 `src/config/const.py`）——若取随机/进程内 UUID，`session_id` 每次重启都变、企微会话与历史每重启断链；须有单测断言"跨进程稳定"。
- **理由**：`session_id` 三表均为 `String(36)`，可读拼接串落不了库；UUIDv5 恰好 36 字符、确定性、零 schema 变更。
- **后果**：并发锁粒度=群（同群多人会撞锁，通道侧需回"正在处理上一条"）；敏感问答进全群上下文（已接受）。
- **备选（否）**：(b) 迁移三表列宽 → 动 schema 与迁移；(c) 自建映射表 → 多一张表维护；群内按人 → 上下文私有但回复公开，割裂。
- **`user_id` 同样派生**：`sessions.user_id` 亦为 `String(36)` 且契约语义为 UUID（`api_contract.md`），故 `user_id = uuid5(WECOM_NS, "wecom-user|{from_userid}")`；可读 `from_userid`（非超管为密文）只进日志。

### D3 默认 `kb_id` 传空
与站点一致：有 kb 才走 kb；传空时 Agent 自然退到联网/纯生成。
- **理由**：Agent 自主决定工具，通道不替它选库；也避免本 change 绑定 KB（后置）。

### D4 投影层 = 三段式 `start/update/finalize`
对标 openakita `StreamPresenter`：共享节流、思考格式化、非流式平台降级。
- **理由**：把"通道渲染差异"收敛到一层，业务只喂事件；节流/降级逻辑集中一处。
- **备选（否）**：在 handler 里直接 `for event: sink.reply_stream(...)` → 节流/累积/降级散落，无法复用与单测。
- **降级载体（不新增方法）**：`ReplySink` 只有 `reply_stream(content, finish)`，故"非流式降级"即**单次 `reply_stream(content, finish=True)`**（一次性收尾）；不新增 sink 方法、不加能力位（D13）；**唯一例外**是 D9 所需的可选 `feedback` 参数。

### D5 快照式发送（累积全文）
每帧发送截至该帧的完整正文，而非增量。
- **依据**：企微整段替换语义（LangBot `:718`）。
- **E1 已确认（2026-10-08 实测，见 `docs/agents/wecom-sdk-facts.md`）**：官方 SDK 同为**快照**（同一 stream 发 `甲`→`甲乙`→`甲乙丙`，气泡最终显示 `甲乙丙`）。
  原先预留的"若反证为追加则先修订投影 spec"的**分叉关闭**，投影能力按快照实现即可。

### D6 节流 + 帧数/长度上限 + 长流保活
发送间隔 ≥100ms、内容未变跳过、空白/零宽帧跳过；中间帧数上限、单流内容上限（保留尾部）；**流式期间每 ≤4min 发一次非终态帧**以防 6min 收尾时限。
- **理由**：每帧等 ack（5s）+ 同 `req_id` 串行 + SDK 队列容量有限，不限流会堆队列；保活与流生命周期同源，故归投影层（见 D11）。
- **依据**：CowAgent `:685`、LangBot `:65`、openakita `_stream_keepalive_loop:1977`。
- **保活帧豁免**：保活帧 SHALL 不受"内容未变跳过"与"空白/零宽不发送"约束——长静默期（澄清等待/出网调用）累积正文与上一帧相同，若不豁免则保活与节流互斥；保活即**重发当前累积快照**，不新造可见文案。
- **实现要点（消费形态：队列，不是 `wait_for(anext)`）**：`_subscribe_events` 只在有事件时产出，澄清等待期可长达 `ASK_USER_TIMEOUT`（120s）无任何事件。事件流 SHALL 以 **`asyncio.Queue`** 暴露（后台 pump 任务把上游生成器事件入队），投影对 `queue.get()` 施加超时，超时即发保活帧。**禁止**对上游取事件的在途任务施加超时取消——`_subscribe_events` 不捕 `CancelledError`（`streaming.py:22-62`），`asyncio.wait_for(anext(gen), t)` 一旦超时即终结生成器、后续取值抛 `StopAsyncIteration`，**恰在需要保活时把长流静默截断**。

### D7 无渲染事件丢弃
`reasoning` / `delegate` / `task` / `model_info` / `agent_used` 在企微无对应渲染 → 丢弃且不得中断。
- **备选**：折叠进正文 → 污染答案；不采用。

### D8 引用降级为文末「参考来源」
`citation` 事件收集为文末列表，随终态帧发送。
- **理由**：企微无结构化引用通道；来源是 RAG 可信度核心，必须保留。Markdown 排版受 E8 结论约束。

### D9 trace_id：自生成 + 三路返回
handler 入口用既有 `new_trace_id()` 生成 `trace_<uuid>` → set `current_trace_id`（Langfuse/日志携带）；三路返回：首帧 `feedback.id`、日志锚点、终态 footer（**受配置开关控制；反馈标识不可用时强制开启**）。
- **理由**：企微无 HTTP 头，`X-Trace-ID` 不可用；复用 `new_trace_id()` 与站点同源，故 Langfuse 行为一致。
- **E10 已确认（2026-10-08 实测）**：`feedback` 承载**可用** —— 首帧带 `feedback={"id": trace_id}` 后该条消息出现 👍/👎 按钮，用户点赞的回执帧里 `body.event.feedback_event.id` **原样回传**该值（`type` 表示赞/踩）。因此**三路全部可用**，footer 按配置开关即可（**不必**"反馈不可用时强制开启"，该退化分支保留但不触发）。
- **协议前提（须扩 `ReplySink`）**：现有 `ReplySink.reply_stream(content, finish)` **无 feedback 参数**，两实现（`callback.py` / `long_connection.py`）与相关测试绑定该协议 ⇒ 须扩展为可选参数 `feedback: dict | None = None`（callback 实现忽略），否则该路落不了地。

### D10 `msgid` 去重：TTL + 上限双淘汰
- **理由**：占位阶段重复回复无害，接 Agent 后重复入站会重复烧 token/重复落库；裸 dict 会随消息量无界增长。
- **依据**：openakita `_seen_msg_ids` 双淘汰；CowAgent 仅内存不持久为反面。
- **局限（接受）**：进程内，重启后窗口清空——企微极少重推，接受；如需强保证另议。

### D11 驱动可靠性两补丁（保活不在此层）
**认证等待** + **被顶处置**；保活归投影层（D6）。**E3/E4 已实测，结论见 `docs/agents/wecom-sdk-facts.md`。**
- **理由**：官方 SDK `connect()` **不等认证**即返回（实测差 ~0.17s），逐台 `try/except` 现为死代码、启动锚点 `n=total` 失真；被顶会形成互踢循环。**保活需要知道有没有活跃流，只有投影层持有 stream/req_id**，放驱动会反向依赖流状态。
- **认证等待与失败判据（E3 实测）**：驱动 SHALL 等 **`authenticated` 事件**才判定该台就绪；失败以 **`error` 事件**呈现，**凭证类判据 = `errcode=853000`**（`errmsg` 形如 `invalid bot_id or secret`）——**普通连接失败/接收错误的 `on_error` 不构成凭证类**（据此行动会把瞬时错误变成永久离线）。SDK 认证失败**既不抛也不断连**、且**自己不重连**，故驱动 SHALL 主动 `disconnect()` 并置"就绪=否"，避免留下挂着的未认证连接。**等待超时不触发断开**（只置降级、交 SDK 自愈），且等待 SHALL 有上界（≤5s）。
- **被顶处置（E4 实测，方向修正）**：被顶是**三步**——① 服务端给旧连接推业务事件 `event.disconnected_event`（此刻 WS 无异常）；② 服务端**随后**才关闭旧连接（实测延迟 **313s / 2.2s**，不固定，原因 `no close frame received or sent`）；③ 该关闭触发 SDK `disconnected` → 自动重连（1s 退避）→ **重连成功的一方立刻顶掉另一方** ⇒ **互踢循环自我维持**。
  ⇒ 驱动 SHALL **订阅 `event.disconnected_event`**，收到即：记 warning + 标记该台**不就绪** + **主动 `disconnect()` 且不再抢回**。只"停重连"不够——必须在收到该事件时**立即断开自己**，否则服务端仍会在它自己的时机关闭并触发 SDK 重连，循环照旧。
  ⚠️ **当前生产驱动未订阅该事件**（`long_connection.py` 的 `_EVENT_EVENTS` 缺它）⇒ 一旦两连接重叠（滚动重启 / 双进程）会**静默无限互踢**。
- **注册表语义拆分（防关机泄漏）**：`_drivers` SHALL 收**所有已启动**的驱动（供 `stop()` 关闭，**含未认证就绪者**）；锚点的"认证成功台数"另计（按"认证就绪"标志/集合），**二者不得混用同一计数**——否则超时驱动不入表会让 `stop()` 泄漏该活连接。
- **等待须有上界且并行启动**：三台现为逐台 `await driver.start()`，加"等认证"后串行阻塞启动，而主规格有"SHALL NOT 阻塞应用启动" ⇒ 认证等待 SHALL 有上界（≤5s），且逐台启动 SHALL 并行（`asyncio.gather(..., return_exceptions=True)`）以不阻塞启动（保留单台降级语义）。
- **规范归属（一事一档）**：驱动层行为（认证等待 / 被顶处置）**唯一**由本 change 的能力 `wecom-agent-bridge` 规范；`wecom-channel` 的 delta 只收紧"装配 / 降级 / 锚点台账"口径，不重复规范驱动行为。
- **停重连的适用范围**：仅"**被顶替 / 凭证类致命断开**"停重连；**普通瞬时断开仍 SHALL 交 SDK 自愈**（不得因抖动停止重连）。

### D12 下沉入口：`services` 层单一 `start_turn(...)` → 事件流（含闸门与收尾）
在 `services/` 提供**唯一编排入口**，产出**结构化 `SSEEvent` 流**（不搬文本层），内部依次完成站点编排的全部前置与收尾：

```
async def start_turn(svc, *, session_id, kb_id, query,
                     user_id="", deep_thinking=False, agent="",
                     title: str | None = None,   # None → query[:20]（站点不传）
                     abort_signal=None) -> TurnHandle
#   TurnHandle.events: AsyncIterator[SSEEvent]   # 以 asyncio.Queue 暴露（见 D6：不得取消上游在途取值）
#   冲突抛 TurnBusy（由调用方决定：站点 409 / 通道回"正在处理上一条"）
```

内部顺序（自 `api/chat.py` 的 `_stream_rag_response` + `_run_with_finalize` 提取）：
1. `await svc.set_chat_repo()` —— **漏此则 `save_*` 静默跳过**（`ChatManager.save_*` 是 `if self._persistence:` 无异常无告警）。
2. **原子闸门（进程内为主，Redis 兜底）**：以**进程内原子预留**为主——`is_running` 检查与"把会话标为预留"之间**不得有 `await`**（单 worker 部署下进程内状态才是权威）；Redis `SETNX chat_lock` 仅作**跨实例兜底**（不可用时按现状跳过、不阻塞请求）。未获得即抛 `TurnBusy`。只做预检、或把注册放在多次 `await` 之后，会在 check→注册 窗口内双开同一 `session_id`，随后一方 `clear_buffer` 清掉另一方缓冲。
3. 落库前置：`save_session_async` + `save_user_async`。
4. `svc.agent_service.stream_chat(...)` → `(subscription, launch_ctx)`（**在 `SSEEvent` 层**；投影层直接吃结构化事件，无需反解 SSE 文本）。
5. 构造 `abort_signal` 并接到 `ctx.abort_signal`（`ask_user` 的等待依赖它）。
6. 起后台任务：跑生成 + 收尾落库（complete/interrupted）+ 写终态事件 + 释放锁 + 注销注册表。
7. 注册进 `streaming_manager`（把第 2 步的**同步预留**替换为任务引用）；返回 handle。

- **`title` 入参**：`sessions.title` 仅首次落库确定（`create_session` 幂等、`on_conflict_do_nothing`），而 D19 要求企微会话可区分 ⇒ 入口 SHALL 接受可选 `title`（站点不传 → `query[:20]`；通道传 `[企微·{bot_key}] …`）。否则该"可区分"要求无干净实现路径。
- **失败语义（保站点行为不变）**：分两类，边界在 `stream_chat` **调用**处：
  - **编排前置（闸门 / 落库）失败 → 抛**：闸门未获得 → `TurnBusy`（站点译 **409**，见下）；落库前置失败 → 抛出（站点 **500**）。
  - **`stream_chat` 调用及其后失败 → 不抛**：改为在 `TurnHandle.events` 上产终止态 `error` + `done` 事件（站点 **200 + SSE 错误**）。故 `start_turn` 在 `stream_chat` 失败时 **SHALL 仍返回句柄**，其 `events` 立即产终止态事件。
  - 依据：站点现行为正是如此——订阅建立失败发 SSE 错误（`chat.py:190-200`），落库前置失败抛（`:344-352`）；合并成一类会让站点从"200+SSE"变"500"，违反"站点外部行为不变"。
- **预留的释放（防会话永久锁死）**：第 2 步建立的进程内预留 SHALL 在**所有早退路径**释放——第 3–7 步包进 `try/except`，任一失败时先释放预留（及已取的 Redis 锁）再按上面的失败语义抛出/收尾。否则一次落库或建订阅失败会让该会话被**永久占用**（Redis 不可用时预留是唯一权威闸门且无 TTL），后续入站一律被拒——这是相对站点的回归（站点落库失败时未注册、且会释放 Redis 锁）。

消费者：
- `api/chat.py`：`start_turn` → 事件转 SSE 帧；`TurnBusy` → 409（与现状行为一致）。
- `channels/wecom/handler.py`：`start_turn` → 事件喂投影层；`TurnBusy` → 回"正在处理上一条"。
- `api/sessions.py`：resume 端点复用 `streaming_manager` 订阅；**锁释放函数随之下沉**，其 import 由 `api.chat` 改为 services（`sessions.py:13` 现 import `api.chat`，漏改会断）。

- **理由**：编排移出 api 层但保留唯一实现，通道与站点不分叉；闸门与落库前置内聚于同一入口，避免通道各写一半。
- **约束**：**站点外部行为不变**——入口对站点是等价替换，须回归 `tests/` 与前端 SSE 冒烟。
- **备选（否）**：通道内复制一份编排 → 双份维护且易漏闸门/落库前置；下沉文本层 → 通道须反解 SSE 文本。

### D13 能力位：本轮不做（删除）
原计划给 `ChannelDriver` 加 `capabilities` 声明位。评审发现**无消费者**：handler 只拿到 `(msg, sink)`、见不到 driver，且 handler 是跨机器人共享的模块函数；本 change 又只有一个流式驱动（回调不桥接）⇒ 声明位属过度设计，**本轮删除**。
- **复查条件**：将来接入多通道/多能力差异时再引入，且**必须有读点**（放 sink，或按通道实例暴露给业务）。

### D14 澄清：文本回填 + 仅触发者 + 一问一答
dash 模式下 `ask_user` 常出纯文本问题（options 空），卡片覆盖不全；本轮以文本回填。**只有"触发澄清者"的下一条消息**才回填；他人消息走正常回合（撞会话锁 → 回"正在处理上一条"）。**一次澄清按一问一答**：若模型一次问多问，合并为一条编号问题，用户整段回答塞进单个 `custom`。
- **理由**：群粒度 A 共享的是上下文、不是决策权；无此规则则群里任何消息都会被误吞为答案。`_format_answers_text` 只格式化答案、不带问题标签，多问自由文本会歧义，故先做单问。
- **依据**：`ask_tools.py` dash 分支 `options = q.options or []`。
- **触发者映射归通道**：`ask_user` 侧只有 `session_id`、`pending_asks` 也不存身份 ⇒ 通道须自持 `session_id → 触发者 userid` 映射（**带 TTL + 上限**），在收到 `ask_user` 事件时写入，回填时校验来源；TTL 对齐澄清超时并留清理。
- **回填须写历史与消息表**：站点经 `clarify.py` 把答案写 Redis 历史 + MySQL（`chat_manager.add_message_async` + `save_user_async`）⇒ 通道回填路径 SHALL 有同样的写历史 + 写库效果（**经下条所述的共用函数**，不自行实现），否则答案不进上下文、刷新即丢。
- **答案应用逻辑须下沉共用（不得复制）**：站点把"pop `pending_asks` + `_format_answers_text` + 双写"整段写在 `api/clarify.py`。通道既不得 import `api`、也不得复制一份（与 D12「唯一实现」冲突）⇒ 该逻辑 SHALL 下沉为 `services` 函数（如 `resolve_clarify_answer(session_id, answers)`），`clarify.py` 与桥接共用；tasks 增项。

### D15 桥接仅长连接，回调保持 legacy 占位
桥接 handler 只对长连接路径生效；`WECOM_BOT_MODE=callback` 仍走占位 handler。
- **理由**：回调 sink 只保留最后一次回包且在 HTTP 请求内同步 `await`，Agent 回合（几十秒）必超回调超时窗口并触发重试风暴；真正的回调流式是"无刷新回调 + response_url 轮询"的独立工程。
- **备选（否）**：共用 handler 顺带升级 → 事实相反（超时/重试），无收益。

### D16 空回答 / `abstention` 的终态兜底
`abstention` 事件 → 终态正文追加转人工提示；**终态若正文仍为空 → 发兜底文案并 `finish=true`**。
- **理由**：本项目存在"迭代触顶 → 空回答"的已知情形；若沿用"空白 final 不关流"，无后续内容时会悬挂到 6min。"不关流"仅在确有后续内容时成立。

### D17 占位首帧 = 首个 `status` 事件
不新造文案：把站点管线本就有的首个 `status` 事件（如"检索中…"）渲染为占位首帧。
- **理由**：给用户即时反馈、为 keepalive 提供锚点、规避"首帧前时限未知"（该项列入 Spike E11）。

### D18 错误文案脱敏
用户侧只发脱敏文案（复用既有文案常量），原始异常只进日志。
- **理由**：企微是半公开场景（尤其群聊），不得外泄内部细节。

### D19 会话不隔离，但可区分
企微会话与站点对话**同表**（harness 统一），不隔离；靠 `sessions.title`（如 `[企微·finance] …`）与日志区分。
- **理由**：统一会话是对的；过滤留后续，避免过度设计。

### D20 依赖注入：services 层单例访问器
`AppService` 单例访问器从 `api/dependencies.py` 挪到 `services/`（`api/dependencies` 变为转发薄壳，`Depends(get_app_service)` 用法不变）；通道侧（`wecom_service` / handler）在用时经该访问器取 `AppService`。
- **理由**：`channels` 不得 import `api`，而现单例只存在于 api 层 → 否则桥接**拿不到 `AppService`**（`lifespan` 现以 `await wecom_service.start()` 无参调用，handler 又是模块级共享函数）。访问器下沉后两侧都不违层规则，且**不改 `lifespan` 顺序与 `start()` 签名**。
- **备选（否）**：`lifespan` 构造并显式注入 `start(svc)` → 把 `AppService`（会建 `VectorStore`/`DocRouter`/`ChatManager`/`AgentService`）从"首请求延迟初始化"提前到启动期，改启动语义与耗时；全局单例虽不理想，但与 api 层既有做法一致、改动最小。

### D21 消费 `feedback_event`：还原 trace_id
驱动已订阅 `event.feedback_event`（`long_connection.py`），但现流程对 event 一律 `return`。本 change SHALL 处理反馈事件：取回反馈标识（= `trace_id`）并落日志（持久化到反馈表留后续）。
- **理由**：spec 断言的"回传事件中可还原 `trace_id`"必须有实现，否则该场景不可证。
- **前置门槛（须纳入 Spike）**：驱动对**每帧**先 `parse_inbound(frame["body"])`，而它**强制 `msgid`**（`parse.py:22`）⇒ 若反馈回执的 body 无 `msgid`，事件在进 handler 前即被丢弃。E10 SHALL 增记该字段有无；若无，驱动 SHALL 按事件类型**在 parse 前分流**（或放宽事件帧的 parse 约束）。

### D22 不做（后置）
人设/KB 绑定、澄清卡片、回调桥接、企微取消、多实例主备。

## Risks / Trade-offs

- [SDK 快照语义与参考项目不一致] → Spike E1 先钉；若 E1 **反证为追加**，则**先修订**投影 spec 的「快照式流式投影」需求再实施，**不预留双模式**（双模式 presenter 会直接违反该硬 SHALL）。
- [编排下沉改到站点热点路径] → 以"站点行为不变"为验收前提；两入口共用同一函数；跑全量测试 + 前端 SSE 冒烟。
- [节流不当导致气泡跳动或帧被丢] → 节流阈值与帧上限照参考先验值起步（100ms/85），Spike E6 实测后定。
- [被顶停重连后该台彻底离线] → 明确"被顶=人工干预信号"，记 warning + 锚点反映；不自动抢回。
- [群聊澄清被误吞] → D14 的"仅触发者回填"规则；他人消息走正常回合。
- [空回答悬挂流] → D16 兜底收尾。
- [trace_id footer 污染观感] → footer 受配置开关控制，客服场景可关。
- [群聊并发撞锁] → 通道侧对冲突回"正在处理上一条"，不静默丢。
- [Spike 期间误连线上导致互踢] → **生产开关由 compose `environment` 强制覆盖 `.env`**（根 `docker-compose.image.yml:203-205` 写死 `WECOM_BOT_ENABLED: "true"`）⇒ 操作前置 SHALL 是"改 compose 的 `environment`（或加 override）再重建"，**置 `.env` 无效**；否则 Spike 仍会踢掉生产连接。

## Migration Plan

1. 前置 `wecom-multi-bot` 已归档（主规格 `wecom-channel` 就位），无需额外合入步骤。
2. 执行 Spike，产出「官方 aibot SDK 行为事实表」，据结论确认 D5（快照语义）并定 D9（承载可用性）与节流/保活阈值。
3. 实施编排下沉 → 通道桥接 → 投影层 → 可靠性补丁。
4. 灰度：先在一台机器人验证（P1），再全量三台。
5. **回滚**：停用须改 compose `environment`（或 override），**置 `.env` 无效**（同 Risks）；代码回滚沿用 `deploy-runbook` 镜像回指旧 tag。

## Open Questions

**全部已关闭（2026-10-08 Spike 实测；结论与证据见 `docs/agents/wecom-sdk-facts.md`）**：

| # | 原问题 | 实测结论 |
|---|---|---|
| E1 | `reply_stream` 是快照还是追加？ | **快照**（整段替换）→ D5 分叉关闭 |
| E2 | 重复帧 / 空白帧能否安全跳过？ | 平台**接受**且**无可见瑕疵** → 跳过是优化、非必需 |
| E3 | 是否暴露可等待的认证事件、失败是否静默？ | **有 `authenticated` 事件**（`connect()` 不等它）；失败只走 `error`（`errcode=853000`），**不断连、不重连** |
| E4 | 被顶后的事件与重连行为？ | `disconnected_event`（立即）→ 服务端延迟关闭（313s / 2.2s）→ SDK 自动重连 → **互踢循环**；D11 已按此改写 |
| E5 | `template_card_event` 的 key / 身份字段？ | `body.event.template_card_event.event_key` + `.task_id`；帧带 `from.userid`/`chattype`/`msgid`/`msgtype` → **卡片路可用** |
| E6 | 帧节奏与 ack？ | 单帧 ack **0.24~0.45s**；30 帧无超时 → 100ms 节流不是瓶颈 |
| E7 | 6min 时限是否成立、4min 保活是否有效？ | 保活下流**存活 8.5 分钟以上** → 240s 保活有效 |
| E8 | Markdown 渲染能力？ | **全量渲染**（含表格、代码块） |
| E10 | `feedback.id` 与 `feedback_event` 字段名？ | `body.event.feedback_event.id` **原样回传** → 三路可用，footer 不必强制开启 |
| E11 | 首帧前是否有时限？ | 空等 **120s** 后首帧仍可发（6 分钟级未测，非必要） |
| OQ1（架构评审） | 下沉入口形态与依赖注入点 | 已定 → 见 **D12** / **D20** |
