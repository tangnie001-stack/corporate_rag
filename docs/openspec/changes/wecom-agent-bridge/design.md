## Context

- **现状**：`wecom-multi-bot` 已上线三台长连接机器人，`wecom_service` 的 handler 仅回写占位文案。站点侧 Agent 管线（`agent_service.stream_chat` + `_run_generation`，`create_agent` 装配，工具 `retrieve_kb` / `search_web` / `ask_user` / `delegate_task`）在企微不可达。
- **参考项目实证**（详见 `docs/agents/reference-projects.md` 域 5，本 change 的结论全部据此）：
  - **快照语义**：企微每次刷新以整段替换气泡，每帧须发累积全文（LangBot `libs/wecom_ai_bot_api/ws_client.py:718` 注释原文 + `push_stream_chunk:649`）。
  - **节流**：≤1 推/100ms 且内容未变跳过（CowAgent `wecom_bot_channel.py:685`）；单流内容上限约 200k 字符、保留尾部（LangBot `ws_client.py:65`）。
  - **收尾时限**：首帧起 6 分钟；4min 保活（openakita `wework_ws.py:1977`）。
  - **中间帧上限**：`MAX_INTERMEDIATE_STREAM_MSGS=85`（openakita `:101`）。
  - **认证等待**：LangBot `_wait_for_auth:794` 等 10s 并校验 `errcode==0`；被顶号 `_displaced=True` → 停重连（openakita）。
  - **去重**：`_seen_msg_ids` 10min TTL + 500 上限双淘汰（openakita `:586`）。
  - **投影层抽象**：`StreamPresenter` 三段式 `start → update → finalize`（openakita `channels/stream_presenter.py`）。
  - **能力位**：声明式 `capabilities` 表（openakita `channels/base.py:195`）。
- **约束**：本项目用**官方 `aibot` SDK**（非自研客户端），上列结论跨实现通用、但 SDK 层行为须由 Spike 实证；`channels` 不得 import `api`；单文件 400 行、单函数 80 行红线；单 worker 进程内状态。
- **已锁定的产品决策**：群聊粒度按群；默认 `kb` 传空（与站点逻辑一致）；`ask_user` 保持站点同款（`ASK_USER_MODE_DSH` 默认 true = dash）；trace_id 走 feedback + 日志 + 可见 footer；人设/KB 绑定留后续 change。

## Goals / Non-Goals

**Goals:**
- 企微三台机器人走**站点同款 Agent 管线**，Agent 行为（检索/联网/澄清/委派/引用）100% 继承。
- 输出投影：把 Agent 事件流保真地投影进企微能渲染的子集，长流不断、不重复、可追溯。
- 通道侧可靠性：连接就绪可判定、被顶不互踢、长流不被时限断开。

**Non-Goals:**
- 人设/知识库按机器人绑定（后续 change）。
- 澄清卡片（本轮以文本回填为主）。
- 回调模式、多实例主备、长期记忆。
- 站点前端与响应契约改造。

## Decisions

### D1 定位 = 通道适配 + 输出投影，复用站点 Agent 管线
企微不新建"RAG 通路"，而是把入站消息喂进 `agent_service` 同一条管线；是否检索/联网/澄清由 Agent 决定。
- **备选（否）**：通道侧自建"检索问答"短路 → 会分叉出第二套 Agent 行为，与站点不一致，违背"同款体验"。

### D2 会话粒度：群按 `chatid`、单聊按 `from_userid`
`session_id = wecom:{bot_key}:group:{chatid}` / `wecom:{bot_key}:single:{from_userid}`。
- **后果**：并发锁粒度=群（同群多人会撞锁，通道侧需回"正在处理上一条"）；澄清全群可答；敏感问答进全群上下文（已接受）。
- **备选（否）**：群内按人 → 上下文私有但回复公开，产生割裂；且与"群即协作场景"的用法不符。

### D3 默认 `kb_id` 传空
与站点一致：有 kb 才走 kb；传空时 Agent 自然退到联网/纯生成。
- **理由**：Agent 自主决定工具，通道不替它选库；也避免本 change 绑定 KB（后置）。

### D4 投影层 = 三段式 `start/update/finalize`
对标 openakita `StreamPresenter`：共享节流、思考格式化、非流式平台降级。
- **理由**：把"通道渲染差异"收敛到一层，业务只喂事件；节流/降级逻辑集中一处。
- **备选（否）**：在 handler 里直接 `for event: sink.reply_stream(...)` → 节流/累积/降级散落在流程里，无法复用与单测。

### D5 快照式发送（累积全文）
每帧发送截至该帧的完整正文，而非增量。
- **依据**：企微整段替换语义（LangBot `:718`）。
- **待验**：我们的官方 SDK 是否同样如此（Spike E1）。若为追加，则投影层改为"只转增量"（实现分叉点，见 Open Questions）。

### D6 节流 + 帧数/长度上限
发送间隔 ≥100ms、内容未变跳过、空白/零宽帧跳过；中间帧数上限、单流内容上限（保留尾部）。
- **理由**：每帧等 ack（5s）+ 同 `req_id` 串行 + SDK 队列容量有限，不限流会堆队列。

