## Why

本项目目前的 skill **只能从本地磁盘目录加载**：`SkillLoader` 扫描 `skills/<name>/SKILL.md`（`docs/openspec/specs/skill-registry/spec.md` 的「Skill 文件结构」）。要让一个外部来源的 skill 可用，只能手工把它拷进 `skills/` 目录并重新部署 —— "**外部的 skill 能正常加载、正常用起来**"这一目标目前没有实现路径。

同域参照 WeKnora 有 **7 种来源**（ClawHub / SkillHub / github / gitlab / skills.sh / zip / 直连 SKILL.md，`client/skill.go:55`），但它的安装形态是"**起沙箱执行、以分钟计**"，因此走**异步 install-events 流** —— 而本项目**没有异步任务框架**（requirements_pool 域 4 明写：WeKnora 的"六池 worker + 死信"是"本项目流水线最薄的一环"）。

因此本变更把这件事**按"获取"与"执行"解耦**切开，只做前半段：**拉取 + 校验 + 落盘，不执行任何脚本、不起沙箱**。绝大多数 skill 就是一份方法论 markdown，没有安装步骤 —— 这一半能覆盖"外部 skill 能正常用起来"的绝大部分，且不依赖异步框架。

## What Changes

- 新增 **skill 获取入口**：给定一个外部来源，拉取并落到本地 `skills/<name>/`。本期支持的来源形态：
  - **直连 SKILL.md**（HTTP/HTTPS 单文件）
  - **zip / tar 归档**（内含一个或多个 `<name>/SKILL.md`）
  - **git 仓库目录**（浅克隆后取指定子目录）
- 拉取后 **SHALL 复用既有校验**：frontmatter 必填字段、`name` 为 ASCII slug、`name` 与目录名一致、与既有 skill 名冲突时 fail-fast（`SkillRegistry` 已有该语义）。
- 获取入口 SHALL **在落盘前完成全部校验**，校验不过则不留任何文件（原子落盘）。
- 新增只读的**获取来源清单**接口或 CLI，使"这个 skill 是从哪来的"可查（为将来"更新/卸载"留出锚点）。

## Capabilities

### New Capabilities

- `skill-acquisition`: skill 的外部来源获取 —— 来源形态（直连单文件 / 归档 / git 目录）、拉取与校验、原子落盘、失败不留残留、来源可追溯。

### Modified Capabilities

- `skill-registry`: `Skill 文件结构` 要求由"只从项目根 `skills/` 加载"扩展为"来源可以为外部，但落盘后仍统一以 `skills/<name>/SKILL.md` 为读取面"（加载器本身不改语义）。

## Impact

**代码**

- 新增 `src/agents/skills/acquisition/`（来源适配器 + 拉取 + 校验 + 原子落盘）与对应 CLI（如 `python -m src.cli.install_skill <source>`）
- `src/agents/skills/registry.py` — 复用既有的冲突 fail-fast；新增"安装后重载"触发点
- 依赖：需要 HTTP 客户端（项目已用 `httpx`）与归档解包（标准库 `zipfile` / `tarfile`）

**测试**

- 新增来源适配器单测：正常拉取 / frontmatter 缺失 / `name` 非 ASCII slug / 名冲突 / 归档含多个 skill / 归档路径穿越（`../`）/ 网络失败
- **路径穿越与解包炸弹是本期必须覆盖的安全用例**（外部来源是不可信输入）

**文档**

- `docs/agents/defensive-patterns.md` — 登记"外部内容解包"这一类风险（路径穿越 / 炸弹 / 覆盖既有 skill）
- `docs/agents/cookbook.md` — 记录"从一个外部来源安装 skill"的可复用操作流程
- `CLAUDE.md` 的 `skills/` 说明 — 补一句"外部来源安装后仍落在同一读取面"

**明确不在本变更范围**

- **不执行任何脚本、不起沙箱** —— 只做"取到本地"，不做"装好依赖"。WeKnora 的沙箱安装 + 异步 install-events 流依赖异步任务框架，属生产化那一层。
- **不做渐进披露**（system prompt 只放 skill 目录 + 按需 `read_file` 拉正文）—— 它缺的不是 prompt 段而是"文件读取工具"，已登记 `requirements_pool.md` F-12。
- 不做 skill 的版本管理与自动更新（本期只解决"能装进来"）。
- 不改 skill 的触发/执行语义（属 change `session-agent-and-skill-invocation`，已归档）。
