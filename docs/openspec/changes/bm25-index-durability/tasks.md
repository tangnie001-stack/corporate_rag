## 1. 观测先行（F5，独立可先行，纯增量）

- [ ] 1.1 `src/infra/search/bm25_index.py`：索引缺失时落 `[retrieval] bm25 index missing`（`info`，含 `kb_id`）；不可用（读/解析/打分/取分块任一失败）时落 `[retrieval] bm25 index unreadable`（`warning`，含 `kb_id` + 原因），在 `src/core/log_events.py` 的 `Event` 与 `log_event_specs.py` 的 `EVENT_SPECS` 登记
- [ ] 1.2 把 `infra/search/bm25_index.py` 补进 `docs/agents/logging-rules.md` 前缀主表 `[retrieval]` 的归属列表（前缀不新建）
- [ ] 1.3 单测：缺失 → `missing` 事件且级别 info；不可用 → `unreadable` 事件带原因、级别 warning；正常 → 两者都不产生
- [ ] 1.4 实跑一轮，确认事件出现在 trace 里（当前只有 `b9e74e82…` 有索引、其余 KB 无索引，正好是可对照的样本）

## 2. 格式迁移 + 写入与加载加固（F3 / F4 / F8，与第 3 节同属一个发布序列）

> ⚠ **本节代码不得单独上线**（design D1）：当前唯一存在的索引是容器可写层里的 `bm25.pkl`，而本节改为只读 `bm25.json` 且不做兼容。单独上线会让目标 KB 立即可见为"索引缺失"。

- [ ] 2.1 **先写会失败的测试**：`build_index` 落盘文件为 JSON（可被 `json.load` 读出）；两次并发调用产生的临时文件名互不相同；临时文件与目标同目录
- [ ] 2.2 `bm25_index.py:36-39` `build_index` 改为：序列化 chunks 为 JSON（`{"chunks": [{"id","content","metadata"}, ...]}`）→ 用 `tempfile.mkstemp(dir=目标目录)` 取**唯一**临时名写入 → `flush` + `os.fsync` → `os.replace`。⚠ 临时文件必须与目标同目录（跨文件系统重命名会失去原子性）；**禁止固定名**（同一 KB 的并发重建是真实路径：`document_service.py:27` `Semaphore(3)` + `:240` `create_task`）
- [ ] 2.3 文件名由 `bm25.pkl` 改为 `bm25.json`（三处引用），常量集中到 `src/config/const.py`。**不做 pkl 读取兼容**（代价与对策见 design D1/D4）
- [ ] 2.4 读取路径改为：读 JSON → 重建 `BM25Okapi` → 打分 → 构造结果；删除 `:98-116` 的 dict/ChunkData 双格式兼容分支
- [ ] 2.5 **先写会失败的测试**：① 非 JSON 内容 → `search` 返回 `[]`；② JSON 合法但缺 `chunks` 键 → 返回 `[]`；③ `chunks` 为空 → 返回 `[]`；④ **读取失败（模拟 `OSError`）→ 返回 `[]` 且文件仍在**；⑤ **打分阶段抛异常 → 返回 `[]` 且文件仍在**
- [ ] 2.6 `bm25_index.py:86-117` 把**读取 → 解析 → 重建 → 打分 → 构造结果整段**包 try/except → warning + 返回 `[]`。⚠ 只包读取步骤不够（跨版本/结构损坏落在读取之后）
- [ ] 2.7 **先写会失败的测试（F8 自愈）**：① 内容无法解析 → 文件**已被删除**；② 文件**不存在** → 不执行删除；③ 读取失败 / 打分失败 → **不删除**；④ **并发保护**：读取后、删除前替换该路径为完好文件 → **不删除**（用 `st_ino`/`st_mtime_ns` 比对实现，测试可注入中间步骤）；⑤ 删除本身失败（如无权限）→ `search` 仍返回 `[]` 且只记 warning
- [ ] 2.8 实现 F8：删除的**充要条件 = 文件存在 + 内容无法解析或不符合预期结构**；删除前重新 `stat` 比对文件身份，不一致则不删；删除失败仅记 warning、不向上抛；事件带标明"已移除"的字段
- [ ] 2.9 **新增只读校验路径**：`BM25Index` 增加一个只读探针（**读取 + 解析 + 结构校验 + 重建 `BM25Okapi`** → 返回三态：不存在 / 存在但不可用 / 存在且可用），**绝不删除、绝不写入**。⚠ ① 必须与 2.6–2.8 的自愈加载路径**分开**：`search` 现在只有一条加载入口，若校验复用它，一次"只读检查"会当场删掉损坏文件。② 探针判"可用"的范围必须**覆盖加载路径的失败面**（含重建与打分所需的全部前提），否则会出现"校验说可用、加载说不可用"的分叉 —— 校验只需多做一次重建（不写盘），成本可忽略
- [ ] 2.10 复核 `src/rag/retrieval.py:87` 的 `asyncio.gather` **不加** `return_exceptions=True`：dense 是主路，它失败应该失败；BM25 支路异常已在 2.6 收敛为"返回空"。在代码注释里写明这个边界（否则后人会"顺手"加上）
- [ ] 2.11 跑 `pytest tests/ -v` + `ruff check .` + `pyright src/`

