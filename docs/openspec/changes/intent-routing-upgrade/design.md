## Context

当前 `QueryRouter` 实现（`src/infra/search/query_router.py`）只做基于正则的规则匹配（simple/vague/medium 三种），LLM 兜底 `_llm_classify()` 是始终返回 `"medium"` 的 stub。同时 `src/rag/retrieval.py` 中另有一套独立的 `classify_query()` 规则，两套逻辑互不互通。

`AgentState` 和 `nodes.py` 的 `classify_node` 目前只输出 `route`（"simple" | "medium" | "complex"），不追踪实体、置信度、追问需求。`route_vague` 在 `classify_node` 中被映射为 medium，功能上形同虚设。

参考 `financial_rag` 项目的 `IntentRouterAgent`（3 tier: 正则实体提取 → 复杂度评分 → LLM 结构化输出）和 `ClarificationService`（缺参检测 + 智能追问），本次设计一次性补齐。

## Goals / Non-Goals

**Goals:**
- QueryRouter 改为三层（实体提取 → 复杂度评分 → LLM 合并输出），去掉语义路由层
- EntityExtractor 用正则抽取财务关键实体（年份/季度/金额/指标/公司名等），0 LLM 成本
- ComplexityScorer 用关键词加权评分，输出作为 LLM 决策的 hint，不做最终判决
- classify_node 改为 `make_classify_node(llm)` 工厂函数，一次 LLM 调用输出 route + missing_entities + confidence
- 追问能力：当 classify 检测到缺关键实体时，graph 走 `classify → END`，`agent_service` 发 `SSEClarificationEvent`
- 清理 `retrieval.py` 的 `classify_query()`，统一走新的三层路由
- `EMBEDDING_MODEL` 默认值改为 `qwen3.7-text-embedding`

**Non-Goals:**
- 不做语义路由层（参考 financial_rag，不用 embedding 做复杂度分类）
- 不做 Phase 3 的 FaithfulnessChecker / Reflection（那是 generate 之后的质量环节）
- 不做前端 UI 实现（用户单独出设计稿）

## Architecture

```
                       用户 query
                           │
                ┌──────────▼──────────┐
                │  L0: 问候/长度检测    │
                │  "你好/谢谢/≤2词"    │ ───→ simple（直接 LLM）
                └──────────┬──────────┘
                           │
                ┌──────────▼──────────┐
                │  EntityExtractor     │  Tier 1: 正则
                │  year/quarter/       │  0 LLM cost
                │  metric/money/company │
                └──────────┬──────────┘
                           │ entities[]
                ┌──────────▼──────────┐
                │  ComplexityScorer    │  Tier 2: 关键词加权
                │  LOW=1 MEDIUM=2      │  0 LLM cost
                │  HIGH=3 VERY_HIGH=4  │
                └──────────┬──────────┘
                           │ complexity_score
                ┌──────────▼──────────┐
                │  LLM Classifier      │  Tier 3: 1 次 LLM 调用
                │  输入: query +       │  温度 0.1
                │   entities + score + │
                │   history            │
                │  输出: JSON          │
                └──────────┬──────────┘
                           │
                    ┌──────▼──────┐
                    │ 判断追问     │
                    │ missing_    │
                    │ entities?   │
                    └──┬──────┬───┘
                  是   │      │  否
                ┌──────▼┐  ┌─▼──────────┐
                │  END  │  │ 正常路径    │
                │  SSE: │  │ rewrite →  │
                │clarif │  │ retrieve → │
                │ ication│  │ generate   │
                └───────┘  └────────────┘
```

## Data Flow

### 正常路径（无追问）

```
classify_node LLM 输出:
{
  "route": "medium",
  "missing_entities": [],
  "confidence": 0.92
}
→ rewrite → retrieve → rerank → generate → format → SSE: token + citation + done
```

### 追问路径

