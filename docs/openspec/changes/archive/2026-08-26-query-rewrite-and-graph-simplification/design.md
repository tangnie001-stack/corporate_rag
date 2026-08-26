## Context

`rewrite_node` 是纯规则实现（`expand_query`/`condense_query`/`decompose_query`）。系统性调查（systematic-debugging + 控制实验）确认"反复追问/abstention"的三层根因：

1. **改写盲拼接**稀释查询（`腾讯2024年营收多少 毛利率呢` → rerank 0.18）。
2. **表述鸿沟**：文档报告期用中文数字"二零二四年"、表格 chunk 正文无公司名（公司名只在 metadata），带约束 query 与文档表述不匹配时 reranker 分数减半（`二零二四年毛利率`=0.833 vs `2024年毛利率`=0.221 vs `腾讯2024年毛利率是多少`=0.198）。分数标尺实测：强相关 0.52 / 中高相关 0.375 / 弱相关 0.27 / 无关 0.07-0.15，**相关（0.14-0.37）与无关（0.07-0.15）区间重叠**，绝对阈值无法区分。
3. **`RERANK_MIN_SCORE=0.3` 误杀**：相关 chunk 大量在 0.14-0.17，被 0.3 全拦 → abstain。交叉编码器分数是相对排序信号、无绝对意义（业界共识：需在自己数据上校准，无统一阈值）。

RAGAS 评估盲区：测试集问题由 chunk 生成、与文档字面匹配（"东软集团 600718 2025年第一季度报告 闫伟超 职务"），`context_precision`/`context_recall` 5/6 样本 = 1.0 属虚高，测不出难样本；且 RAGAS 在 rerank 之后测"幸存者"，被 0.3 拦掉的难样本不参与评分。

澄清链路连环问：实测 session 5 轮往返（公司→指标→兜底→期间→指标重复）才问完缺失维度。

关联 change：`intent-routing-upgrade`（in-progress）覆盖 classify 三层 + 追问，其 design 中"agent_service 捕获 grader/downgrade"的引用随本次删除而失效。

## Goals / Non-Goals

**Goals:**
- 独立 LLM 改写：medium → `standalone_query`，complex → `sub_queries`，约束保护，fallback 链
- 多查询检索：双路径 + N 路 RRF，解决表述鸿沟召回
- **rerank 去阈值**：纯 top-N 相对截断；abstention 由 LLM 语义判断
- 删除 grader 与重试环
- **批量澄清**：一次往返问完所有缺失维度；abstention 引导提示可查指标

**Non-Goals:**
- 不改 classify 三层路由与追问触发框架（属 `intent-routing-upgrade`）
- **不做多期间/多公司 KB 的 metadata 约束过滤**（当前 KB 每文档单报告期；多期间时双路径可能命中错误期间，留作 Open Questions）
- 不新增 LLM 调用做相关性判定（abstain 由 generate 的既有 prompt 判断承担，不引入 self-RAG 式 reflection）

## Decisions

### D1: 独立 LLM 改写，不并入 classify

**方案**：`rewrite_node` 改为 `make_rewrite_node(classify_llm)` 工厂（async），medium/complex 单独调用一次 flash（温度 0.1）单任务改写。prompt 与 classify 分离。

**理由**：A/B 实验证明捆绑方案（classify 内多任务输出改写）s6 未改写、s1 引入 KB 候选误导约束，质量不稳定；独立改写稳定正确。成本 +1 flash（~200-500ms），符合业界基准（Arg Software 3-call、Tarragon query rewriting +1 call）。

### D2: 改写触发条件

**方案**：complex 必触发；medium 仅当 `history 非空 or len<10 or 含口语/省略词 or 含分析/解释/说明/为什么` 触发，否则原样返回。**附加：classify 已判定 query 完整（missing_entities 为空且 query 含全部关键实体）时跳过**——批量澄清提交的组合消息（"东软集团 毛利率 2025年第一季度"）已完整，不应因 history 非空而误触发。

