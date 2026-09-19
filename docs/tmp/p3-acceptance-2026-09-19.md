# P3 词法检索验收（BM25 → PostgreSQL 全文检索）

- HEAD：`8cd8f45`（`docs(p3): 回填修复轮 1 的 commit hash`；本波提交前的实际 HEAD，本文件此后含「修复轮 1」「修复轮 2」两轮补记）
- 回滚锚点：`2afa8ab1d10e051aee528c18050e990819f0de2a`（Task 0 记录，`git revert` / `git reset` 目标）
- 测试：`1058 passed, 14 skipped, 31 warnings in 103.65s (0:01:43)`
- ruff：`All checks passed!`（exit 0）；pyright：`0 errors, 0 warnings, 0 informations`（exit 0）
- 验收执行日期：2026-09-19（容器时钟 2026-09-20 CST，日志文件 `app_2026-09-20.log`）

---

## ① 门禁三连（完整输出）

### `POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/ -q`（尾部）

```
-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
1058 passed, 14 skipped, 31 warnings in 103.65s (0:01:43)
```

### `ruff check .`

```
All checks passed!
```

（`ruff_exit=0`）

### `pyright src/`

```
0 errors, 0 warnings, 0 informations
WARNING: there is a new pyright version available (v1.1.411 -> v1.1.414).
```

（`pyright_exit=0`；无 error，满足「不新增 error」判据）

---

## ② 重写脚本可执行检查（DoD D4）

```
$ POSTGRES_HOST=localhost .venv/bin/python scripts/rewrite_content_seg.py --check
total=176 stale=0
exit=0

$ POSTGRES_HOST=localhost .venv/bin/python scripts/rewrite_content_seg.py --apply
rewritten=0
exit=0
```

- `--check`：`total=176 stale=0`，退出码 **0** —— 存量 176 行 `content_seg` 全部为 jieba 分词输出，无过期行。
- `--apply`：`rewritten=0` —— **幂等**（无可重写的过期行）。
- 清理 E2E 污染**之后**复跑 `--check`：仍为 `total=176 stale=0`，退出码 0。

---

## ③ DoD 逐条结论

| # | 判据 | 结论 | 证据 |
|---|---|---|---|
| D1 | 分词单一入口 | ✅ 达成 | `src/infra/search/tokenizer.py` 为唯一 jieba 入口；`tests/infra/search/test_tokenizer.py` + `tests/config/test_no_bm25_leftovers.py` 全绿（全量 1058 passed） |
| D2 | 查询安全化与兜底 | ✅ 达成 | `src/infra/db/lexical_query.py`（`LexicalQuery` 5 字段含 `is_blank`、`build_lexical_query`、`escape_like`）；`tests/infra/db/test_lexical_query.py` 15 passed；H3 非法字符剔除 + `is_blank` 短路 + 子串兜底均有覆盖 |
| D3 | 词法路读 PG | ✅ 达成 | `ChunkRepo.search_lexical`（`tsv @@ to_tsquery` + `ts_rank`，词元全滤空时 `content LIKE`）经 `PgVectorStore.lexical_search` 暴露；`tests/infra/db/test_chunk_repo_lexical.py` 全绿；反向证伪实测命中（见 ⑤） |
| D4 | 存量已全量重写 | ✅ 达成 | `--check` 退出码 **0**、`stale=0`、`total=176`；`--apply` 幂等 `rewritten=0`（见 ②） |
| D5 | 探针完成横向比较 | ✅ 达成 | `docs/tmp/p3-lexical-probe-2026-09-19.md`（分组 A/B/C + 显式失效用例 + 限制声明齐备）；五条结论原样收录于 ⑨ |
| D6 | 融合迁移完成 | ✅ 达成 | `src/rag/fusion.py`（纯函数 `rrf_fusion`，无 DB 依赖）；`tests/rag/test_fusion.py` 全绿；`RRF_K` / `RRF_TOP_N` 进 `settings` |
| D7 | 两路同源并发 + 贡献可见 | ✅ 达成 | E2E 实测 `[retrieval] hybrid done … dense_count=1 sparse_count=1 result_count=1`（`sparse_count > 0`，见 ④）；`asyncio.gather` 两路 + `rrf_fusion`；`tests/rag/test_fusion.py::test_hybrid_runs_both_paths_concurrently_on_same_store` 以 `asyncio.Barrier(2)` 证伪并发 |
| D8 | 无独立词法索引组件 | ✅ 达成 | `src/infra/search/bm25_index.py` 不存在（`ls` 报 No such file）；`tests/config/test_no_bm25_leftovers.py` 全绿（守卫含 `bm25_index` / `BM25Index` / `BM25_INDEX_DIR` / `rank_bm25`） |
| D9 | 门禁全绿 + E2E | ✅ 达成 | 门禁三连全过（见 ①）+ 真实 E2E 六条观测通过（见 ④） |

