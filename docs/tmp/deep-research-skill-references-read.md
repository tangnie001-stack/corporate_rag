# skill 附随文件（`references/`）按需可读 — 业界对照与实现设计

> 归属：**F-12**（`docs/agents/requirements_pool.md`「skill 渐进披露（目录 + 按需读）」）的设计笔记。
> 状态：**设计，未实施**。落地须另开 change（暂名 `skill-resource-read`），并排在 `fork-tool-face-by-inheritance` **之后**。
> 已定案：**OQ-A** URI 单参（改判）、**OQ-B** 注入层不列（改判）、**OQ-C** symlink 取严、**OQ-D** 读工具进主 agent 工具面、**OQ-E** 不造每委派累计上限、**OQ-F** 整棵可读+拒读垃圾目录。唯一残留：OQ-B 的 (a)/(b)。
> ⚠ 另附 **§9 待复核**：我们此前对 WeKnora 上下文层的描述与当前代码不符，影响**上下文管理那批计划**。
> 日期：2026-09-27。来源：本地参考库源码（WeKnora / deepseek-harness）+ Firecrawl 一手检索（见文末）。

---

## 1. 问题与目标

三份外部 skill 的正文点名了附随文件，但 fork 子代理**零工具**、harness **无任何文件读取面**，导致这三处引用悬空：

```
skills/financial-statement-analyzer/SKILL.md:25,64  → references/analysis-methodology.md
skills/financial-statement-analyzer/SKILL.md:118    → references/output-template.md
skills/market-sizing-analysis/SKILL.md:65           → references/details.md
```

目标：让**模型按需**读取 skill 自己目录下的附随文件（`references/`、`assets/`、`examples/` …），
且**内容只进子代理上下文、不进主 agent 上下文**。

---

## 2. 业界对照（四家）

| | 加载/注入 SKILL.md 那一刻 | 运行期·模型按需 | 通道 |
|---|---|---|---|
| **Anthropic 官方 / Claude Code** | 不读 | ✅ 读（`bash cat`） | **文件系统 + 代码执行**；L3「None until accessed」；「bundled 内容事实上无上限」 |
| **agentskills.io 规范（tool-based agent）** | 不读 | ✅ 读 | **专用工具**；「The specific tool implementation is up to the developer」；`<skill_content>` 包裹 + 基址；**「不要暗示自动清点」** |
| **WeKnora** | 不读内容，**但返回文件树** | ✅ 读 | 专用 `read_file` + `skill://<name>/<rel>`；白名单 + 包内校验；**不依赖沙箱** |
| **deepseek-harness** | 不读、**也不列** | ✅ 读 | **通用 fs `read`**；只注入 `Base directory` 提示；「加载结果不枚举 skill 目录」 |

**共识**：三家**加载期都不把 references 塞进上下文**（"按需"是原则），分歧只在
① 通道是**专用工具**还是**通用文件工具**；② 加载期**列不列清单**。

**我们归"专用工具"那一支** —— 我们没有 bash、没有沙箱、没有通用 fs，所以 deepseek-harness 那套
（给个基址提示、让模型用通用 read 去读）**对我们无效**：模型拿着路径也读不了。

**风险提示（行业已验证）**：Anthropic 自家 Claude Code 的 issue #15757 至今未解 —— skill 读自己的
bundled 文件仍弹权限，社区留言"这让 skill 子目录毫无意义"。说明这块是**行业共同痛点**，不是我们独有的坑。

---

## 3. WeKnora 参考实现拆解

### 3.1 四层骨架

```
模型调用 read_file(path="skill://<name>/<rel>", offset, limit, max_bytes)
        │
   ┌────┴──────────────────────────────────────────────────────┐
   │ ① 寻址        read_file.go:108-121                        │
   │    Cut("skill://") → name / rel                           │
   │    段级拒 `..` / `.` / 空段；拒 `\` 与 NUL                 │
   ├───────────────────────────────────────────────────────────┤
   │ ② 白名单       read_file.go:122-131                       │
   │    name 必须在 GetAllMetadata() 内                         │
   ├───────────────────────────────────────────────────────────┤
   │ ③ 读           skills/loader.go:196-251                   │
   │    filepath.Clean → 拒 `..` 前缀 / 绝对路径                │
   │    Abs + HasPrefix(absFile, absSkillDir)                  │
   │    ★ os.OpenRoot(base).ReadFile(rel)                      │
   │      注释：资源读取不得跟随 bundle symlink 指向宿主机文件   │
   ├───────────────────────────────────────────────────────────┤
   │ ④ 渲染         read_file.go:133-179                       │
   │    rel==SKILL.md → 标题 + 描述 + 执行方式 + 正文           │
   │                    + 「## Bundled files」树                │
   │    else          → ReadSkillFile                          │
   │    >8MiB → 报错「拆分文件」（不静默截断）                   │
   │    renderFilePage：offset(1-based 行) / limit 2000         │
   │      / max_bytes ≤64KiB / next_offset / 二进制抑制          │
   └───────────────────────────────────────────────────────────┘
```

