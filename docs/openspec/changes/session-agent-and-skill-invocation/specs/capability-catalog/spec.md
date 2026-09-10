## ADDED Requirements

### Requirement: 能力清单文件

系统 SHALL 用两份独立的清单文件描述可展示给用户的能力，分别放在对应内容目录内：`skills/catalog.json`（skill 清单）与 `agents/catalog.json`（智能体清单）。每项至少含 `name`；智能体项 SHALL 含 `display_name` 与 `description`，skill 项 SHALL 含 `description`。业务侧新增内容时 SHALL 同步维护对应清单。

#### Scenario: 清单结构

- **WHEN** 读取 `agents/catalog.json`
- **THEN** 返回形如 `{"agents": [{"name": "...", "display_name": "...", "description": "..."}]}`
- **AND** 读取 `skills/catalog.json` 返回 `{"skills": [{"name": "...", "description": "..."}]}`

#### Scenario: 清单与目录相互独立

- **WHEN** 只修改 `skills/` 下的内容
- **THEN** `agents/catalog.json` 不受影响（两份清单互不耦合）

### Requirement: 能力清单接口

系统 SHALL 提供两个只读接口 `GET /api/skills` 与 `GET /api/agents`，返回对应清单；接口 SHALL 经 service 层读文件（api 层不直接读文件），并 SHALL 按文件 mtime 缓存——文件更新后无需重启即生效。

#### Scenario: 正常返回

- **WHEN** 前端请求 `GET /api/agents`
- **THEN** 返回 200 与 `{"agents": [...]}`，供智能体选择器渲染

#### Scenario: mtime 缓存生效

- **WHEN** 清单文件被修改且 mtime 变化
- **THEN** 下一次请求返回新内容（不重启进程）

#### Scenario: 清单缺失或损坏

- **WHEN** 清单文件缺失或 JSON 解析失败
- **THEN** 接口返回 200 与空列表，并记 warning（不返回 500、不阻塞前端）

### Requirement: 清单与目录一致性护栏

系统启动时 SHALL 对比清单与对应内容目录，不一致 SHALL 记 warning 列出差异（"清单有但目录无"、"目录有但清单未登记"），不阻塞启动。

#### Scenario: 清单列了不存在的文件

- **WHEN** `agents/catalog.json` 列了 `ghost` 但 `agents/ghost.md` 不存在
- **THEN** 启动记 warning 指出该差异

#### Scenario: 目录有文件但未登记

- **WHEN** `agents/new-expert.md` 存在但未登记进 `agents/catalog.json`
- **THEN** 启动记 warning 指出前端将不可见

#### Scenario: 不一致不阻塞

- **WHEN** 存在上述不一致
- **THEN** 服务正常启动（护栏只 warn，fail-open）
