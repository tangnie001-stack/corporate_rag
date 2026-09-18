# capability-catalog Specification

## Purpose
TBD - created by syncing change session-agent-and-skill-invocation. Update Purpose after archive.

## Requirements


### Requirement: 能力清单接口（由 registry 派生）

系统 SHALL 提供两个只读接口 `GET /api/skills` 与 `GET /api/agents`，**响应遵循项目统一信封**：skill 列表置于 `data.skills`、智能体列表置于 `data.agents`（与 `/auth/verify`、`/sessions/task-status` 的 `data={...}` 同构；前端按 `body.code === 'SUCCESS' && body.data` 解析）。

清单 **SHALL 由 registry 派生，不引入 catalog 文件**：

- `GET /api/skills` SHALL 返回 registry 的 `user_visible()` 结果（**服务端**剔除 `user-invocable: false`），每项含 `name` / `description`
- `GET /api/agents` SHALL 返回 registry 中全部可加载预设，每项含 `name` / `display_name` / `description`
- 列表 SHALL 随 registry 的懒重载（文件 mtime 变化）自动更新，不设独立缓存层
- 接口 SHALL 经 service 层取数（api 层不直接读文件/扫描目录）

#### Scenario: 正常返回

- **WHEN** 前端请求 `GET /api/agents`
- **THEN** 返回 200 与 `{"code":"SUCCESS","message":…,"data":{"agents":[{"name":"finance-expert","display_name":"财务专家","description":"…"}]}}`
- **AND** `GET /api/skills` 同构返回 `data.skills`

#### Scenario: 服务端隐藏不可用户调用的技能

- **WHEN** 某 skill 声明 `user-invocable: false`
- **THEN** 该 skill 不出现在 `GET /api/skills` 的 `data.skills` 中（前端无需再过滤）

#### Scenario: 新增内容文件即生效

- **WHEN** 向 `agents/` 放入新的 `<name>.md` 并更新其 mtime
- **THEN** 下一次请求 `GET /api/agents` 即包含该预设（**无需登记任何清单文件**）
- **AND** 前端选择器随之可见

> 限定：**接口与前端的"即时生效"成立**；`delegate_task` 工具 description 中的可用 skill 列表仍是**图构建期快照**（`make_delegate_task` 时生成，既有行为），新增 skill 需重启才对该清单生效。本 change 不改变该行为，如需一致需单独评估"每次调用重建 description"。

#### Scenario: 读取失败 fail-open

- **WHEN** 目录不可读或预设全部加载失败
- **THEN** 接口返回 200 与空列表，并记 warning（不返回 500、不阻塞前端）

### Requirement: 默认项由前端合成，不入清单

智能体清单 SHALL **只包含 registry 中真实存在的预设**；选择器首项「默认」SHALL 由**前端合成**（`value=""`），后端不返回伪项。

理由：`agent=""` 正好对应后端"未绑定 → 使用系统默认 prompt"的语义（见 `agent-service`），零后端分支；若后端返回伪项，会污染"预设 ＝ 真实文件"的定义并使 `agent` 校验出现特例。

#### Scenario: 清单不含默认项

- **WHEN** 前端请求 `GET /api/agents`
- **THEN** `data.agents` 中不包含名为「默认」或 `name=""` 的条目

#### Scenario: 选择默认即未绑定

- **WHEN** 用户在新对话页选择「默认」并提问
- **THEN** 请求携带 `agent=""`，后端按系统默认 prompt 生成且会话不绑定智能体

### Requirement: 展示名归属内容文件

智能体的展示名 SHALL 来自其 frontmatter 的可选字段 `display_name`（缺省 = `name`），不通过独立清单文件维护；中文展示需求一律走该字段或 `description`，**`name` 保持 ASCII slug**（见 `agent-preset`）。

#### Scenario: 缺省展示名

- **WHEN** 预设未声明 `display_name`
- **THEN** 清单中该预设的 `display_name` 等于其 `name`