```
classify_node LLM 输出:
{
  "route": "medium",
  "missing_entities": [
    {"type": "year", "question": "请问您想查询哪一年的数据？"}
  ],
  "confidence": 0.85
}
→ route_by_intent: "clarify" → END
→ agent_service 捕获 classify 的 CHAIN_END, 检测到 missing_entities
→ SSE: clarification {type, question, suggestions} → done

用户回答 "2024年"（同 session_id）:
→ 新请求，_history 包含上轮对话
→ classify_node: entities = {year: "2024", metric: "营收"}, missing = []
→ 正常路径
```

## Decisions

### D1: 不用语义路由，用实体提取 + 复杂度评分

**方案**：去掉原规划中的 L2 semantic routing，改为 `EntityExtractor`（正则实体提取）+ `ComplexityScorer`（关键词加权评分）。

**理由**：复杂度是结构/功能维度（"需要几步检索"），不是语义维度（"说的什么内容"）。embedding 相似度对话题路由有效，但对复杂度路由效果有限。financial_rag 也没有采用语义路由，而是走实体提取 + 加权评分的路线。

### D2: LLM 始终做最终分类决策

**方案**：EntityExtractor + ComplexityScorer 的输出作为 LLM 的输入上下文（提示注入），LLM 做最终的路由+槽位补全决策。复杂度评分不作为判决依据，仅作为 hint。

**理由**：正则捞不到的非标准表达（口语、方言、省略句）需要 LLM 泛化理解。与 financial_rag 一致。

### D3: 追问在图外处理（非 graph 节点）

**方案**：classify_node 输出携带 `missing_entities`，条件边路由到 `END`。`agent_service.stream_chat()` 在 `CHAIN_END` 中捕获 classify 输出，检测到 `missing_entities` 后发 `SSEClarificationEvent` 并提前结束。

**理由**：
- 避免新增 clarify_node 节点
- 追问不是 graph 内的业务逻辑，是外部交互流程
- agent_service 已经通过 CHAIN_END 捕获 contexts/grader/downgrade，模式一致

### D4: classify_node 改为工厂函数，委托 QueryRouter

**方案**：`make_classify_node(llm)` 创建 classify 节点，内部实例化 `QueryRouter(llm)`。QueryRouter 封装 EntityExtractor、ComplexityScorer、LLM 三层。classify_node 只做委托调用。

**理由**：与 financial_rag 的 IntentRouterAgent 一站式路由模式保持一致。classify_node 作为 graph 节点，业务逻辑委托给 QueryRouter。

### D5: 一次 LLM 调用完成分类+抽槽

**方案**：classify 的 LLM prompt 同时要求输出 route + missing_entities + confidence。不做两次独立调用。

**理由**：
- financial_rag 也是同一个 prompt 输出 intent + entities + confidence
- 省 1 次 LLM 调用（~2s）
- query 分析上下文共享，分开反而丢失信息

## EntityExtractor 设计

```python
# src/infra/search/entity_extractor.py

@dataclass
class ExtractedEntity:
    type: str          # year / quarter / month / metric / company / money / percentage
    value: str | None  # 提取到的值
    confidence: float   # 正则=1.0
    source: str        # "regex"

class EntityExtractor:
    PATTERNS = {
        "year":   r"20\d{2}",
        "quarter": r"第?[1-4]季[度]?|Q[1-4]",
        "month":  r"\d{1,2}月",
        "metric": r"(营收|利润|收入|成本|资产|负债|现金流|毛利率|净利率|周转率|ROE|ROA)",
        "money":  r"[¥$]?\d+(?:,\d{3})*(?:\.\d{2})?[亿万元]?",
        "percentage": r"\d+(?:\.\d+)?%",
        "company": r"(?:[A-Z]\w{1,10}|[\u4e00-\u9fa5]{2,8})(?:公司|集团|有限[公司])?",
    }

    def extract(self, query: str) -> list[ExtractedEntity]:
        """正则提取所有匹配实体。"""
```

**注意**：`company` 正则只做粗略匹配，复杂公司名由 Tier 3 LLM 补充。

## ComplexityScorer 设计

