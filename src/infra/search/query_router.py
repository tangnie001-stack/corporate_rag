"""查询意图路由器模块 — 根据查询内容分类路由到不同处理策略。

采用混合策略：
  - L0: 基于正则表达式的快速规则匹配（simple / vague / medium）
  - L3: LLM 分类兜底（当前为 stub，返回预设 fallback）

分类结果：
  - simple: 简单财务数据查询，可直接用 LLM 回答
  - vague: 模糊/短查询，需结合历史上下文改写
  - medium: 中等复杂度查询，需 RAG 检索
  - complex: 复杂查询（当前 fallback 至 medium）
"""

import re


class QueryRouter:
    """查询意图路由器 — 使用规则匹配 + LLM 兜底分类查询意图。

    Attributes:
        SIMPLE_PATTERNS: 匹配简单财务查询的正则表达式列表
        VAGUE_PATTERNS: 匹配模糊/短查询的正则表达式列表
        MEDIUM_PATTERNS: 匹配中等复杂度查询的正则表达式列表
    """

    SIMPLE_PATTERNS = [
        r"\d{4}年.*(营收|利润|收入|成本|资产|负债|现金流)",
        r"(营收|利润|收入|成本).*[多几多少]",
        r"[多几多少].*(钱|元|亿|万|率)",
    ]

    VAGUE_PATTERNS = [
        r"^(营收|利润|收入|成本|资产|负债|现金流|净利润|毛利率)$",
        r"^(这个|那|该).*(公司|企业|项目).*(怎么|如何|呢|吗)",
        r"^(帮|请|麻烦|帮我).*看[一看]",
    ]

    MEDIUM_PATTERNS = [
        r"(分析|解释|说明|为什么|原因)",
        r"(对比|比较|差异|区别|versus|vs)",
    ]

    def __init__(self, fallback: str = "medium") -> None:
        """初始化路由器。

        Args:
            fallback: 当规则和 LLM 都无法分类时的默认返回类型
        """
        self._fallback = fallback
        self._cache: dict[str, str] = {}

    def route(self, query: str) -> str:
        """对查询进行分类路由。

        分类优先级：L0 规则 (simple → vague → medium) → L3 LLM 兜底

        Args:
            query: 用户查询文本

        Returns:
            分类标签：simple / vague / medium / complex
        """
        if query in self._cache:
            return self._cache[query]

        for p in self.SIMPLE_PATTERNS:
            if re.search(p, query):
                return "simple"

        for p in self.VAGUE_PATTERNS:
            if re.search(p, query):
                return "vague"

        for p in self.MEDIUM_PATTERNS:
            if re.search(p, query):
                return "medium"

        result = self._llm_classify(query)
        self._cache[query] = result
        return result

    def _llm_classify(self, query: str) -> str:
        """LLM 分类兜底（当前为 stub，返回预设 fallback）。

        Args:
            query: 用户查询文本（当前未使用，但保留接口签名）

        Returns:
            self._fallback 的值
        """
        return self._fallback
