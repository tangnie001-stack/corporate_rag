# Intent Routing Upgrade 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 QueryRouter 从纯正则规则升级为三层架构（实体提取 → 复杂度评分 → LLM 合并输出），新增实体提取、追问对话能力。

**Architecture:** QueryRouter 封装 EntityExtractor（正则实体提取）和 ComplexityScorer（关键词加权评分）两层，再通过 LLM 做最终路由决策。classify_node 改为工厂函数委托 QueryRouter。Graph 条件边新增 `"clarify": END` 分支，agent_service 检测 missing_entities 后发 SSEClarificationEvent。

**Tech Stack:** Python 3.11+ / LangGraph / LangChain ChatOpenAI / re / numpy

## Global Constraints

- EMBEDDING_MODEL 默认值改为 qwen3.7-text-embedding
- classify LLM 温度 0.1
- LLM 输出 key="confidence"，state 字段名="classification_confidence"
- 新增 prompt 常量走 PromptManager（Langfuse 兜底本地）
- 所有函数必须写 docstring（中文）
- 不引入新的第三方库

---

### Task 1: 基础设施与配置

**Files:**
- Modify: `src/config/settings.py`
- Modify: `src/config/prompts.py` (L1-L52)
- Modify: `src/infra/llm/prompt_manager.py`
- Test: 无需单独测试（这些是常量定义和配置）

**Interfaces:**
- Consumes: 现有 PromptManager 模式、现有 settings.py 模式
- Produces: `CLASSIFIER_SYSTEM_PROMPT`, `CLASSIFIER_USER_TEMPLATE`, `PromptManager.get_classifier_prompt()`

- [ ] **Step 1: 修改 settings.py 默认值**

```python
# src/config/settings.py

# 将 EMBEDDING_MODEL 默认值从 deepseek-embed-v4 改为 qwen3.7-text-embedding
EMBEDDING_MODEL: str = os.getenv("EMBEDDING_MODEL", "qwen3.7-text-embedding")

# 新增配置项（放在 LLM_TEMPERATURE 附近）
CLASSIFIER_TEMPERATURE: float = float(os.getenv("CLASSIFIER_TEMPERATURE", "0.1"))
CLARIFICATION_ENABLED: bool = os.getenv("CLARIFICATION_ENABLED", "true").lower() in ("true", "1", "yes")
```

- [ ] **Step 2: 在 prompts.py 新增 classifier 常量**

```python
# src/config/prompts.py，文件末尾追加

# ====== Classifier Prompt ======

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

CLASSIFIER_USER_TEMPLATE: str = """用户问题：{query}

已提取实体（正则）：
{entities}

复杂度评分（规则预判）：{complexity_score}

对话历史（最近2轮）：
{history}

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

- [ ] **Step 3: PromptManager 新增 get_classifier_prompt 方法**

```python
# src/infra/llm/prompt_manager.py

from src.config.prompts import (
    FINANCIAL_SYSTEM_PROMPT,
    USER_PROMPT_TEMPLATE,
    CLASSIFIER_SYSTEM_PROMPT,  # 新增导入
    CLASSIFIER_USER_TEMPLATE,   # 新增导入
)

# 在 PROMPT_NAMES dict 中新增
PROMPT_NAMES = {
    "system": "financial-system-prompt",
    "user": "user-prompt-template",
    "classifier": "classifier-prompt",  # 新增
}

# 新增方法
def get_classifier_prompt(
    self,
    query: str,
    entities: str,
    complexity_score: float,
    history: str,
) -> str:
    """获取 classifier 完整 prompt（system + user）。
    
    从 Langfuse 拉取或使用本地兜底。
    将 entities、complexity_score、history 填充到 user template 中。
    
    Args:
        query: 用户查询文本
        entities: 已提取实体文本（如 "year=2024, metric=营收"）
        complexity_score: 复杂度评分
        history: 对话历史文本（最近2轮，由调用方格式化）
    
    Returns:
        完整的 prompt 文本（system + user 消息拼接）
    """
    sys_prompt = self._get(
        self.PROMPT_NAMES["classifier"],
        CLASSIFIER_SYSTEM_PROMPT,
    )
    user_prompt = CLASSIFIER_USER_TEMPLATE.format(
        query=query,
        entities=entities or "无",
        complexity_score=str(complexity_score),
        history=history or "无",
    )
    return f"{sys_prompt}\n\n{user_prompt}"
```

- [ ] **Step 4: Commit**

```bash
git add src/config/settings.py src/config/prompts.py src/infra/llm/prompt_manager.py
git commit -m "feat: add classifier config, prompts and PromptManager method"
```

---

### Task 2: EntityExtractor 模块

**Files:**
- Create: `src/infra/search/entity_extractor.py`
- Create: `tests/infra/search/test_entity_extractor.py`

**Interfaces:**
- Consumes: `re` 标准库
- Produces: `ExtractedEntity` dataclass, `EntityExtractor` class with `extract(query) -> list[ExtractedEntity]`

- [ ] **Step 1: Write the failing tests**

```python
# tests/infra/search/test_entity_extractor.py
"""EntityExtractor 正则实体提取单元测试。"""

from src.infra.search.entity_extractor import EntityExtractor, ExtractedEntity


extractor = EntityExtractor()


def test_extract_year():
    result = extractor.extract("2024年营收多少")
    entities = {e.type: e.value for e in result if e.source == "regex"}
    assert entities.get("year") == "2024"


def test_extract_metric():
    result = extractor.extract("毛利率是多少")
    entities = {e.type: e.value for e in result if e.source == "regex"}
    assert entities.get("metric") == "毛利率"


def test_extract_quarter():
    result = extractor.extract("一季度净利润多少")
    entities = {e.type: e.value for e in result if e.source == "regex"}
    assert entities.get("quarter") is not None