**判定：DoD D1–D9 全部达成。**

---

## ④ E2E 观测记录（六条，不可省）

真实冒烟：登录 → 建库 → 上传 → ready → 提问 → citations。新建 KB `b987747dd83547949b1c4e8af1ebbb50`、文档 `0e0aeeed-abaf-4470-b097-dc193cb21661`（内容含中文财务术语）。

> 鉴权说明：`/api/kbs*` 走 **Cookie `token`**（`src/middleware/auth.py:31`），**不是** `Authorization: Bearer`；Bearer 形式会得到 `{"code":"AUTH_REQUIRED"}`。E2E 用 cookie jar（`curl -c/-b`）完成。

### 观测 1：登录

```
POST /api/auth/login  body {"account":"admin","password":"***"}
→ {"code":"SUCCESS","message":"操作成功","data":{"token":"84f640f7…（64 hex，已脱敏）","user_id":"3f0375d6-43d6-400d-881f-343daadb7737"}}
Cookie: token=84f640f7…
```

### 观测 2：建库

```
POST /api/kbs  body {"name":"p3-smoke","description":"P3 E2E"}
→ {"code":"SUCCESS","message":"操作成功","data":{"id":"b987747dd83547949b1c4e8af1ebbb50","created":true}}
```

### 观测 3：上传

```
POST /api/kbs/documents/upload  multipart file=@p3_smoke.txt, kb_id=b987747dd83547949b1c4e8af1ebbb50
→ {"code":"SUCCESS","message":"操作成功","data":{"doc_id":"0e0aeeed-abaf-4470-b097-dc193cb21661","status":"processing","filename":"p3_smoke.txt","dedup":false}}
```

### 观测 4：ready

```
POST /api/kbs/documents/status  body {"kb_id":"b987747d…","doc_id":"0e0aeeed…"}
→ {"code":"SUCCESS","message":"操作成功","data":{"status":"ready","chunk_count":1,"progress":100,"error":"","processing_state":"completed","processing_progress":100,"processing_message":"处理完成，共 1 个分块"}}
```

文档入库后的 `chunks.content_seg`（写入侧 jieba 输出，直接查库）：

```
公司 资产负债率 上升 研发 费用 增加 净利润 同比 下降 报告 期内 公司 资产负债率 65.3% 上年 同期 上升 4.1 百分点 主要 系有息 负债 规模 扩大 所致 研发 费用 同比 增长 18.0% 公司 持续 加大 核心技术 投入 行业 景气 下行 影响 净利润 同比 下降 12.5% 毛利率 有所 收窄
```

### 观测 5：提问与答案

`POST /api/chat/stream` body `{"session_id":"526f28a2-…","kb_id":"b987747d…","query":"资产负债率"}`

- 检索入参（SSE `status` 的 `detail`）：`query=资产负债率 top_k=10`
- 答案正文（token 拼接）：引用知识库内容，含「资产负债率为 65.3%，较上年同期上升 4.1 个百分点」「有息负债规模扩大」等上传文档原句。

### 观测 6：citations 与 `hybrid done` 原始日志行

`event: citation`：

```json
{"source": "p3_smoke.txt", "page": 1, "snippet": "公司资产负债率上升，研发费用增加，净利润同比下降。\n\n报告期内，公司资产负债率为 65.3%，较上年同期上升 4.1 个百分点，主要系有息负债规模扩大所致。研发费用同比增长 18.0%，公司持续加大核心技术投入。受行业景气度下行影响，净利润同比下降 12.5%，毛利率亦有所收窄。", "score": 0.6319336963318194, "highlighted_snippet": null, "index": 1, "kind": "kb", "tier": 0}
```

`source=p3_smoke.txt`、`page=1` 正确，`citations` 非空。

检索日志原文（容器内 `/data/logs/app_2026-09-20.log`；`docker compose logs` 不落盘，见 ⑪ concern）：

```
2026-09-20 03:16:24.081 | INFO    | trace_355019fd-699d-4f5a-8a0f-22d9b798c092 | 526f28a2-0c83-41c1-82fd-6224b7ae70ca | src.core.logging:log_event:213 - [retrieval] hybrid done kb_id=b987747dd83547949b1c4e8af1ebbb50 query_len=5 dense_count=1 sparse_count=1 result_count=1
```

- **`dense_count=1`**
- **`sparse_count=1`**（**> 0，D7 判据成立**）
- `result_count=1`

#### 关于首句自然语言查询的 `sparse_count=0`（如实记录，非 D7 失败）

同一 E2E 的第一句查询 `query="资产负债率有什么变化"` 时，agent 把检索入参改写为 `query=资产负债率 变化 趋势`，对应日志：

```
2026-09-20 03:13:42.953 | INFO    | trace_c7182b58-758c-4e1a-b762-885080fe8bdd | a610dfbf-0d50-44b7-be1c-5e0c4e4e49b8 | src.core.logging:log_event:213 - [retrieval] hybrid done kb_id=b987747dd83547949b1c4e8af1ebbb50 query_len=11 dense_count=1 sparse_count=0 result_count=1
```

