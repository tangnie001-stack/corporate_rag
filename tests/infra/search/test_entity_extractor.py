"""EntityExtractor 正则实体提取器的单元测试。

测试覆盖：
  - 年份提取
  - 财务指标提取
  - 季度提取
  - 金额提取
  - 百分比提取
  - 公司名称提取
  - 无匹配返回空列表
  - 多实体同时提取
"""

from src.infra.search.entity_extractor import EntityExtractor


def test_extract_year() -> None:
    """应提取年份 2024。"""
    extractor = EntityExtractor()
    entities = extractor.extract("2024年营收多少")
    years = [e for e in entities if e.type == "year"]
    assert len(years) == 1
    assert years[0].value == "2024"


def test_extract_metric() -> None:
    """应提取财务指标 毛利率。"""
    extractor = EntityExtractor()
    entities = extractor.extract("毛利率是多少")
    metrics = [e for e in entities if e.type == "metric"]
    assert len(metrics) == 1
    assert metrics[0].value == "毛利率"


def test_extract_quarter() -> None:
    """应提取季度信息。"""
    extractor = EntityExtractor()
    entities = extractor.extract("一季度净利润多少")
    quarters = [e for e in entities if e.type == "quarter"]
    assert len(quarters) == 1


def test_extract_money() -> None:
    """应提取金额信息。"""
    extractor = EntityExtractor()
    entities = extractor.extract("营收100亿")
    monies = [e for e in entities if e.type == "money"]
    assert len(monies) == 1


def test_extract_percentage() -> None:
    """应提取百分比信息。"""
    extractor = EntityExtractor()
    entities = extractor.extract("毛利率15%")
    percentages = [e for e in entities if e.type == "percentage"]
    assert len(percentages) == 1


def test_extract_company() -> None:
    """应提取公司名称。"""
    extractor = EntityExtractor()
    entities = extractor.extract("腾讯公司2024年营收")
    companies = [e for e in entities if e.type == "company"]
    assert len(companies) == 1


def test_no_match_returns_empty() -> None:
    """无匹配时应返回空列表。"""
    extractor = EntityExtractor()
    entities = extractor.extract("你好")
    assert entities == []


def test_multiple_entities() -> None:
    """应同时提取多种实体。"""
    extractor = EntityExtractor()
    entities = extractor.extract("2023年腾讯净利润100亿")
    types = {e.type for e in entities}
    assert "year" in types
    assert "company" in types
    assert "metric" in types
    assert "money" in types