def test_extract_money():
    result = extractor.extract("营收100亿")
    entities = {e.type: e.value for e in result if e.source == "regex"}
    assert entities.get("money") is not None


def test_extract_percentage():
    result = extractor.extract("毛利率15%")
    entities = {e.type: e.value for e in result if e.source == "regex"}
    assert entities.get("percentage") is not None


def test_extract_company():
    result = extractor.extract("腾讯公司2024年营收")
    entities = {e.type: e.value for e in result if e.source == "regex"}
    assert entities.get("company") is not None


def test_no_match_returns_empty():
    result = extractor.extract("你好")
    assert len(result) == 0


def test_multiple_entities():
    result = extractor.extract("2023年腾讯净利润100亿")
    types = {e.type for e in result if e.source == "regex"}
    assert "year" in types
    assert "company" in types
    assert "metric" in types
    assert "money" in types
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/infra/search/test_entity_extractor.py -v`
Expected: ModuleNotFoundError (entity_extractor.py 还不存在)

- [ ] **Step 3: Write EntityExtractor implementation**

```python
# src/infra/search/entity_extractor.py
"""正则实体提取器 — 从查询中提取财务关键实体，0 LLM 成本。

支持的实体类型：
  - year/quarter/month: 时间维度
  - metric: 财务指标（营收、利润、毛利率等）
  - company: 公司名称（粗略匹配，复杂名称由 LLM 补充）
  - money/percentage: 金额和百分比
"""

import re
from dataclasses import dataclass, field


@dataclass
class ExtractedEntity:
    """提取到的实体。

    Attributes:
        type: 实体类型（year / quarter / month / metric / company / money / percentage）
        value: 提取到的值，未匹配时为 None
        confidence: 置信度，正则=1.0
        source: 提取来源（"regex" | "llm"）
    """
    type: str
    value: str | None
    confidence: float = 1.0
    source: str = "regex"


class EntityExtractor:
    """正则实体提取器 — 从查询中提取预定义模式的实体。"""

    PATTERNS: dict[str, str] = {
        "year":   r"20\d{2}",
        "quarter": r"第?[1-4]季[度]?|Q[1-4]",
        "month":  r"\d{1,2}月",
        "metric": r"(营收|利润|收入|成本|资产|负债|现金流|毛利率|净利率|周转率|ROE|ROA)",
        "money":  r"[¥$]?\d+(?:,\d{3})*(?:\.\d{2})?[亿万元]?",
        "percentage": r"\d+(?:\.\d+)?%",
        "company": r"(?:[A-Z]\w{1,10}|[\u4e00-\u9fa5]{2,8})(?:公司|集团|有限[公司])?",
    }

    def extract(self, query: str) -> list[ExtractedEntity]:
        """正则提取所有匹配实体。

        Args:
            query: 用户查询文本

        Returns:
            提取到的实体列表，按 PATTERNS 顺序匹配
        """
        entities: list[ExtractedEntity] = []
        for etype, pattern in self.PATTERNS.items():
            match = re.search(pattern, query)
            if match:
                entities.append(ExtractedEntity(
                    type=etype,
                    value=match.group(0),
                    confidence=1.0,
                    source="regex",
                ))
        return entities
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/infra/search/test_entity_extractor.py -v`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add src/infra/search/entity_extractor.py tests/infra/search/test_entity_extractor.py
git commit -m "feat: add EntityExtractor for regex-based entity extraction"
```

---

### Task 3: ComplexityScorer 模块

**Files:**
- Create: `src/infra/search/complexity_scorer.py`
- Create: `tests/infra/search/test_complexity_scorer.py`

**Interfaces:**
- Consumes: `re` 标准库, `ExtractedEntity` from entity_extractor
- Produces: `score_complexity(query, entities) -> float`

- [ ] **Step 1: Write the failing tests**

```python
# tests/infra/search/test_complexity_scorer.py
"""ComplexityScorer 复杂度加权评分单元测试。"""

from src.infra.search.complexity_scorer import score_complexity


def test_empty_query_returns_zero():
    assert score_complexity("", []) == 0.0


def test_greeting_scores_low():
    score = score_complexity("你好", [])
    assert 0 < score <= 2


def test_medium_keyword_increases_score():
    score = score_complexity("计算2024年营收", [])
    # "计算"=2
    assert score >= 2


def test_high_keyword_increases_score():
    score = score_complexity("对比A和B的差异", [])
    # "对比"=3, "差异"=3, "和"=2 → 8
    assert score >= 5


def test_entities_increase_score():
    from src.infra.search.entity_extractor import ExtractedEntity
    entities = [ExtractedEntity(type="year", value="2024")]
    score_no_entity = score_complexity("营收多少", [])
    score_with_entity = score_complexity("营收多少", entities)
    assert score_with_entity > score_no_entity


def test_and_conjunction_increases_score():
    score = score_complexity("营收和利润", [])
    assert score >= 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/infra/search/test_complexity_scorer.py -v`
Expected: ModuleNotFoundError (complexity_scorer.py 还不存在)

- [ ] **Step 3: Write ComplexityScorer implementation**