根因（已排查）：生产查询构造原为 **前缀 AND**（`lexical_query.py:29 _JOINER = " & "`），`资产负债率 变化 趋势` 的词元为 `资产负债率` / `变化` / `趋势`；语料 `content_seg` 只含 `资产负债率`，不含 `变化`、`趋势` → AND 谓词为空 → `sparse_count=0`。**该说明已被修复轮 1 推翻并更新**：AND 使词法路对典型多词自然查询贡献为零，连接符已翻转为前缀 OR（见文末「修复轮 1」）。翻转后同一类自然查询 `sparse_count > 0`（见下）。

#### 修复轮 1 复测：自然语言查询的 `sparse_count>0`（D7 的更强证据）

连接符翻转为前缀 OR 后，新建 KB `99e491625c314a8fb78348c756ab311a`、文档 `7eaa289e-5e7d-4e56-a059-10a7f35a8f83`（`p3_fix1_smoke.txt`，同含中文财务术语），用**自然的多词问题** `query="资产负债率有什么变化"` 复测。检索入参为 `query=资产负债率 变化`（SSE `status.detail`），原始日志行：

```
2026-09-20 03:24:46.864 | INFO    | trace_791db44b-0c2e-43ca-8fbe-7de5360ef1c5 | 2e530932-d5be-43cc-8b5b-cc4845d249be | src.core.logging:log_event:213 - [retrieval] hybrid done kb_id=99e491625c314a8fb78348c756ab311a query_len=8 dense_count=1 sparse_count=1 result_count=1
```

- **`sparse_count=1`（> 0）** —— AND 时代同类多词查询为 0，翻转后自然查询亦成立。
- `citations` 非空：`{"source":"p3_fix1_smoke.txt","page":1,...,"score":0.4959686089383502,"tier":0}`；答案正确引用上传内容（65.3% / +4.1pp / 研发费用 +18.0% / 净利润 -12.5%）。

同一语料上 AND 与 OR 的受控对照（同一查询、只换连接符）：

```
query='资产负债率 变化'      terms=('资产负债率','变化')
  OR  (翻转后) tsquery='资产负债率:* | 变化:*'      sparse_count=1
  AND (翻转前) tsquery='资产负债率:* & 变化:*'      sparse_count=0
query='资产负债率 变化 趋势'  terms=('资产负债率','变化','趋势')
  OR  (翻转后) tsquery='资产负债率:* | 变化:* | 趋势:*'      sparse_count=1
  AND (翻转前) tsquery='资产负债率:* & 变化:* & 趋势:*'      sparse_count=0
```

即：**翻转前**，词法路对典型多词自然查询 `sparse_count=0`、贡献为零，「混合检索」退化为纯 dense；**翻转后**，同一查询 `sparse_count=1`，词法路真实贡献可见。D7 不再仅由精心挑选的单概念查询（`资产负债率`）成立。

---

## ⑤ 反向证伪（词法路独立贡献，非被 dense 兜住）

`lexical_search` 独立调用（`POSTGRES_HOST=localhost`，kb = E2E 库）：

```
lexical hits: [('0e0aeeed-abaf-4470-b097-dc193cb21661:0', 0.076)]
substring fallback: []
```

- `资产负债率` → **命中该文档分块，`lexical_score=0.076 > 0`**；debug 行 `[PG] method=lexical_search … rows=1 | substring=False`。
- `涨了吗` → 分词后词元全部为单字被滤除（`use_substring=True`），**走原文子串兜底、不抛错**；debug 行 `rows=0 | substring=True`，返回空列表（语料不含该串，符合预期）。

结论：词法路确实在独立取数并产出非零得分，且兜底路径安全（不抛错）。

---

## ⑥ 清理后的计数与工作区状态

清理前（含本轮 E2E）：

```
91|103|177|1      （knowledge_base | document | chunks | users）
```

删除本轮 E2E 产生的行（1 KB + 1 document + 1 chunk + 4 conversation_history，事务内按 FK 顺序）：

```
BEGIN; DELETE 1 (chunks); DELETE 1 (document); DELETE 4 (conversation_history); DELETE 1 (knowledge_base); COMMIT;
```

清理后：

```
90|102|176|1      （knowledge_base | document | chunks | users）
```

- **`chunks = 176`（语料已恢复）**，且 176 块全部归属 `user_id = p2-migration` 的 5 个知识库（`SELECT k.user_id, count(c.*) … GROUP BY k.user_id` → `p2-migration -> 176`）。
- `users = 1`（登录自动注册账号，未触碰）。

`git status --short`：

```
（空，工作区干净）
```

### ⚠ 与 brief 预期 `5|0|176|1` 的差异（如实报告，不得粉饰）