## 3. 挂载与回填（F1 + F2，与第 2 节同一发布序列，中间不留观察窗）

- [ ] 3.1 `docker-compose.yml` 在 `chroma_persist` / `ragas` 之后加 `- ./data/bm25_index:/app/data/bm25_index`
- [ ] 3.2 ⚠ **知晓副作用**：挂载后宿主机目录（当前只有一个空 `kb/`）会遮蔽容器内既有的 `b9e74e82…` 索引 → **所有 KB 立刻变为"索引缺失"**，检索静默降级为纯 dense（**不是检索失败**）。这是预期的，但必须紧接着执行 3.3
- [ ] 3.3 `docker compose up -d --force-recreate app` 后**立刻**在容器内回填，且**必须显式给出目标 kb 集合**：
  - ⚠ **禁止用无参形式** `python -m src.cli.rebuild_bm25` —— 它会遍历全部未删除 KB（实测 **1340** 个），而 `vector_store.get_all_chunks` 内部调 `get_or_create_collection`（**不存在即创建**），会新建约 **649** 个空 collection（实测现有 691 个 collection、176 条 embedding、仅 **5** 个含分块）
  - 目标集合取 `--check`（第 4 节 4.4）的输出；该命令未就绪前可用一次性脚本按 4.2 的只读访问器产出同一集合
- [ ] 3.4 验收（脚本化，不靠人眼数字）：对每个有分块的 KB，宿主机 `data/bm25_index/{kb_id}/bm25.json` 存在，且对该 KB 调 `search` 返回非空
- [ ] 3.5 验收（F1 的核心验收）：`docker compose up -d --force-recreate app` **再重建一次**，确认 `bm25.json` 仍在
- [ ] 3.6 把"回填是一次性部署动作"与"必须显式指定 kb 集合"记进 `docs/agents/cookbook.md`

## 4. CLI 防护与清理（F7 / F6；4.1/4.2/4.3/4.6/4.7 可独立先行，4.4/4.5 依赖 2.9）

