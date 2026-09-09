"""SOURCE_TIER_RULES 与 resolve_source_tier 解析函数测试（source-tier-labeling）。"""

from src.config.const import (
    SOURCE_TIER_LABELS,
    SOURCE_TIER_RULES,
    resolve_source_tier,
)

WEB = "web"
KB = "kb"


class TestResolveSourceTier:
    """定档规则：KB=T0；域边界后缀匹配；最长后缀优先；模式升档；未命中 T3。"""

    def test_kb_kind_returns_t0(self):
        """KB 来源不走域名解析，固定 T0（内部文档）。"""
        assert resolve_source_tier("anything.pdf", KB) == 0

    def test_t1_domain_hit_including_subdomain(self):
        """子域命中规则域：static.www.tencent.com → T1。"""
        assert (
            resolve_source_tier("https://static.www.tencent.com/annual.pdf", WEB) == 1
        )

    def test_t4_ugc_hit(self):
        """zhuanlan.zhihu.com 命中 zhihu.com → T4。"""
        assert resolve_source_tier("https://zhuanlan.zhihu.com/p/123", WEB) == 4

    def test_domain_boundary_no_hit(self):
        """域边界：evil-zhihu.com 含规则子串但非其子域，不得命中 T4。"""
        assert resolve_source_tier("https://evil-zhihu.com/post", WEB) == 3

    def test_longest_suffix_priority(self):
        """最长后缀优先：guba.eastmoney.com(T4) 覆盖 eastmoney.com(T2)。"""
        assert resolve_source_tier("https://guba.eastmoney.com/news,123.html", WEB) == 4
        assert resolve_source_tier("https://www.eastmoney.com/a/1.html", WEB) == 2

    def test_gov_cn_pattern_t1(self):
        """.gov.cn 模式升 T1（清单未命中时兜底）。"""
        assert resolve_source_tier("http://www.mof.gov.cn/xx", WEB) == 1

    def test_edu_cn_pattern_t1(self):
        """.edu.cn 模式升 T1。"""
        assert resolve_source_tier("https://www.tsinghua.edu.cn/", WEB) == 1

    def test_unmatched_default_t3(self):
        """未命中清单与模式 → 中性默认档 T3，不报错不丢弃。"""
        assert resolve_source_tier("https://example.org/page?q=1", WEB) == 3

    def test_normalization_case_www_port_path(self):
        """大写/www 前缀/端口/路径不影响定档。"""
        assert resolve_source_tier("HTTPS://WWW.Tencent.COM:8443/a/b?q=1", WEB) == 1

    def test_labels_and_rules_consistent(self):
        """标签表覆盖全部档位；规则表值域合法。"""
        assert SOURCE_TIER_LABELS == {
            0: "内部文档",
            1: "官方一手",
            2: "权威媒体",
            3: "一般",
            4: "UGC",
        }
        assert all(t in SOURCE_TIER_LABELS for t in SOURCE_TIER_RULES.values())
