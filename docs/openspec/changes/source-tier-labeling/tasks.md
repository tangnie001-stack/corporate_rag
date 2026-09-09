# Tasks: source-tier-labeling

依赖顺序：1 → 2 → 3 → 4 → 5 → 6；第 2 组（前端徽标）依赖 1.3 的 citation tier 字段，可与 2.x 后端部分并行推进到 4.2 前。

## 1. 规则表与解析

- [ ] 1.1 TDD：`resolve_source_tier(url, kind)` 解析函数——KB kind=T0；清单命中（tencent.com/zhihu.com 等种子域名）定 T1-T4；`.gov.cn`/`.edu.cn` 模式升 T1；未命中默认 T3；匹配语义用例：域边界后缀（`evil-zhihu.com` 不命中 `zhihu.com`）、最长后缀优先（`finance.sina.com.cn` 优先于 `sina.com.cn`）、大小写/`www.` 前缀/路径/端口归一化
- [ ] 1.2 `const.py` 实现 `SOURCE_TIER_RULES` 种子清单（T1 官方：tencent.com/cninfo.com.cn/sse.com.cn/szse.cn 等；T2 媒体：caixin.com/yicai.com/wallstreetcn.com/finance.sina.com.cn/eastmoney.com 等；T4 UGC：zhihu.com/xueqiu.com/weibo.com 等——LLM 批量拟候选 + 人工校对定稿）
- [ ] 1.3 `RAGContext` 增加 `tier` 字段：默认 `None`（未定档/存量），**不设 T0 兜底默认**；T0 仅由 `resolve_source_tier(kind=kb)` 显式产出（D6）

## 2. tier 透传

- [ ] 2.1 三个构造点显式赋 tier：`retrieval.py` / `rag_tools.py`（KB 构造点，经 `resolve_source_tier(kind=kb)` 产出 T0——缺此步 KB 徽标永不显示）与 `web_tools.py`（web 构造点，显式调用解析函数）
- [ ] 2.2 context 块文本追加档位标注——两处渲染点各自追加，格式统一「(档位标签)」括注，标签文案同源 const：KB 路径 `to_prompt_text`（`来源: xxx.pdf (第1页, 内部文档)`，标签并入页码括注），web 路径 `web_tools.py` 块文本手拼处（`[3] 来源: url (权威媒体)`）；`to_prompt_text` 与 RAGAS 共用，标注后为评估基线分界点
- [ ] 2.3 citation 链路四处携带 tier（tier=None 时落库与 payload 均为 null）：`nodes.py` format_node citations dict → `utils/sse.py` SSECitationEvent 字段 + payload_for_buffer → `services/agent_service.py:315` 事件构造 → `agent_service.py:505` 手拼落库 dict（source/page/snippet/kind/index 处加 tier；漏改则历史回放徽标全挂且实时测试发现不了）
- [ ] 2.4 TDD：全链透传测试（web 结果 → RAGContext.tier → context 文本标注 → citation payload → 落库 dict 全链断言；含 KB 构造点 T0 用例）

## 3. 非法编号观测

- [ ] 3.1 `format_node` 超范围编号记 `retrieval_signal: signal=invalid_citation`（同编号重复出现聚合计 count，如 `ids=[99,99,102] count=3`），替换静默忽略
- [ ] 3.2 TDD：幻觉编号用例（[99] 超池 → 不进 citations 且产出信号；[99] 重复出现 → count 聚合为 3）

## 4. 前端徽标（chat.html）

> **执行方式**：本组实现时调用 `/frontend-design` skill，视觉规格以 `docs/design/pages/chat-citation-tier-2026-09-09.md` + 预览 `chat-citation-tier-mockup-2026-09-09.html` 为唯一依据（设计方向已确认：徽标仅在抽屉条目、来源名称之前；横条不加），不重新发散设计。

- [ ] 4.1 tier → 中文等级映射表（官方一手/权威媒体/一般/UGC/内部文档），以 7.1 在 api_contract 登记的权威映射为准，前端照抄不另造文案
- [ ] 4.2 抽屉条目徽标渲染（来源名称之前；tier 缺失/null 不显示），横条保持既有形态不加徽标；实时与历史回放路径一致
- [ ] 4.3 核对 docs/design/（MASTER.md / pages）中引用抽屉的规格档，有登记则补一行徽标说明（一事一档）

## 5. 候选规则信号（V1 极简）

- [ ] 5.1 离线聚合 SQL：从 `conversation_history.sources` 提取**全部**域名，统计引用次数/跨会话数，阈值 ≥5 次且跨 ≥3 会话（不做 SQL 侧「未命中清单」过滤——避免复刻 Python 解析逻辑的双头维护，过滤放人工审核步骤人眼对照规则表；「进正文比例」字段拿掉，sources 落库的均为已引用条目，该比例恒为 1 无意义）
- [ ] 5.2 negative cache 机制：拒绝的域名登记进 cookbook 拒绝清单（文档记录，**不建数据库表**），防重复候选——实现前先跑一次聚合验证数据可得性
- [ ] 5.3 cookbook 登记「候选规则审核」操作流程（跑 SQL → 对照规则表排除已命中 + negative cache → 看样本消息引用上下文 → 批准改 const.py / 拒绝记清单）

## 6. 验证

- [ ] 6.1 playwright：抽屉徽标渲染（T1/T2/T4 各一例 + tier 缺失降级 + 横条无徽标确认），实时与历史回放一致
- [ ] 6.2 端到端：真实联网提问 → 检查 context 块文本档位标注 → 引用徽标与来源域名分级正确
- [ ] 6.3 标准门禁：pytest 全过 / ruff 无错误 / pyright 无新增 error

## 7. 文档登记

- [ ] 7.1 api_contract.md：citation 新增 tier 字段说明 + **tier→中文标签权威映射表**（前端 4.1 照抄的唯一来源）
- [ ] 7.2 glossary.md：新术语「来源等级（source tier）」「候选规则信号」
- [ ] 7.3 cookbook.md：候选规则审核操作流程（5.3 完成后合并登记）+ 拒绝清单（negative cache 落点）
