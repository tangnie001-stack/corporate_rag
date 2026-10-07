## Why

上一轮企微智能机器人落地只做了**单机器人**：`settings` 是一组扁平 `WECOM_BOT_*`、`wecom_service` 是一个单例驱动，且当前用的是 **URL 回调**。现在业务线拆成三条（代码开发 / 客服 / 财务），需要**多台智能机器人**且全部改走**长连接**。当前形状无法承载：

- 单例驱动 → 只能接一台；
- 扁平凭证 → 无法表达"每机器人一组 `bot_id`/`secret`"；
- handler 无机器人维度 → 无法按业务线分流。

同时，上一轮引入的官方长连接 SDK（`wecom-aibot-python-sdk`）在文档里只有零散两句，缺成篇说明（用哪个包、怎么用、有哪些约束、依赖与部署影响），需一并补上。

## What Changes

- **配置**：扁平单机器人 → **`WECOM_BOTS` JSON 注册表**（每台 `{key, bot_id, secret}`，**必须写在一行**）；配置代码落在**新模块 `src/config/wecom_bots.py`**（避免 `settings.py` 顶破 400 行红线），由 `load_wecom_bots()` 在 `start()` 中**解析一次**并逐台校验（受 `WECOM_BOT_ENABLED` 门控、非法即 fail-fast，**错误信息不泄露 secret**）；`WECOM_BOT_MODE` 默认改为 `long_connection`。**BREAKING**（配置形状变更）
  - 单行与"两道解析都通过"已由前置 spike 实测确认（宿主 `python-dotenv` + 容器 compose `env_file`）
- **编排**：`wecom_service` 单例 `_driver` → **驱动注册表** `dict[bot_key, LongConnectionDriver]`；`start/stop` 遍历；**逐台降级**（一台连不上不影响其余、不阻塞应用），`stop()` **逐台容错且幂等**
- **分发**：handler 按入站 `msg.aibotid` → `bot_key`（`aibotid` 即 BotID）；日志带 `bot_key=<key>` 维度；回复仍为占位文案（**不接 RAG**）
- **删除死配置**：长连接专用的扁平 `WECOM_BOT_ID` / `WECOM_BOT_SECRET`；回调三字段保留并标注"仅 `callback` 模式"（**回调保留为 legacy 单机器人**，不再启用）
- **SDK 说明补录**：官方 `aibot` SDK 的"用法与约束"落 `code-map.md`、"依赖与部署影响"落 `deploy-runbook.md`
- **文档同步**：`.env.example` / `.env.template` / `glossary.md` / `cookbook.md` / `api_contract.md` / `src/api/README.md` / `logging-rules.md`

## Capabilities

### New Capabilities

- `wecom-channel`: 企业微信智能机器人接入通道的**多机器人长连接**能力——多机器人配置注册表及其启动期校验、逐台驱动装配与生命周期、按 `aibotid` 的入站分发、逐台失败降级、官方 SDK 的用法约束，以及 SDK 依赖的部署影响。

### Modified Capabilities

（无 —— 仓库此前没有 `wecom`/`channel` 相关的 OpenSpec 主规格；上一轮回调+长连接实现走的是 superpowers，未进入 OpenSpec。本 change 首次为该域建立规格。）

## Impact

- **配置**：`.env` / `.env.example` / `.env.template`（形状变更，`WECOM_BOTS` **单行**）；目标机 `.env` 需人工同步（见 `deploy-runbook.md §1.4`）
- **代码**：
  - 新增 `src/config/wecom_bots.py`（`WeComBotConfig` + `load_wecom_bots()`）
  - `src/config/settings.py`（`WECOM_BOT_MODE` 默认值、删除两个扁平长连接变量；行数不增长）
  - `src/services/wecom_service.py`（单例 → 注册表、逐台降级、`stop` 容错、`get_driver(bot_key)`）
  - `src/channels/wecom/long_connection.py`（**仅**错误日志加 `bot_id=` 维度）
- **测试**：`tests/services/test_wecom_service.py`（15 个用例多为单机器人假设，需重写）；`tests/api/test_wecom.py`（回调路由，基本不动）
- **文档**：`docs/agents/{code-map,glossary,cookbook,api_contract,logging-rules,deploy-runbook}.md`、`src/api/README.md`
- **依赖**：无新增（`wecom-aibot-python-sdk==1.0.2` 已在，传递依赖 `websockets` / `aiohttp` / `pyee` / `cryptography` / `certifi`）；但需**重建 app 镜像 + 预热云效 PyPI 代理仓 `repo-okxha`**
- **运行时**：**N 台机器人（当前 3 台）= N 条长连接**，由**同一进程**持有（单实例部署；多实例主备分配不在本 change）
- **不影响**：`/api/wecom/callback` 端点行为、数据库结构、前端取值