关键数值（`internal/agent/tools/workspace_reader.go:39-48`、`sandbox_write.go:43`）：

| 常量 | 值 | 作用 |
|---|---|---|
| `defaultReadSandboxMaxBytes` / `maxReadSandboxMaxBytes` | 64 KiB | 单次返回字节预算 |
| `readSandboxPageOverhead` | 512 runes | 页大小 = 输出预算 − 开销 |
| `maxSandboxFileBytes` | 8 MiB | 文件硬上限，超则报错让拆分 |
| `limit` 默认 | 2000 行 | 每次返回行数 |

### 3.2 最值得抄的三条判断

1. **分页是显式的**：超限 → 报错让你拆文件，或给 `next_offset` 续读；**从不静默丢内容**。
2. **路径安全分四道**，最后一道是 OS 原语（`os.Root`）关掉 symlink 竞态，而不是字符串检查。
3. **文件树跳过 `.venv` / `node_modules` / `__pycache__` / `.git`**
   （`internal/agent/tools/skill_resources.go:9-14`，注释：「Listing them as a flat bullet list
   would dump thousands of paths into the turn」）。

### 3.3 一条反直觉收获：与"掐中间"截断的冲突

`workspace_reader.go:250-256` 注释：上层注册表会把超长输出**掐掉中间、保留头尾**；若一页正好超限，
续读提示在尾部**会活下来**，于是它宣称"已展示 1-2000 行"，而中间一整块被**悄悄丢掉** —— 证词错误。
故他们**按输出预算反算页大小，让截断永不触发**。

这与本项目第 2 刀 change（`_truncate` 改"头尾保留 + 标注省略量"）是**同一族 bug**。
两条不冲突，但**读工具这里应取 WeKnora 的做法**：让页小于预算，而不是让页被截断。

---

## 4. deepseek-harness 的对照（我们抄不了的部分）

```xml
<skill_content name="...">
<skill_resources>
Base directory for this skill: /path/to/skills/xxx
Resolve relative paths mentioned by this skill against the base directory before using them.
Load referenced resources only as needed.
</skill_resources>
<skill_instructions>…正文…</skill_instructions>
</skill_content>
```

（`packages/skill/skill/src/index.ts:172-210`）

- 文档明写（`docs/subsystems/skills.zh.md:235`）：**「`resourceBase` 仅按需解析显式引用的脚本、
  参考资料和资产；加载结果不枚举 skill 目录。」**
- 真正读文件的是**通用 fs `read` 工具**（`packages/fs/tool-fs/src/read.ts`，
  `file_path/offset/limit`、`READ_LIMIT=2000`、maxBytes、大文件流式、窗口渲染）。
- watcher 明确忽略 bundle 内资源变更（`skill-filesystem/src/index.ts:658-684` 最深只认 `<dir>/SKILL.md`）。

**可抄的只有一件事：`<skill_content>` / `<skill_resources>` 这个包裹形状与其文案。**
其余（"给基址、让通用工具去读"）以我们**没有通用文件工具**为前提，不成立。

---

## 5. 我们的设计

### 5.1 原理三句

1. **注入**：skill 正文 + **资源前缀**（`skill://<name>/`）在 fork 启动时进子代理首条消息；
   **不注入文件清单**（L2 是注入，不是读取；清单在工具侧按需要）。
2. **按需**：附随文件的**内容**不进上下文；模型凭正文里的相对路径 + 前缀拼出地址，需要时才调工具。
3. **受控**：工具只在**某一个 skill 自己的目录**里读，四道校验 + 分页 + 上限。

### 5.2 读取流程（时序）

