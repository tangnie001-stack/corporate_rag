"""提示词模板集 — 集中管理所有 LLM prompt。

本模块存放 RAG 问答链路中使用的所有提示词模板，包括：
  - 系统指令（约束 LLM 行为）
  - 用户消息模板（拼接参考文档 + 问题）
  - 其他供 LLM 调用的 prompt 片段

设计原则：
  1. 所有提示词集中在 prompts.py 中，而非散落在业务模块中
  2. 系统指令与用户模板分离，便于分别调试和优化
  3. 模板中的占位符统一使用 {var} 格式（str.format()）
  4. 每个常量必须有详细注释，说明用途和占位符含义

与 queries.py 的关系：
  queries.py 管理发给 MySQL 的 SQL 语句
  prompts.py 管理发给 LLM 的提示词
  两者互不依赖，但遵循相同的"常量集中管理"理念
"""

# ====== 系统指令 ======

# 金融问答系统提示词 — 约束 LLM 在 RAG 场景下的回答行为。
# 关键约束：禁止推算、要求标注年份、找不到时明确告知。
# 适用于所有 RAG 问答场景，作为 SystemMessage 发送。
FINANCIAL_SYSTEM_PROMPT: str = """你是一个专业金融文档分析师。请严格遵循以下规则：

1. 仅根据提供的文档内容回答，不要计算文档中没有直接给出的比率或汇总数据
2. 回答中必须标注数据对应的年份/报告期
3. 如果文档中找不到相关信息，明确说明"未在文档中找到相关数据"
4. 回答语言与用户提问语言一致"""

# ====== 用户消息模板 ======

# 用户提问时的完整消息模板，包含参考文档块和用户问题。
#
# 占位符说明：
#   {context} — 已格式化的参考文档字符串（由 RAGChain._format_context 生成）
#               格式为 "[1] 来源: xxx.pdf (第5页)\n内容: ..."
#   {query}   — 用户输入的原始查询文本
#
# 模板末尾的"未在文档中找到相关数据"与 FINANCIAL_SYSTEM_PROMPT 中第 3 条保持一致，
# 在 user message 层再次强调，降低 LLM 编造信息的倾向。
USER_PROMPT_TEMPLATE: str = """请根据以下文档内容回答问题。

【参考文档】
{context}

【问题】
{query}

请基于以上文档内容回答。如果文档中没有相关信息，请说明"未在文档中找到相关数据"。
"""

# ====== Classifier Prompt ======

# 查询分类系统提示词 — 引导 LLM 分析用户查询复杂度、补充缺失实体并确定路由。
# 适用于查询到达后的前置分类阶段，作为 SystemMessage 发送给分类器 LLM。
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

# 查询分类用户消息模板 — 拼接用户问题、提取的实体、复杂度评分和对话历史。
#
# 占位符说明：
#   {query}            — 用户输入的原始查询文本
#   {entities}         — 已提取的实体列表（字符串），正则匹配结果，可能为空
#   {complexity_score} — 规则预判的复杂度评分（浮点数）
#   {history}          — 最近 N 轮对话历史（字符串），用于多轮上下文推断
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


# ====== Abstention / 拒答 ======

# 拒答语检测关键词：回答命中任一关键词时，format_node 不输出引用
ABSTENTION_MARKERS: tuple[str, ...] = ("未在文档中找到",)

# abstention 出口的回答文案：检索无达标 context 时直接返回，不回 LLM
ABSTENTION_TEXT: str = "未在文档中找到相关数据。请尝试更换问题表述或补充更多文档。"


# ====== 实体抽取 ======

# 实体抽取系统提示词 — 引导 LLM 校验规则候选并补全盲区。
# 输入含文件名、标题树、正文前缀、规则候选；输出 JSON 三字段。
# 关键约束：规则结果与原文一致时保留，仅当原文无依据或明显不是实体时才纠正。
ENTITY_EXTRACTION_SYSTEM_PROMPT: str = """你是一个金融文档实体抽取专家。基于文件名、文档标题结构和正文片段，提取文档级实体。

核心实体（必须尽力提取）：company（公司名）、report_period（报告期，如"2025年第一季度"）、sec_code（证券代码）。
可选实体（顺带返回，不勉强）：person（人名）、currency（币种）、report_type（报表类型）。

规则层已给出候选结果，可能正确也可能错误。你的任务：
1. 校验规则候选：与原文一致则保留；原文无依据或明显不是实体（如描述性短语）则纠正
2. 补全规则漏掉的核心实体
3. 只返回 JSON，不要其他内容。"""

# 实体抽取用户消息模板。
# 占位符: {filename} 文件名; {heading_tree} 标题树; {text_prefix} 正文前缀; {rule_candidates} 规则候选
ENTITY_EXTRACTION_USER_TEMPLATE: str = """文件名：{filename}

文档标题结构：
{heading_tree}

正文开头：
{text_prefix}

规则层候选（可能正确也可能错误）：
{rule_candidates}

输出 JSON（严格按此格式）：
{{
  "rule_correct": true,
  "reason": "简要说明校验依据",
  "entities": {{
    "company": "公司名",
    "report_period": "报告期",
    "sec_code": "证券代码"
  }}
}}
"""