brief Step 5 预期收尾计数为 `5|0|176|1`，实测为 `90|102|176|1`。差异根因**已定位**，且**非本轮 E2E 造成**，故按边界规定**未删除**：

- 分块数（核心语料指标）已恢复为 **176**，用户数为 **1**，与预期一致。
- `knowledge_base` 多出的 85 行、`document` 多出的 101 行是**既有测试污染**：`tests/infra/db/test_db.py` 直接对真实 PG（`POSTGRES_HOST=localhost`）写入 `test-kb-*` / `test-doc-*` 行（`user_id=test-user`，无分块），**无 teardown 清理**。按 `created_at` 分批可见每次 `pytest` 运行新增约 5 KB / 6 document（本轮门禁 Step 1 的 pytest 亦新增了一批，时间戳 19:09）。
- P2 报告 `docs/tmp/p2-dense-equivalence-2026-09-19.md:81` 早已登记同类现象（「knowledge_base 中有 5 行无分块的 `test-user` 残留」），本次累积量更大。
- 边界明令「`DELETE`/`TRUNCATE` 任何非本轮 E2E 产生的行」→ 未删除这些测试残留行。**这是测试卫生缺陷，登记进 ⑩ 残留清单。**

---

## ⑦ 回滚依据核查

- `data/bm25_index`：**存在**（目录内含 `kb/`；P3 刻意保留，词法路回滚依据，随 F-19/F-28 在 P4 清理）。
- `data/chroma_persist`：**存在**（P3 未触碰；dense 搬迁与等价性验收的唯一语料来源 + 回滚依据）。
- MySQL 数据卷 `corporate_rag_mysql_data`：**未触碰**（`docker volume ls` 可见该卷；`corporate-rag-mysql` 容器仍 `Up 10 hours (healthy)`，为 P1 退役后遗留容器，本轮未作任何删除/清空动作）。
- 全程未执行：`docker compose down -v`、`docker volume prune`、`rm -rf data/chroma_persist`、`rm -rf data/bm25_index`、删 `users` 行。

---

## ⑧ 本阶段残留清单

| # | 残留项 | 归属 |
|---|---|---|
| R1 | `rank_bm25` 依赖未删（`pyproject.toml` 仍在；`scripts/lexical_probe.py` 分组 A 仍用 `BM25Okapi` 作基线）；`data/bm25_index` 目录保留 | 需求池 **F-28** → P4（随 F-19 依赖与卷清理） |
| R2 | `chromadb` 依赖、`data/chroma_persist`、`deploy/chroma/` 等 Chroma 残留 | 需求池 **F-19** → P4 |
| R3 | `multi-query-retrieval` 的在效规格/ delta 正文引用已不存在的 `retrieve_node` / `rewritten_queries`（`rrf_fusion_multi` 无生产调用方） | 需求池 **F-29** → 随 `retrieval-fetch-and-dedup` 重定基处理 |
| R4 | 词法路质量的**判据缺口**：探针为集合成员口径，`k ≈ 池规模` 下**测不了排序/分词质量**，替换的收益/损失未被建立 | 需求池 **F-30** → k ≪ 池的新判据或端到端 RAGAS |
| R5 | 端到端答案质量（RAGAS）不在本次验收内 | 需求池 **F-30** 关联；语料到位后的独立评估活动 |
| R6 | **测试污染**：`tests/infra/db/test_db.py` 直连真实 PG 写入 `test-user` KB / 空 `user_id` document 且**无 teardown**，每次 pytest 累积约 5 KB / 6 doc（本轮已致 `knowledge_base` 达 90 行）。修复轮 1 已用仓库复位/搬迁路径把 dev 库恢复为 `5|0|176|1` | **需求池 F-31**（修复轮 1 登记）；归属测试基础设施（给真实 PG 测试补 fixture teardown 或统一走复位） |
| R7 | `alembic/env.py` 的 `compare_server_default=True` | 需求池 **F-22** → 独立变更 |
| R8 | `src/infra/db/mysql_db/` 包改名（内容已全为 PG repo） | 需求池 **F-18** → 独立变更 |
| R9 | `src/api/documents.py:246` 越层与双删除路径收编 | 需求池 **F-23 / F-25** → P4 同事务改造 |
| R10 | `tokenizer.py` 可能保留 jieba 切出的 `\r\n`（len=2 的单 token）写入 `content_seg`；`to_tsvector('simple')` 不为其产生 lexeme，**无检索后果** | Task 1 deferred minor（终审可 triage） |
| R11 | `glossary.md` 新增词条用 `###` 标题，与同文件既有 `- **term**：` 列表项格式不一致 | Task 10 deferred minor |

---

## ⑨ 探针五条结论（原样收录，不得改写）

来源：`docs/tmp/p3-lexical-probe-2026-09-19.md`。