```
用户提问
   │
   ▼
主 agent 工具面（retrieve_kb / ask_user / search_web / delegate_task  [+ read_skill_resource]）
   │  判断需要领域专家 → delegate_task(task, skill="market-sizing-analysis")
   │  task 里带已检索材料
   ▼
fork 执行者（子上下文，工具面 = 继承执行者 − FORK_FORBIDDEN_TOOLS）
   │  首条消息 =
   │    <skill_content name="market-sizing-analysis">
   │      <skill_resources>
   │        Resources: skill://market-sizing-analysis/  （相对路径基于此前缀）
   │        正文若引用附随文件，需要时自行读取
   │      </skill_resources>
   │      <skill_instructions> …正文… </skill_instructions>
   │    </skill_content>
   │   ※ 注入层【不列】文件清单（OQ-B）；清单在工具侧读 SKILL.md 时给
   │
   │  模型判断：正文写「…lives in references/details.md」→ 拼出地址
   ▼
   模型调用 read_skill_resource(path="skill://market-sizing-analysis/references/details.md")
   │
   ├─ ① 白名单：skill 在 registry 可见名单内？
   ├─ ② 段级：拒 `..` / `.` / 空段 / `\` / NUL
   ├─ ③ 容器：`dir_fd` 逐段 `O_NOFOLLOW` 打开（拒一切 symlink；open 期关竞态）
   ├─ ④ 大小：受 max_bytes 截断为分页；文件 > hard cap → 明确报错
   └─ ⑤ 分页：offset / limit / max_bytes → 正文 + next_offset（若还有）
   │
   ▼
   内容进入【子代理】上下文（主 agent 上下文不变 ← 核心收益）
   │
   │  子代理可多次读、读别的文件、也可自行 retrieve_kb
   ▼
   子代理产出结论 → _truncate（头尾保留）→ 回传主 agent
```

### 5.3 「按需」到底省了什么

- **不调工具 = 0 token**；调了 = 该文件全文进上下文。所以「按需」不是省 token，
  是**省"用不到的文件"**：`data-sources.md`（421 行）、`examples/saas-market-sizing.md`（377 行）
  若从不被引用，就永远是 0 成本。
- 另一层收益更关键：**内容进的是子代理的上下文**，主 agent 只看到截断后的结论
  → 主上下文不被 references 污染。这是 fork 架构自带的好处。

### 5.4 落点清单

| WeKnora 元件 | 我们的落点 | 差异 / 要点 |
|---|---|---|
| `read_file` 统一工具（workspace+skill+web） | **只做 skill 那一支**：新 `src/agents/tools/skill_tools.py` | 我们没有 workspace / web 文件源 |
| `skills.Manager.GetAllMetadata()` 当白名单 | **`SkillRegistry` 现成的可见名单**（`src/agents/skills/registry.py:88-103`） | ✅ 白名单**免费** |
| `Loader.LoadSkillFile`（四道校验） | 新 `src/agents/skills/resources.py`（纯函数） | 见 §5.5 OQ-C（**已定：严**） |
| `os.OpenRoot`（open 期关 symlink 竞态） | `dir_fd` 逐段 `O_NOFOLLOW` | 实测本机 Python 3.12.3 **无 `os.Root`**；`os.open in os.supports_dir_fd == True`；`O_NOFOLLOW` 比 Go `os.Root` **更严**（拒一切 symlink） |
| **条件挂载 + 按「能力/角色/白名单」组装工具集**（`read_file.go:50,67`；`docs/agent-tools-design.md`） | **一个工具，主/子共用**；差异交给 registry 可见名单 | 见 §5.5 OQ-D（**已定：进主 agent 工具面**） |
| `path="skill://<name>/<rel>"` 寻址（`prompts.go:223` 在 L1 喂地址） | 同款 **URI 单参** | 见 §5.5 OQ-A（**已定**） |
| `formatSkillFileTree` + skip 表 | 同款；skip 表 + **拒绝目录** + 条目数上限入 `src/config/const.py` | 清单挂"读 `SKILL.md`"，**不注入**（§5.5 OQ-B） |
| `renderFilePage` | 新 `src/agents/skills/paging.py` | ⚠ **我们没有 OutputBudget**（全仓无工具结果截断）→ 页大小只能由 const 定 |
| >8MiB 报错 | 同款；阈值入 const | 不静默截断 |
| description 按 scope 拼 | 我们的 `tools.yaml` 段 + 工具 docstring | 受 `prompt-ownership.md` 与「段数最小性」约束 |
| 读 SKILL.md 时返回 指令 + **文件清单** + 执行方式 | 同款，**去掉执行方式**（我们无 shell） | 读 `SKILL.md` → "正文 + 清单"；**(a)/(b) 待选**（§5.5 OQ-B） |
| skill 名来自 tenant DB / manifest | 无需 | 我们是本地目录，`SKILLS_DIR`（`src/config/settings.py:295`）+ compose 挂载 `/app/skills` |

### 5.5 关键取舍（OQ-A ~ OQ-F 定案；OQ-B 余 (a)/(b) 待选）

