# 知识图谱（understand-anything）

> 归属：代码知识图谱的**查询方式**、**新鲜度判据**、**自动更新行为**与**已知坑**。
> 图谱本身是生成物，不进本表；本文件是使用它的规则。

## 是什么 / 在哪

由 `understand-anything` 插件（`/understand` 技能）对全仓分析后产出，与源码同库提交：

| 路径 | 内容 |
|------|------|
| `.ua/knowledge-graph.json` | 图谱本体：节点（含 `summary` / `tags` / `complexity` / `filePath`）、边、10 层架构、导览 |
| `.ua/fingerprints.json` | 结构指纹基线，增量更新的对比依据 |
| `.ua/meta.json` | `gitCommitHash` / `analyzedFiles`，新鲜度判据的输入 |
| `.ua/.understandignore` | 扫描范围（等同 gitignore 语法） |
| `.ua/intermediate/` | 中间态，已 gitignore；仅保留 `scan-result.json` 供增量复用 |

当前规模：1788 节点 / 3282 边 / 10 层 / 15 步导览（419 个文件）。节点类型含 `file` / `function` / `class` / `config` / `document` / `service`；边类型含 `contains` / `imports` / `exports` / `calls` / `tested_by` / `depends_on` / `routes` / `deploys` / `documents` 等。

## 怎么查（别整包读）

`.ua/knowledge-graph.json` 有 1.6 MB（≈40 万 token），**任何情况下都不要整包读进上下文**。两种正确姿势：

**① 用技能**（首选）：`/understand-chat <自然语言问题>`；单文件/函数深挖用 `/understand-explain <路径>`；看未提交改动的波及面用 `/understand-diff`。

**② 确定性查询**（任意 agent 可做，三步）：

1. **先查新鲜度**（见下节）
2. 按关键字 grep 出节点：匹配 `"name"` / `"summary"` / `"tags"`，记下命中的 `id`
3. 用命中的 `id` 回 grep `"edges"` 取 **1-hop 邻域**（入边＝谁依赖/调用/引用它；出边＝它依赖谁），**只基于这一小片作答**

实测成本：一次邻域查询输出约 **400 字节～2 KB**，相对整图约 **1/4000**。想更省就在同一节点上多跳几次，而不是换更宽的查询。

**图谱不可替代 grep/读码的场景**：需要精确行号、需要多跳传递闭包、需要看实现细节时——图谱是**索引**，不是真相来源（源码才是）。

## 新鲜度判据：hash 不等 ≠ 过期

`project.gitCommitHash` 与 `HEAD` 不同**并不等于**图谱过期。必须比对**项目文件差异，且排除 `.ua/`**（生成物不算项目漂移）：

```bash
GRAPH_COMMIT=$(git rev-parse --verify --end-of-options "<graph.gitCommitHash>^{commit}")
git diff --name-only "$GRAPH_COMMIT" HEAD -- . | grep -v '^\.ua/'
git diff --name-only -- . ; git diff --cached --name-only -- . ; git ls-files --others --exclude-standard -- .
```

项目差异为空 ⇒ 图谱未过期，可正常作答；有输出 ⇒ 先提示"图谱可能遗漏这些改动"，建议刷新。

## 自动更新（已启用）

`autoUpdate: true` 写在 `.ua/config.json`（`/understand --auto-update` 即写此值）。驱动它的是**插件的两个钩子**，不是定时任务：

- `PostToolUse`（matcher `Bash`）：每次命令含 `git commit` 后触发增量更新
- `SessionStart`：`meta.gitCommitHash` 与 HEAD 不同时，要求 agent **不询问用户**直接增量更新

成本分级（写在钩子的提示词里）：纯删除 / 纯忽略 / 仅生成物 / 仅外观变更**不派任何 LLM agent**；只有结构变更才派 file-analyzer；架构与导览 agent 仅在计划要求时运行。关闭：把 `autoUpdate` 置 `false`（或 `/understand --no-auto-update`）。

自动更新链：`prepare-incremental.mjs` →（按需）`compute-batches.mjs --changed-files=` → file-analyzer → `merge-batch-graphs.py` → （`PARTIAL_UPDATE` 时不派 architecture/tour）→ `finalize-incremental.mjs`。**该路径不派 assemble-reviewer / graph-reviewer。**

## 已知坑

1. **收敛范围必须写进 `.ua/.understandignore`，不能只靠 CLI 参数**。`--exclude` 只对当次生效，增量更新只读该文件；否则文件集变化会让动作升级为 `FULL_UPDATE`（重建全图）。
2. **增量重析会丢失该文件的语义边，且是静默的**。根因有两层：① `incremental-symbol-baseline.json` **只携带节点/符号、不携带边**，所以被重析的 agent 无从知道该文件原有那些边，只能靠源码重新推导；② 合并用新批次**整块替换**该文件的边，而校验**只验节点/符号、不验边**（实测丢了 4 条边仍报 `Symbol validation passed`）。
   ⇒ **做法**：派 file-analyzer 做增量时，显式把该文件**现有的边逐条列出**（从 `knowledge-graph.json` 里 `grep` 该节点 id），并要求"按当前源码逐条重推、仍成立的保留"；合并后务必对比候选与已保存图谱的边集合，确认 `丢失=0`。
3. **只含生成物（`.ua/*`）的提交不推进基线**，`meta.gitCommitHash` 会落后于 HEAD，直到下一次含真实代码变更的提交才自愈。建议图谱产出与下一次真实代码变更一起提交；`--amend` 无用（amend 改哈希）。
4. **重跑 `merge-batch-graphs.py` 会回退 assemble-reviewer 的修复**（其修复是就地改写批次产物）。重跑合并就要一并重跑 assemble-reviewer。

## 看图谱（dashboard）

```bash
cd <PLUGIN_ROOT>/packages/dashboard && GRAPH_DIR=<项目根> npx vite --host 127.0.0.1
```

启动日志会打印 `Dashboard URL: http://127.0.0.1:<PORT>/?token=<TOKEN>`，**必须带 `?token=`**，否则被 token 门拦住（直接访问图谱数据会返回 403，属预期）。依赖与 core 构建已在插件安装时就位，无需再装。
