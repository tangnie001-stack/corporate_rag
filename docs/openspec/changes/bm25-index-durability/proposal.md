## Why

`trace_c54ce259` 的排查暴露出一个**活了半个月无人发现**的缺陷：配置说开了混合检索、日志照打 `hybrid done`，实际只有 dense 一路在工作。修复前的实测：4 个 query 的 `bm25` 结果数全为 0；`find / -name "bm25.pkl"` 零命中。

根因是两个缺口叠加，且**失败是静默的**：

1. **该 KB 的文档入库早于修复**（`a9a48d1`，2026-09-02）→ 本就需要回填。
2. **回填产物没有持久化** → `docker-compose.yml:245-246` 只挂了 `chroma_persist` 与 `ragas`，**漏了 `bm25_index`**；2026-09-12 容器重建时，09-02 的回填成果随容器可写层一起丢弃。

静默的原因：`bm25_index.py:87-88` 在索引文件缺失时**直接返回空列表**，无日志、无事件；而 `[retrieval] hybrid done` 只记**融合后**的 `result_count`，看不到分路贡献（`retrieval.py:89-94`）。

这不是孤例风险：**索引缺失只是"质量降级"，而一旦加了挂载却不做写入/加载加固，就会升级为"检索彻底不可用"**（见下方风险链）。

本变更同时是 change `retrieval-fetch-and-dedup` 第 5 节（分数形态采样）的**前置** —— BM25 半死状态下采出的分数分布会把"混合已退化为纯 dense"的噪声当成信号。

## What Changes

- **给 `bm25_index` 加卷挂载**（`docker-compose.yml` 与 `docker-compose.prod.yml`），使索引脱离容器可写层。
- **持久化格式改为只存 chunks 的 JSON**（`bm25.json`），加载时重建 `BM25Okapi`。原格式用 pickle 同存 `bm25` 对象与 chunks，带来两个独立风险：反序列化可执行任意代码（`data/` 为 0777、容器以 root 运行）；跨代码版本时 pickle 可能加载成功而 `get_scores`/取键失败，落在"反序列化异常"之外。实测顺带收益：JSON 为 pkl 的 0.8 倍，加载+重建 5.5 ms vs `pickle.load` 58.2 ms（⚠ 仅 51 chunk 量级实测）。**不做 pkl 读取兼容**，因此格式迁移必须与挂载 + 回填同属一个发布序列（见下方 ⚠ 发布批次）。
- **索引写入尽力原子化**：`build_index` 改为"写**唯一命名的**同目录临时文件 → `fsync` → `os.replace`"。唯一名是硬要求：同一 KB 的并发重建是真实路径（`document_service.py:27` `Semaphore(3)` + `:240` `create_task`），固定名会让两个线程互相覆盖写、把半写文件替换上去。
- **加载失败降级**：读文件、解析、重建、打分、构造结果**整段**包 try/except → 记 warning 并返回空列表，而不是向上抛。只包读取步骤不够 —— 跨版本/结构损坏恰好发生在读取之后。
- **索引损坏即自愈（条件收窄）**：只在**文件存在且内容为确定性损坏**（JSON 解析失败或 schema 校验失败）时删除该文件并落事件（标明已移除）；`OSError`（瞬时 I/O、权限）与打分/下标异常**只降级不删**，否则一次瞬时故障就会销毁完好文件。删除前重新 `stat` 比对，避免删掉并发写入的新文件。
- **让"索引陈旧"也可见**：`document_service.py:145-150` 目前重建失败只 `logger.warning`、不落事件，且**旧索引继续被检索** → 用户拿到过期结果而无人知道。补一条 warning 事件。
- **回填 CLI 加 kb 校验、禁止无参全量扫描、新增 `--check`**：`--check` 列出"有分块但没有可用索引"的 KB 并落 warning —— 它既是回填的目标集合来源，也让本次事故的真实形态以 warning 出现（`search` 侧只知道 `kb_id`，分不清"KB 本来就空"与"索引丢了"）。
- **清理误建垃圾目录** `data/bm25_index/kb/`（空目录）。
- **让缺失可见**：索引缺失/不可读时落显式事件（配合 change `retrieval-fetch-and-dedup` 的 `dense_count` / `bm25_count` 分路计数）。

## Capabilities

### New Capabilities

（无 —— 检索质量与可观测性的能力归属已存在，见下方 Modified）

### Modified Capabilities

- `retrieval-quality`: 新增三条要求 ——「混合检索的分路可用性」（降级为纯 dense 且不得使整条检索失败，保护范围覆盖整段读路径）、「BM25 索引写入的尽力原子性」含**唯一临时文件名**、「损坏索引的自愈」（**只在确定性损坏时删除** + 删除前重新 `stat` 防误删并发新文件）。
- `observability-logging`: 新增两条要求 ——「BM25 索引缺失可见」（缺失 `info` / 不可读 `warning` / 移除动作可辨认）与「索引陈旧可见」（重建失败但旧索引仍在服务时须落 `warning`）。

## Impact

**代码**

