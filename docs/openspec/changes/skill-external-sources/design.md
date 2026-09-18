## Context

**现状**：`SkillLoader` 只扫本地 `skills/<name>/SKILL.md`（`src/agents/skills/loader.py:84` 起）。`SkillRecord.source_path`（`src/agents/skills/models.py:53`）记录的是**本地 SKILL.md 的绝对路径**，用于懒重载签名 —— 它**不是"来源"**（不含 github / zip 之类的出处）。`SkillRegistry` 已有名称冲突 fail-fast（`registry.py:50-51`）。现有 frontmatter 契约（8 字段）、`name` ASCII slug 校验、`allowed-tools` 逗号分隔解析都已落地。

**本项目已有的加载面**：`skills/<name>/SKILL.md`，由 compose volume 挂进容器 `deploy/nginx`…（`docker-compose.override.yml:6`、`docker-compose.prod.yml:213`）。

**同域参照 WeKnora 的差异（决定本期范围）**：

| 环节 | WeKnora | 本项目本期 |
|---|---|---|
| 来源 | 7 种（ClawHub / SkillHub / github / gitlab / skills.sh / zip / 直连 SKILL.md） | **3 种**（直连单文件 / 归档 / git 目录） |
| 校验 | 有 | ✅ 复用既有（frontmatter / slug / 冲突） |
| **执行安装** | **起沙箱执行、以分钟计** | ❌ **不做** |
| 状态流 | **异步 install-events** | ❌ 不做（**本项目无异步任务框架**） |

**无异步框架是硬约束**：requirements_pool 域 4 记录 WeKnora 的"六池 worker + 死信 + 双层持久化"是"本项目流水线最薄的一环"。故本期只能做**同步、无执行**的获取。

## Goals / Non-Goals

**Goals:**

- 能从 3 种外部来源把 skill **取到本地并可用**（落盘即被 `SkillRegistry` 认出）。
- 外部输入**不可信**这一前提下的安全边界（路径穿越、解包炸弹、覆盖既有 skill）。
- 失败**不留残留**（不产生半个 skill 目录）。
- "这个 skill 从哪来"可查。

**Non-Goals:**

- 不执行 skill 附带的脚本 / 不安装依赖 / 不起沙箱。
- 不做异步安装状态流。
- 不做渐进披露（缺文件读取工具，见 `requirements_pool.md` F-12）。
- 不做版本管理、自动更新、卸载（本期只解决"装进来"）。
- 不改 skill 的触发与执行语义。

## Decisions

### D1：三种来源形态，按"能拿到一个目录"归并

| 形态 | 输入 | 落盘产物 |
|---|---|---|
| 直连单文件 | 一个指向 `SKILL.md` 的 HTTP/HTTPS URL | `skills/<name>/SKILL.md`（`name` 取自 frontmatter） |
| 归档 | 一个 zip / tar 的 URL 或本地路径 | 归档内的每个 `<name>/SKILL.md` 各落一份 |
| git 目录 | `<repo-url>` + 可选 `--subdir` / `--ref` | 浅克隆后取该子目录（内含一个或多个 skill） |

**归并点**：三者最终都归约为"**一组 (name, SKILL.md 内容, 可选附属文件)**"。校验与落盘只写一份实现，来源适配器只负责"产出这组东西"。这样新增来源是加适配器，不是改落盘。

### D2：本期不执行任何脚本 —— 且这一点要写进 spec 而不是只写在文档里

**候选**：本期同步执行 / 本期不执行 / 只支持"纯 markdown skill"并在校验时拒绝带脚本的 skill。

**选"本期不执行"**，且**在校验层拒绝含可执行内容的 skill**（相对保守的一步）：

- 执行需要沙箱（隔离文件系统 / 网络 / 资源上限），而沙箱又依赖异步框架来管理生命周期 —— 两个前置都不具备。
- "取到本地、模型可用"已经覆盖绝大多数 skill 的价值（方法论 markdown）。
- 拒绝含脚本的 skill 是**可逆的保守选择**：将来有沙箱时再放开，比现在放开后收回来容易。

### D3：原子落盘 —— 先落临时目录，校验通过后再整体改名

```
拉取 → 解到 <skills>/.staging/<uuid>/       ← 与目标同文件系统
     → 逐 skill 校验（frontmatter / slug / 名冲突）
     → 全部通过 → 逐个 os.replace 到 skills/<name>/
     → 任一失败 → 删除 .staging/<uuid>/，不留残留
```