### D7 无渲染事件丢弃
`reasoning` / `delegate` / `task` / `model_info` / `agent_used` 在企微无对应渲染 → 丢弃且不得中断。
- **备选**：折叠进正文 → 污染答案；不采用。

### D8 引用降级为文末「参考来源」
`citation` 事件收集为文末列表，随终态帧发送。
- **理由**：企微无结构化引用通道；来源是 RAG 可信度核心，必须保留。Markdown 排版受 E8 结论约束。

### D9 trace_id：自生成 + 三路返回
handler 入口生成 `trace_<uuid>` → set `current_trace_id`（Langfuse/日志携带）；三路返回：首帧 `feedback.id`、日志锚点、终态 footer（可见）。
- **理由**：企微无 HTTP 头，`X-Trace-ID` 不可用；`feedback.id` 是站点"trace_id↔反馈"闭环的等价物。
- **待验**：`feedback.id` 取值约束与 `feedback_event` 承载字段名（Spike E10）。

### D10 `msgid` 去重：TTL + 上限双淘汰
- **理由**：占位阶段重复回复无害，接 Agent 后重复入站会重复烧 token/重复落库；裸 dict 会随消息量无界增长。
- **依据**：openakita `_seen_msg_ids` 双淘汰；CowAgent 仅内存不持久为反面。

### D11 驱动可靠性三补丁：认证等待 / 被顶停重连 / 4min 保活
- **理由**：官方 SDK `connect()` 不等认证即返回（`ws.py`），逐台 `try/except` 现为死代码、启动锚点 `n=total` 恒真失真；被顶后自动重连会互踢；长流会被 6min 时限断开。
- **依据**：LangBot `_wait_for_auth:794`；openakita `_displaced` / `_stream_keepalive_loop:1977`。

### D12 编排下沉到 `services/`（站点行为不变）
把 `api/chat.py` 的"跑一轮 + 落库 + 终态"提取为 service 函数，站点与通道共用。
- **理由**：层间规则禁止 `channels` import `api`；复制一份会分叉。
- **约束**：站点外部行为不变，须回归 `tests/` 与前端 SSE 冒烟。

### D13 声明式能力位
`ChannelDriver` 增加 `capabilities`（stream/markdown/…），业务按能力降级。
- **依据**：openakita `base.py:195`。

### D14 澄清本轮以文本回填为主
dash 模式下 `ask_user` 常出纯文本问题（options 空），卡片覆盖不全；本轮实现"入站文本回填 `pending_asks`"，卡片留后续。
- **依据**：`ask_tools.py` dash 分支 `options = q.options or []`。

### D15 不做（后置）
人设/KB 绑定、澄清卡片、多实例主备。

## Risks / Trade-offs

- [SDK 快照语义与参考项目不一致] → Spike E1 先钉；投影层预留"累积/增量"两种模式，按结论选一。
- [编排下沉改到站点热点路径] → 以"站点行为不变"为验收前提；两入口共用同一函数；跑全量测试 + 前端 SSE 冒烟。
- [节流不当导致气泡跳动或帧被丢] → 节流阈值与帧上限照参考先验值起步（100ms/85），Spike E6 实测后定。
- [被顶停重连后该台彻底离线] → 明确"被顶=人工干预信号"，记 warning + 锚点反映；不自动抢回。
- [trace_id footer 污染观感] → footer 受配置开关控制，客服场景可关。
- [群聊并发撞锁] → 通道侧对冲突回"正在处理上一条"，不静默丢。
- [Spike 期间误连线上导致互踢] → 操作前置：先关线上 wecom（`WECOM_BOT_ENABLED=false` + 重建）再跑 Spike。

## Migration Plan

1. 合入前置 `wecom-multi-bot`（其未归档 spec 提供长连接驱动）。
2. 执行 Spike，产出「官方 aibot SDK 行为事实表」，据结论锁定 D5/D9 的实现分叉与阈值。
3. 实施编排下沉 → 通道桥接 → 投影层 → 可靠性补丁。
4. 灰度：先在一台机器人验证（P1），再全量三台。
5. **回滚**：通道为可选项——`WECOM_BOT_ENABLED=false` 即停用；代码回滚沿用 `deploy-runbook` 镜像回指旧 tag。

## Open Questions

- **E1**：官方 SDK `reply_stream` 是快照还是追加？→ 决定 D5 实现分叉。
- **E3**：SDK 是否暴露可等待的认证事件、认证失败是否静默？→ 定 D11 实现方式。
- **E4**：SDK 被顶后事件与重连行为？→ 定"停重连"判据。
- **E10**：`feedback.id` 约束与 `feedback_event` 字段名？→ 定 D9 承载。
- **E7**：6min 时限对我们 SDK 是否成立、4min 保活是否有效？
- **E5**（仅影响后续卡片）：`template_card_event` 回调帧的 key/身份字段名。