**理由**：medium 无历史完整查询改不改无收益；组合消息再改写是浪费。触发判断为确定性规则，零成本。

### D3: 双路径检索

**方案**：`rewrite_node` 输出 `rewritten_queries` = 改写结果 + 原始 query（去重，原 query 必须保留）；`retrieve_node` 用 `asyncio.gather` 并行逐条 dense+BM25 检索，新增 generalized N 路 RRF（现有 `rrf_fusion` 仅 2 源）合并去重后统一 rerank。

**理由**：实验证明带约束改写 rerank 掉分（0.19），裸原 query 0.52 可命中；双路径保证原 query 高分召回不被稀释。并行避免延迟线性叠加（Tarragon：expansion "latency 不是 N 倍"）。

### D4: rerank 打分策略

**方案**：**medium 统一用原始 query 打分；complex 逐子查询打分（每条子查询对合并池 rerank，取各自 top 合并）**。

**理由**：实验证实——medium 原 query 打分 `毛利率`/`毛利率呢` 分别 0.523/0.374 通过；complex 统一原 query 打分（"对比一下腾讯和东软的利润"）腾讯利润 chunk 仅 0.168、东软利润 0.156 全被拦，而逐子查询打分东软可达 0.375。去阈值后分数不再拦截，但 complex 逐子查询保证每个侧面被正确排序召回。

**备选**：complex 统一原 query 打分——排序会埋没子查询侧面，否决。

### D5: 状态设计

**方案**：新增 `rewritten_queries: list[str]`（检索用）；保留 `rewritten_query: str`（medium=standalone_query，complex=join，维持 generate 兼容）。删 `grader_score`/`retrieval_retries`/`downgraded`/`downgrade_reason`/`_prev_rewritten_query` 字段与 `LangGraphNode.Grader` 类、`DOWNGRADE_REASON_REWRITE_NO_INCREMENT` 常量。

**理由**：grader 字段无消费者（grep 确认），随删除清理；`rewritten_query` 保留避免 generate 行为变更面扩大。`_llm_rewrite` 放 `infra/search/query_router.py`（已有 LLM 调用 + token 统计模式），不放 `rag/retrieval.py`（保持纯函数）。

### D6: 删除 grader 与重试环

**方案**：删 `grader_node`、`route_by_grader`、`RetrievalGrader`；workflow `retrieve → rerank` 直连。abstention 由生成链路承担。

**理由**：grader 唯一实质作用（重试）已被证明 100% 无意义（确定性复现失败）；jieba 覆盖度与 rerank 分数双信号矛盾干扰排查。

### D7: 批量澄清（一次往返问完）

**方案**：classify prompt 强化"列出**所有**缺失实体（信息增益排序）"；`agent_service` 遍历全部 `missing_entities` 构造 `questions: [{type, question, suggestions}]`；`SSEClarificationEvent` 结构扩展为 questions 列表（保留单 question 兼容）；前端 `chat.html` 渲染多问题表单（每维度 chips + 输入框），用户一次选择/填写后**组合成一条消息**（"东软集团 毛利率 2025年第一季度"）提交，落 Redis history 一条。

**理由**：实测 session 5 轮连环问（公司→指标→兜底→期间→指标重复），用户明确要求一次问完。金融查询缺失维度是已知有限集合（公司/期间/指标），适合结构化表单式批量澄清，与 tianpan "Ask-one-question-max" 反对的开放式连环追问不同。组合消息天然满足"用户选择下次对话能拿到"（history + classify 提取）。

**边界**：维度数 ≤4（超出用通用输入框）；每个维度"其他"必须配输入框（避免用户选空兜底）；一次回答后仍缺次要维度 → best-guess 执行不再问。

### D8: abstention 引导

**方案**：generate 返回 abstain（检索空导致）时，agent_service 发一次 clarification 事件，基于 KB 候选实体提示可查指标（"东软 2025Q1 未披露毛利率，可查询：营收/净利润…"）；无候选时保持纯 abstention 文案。suggestions 来源：KB 实体聚合的 company/period + `SUGGESTIONS_MAP` 的 metric（静态兜底）。