> ⚠ **本节判据已被推翻**：第 4 条的「`prefix-OR` 恰高 10.0pp、未达翻转阈值，故维持 `" & "`」是**探针当时的结论**；后续发现判据只看了分组 B 的 +10.0pp，而翻转的**决定性依据在服务路径实测** —— 前缀 AND 下多词自然查询（如「资产负债率 变化 趋势」）整条谓词为空、`sparse_count=0`，词法路零贡献；翻转为前缀 OR 后同一查询 `sparse_count=1`（证据见「修复轮 1」§③）。**连接符当前落地为 `_JOINER = " | "`（前缀 OR）**（`src/infra/db/lexical_query.py`）。第 4 条正文原样保留探针当时的判据（「超过 10 个百分点」vs 实测 10.0pp），作为决策史的一部分。

1. 分组 A1（唯一同口径对比）：`char-bm25` 1.0000 vs `jieba-bm25` 0.9667，差 **1 个词项 = 3.33pp**，方向与"jieba 更好"**相反**；
2. 该差值**可归因于分词造成的排序差异**，但命中判据是"原始正文是否包含"、**不含排序质量** ⇒ **不能读作质量差**，"替换有收益/无收益"**均未被本探针建立**；
3. 分组 C（`ts_rank` vs `ts_rank_cd`）零差是**结构必然**（过滤臂候选集相同且 ≤ k），该口径**测不了排序质量**；
4. 分组 B：`prefix-AND` 0.5000 > `plainto-AND` 0.4667（**印证 H2 前缀通配修复方向正确**）；`prefix-OR` 0.6000 恰高 10.0pp，未达"超过 10 个百分点"的翻转阈值，故维持 `" & "`；`substring` 1.0000 是**定义性**（恒真），非策略对比；〔**本结论已被推翻（见本节顶部注）**：+10.0pp 是 OR 作为超集谓词的机械后果、不构成质量证据；真正依据是服务路径 `sparse_count` 由 0→1 的实测。**当前落地为前缀 OR（`" | "`）**。〕
5. 端到端答案质量（RAGAS）**不在本次验收内**，已登记为需求池 F-30（判据缺口）+ 语料到位后的独立评估。

---

## ⑩ 验收结论

- **DoD D1–D9 全部达成**（逐条见 ③）。
- 门禁三连全绿；真实 E2E 六条观测通过；`hybrid done` 实测 `dense_count=1 / sparse_count=1`（构造了含该中文词项的查询，D7 成立）。
- 反向证伪成立：词法路独立产出非零得分，兜底路径不抛错。
- 语料已恢复到 **176 分块**；工作区干净。
- 已知残留与既有污染见 ⑥ / ⑧，均**不属于 P3 的 DoD 范围或按边界不得处理**。

---

## 修复轮 1

> 修复轮 1/5：收口 Task 11（`b31fee3`）暴露的 F1（查询连接符 AND→OR，控制器裁决 R11-a）、F2（dev 库恢复）、F3（自然语言查询 E2E 复测）、F4（登记 F-31）。

### ① 改动清单（文件:行）

| 文件:行 | 改动 | 关键原文（改后） |
|---|---|---|
| `src/infra/db/lexical_query.py:9`（模块 docstring） | `&` → `|` | `` `词元:*`（前缀通配…），以 ` | ` 连接。`` |
| `src/infra/db/lexical_query.py:28-32`（`_JOINER`） | `" & "` → `" | "`，注释改写为「召回取向」并陈述理由 | `# 词元之间的连接符：前缀 OR。取**召回取向** —— AND 只要有一个补词不在库里\n# 就让整条查询返回 0（实测自然语言查询被改写为「资产负债率 变化 趋势」时\n# `变化`/`趋势` 不在语料，sparse_count=0，词法路贡献为零）；精度由下游\n# RRF / 去重 / rerank 承担，不靠本层收紧谓词。\n_JOINER = " | "` |
| `src/infra/db/lexical_query.py:39`（`LexicalQuery.tsquery` docstring） | `&` → `|` | `` `词元:*` 以 ` | ` 连接；词元为空时为空串…`` |
| `tests/infra/db/test_lexical_query.py:15` | 断言 `" & ".join` → `" | ".join` | `assert plan.tsquery == " | ".join(f"{t}:*" for t in plan.terms)` |
| `tests/infra/db/test_lexical_query.py:72` | 注释 ` & ` → ` | ` | `# 词元之间才允许出现 ` | `，单看词元部分不得含任何操作符` |
| `docs/agents/api_contract.md:846`（§4.5） | ` & ` → ` | ` + 补前缀 OR 语义说明 | `…再拼成 `词元:*` 并以 ` | ` 连接（前缀 OR：任一词元命中即召回，精度由下游 RRF/rerank 承担）…` |
| `docs/tmp/p3-lexical-probe-2026-09-19.md` §选型结论（约 87-92 行） | 选型由 prefix-AND 改为 **prefix-OR**，依据改写为服务路径实测；三张表测量数字未动 | `- 查询构造：**prefix-OR**（分组 B 命中数/总数：prefix-AND = 15/30 / prefix-OR = 18/30 …）`；`- **连接符翻转（改选 prefix-OR）**：依据**不是**分组 B 的 +10.0 个百分点…真正依据是**服务路径实测**…` |
| `docs/agents/requirements_pool.md`（F-30 后） | 新增 F-31（6 列，与表头一致） | 见 ② |
| `docs/tmp/p3-acceptance-2026-09-19.md`（§④、§⑧、本「修复轮 1」） | 补自然语言查询 E2E 观测；R6 关联 F-31 | 本文件 |

