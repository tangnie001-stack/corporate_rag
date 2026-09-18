# 检索链路的三层问题

状态：分层清单（第 3 层已细化，第 1、2 层待展开）｜ 日期：2026-09-17
来源：`trace_c54ce259` 排查（"KB 检索 4 次 + 空答案"）

---

## 0. 为什么要分层

`trace_c54ce259` 的表象是"模型检索 4 次、产出空答案"。但逐层拆开后发现是**三个互不重叠的缺陷叠在一起**：

```
表象：检索 4 次 → answer_len=0
  │
  ├─ 第 1 层  召回被截断（51 → 2）      与"没数据"无关，是配置 + 顺序问题
  ├─ 第 2 层  模型不知道该止损            信号缺失，与截断无关
  └─ 第 3 层  BM25 静默失效 + 未持久化   独立缺陷，与前两层无关
```

**为什么必须分开改**：三层可以独立修、独立验证。混在一起改会无法判断是哪个起了作用（违反"一次只改一个变量"）。**建议顺序 3 → 1 → 2**：第 3 层会污染第 1 层的验证基线。

---

## 1. 三层总览

| 层 | 问题 | 关键位置 | 状态 |
|---|---|---|---|
| **1** | 召回天花板：`RETRIEVAL_MAX_PER_DOC=1` × 文档数 = 硬顶 | `retrieval.py:95`、`settings.py:183` | 已定位根因，**待展开方案** |
| **2** | 收敛信号缺失：模型无法判断"检索已饱和" | `logging.py:234`、`rag_tools.py:129`、`prompts.py:50-55` | 已定位根因，**待展开方案** |
| **3** | BM25 静默失效 + 索引未持久化 | `bm25_index.py:87-90`、`docker-compose.yml:245-246` | **清单已细化，见 §4** |

---

## 2. 第 1 层：召回天花板

### 2.1 是什么

```
KB：51 个 chunk，分属 2 篇文档
        ↓ dense 检索（TOP_K_RETRIEVAL=8）+ BM25 → RRF 融合 ≈ 13 条
        ↓ ★ _dedup_by_doc_id(max_per_doc=RETRIEVAL_MAX_PER_DOC=1)   ← 在 rerank【之前】
        ↓   2 篇文档 × 每篇至多 1 条 = 【硬顶 2】
        ↓ rerank 只拿到 2 条 → 只能对这 2 条排序
        → 模型每轮只看到 2 条（其中 1 条恒为另一家公司）
```

### 2.2 证据

| 证据 | 内容 |
|---|---|
| 代码 | `retrieval.py:95` `results = _dedup_by_doc_id(results)`（hybrid 分支）；`:116`（非 hybrid）；**两处都在 rerank 之前** |
| 配置 | `settings.py:183` `RETRIEVAL_MAX_PER_DOC` 默认 `1`；`.env`：`TOP_K_RETRIEVAL=8` / `TOP_K_RERANK=5` |
| 实测（N 是线性控制变量） | N=1 → 每轮 2 条；N=2 → 4 条；N=3 → 6 条；N=5 → 10 条（4 轮累计不同 chunk：3 / 6 / 9 / 17） |
| 决定性证据 | 重放同 4 个 query：**iter1 与 iter2 返回完全相同的 2 个 chunk**（换了个完全不同的 query，拿回一模一样的） |
| KB 实际内容 | `neusoft_2025_q1.pdf`(13 chunks) + `tencent_2024_annual.pdf`(38 chunks)，**共 2 篇存活文档** |

### 2.3 根因：`N=1` 是**未验证的保守默认值**

- `docs/agents/glossary.md:36-37` 明确写：dedup 目的是"提升上下文多样性"；`RETRIEVAL_MAX_PER_DOC` "为 A/B 实验变量（N=1 vs N=2/3，**结论待真实 KB 评估后写入**）"
- `docs/openspec/changes/archive/2026-09-12-retrieval-quality-signals/tasks.md`：
  ```
  - [ ] 2.4 A/B 脚本：N=1 vs N=2/3 跑 RAGAS —— 脚本 src/cli/compare_dedup.py 已合入，未对真实测试集跑通出对照
  - [ ] 2.5 依据 A/B 结论定 N（或维持 1），结论写入 change 记录 —— 结论未产出
  ```
  change 在**这两个任务未完成的状态下被归档**（勾选说明归因于"当前环境无真实 KB + 无行为信号日志"）