```python
# src/infra/search/complexity_scorer.py

COMPLEXITY_RULES = {
    ComplexityLevel.LOW:    [(r"(你好|您好|hi|hello)", 1),
                             (r"(什么是|什么叫|定义)", 1)],
    ComplexityLevel.MEDIUM: [(r"(计算|查询|找出|列出)", 2),
                             (r"(如何|怎么|怎样)", 2)],
    ComplexityLevel.HIGH:   [(r"(比较|对比|差异|versus|vs)", 3),
                             (r"(分析|解释|说明|为什么|原因)", 3)],
    ComplexityLevel.VERY_HIGH: [(r"(报告|报表|生成)", 4),
                                (r"多个|多.*?个|各种|所有", 4)],
}

def score_complexity(query: str, entities: list[ExtractedEntity]) -> float:
    """返回加权分数。"""
    score = 0.0
    for level, patterns in COMPLEXITY_RULES.items():
        for pattern, weight in patterns:
            if re.search(pattern, query):
                score += weight
    # 实体数量加分
    score += len(entities) * 0.5
    # "和/或/与" 加分（多条件）
    if re.search(r"[和或与]", query):
        score += 2
    return score
```

## LLM Classifier Prompt 设计

### 系统指令（`prompts.py` 新增）

```python
CLASSIFIER_SYSTEM_PROMPT: str = """你是一个查询分析专家。分析用户查询的复杂度并补充缺失的实体信息。

输入包含：
- 用户问题
- 已提取实体列表（正则匹配，可能为空）
- 复杂度评分（规则预判，仅供参考）
- 对话历史（多轮上下文）

分析任务：
1. 确定路由（route）：simple / medium / complex
2. 补充缺失实体：检查 query 中是否缺少关键信息（年份、公司、指标等）
3. 评估置信度：0~1

规则：
- simple: 问候、感谢、单一事实查询，不需要检索或仅需简单检索
- medium: 需单次检索的事实性问题（"2024年营收多少"）
- complex: 需多步推理、对比、因果关系分析（"对比A和B的差异"）
- 如果 query 缺少关键信息（如 "营收多少" 缺年份，但历史没提），标记为 missing_entities
- 如果缺失实体可以从对话历史中推断，不要标记为缺失

只返回 JSON，不要包含其他内容。"""
```

### 用户消息模板

```python
CLASSIFIER_USER_TEMPLATE: str = """用户问题：{query}

已提取实体（正则）：
{entities}

复杂度评分（规则预判）：{complexity_score}

对话历史（最近2轮）：
{history}

说明：{history} 由 QueryRouter 将 AgentState._history（最近2轮）格式化为文本：
"用户: 2024年营收多少\n助手: 2024年营收为100亿\n用户: 利润率呢"

输出 JSON（严格按此格式）：
{{
  "route": "simple|medium|complex",
  "missing_entities": [
    {{"type": "year", "question": "请问您想查询哪一年的数据？"}}
  ],
  "confidence": 0.0
}}
"""
```

## SSE 新事件

```python
# src/utils/sse.py 新增

@dataclass
class SSEClarificationEvent:
    type: str              # "entity_completion" | "intent_clarification"
    question: str          # 追问文本
    missing_entities: list[dict]  # [{"type": "year"}, ...]
    suggestions: list[str] # 快捷选项，如 ["2023年", "2024年", "其他"]

# SSE wire format:
# event: clarification
# data: {"type":"entity_completion","question":"请问您想查询哪一年的数据？",...}

### 追问触发逻辑

clafify_node 的 SSE type 按以下规则确定（参考 financial_rag 的 ClarificationType）：

- `missing_entities` 非空 → type = `"entity_completion"`（实体缺失，最常见场景）
- `missing_entities` 为空 且 `confidence < 0.5` → type = `"intent_clarification"`（意图模糊）
- 以上都不满足 → 不发 clarification，走正常路径
```

## AgentState 新增字段

```python
# src/agents/graph/state.py

# ── 意图理解 ──
extracted_entities: list[dict] = field(default_factory=list)  # EntityExtractor 输出
missing_entities: list[dict] = field(default_factory=list)    # LLM 标记的缺失实体
classification_confidence: float = 0.0                        # LLM 置信度（LLM 输出 key="confidence"，state 字段命名保持项目风格）
```