- `src/infra/search/bm25_index.py` — `build_index`（JSON 序列化 + 唯一临时名 + 原子写）、`search`/加载路径（整段 try/except 降级 + 确定性损坏即删 + 删除前重新 stat）、**新增只读校验路径**（返回"不存在/存在但不可用/存在且可用"三态，不删除任何文件）
- `src/infra/db/vector_store/__init__.py` / `client.py` — **新增不创建 collection 的只读分块计数访问器**（现有 `list_collections()` 只返回名称，`client.py:112-113` 丢掉了 chromadb 返回的 Collection 对象，无法在不创建的前提下得到分块数）
- `src/services/document_service.py` — `_rebuild_kb_index` 失败分支补"索引陈旧"事件
- `src/cli/rebuild_bm25.py` — kb 存在性校验、禁止无参全量扫描、新增 `--check`
- `src/core/log_events.py` / `log_event_specs.py` — 新增"索引缺失/不可读/自愈移除/索引陈旧"事件
- `src/config/const.py` — 索引文件名常量（`bm25.json`）

**部署**

- `docker-compose.yml` — 加 `./data/bm25_index:/app/data/bm25_index`
- `docker-compose.prod.yml` — 同上，**并补齐本缺的两处数据卷** `./data/chroma_persist` 与 `./data/ragas`；同时写入"prod 与 dev 不同机"前置（两份 compose 的 named volumes 与 `./data/*` 路径完全相同，同机执行会互相污染）

**运维动作（一次性）**

- 挂载生效后回填全部**有分块**的 KB（用 `--check` 得出目标集合，显式传 kb，不用无参形式）
- 删除宿主机误建的空目录 `data/bm25_index/kb/`

**文档**

- `docs/agents/logging-rules.md` — 新事件登记 + `[retrieval]` 归属补 `infra/search/bm25_index.py`
- `docs/agents/defensive-patterns.md` — 登记三类可复发缺陷：① 未挂载的持久化产物随容器重建静默丢失（含 `/mnt/d` 为 9p、`data/` 被 gitignore 两条环境事实）；② 遍历全部 KB 的管理类 CLI 会经 `get_or_create_collection` 产生空 collection 副作用；③ prod 部署定义长期未跑导致与规则脱节（漏挂数据卷、worker 数与规则冲突、路径与 dev 相同）

**⚠ 发布批次**

各类加固**不是**全部必须成批，但**格式迁移必定成批**：

```
F3 格式迁移（只读 bm25.json、不做 pkl 兼容）
  + F1 挂载 → 遮蔽容器内唯一那份 bm25.pkl
  + F2 回填 → 写出 bm25.json
  三者必须同一发布序列，中间不留观察窗
```

理由：当前**唯一存在**的索引是容器可写层里的 `bm25.pkl`（宿主机没有）。若新代码先上线而未同时完成挂载 + 回填，新代码读不到 `bm25.json` → 该 KB 立刻降级 —— 正是本变更要消灭的状态。**这是"格式迁移"要求成批，不是"安全性"要求成批。**

- **可独立先行**：F5 观测（在现有读路径上加事件，纯增量）、F6 清理、F9 prod 补齐、F10 索引陈旧可见，以及 F7 中的 kb 校验与只读计数访问器（F7 的 `--check` 还依赖只读校验探针，该探针随 F3/F4/F8 一起改 `bm25_index.py` 时落地）
- **必须同一发布序列**：F3 → F4 → F8 → F1 → F2

**⚠ 为什么"只加挂载"的后果是降级而非硬失败（更正上一版表述）**

```
F1 挂载生效 → 宿主机空目录遮蔽容器内既有索引 → 全部 KB 变为"索引缺失"（静默降级，检索仍可用）
```

"挂载单独执行会把静默降级升级成硬失败"这一因果**不成立** —— 缺失路径仍是 `return []`。硬失败需要一个**截断的索引文件**，其成因是"非原子写 + 写入过程被中断"，与挂载无关，且**在当前每次文档入库重建的路径上就已存在**：

```
9p 上非原子写 → 崩溃留截断文件
  → 加载抛异常（当前无保护）
  → asyncio.gather 无 return_exceptions（retrieval.py:87）
  → retrieve_kb 整个失败 → ToolNode(handle_tool_errors=True) 捕获 → 错误消息回喂模型
  → 该 KB 检索不可用，且【不自愈】（要人工删文件）
```

把因果写准的实际意义：F4/F5/F8 是独立的写健壮性加固，可以先落地；F1 也不必等它们，只是必须紧跟 F2。

**⚠ 回填命令的副作用（实测）**

`python -m src.cli.rebuild_bm25`（无参）会遍历**全部未删除 KB**，而 `vector_store.get_all_chunks` 内部调 `get_or_create_collection`（**不存在即创建**）。实测本环境 `knowledge_base` 有 **1340** 行未删除、Chroma 现有 **691** 个 collection（**176** 条 embedding），其中只有 **5** 个含分块 —— 无参执行会新建约 **649 个空 collection**。回填 SHALL 显式传入目标 kb 集合，或改用不建 collection 的方式预筛。

**明确不在本变更范围**

- 检索取数口径、去重单位、候选池（另见 change `retrieval-fetch-and-dedup`）
- 分数阈值（同上）
- BM25 算法本身的调参（分词、k1/b）
- **每次检索全量重载索引的进程内缓存**（`BM25Index.search` 每次调用都读文件；本变更改 JSON 后 51 chunk 下单次成本从 58.2 ms 降到 5.5 ms，但不消除"每次重载"本身，且**千 chunk 量级未实测**）—— 已登记 `requirements_pool.md` F-15
- **消除"陈旧索引仍可读"**：F10 只让它可见；彻底修（失败即删旧索引，让该 KB 降级为空而不是继续给过期数据）是行为变更，不在本变更
- `docker-compose.prod.yml` 的 `--workers 4` 与 `CLAUDE.md`「生产单 worker」规则的既有冲突（本变更只补数据卷，不改 worker 数）