- **那两个阻塞条件现在已解除**：真实 KB 有了（`b9e74e82`），行为信号日志有了（`trace_c54ce259` 含 `signal=reretrieve` / `signal=cited`）
- A/B 工具现成：`src/cli/compare_dedup.py`（N∈{1,2,3}，子进程覆盖环境变量跑 RAGAS）；测试集现成：`data/ragas/testset/testset_b9e74e82..._v1/v2.json`

### 2.4 附带结论

- **改 dedup 顺序不影响条数**（已实测推理）：dedup 在前 → `dedup(13)→2`；dedup 在后 → `rerank(13)→5 → dedup(5)→2`。**天花板是 `文档数 × N`，与顺序无关。** 顺序只决定"同样 2 条里选得准不准"（先 rerank 能选出每篇里最相关的那条）——**这是两个独立问题**
- **reranker 分数不区分相关与无关**（实测）：iter2 里腾讯 0.191 **高于** 4 条东软的 0.189/0.187；**加绝对分数阈值救不了，会先误杀东软**
- 6 条被召回的 chunk 全部是文档 `meta_info.eval.structure_integrity.table.broken` 标记过的**破损表格碎片**（形如 `| | | | 已报告 | | |`）→ 与分块质量有交叉

### 2.5 待办（未展开）

- [ ] 跑通 `compare_dedup.py` 的 A/B（前提：DASHSCOPE key 可用；会真实调用 RAGAS 裁判模型，有成本）
- [ ] 依结论定 `N`，或给出"维持 1"的实测依据
- [ ] 决定 `TOP_K_RETRIEVAL=8` 是否偏小（候选池只覆盖 51 的 1/4）
- [ ] 决定 dedup 是否移到 rerank 之后（独立问题，勿与 N 混改）

---

## 3. 第 2 层：收敛信号缺失

### 3.1 是什么

模型**无法判断"检索已经饱和"**，只能靠换 query 盲试，直到迭代上限。

**当前唯一能停下来的是**（`route_agent`，`agent_node.py:338-362`）：
1. 模型的最后一条消息不含 tool_call，或
2. `_agent_iterations >= effective_max`（5，或 delegate 放宽后 5+bonus）

**没有"结果没变就停"这个判断。**

### 3.2 证据

| 位置 | 状态 |
|---|---|
| `logging.py:234` | `retrieval_signal` 结尾就是 `logger.info(message)` —— **六个信号（`EMPTY_RESULT`/`RERETRIEVE`/`TO_WEB`/`ABSTAIN_AFTER_RETRIEVE`/`CITED`/`INVALID_CITATION`）全部只进日志，没有任何消费方回喂控制流** |
| `rag_tools.py:129` | `ctx.missing_years = compute_missing(parsed["years"], candidates)` —— **全 `src/` 无任何读取点**（只有写入 + 字段定义）。且该赋值本身恒为 `[]`：`parse_temporal` 返回的 years 恒为 candidates 子集 |
| `EMPTY_RESULT` 触发面 | `rag_tools.py:204` `if kb_id and not results:` —— **只在 `len==0` 时发**；trace 里 `result_count=2`，所以**从未触发** |
| `to_prompt_text`（`context.py:51-61`） | 只渲染 来源/页码/实体/内容 —— **没有条数、没有"库中共 N 条"、没有"与上轮重复"** |

### 3.3 根因：两处，缺一不可

**根因 A：提示词的检索阶梯被智能体预设**整体替换**掉了**

- `rag/prompt.py:48-53`：`if persona: base = persona` —— **整体替换**内置基础段
- 被替换掉的 `FINANCIAL_SYSTEM_PROMPT`（`prompts.py:50-55`）含完整阶梯：
  ```
  4. 检索结果为空或全部明显不相关时：换一种问法重新调用 retrieve_kb（第二次检索显式传 top_k=10）
  5. 再次检索仍无相关结果时：说明"该问题不在当前知识库范围内"，再调用 search_web
  7. 检索结果相关但不足以回答时：按已有内容作答并说明证据不足，或调用 ask_user 澄清，不得编造
  ```