**OQ-A 寻址形状 —— 已定：URI 单参 `skill://<name>/<rel>`（改判，原倾向双参）**

| | 做法 | 关键证据 |
|---|---|---|
| **WeKnora** | **单一 `path` + scheme 前缀**（`skill://` / `web://` / `/workspace/...`） | ① 合并动因（`docs/agent-tools-design.md`）：「`read_skill` 和 `read_sandbox_file` 各自维护读取入口，技能读取没有连续分页 → 合并为 `read_file(path, offset, limit, max_bytes)`，**共享分页及输出预算**；**按来源保留访问控制**」<br>② **L1 直接把地址交给模型**：`prompts.go:223` 生成 `<skill name="…" path="skill://<name>/SKILL.md">` |
| **deepseek-harness** | 两件事分开：`skill` 工具用 `name` 激活；资源用**通用 fs** 的**真实路径**读 | `renderResourceHint` 只给 `Base directory: <path>` |

→ **定案：URI 单参**。理由：注入层本来就要写基址，把它写成 `skill://<name>/` 前缀后，
模型只需**一个参数、复制粘贴**（WeKnora 的 L1 就是这么喂地址的）；双参要模型把 skill 名再写一遍。
→ 代价：自己解析 URI（~10 行）。
→ 备选：`(skill, path)` 双参 —— 可接受，但证据偏向 URI。
→ 已排除：绑定"当前 skill"的单参形态（主 agent 无"当前 skill" → 会变 fork 专用 → 与 route C 冲突）。

**OQ-B 清单 —— 已定：注入层不列；清单挂在「读 SKILL.md」上（改判，原建议"列"）**

决定性证据（WeKnora `internal/agent/prompts.go:209-227`，注释原文）：

```go
// This is a lightweight representation that only includes skill name and description
<skill name="%s" path="skill://%s/SKILL.md"><description>%s</description></skill>
//   description 截断 formatDocSummary(skill.Description, 600)
```

WeKnora 的注入层**只有 `name` + `path` + `description`，没有文件清单**；清单只在**读 SKILL.md 的返回**里
（`read_file.go:157-162` 的 `## Bundled files`）。DSH 则文档明写「加载结果**不枚举** skill 目录」。

→ **两家在「注入」这个位置都等于"不列"**。WeKnora 的"列"发生在另一个位置，而**我们没有那个位置**
→ 原先"在注入里列"的建议是**错位的**。

**WeKnora 怎么让模型知道"可以要清单"**：写进**工具描述**（`read_file.go:68`）——
> `skill://<name>/SKILL.md` loads the allowed skill's instructions, **file list** and execution guidance

→ **能力通过工具描述告知，不占常驻 token** —— 这正是规范那句「不要暗示自动清点」的正确落法。

→ **定案**：注入层不列；工具侧支持 `SKILL.md` 特殊路径 → 返回"正文 + 文件树"（照抄 `read_file.go:133-162`）；
把"读它会返回清单"写进工具描述。

⚠ **一个 WeKnora 没有、我们必须处理的问题**：我们的正文**已被注入**，所以调 `read(SKILL.md)` 会把正文
**再进一次**上下文（WeKnora 不会——它的 L2 本就是那次调用）。两选一（**待定**）：
- **(a)** 照抄 WeKnora（返回正文 + 清单），靠"模型只在想发现文件时才调"摊薄；
- **(b)** 该路径**只返回清单** + 一句"正文已在本会话给出"（省 token，但与 WeKnora 不完全一致）。

**OQ-C symlink 姿态 —— 已定：严（拒绝路径中任何 symlink）**

WeKnora **两侧都做**，并且自己写下了为什么不能只做"字面校验"：

```
① 安装侧（不可信输入）——直接拒绝
   tenant_skill_bundle.go:208-209   zip 条目 ModeSymlink → ErrSkillBundleInvalid
   tenant_source.go:372             列条目时跳过 symlink
   tenant_skill_bundle.go:200       同样的段级规则：拒绝对路径 / "../"
② 运行期（读取）——OS 原语兜底
   skills/loader.go:232-234         os.OpenRoot；注释明写
                                     "must not follow a bundle symlink into host files"
                                     "enforces containment during the open, including symlink races"
③ 目录准备侧
   sandbox/session_manager.go:343   if [ -L "$d" ]; then ... exit 1; fi
④ 理由（session_manager.go:1249-1252）
   "Validation is lexical (path.Clean plus prefix checks): a symlink under an … would grant …"
   → 即：Clean + 前缀检查这类【字面校验】对抗不了 symlink，必须在【真正 open 的那一刻】用 OS 原语
```