**理由**：数据缺失场景（如东软 Q1 无毛利率）任何检索改造都救不了，abstain 是正确行为；引导能提升用户体验（告知可查什么）。一次询问、不连环。

### D9: rerank 去阈值 + LLM abstain 判定

**方案**：`rerank_results` 删除 `RERANK_MIN_SCORE` 绝对过滤，改为**纯 top-N 相对截断**（取前 `TOP_K_RERANK` 条，不看分数）。abstention 判定：**检索返回空（dense+bm25 无结果）→ 静态 abstain**；**检索非空 → top-N 全部交 LLM 语义判断**（`FINANCIAL_SYSTEM_PROMPT` 已有"文档中没有相关信息，请说明'未在文档中找到相关数据'"指令），LLM 决定能答或 abstain。

**理由**：分数标尺实测相关（0.14-0.37）与无关（0.07-0.15）区间重叠，**任何绝对阈值要么误杀相关要么漏拦无关**，绝对分数无法承担相关性判定；交叉编码器分数是相对排序信号（业界共识）。abstain 判定本应下沉到 LLM（语义判断），0.3 阈值抢在前面短路了 LLM。

**风险控制**（不依赖分数）：检索空硬边界 + prompt 强约束 + 事后 abstain 检测（`ABSTENTION_MARKERS`）+ 难样本幻觉率回归验收。

## Risks / Trade-offs

| 风险 | 影响 | 缓解 |
|------|------|------|
| 去阈值后弱相关 context 混入 prompt | LLM 幻觉（引用无关内容回答） | prompt 强约束（已有 abstain 指令）；难样本幻觉率验收；事后 abstain 检测 |
| complex 逐子查询打分增加 rerank 调用 | rerank 调用次数 = 子查询数（2-4 次） | 仅 complex 触发；并行；候选池已合并，可用子查询批量打分 |
| 双路径 + 原 query 打分在多期间/公司 KB 下命中错误期间 chunk | 答错公司/期间 | 当前 KB 每文档单报告期无冲突；批量澄清后组合消息含约束可压住；多期间时需 metadata 过滤（Open Questions） |
| 批量澄清表单认知负担 | 用户面对多问题 | 维度 ≤4 封顶；选项式低负担；已确认用户偏好一次问完胜过 5 轮往返 |
| 删除 state 字段影响 eval_ragas 初始构造 | 评估脚本报错 | 同步删除三行 |
| RAGAS 指标虚高无法验收难样本 | 回归不敏感 | 验收改为难样本 abstain 率 + 幻觉率 |

## Migration Plan

1. **先删 grader + rerank 去阈值 + abstention 改造**（独立可验证）：改 workflow/nodes/agent_service/state/eval_ragas/rerank_results，删 grader.py 与相关测试；难样本集跑 abstain 率/幻觉率基线。
2. **再实施独立改写 + 多查询检索**：改 prompts/query_router/nodes/workflow/state，新增 rewrite/检索测试。
3. **最后批量澄清 + abstention 引导**：改 classify prompt/agent_service/sse/chat.html，前端 playwright 验证。
4. 部署容器，用难样本集（s1-s6 + session 真实轨迹）回归：s2/s5 由 abstain 转命中、complex 子查询命中、session 场景 1 轮往返问完。
5. 回滚：各步独立 commit 可 revert；不涉及数据迁移。

## 验证结果（2026-08-11 容器部署实测）

### 难样本验收（`src/cli/check_abstain.py`，KB test123，真实 LLM/rerank/检索）