- trace 日志：`persona_source=preset` → **模型收到的是 `agents/finance-expert.md`（4 条工作原则，无任何检索协议）+ 一句"必须先检索"**
- `prompt.py:54-57` 的注释记录了当初不注入的理由："无条件注入会破坏默认行为逐字不变（端到端快照）需求"——**其前提假设（"persona 非空时由预设自己承载等价指引"）已被证伪**
- 模型唯一收到的停止条件是 `USER_PROMPT_TEMPLATE`（`prompts.py:98`）里的 *"若**工具仍无法获得**相关信息，再说明…"* —— 而 `retrieve_kb` **每次成功返回 2 条、从不返回空、从不报失败** → **这一格永远不会亮**

**根因 B：没有"检索饱和"的可观测信号**

- 六个 `retrieval_signal` 无消费方（见 3.2）
- `ctx.missing_years` 写入即死（恒 `[]` 且无读者）
- 无检索次数预算（`retrieve_call_seq` 存在但只进日志；`MAX_AGENT_ITERATIONS=5` 是**总迭代**预算，混着检索/联网/追问/委派）

### 3.4 与第 3 层的交互（更正）

先前我曾判断"第 2 层可独立处理"。**实测后修正**：第 1 层修好后，模型拿到的是信息量 2~3 倍且更具体的材料，**盲目重试的动机会大幅下降**。所以第 2 层的必要性**依赖第 1 层的结论**——应先做第 1 层再评估。

### 3.5 待办（未展开）

- [ ] 决定"止损信号"的**判据**（结果集不变 / 空结果 / 检索次数 / 组合）
- [ ] 决定**拦截层**（工具内算证据 / 路由层决策 / 循环外节点）—— ⚠ 动作**不应**放工具内：`retrieve_kb` 在 ToolNode 里被并行执行，多个协程同时 ask 会撞单槽保护（`ask_confirm.py:34`）
- [ ] 决定**动作**（告诉模型 / 路由短路 / 问用户 / 直接联网）
- [ ] 修复提示词阶梯的丢失（属 prompt 归属表那条线，见 `docs/tmp/deep-research-prompt-management.md`）
- [ ] 顺带：把 `retrieval_signal` 接上消费方，或明确它只作观测

---

## 4. 第 3 层：BM25 静默失效 + 索引未持久化

### 4.1 是什么

**"混合检索"名不副实**：配置说开、日志说 hybrid、实际只有 dense。

```python
# retrieval.py:82-96
if HYBRID_SEARCH_ENABLED and bm25 and kb_id:
    d, b = await asyncio.gather(dense_t, bm25_t)
    results = rrf_fusion(d or [], b or [])              # ← b = [] 时融合结果 == dense
    log_event(Event.HYBRID_DONE, result_count=len(results))   # ← 照打 hybrid
```

### 4.2 证据

| # | 证据 | 内容 |
|---|---|---|
| 1 | `bm25_index.py:87-88` | `if not (kb_dir / "bm25.pkl").exists(): return []` —— **缺文件静默返回空**，无日志、无事件 |
| 2 | `HYBRID_DONE` 事件 | 只记 `result_count`（融合后），**不记 dense / bm25 各自贡献** → 一条死掉的 BM25 支路在日志里毫无痕迹 |
| 3 | 实测重放 | 回填前 `bm25=0`（4 个 query 全是 0）；回填后 `bm25=8`（全部 score>0）、RRF 候选池 8 → 11~13 |
| 4 | 全盘查找 | 修复前 `find / -name "bm25.pkl"` → **零命中** |
| 5 | 容器挂载表 | 只有 `chroma_persist` / `ragas` 是 bind mount，**`bm25_index` 无映射** |

### 4.3 根因：两个缺口叠加，且历史上修过一次又复发

```
2026-08-03/08-11  该 KB 文档入库            ← 早于修复
2026-09-02        a9a48d1 修复
                  commit 原文："根因：BM25Index.build_index 全链路无调用点…
                                 叠加单文档占满 dense top-K + doc_id 去重，检索只喂 1 条 context"
                  修复内容：VectorStore.get_all_chunks / rebuild_from_results /
                           document_service 入库后重建 / app_service 删 KB 时清理 /
                           cli/rebuild_bm25.py 存量回填
                  commit 自称："存量 4 个 KB 已执行回填，BM25 检索恢复正常"
2026-09-12        容器重建                ← data/bm25_index 无卷挂载 → 回填成果随容器层丢弃
2026-09-16        trace_c54ce259：bm25 全 0
```