**候选**：直接写目标目录（失败留残骸）/ 临时目录 + 改名（原子）。

**选后者**。理由：外部来源失败是常态（网络中断、归档损坏、名冲突），"失败留半个 skill 目录"会污染加载器（下次启动扫到一个 frontmatter 不全的目录）。同文件系统是硬约束 —— 跨文件系统改名会退化为"复制 + 删除"，失去原子性。

**注意 `.staging` 必须被加载器忽略**（`skills/` 下以 `.` 开头的目录不参与扫描），否则半成品会被当成 skill。

### D4：校验复用既有实现，不新写一套

已有：frontmatter 必填与类型、`name` ASCII slug（`^[A-Za-z0-9][A-Za-z0-9_-]*$`）、`context` 取值、`allowed-tools` 解析、名冲突 fail-fast。

**做法**：落盘前用 `SkillLoader` 的解析函数对**候选内容**做一次"预加载"，解析失败即拒绝。这样"外部来的 skill"与"本地手放的 skill"受同一套契约约束（一处事实一个 owner）。

### D5：来源可追溯 —— 记录来源元数据但不进 SKILL.md

**候选**：写进 SKILL.md frontmatter（污染内容文件，且会被重新安装覆盖）/ 单独一个来源记账文件（如 `skills/<name>/.source.json`）/ 不记录。

**选"单独记账文件"**。理由：SKILL.md 是**内容**，来源是**元数据**；混在一起会让"作者写的内容"与"安装器写的记录"争同一块地。记账文件也让将来的卸载/更新有锚点。

### D6：安全边界必须进 spec（外部输入是不可信的）

三项必须显式断言，各有对应测试：

1. **路径穿越**：归档条目名含 `../` 或绝对路径 → 拒绝整个归档。
2. **解包炸弹**：解包后总字节数 / 条目数超上限 → 拒绝（上限走 `src/config/`）。
3. **不覆盖既有 skill**：目标名已存在 → fail-fast（复用 `SkillRegistry` 语义），不做静默覆盖。

## Risks / Trade-offs

- **[执行缺失被误解为"skill 装不全"]** → 在 CLI 输出与文档中明确"本期只取不装"；对含脚本的 skill 报明确原因（而非静默忽略）。
- **[向后不兼容的收紧]** → 校验层拒绝含脚本的 skill，会让"本地手工放的带脚本 skill"也走不通。**缓解**：收紧只作用于**安装路径**，不改 `SkillLoader` 对本地目录的既有行为（避免影响存量）。
- **[`.staging` 残留]** → 进程崩溃会留下 `.staging/<uuid>/`。缓解：启动时清理超过 N 小时的 staging 目录（一条维护动作）。
- **[归档来源的信任边界]** → 即使不解包执行，**内容仍会进模型上下文**（skill 正文会作为隐藏消息注入）。这意味着外部 skill 是一个 **prompt 注入面**。缓解：本期不解决，但在 `docs/agents/defensive-patterns.md` 登记该风险类别（"外部内容既不解包执行、也会进上下文"），并要求安装是**显式人工动作**而非自动。
- **[git 目录来源需要 git 二进制]** → 容器内是否有 git 未验证。若没有，退化为"只支持 zip"（归档可用 `httpx` + `zipfile` 覆盖同样场景）。

## Migration Plan

1. 来源适配器 + 归约层 + 校验预加载（含全部安全用例的单测）。
2. 原子落盘（含 `.staging` 清理）。
3. CLI 入口 + 来源记账文件。
4. `skill-registry` spec 的 `Skill 文件结构` 要求补"来源可外部、读取面不变"。
5. 文档登记（defensive-patterns / cookbook / CLAUDE.md 一句话）。

**回滚**：全部为新增路径；卸载即删除 acquisition 包与 CLI，既有加载面不受影响。已安装的 skill 留在 `skills/`，与手工放置的无法区分（记账文件可辅助识别）。

## Open Questions

1. **容器内是否可用 git** → 决定 git 目录来源是否落地，还是本期只做"直连单文件 + 归档"。需实测。
2. **来源形态是否需要"skill 市场"式的列表发现**（WeKnora 的 ClawHub / SkillHub）→ 本期不做，但归属哪一层未定（可能属产品侧而非 harness）。
3. **是否允许非管理员安装** → 外部 skill 进上下文 = 注入面。本期默认"显式人工动作"，是否需要权限门（复用将来的审批闸门）待定。
4. **卸载与更新** → 本期只装不卸；记账文件已为它留锚点，但语义（更新时如何处理用户本地修改）未定。