- `docs/agents/glossary.md` 的「词法检索」「分词口径」两条**未提连接符/查询构造**，无需改动。
- ⚠ 边界外但已察觉：`docs/agents/data-flow.md:145` 仍有 `词元:* & …` 字样（不在本轮允许改动清单内，未动，登记为 concern）。

### ② 验证命令与证据

**F2（dev 库恢复）——按仓库复位/搬迁路径，非逐行 DELETE：**

```
# 1) 复位业务表（刻意不含 users）
$ docker compose exec -T postgres psql -U corporate_rag -d corporate_rag -c \
  "TRUNCATE conversation_history, document, knowledge_base, chunks CASCADE;"
TRUNCATE TABLE

# 2) 从 Chroma 重新搬迁（幂等）
$ POSTGRES_HOST=localhost .venv/bin/python scripts/migrate_chroma_to_pg.py
migrate chroma->pg done: {'collections': 5, 'records': 176, 'written': 176, 'dimension_mismatch': 0, ...}
corpus ok: collections=691 non_empty=5 chunks=176 dim=1024 none_embedding=0

# 3) 不变量复验 + 重写
$ POSTGRES_HOST=localhost .venv/bin/python scripts/rewrite_content_seg.py --check
total=176 stale=0
exit=0
$ POSTGRES_HOST=localhost .venv/bin/python scripts/rewrite_content_seg.py --apply
rewritten=0
exit=0

# 复验（预期 5|0|176|1 与 5）
$ docker compose exec -T postgres psql -U corporate_rag -d corporate_rag -tAc \
  "SELECT (SELECT count(*) FROM knowledge_base) || '|' || (SELECT count(*) FROM document) || '|' || (SELECT count(*) FROM chunks) || '|' || (SELECT count(*) FROM users);"
5|0|176|1
$ docker compose exec -T postgres psql -U corporate_rag -d corporate_rag -tAc "SELECT count(DISTINCT kb_id) FROM chunks;"
5
```

（复核）176 块全部归属 `p2-migration` 的 5 个知识库：`SELECT k.user_id || ' -> ' || count(c.*) … GROUP BY k.user_id` → `p2-migration -> 176`。TRUNCATE 前计数 `90|102|176|1`，F-31 所述既有测试污染行已随复位清除；未碰 `users`、未碰 `data/`、未动 MySQL 卷。

**F3（自然语言查询 E2E 复测）——见 §④「修复轮 1 复测」**，`hybrid done` 原始日志行：

```
2026-09-20 03:24:46.864 | INFO    | trace_791db44b-0c2e-43ca-8fbe-7de5360ef1c5 | 2e530932-d5be-43cc-8b5b-cc4845d249be | src.core.logging:log_event:213 - [retrieval] hybrid done kb_id=99e491625c314a8fb78348c756ab311a query_len=8 dense_count=1 sparse_count=1 result_count=1
```

E2E 产生的 KB/文档/会话已清理（`DELETE 1(chunks); DELETE 1(document); DELETE 2(conversation_history); DELETE 1(knowledge_base); COMMIT`），随后按 F2 顺序复位恢复。

**F1 定向测试：**

```
$ POSTGRES_HOST=localhost .venv/bin/python -m pytest \
  tests/infra/db/test_lexical_query.py tests/infra/db/test_chunk_repo_lexical.py tests/rag/test_retrieval.py -v
56 passed, 1 warning in 1.40s

$ .venv/bin/pyright src/
0 errors, 0 warnings, 0 informations

$ ruff check src/ tests/
All checks passed!
```

### ③ 翻转前后 `sparse_count` 对照（同一类自然查询）

| 查询（agent 改写后的检索入参） | 翻转前 AND | 翻转后 OR |
|---|---|---|
| `资产负债率 变化`（来自自然问句「资产负债率有什么变化」） | `0` | `1` |
| `资产负债率 变化 趋势`（上一轮 E2E 首句的自然改写） | `0`（`query_len=11`，日志行见 §④） | `1` |

受控对照口径：同一 E2E 语料、同一查询、只替换连接符（`' | '` ↔ `' & '`），分别经 `VectorStore.lexical_search` 与 `ChunkRepo.search_lexical` 取数；AND 臂 `sparse_count=0`、OR 臂 `sparse_count=1`。翻转前生产日志实测亦为 0（`query_len=11`），与受控对照一致。