**两个缺口：**
1. **该 KB 文档入库早于修复** → 本来就需要回填
2. **回填产物没有持久化** → `docker-compose.yml:245-246` 只挂了 `chroma_persist` 和 `ragas`，**漏了 `bm25_index`**

**且失败是静默的**（证据 1+2）—— 这是它能存活半个月无人发现的原因。

### 4.4 当前状态（含调查期间的临时改动）

```
容器内（注意：这是临时状态）
  /app/data/bm25_index/
    └── b9e74e820e0a4bad8472304446e54f5c/
          └── bm25.pkl   236,643 bytes     ← 【我 09-16 调查时回填的，仅此一个】

宿主机
  data/bm25_index/
    └── kb/            ← 空目录（0 字节），来源未确认，疑为某次手工调用用了字面量 "kb"
                         注：测试已用 tmpdir 隔离（tests/infra/search/test_bm25_index.py:34 等），
                             非测试污染

容器创建时间：2026-09-12   ← 我补索引（09-16）在其之后，所以现在还在
```

**有 ready 文档的 5 个 KB，只有 1 个有索引：**

| KB | ready 文档 | BM25 索引 |
|---|---|---|
| `b9e74e82…`（test123，trace 那个库） | 2 | ✅ 我补的（**临时**） |
| `9498856e…` | 1 | ❌ |
| `ea84fb72…` | 3 | ❌ |
| `095efa16…`（e2e_p1_kb） | 2 | ❌ |
| `2f85ec3c…` | 1 | ❌ |

**⚠ 两个直接后果：**
1. `b9e74e82` 的检索行为现已**不同于** trace 时（真混合 vs 纯 dense）→ **第 1 层 / 第 2 层若拿该 trace 做重放对比，基线已变且不可复现**（容器再重建一次就丢）
2. trace 的重放对比（如"库边界"项的 G2）必须先固定这个状态

### 4.5 ★ 处理清单（7 项）

| # | 动作 | 位置 | 为什么必须 | 验收 |
|---|---|---|---|---|
| **F1** | **加挂载** | `docker-compose.yml:246` 后加 `- ./data/bm25_index:/app/data/bm25_index`；`docker-compose.prod.yml` 同样 | 让索引持久（当前它在容器可写层） | `docker compose up -d --force-recreate app` 后 `bm25.pkl` 仍在 |
| **F2** | **在挂载生效后回填** | 容器内 `python -m src.cli.rebuild_bm25`（全部 KB） | 挂载不会凭空生成索引；4 个 KB 仍缺 | 宿主机 `data/bm25_index/{kb_id}/bm25.pkl` 出现，5 个 KB 齐全 |
| **F3** | **写入原子化** | `bm25_index.py:38-39` `build_index`：改 `temp 文件 + os.replace` | 现在 `open("wb")` + `pickle.dump` **直写目标文件**；`/mnt/d` 是 **9p**（`df -T` 实测），崩溃/中断 → **截断的 pkl** | 单测断言使用 `os.replace`（或模拟中断后目标文件仍完整） |
| **F4** | **加载失败降级** | `bm25_index.py:89-90` `pickle.load` 包 `try/except` → 记 warning + 返回 `[]` | 现在无保护。而 `retrieval.py:87` 的 `asyncio.gather` **没有 `return_exceptions=True`** → bm25 抛异常会**让整个 `retrieve_kb` 失败**（不只 BM25 腿） | 单测：放入损坏 pkl → `search` 返回 `[]` 而非抛 |
| **F5** | **让缺失可见** | 推荐：`Event.HYBRID_DONE` 增 `dense_count` / `bm25_count` 两个字段（`retrieval.py:89-94`）。可选：索引缺失时记 `BM25_INDEX_MISSING` | 这是"活了半个月无人发现"的直接原因；同时补上"看不出 BM25 贡献多少"的观测缺口 | 一次正常 hybrid 请求的日志能看到两个分路计数 |
| **F6** | **清理垃圾目录** | 删 `data/bm25_index/kb/`（空目录） | 误建产物；且未来加挂载后会进容器，可能被当成一个 KB | 目录不存在 |
| **F7** | **给回填 CLI 加防护**（可选） | `rebuild_bm25.py:49-50` `kb_ids = [args.kb]` —— **不校验 kb 是否存在** | 防止再出现 `kb/` 这类误建。注：现有逻辑对不存在 kb 会走 `KB_CHUNKS_MISSING` 跳过，**不会建目录**，所以此项优先级低 | 传不存在的 kb_id 时明确报错或仅警告 |