## Suggestions 映射表

```python
# src/infra/search/entity_extractor.py 或 query_router.py

# 追问快捷选项映射表（financial_rag 模式：硬编码，不靠 LLM 生成）
SUGGESTIONS_MAP: dict[str, list[str]] = {
    "year":     ["2023年", "2024年", "其他"],
    "quarter":  ["一季度", "二季度", "三季度", "四季度"],
    "month":    ["1月", "12月", "其他"],
    "company":  ["腾讯", "阿里巴巴", "其他"],
    "metric":   ["营收", "利润", "毛利率", "其他"],
    "default":  ["请补充说明", "其他"],
}
```

clafify_node 根据 missing_entities 的类型从映射表取 suggestions，未匹配的类型用 "default"。

## 文件改动清单

### 新增文件
| 文件 | 说明 |
|------|------|
| `src/infra/search/entity_extractor.py` | 正则实体提取器 |
| `src/infra/search/complexity_scorer.py` | 复杂度加权评分器 |
| `tests/infra/search/test_entity_extractor.py` | 实体提取测试 |
| `tests/infra/search/test_complexity_scorer.py` | 复杂度评分测试 |
| `tests/services/test_agent_service.py` | 追问流程测试（mock graph，验证 clarification SSE） |

### 修改文件
| 文件 | 改动 |
|------|------|
| `src/infra/search/query_router.py` | 重写为三层架构 |
| `src/config/settings.py` | EMBEDDING_MODEL 默认值改 qwen3.7-text-embedding；新增 CLASSIFIER_TEMPERATURE、CLARIFICATION_ENABLED |
| `src/config/prompts.py` | 新增 CLASSIFIER_SYSTEM_PROMPT、CLASSIFIER_USER_TEMPLATE |
| `src/config/const.py` | LangGraphNode.Classify 新增 EXTRACTED_ENTITIES、MISSING_ENTITIES、CLASSIFICATION_CONFIDENCE 常量 |
| `src/infra/llm/prompt_manager.py` | 新增 get_classifier_prompt() |
| `src/agents/graph/state.py` | AgentState 新增 extracted_entities、missing_entities、classification_confidence |
| `src/agents/graph/nodes.py` | classify_node → make_classify_node(llm)；修改 route_by_intent 加 clarify 分支 |
| `src/agents/graph/workflow.py` | classify 条件边新增 "clarify": END |
| `src/utils/sse.py` | 新增 SSEClarificationEvent、sse_clarification()、to_sse 分发分支 |
| `src/services/agent_service.py` | stream_chat 捕获 classify CHAIN_END 的 missing_entities 并处理 |
| `src/rag/retrieval.py` | 删除 classify_query()；rewrite_query 改为接收 intent_route 参数 |
| `tests/infra/search/test_query_router.py` | 新增实体提取、追问触发测试 |
| `tests/agents/graph/test_graph.py` | 新增追问路径测试 |

### 前端
| 改动 | 说明 |
|------|------|
| SSE 新增 `event: clarification` | 需要监听并展示追问 UI |
| `session_id` 复用 | 用户回答追问后同 session 发第二次请求 |
| 追问期间状态 | 显示"等待补充信息"，不要显示"回答结束" |

## Risks / Trade-offs

| 风险 | 影响 | 缓解 |
|------|------|------|
| LLM classify 增加首 token 延迟 ~1s | 用户体验 | classify 是第一个节点，影响有限；Tier 1/2 的纯规则拦截可跳过 LLM |
| 追问后用户跑题 | 对话上下文混乱 | `_history` 正常记录，classify 结合 history 判断，问题不大 |
| 正则实体覆盖不全 | 部分实体漏提 | Tier 1 是锦上添花，漏了 Tier 3 LLM 会补 |
| 复杂度评分与 LLM 判断冲突 | LLM 错误理解 query | 评分只作为 hint，LLM 做最终决策，不硬约束 |
| PromptManager 新增 Langfuse prompt | 运维成本 | 默认兜底到本地常量，不配 Langfuse 不影响使用 |
