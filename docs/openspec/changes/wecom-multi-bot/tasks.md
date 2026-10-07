> 本 change **不接 RAG**（回复仍为占位）与**不绑知识库**。配置形状变更属 **BREAKING**，需同步目标机 `.env`（见 `deploy-runbook.md §1.4`）。
> 「三台机器人在企微后台切换为长连接模式」是**纯手工运维步骤**，已记入 `design.md` 的 Migration Plan，不作为可勾选任务。

## 1. 前置验证

- [x] 1.1 **（spike 已完成）** 验证 `WECOM_BOTS` 的 JSON 能否穿过两道解析。结论：**能**——宿主 `python-dotenv` 与容器 compose `env_file` 均通过（引号保留、空格/`#`/`$` 安全）；**硬约束 = 必须单行**（多行美化让两道解析都失败）。旁证：compose 只对 `command`/`environment` 里的 `$` 插值，与 `env_file` 取值无关。
- [ ] 1.2 在 `.env.example` / `.env.template` 注释中写明"`WECOM_BOTS` 必须写在一行"

## 2. 配置层

- [ ] 2.1 新增 `src/config/wecom_bots.py`：`WeComBotConfig`（frozen dataclass：`key` / `bot_id` / `secret`，每字段带行内注释）
- [ ] 2.2 同模块 `load_wecom_bots()`：读 `os.getenv("WECOM_BOTS")`，`json.loads` 解析（非法 JSON → 抛错）
- [ ] 2.3 逐台校验：`key`/`bot_id`/`secret` 非空、`key` 匹配 `^[a-z0-9_-]+$`、`key` 唯一、`bot_id` 唯一、`key` 不为保留字 `callback`；违反即抛错
- [ ] 2.4 **错误信息脱敏**：抛错/日志只标识到下标或 `key`（如 `WECOM_BOTS[1].secret 为空`），**不得**包含 `WECOM_BOTS` 原值或任何 `secret`
- [ ] 2.5 `src/config/settings.py`：`WECOM_BOT_MODE` 默认值改 `long_connection`；**不新增行数进红线**
- [ ] 2.6 `settings.py` 移除 `WECOM_BOT_ID` / `WECOM_BOT_SECRET`；回调三字段保留并加"仅 callback 模式"注释

## 3. 编排层（`src/services/wecom_service.py`）

- [ ] 3.1 `_driver` 单例 → `_drivers: dict[str, ChannelDriver]`（**不限于长连接**）；回调驱动存**保留键 `callback`**
- [ ] 3.2 `start()`：调用 `load_wecom_bots()`（受 `WECOM_BOT_ENABLED` 门控，且置于逐台 `try/except` **之外**）；**解析只做一次**，注册表 + `aibotid→bot_key` 反查表存进 `wecom_service`
- [ ] 3.3 `start()` 逐台构造并 `await driver.start()`；单台失败只记 warning 并跳过（不置空全局、不阻塞启动）
- [ ] 3.4 `start()`：`mode=long_connection` 且注册表为空 ⇒ 抛错（按 mode 判定，不放进通用 loader）
- [ ] 3.5 `stop()`：逐台 try/except 断开（失败记 warning 继续），幂等；覆盖回调驱动
- [ ] 3.6 启动锚点日志：`[wecom] bots connected n=<ok> total=<all>`
- [ ] 3.7 `get_driver(bot_key)`：未命中抛 `RuntimeError`；`get_callback_driver()` 从保留键取并保留类型校验

## 4. 分发与日志

- [ ] 4.1 handler 依据反查表定位 `bot_key`；未知 `aibotid` 记 warning 并安全返回；回调模式下 `bot_key` 固定为 `callback`（**不**逐消息重解析）
- [ ] 4.2 业务 handler 日志带 `bot_key=<key>`
- [ ] 4.3 `long_connection.py` 错误日志带机器人标识，且该值**按日志规约编码**（token 不安全则转义）
- [ ] 4.4 更新 `docs/agents/logging-rules.md` 前缀主表：`[wecom]` 描述补上 `long_connection`

## 5. 测试

- [ ] 5.1 重写 `tests/services/test_wecom_service.py`：配置解析与校验（合法 / 非法 JSON / **跨多行** / 缺字段 / `key` 非法字符集 / `key` 重复 / `bot_id` 重复 / `key=callback` / 空数组 / `ENABLED=false` 容忍坏配置）、**错误信息不含 secret**、逐台装配、**单台失败逐台降级**、`stop` 逐台容错与幂等、`get_driver(bot_key)` 命中与未命中、锚点日志
- [ ] 5.2 分发用例：已知 `aibotid` → 对应 `bot_key`；未知 `aibotid` → warning 且不抛；回调模式 → `bot_key=callback`
- [ ] 5.3 **守卫**：新增一条静态断言测试——长连接驱动源码不出现 `.run(`
- [ ] 5.4 核对 `tests/api/test_wecom.py`（回调路由 404 行为不变）
- [ ] 5.5 全量门禁：`POSTGRES_HOST=localhost pytest tests/ -q`、`ruff check .`、`pyright src/`（不新增 error）

## 6. SDK 说明补录

- [ ] 6.1 `docs/agents/code-map.md`：channels 附近新增"官方 `aibot` SDK 用法与约束"要点（包名/版本/导入名；SDK 承担认证/心跳/重连/分发/流式/卡片/文件解密；只用 `connect`/`disconnect`/`on`/`reply_stream`；**禁止 `run()`**）
- [ ] 6.2 `docs/agents/deploy-runbook.md`：登记该 SDK 的依赖与部署影响（传递依赖 `websockets`/`aiohttp`/`pyee`/`cryptography`/`certifi` ⇒ 重建 app 镜像 + 预热 `repo-okxha`）

## 7. 文档登记与同步

- [ ] 7.1 `.env.example` / `.env.template`：扁平企微块 → `WECOM_BOTS` JSON（含"必须单行"注释与 `WECOM_BOT_MODE=long_connection`）
- [ ] 7.2 `docs/agents/glossary.md`：新增"多机器人配置（`WECOM_BOTS`）"、更新"长连接驱动"条目
- [ ] 7.3 `docs/agents/code-map.md`：结构行与"常见改动落点速查"补"多机器人"与配置入口（`src/config/wecom_bots.py`）
- [ ] 7.4 `docs/agents/cookbook.md`：长连接接入条目补"多机器人 + 逐台降级 + 目标机 `.env` + `WECOM_BOTS` 必须单行"
- [ ] 7.5 `docs/agents/api_contract.md` 与 `src/api/README.md`：核对回调端点在 `long_connection` 模式返回 404 的表述
- [ ] 7.6 `docs/agents/deploy-runbook.md §1.4`：`.env` 示例更新为含 `WECOM_BOTS`（单行）的形态

## 8. 上线与冒烟

- [ ] 8.1 产出可直接粘贴的目标机 `.env` 企微段（`WECOM_BOTS` 含三台、**单行**）
- [ ] 8.2 冒烟判据（验证项）：@ 每台机器人收到占位文案；日志出现对应 `bot_key=<key>`；启动锚点 `n=3 total=3`（若 `n<total` 说明有台未连上，需区分"端点不可达"与"管理员未切换长连接模式"）