**这条直接否掉了"简姿态"** —— `resolve() + is_relative_to()` 正是 WeKnora 判定"不够"的 lexical 方案。

但有一个差异必须讲清，否则照抄会跑偏：

| | Go 的 `os.Root` | Python 3.12（我们） |
|---|---|---|
| 语义 | **confined**：允许**指内**的 symlink，只拒"逃出 root" | **无 `os.Root`**（实测 `hasattr(os,'Root') == False`） |
| 等价手段 | — | `dir_fd` + `O_NOFOLLOW` 逐段打开（实测 `os.open in os.supports_dir_fd == True`） |
| 严格度 | 中 | **更严**：`O_NOFOLLOW` 拒**一切** symlink，含指内的 |

→ **定案**：取**严**（逐段 `dir_fd` + `O_NOFOLLOW`）。三条理由：
1. 这是 Python 侧**唯一能关掉竞态**的现成手段（`resolve` 系都有 TOCTOU）；
2. "更严"在本场景**无损失** —— skill 包本不该有 symlink，且安装侧本来就要拒；
3. 于是**读取侧与安装侧共用同一条规则** —— 这正是 WeKnora 的做法（已从代码确认，
   不再是推测），与 `skill-external-sources` tasks 4.4 合为**一条判据两个执行点**。

**OQ-D 读工具是否进主 agent 工具面 —— 已定：进**

诚实边界：**WeKnora 没有 subagent**（全仓 grep `subagent|delegate` 只命中 chat_pipeline / file
service 等无关处）→ 它**答不了"fork 该不该拿到读工具"**。但它回答了**同构**的问题：

| 机制 | 出处 | 内容 |
|---|---|---|
| 条件挂载 | `read_file.go:50,67` | `WithSkills(manager, shell)`；skill 作用域只在 `skills != nil && IsEnabled()` 时出现 |
| 按后端能力 | `docs/agent-tools-design.md` | 「无 Shell 后端 → **技能可阅读、脚本不可执行**；按后端能力继续提供文件读写」 |
| 按角色 | 同上 | 「`write_skill_file/edit_skill_file` 与沙箱写入/编辑 —— **两组只在不同角色下注册**，不会同时占用普通会话工具列表」 |
| 按白名单 | `read_file.go:122-131` | 读哪个 skill 由**本 agent 被授权的 skill 集合**决定 |
| 默认收紧 | 同上 | 「未指定 allowed_tools 的后端默认值：**从 11 项缩为 5 项**」 |

→ WeKnora 把差异交给 **「能力 + 角色 + 白名单」**，**不交给"工具在不在"**。

→ **定案**：**进主 agent 工具面**（一个工具、两处共用；收窄靠名称名单表达）。这与 route C 的
「继承」**同构**：不声明就继承，要收窄才用名单。

→ **代价（必须认）**：进主 agent 就得正式宣告"不再是固定三件套" —— 见 §5.7 的连带改动。

**OQ-E 子代理无 token 预算 —— 已定：不造"每委派累计上限"**

| | 机制 | 关键数值（均为源码实测） |
|---|---|---|
| **WeKnora** | **独立 `internal/agent/compaction/` 包** | `Threshold = MaxContextTokens − ReserveTokens`；`DefaultReserveTokens = 16384`；`DefaultKeepRecentTokens = 20000`（且 `KeepRecent ≤ usable/4`）；`summaryBudget = reserve × 4/5`；`Reason = threshold \| overflow`；`contextSafetyTokens = 4096`；`DefaultAgentMaxIterations = 20` |
| | **cut point 规则** | `isCutPointMessage: msg.Role != "tool"` —— 注释：cut 落在 assistant 与 tool 之间会**留下孤儿 tool result，provider 直接拒** |
| | 单个 tool result 裁剪 | `toolResultMaxChars = 2000`，**只在序列化给摘要器时** |
| **deepseek-harness** | **三件套**：`token-meter` + **`compaction-tool-result-pruner`** + `compaction-basic` | pruner：超 **8192** 字符 → **头 4096 + "middle pruned" 标记 + 尾 1024**；**原文留 session log 供回放**；**不做模型调用**，可能因此免掉摘要；**只在压力/溢出触发时运行**（"a below-pressure conversation is never touched"） |
| | `compaction-basic` | 默认 `thresholdRatio = 0.8`、保留最新 **16%** 逐字、`compactionRetries = 1` |
| | **子代理** | teammate = **独立 Session**，`context: 'fresh' \| 'fork'` → **有自己的窗口、自己的压缩** |