- [ ] 4.1 `src/cli/rebuild_bm25.py:49-50` 对不存在的 `kb_id` 加显式校验（报错），防**未来**误传
- [ ] 4.2 **新增不创建 collection 的只读分块计数访问器**（`VectorStore` + `ChromaClient`）：现有 `list_collections()` → `list_collection_names()`（`client.py:105-113`）**只返回名称**、丢掉了 chromadb 返回的 Collection 对象，无法在不创建的前提下得到分块数。新访问器 SHALL 直接对 `client.list_collections()` 返回的对象取 `.count()`。⚠ 反例：`eval_ragas.py:512` 现在的写法 `get_or_create_collection(kb_id).count()` 正是本变更要消灭的副作用模式
- [ ] 4.3 **先写会失败的测试**：新访问器对不存在的 kb 不创建 collection；调用前后 chromadb 的 collection 数不变
- [ ] 4.4 新增 `--check`（不写盘）：用 2.9 的只读探针判"可用"、用 4.2 的访问器判"有分块"，列出"有分块但没有可用索引"的 KB 并落 `warning` 事件；并给无参回填形式加同样的预筛，或直接拒绝执行并提示显式传 kb
- [ ] 4.5 **先写会失败的测试**：`--check` 不新建 collection、不写入/删除任何索引文件（含**损坏文件仍存在**）；对"有分块无索引"的 KB 落 warning；对"有 collection 但无分块"的 KB **不**落 warning
- [ ] 4.6 删除宿主机 `data/bm25_index/kb/`（0 字节误建目录）—— 加挂载后它会进容器，可能被当成一个 KB
- [ ] 4.7 成因核查（**不声称本项能消除成因**）：现有逻辑对不存在的 kb 会走 `KB_CHUNKS_MISSING` 跳过、**不会建目录**，宿主机那个空 `kb/` 另有来源（时间线早于 `a9a48d1`，疑为提交前手工调用 `build_index`）。结论写进 7.1，不要写成"已消除成因"

## 5. 索引陈旧可见（F10，独立可先行）

- [ ] 5.1 `document_service.py:145-150` 的失败分支补 `warning` 事件（含 `kb_id`、原因，并标明旧索引仍在服务）
- [ ] 5.2 **先写会失败的测试**：重建失败时产生该事件；重建成功时不产生
- [ ] 5.3 ⚠ **明确不修**：旧索引仍继续服务（用户仍可能拿到过期结果）。彻底修（失败即删旧索引，让该 KB 降级为空）是行为变更，不在本变更（见 proposal「明确不在本变更范围」）

## 6. prod 部署定义补齐（F9，独立可先行）

- [ ] 6.1 ⚠ 前置核实：`docker-compose.prod.yml` **尚未投入使用**（当前容器来自 `docker-compose.yml` + override，已由 `docker inspect` 标签确认）→ 此刻补挂载无既有数据需迁移。**若执行时发现 prod 已部署，先停下来确认宿主机两个目录的实际内容**
- [ ] 6.2 `docker-compose.prod.yml` 补三处数据卷：`./data/chroma_persist`、`./data/ragas`、`./data/bm25_index`（只加 bm25 会制造"索引持久化、向量未持久化"的孤儿引用）
- [ ] 6.3 写入**"prod 与 dev 不同机"前置**（compose 注释 + 部署文档）：两份 compose 的 named volumes（如 `corporate_rag_mysql_data`）与 `./data/*` 路径完全相同，同机执行会直接改写 dev 的向量与索引数据
- [ ] 6.4 **不改** `--workers 4`：它与 `CLAUDE.md`「生产单 worker」的冲突是既有问题，登记进 7.1，不在本变更处置

## 7. 收尾

- [ ] 7.1 `docs/agents/defensive-patterns.md` 登记三类可复发缺陷：① "未挂载的持久化产物随容器重建静默丢失"（含 `/mnt/d` 为 9p、`data/` 被 gitignore 两条环境事实）；② "遍历全部 KB 的管理类 CLI 会经 `get_or_create_collection` 产生空 collection 副作用"；③ "prod 部署定义长期未跑导致与规则脱节（漏挂数据卷、worker 数与规则冲突、路径与 dev 相同）"
- [ ] 7.2 归档前 `openspec validate bm25-index-durability` 通过并归档
- [ ] 7.3 通知 change `retrieval-fetch-and-dedup`：本变更已落地，其第 5 节采样可开始（其 tasks 5.0 的前置条件解除）
