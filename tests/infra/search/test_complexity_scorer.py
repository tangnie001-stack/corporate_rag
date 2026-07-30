"""测试复杂度加权评分器。"""

from src.infra.search.complexity_scorer import score_complexity
from src.infra.search.entity_extractor import ExtractedEntity


class TestScoreComplexity:
    """score_complexity 函数测试。"""

    def test_empty_query_returns_zero(self):
        """空查询返回 0.0。"""
        assert score_complexity("", []) == 0.0
        assert score_complexity("   ", []) == 0.0

    def test_greeting_scores_low(self):
        """问候语得分为 LOW 级别。"""
        score = score_complexity("你好", [])
        assert 0 < score <= 2

    def test_medium_keyword(self):
        """中等关键词得分 >= 2。"""
        score = score_complexity("计算2024年营收", [])
        assert score >= 2

    def test_high_keyword(self):
        """高级关键词：对比A和B的差异 >= 5（对比3 + 差异3 + 和2 = 8）。"""
        score = score_complexity("对比A和B的差异", [])
        assert score >= 5

    def test_entities_increase_score(self):
        """有实体时得分更高。"""
        score_no_entity = score_complexity("计算2024年营收", [])
        entities = [
            ExtractedEntity(type="year", value="2024"),
        ]
        score_with_entity = score_complexity("计算2024年营收", entities)
        assert score_with_entity > score_no_entity

    def test_and_conjunction(self):
        """含"和"的查询得分 >= 2。"""
        score = score_complexity("营收和利润", [])
        assert score >= 2
