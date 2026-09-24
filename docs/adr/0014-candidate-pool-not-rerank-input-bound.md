# ADR-0014：更正 ADR-0001 的两段陈述（候选池非精排输入上界；多库路径死因）

- **Status**：Accepted
- **Date**：2026-09-24
- **Deciders**：用户（决策）；Claude（调研与实测）；独立评审子代理（发现）
- **Supersedes**：ADR-0001 的「候选池 30 的成本收益陈述」与「多库路径的 `not kb_id` 分支陈述」（局部）

## 背景与问题

ADR-0001（2026-09-18）决定取消每文档配额、改用内容级（父块）去重，本变更 `retrieval-fetch-and-dedup` 正在实施它。实施前的一轮独立架构评审发现该 ADR 的**两段陈述与代码事实不符**。

**为什么不原地改**：ADR-0001 的决策 2（候选池 `TOP_K_RETRIEVAL` 取 30）**已先行落地代码**（`src/config/settings.py:243` 默认 30、`.env`、`README.md`）。按 `README.md`「原地修订…仅当该 ADR 尚未落地任何代码；一旦已落地代码，一律改为『新开 ADR + 反向指针』」，故不修订正文，改为新开本 ADR 承载更正，ADR-0001 正文一字不改，仅在其 `Status` 上加反向指针。

## 更正一：候选池大小不是精排输入的上界，故「精排成本降约 40%」不成立

- **原文**（ADR-0001「接受的代价」末条）：「候选池 30 目前无实测依据：实测只覆盖 50。取 30 的收益是精排成本降约 40%，而召回损失未测」
- **事实**：`TOP_K_RETRIEVAL` 约束的是**各支路取数**（dense 与词法各取至多该条数）；hybrid 路径喂给精排的是 **RRF 融合结果**，其上界是 `RRF_TOP_N`（`src/config/settings.py:287`，当前 50），与 `TOP_K_RETRIEVAL` 无关。
- **证据**：`src/rag/retrieval.py:91` 的 `rrf_fusion(dense_results, sparse_results, k=RRF_K, top_n=RRF_TOP_N)` 与 `src/rag/fusion.py` 的 `ranked[:top_n]`；本变更的触发 trace `trace_19e8e472`（2026-09-23）实测 `hybrid done result_count=30~35`，远未触及 50。
- **更正**：**撤回**「精排成本降约 40%」这一收益陈述。候选池 30 的现存依据只剩「各支路取数规模」与「落在业界区间 30~100 内」，**不是**精排成本。若确需压缩精排输入/成本，应调整 `RRF_TOP_N`（该动作同时影响召回面，属独立评估项）。
- **决策本身不变**：`TOP_K_RETRIEVAL=30` 仍然有效（已落地）。

## 更正二：`retrieval.search` 没有 `kb_id` 空分支

- **原文**（ADR-0001「现状机制」）：「`retrieval.search` 的唯一生产调用方是 `rag_tools.py:136` 且 `kb_id` 恒非空（`:135` 守卫）→ `retrieval.py:98-105` 的 `not kb_id` 分支生产不可达」
- **事实**：`retrieval.search`（`src/rag/retrieval.py:67-117`）**不存在** `kb_id` 空分支；空守卫在调用方 —— `src/agents/tools/rag_tools.py:132` 的 `if kb_id:`，为空时根本不调用 `search`（唯一生产调用方是 `rag_tools.py:133`）。
- **结论不变**：多库 / 空 `kb_id` 路径在生产不可达这一**判断**仍然成立，只是机制描述写错了。

## 附注（属"当时快照"，不构成单独取代）

ADR-0001 正文的若干**行号引用**已随 `postgres-storage-*` 变更漂移（如去重调用点 `:95` 现为 `:102`；`dedup_max_per_doc` 的写方在 `src/agents/tools/rag_tools.py`，而非正文所写 `src/rag/retrieval.py:197`）。行号是写作时点的快照，不逐个取代；查现状以 `docs/agents/code-map.md` 与本 ADR 为准。

## 后果

- 读者经 ADR-0001 的 `Status` 指针可达本 ADR，不会再据「降 40%」做取舍判断。
- `retrieval-fetch-and-dedup` 的 `design.md` D3「口径更正」与 Open Questions 5 与本 ADR 同源，二者互为引用。
- ADR-0001 的决策（取消每文档配额 + 内容级去重 + 候选池 30 + 精排 5）**全部继续有效**。

## 复查触发条件

- 若决定调整 `RRF_TOP_N` → 重估「精排输入规模 / 成本」这条线，本 ADR 的更正一随之复核。
- 若 `retrieval.search` 将来真的引入 `kb_id` 空分支 → 更正二的前提变化，需重新描述死因。