**两家都不设"每次读取累计字节上限"** —— 都让它进来，然后在压力下裁剪/压缩。

→ **定案**：**不造"每委派累计字节上限"**。理由：两家都没有；且它与"按需"冲突 —— 模型需要读第 3 个
文件时被拦住，它只能失败。改为三层兜底：
1. **单次页大小上限**（已有先例：WeKnora 64 KiB / `limit` 2000 行；DSH 8192 字符阈值）
2. **compaction 兜底** —— ⚠ **我们还没有这一层**（属上下文管理那批计划，见 §9）
3. **短期兜底**：现有三层防失控（idle / total / turn 上限）+ `_truncate` 回传
   —— **爆的是子代理自己的窗口，主上下文不受影响**，这本身就是一道闸

**两个额外收获：**

1. **DSH 的 pruner 与我们第 2 刀同构**（头 4096 / 尾 1024 / 中间标记 ↔ 我们的头尾保留 + 省略量标注）
   → **第 2 刀有外部先例支持**，可记一笔。
2. **两家在同一点上互相矛盾**（对上下文管理 roadmap 直接有用）：

```
WeKnora：reserve 必须【绝对】—— compaction/settings.go 注释原文
  "It is an absolute reserve rather than a fraction of the window,
   because what has to fit is the response, whose size does not scale with the window."
DSH    ：用【比例】—— thresholdRatio=0.8、retain 16%
```

WeKnora 的论证更锋利，且**正好回答"窗口 256K→1M 对升级有没有影响"**：按比例做，窗口越大 reserve
越大，而"要放下的回复"根本不随窗口缩放 → 大窗口下比例法**白白浪费**空间。

**OQ-F 读取根范围 —— 已定：整棵 skill 目录；但额外拒读垃圾目录（偏离参考）**

| | 读取范围 | 清单 |
|---|---|---|
| WeKnora | `LoadSkillFile` **只做容器校验，无子目录白名单** → 整棵可读；安装侧也无子目录白名单 | 走整棵，但**跳过** `.venv` / `node_modules` / `__pycache__` / `.git` |
| deepseek-harness | `resourceBase = { kind: 'directory', path: skillDir }` → 整棵 | 不列 |
| agentskills 规范 | 「may contain **any** files and directories beyond the required SKILL.md」 | — |

→ **定案**：整棵可读（不限于 `scripts` / `references` / `assets`）。
→ **多走一步（偏离两个参考，须标注为自主决定）**：把 `.venv` / `node_modules` / `__pycache__` / `.git`
从**可读**收为**拒绝**。理由：我们**没有 shell**，这些目录零价值，拒读等于**免费收窄攻击面**
（两家不拒是因为它们要跑脚本）。

### 5.6 安全边界清单（须进 `docs/agents/defensive-patterns.md`）

1. 段级拒 `..` / `.` / 空段；拒 `\` 与 NUL
2. 容器校验用 **`dir_fd` 逐段 `O_NOFOLLOW` 打开**，不用字面校验
   （WeKnora `session_manager.go:1249-1252` 已证：`Clean` + 前缀检查这类**字面校验对抗不了 symlink**）
3. **拒路径中任何 symlink** —— 与安装侧（`skill-external-sources` tasks 4.4「归档内 symlink 条目必须拒绝」）
   是**同一条规则的两个执行点**（WeKnora 同样：`tenant_skill_bundle.go:208` 拒 + `loader.go:234` 兜底）
4. skill 名必须在 registry 可见名单内
5. 单次返回 ≤ `max_bytes`；文件 > hard cap → **明确报错**（不静默截断）
6. **`.venv` / `node_modules` / `__pycache__` / `.git`：清单跳过，且直接拒读**
   （清单跳过照 WeKnora；**拒读是我们的加严**，见 §5.5 OQ-F）
7. 清单**条目数上限**（WeKnora 安装侧有 `maxBundleZipEntries` / `maxSkillBundleFileBytes` 先例）
8. **只读**：注册时 `readonly=True`。否则 `derive_invocation_flags` 会把 skill 判成"有写能力"
   → 默认 `disable-model-invocation=True` → **skill 静默不再被自动委派**
9. **注入面**：读进来的内容进上下文 → 外部 skill 的 references 是 untrusted，须登记

### 5.7 与既有件的关系 / 不做清单

| 关系 | 说明 |
|---|---|
| **硬依赖** | `fork-tool-face-by-inheritance` 先落地，否则工具进不了子代理 |
| **收口** | F-34 ②（`tests/agents/skills/test_first_batch_skills.py:52-58` 零工具守卫）在本 change 关闭 |
| **连带** | `check_docs` 要认识新工具名（`docs/openspec/specs/skill-registry/spec.md:78-83` 会校验 skill 正文引用的工具名） |
| **收口** | `skill-external-sources` 的 Non-Goals「不做渐进披露」那句可回收 |
| **关联带（OQ-D 已定）** | 工具进主 agent 面 → 正式宣告"不再固定三件套"：改 `src/agents/tools/rag_tools.py:75` 那句注释，并同步 `delegate-task` / `tool-registry` 相关 spec 里的"固定三件套"表述 |
| **关联带（OQ-A 已定）** | 注入层给 `skill://<name>/` 前缀（`executor._render_fork_task` 的 `<skill_resources>`） |
| **不做（OQ-B 已定）** | 注入层**不列**文件清单；清单挂"读 `SKILL.md`"，该能力写进**工具描述**（不占常驻 token） |
| **不做（OQ-E 已定）** | 不造"每委派累计字节上限"；靠单次页大小 + compaction（未做）+ 现有三层防失控兜底 |
| **不做** | `shell_exec` / 安装 / sandbox / `.manifest.json` / 租户白名单 DB（WeKnora 有租户体系，我们没有） |
| **不做** | 通用文件系统面（`/workspace` 之类）——只做 skill 自己目录 |