**改动规模**：1 行 YAML（×2 compose 文件）+ 2 处代码（F3/F4）+ 1 个日志字段（F5）+ 清理（F6）。

### 4.6 ⚠ 顺序与风险（这条最关键）

```
只做 F1（挂载）不做 F3 + F4
        ↓
把"静默降级（质量差但仍可用）"升级为"硬失败（检索彻底不可用）"

原因链：
  9p 上非原子写 → 崩溃留截断 pkl
  → pickle.load 抛异常（无保护）
  → asyncio.gather 无 return_exceptions → 向上抛
  → retrieve_kb 整个失败
  → ToolNode handle_tool_errors 捕获 → 错误消息回喂模型
  → 该 KB 检索不可用，且【不会自愈】（要人工删 pkl）
```

**→ 建议 F1 + F3 + F4 + F5 一批做；F2 在 F1 生效后做。**

### 4.7 附带发现（建议一并处理）

**pickle 里存的是当时的对象**（`bm25_index.py:39` 同时存 `bm25` 对象与 `chunks`）：

```python
pickle.dump({"bm25": bm25, "chunks": chunks}, f)
```

- 这解释了 `bm25_index.py:98-116` 那段"chunks 可能是 dict（旧格式）或 ChunkData（新格式）"的兼容代码
- **代价**：索引一旦持久化，就**跨代码版本**存活。代码升级后老 pkl 可能反序列化失败 → 这正是 F4 要兜的
- 备选：只持久化 `chunks`，BM25 对象在加载时重建（避免 pickle 类对象）。代价是每次加载多一次建索引开销。**待评估**

---

## 5. 三层之间的关系

```
                    trace_c54ce259「检索 4 次 + 空答案」
                                │
        ┌───────────────────────┼───────────────────────┐
        ▼                       ▼                       ▼
   第 1 层                 第 2 层                  第 3 层
   召回天花板              收敛信号缺失              BM25 静默失效
   （每轮只 2 条）         （不知该止损）            （混合→纯 dense）
        │                       │                       │
        │  放大 ──────────────► │                       │
        │  （材料少 → 更想重试）  │                       │
        │                       │                       │
        └──────── 都会让"检索不到"的判断失真 ────────────┘
                                │
                                ▼
                        用户感知：耗尽 5 次机会、
                        空答案、最后才问要不要联网
```

**依赖关系（按修复顺序）：**

| 顺序 | 层 | 理由 |
|---|---|---|
| 1 | **第 3 层** | 独立、小、且**它会污染第 1 层的验证基线**（当前 `b9e74e82` 已是混合模式，与 trace 时不同） |
| 2 | **第 1 层** | 影响最大，且它的结论（`N` 取几）会决定第 2 层还需不需要做 |
| 3 | **第 2 层** | 必要性依赖第 1 层结论；且它与 prompt 归属表那条线交叉（检索阶梯的丢失） |

---

## 6. 与"库边界"项的关系

三层的结论共同决定了"库边界"（`docs/tmp/kb-boundary-visibility-problem.md`）的定位：

```
用户诉求："确实没数据时提前止损"
        │
        ├─ ① 库里根本没有 → 库边界项覆盖（且只在小库成立）
        ├─ ② 库里有但没召回 → 【第 1 层】
        └─ ③ 模型不知该止损 → 【第 2 层】
```

**→ 第 1、2 层才是用户原始诉求的"正主"；库边界项只覆盖第一类的一半。** 而第 3 层是三者共同的观测前提（BM25 失效会让"检索不到"这个判断本身失真）。