| 样本 | 期望 | 结果 | 回答 |
|---|---|---|---|
| s2 `毛利率呢`（历史腾讯2024营收） | hit | PASS | "腾讯2024年毛利率 53%[1]" |
| s5 `他们的营收呢`（历史介绍一下腾讯） | hit | PASS | "腾讯营收：2024年度总收入 6,603亿元" |
| s6 `2024年呢`（历史2023净利） | hit | PASS | "2024年净利润 196,467百万元" |
| s3 对比腾讯/东软利润 | hit | PASS | 腾讯利润数据（年度盈利 2,272亿） |
| s1 `毛利率呢`（历史2025Q1，数据缺失） | abstain | PASS | "未在文档中找到…文档仅包含腾讯2024"（未拿腾讯冒充东软） |
| 批量澄清 `本季度营收情况如何？` | 多维度 | PASS | classify 一次列出 2 个缺失维度 |

**5/5 达标**：多轮追问从 abstain 转命中、complex 对比命中、数据缺失诚实拒绝、缺参一次问完。

### RAGAS 回归对比（基线 fix-pdf-table 后 rerun vs 改造后）

| 指标 | 基线 | 改造后 | 说明 |
|---|---|---|---|
| context_recall | 0.9167 | **1.0000** | ↑ 双路径检索召回更全 |
| context_precision | 1.0000 | 1.0000 | = 无回归 |
| faithfulness | 1.0000 | 0.9750 | ↓0.025，部分子问题诚实拒答 |
| answer_relevancy | 0.8802 | 0.7779 | ↓0.10，诚实拒答段副作用 + 指标噪声（基线 n=3 统计意义弱） |

### playwright 前端验证

- **发现部署问题**：nginx 容器跑旧版 chat.html（部署只 rebuild app，chat.html 由 nginx 服务）→ 需 rebuild nginx 同步前端
- 后端确认：classify 一次输出 2 个缺失（company + year/period），SSE questions 数组正确
- 前端实测：多问题表单渲染（2 sections + chips + 提交按钮）✅、chip 点击填入对应维度 ✅、组合提交 "东软集团股份有限公司 2024年" ✅

### 实现期发现并修复的真实 bug

- **并行检索触发 ChromaDB 线程安全崩溃**（`RustBindingsAPI`）：Task 11 的 `asyncio.gather` 并行检索在真实环境崩溃（共享 `PersistentClient` 非线程安全）。修复：`ChromaClient` 加 `threading.RLock` 串行化所有 chromadb 访问；`similarity_search_multi` 改回串行；新增并发安全测试。

### 部署注意点

本次改动涉及 app + nginx 两个镜像，部署需同时 rebuild：

```bash
docker compose build app nginx && docker compose up -d --force-recreate app nginx
```

## Open Questions

- **LLM abstain 判定可靠性（基线实验已通过，幻觉率 0/3）**：qwen3.7-max 实测 4 场景——正常回答 1/1 正确（"53%[1]"），应 abstain 3/3 全部正确（无关公司/缺指标/完全不相关），且 S3 主动说明"不自行计算文档中未直接给出的比率"。去阈值方案可行，张冠李戴风险在 LLM 判定层被拦住。实现时按 tasks 6.5 用更大难样本集正式跑。
- **rerank API 输入上限（已落地）**：`rrf_fusion_multi` 合并后 `top_n=50` 截断，候选池不会超过 50 条进 rerank，无超限风险。
- **complex context 数量上限（已落地）**：rerank_node complex 分支合并后按 `ctx.score` 降序并截断到 `TOP_K_RERANK`（final-fix 实现）。
- **相对时间表达**："本季度/今年/去年"无解析机制（classify 不标 missing、rewrite 不解析），是既有问题；批量澄清期间维度可能兜不住"本季度"类输入，后续需相对时间解析。
- **多期间/多公司 KB 下双路径 + 原 query 打分的错误期间风险**：当前 KB 每文档单报告期无冲突；G2 实验证明 LLM 能识别"东软问题 + 腾讯 context"并 abstain，张冠李戴风险已在 LLM 判定层缓解；多期间时仍建议 metadata 约束过滤。
- **双路径检索升级为多变体**：未来用户问法口语化多样（措辞差异导致检索 miss）时，可在约束补全基础上叠加多变体生成（MultiQueryRetriever/RAG-Fusion），双路径被泛化为 N+1 路多路径。已记录需求池 F-09。