```python
# src/infra/search/complexity_scorer.py
"""复杂度加权评分器 — 为查询计算复杂度分数，作为 LLM 的 hint。

评分规则：
  - LOW (1): 问候、定义查询
  - MEDIUM (2): 计算、查询类
  - HIGH (3): 对比、分析类
  - VERY_HIGH (4): 报告、多条件
  - 实体数量 ×0.5
  - "和/或/与" +2
"""

import re
from enum import IntEnum

from src.infra.search.entity_extractor import ExtractedEntity


class ComplexityLevel(IntEnum):
    """复杂度级别（用于规则分类）。"""
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    VERY_HIGH = 4


# 复杂度规则表：(正则模式, 权重)
COMPLEXITY_RULES: dict[ComplexityLevel, list[tuple[str, int]]] = {
    ComplexityLevel.LOW: [
        (r"(你好|您好|hi|hello)", 1),
        (r"(什么是|什么叫|定义)", 1),
    ],
    ComplexityLevel.MEDIUM: [
        (r"(计算|查询|找出|列出)", 2),
        (r"(如何|怎么|怎样)", 2),
    ],
    ComplexityLevel.HIGH: [
        (r"(比较|对比|差异|versus|vs)", 3),
        (r"(分析|解释|说明|为什么|原因)", 3),
    ],
    ComplexityLevel.VERY_HIGH: [
        (r"(报告|报表|生成)", 4),
        (r"多个|多.*?个|各种|所有", 4),
    ],
}


def score_complexity(query: str, entities: list[ExtractedEntity]) -> float:
    """计算查询复杂度评分。

    评分只作为 LLM 的 hint，不做最终判决。

    Args:
        query: 用户查询文本
        entities: EntityExtractor 提取的实体列表

    Returns:
        加权分数，越高表示越复杂
    """
    if not query.strip():
        return 0.0

    score = 0.0
    for level, patterns in COMPLEXITY_RULES.items():
        for pattern, weight in patterns:
            if re.search(pattern, query):
                score += weight

    # 实体数量加分：多个维度说明查询更具体
    score += len(entities) * 0.5

    # "和/或/与" 加分：多条件查询
    if re.search(r"[和或与]", query):
        score += 2

    return score
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/infra/search/test_complexity_scorer.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add src/infra/search/complexity_scorer.py tests/infra/search/test_complexity_scorer.py
git commit -m "feat: add ComplexityScorer for keyword-weighted complexity scoring"
```

---

### Task 4: AgentState 扩展

**Files:**
- Modify: `src/agents/graph/state.py`
- Modify: `tests/agents/graph/test_state.py`

**Interfaces:**
- Consumes: 现有 AgentState dataclass
- Produces: AgentState 新增 `extracted_entities`, `missing_entities`, `classification_confidence` 字段

- [ ] **Step 1: Write the failing test**

```python
# tests/agents/graph/test_state.py 追加

def test_agent_state_intent_fields():
    """验证新增的意图理解字段默认值。"""
    state = AgentState()
    assert state.extracted_entities == []
    assert state.missing_entities == []
    assert state.classification_confidence == 0.0

def test_agent_state_intent_fields_with_values():
    """验证 intent 字段可以正常赋值。"""
    state = AgentState(
        extracted_entities=[{"type": "year", "value": "2024"}],
        missing_entities=[{"type": "year"}],
        classification_confidence=0.85,
    )
    assert len(state.extracted_entities) == 1
    assert len(state.missing_entities) == 1
    assert state.classification_confidence == 0.85
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/agents/graph/test_state.py::test_agent_state_intent_fields -v`
Expected: AttributeError (字段还不存在)

- [ ] **Step 3: Modify AgentState**

```python
# src/agents/graph/state.py

# 在 # ── 路由控制 ── 与 # ── 内部 ── 之间插入：
    # ── 意图理解 ──
    extracted_entities: list[dict] = field(
        default_factory=list
    )  # EntityExtractor 输出
    missing_entities: list[dict] = field(
        default_factory=list
    )  # LLM 标记的缺失实体（如 [{"type": "year", "question": "哪一年？"}]
    classification_confidence: float = 0.0  # LLM 置信度（LLM 输出 key="confidence"）
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/agents/graph/test_state.py -v`
Expected: 6 passed (原有4个 + 新增2个)

- [ ] **Step 5: Commit**

```bash
git add src/agents/graph/state.py tests/agents/graph/test_state.py
git commit -m "feat: add intent understanding fields to AgentState"
```

---

### Task 5: LangGraphNode 常量扩展

**Files:**
- Modify: `src/config/const.py`

**Interfaces:**
- Consumes: 现有 LangGraphNode.Classify（仅 NAME）
- Produces: `LangGraphNode.Classify.EXTRACTED_ENTITIES`, `.MISSING_ENTITIES`, `.CLASSIFICATION_CONFIDENCE`

- [ ] **Step 1: Modify LangGraphNode.Classify**

```python
# src/config/const.py

class Classify:
    NAME: str = "classify"  # 查询分类
    EXTRACTED_ENTITIES: str = "extracted_entities"  # 正则提取的实体列表
    MISSING_ENTITIES: str = "missing_entities"  # LLM 标记的缺失实体
    CLASSIFICATION_CONFIDENCE: str = "classification_confidence"  # LLM 置信度
```

- [ ] **Step 2: 验证——搜索现有引用确认没有 break**

```bash
grep -rn "LangGraphNode.Classify" src/ --include="*.py"
# 确认只有 workflow.py 和 nodes.py 引用 Classify.NAME
```

- [ ] **Step 3: Commit**

```bash
git add src/config/const.py
git commit -m "feat: add Classify output field constants to LangGraphNode"
```

---

### Task 6: SSE 新事件

**Files:**
- Modify: `src/utils/sse.py`

**Interfaces:**
- Consumes: 现有 SSEEvent 模式
- Produces: `SSEClarificationEvent` dataclass, `sse_clarification()` 函数, `to_sse()` 新增分支

- [ ] **Step 1: Write the failing tests**

