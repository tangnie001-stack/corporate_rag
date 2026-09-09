# Proposal: source-tier-labeling

## Why

回答中的引用来源（尤其是联网搜索结果）质量参差：腾讯官网年报 PDF 与知乎帖在系统里无差别对待，用户无法区分「官方一手」与「UGC 观点帖」，模型也不被引导优先引用权威来源。曾考虑用 LLM judge 校验引用正确性/权威性，实战否决（误判率高、影响对话质量）。定案方案借鉴 firecrawl-deep-research 的「来源优先级 + 透明呈现 + 读者终审」思路：用**确定性规则**给来源定权威等级并全程透明化，可信度判断的终审权交给用户，不做 LLM 裁决。

## What Changes

- **来源权威分级规则表**：`const.py` 新增 `SOURCE_TIER_RULES`（T1 官方一手 / T2 权威财经媒体 / T3 默认档 / T4 UGC 的域名清单 + 模式规则），并提供域名 → tier 解析函数（清单命中 → 定档；`.gov.cn`/`.edu.cn` 模式升档；未命中 → 默认 T3）
- **KB 内部文档 = T0**：知识库文档不走域名分级（用户上传的内部资料，天然最高信任级）
- **tier 全链路透传**：web 检索结果解析时计算 tier 写入 `RAGContext`；返回给模型的 context 块文本携带档位标注（模型天然感知，优先引用高等级来源——利用模型既有领域常识，即 firecrawl 的 agent 判断模式）；citation 落库携带 tier
- **前端权威徽标**：引用抽屉条目展示来源等级（T1 官方/T2 媒体/T3 一般/T4 UGC，KB 显示「内部文档」，位于来源名称之前；横条保持既有形态不加徽标）——可信度透明呈现给用户，系统不裁决
- **非法引用编号观测信号**：format_node 中被忽略的超范围编号（幻觉编号）补记 retrieval_signal（当前为静默忽略，无观测）
- **候选规则信号流程（V1 极简）**：离线 SQL 从 `sources` 列聚合未命中域名的引用统计（次数/跨会话），过阈值输出候选，人工审核后加入规则表——清单成长机制建立在行为数据 + 人工闸门上，无 LLM 定档、无新模块

## Capabilities

### New Capabilities
- `source-tier-labeling`: 来源权威分层——域名分级的规则表与解析、tier 在检索结果/context/引用全链路的透传、前端权威徽标、未命中默认档与模式兜底、候选规则信号的离线聚合与人工审核流程

### Modified Capabilities
- `chat-harness-ui`: 引用抽屉条目新增来源权威等级徽标（名称之前；实时与历史回放路径一致，横条不加）

## Impact

- **代码**：`src/config/const.py`（规则表 + 解析函数）、`src/infra/search/tavily_client.py` 或 `src/agents/tools/web_tools.py`（tier 计算）、`src/rag/context.py`（RAGContext 加 tier 字段；`to_prompt_text` 追加档位标注，RAGAS 评估上下文随之包含标注，构成评估基线分界点）、`src/agents/graph/nodes.py`（非法编号观测信号）、`src/utils/sse.py` 与 `src/services/agent_service.py`（citation 链路加字段与落库 dict）、`deploy/nginx/html/chat.html`（徽标；**前端实现阶段经 `/frontend-design` skill 按设计规格档执行**，见下条）
- **API**：citation 数据结构增加 tier 字段（SSECitationEvent payload；tier=null 表示存量/未知，前端降级）；api_contract 登记 tier→中文标签权威映射
- **数据库**：无表结构变更（tier 随 sources JSON 落库；negative cache 用文档记录，不建表）
- **文档**：glossary（来源等级术语）、api_contract（citation 新字段）、cookbook（候选规则审核操作）
- **设计**：页面规格 `docs/design/pages/chat-citation-tier-2026-09-09.md` + 效果预览 `docs/design/chat-citation-tier-mockup-2026-09-09.html`（已登记 MASTER.md）；**前端部分实现时调用 `/frontend-design` skill，以规格档为唯一视觉依据，不重新发散设计**
- **依赖**：无新增外部依赖；分级清单为人工维护的先验数据（无外部数据集可用，已核实 firecrawl-workflows 仓库与本地参考项目均无现成数据）
- **明确不做**：检索期过滤/丢弃低档来源（后置增强）、LLM 定档（judge 实战否决）、候选管理界面（YAGNI）、外部可信度数据集接入（领域/语言不匹配）
