"""文档级实体抽取器测试。

覆盖：
  - 文件名正则提取（company/year/quarter）
  - 标题栈规则层（year/quarter/report_type）
  - LLM 兜底三态开关（on/off/auto）+ 非法值兜底
  - 可选实体不被过滤 + LLM 失败降级规则结果
"""

from src.infra.search.document_entity_extractor import (
    DocumentEntityExtractor,
    extract_from_filename,
    extract_from_headings,
)


class TestExtractFromFilename:
    def test_neusoft_filename(self):
        entities = extract_from_filename("neusoft_2025_q1.pdf")
        assert entities.get("company") == "neusoft"
        assert entities.get("year") == "2025"
        assert entities.get("quarter") == "Q1"

    def test_tencent_filename(self):
        entities = extract_from_filename("tencent_2024_annual.pdf")
        assert entities.get("company") == "tencent"
        assert entities.get("year") == "2024"

    def test_report_name(self):
        entities = extract_from_filename("report.pdf")
        assert entities == {}


class TestExtractFromHeadings:
    def test_neusoft_headings(self):
        heading_tree = [(1, "2025 年第一季度报告"), (1, "一、主要财务数据")]
        entities = extract_from_headings(heading_tree)
        assert entities.get("year") == "2025"
        assert entities.get("quarter") in ("一", "Q1")

    def test_report_type_extracted(self):
        heading_tree = [(2, "（一）资产负债表")]
        entities = extract_from_headings(heading_tree)
        assert entities.get("report_type") == "资产负债表"


class TestLLMFallback:
    class _FakeLLM:
        """mock LLM：返回固定 JSON 内容。"""

        def __init__(self, content: str):
            self._content = content

        def invoke(self, messages, **kwargs):
            class _Resp:
                content = None

            r = _Resp()
            r.content = self._content
            return r

    class _BrokenLLM:
        """mock LLM：invoke 抛异常，验证降级到规则结果。"""

        def invoke(self, messages, **kwargs):
            raise RuntimeError("llm unavailable")

    def test_auto_mode_skips_when_rules_complete(self, monkeypatch):
        """规则层已抽齐核心实体时，auto 模式不调 LLM，结果即规则结果。"""
        from src.config import settings

        monkeypatch.setattr(settings, "ENTITY_LLM_FALLBACK", "auto")
        extractor = DocumentEntityExtractor(llm=self._FakeLLM('{"entities": {}}'))
        # neusoft 文件名给出 company/year，正文给出 sec_code/report_period
        text = "证券代码：600718\n2025 年第一季度"
        heading_tree = [(1, "2025 年第一季度报告")]
        entities = extractor.extract("neusoft_2025_q1.pdf", heading_tree, text, "pdf")
        assert entities.get("company") == "neusoft"

    def test_auto_mode_triggers_when_missing_core(self, monkeypatch):
        """auto 模式规则缺核心实体时触发 LLM，由 LLM 补全。"""
        from src.config import settings

        monkeypatch.setattr(settings, "ENTITY_LLM_FALLBACK", "auto")
        llm = self._FakeLLM('{"entities": {"report_period": "2025年第一季度"}}')
        extractor = DocumentEntityExtractor(llm=llm)
        entities = extractor.extract("report.pdf", [], "无证券代码 无年份", "pdf")
        assert entities.get("report_period") == "2025年第一季度"

    def test_off_mode_never_calls_llm(self, monkeypatch):
        """off 模式不调 LLM，规则结果原样返回。"""
        from src.config import settings

        monkeypatch.setattr(settings, "ENTITY_LLM_FALLBACK", "off")
        extractor = DocumentEntityExtractor(llm=self._FakeLLM('{"entities": {"company": "X"}}'))
        entities = extractor.extract("report.pdf", [], "无内容", "pdf")
        assert "company" not in entities

    def test_invalid_mode_treated_as_auto(self, monkeypatch):
        """非法三态值应按 auto 处理：规则缺核心实体时仍调 LLM。"""
        from src.config import settings

        monkeypatch.setattr(settings, "ENTITY_LLM_FALLBACK", "invalid_mode")
        llm = self._FakeLLM('{"entities": {"company": "某公司"}}')
        extractor = DocumentEntityExtractor(llm=llm)
        entities = extractor.extract("report.pdf", [], "无内容", "pdf")
        assert entities.get("company") == "某公司"

    def test_optional_entity_kept_from_llm(self, monkeypatch):
        """LLM 返回的可选实体（report_type）不应被过滤丢弃。"""
        from src.config import settings

        monkeypatch.setattr(settings, "ENTITY_LLM_FALLBACK", "auto")
        llm = self._FakeLLM(
            '{"entities": {"company": "某公司", "report_type": "资产负债表"}}'
        )
        extractor = DocumentEntityExtractor(llm=llm)
        entities = extractor.extract("report.pdf", [], "无内容", "pdf")
        assert entities.get("company") == "某公司"
        assert entities.get("report_type") == "资产负债表"

    def test_llm_failure_falls_back_to_rule_result(self, monkeypatch):
        """LLM 兜底失败时静默降级为规则结果，不抛异常。"""
        from src.config import settings

        monkeypatch.setattr(settings, "ENTITY_LLM_FALLBACK", "auto")
        extractor = DocumentEntityExtractor(llm=self._BrokenLLM())
        # txt 不走标题层；文件名给出 company/year，正文给出 sec_code，
        # 缺 report_period 触发 LLM → 失败 → 返回规则结果
        entities = extractor.extract("neusoft_2025_q1.pdf", [], "证券代码：600718", "txt")
        assert entities.get("company") == "neusoft"
        assert entities.get("sec_code") == "600718"