在 `tests/utils/test_sse.py`（如不存在则创建）：
```python
# tests/utils/test_sse.py
"""SSE 工具函数单元测试。"""

from src.utils.sse import (
    SSEClarificationEvent,
    sse_clarification,
    to_sse,
)


def test_sse_clarification_format():
    event = SSEClarificationEvent(
        type="entity_completion",
        question="请问您想查询哪一年的数据？",
        missing_entities=[{"type": "year"}],
        suggestions=["2023年", "2024年", "其他"],
    )
    output = sse_clarification(event)
    assert output.startswith("event: clarification")
    assert "entity_completion" in output
    assert "2023年" in output
    assert output.endswith("\n\n")


def test_to_sse_handles_clarification():
    event = SSEClarificationEvent(
        type="entity_completion",
        question="test",
        missing_entities=[{"type": "year"}],
        suggestions=["a", "b"],
    )
    output = to_sse(event)
    assert output.startswith("event: clarification")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/utils/test_sse.py -v`
Expected: ImportError (SSEClarificationEvent 还不存在)

- [ ] **Step 3: Add SSEClarificationEvent and sse_clarification**

```python
# src/utils/sse.py

# 在 SSEModelInfoEvent 之前或之后新增：

@dataclass
class SSEClarificationEvent:
    """追问事件 — 当系统需要用户补充信息时触发。"""
    type: str  # "entity_completion" | "intent_clarification"
    question: str  # 追问文本
    missing_entities: list[dict]  # [{"type": "year"}, ...]
    suggestions: list[str]  # 快捷选项


# 更新 SSEEvent 联合类型
SSEEvent = (
    SSEStatusEvent
    | SSETokenEvent
    | SSECitationEvent
    | SSEErrorEvent
    | SSEDoneEvent
    | SSEModelInfoEvent
    | SSEClarificationEvent  # 新增
)


def sse_clarification(event: SSEClarificationEvent) -> str:
    """构建 SSE clarification 事件。
    
    Args:
        event: 追问事件对象
    
    Returns:
        SSE 格式的文本行
    """
    return (
        f"event: clarification\n"
        f"data: {json.dumps({
            'type': event.type,
            'question': event.question,
            'missing_entities': event.missing_entities,
            'suggestions': event.suggestions,
        }, ensure_ascii=False)}\n\n"
    )
```

- [ ] **Step 4: Update to_sse() to handle clarification**

```python
# src/utils/sse.py to_sse 函数中新增 match 分支

    match event:
        case SSETokenEvent(token=token):
            return sse_token(token)
        # ... 原有分支 ...
        case SSEClarificationEvent(
            type=t, question=q, missing_entities=me, suggestions=s
        ):
            return sse_clarification(SSEClarificationEvent(t, q, me, s))
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/utils/test_sse.py -v`
Expected: 2 passed

- [ ] **Step 6: Commit**

```bash
git add src/utils/sse.py tests/utils/test_sse.py
git commit -m "feat: add SSEClarificationEvent and sse_clarification function"
```

---

### Task 7: QueryRouter 重写

**Files:**
- Modify: `src/infra/search/query_router.py`
- Modify: `tests/infra/search/test_query_router.py`

**Interfaces:**
- Consumes: `EntityExtractor`, `score_complexity`, `PromptManager.get_classifier_prompt()`, `llm` (ChatOpenAI)
- Produces: `QueryRouter.__init__(llm, prompt_manager)`, `QueryRouter.route(query, history) -> dict`

- [ ] **Step 1: Write the failing test**

```python
# tests/infra/search/test_query_router.py 重写
"""QueryRouter 意图路由模块的单元测试。

测试范围:
  - L0 问候拦截
  - L1 实体提取
  - L2 复杂度评分
  - L3 LLM 分类
  - 追问触发
"""

from unittest.mock import Mock, patch
from src.infra.search.query_router import QueryRouter
from src.config import CLASSIFIER_TEMPERATURE


def test_l0_greeting_returns_simple():
    """问候/长度拦截直接返回 simple，不经过实体提取和 LLM。"""
    router = QueryRouter(llm=Mock())
    router._llm_classify = Mock()  # 不应被调用
    result = router.route("你好", history=[])
    assert result["intent"]["route"] == "simple"
    router._llm_classify.assert_not_called()


def test_l0_short_query_returns_simple():
    router = QueryRouter(llm=Mock())
    router._llm_classify = Mock()
    result = router.route("谢谢", history=[])
    assert result["intent"]["route"] == "simple"
    router._llm_classify.assert_not_called()


def test_entity_extraction_included_in_output():
    router = QueryRouter(llm=Mock())
    # mock LLM 返回
    router._llm_classify = Mock(return_value={
        "route": "medium",
        "missing_entities": [],
        "confidence": 0.9,
    })
    result = router.route("2024年营收多少", history=[])
    assert result["extracted_entities"] is not None
    assert len(result["extracted_entities"]) > 0


def test_missing_entity_triggers_clarify():
    router = QueryRouter(llm=Mock())
    router._llm_classify = Mock(return_value={
        "route": "medium",
        "missing_entities": [{"type": "year", "question": "哪一年？"}],
        "confidence": 0.85,
    })
    result = router.route("营收多少", history=[])
    assert len(result["missing_entities"]) > 0


def test_cache_hits_llm():
    router = QueryRouter(llm=Mock())
    router._llm_classify = Mock(return_value={
        "route": "medium", "missing_entities": [], "confidence": 0.9,
    })
    r1 = router.route("2024年营收", [])
    r2 = router.route("2024年营收", [])
    # 第二次应命中缓存，不调用 LLM
    assert router._llm_classify.call_count == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/infra/search/test_query_router.py -v`
Expected: 部分失败（旧测试与新接口不兼容，正常）

- [ ] **Step 3: Rewrite QueryRouter**

