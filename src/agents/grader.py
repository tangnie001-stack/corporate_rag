# src/agents/grader.py
"""检索质量评分器（规则版）。

使用关键词覆盖度评估检索结果质量。
分数范围 0~1，阈值 0.5。
"""

from loguru import logger
import jieba

from src.config import TOP_K_RERANK


class RetrievalGrader:
    """规则版检索质量评分器。

    策略：
    1. 提取查询中有意义的关键词（长度 >= 2 的非停用词）
    2. 检查关键词在精排后 Top-N 结果中的出现比例
    3. 返回覆盖度作为质量分数
    """

    KEYWORD_MIN_LEN = 2
    DEFAULT_PASS = 0.8

    def grade(self, query: str, results: list, reranked: list) -> float:
        """返回质量分数 0~1。

        Args:
            query: 用户查询
            results: 检索原始结果（当前未使用，保留接口签名）
            reranked: 精排后的结果列表

        Returns:
            float: 质量分数，< 0.5 认为不合格
        """
        tokens = jieba.lcut(query)
        keywords = [t for t in tokens if len(t) >= self.KEYWORD_MIN_LEN]

        if not keywords:
            return self.DEFAULT_PASS

        top_contents = [c.content for c in reranked[:TOP_K_RERANK]]
        if not top_contents:
            return 0.0

        covered = sum(
            1 for kw in keywords if any(kw in content for content in top_contents)
        )
        coverage = covered / len(keywords)
        logger.debug("RetrievalGrader: coverage={:.2f} keywords={}", coverage, keywords)
        return coverage
