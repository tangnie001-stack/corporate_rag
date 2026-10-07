## Context

- **现状**：`settings.py` 384 行（接近 400 红线）有 **8 个**扁平 `WECOM_BOT_*`（`ENABLED`/`MODE`/`ID`/`SECRET`/`TOKEN`/`ENCODING_AES_KEY`/`RECEIVE_ID`/`LOG_CONTENT`）；`wecom_service` 持有单例 `_driver`（`CallbackDriver | LongConnectionDriver | None`），`start()` 按 `WECOM_BOT_MODE` 构造一台；handler 是全局单例 `_default_handler`，签名为 `(msg, sink)`。
- **需求**：三条业务线各一台机器人，**全部长连接**；回复暂用占位（不接 RAG）。
- **约束**：
  - 长连接**每机器人同时只能一条连接**（官方 101463）；单实例部署，3 台 = 同进程 3 条连接，可接受。
  - `main.py` lifespan 已 `await wecom_service.start()/stop()`，无需改；`api/wecom.py` 非 `callback` 模式 → 404，无需改。
  - 官方 SDK `aibot`：只用 `connect()` / `disconnect()` / `on()` / `reply_stream()`；**禁用 `run()`**。
  - 现有 `start()` 开头 `if not settings.WECOM_BOT_ENABLED: return`，凭证校验在门控**之内**。
  - 日志规约：键名 snake_case；字符串值 token 安全（`^[A-Za-z0-9_./:@-]+$`）否则须转义。
  - **spike 实测（task 1.1）**：`WECOM_BOTS` 的 JSON **可**穿过宿主 `python-dotenv` 与容器 compose `env_file` 两道解析（引号保留、空格安全、`#`/`$` 安全），**但必须写在一行**（多行美化会让两道解析都失败）。

## Goals / Non-Goals

### Goals

- 支持 N 台机器人（每台一条长连接），配置**一处声明**。
- 配置/凭证错误 **fail-fast**（且**不泄露 secret**）；连接失败 **逐台降级**。
- 入站消息能区分业务线（`aibotid → bot_key`），日志带机器人维度。
- 官方 SDK 的用法与约束、依赖与部署影响有**成篇文档**。

### Non-Goals

- 接 RAG / 每业务线绑知识库（回复仍为占位文案）。
- 回调模式的**多机器人**（回调保留为 legacy 单机器人，见 D8）。
- 多实例主备分配 / 选主（单实例部署；上 2 台机时另议）。
- 每业务线不同的欢迎语 / 模板卡片 / 占位文案；`WECOM_BOT_LOG_CONTENT` 保持全局（D11）。
- `msgid` 去重（本轮仅记日志；接 RAG 时另做）。
- 健康探针 / 连接状态对外暴露。

## Decisions

### D1 配置形状 = `WECOM_BOTS` JSON 数组（**单行**）；代码落 `src/config/wecom_bots.py`

每台 `{"key", "bot_id", "secret"}`。理由：N 可变、一处声明、好校验。

- **落点**：`settings.py` 已 384 行，加代码会破 400 红线 ⇒ 新增 `src/config/wecom_bots.py` 承载 `WeComBotConfig`（frozen dataclass）与 `load_wecom_bots()`（读 `os.getenv("WECOM_BOTS")`、`json.loads`、逐台校验）；`settings.py` 不增长（必要时仅一行）。
- **单行约束**（spike 结论）：JSON 必须写在一行；跨行会让解析失败 ⇒ 由启动期校验拦下（见 spec）。
- **校验时机**：解析与校验都在 `load_wecom_bots()` 内、由 `start()` 调用 ⇒ **受 `WECOM_BOT_ENABLED` 门控**（禁用时不解析、坏配置不炸），与现有 `_validate_credentials` 一致。
- **备选（非必需）**：若将来 JSON 方案出现脆弱，可退回编号 env 变量（`WECOM_BOT_1_KEY` / `_ID` / `_SECRET`）；spike 已证明 JSON 可用，故本 change 不采用。
- 备选（否）：单独配置文件 → 违背"密钥只放 `.env`"约定。

### D2 `WECOM_BOT_MODE` 保留

全为 long connection，但 `mode` 保留（默认 `long_connection`）以支持 legacy 回调与可测性；不引入"每台一个 mode"。

### D3 驱动注册表 = `dict[str, ChannelDriver]`；逐台降级；`stop` 容错幂等；空注册表由 `start()` 判

- `_drivers: dict[str, ChannelDriver]`（**不限于长连接**）：长连接按 `key` 存入；**回调存保留键 `callback`**（见 D8）⇒ `get_callback_driver()` 从该键取。
- `start()` 逐台构造 + `await driver.start()`，**某台失败单独记 warning 并跳过**（不置空全局、不阻塞应用）；凭证/配置校验仍 fail-fast。
- **空注册表 fail-fast**：`mode=long_connection` 且注册表为空 ⇒ 在 `start()` 内按 mode 判定后抛错（不能放进通用 `load_wecom_bots()`，因为 callback 模式空注册表是正常的）。
- `stop()` 逐台 `try/except`（失败记 warning 继续），且**幂等**。
- **启动锚点日志**：成功路径记一条 INFO（如 `[wecom] bots connected n=<ok> total=<all>`），供冒烟与运维判定"连上了几台"。

### D4 解析只做一次；反查表归属 `wecom_service`；解析在逐台 `try/except` **之外**