```python
# src/infra/search/query_router.py
"""查询意图路由器模块 — 三层架构：实体提取 → 复杂度评分 → LLM 分类。

架构:
  L0: 问候/长度检测（直接返回 simple，0 LLM）
  L1: EntityExtractor（正则实体提取，0 LLM cost）
  L2: ComplexityScorer（关键词加权评分，0 LLM cost）
  L3: LLM Classifier（1 次 LLM 调用，输出 route + missing_entities + confidence）
"""

import json
import re
from typing import Any

from loguru import logger
from langchain_core.messages import HumanMessage

from src.infra.search.entity_extractor import EntityExtractor, ExtractedEntity
from src.infra.search.complexity_scorer import score_complexity
from src.infra.llm.prompt_manager import PromptManager
from src.agents.graph.state import RAGQueryIntent

# 问候/短查询模式（L0 拦截）
_GREETING_PATTERNS = [
    r"^(你好|您好|hi|hello|thanks|谢谢)$",
]
_SHORT_QUERY_THRESHOLD = 2  # ≤2 个中文字符判为 short

# 追问快捷选项映射表
SUGGESTIONS_MAP: dict[str, list[str]] = {
    "year":     ["2023年", "2024年", "其他"],
    "quarter":  ["一季度", "二季度", "三季度", "四季度"],
    "month":    ["1月", "12月", "其他"],
    "company":  ["腾讯", "阿里巴巴", "其他"],
    "metric":   ["营收", "利润", "毛利率", "其他"],
    "default":  ["请补充说明", "其他"],
}


def _format_history(history: list) -> str:
    """将对话历史格式化为文本（最近 2 轮）。
    
    Args:
        history: ChatMessage 列表
    
    Returns:
        格式化后的历史文本，如 "用户: xx\n助手: yy"
    """
    lines = []
    for msg in history[-4:]:  # 最多 4 条（2 轮对话）
        role = "用户" if getattr(msg, "role", "") == "user" else "助手"
        content = getattr(msg, "content", "") or ""
        lines.append(f"{role}: {content}")
    return "\n".join(lines)


class QueryRouter:
    """查询意图路由器 — 实体提取 → 复杂度评分 → LLM 分类。
    
    Attributes:
        _entity_extractor: EntityExtractor 实例
        _llm: ChatOpenAI 实例（Tier 3 使用）
        _prompt_manager: PromptManager 实例
        _cache: 查询缓存（避免重复调用 LLM）
    """

    def __init__(
        self,
        llm=None,
        prompt_manager: PromptManager | None = None,
    ):
        self._entity_extractor = EntityExtractor()
        self._llm = llm
        self._prompt_manager = prompt_manager or PromptManager()
        self._cache: dict[str, dict[str, Any]] = {}

    def route(self, query: str, history: list | None = None) -> dict[str, Any]:
        """执行三层路由。
        
        Args:
            query: 用户查询文本
            history: 对话历史（ChatMessage 列表）
        
        Returns:
            dict 包含:
              - intent: RAGQueryIntent（含 route 字段）
              - extracted_entities: list[dict]
              - missing_entities: list[dict]
              - classification_confidence: float
        """
        history = history or []
        cleaned = query.strip()

        # ── L0: 问候/长度检测 ──
        if not cleaned:
            return self._simple_result()
        if any(re.match(p, cleaned) for p in _GREETING_PATTERNS):
            logger.info("QueryRouter L0: greeting -> simple")
            return self._simple_result()
        if len(cleaned) <= _SHORT_QUERY_THRESHOLD:
            logger.info("QueryRouter L0: short query -> simple")
            return self._simple_result()

        # ── 缓存 ──
        if cleaned in self._cache:
            logger.info("QueryRouter: cache hit for '{}'", cleaned[:30])
            return self._cache[cleaned]

        # ── L1: 实体提取 ──
        entities = self._entity_extractor.extract(cleaned)
        entities_dict = [
            {"type": e.type, "value": e.value, "confidence": e.confidence}
            for e in entities
        ]

        # ── L2: 复杂度评分 ──
        complexity_score = score_complexity(cleaned, entities)

        # ── L3: LLM 分类 ──
        if self._llm:
            llm_result = self._llm_classify(cleaned, entities, complexity_score, history)
        else:
            # 无 LLM 时的兜底（基于评分）
            llm_result = self._fallback_route(complexity_score)

        result: dict[str, Any] = {
            "intent": RAGQueryIntent(route=llm_result["route"]),
            "extracted_entities": entities_dict,
            "missing_entities": llm_result.get("missing_entities", []),
            "classification_confidence": llm_result.get("confidence", 0.0),
        }

        self._cache[cleaned] = result
        return result

    def _simple_result(self) -> dict[str, Any]:
        """L0 拦截时的快速返回。"""
        return {
            "intent": RAGQueryIntent(route="simple"),
            "extracted_entities": [],
            "missing_entities": [],
            "classification_confidence": 1.0,
        }

    def _llm_classify(
        self,
        query: str,
        entities: list[ExtractedEntity],
        complexity_score: float,
        history: list,
    ) -> dict[str, Any]:
        """LLM 分类兜底。
        
        用 PromptManager 构建 prompt，调用 LLM 获取 JSON 输出。
        
        Args:
            query: 用户查询
            entities: 正则提取的实体列表
            complexity_score: 复杂度评分
            history: 对话历史
        
        Returns:
            {"route": str, "missing_entities": list, "confidence": float}
        """
        entities_text = "; ".join(
            f"{e.type}={e.value}" for e in entities if e.value
        ) if entities else "无"
        history_text = _format_history(history)

        prompt = self._prompt_manager.get_classifier_prompt(
            query=query,
            entities=entities_text,
            complexity_score=complexity_score,
            history=history_text,
        )

        try:
            from src.config import CLASSIFIER_TEMPERATURE
            messages = [HumanMessage(content=prompt)]
            response = self._llm.invoke(messages, temperature=CLASSIFIER_TEMPERATURE)
            raw = response.content.strip()
            if raw.startswith("```"):
                lines = raw.split("\n")
                raw = "\n".join(lines[1:-1] if lines[-1] == "```" else lines[1:])
            result = json.loads(raw)
            return {
                "route": result.get("route", "medium"),
                "missing_entities": result.get("missing_entities", []),
                "confidence": result.get("confidence", 0.5),
            }
        except Exception as e:
            logger.warning("QueryRouter LLM classify failed: {}", e)
            return self._fallback_route(complexity_score)

    def _fallback_route(self, complexity_score: float) -> dict[str, Any]:
        """LLM 失败时的兜底路由。
        
        Args:
            complexity_score: 复杂度评分
        
        Returns:
            路由决策 dict
        """
        if complexity_score >= 3.5:
            route = "complex"
        elif complexity_score >= 1.5:
            route = "medium"
        else:
            route = "simple"
        return {
            "route": route,
            "missing_entities": [],
            "confidence": 0.5,
        }