### ④ 最终计数与工作区

```
5|0|176|1      （knowledge_base | document | chunks | users）
5              （SELECT count(DISTINCT kb_id) FROM chunks）
```

`git status --short`（见提交前；提交后为空）：

```
 M docs/agents/api_contract.md
 M docs/agents/requirements_pool.md
 M docs/tmp/p3-lexical-probe-2026-09-19.md
 M src/infra/db/lexical_query.py
 M tests/infra/db/test_lexical_query.py
```

- 收尾计数在**只跑 F1 定向测试**之后取得；**未再跑全量 pytest**（避免再次污染）。全量门禁结论沿用上一轮 `1058 passed, 14 skipped`。
- 未删 `users`、未碰 `data/`、未动 MySQL 卷、未改探针脚本、未改测量数字。

### ⑤ commit hash

- **`d2bbc5df033fd89d651ef1b40a1dd21f1d1cf178`**（`d2bbc5d`，`fix(p3): 查询构造翻转为前缀 OR（服务路径实测 AND 使词法路零贡献）+ 恢复语料`）

---

## 修复轮 2（终审修复波）

> 终审整段返回 "With fixes"（**无 Critical**，5 条 Important + 若干一并处理项）。**根因一条**：最后一次决策反转（连接符前缀 AND → 前缀 OR）没有传播到所有仍记旧状态的产物，导致交付物与交付代码自相矛盾。本波不设计新方案，只把翻转补齐。完整报告见 `.superpowers/sdd/2026-09-19-postgres-storage-p3-lexical-tsvector/final-fix-report.md`；commit hash 见文末「修复轮 2 · commit hash」。

### ① 改动清单（文件:行）

| 发现 | 文件:行 | 改动 |
|---|---|---|
| I1 | `docs/openspec/changes/postgres-storage-consolidation/design.md:283` | 决定叙述由 ` & ` 连接改为当前状态 ` \| `（前缀 OR），并补召回取向理由 |
| I1 | `.../design.md:457` | 「改定」句的落地形态连接符 `token:* & …` → `token:* \| …` |
| I2 | `docs/agents/data-flow.md:145` | `词元:* & …` → `词元:* \| …` |
| I3 | 本文件 §⑨（`262-270`） | 保留探针当时判据，显式标注已被推翻 |
| I4 | `scripts/lexical_probe.py:211-216`、`:318` | 两臂各自显式构造（不再从生产构造器反推）+ 显式用例 mode 标签 |
| I5 | `scripts/lexical_probe_report.py:258-279`、`:22` | 选型结论改为 prefix-OR，如实陈述两层依据 |
| M1 | `.../design.md:116` | `src/infra/search/bm25_index.py:120-152` → `src/rag/fusion.py` |
| M1 | `docs/openspec/specs/typed-data-layer/spec.md:21` | **不改**：delta 的 MODIFIED requirement 整块替换，已覆盖 `bm25_res`（判据见终审报告 ④） |
| M2 | `docs/agents/glossary.md:194` | 复核为已是 D6（`f656219` 已改），无需改动 |
| M3 | `src/agents/tools/web_tools.py:4` | docstring 去掉 `bm25` |
| M4 | 本文件 `:3` | HEAD `f656219` → `8cd8f45`（本波提交前实际 HEAD） |

**I1 改后原文（`design.md:283`）：**

```
**决定**：查询串 SHALL 在程序侧构造 —— 每个词元先按安全字符集（CJK / 字母 / 数字 / 下划线）剔除，再输出 `token:*`，以 ` | ` 连接（**前缀 OR**，取**召回取向**：AND 只要有一个补词不在库里就让整条查询返回 0 —— 服务路径实测自然语言查询被改写为「资产负债率 变化 趋势」时 `变化`/`趋势` 不在语料，`sparse_count=0`，词法路贡献为零；精度由下游 RRF / 去重 / rerank 承担，不靠本层收紧谓词）；剔除后为空则整体降级为 H1 的子串兜底。
```

**I2 改后原文（`data-flow.md:145`）：**

```
  │   词法路：PostgreSQL 全文检索（chunks.tsv @@ to_tsquery('simple', 词元:* | …)，
  │            按 ts_rank 降序；词元全被滤掉时降级为 content 子串匹配）
```

**I3 改后原文（本文件 §⑨ 第 4 条，历史判据保留 + 内联标注）：**

```
4. 分组 B：`prefix-AND` 0.5000 > `plainto-AND` 0.4667（**印证 H2 前缀通配修复方向正确**）；`prefix-OR` 0.6000 恰高 10.0pp，未达"超过 10 个百分点"的翻转阈值，故维持 `" & "`；`substring` 1.0000 是**定义性**（恒真），非策略对比；〔**本结论已被推翻（见本节顶部注）**：+10.0pp 是 OR 作为超集谓词的机械后果、不构成质量证据；真正依据是服务路径 `sparse_count` 由 0→1 的实测。**当前落地为前缀 OR（`" | "`）**。〕
```