---

## 6. 与 `fork-tool-face-by-inheritance` 的排序关系

**为什么必须先继承、后读取**：

```
若 F-12 先落地：子代理仍零工具 → 读工具只到得了主 agent
                → references 对"分析子代理"依旧不可达，F-12 的目的落空
                → 除非回去用被否掉的 allowed-tools 声明机制
若继承先落地：工具面 = 继承执行者工具面 → 新工具自动流进子代理 ✅
```

这正是对方要求「排在 F-12 之前」的原因，两句话合起来是自洽的。

**能否合并成一个 change**：建议**不合并**（对方已定 change 名与边界；两者语义面不同：
一个是"工具面继承的通则"，一个是"新增工具 + 安全面"，混在一起会让评审与回滚失真），
但**可以在同一个 worktree 里按序连续实施、分两笔提交**。

---

## 7. tasks 骨架（供 change 立档用）

```
1. src/config/const.py        — 页大小 / 行上限 / max_bytes / hard cap / 清单条目上限 / 跳过并拒读的目录表
2. src/agents/skills/resources.py — 纯函数：URI 解析 / 段级校验 / 容器校验(dir_fd+O_NOFOLLOW) / 读 / 列清单
3. src/agents/skills/paging.py    — offset / limit / next_offset / 二进制抑制
4. src/agents/tools/skill_tools.py — read_skill_resource(path="skill://<name>/<rel>") + docstring
   （"何时调用"＋"读 SKILL.md 会一并返回附随文件清单"）
5. 注册（readonly=True）+ tools.yaml 段
6. 正式宣告"不再固定三件套"：src/agents/tools/rag_tools.py:75 注释 + delegate-task / tool-registry spec
7. src/agents/skills/executor.py  — _render_fork_task 的 <skill_resources> 给 skill://<name>/ 前缀（不列清单）
8. 测试：URI 解析 / 穿越 / symlink / 白名单 / 上限 / 分页 / 拒读目录 / 集成（子代理真读到 references）
9. 文档：defensive-patterns、glossary、code-map、api_contract、check_docs 登记
10. 收口 F-34 ② 与 skill-external-sources Non-Goals
```

---

## 8. 问题清单（全部定案）

| # | 问题 | 结论 |
|---|---|---|
| **OQ-A** | 寻址形状 | **已定：URI 单参 `skill://<name>/<rel>`**（改判，原倾向双参）—— 注入层给 `skill://<name>/` 前缀，模型一个参数复制即可（§5.5） |
| **OQ-B** | 清单列 or 不列；列在哪 | **已定：注入层不列**（改判，原建议"列"）；清单挂"读 `SKILL.md`"，该能力写进**工具描述**。**(a) 返回正文+清单 / (b) 只返回清单 —— 待选**（§5.5） |
| **OQ-C** | symlink 姿态 | **已定：严** —— `dir_fd` 逐段 `O_NOFOLLOW`，拒一切 symlink；与安装侧合为一条规则（§5.5） |
| **OQ-D** | 读工具是否进主 agent 工具面 | **已定：进** —— 与 route C「继承」同构；连带改 `rag_tools.py:75` 注释 + 相关 spec（§5.5 / §5.7） |
| **OQ-E** | 子代理无 token 预算 | **已定：不造"每委派累计上限"** —— 两家都没有；改走"单次页上限 + compaction（未做）+ 现有三层防失控"（§5.5） |
| **OQ-F** | 读取根范围 | **已定：整棵 skill 目录可读**；但 `.venv`/`node_modules`/`__pycache__`/`.git` **清单跳过且拒读**（拒读为我们的加严）（§5.5） |