```

- [ ] **Step 4: Update tests to match new QueryRouter interface**

```python
# 保留 Step 1 中的测试用例，确认全部通过
```

- [ ] **Step 5: Run all tests**

Run: `pytest tests/infra/search/test_query_router.py tests/infra/search/test_entity_extractor.py tests/infra/search/test_complexity_scorer.py -v`
Expected: all passed

- [ ] **Step 6: Commit**

```bash
git add src/infra/search/query_router.py tests/infra/search/test_query_router.py
git commit -m "feat: rewrite QueryRouter with 3-tier architecture (entity-scorer-LLM)"
```

---

### Task 8: classify_node 改造 + workflow 条件边

**Files:**
- Modify: `src/agents/graph/nodes.py` (L64-L81)
- Modify: `src/agents/graph/workflow.py` (L24-L26, L64-L86)
- Test: `tests/agents/graph/test_graph.py`

**Interfaces:**
- Consumes: `QueryRouter`, `llm`, `AgentState`
- Produces: `make_classify_node(llm)` factory, `route_by_intent` updated with "clarify"

- [ ] **Step 1: Write the failing test**

```python
# tests/agents/graph/test_graph.py 新增

from unittest.mock import Mock
from src.agents.graph.state import AgentState
from src.agents.graph.nodes import make_classify_node
from src.agents.graph.workflow import route_by_intent


def test_make_classify_node_returns_callable():
    node = make_classify_node(llm=Mock())
    assert callable(node)


def test_classify_node_output_has_new_fields():
    """classify_node 输出包含 extracted_entities / missing_entities / confidence。"""
    llm = Mock()
    llm.invoke.return_value.content = '{"route":"medium","missing_entities":[],"confidence":0.9}'
    node = make_classify_node(llm)
    state = AgentState(query="2024年营收多少")
    result = node(state)
    assert "extracted_entities" in result
    assert "missing_entities" in result
    assert "classification_confidence" in result


def test_route_by_intent_returns_clarify_when_missing_entities():
    """route_by_intent 检测到 missing_entities 时返回 'clarify'。"""
    state = AgentState(
        query="营收多少",
        intent={"route": "medium", "rewritten": False},
        missing_entities=[{"type": "year"}],
    )
    # 注意：route_by_intent 需要改为检查 state.missing_entities
    # 详细逻辑见 Step 3
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/agents/graph/test_graph.py::test_make_classify_node_returns_callable -v`
Expected: ImportError (make_classify_node 还不存在)

- [ ] **Step 3: Modify nodes.py**

```python
# src/agents/graph/nodes.py

# 将 classify_node 从纯函数改为工厂函数
def make_classify_node(llm) -> Callable:
    """创建分类节点工厂函数。
    
    内部实例化 QueryRouter(llm)，封装三层路由（实体提取→复杂度评分→LLM）。
    
    Args:
        llm: ChatOpenAI 实例（用于 Tier 3 分类）
    
    Returns:
        classify_node 异步函数
    """
    from src.infra.search.query_router import QueryRouter

    router = QueryRouter(llm=llm)

    async def classify_node(state: AgentState) -> dict:
        """查询分类节点：基于 QueryRouter 输出三级路由 + 实体 + 置信度。"""
        logger.info("classify_node start: query={}", state.query[:50])

        result = router.route(state.query, state._history)

        logger.info(
            "classify_node done: route={} entities={} missing={} confidence={}",
            route,
            len(result["extracted_entities"]),
            len(result["missing_entities"]),
            result["classification_confidence"],
        )

        return {
            "intent": result["intent"],
            "extracted_entities": result["extracted_entities"],
            "missing_entities": result["missing_entities"],
            "classification_confidence": result["classification_confidence"],
        }

    return classify_node
```

**注意**：保留原有的 `classify_node` 函数在模块中仍可导入，但 workflow 中需要改成用 `make_classify_node` 创建。旧函数可以删除或标记为 deprecated。

- [ ] **Step 4: Modify route_by_intent 加 clarify 分支**

```python
# src/agents/graph/workflow.py

from src.config.const import LangGraphNode

def route_by_intent(state: AgentState) -> str:
    """根据意图路由到不同路径。
    
    - simple: 跳过 rewrite，直接 retrieve
    - medium/complex: 走 rewrite
    - clarify: 需要追问，直接结束
    """
    if state.missing_entities:
        logger.info("route_by_intent: missing_entities -> clarify")
        return "clarify"
    return state.intent.route or "medium"
```

- [ ] **Step 5: Update workflow.py graph builder**

```python
# src/agents/graph/workflow.py

# imports 新增
from src.agents.graph.nodes import (
    make_classify_node,  # 替换 classify_node
    ...
)