**I4 改后核心（`lexical_probe.py:211-216`）：**

```
        plan = build_lexical_query(term)
        # 两臂各自显式构造：生产构造器 `build_lexical_query` 只产出当前选定的
        # 连接符（` | `），从它反推另一臂会退化成空操作（两臂发出同一条谓词，
        # 重跑无法复现 A/B 差异）；`plan.terms` 已是安全化后的词元，直接拼装。
        and_tsq = " & ".join(f"{t}:*" for t in plan.terms)
        or_tsq = " | ".join(f"{t}:*" for t in plan.terms)
```

**I5 改后核心（`lexical_probe_report.py` 选型结论）：**

```
- 查询构造：**prefix-OR**（分组 B 命中数/总数：prefix-AND = 15/30 / prefix-OR = 18/30 / plainto-AND = 14/30 / substring = 30/30（定义性））
- **连接符取 prefix-OR**（`_JOINER = " | "`）。依据分两层：（1）探针分组 B 显示 OR 的命中覆盖面**不低于** AND（18/30 vs 15/30，高 10.0 个百分点）—— 但 OR 是 AND 的**超集谓词**，在「集合成员」口径近饱和时该差值是其**机械后果、不能读作质量证据**（三张表的测量数字是那次对比的记录）；（2）**决定性的依据来自服务路径实测**：前缀 AND 下，多词自然查询（如「资产负债率 变化 趋势」）因补词不在语料而整条谓词为空 ⇒ `sparse_count=0`、词法路对典型查询零贡献（混合检索退化为纯 dense）；翻转为 OR 后同一查询 `sparse_count=1`。精度交由下游 RRF / 去重 / rerank 承担。
```

### ② 探针重跑：分组 B 两臂已可复现（I4 证据）

重跑命令（只读，未写业务数据）：

```
POSTGRES_HOST=localhost .venv/bin/python scripts/lexical_probe.py --out /tmp/probe_rerun1.md
```

分组 B 输出：

| 配置 | 命中数/总数 | 词项命中率 |
|---|---|---|
| prefix-AND | 15/30 | 0.5000 |
| prefix-OR | 18/30 | 0.6000 |
| plainto-AND | 14/30 | 0.4667 |
| substring（定义性，非策略对比） | 30/30 | 1.0000 |

两臂谓词确实不同（直接证明，非仅计数差）：`营业收入` → `terms=('营业','收入')`，AND = `营业:* & 收入:*`，OR = `营业:* | 收入:*`。

与交付产物 `docs/tmp/p3-lexical-probe-2026-09-19.md` 对照：**分组 B 四行逐字一致**（15/30 / 18/30 / 14/30 / 30/30），词项清单一致。差异仅在**消费生产构造器 `build_lexical_query` 的两组**：A2 `jieba-tsrank` 与 C `ts_rank`/`ts_rank_cd` 由翻转前的 15/30 变为翻转后的 18/30（它们随生产连接符一并翻转）。产物作为"那次对比的记录"**保持原样**，本次重跑差异如实登记于此，**未改动产物任何测量数字**。

### ③ 门禁三连（翻转后全量）

```
=== pytest ===
1058 passed, 14 skipped, 31 warnings in 101.37s (0:01:41)

=== ruff ===
All checks passed!

=== pyright ===
0 errors, 0 warnings, 0 informations
```

### ④ 语料恢复与复验（F-31 顺序：先全量，后恢复，之后不再跑全量）

```
污染后（恢复前）：10|6|176|1
$ docker compose exec -T postgres psql -U corporate_rag -d corporate_rag -c \
    "TRUNCATE conversation_history, document, knowledge_base, chunks CASCADE;"
TRUNCATE TABLE
$ POSTGRES_HOST=localhost .venv/bin/python scripts/migrate_chroma_to_pg.py
corpus ok: collections=691 non_empty=5 chunks=176 dim=1024 none_embedding=0
migrate chroma->pg done: {'collections': 5, 'records': 176, 'written': 176, ...}
$ POSTGRES_HOST=localhost .venv/bin/python scripts/rewrite_content_seg.py --check
total=176 stale=0 ; exit=0
$ POSTGRES_HOST=localhost .venv/bin/python scripts/rewrite_content_seg.py --apply
rewritten=0 ; exit=0
```

复验：

```
5|0|176|1      （knowledge_base | document | chunks | users）
5              （count(DISTINCT kb_id) FROM chunks）
p2-migration -> 176
```

恢复之后**未再跑全量 pytest**。未删 `users`、未碰 `data/`、未动 MySQL 卷。

### ⑤ commit hash

见终审修复报告 `.superpowers/sdd/2026-09-19-postgres-storage-p3-lexical-tsvector/final-fix-report.md` ⑥ 与文末回填。