**唯一残留**：OQ-B 的 (a)/(b) 二选一（读 `SKILL.md` 时是否重复返回正文）。

---

## 9. 待复核：此前对 WeKnora 上下文层的描述

我们此前（可能已写进上下文管理 roadmap）对 WeKnora L2 的描述是
「`clamp(W/5, 8K, 32K)`、`trimToolResultsToBudget`、water-filling 公平分配、cut point 不落 tool 结果前」。
**以当前代码复核，只有最后一条对得上**：

| 此前描述 | 当前代码 | 判定 |
|---|---|---|
| `clamp(W/5, 8K, 32K)` | `Threshold = MaxContextTokens − ReserveTokens`（**绝对值**）；`compaction/settings.go` 注释**明确反对按窗口比例** | ❌ 不符，且**方向相反** |
| `trimToolResultsToBudget` | 全仓无此函数；裁剪只发生在 `compaction/serialize.go` 的 `toolResultMaxChars = 2000`（**仅序列化给摘要器时**） | ❌ 不符 |
| water-filling 公平分配 | 未找到 | ❌ 不符 |
| cut point 不落 tool 结果前 | `compaction/cutpoint.go`：`isCutPointMessage(msg) { return msg.Role != "tool" }` | ✅ 符合 |

**影响面**：这不是 F-12 的问题 —— 它影响的是**上下文管理那批计划**：若那批计划建立在"按窗口比例
分预算"的形状上，方向与 WeKnora 的真实做法**相反**。**建议排期前复核。**

---

## 10. 来源

**一手规范 / 官方**
- Anthropic 工程博客《Equipping agents for the real world with Agent Skills》— 三级渐进披露原始定义；"filesystem + code execution → bundled 内容事实上无上限"
- Agent Skills 官方 docs（platform.claude.com）— L1/L2/L3 表与 token 成本
- agentskills.io 规范 — `references/` 定义；"one level deep"；`allowed-tools` 标 Experimental
- agentskills.io 客户端集成指南（经 `jamie-bitflight/claude_skills` 留档）— **tool-based agent 的专用工具指引**、`<skill_content>` 包裹、"不要暗示自动清点"
- claude-code issue #15757 — skill 读自己的 bundled 文件仍弹权限（**未解**）

**本地参考库源码**
- WeKnora（读取机制）：`internal/agent/tools/read_file.go`、`internal/agent/tools/skill_resources.go`、`internal/agent/tools/workspace_reader.go`、`internal/agent/skills/loader.go`、`internal/sandbox/skill_paths.go`、`docs/agent-skills.md`
- WeKnora（两处新增证据）：`internal/application/service/tenant_skill_bundle.go`（安装侧拒 symlink）、`internal/agent/skills/tenant_source.go`（列条目跳过 symlink）、`internal/sandbox/session_manager.go:343,1249-1252`（字面校验不足的理由）、`docs/agent-tools-design.md`（工具集按能力/角色/白名单组装）
- WeKnora（上下文层，供 OQ-E 与 §9）：`internal/agent/prompts.go:209-227`（L1 注入形态：只有 name/path/description）、`internal/agent/compaction/{settings,cutpoint,serialize,compactor}.go`、`internal/agent/const.go`
- deepseek-harness：`packages/skill/skill/src/index.ts`、`packages/skill/skill-filesystem/src/index.ts`、`packages/fs/tool-fs/src/read.ts`、`docs/subsystems/skills.zh.md`
- deepseek-harness（上下文层，供 OQ-E）：`packages/compaction/compaction-tool-result-pruner/README.md`、`packages/compaction/compaction-basic/README.md`、`docs/subsystems/agent-team.zh.md`

**本项目**
- `docs/agents/requirements_pool.md` F-12 / F-34
- `docs/openspec/specs/delegate-task/spec.md:39,110-114`（工具面口径自相矛盾）
- `src/agents/skills/{loader,registry,fork_tools,executor,rendering}.py`、`src/config/{const,settings}.py`

---

## 附：Rerun Inputs

```
workflow: firecrawl-deep-research (+ 本地源码精读)
topic: skill references/ 不可读的处理方式与实现设计
depth: thorough
output: 设计笔记（本文件）
```