# builder.add_node 中
# 将:
#     builder.add_node(LangGraphNode.Classify.NAME, classify_node)
# 改为:
    builder.add_node(LangGraphNode.Classify.NAME, make_classify_node(llm))

# 条件边新增 clarify 分支
builder.add_conditional_edges(
    LangGraphNode.Classify.NAME,
    route_by_intent,
    {
        "simple": LangGraphNode.Retrieve.NAME,
        "medium": LangGraphNode.Rewrite.NAME,
        "complex": LangGraphNode.Rewrite.NAME,
        "clarify": END,  # 新增
    },
)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/agents/graph/test_graph.py -v`
Expected: all passed

- [ ] **Step 7: Commit**

```bash
git add src/agents/graph/nodes.py src/agents/graph/workflow.py tests/agents/graph/test_graph.py
git commit -m "feat: convert classify_node to factory, add clarify branch to conditional edge"
```

---

### Task 9: agent_service 改造

**Files:**
- Modify: `src/services/agent_service.py`
- Test: `tests/services/test_agent_service.py`

**Interfaces:**
- Consumes: `SSEClarificationEvent`, `AgentState.missing_entities`
- Produces: stream_chat 在 CHAIN_END 中捕获 classify 输出，处理 clarification

- [ ] **Step 1: Write the failing test**

```python
# tests/services/test_agent_service.py
"""AgentService stream_chat 追问流程测试。"""

import pytest
from unittest.mock import AsyncMock, Mock, patch
from src.utils.sse import SSEClarificationEvent


@pytest.mark.asyncio
async def test_stream_chat_sends_clarification_when_missing_entities():
    """当 classify 输出缺失实体时，应发送 SSEClarificationEvent。"""
    from src.services.agent_service import AgentService

    service = AgentService.__new__(AgentService)
    service._graph = AsyncMock()
    service._llm = Mock()
    service._chat_manager = AsyncMock()
    service._chat_manager.get_history_async.return_value = []
    service._chat_manager.add_message_async = AsyncMock()
    service._prompt_manager = Mock()
    service._tracer = Mock()

    events = []
    async for event in service.stream_chat("kb1", "session1", "营收多少"):
        events.append(event)

    clarification_events = [e for e in events if isinstance(e, SSEClarificationEvent)]
    assert len(clarification_events) > 0
    assert clarification_events[0].type == "entity_completion"


@pytest.mark.asyncio
async def test_stream_chat_normal_flow_no_clarification():
    """当 classify 没有缺失实体时，不走追问路径。"""
    from src.services.agent_service import AgentService

    service = AgentService.__new__(AgentService)
    service._graph = AsyncMock()
    service._llm = Mock()
    service._chat_manager = AsyncMock()
    service._chat_manager.get_history_async.return_value = []
    service._chat_manager.add_message_async = AsyncMock()
    service._prompt_manager = Mock()
    service._tracer = Mock()

    events = []
    async for event in service.stream_chat("kb1", "session1", "2024年营收多少"):
        events.append(event)

    clarification_events = [e for e in events if isinstance(e, SSEClarificationEvent)]
    assert len(clarification_events) == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/services/test_agent_service.py -v`
Expected: AssertionError（agent_service 还没改）

- [ ] **Step 3: Modify agent_service.stream_chat**

```python
# src/services/agent_service.py

# 新增 import
from src.utils.sse import (
    SSEClarificationEvent,
    ...
)

# 在 stream_chat 方法中，async for 循环内部新增 clarify 检测逻辑

_clarification_pending = None  # 新增在 try 块开始前

# 在 CHAIN_END 的 match 分支中新增 classify 检测
case LangGraphEvent.CHAIN_END:
    output = event.get(LangGraphKey.DATA, {}).get(LangGraphKey.OUTPUT)
    if isinstance(output, dict):
        if LangGraphNode.Classify.NAME in name:
            missing = output.get("missing_entities", [])
            if missing:
                _clarification_pending = {
                    "type": "entity_completion",
                    "missing_entities": missing,
                }
        # 原有 rerank/grader/generate 分支保持不变...

# 在 async for 循环结束后，clarify 检测
# 在 yield SSECitationEvent 之前
if _clarification_pending:
    cp = _clarification_pending
    # 从 missing_entities 取第一个构造事件
    first = cp["missing_entities"][0]
    entity_type = first.get("type", "default")
    from src.infra.search.query_router import SUGGESTIONS_MAP
    suggestions = SUGGESTIONS_MAP.get(entity_type, SUGGESTIONS_MAP["default"])
    yield SSEClarificationEvent(
        type=cp["type"],
        question=first.get("question", "请补充相关信息"),
        missing_entities=cp["missing_entities"],
        suggestions=suggestions,
    )
    yield SSEDoneEvent()
    return  # 不发送后面的 citation/model_info/done

# 原有逻辑不变...
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/services/test_agent_service.py -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add src/services/agent_service.py tests/services/test_agent_service.py
git commit -m "feat: add clarification detection and SSEClarificationEvent emission in agent_service"
```

---

### Task 10: 清理 retrieval.py 重复分类器

**Files:**
- Modify: `src/rag/retrieval.py` (删除 L143-L190 classify_query, 修改 rewrite_query)
- Modify: `src/agents/graph/nodes.py` (rewrite_node 调用处)
- Test: 相关测试文件

- [ ] **Step 1: 检查 rewrite_query 当前调用者**

```bash
grep -rn "rewrite_query" src/ --include="*.py"
# 确认只有 nodes.py 中 rewrite_node 调用
```

- [ ] **Step 2: 删除 classify_query，修改 rewrite_query 签名**

```python
# src/rag/retrieval.py

# 删除 classify_query 函数（L143-L190 整块）
# 删除 classify_query 对应的 import 和引用