`start()` **唯一**调用 `load_wecom_bots()`，把注册表 + `aibotid → bot_key` 反查表存进 `wecom_service`；handler **只读**该表，**绝不逐消息重解析**。解析调用置于逐台 `try/except` 之外，否则配置错误会被降级吞掉。

### D5 分发与日志：handler 内 `aibotid` 反查；`bot_key=`；`bot_id` 值编码

- 反查（D4 的表）；未知 `aibotid` 记 warning 并安全返回。
- 业务 handler 日志键名 `bot_key=<key>`；`long_connection.py` 的错误日志用 `self._bot_id` 记 `bot_id=<id>`。
- **`bot_id` 值编码**：`bot_id` 只校验非空+唯一、无字符集约束 ⇒ 写日志时**按日志规约对值编码**（token 不安全则加引号转义），不得裸写导致畸形日志行。

### D6 删除死配置

长连接扁平 `WECOM_BOT_ID` / `WECOM_BOT_SECRET` 删；`TOKEN` / `AESKEY` / `RECEIVE_ID` 保留并注明"仅 `callback` 模式"。

### D7 SDK 说明落点

用法与约束 → `docs/agents/code-map.md`；依赖与部署影响 → `docs/agents/deploy-runbook.md`；**不新开归属文档**。

### D8 回调保留为 legacy 单机器人，固定 `bot_key=callback`

回调机器人不进 `WECOM_BOTS`（用扁平变量）⇒ 反查会失败。约定：回调模式下 handler 的 `bot_key` **固定为 `callback`**，且其驱动存入 `_drivers` 的**保留键 `callback`**。

### D9 `get_driver(bot_key)`，未命中抛 `RuntimeError`

`get_driver()` 生产无调用方（仅测试）；改为 `get_driver(bot_key)`，未命中抛 `RuntimeError`（与 `get_callback_driver` 风格一致）。

### D10 配置校验：字段非空、`key` 字符集、`key`/`bot_id` 唯一、`callback` 保留键、**错误信息脱敏**

`key` 须匹配 `^[a-z0-9_-]+$`（日志可裸写、可作 dict key）且唯一；`bot_id` 须唯一；`key`/`bot_id`/`secret` 非空；**`callback` 为保留键，长连接侧拒绝**。违反即 fail-fast。

- **错误信息脱敏（评审 Blocker）**：错误信息只允许标识到**下标 / `key`**（如 `WECOM_BOTS[1].secret 为空`），**禁止包含 `WECOM_BOTS` 原值或任何 `secret` 内容** —— 三台凭证同处一个 env blob，回显原值会批量泄露。

### D11 `WECOM_BOT_LOG_CONTENT` 保持全局

沿用现状：一个全局开关控制是否打印用户正文。

### D12 "禁止 `run()`"有可测守卫

除文档外，增加**一处自动化断言**：长连接驱动源码不出现 `.run(`（最小守卫，避免"承诺的失败模式无处失败"）。

### D13 spec 粒度

`key` 字符集、`bot_id` 唯一、`callback` 保留键、`ENABLED` 门控、单行约束、错误脱敏**并入**既有配置 Requirement；`bot_key=callback` 写入 legacy Requirement。

## Risks / Trade-offs

- [~~JSON 穿不过 `env_file`~~] → **已由 spike 排除**（两道解析均通过）；残留风险仅"必须单行"，已写进 `.env.example` 并由启动期校验拦下。
- [**fail-fast 错误信息泄露 secret**] → D10 明令只标识到下标/`key`，禁止含原值/secret（评审 Blocker）。
- [3 台同进程，进程重启 / 单机故障 → 3 台同时断] → 单实例部署已接受；多实例主备不在本 change。
- [handler 仍占位 → 切长连接后用户拿到占位文案] → 本轮目标即"通链路"，RAG 另开 change。
- [**逐台降级会吞掉"管理员未切换长连接模式"**] → 该台仍为回调模式 ⇒ 连接失败降级为 warning；靠 D3 的**启动锚点 INFO**（`n=<ok> total=<all>`）区分"端点不可达"与"根本没切"。
- [旧 `.env` 残留扁平变量 → 长连接读不到] → 删/注释旧变量 + 文档写明；`mode=long_connection` 且注册表为空（ENABLED=true）⇒ fail-fast。

## Migration Plan

1. 代码合并 + 配置形状切换（本 change）。
2. 目标机 `.env` 写 `WECOM_BOTS`（**人工**，须**单行**，见 `deploy-runbook.md §1.4`）。
3. **企微后台（纯手工，不属于可实现任务）**：三台机器人各自改「API 模式 → 使用长连接」；**切换后原回调配置失效**。
4. 重建 app 镜像（依赖已在，但镜像需重打）+ 预热 `repo-okxha`。
5. 冒烟：@ 每台机器人 → 收到占位文案；日志出现 `bot_key=<key>`；**启动锚点 `n=3 total=3`**。

**回滚**：`WECOM_BOTS` 改回、镜像回指旧 tag（沿用 runbook §7）。

## Open Questions

- ~~`WECOM_BOTS` 的 JSON 在 compose `env_file` 下是否原样传递？~~ → **已解决（spike）**：两道解析均通过，须单行。
- `msgid` 重复：本轮**仅记日志、不去重**（占位阶段重复回复无害）；接 RAG 前需补排重（已知缺口）。
- 三台机器人的创建者是否为超管（影响 `from.userid` 明文/密文）—— 不影响本轮。
- 长连接"回答时限"口径（企微 6min? / 腾讯云 3min?）—— 接 RAG 前需实测钉死。