# 修改 rewrite_query 签名
def rewrite_query(
    query: str,
    history: list[ChatMessage],
    intent_route: str = "medium",  # 新增参数，来自 classify_node
) -> str | list[str]:
    """根据传入的意图路由执行相应的改写策略。
    
    不再内部分类，由调用方（classify_node）传入 intent_route。
    
    Args:
        query: 用户查询文本
        history: 对话历史
        intent_route: classify_node 输出的路由（simple/medium/complex）
    
    Returns:
        str: simple / medium 路径返回改写后的单条查询
        list[str]: complex 路径返回分解后的多条子查询
    """
    if intent_route == "simple":
        return query
    elif intent_route == "complex":
        return decompose_query(query)
    else:  # medium
        if len(query.strip()) < 10:
            return expand_query(query, history)
        if any(w in query for w in ["分析", "解释", "说明", "为什么"]):
            return condense_query(query)
        return query
```

- [ ] **Step 3: 更新 rewrite_node 调用处**

```python
# src/agents/graph/nodes.py

def rewrite_node(state: AgentState) -> dict:
    """查询改写节点：对非 simple 路径的查询进行改写。"""
    query = state.query
    intent_route = state.intent.route or "medium"
    rewritten = rewrite_query(query, state._history or [], intent_route=intent_route)
    # 后续代码不变...
```

- [ ] **Step 4: 更新受影响的测试**

```bash
grep -rn "classify_query\|rewrite_query" tests/ --include="*.py"
# 更新 mock classify_query 的地方改为 mock rewrite_query 的新签名
```

- [ ] **Step 5: Run full test suite**

Run: `pytest tests/ -v`
Expected: 全部通过（原有测试 + 新增测试）

- [ ] **Step 6: Commit**

```bash
git add src/rag/retrieval.py src/agents/graph/nodes.py
# 添加更新的测试文件
git commit -m "refactor: remove duplicate classify_query, unify routing through QueryRouter"
```

---

### Task 11: 追问流程集成测试

**Files:**
- Test: `tests/services/test_agent_service.py`（已创建）
- Test: `tests/agents/graph/test_graph.py`（已修改）
- Test: `tests/infra/search/test_query_router.py`（已修改）

- [ ] **Step 1: 测试 missing_entities 触发条件**

```python
# tests/infra/search/test_query_router.py 追加

def test_no_year_triggers_missing():
    """缺年份时触发 missing_entities。"""
    router = QueryRouter(llm=Mock())
    router._llm_classify = Mock(return_value={
        "route": "medium",
        "missing_entities": [{"type": "year", "question": "请问您想查询哪一年的数据？"}],
        "confidence": 0.85,
    })
    result = router.route("营收多少", history=[])
    assert len(result["missing_entities"]) == 1
    assert result["missing_entities"][0]["type"] == "year"
```

- [ ] **Step 2: 测试 history 补齐后不触发追问**

```python
def test_history_resolves_entity():
    """history 中有年份信息时，不标记为缺失。"""
    from src.infra.llm.chat_message import ChatMessage
    router = QueryRouter(llm=Mock())
    router._llm_classify = Mock(return_value={
        "route": "medium",
        "missing_entities": [],
        "confidence": 0.92,
    })
    history = [
        ChatMessage(role="user", content="2024年营收多少"),
        ChatMessage(role="assistant", content="2024年营收为100亿"),
    ]
    result = router.route("利润率呢", history=history)
    assert len(result["missing_entities"]) == 0
```

- [ ] **Step 3: 测试 graph 条件边 clarify 路径**

```python
# tests/agents/graph/test_graph.py 追加

def test_route_by_intent_returns_clarify():
    from src.agents.graph.state import AgentState, RAGQueryIntent
    from src.agents.graph.workflow import route_by_intent
    state = AgentState(
        query="营收多少",
        intent=RAGQueryIntent(route="medium"),
        missing_entities=[{"type": "year"}],
    )
    assert route_by_intent(state) == "clarify"

def test_route_by_intent_returns_normal():
    from src.agents.graph.state import AgentState, RAGQueryIntent
    state = AgentState(
        query="2024年营收多少",
        intent=RAGQueryIntent(route="medium"),
        missing_entities=[],
    )
    assert route_by_intent(state) == "medium"
```

- [ ] **Step 4: Run full test suite**

Run: `pytest tests/ -v`
Expected: all passed

- [ ] **Step 5: Run lint check**

Run: `ruff check .`
Expected: All checks passed!

- [ ] **Step 6: Commit**

```bash
git add tests/
git commit -m "test: add integrated tests for clarification flow"
```

---

### Task 12: 集成验证

- [ ] **Step 1: 运行完整测试套件**

Run: `pytest tests/ -v`
Expected: 全部通过

- [ ] **Step 2: 运行 lint 检查**

Run: `ruff check . --fix`
Expected: All checks passed!

- [ ] **Step 3: 检查遗留问题**

```bash
# 无遗留 print() 或 TODO
grep -rn "print(" src/ --include="*.py" | grep -v "__repr__\|# print"
grep -rn "TODO\|FIXME" src/ --include="*.py"
```

- [ ] **Step 4: 检查代码位置和层次**

```bash
# 确认 api/ 中没有引入 infra 层的 import
grep -rn "from src.infra" src/api/ --include="*.py"
# 应无输出或仅有现有合法的引用

# 确认新增模块都在 infra/search/ 中
ls src/infra/search/
# 应包含 entity_extractor.py, complexity_scorer.py, query_router.py
```

- [ ] **Step 5: 最终 commit**

```bash
git add -A
git commit -m "chore: final verification and cleanup for intent routing upgrade"
```

---

**Plan complete and saved to `docs/superpowers/plans/2026-07-29-intent-routing-upgrade.md`.**

Two execution options:

1. **Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration
2. **Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?
