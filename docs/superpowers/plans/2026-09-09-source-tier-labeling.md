# source-tier-labeling Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task（2026-09-09 拷问确认：本会话 inline 顺序执行，每 Task 完成后设检查点；Task 6 另需调用 `/frontend-design`）. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给聊天引用来源做权威分级（T0-T4），tier 从规则表全链路透传到模型 context 与前端徽标，并补幻觉引用编号观测信号。

**Architecture:** 域名规则表确定性定档（`const.py` 单函数 `resolve_source_tier`）→ 三个 `RAGContext` 构造点显式赋 tier → 两处 context 渲染点追加档位标注（模型感知）→ citations dict / SSE payload / 落库 dict 四处携带 tier → chat.html 按 api_contract 权威映射渲染徽标。候选规则信号为离线 SQL + 人工审核（零新模块）。

**Tech Stack:** Python 3.11 / FastAPI / LangGraph / pytest / Loguru / 原生 JS（chat.html）

**Spec:** `docs/openspec/changes/source-tier-labeling/`（proposal.md / design.md / specs/ / tasks.md — 设计决策 D1-D6 与 Risks 以 design.md 为准）

## Global Constraints

- 中文注释；所有函数必须写 docstring；dataclass 新增字段必须加行内注释（来源/范围/用途）
- 不用三元表达式（`a if cond else b`），写完整 if/else
- 硬编码集中管理：规则表/标签/阈值只放 `src/config/const.py`，业务代码零散常量迁入
- 类型不确定的值不用 `getattr(x, "attr", default)` 隐式兜底，用显式判断
- 单文件超 400 行必须拆分（`nodes.py` / `agent_service.py` 已接近红线，只做手术刀插入，不加无关代码）
- 日志：事件消息英文 k=v + `[层名]` 前缀；行为信号用 `core_logging.retrieval_signal` helper，不手拼（规范见 docs/agents/logging-rules.md）
- 测试 mock 外部依赖（tavily/LLM/DB），不发起真实网络调用
- 质量门禁：`pytest tests/ -v` 全过、`ruff check .` 无错误、`pyright src/` 不引入新 error
- 改公共响应结构必须同步受影响测试断言（本计划 Task 5 含一处已知断言更新）
- RAGContext.tier 默认 `None`，**不设 T0 兜底默认**（design D6：防漏传时 web 来源静默标最高信任档）

## 执行基线（2026-09-09 拷问确认 Q1-Q5）

- **执行方式**：本会话 inline 顺序执行（executing-plans），每 Task 完成后设检查点；Task 6 前端调用 `/frontend-design`
- **分支与提交**：实施开始前先提交一个 docs 基线 commit（openspec 产物 / 本 plan / 设计规格与 mockup / MASTER.md 变更），全程当前分支，每 Task 一 commit
- **种子清单**：按 Task 1 代码块直接落库（不再执行「LLM 拟候选 + 人工校对」预演），清单成长走候选规则信号流程（Task 7 cookbook）
- **端到端验证**：Task 7 Step 2 由执行者直接跑（含真实 Tavily 提问与 `docker compose restart`）；若环境不可用降级为代码级验证，并在 cookbook 登记遗留验证步骤
- **RAGAS 基线分界**：登记进 cookbook（并入 Task 7 文档步骤），实现 to_prompt_text 标注当天记录

---

### Task 1: 规则表与解析函数（const.py）

**Files:**
- Modify: `src/config/const.py`（文件末尾追加）
- Test: `tests/config/test_source_tier.py`（新建）

**Interfaces:**
- Consumes: `SSEInteractionTexts.CITATION_KIND_KB` / `CITATION_KIND_WEB`（const.py 已有）
- Produces: `SOURCE_TIER_KB: int`（=0）、`SOURCE_TIER_DEFAULT: int`（=3）、`SOURCE_TIER_RULES: dict[str, int]`、`SOURCE_TIER_PATTERN_SUFFIXES: tuple[str, ...]`、`SOURCE_TIER_LABELS: dict[int, str]`、`resolve_source_tier(url: str, kind: str) -> int`（后续所有任务依赖此签名）

- [ ] **Step 1: 写失败测试**

新建 `tests/config/test_source_tier.py`：

```python
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
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/config/test_source_tier.py -v`
Expected: FAIL，`ImportError: cannot import name 'SOURCE_TIER_LABELS'`

- [ ] **Step 3: 实现**

`src/config/const.py` 文件末尾追加：

```python
# ── 来源权威分级（source-tier-labeling change）──
# KB 内部文档固定档：resolve_source_tier 对 kind=kb 返回，不走域名解析
SOURCE_TIER_KB: int = 0
# web 未命中清单与模式时的默认中性档（非差评）
SOURCE_TIER_DEFAULT: int = 3
# 规则域 → 档位（人工维护先验；增补走候选规则审核流程，见 cookbook）。
# 匹配语义：域边界后缀（== 或 endswith("." + rule)），最长后缀优先
SOURCE_TIER_RULES: dict[str, int] = {
    # T1 官方一手
    "tencent.com": 1,
    "cninfo.com.cn": 1,
    "sse.com.cn": 1,
    "szse.cn": 1,
    # T2 权威财经媒体
    "caixin.com": 2,
    "yicai.com": 2,
    "wallstreetcn.com": 2,
    "sina.com.cn": 2,
    "finance.sina.com.cn": 2,
    "eastmoney.com": 2,
    # T4 UGC
    "zhihu.com": 4,
    "xueqiu.com": 4,
    "weibo.com": 4,
    "guba.eastmoney.com": 4,
}
# 官方/教育机构域名模式：清单未命中时命中模式升 T1
SOURCE_TIER_PATTERN_SUFFIXES: tuple[str, ...] = (".gov.cn", ".edu.cn")
# 档位 → 中文标签（权威映射唯一来源；api_contract.md 同步登记，前端照抄勿另造文案，
# 改动动线：const.py → api_contract.md → chat.html 三步走完才算改完）
SOURCE_TIER_LABELS: dict[int, str] = {
    0: "内部文档",
    1: "官方一手",
    2: "权威媒体",
    3: "一般",
    4: "UGC",
}


def _extract_domain(url: str) -> str:
    """从 url 提取归一化域名。

    去 scheme/路径/query/userinfo/端口与 www. 前缀，转小写。

    Args:
        url: 来源 url

    Returns:
        归一化后的域名字符串
    """
    host = url.strip().lower()
    if "://" in host:
        host = host.split("://", 1)[1]
    host = host.split("/", 1)[0]  # 剥路径与 query
    host = host.split("@")[-1]  # 剥 userinfo
    host = host.rsplit(":", 1)[0]  # 剥端口
    if host.startswith("www."):
        host = host[4:]
    return host


def resolve_source_tier(url: str, kind: str) -> int:
    """来源权威确定性定档。

    KB kind 固定 T0；web 按规则域匹配（域边界后缀、最长后缀优先），
    官方/教育域名模式升 T1，未命中默认中性档 T3。同一 url 恒定输出，
    不随模型采样变化（模型判断不改写档位，design D1）。

    Args:
        url: 来源 url（KB 来源可为文件名，不参与解析）
        kind: 来源类型（SSEInteractionTexts.CITATION_KIND_KB / CITATION_KIND_WEB）

    Returns:
        档位整数（0=内部文档 1=官方一手 2=权威媒体 3=一般 4=UGC）
    """
    if kind == SSEInteractionTexts.CITATION_KIND_KB:
        return SOURCE_TIER_KB
    domain = _extract_domain(url)
    matches = [
        rule
        for rule, tier in SOURCE_TIER_RULES.items()
        if domain == rule or domain.endswith("." + rule)
    ]
    if matches:
        # 最长后缀优先：guba.eastmoney.com(T4) 覆盖 eastmoney.com(T2)
        best = max(matches, key=len)
        return SOURCE_TIER_RULES[best]
    for suffix in SOURCE_TIER_PATTERN_SUFFIXES:
        if domain.endswith(suffix):
            return 1
    return SOURCE_TIER_DEFAULT
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/config/test_source_tier.py -v`
Expected: PASS 全部

- [ ] **Step 5: Commit**

```bash
git add src/config/const.py tests/config/test_source_tier.py
git commit -m "feat: 来源权威分级规则表与 resolve_source_tier 解析函数"
```

---

### Task 2: RAGContext.tier 字段与 to_prompt_text 档位标注

**Files:**
- Modify: `src/rag/context.py`（RAGContext 字段 + to_prompt_text）
- Test: `tests/rag/test_retrieval.py`（文件末尾追加测试类）

**Interfaces:**
- Consumes: `SOURCE_TIER_LABELS`（Task 1）
- Produces: `RAGContext.tier: int | None = None`；`to_prompt_text()` 在 tier 非 None 时输出 `来源: xxx (第N页, 档位标签)`。后续 Task 3/4 依赖该字段名 `tier`

- [ ] **Step 1: 写失败测试**

`tests/rag/test_retrieval.py` 末尾追加（import 区若无 `RAGContext` 则补 `from src.rag.context import RAGContext`——该文件现未导入，漏补会 NameError）：

```python
# ==================== tier 字段与档位标注测试（source-tier-labeling）====================


class TestTierAnnotation:
    """to_prompt_text 档位标注：tier 非 None 追加标签，None 保持存量格式。"""

    def _make(self, tier=None):
        return RAGContext(
            content="内容",
            source="a.pdf",
            page=1,
            doc_id="d1",
            chunk_id="c1",
            tier=tier,
        )

    def test_kb_tier_annotation(self):
        """KB（tier=0）标注「内部文档」，并入页码括注。"""
        assert "(第1页, 内部文档)" in self._make(tier=0).to_prompt_text()

    def test_web_tier_annotation(self):
        """web tier=2 标注「权威媒体」。"""
        assert "(第1页, 权威媒体)" in self._make(tier=2).to_prompt_text()

    def test_tier_none_keeps_legacy_format(self):
        """tier=None（存量/未定档）保持原格式，无标签。"""
        text = self._make(tier=None).to_prompt_text()
        assert "来源: a.pdf (第1页)" in text
        assert "内部文档" not in text

    def test_default_tier_is_none(self):
        """默认值为 None（design D6：不设 T0 兜底，防漏传错标最高信任档）。"""
        assert self._make().tier is None
```

注意：`_make` 里 `tier=tier` 依赖字段已存在，测试先行会因 `TypeError: unexpected keyword argument 'tier'` 失败——这正是预期失败模式。

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/rag/test_retrieval.py::TestTierAnnotation -v`
Expected: FAIL，`TypeError: __init__() got an unexpected keyword argument 'tier'`

- [ ] **Step 3: 实现**

`src/rag/context.py` 三处修改：

3a. import 行追加 `SOURCE_TIER_LABELS`（`from src.config.const import (...)` 中加一项）。

3b. `RAGContext` 字段区（`kind` 字段之后）追加：

```python
    tier: int | None = None  # 来源权威档位（resolve_source_tier 产出：0=内部文档/1=官方/2=媒体/3=一般/4=UGC）；None=未定档（存量数据/防御默认），前端不显示徽标
```

3c. `to_prompt_text` 的首行 parts 构造由

```python
        parts = [f"来源: {self.source} (第{self.page}页)"]
```

改为：

```python
        page_part = f"第{self.page}页"
        if self.tier is not None:
            label = SOURCE_TIER_LABELS.get(self.tier, "")
            if label:
                page_part = f"{page_part}, {label}"
        parts = [f"来源: {self.source} ({page_part})"]
```

同时更新 `to_prompt_text` docstring：补一句「tier 非 None 时来源括注内追加档位标签（KB=内部文档；与生产/RAGAS 共用，标注后为评估基线分界点）」。

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/rag/test_retrieval.py -v`
Expected: PASS（含存量测试，格式未变）

- [ ] **Step 5: Commit**

```bash
git add src/rag/context.py tests/rag/test_retrieval.py
git commit -m "feat: RAGContext.tier 字段与 to_prompt_text 档位标注"
```

---

### Task 3: 三个构造点显式赋 tier + web 块文本标注

**Files:**
- Modify: `src/rag/retrieval.py:169`（KB 构造点）
- Modify: `src/agents/tools/rag_tools.py:156`（KB 超时降级构造点）
- Modify: `src/agents/tools/web_tools.py:132` 与 `:141`（web 构造点 + 块文本）
- Test: `tests/agents/tools/test_web_search.py`（追加 + 更新一处既有断言）；`tests/rag/test_retrieval.py`（追加）；`tests/agents/tools/test_rag_tools.py`（追加）

**Interfaces:**
- Consumes: `resolve_source_tier` / `SOURCE_TIER_LABELS`（Task 1）、`RAGContext.tier`（Task 2）
- Produces: 所有 `RAGContext` 构造点 tier 均已赋值（KB=T0、web=解析结果）；web 工具返回文本含 `(档位标签)`

- [ ] **Step 1: 写失败测试**

`tests/agents/tools/test_web_search.py` 末尾追加（复用该文件既有 `ctx` fixture；`search_web` 是 coroutine-only 工具，必须用 `ainvoke`，同步 `.invoke` 会抛 TypeError）：

```python
# ==================== tier 赋值与块文本标注（source-tier-labeling）====================


@pytest.mark.asyncio
async def test_search_web_assigns_tier_t4_and_annotates(monkeypatch, ctx):
    """命中规则域的 web 结果定档并写入块文本（zhihu.com → T4/UGC）。"""

    async def _t4_search(query, top_k=5, timeout=5.0, transport=None):
        return [
            {
                "url": "https://zhuanlan.zhihu.com/p/1",
                "title": "Z",
                "content": "帖子内容",
                "score": 0.9,
            }
        ]

    async def _t4_extract(urls, timeout=5.0, transport=None):
        return [{"url": u, "content": f"{u} 正文"} for u in urls]

    monkeypatch.setattr(web_tools, "tavily_search", _t4_search)
    monkeypatch.setattr(web_tools, "tavily_extract", _t4_extract)

    out = await search_web.ainvoke({"queries": ["知乎 帖子"]})

    web_ctx = ctx.tool_contexts[-1]
    assert web_ctx.tier == 4  # zhuanlan.zhihu.com 命中 zhihu.com T4
    assert "(UGC)" in out  # 块文本带档位标注


@pytest.mark.asyncio
async def test_search_web_assigns_tier_default_t3(monkeypatch, ctx):
    """未命中域名的 web 结果默认中性档 T3/一般（复用文件顶部 _fake_tavily_search 的 a.com/b.com）。"""
    monkeypatch.setattr(web_tools, "tavily_search", _fake_tavily_search)
    monkeypatch.setattr(web_tools, "tavily_extract", _fake_tavily_extract)

    out = await search_web.ainvoke({"queries": ["测试问题"]})

    web_ctx = ctx.tool_contexts[-1]
    assert web_ctx.tier == 3  # a.com 不在规则表 → T3
    assert "(一般)" in out
```

`tests/rag/test_retrieval.py` 追加 KB 构造点断言（打开该文件现有 rerank 测试，复制其 mock 夹具构造方式——mock vector_store 返回带 metadata 的结果后调 `rerank_results`）：

```python
def test_kb_construction_assigns_tier_zero():
    """KB 构造点（rerank_results）显式产出 T0（内部文档）。"""
    # 夹具照抄本文件现有 rerank 测试的构造（mock 结果 + metadata），断言：
    # contexts = rerank_results(...)
    # assert contexts[0].tier == 0
```

执行说明：`assert contexts[0].tier == 0` 一行按现有 rerank 测试的真实夹具变量名落进测试体；断言本身如上。rag_tools 超时降级分支同理在 `tests/agents/tools/test_rag_tools.py` 中复用其既有超时 mock 模式断言降级分支产出的 `RAGContext.tier == 0`。

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/agents/tools/test_web_search.py -v tests/rag/test_retrieval.py -v`
Expected: FAIL（`AttributeError: 'RAGContext' object has no attribute 'tier'` 或断言 tier 为 None）

- [ ] **Step 3: 实现**

3a. `src/rag/retrieval.py:169` 的 `RAGContext(...)` 构造追加参数（import 区补 `SOURCE_TIER_KB` 或 `resolve_source_tier` 与 `SSEInteractionTexts`，若未导入）：

```python
                tier=resolve_source_tier(
                    r.metadata.get("source", ""),
                    SSEInteractionTexts.CITATION_KIND_KB,
                ),
```

3b. `src/agents/tools/rag_tools.py:156` 超时降级分支的 `RAGContext(...)` 同样追加（import 同上）：

```python
                        tier=resolve_source_tier(
                            r.metadata.get("source", ""),
                            SSEInteractionTexts.CITATION_KIND_KB,
                        ),
```

3c. `src/agents/tools/web_tools.py`：import 区补 `SOURCE_TIER_LABELS, resolve_source_tier`；`:132` 构造追加 `tier=resolve_source_tier(r["url"], SSEInteractionTexts.CITATION_KIND_WEB),`；`:141` 块文本改为：

```python
        tier = resolve_source_tier(r["url"], SSEInteractionTexts.CITATION_KIND_WEB)
```

并把 tier 赋值与块文本统一（构造点与块文本共用同一 tier 变量，避免两次解析）：

```python
        tier = resolve_source_tier(r["url"], SSEInteractionTexts.CITATION_KIND_WEB)
        collector.append(
            RAGContext(
                content=content,
                source=r["url"],
                page=0,
                doc_id=r["url"],
                chunk_id=r["url"],
                kind=SSEInteractionTexts.CITATION_KIND_WEB,
                tier=tier,
            )
        )
        blocks.append(
            f"[{offset + len(blocks) + 1}] 来源: {r['url']}"
            f" ({SOURCE_TIER_LABELS[tier]})\n内容: {content}"
        )
```

- [ ] **Step 4: 更新受影响的既有断言，运行测试确认通过**

先同步既有断言（块文本插入 `(一般)` 后，`test_web_search.py:113` 的整段前缀断言会失败——`q1.com` 未命中规则表定档 T3）：

```python
    # 原：assert out.startswith("[1] 来源: https://q1.com/1\n内容: https://q1.com/1 正文")
    assert out.startswith("[1] 来源: https://q1.com/1 (一般)\n内容: https://q1.com/1 正文")
```

（`:52` `startswith("[2] 来源: https://a.com")` 与 `:114`/`:115` 的 `in` 断言是子串匹配，不受标注影响，无需改。）

Run: `pytest tests/agents/tools/ tests/rag/test_retrieval.py -v`
Expected: PASS 全部

- [ ] **Step 5: Commit**

```bash
git add src/rag/retrieval.py src/agents/tools/rag_tools.py src/agents/tools/web_tools.py tests/agents/tools/ tests/rag/test_retrieval.py
git commit -m "feat: RAGContext 三个构造点显式赋 tier，web 块文本带档位标注"
```

---

### Task 4: format_node citations 携带 tier + 非法编号观测信号

**Files:**
- Modify: `src/core/log_events.py`（Signal 枚举加成员）
- Modify: `src/agents/graph/nodes.py`（format_node）
- Test: `tests/agents/graph/test_graph.py`（追加）

**Interfaces:**
- Consumes: `RAGContext.tier`（Task 2）、`core_logging.retrieval_signal(signal, query, iteration, **fields)`
- Produces: citations dict 新增键 `"tier"`（Task 5 依赖）；`Signal.INVALID_CITATION = "invalid_citation"`（信号消费方：日志检索 `signal=invalid_citation`）

- [ ] **Step 1: 写失败测试**

`tests/agents/graph/test_graph.py` 末尾追加（文件顶部已有 `format_node`、`AgentState`、`RAGContext` 导入，补 `from src.core import logging as core_logging` 与 `from src.core.log_events import Signal`）：

```python
# ==================== tier 透传与非法编号信号（source-tier-labeling）====================


def _one_ctx(source="a.pdf"):
    return RAGContext(
        content="内容", source=source, page=1, doc_id="d1", chunk_id="c1", score=0.9
    )


def test_format_node_citation_carries_tier():
    """citations dict 携带来源 tier（None 透传为 None，非 None 原样透传）。"""
    state = AgentState(
        answer="内容[1][2]",
        tool_contexts=[
            _one_ctx(source="x.pdf"),
            RAGContext(
                content="网页",
                source="https://www.tencent.com/a",
                page=0,
                doc_id="u",
                chunk_id="u",
                score=0.8,
                tier=1,
            ),
        ],
    )
    result = format_node(state)
    assert result["citations"][0]["tier"] is None
    assert result["citations"][1]["tier"] == 1


def test_format_node_invalid_citation_signal(monkeypatch):
    """超范围编号产出 invalid_citation 信号（含去重 ids 与出现次数 count），不进 citations。"""
    recorded = []
    monkeypatch.setattr(
        core_logging, "retrieval_signal", lambda *a, **k: recorded.append((a, k))
    )
    state = AgentState(answer="内容[9][9][99]", tool_contexts=[_one_ctx()])
    result = format_node(state)
    assert result["citations"] == []  # 既有行为不变
    assert len(recorded) == 1  # 聚合为一条信号，不逐次记录
    args, kwargs = recorded[0]
    assert args[0] == Signal.INVALID_CITATION
    assert kwargs["count"] == 3  # [9]×2 + [99]×1
    assert kwargs["ids"] == "9|99"  # 去重升序 pipe join


def test_format_node_valid_citations_no_invalid_signal(monkeypatch):
    """全部编号合法时不产出 invalid_citation 信号。"""
    recorded = []
    monkeypatch.setattr(
        core_logging, "retrieval_signal", lambda *a, **k: recorded.append((a, k))
    )
    state = AgentState(answer="内容[1]", tool_contexts=[_one_ctx()])
    format_node(state)
    assert all(args[0] != Signal.INVALID_CITATION for args, _ in recorded)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/agents/graph/test_graph.py -v -k "tier or invalid_citation"`
Expected: FAIL（KeyError: 'tier' / AttributeError: INVALID_CITATION）

- [ ] **Step 3: 实现**

3a. `src/core/log_events.py` 的 `Signal` 枚举加成员（Signal 无 EVENT_SPECS 校验约束，仅加枚举即可）：

```python
    INVALID_CITATION = "invalid_citation"
```

3b. `src/agents/graph/nodes.py` `format_node` 中，把：

```python
    cited_numbers = {int(m) for m in re.findall(r"\[(\d+)\]", answer)}
    valid_numbers = {n for n in cited_numbers if 1 <= n <= len(contexts)}
    if not valid_numbers:
        core_logging.log_event(Event.FORMAT_DONE, citations=0, reason="no_markers")
        return {"citations": []}
```

替换为：

```python
    raw_numbers = [int(m) for m in re.findall(r"\[(\d+)\]", answer)]
    cited_numbers = set(raw_numbers)
    valid_numbers = {n for n in cited_numbers if 1 <= n <= len(contexts)}
    # 幻觉编号观测信号（design D5）：聚合一条（ids=去重升序，count=出现总次数），防日志噪音
    invalid_numbers = sorted(cited_numbers - valid_numbers)
    if invalid_numbers:
        invalid_count = sum(1 for n in raw_numbers if n in set(invalid_numbers))
        if hasattr(state, "query"):
            invalid_query = state.query
        else:
            invalid_query = ""
        core_logging.retrieval_signal(
            Signal.INVALID_CITATION,
            invalid_query,
            0,
            kb_id="",
            count=invalid_count,
            ids="|".join(str(n) for n in invalid_numbers),
        )
    if not valid_numbers:
        core_logging.log_event(Event.FORMAT_DONE, citations=0, reason="no_markers")
        return {"citations": []}
```

import 区补 `Signal`（`from src.core.log_events import Event, Signal`——已导入则无需改）。

3c. citations dict（`"kind": ctx.kind,` 之后）追加一行：

```python
                "tier": ctx.tier,
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/agents/graph/test_graph.py -v`
Expected: PASS（含存量 format 测试——现有断言不校验多余键，不受影响）

- [ ] **Step 5: Commit**

```bash
git add src/core/log_events.py src/agents/graph/nodes.py tests/agents/graph/test_graph.py
git commit -m "feat: citations 携带 tier，超范围引用编号记 invalid_citation 信号"
```

---

### Task 5: SSE citation 链路四处携带 tier（含 wire 序列化与还原路径）

**Files:**
- Modify: `src/utils/sse.py:51-78`（SSECitationEvent 字段 + payload_for_buffer）、`:293-329`（`sse_citation()` wire 序列化——实时帧最终发出的 `data:` 行在此拼装，漏改则前端永远拿不到 tier）、`:468-478`（`to_sse` citation 分支解构透传）、`:540-549`（`from_payload` 还原——续传/缓冲回放路径，漏改则 tier 静默丢成 None）
- Modify: `src/services/agent_service.py:315`（事件构造）与 `:505`（落库 dict）
- Test: `tests/utils/test_sse.py`（追加）、`tests/utils/test_sse_roundtrip.py`（CASES 补 tier 用例）、`tests/services/test_agent_service.py`（追加 + 更新一处既有断言）

**Interfaces:**
- Consumes: citations dict 的 `"tier"` 键（Task 4）
- Produces: `SSECitationEvent.tier: int | None = None`、`payload_for_buffer()` 含 `tier` 键、**`to_sse()` 输出的 wire JSON 含 `tier` 键**（前端直接消费的契约）、`from_payload()` 往返保留 tier、落库 sources dict 含 `tier` 键（Task 6 前端依赖 wire payload；历史回放依赖落库）

- [ ] **Step 1: 写失败测试**

`tests/utils/test_sse.py` 末尾追加：

```python
# ==================== citation tier 字段（source-tier-labeling）====================


def test_citation_event_tier_in_payload():
    """tier 显式传入时进 payload。"""
    ev = SSECitationEvent(source="a.pdf", page=1, snippet="s", tier=2)
    assert ev.payload_for_buffer()["tier"] == 2


def test_citation_event_tier_default_none():
    """tier 默认 None（存量/未定档语义），payload 键值为 null。"""
    ev = SSECitationEvent(source="a.pdf", page=1, snippet="s")
    assert ev.payload_for_buffer()["tier"] is None


def test_citation_wire_json_contains_tier():
    """wire 契约：to_sse 输出（前端实际收到的 data: 行）必须含 tier 键。

    该测试守住「payload_for_buffer 有 tier 但 wire 序列化漏 tier」的断链——
    此类断链单测上半段全绿、线上徽标全无。
    """
    ev = SSECitationEvent(source="a.pdf", page=1, snippet="s", tier=1)
    wire = to_sse(ev)
    assert "event: citation" in wire
    assert '"tier": 1' in wire


def test_citation_wire_json_tier_none_serialized():
    """tier=None（存量语义）在 wire 中序列化为 null 键，前端据 null 降级。"""
    ev = SSECitationEvent(source="a.pdf", page=1, snippet="s")
    assert '"tier": null' in to_sse(ev)
```

（import 区补 `to_sse`。）

`tests/utils/test_sse_roundtrip.py` 的 `CASES` 列表中，既有 `SSECitationEvent(...)` 用例之后追加一条带 tier 的用例（锁定 tier 往返不丢失）：

```python
    SSECitationEvent(
        source="https://www.caixin.com/a",
        page=0,
        snippet="财经报道",
        score=0.9,
        index=1,
        kind="web",
        tier=2,
    ),
```

该文件的 `test_sse_roundtrip` 参数化测试会自动覆盖新用例（`to_sse(from_payload(...)) == to_sse(ev)`），无需新写测试函数。

`tests/services/test_agent_service.py` 追加（紧邻既有 `test_citation_event_passes_kind`，约 573 行，复用其 `_convert_event` / `_format_end_item`）：

```python
def test_citation_event_passes_tier():
    """format 输出 citations 的 tier 透传到 SSECitationEvent；缺省为 None。"""
    events = _convert_event(
        _format_end_item(
            [
                {
                    "index": 1,
                    "source": "https://www.caixin.com/a",
                    "page": 0,
                    "snippet": "财经",
                    "score": 0.9,
                    "kind": "web",
                    "tier": 2,
                }
            ]
        )
    )
    citations = [e for e in events if isinstance(e, SSECitationEvent)]
    assert citations[0].tier == 2
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/utils/test_sse.py tests/utils/test_sse_roundtrip.py tests/services/test_agent_service.py -v -k tier`
Expected: FAIL（payload 测试 `TypeError: unexpected keyword argument 'tier'`；wire 测试 `assert '"tier": 1' in ...` 失败——roundtrip 新用例同败，因 to_sse 两侧均无 tier 时该用例反而可能通过，以 payload/wire 失败为准）

- [ ] **Step 3: 实现**

3a. `src/utils/sse.py` `SSECitationEvent`：`kind` 字段之后追加：

```python
    tier: int | None = None  # 来源权威档位（0=内部文档/1=官方一手/2=权威媒体/3=一般/4=UGC）；None=存量消息或未定档，前端不显示徽标
```

`payload_for_buffer` 的返回 dict 中 `"kind": self.kind,` 之后追加：

```python
            "tier": self.tier,
```

3b. `src/utils/sse.py:293` `sse_citation()`：签名 `kind` 参数之后、`seq` 之前加 `tier: int | None = None,`，docstring Args 补一行 `tier: 来源权威档位（None=存量/未定档）`，data dict 中 `"kind": kind,` 之后追加：

```python
        "tier": tier,
```

3c. `src/utils/sse.py:468` `to_sse` citation 分支：解构补 `tier=tr,`（放在 `kind=k,` 之后），函数调用改为：

```python
        case SSECitationEvent(
            source=s,
            page=p,
            snippet=snippet,
            score=score,
            highlighted_snippet=hs,
            index=idx,
            kind=k,
            tier=tr,
            seq=seq,
        ):
            return sse_citation(s, p, snippet, score, hs, idx, k, tr, seq)
```

3d. `src/utils/sse.py:540` `from_payload` citation 分支：`kind=payload["kind"],` 之后追加：

```python
            tier=payload.get("tier"),
```

（用 `.get` 而非 `[]`：存量缓冲 payload 无 tier 键，还原为 None 而非 KeyError。）

3e. `src/services/agent_service.py:321` 的 `SSECitationEvent(...)` 构造中 `kind=c.get("kind", ...),` 之后追加：

```python
                    tier=c.get("tier"),
```

3f. `src/services/agent_service.py:505` 落库 dict 中 `"kind": event.kind,` 之后追加：

```python
                            "tier": event.tier,
```

同步更新该处上方注释（`# 落库保留完整引用结构（source/page/snippet/kind/index）` → 加 tier）。

- [ ] **Step 4: 更新受影响的既有断言 + 运行测试**

两处落库 dict 精确比较断言需追加 `"tier": None`（事实核查确认的第二处）：

4a. `tests/services/test_agent_service.py:899` `test_run_generation_accumulates_citation_sources`：

```python
    assert partial_holder["sources"] == [
        {
            "source": "财报.pdf",
            "page": 5,
            "snippet": "营收100亿",
            "kind": "kb",
            "index": 1,
            "tier": None,
        }
    ]
```

4b. `tests/services/test_agent_service.py:1018-1026` `test_stream_chat_persists_citation_sources_on_complete`：同构断言 `assert args[3] == [{"source": "财报.pdf", ...}]`，期望 dict 同样追加 `"tier": None`。

**防 KeyError 约束**：3e 的 `_convert_event` 构造必须用 `tier=c.get("tier")`（`.get` 缺键返回 None），**不得**写 `c["tier"]`——否则 test_dual_stream.py 与 test_agent_service.py 中所有无 tier 键的 citations dict 构造（约 5 处测试）会 KeyError。已有测试的 citations dict 不需要补 tier 键（`.get` 语义下缺键 = None 合法）。

Run: `pytest tests/utils/test_sse.py tests/utils/test_sse_roundtrip.py tests/services/test_agent_service.py tests/services/test_dual_stream.py -v`
Expected: PASS 全部

- [ ] **Step 5: Commit**

```bash
git add src/utils/sse.py src/services/agent_service.py tests/utils/test_sse.py tests/utils/test_sse_roundtrip.py tests/services/test_agent_service.py tests/services/test_dual_stream.py
git commit -m "feat: SSE citation 帧与落库 sources 携带 tier 字段（wire 序列化与还原路径同步）"
```

---

### Task 6: chat.html 权威等级徽标

> **REQUIRED SKILL: `/frontend-design`** — 本 Task 实现时调用该 skill；视觉规格以 `docs/design/pages/chat-citation-tier-2026-09-09.md` + 预览 `docs/design/chat-citation-tier-mockup-2026-09-09.html` 为唯一依据（设计方向已确认：徽标仅在抽屉条目、来源名称之前；横条不加），skill 只辅助实现精度，不重新发散设计。

**Files:**
- Modify: `deploy/nginx/html/chat.html`（约 4 处 JS + CSS）
- Test: playwright-cli 手工验证（Step 5）

**Interfaces:**
- Consumes: SSE citation payload 的 `tier` 键（Task 5）、落库 sources 的 `tier` 键（历史回放）
- Produces: **仅引用抽屉条目**渲染 tier 徽标（来源名称之前）；横条保持既有形态不渲染徽标；tier 缺失/null 不渲染、无 JS 错误。标签映射以 `src/config/const.py SOURCE_TIER_LABELS` 为唯一权威（api_contract 登记），前端照抄。设计规格：`docs/design/pages/chat-citation-tier-2026-09-09.md`

- [ ] **Step 1: 加映射表与徽标 HTML helper**

`chat.html` 中 `// ── 引用交互 ──` 注释块（约 1157 行）之前加：

```js
// tier → 中文等级映射（权威来源：src/config/const.py SOURCE_TIER_LABELS / api_contract.md；
// 改文案动线：const.py → api_contract.md → 本文件 三步同步）
const TIER_LABELS = { 0: '内部文档', 1: '官方一手', 2: '权威媒体', 3: '一般', 4: 'UGC' };

// tier 徽标 HTML：tier 为 null/undefined/未知值时返回空串（存量数据降级不显示）
function tierBadgeHtml(tier) {
  if (tier === null || tier === undefined) return '';
  const label = TIER_LABELS[tier];
  if (label === undefined) return '';
  return '<span class="tier-badge tier-' + tier + '">' + label + '</span>';
}
```

- [ ] **Step 2: 实时路径透传 tier**

2a. `renderCitation`（约 1168 行）签名与 push 改为：

```js
function renderCitation(source, page, snippet, index, tier) {
  ...
  let tierVal = null;
  if (Number.isInteger(tier)) tierVal = tier;
  host._citeData.push({ source, page, snippet, num, tier: tierVal });
  ...
}
```

2b. SSE citation 事件处理（约 1940 行）改为传 `data.tier`：

```js
        renderCitation(data.source, data.page, data.snippet || '', data.index || 0, data.tier);
```

- [ ] **Step 3: 历史回放路径透传 tier**

`attachHistoryCitations`（约 1206 行）对象分支加 tier 提取：

```js
    } else if (item && typeof item === 'object') {
      source = item.source || '';
      page = item.page || 0;
      snippet = item.snippet || '';
      num = item.index || 0;
      if (Number.isInteger(item.tier)) {
        tier = item.tier;
      } else {
        tier = null;
      }
    }
```

（函数开头 `let tier = null;` 与其他 let 声明并列；`cites.push({ source, page, snippet, num: ..., tier })`。）

- [ ] **Step 4: 抽屉渲染徽标 + CSS（横条不改动）**

4a. `renderCiteBar` **不修改**（横条不加徽标，`textContent` 编号渲染与点击交互保持原样；`_citeData` 中的 tier 字段仅供抽屉消费）。

4b. `openCiteDrawer`（约 1255 行）模板中 `.file` 行内、`<span class="fname">` 之前插徽标（名称之前，先等级后内容）：

```js
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h7a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7z"/></svg>
            ${tierBadgeHtml(c.tier)}
            <span class="fname">${escapeHtml(c.source || '')}</span>
            <span class="page">第${c.page || 0}页</span>
```

（`tierBadgeHtml` 返回值只含 const 标签与整数 class，无注入面。）

4c. `<style>` 区追加（色系沿用页面现有低饱和风；规格档 `docs/design/pages/chat-citation-tier-2026-09-09.md`）：

```css
/* 来源权威徽标（source-tier-labeling）：仅抽屉条目渲染，tier 缺失不渲染 */
.tier-badge { display:inline-block; margin-left:6px; margin-right:2px; padding:1px 6px; border-radius:8px; font-size:10px; line-height:14px; vertical-align:middle; white-space:nowrap; }
.tier-0 { background:#ede9fe; color:#6d28d9; }
.tier-1 { background:#dcfce7; color:#15803d; }
.tier-2 { background:#dbeafe; color:#1d4ed8; }
.tier-3 { background:#f1f5f9; color:#475569; }
.tier-4 { background:#ffedd5; color:#c2410c; }
```

- [ ] **Step 5: playwright-cli 验证**

用 playwright-cli 技能打开 `deploy/nginx/html/chat.html`（file:// 或本地 serve），在 console 依次执行并断言。**前置注入必须先做**：`renderCitation` / `attachHistoryCitations` 内部依赖 `lastAiRow()` 查找 `.bubble-row.ai`，空页面直接 early-return，不注入则徽标断言必然失败：

```js
// 0. 前置：注入一个 AI 气泡行（chatContainer 为页面全局 DOM 变量）
const row = document.createElement('div');
row.className = 'bubble-row ai';
row.innerHTML = '<div class="bubble ai">测试回答内容 [1]</div>';
chatContainer.appendChild(row);

// 1. 实时路径 tier 入池 + 横条形态不变
renderCitation('https://www.tencent.com/a.pdf', 0, '年报摘要', 1, 1);
// 断言：.cite-bar 内无 .tier-badge（横条不加徽标），编号渲染与改动前一致

// 2. 抽屉：条目徽标在来源名称之前
openCiteDrawer([{ num: 1, source: 'https://www.tencent.com/a.pdf', page: 0, snippet: 'x', tier: 1 }]);
// 断言：.drawer-item .fname 的前一个兄弟元素为 .tier-1 徽标，文案「官方一手」

// 3. 存量降级：tier 缺失不显示徽标、无 JS 错误
openCiteDrawer([{ num: 1, source: 'a.pdf', page: 5, snippet: 'x' }]);
// 断言：.drawer-item 内无 .tier-badge，页面无 console error

// 4. 历史回放：结构化 sources 含 tier 与不含 tier 混合
attachHistoryCitations(lastAiRow(), [{ source: 'a.pdf', page: 1, snippet: '', index: 1, tier: 0 }, { source: 'b.pdf', page: 2, snippet: '', index: 2 }]);
// 断言：条目1「内部文档」徽标位于名称之前，条目2无徽标
```

四条断言全部通过即达标；任一失败回到对应 Step 修复。执行前顺手核对 docs/design/（MASTER.md / pages）中引用抽屉的规格档，有登记则补一行徽标说明（openspec tasks 4.3；规格档 `chat-citation-tier-2026-09-09.md` 已存在）。

- [ ] **Step 6: Commit**

```bash
git add deploy/nginx/html/chat.html
git commit -m "feat: 引用横条与抽屉渲染来源权威等级徽标（tier 缺失降级）"
```

---

### Task 7: 端到端验证 + 文档登记

**Files:**
- Modify: `docs/agents/api_contract.md`、`docs/agents/glossary.md`、`docs/agents/cookbook.md`
- 验证：真实服务端到端 + 标准门禁

**Interfaces:**
- Consumes: 前 6 个任务的全部产物
- Produces: 权威映射契约登记、术语登记、候选规则审核操作流程（含聚合 SQL 与拒绝清单）

- [ ] **Step 1: 标准门禁**

```bash
pytest tests/ -v
ruff format . && ruff check . --fix
pyright src/
```

Expected: pytest 全过、ruff 无错误、pyright 无新增 error（存量第三方库误报以不新增为准）。部署验证：`docker compose restart app`（override 挂载 src/，无需 --build）。

- [ ] **Step 2: 端到端验证（openspec tasks 6.2）**

1. 真实联网提问（如「腾讯最新的股价表现如何」），观察日志与回答：
   - 日志中 web 检索行为正常，无 `signal=invalid_citation` 异常频率
   - 回答引用横条出现徽标：官网来源「官方一手」、知乎帖「UGC」、未命中域名「一般」、KB 来源「内部文档」
2. 刷新页面走历史回放，确认徽标与实时一致（落库 sources 含 tier）
3. 用一条含 [99] 幻觉编号的构造回答验证日志出现 `retrieval_signal signal=invalid_citation count=N ids=99`

- [ ] **Step 3: api_contract.md 登记 citation tier 字段**

在 citation / SSE 事件契约章节追加：

```markdown
### citation.tier（source-tier-labeling）

SSECitationEvent payload 与落库 sources 的新增字段 `tier: int | null`：

| tier | 标签 | 语义 |
|------|------|------|
| 0 | 内部文档 | KB 知识库文档（不走域名分级） |
| 1 | 官方一手 | T1 官方域名 / .gov.cn / .edu.cn |
| 2 | 权威媒体 | T2 财经媒体清单 |
| 3 | 一般 | T3 未命中默认中性档 |
| 4 | UGC | T4 用户生成内容清单 |
| null | （无徽标） | 存量消息或未定档 |

**标签文案以 `src/config/const.py SOURCE_TIER_LABELS` 为唯一权威，本表与前端 `chat.html TIER_LABELS` 为照抄副本；改动动线：const.py → api_contract.md → chat.html 三步走完才算改完。**
```

- [ ] **Step 4: glossary.md 登记术语**

按词汇表既有格式追加两条：**来源等级（source tier）**（T0-T4 定义、确定性定档、模型不改写、徽标透明呈现）、**候选规则信号**（离线 SQL 统计 + 人工审核 + negative cache 的清单成长机制）。

- [ ] **Step 5: cookbook.md 登记候选规则审核流程 + RAGAS 基线分界**

按 cookbook 操作记录协议追加「候选规则审核」条目，内容含：

1. 跑聚合 SQL（MySQL 8.0 JSON_TABLE）：

```sql
-- 未命中过滤在人工审核步做（不做进 SQL，避免复刻 Python 解析逻辑的双头维护）
SELECT
  SUBSTRING_INDEX(SUBSTRING_INDEX(
    SUBSTRING_INDEX(jt.val, '/', 1), ':', -1), ':', 1) AS domain_raw,
  COUNT(*)                                   AS cite_count,
  COUNT(DISTINCT m.id)                       AS msg_count
FROM conversation_history m
JOIN JSON_TABLE(m.sources, '$[*]' COLUMNS (val VARCHAR(1024) PATH '$.source')) jt
WHERE m.sources IS NOT NULL
GROUP BY domain_raw
HAVING cite_count >= 5
ORDER BY cite_count DESC;
```

（跨会话数若按 session 维度统计，将 `m.id` 换成会话列；执行时先跑一次验证 `sources` 列 JSON 结构可得性，再定稿 SQL——openspec tasks 5.2 前置验证。）

2. 人工对照 `src/config/const.py SOURCE_TIER_RULES` 与下方拒绝清单，排除已命中/已拒绝域名，筛出候选（阈值建议：≥5 次且跨 ≥3 会话）。
3. 打开样本消息核对引用上下文，确认该域名内容性质（官方/媒体/UGC）。
4. 批准 → `SOURCE_TIER_RULES` 加行 → `docker compose restart app` 生效 → 同步 api_contract（若涉及标签）。
5. 拒绝 → 在本条目下的**拒绝清单（negative cache）**追加一行 `域名 | 拒绝日期 | 理由`，不建数据库表。

另登记一条 **RAGAS 基线分界**记录：实现 to_prompt_text 档位标注的当日，标注「此后 NLI 上下文含档位标注，与此前 faithfulness 分数不可直接比」（design Risks 对应项）。

- [ ] **Step 6: 一事一档自检 + Commit**

检查三处文档无互相复制正文（表格已在 api_contract 为权威、glossary 只放术语定义、cookbook 只放操作步骤），然后：

```bash
git add docs/agents/api_contract.md docs/agents/glossary.md docs/agents/cookbook.md
git commit -m "docs: 登记 citation tier 契约、来源等级术语与候选规则审核流程"
```

---

## Self-Review 记录

- **Spec 覆盖**：openspec tasks 1.1-1.3 → Task 1/2；2.1-2.4 → Task 3/4/5；3.1-3.2 → Task 4；4.1-4.3 → Task 6（4.3 规格档核对并入 Task 6 Step 5 playwright 前检查 docs/design/ 是否有引用横条规格档，有则补一行徽标说明）；5.1-5.3 → Task 7 Step 5；6.1-6.3 → Task 6 Step 5 / Task 7 Step 1-2；7.1-7.3 → Task 7 Step 3-5。无遗漏。
- **占位符扫描**：Task 3 的 KB 构造点测试（test_retrieval.py / test_rag_tools.py）保留一处「夹具照抄现有测试」的指引——mock 夹具变量名依现有测试而定，属有意引用而非 TBD；web 侧与 wire/roundtrip 测试均含完整可运行代码。
- **强模型评审修复记录（2026-09-09）**：P0-1 补 SSE wire 序列化链（sse_citation/to_sse/from_payload + wire 与 roundtrip 测试）；P0-2 修 Task 2 页码断言（第0页→第1页）；P1 修 Task 3 既有断言 :113 更新登记、恒真断言与 `.invoke`→`ainvoke`、Task 4 矛盾测试版本删除、playwright 前置气泡注入、test_retrieval.py RAGContext import 提示。
- **类型一致性**：`resolve_source_tier(url: str, kind: str) -> int`、`RAGContext.tier: int | None`、`SSECitationEvent.tier: int | None`、citations dict 键 `"tier"`、payload 键 `"tier"`、wire JSON 键 `"tier"`、落库键 `"tier"`、前端 `TIER_LABELS` 与 `SOURCE_TIER_LABELS` 值一致——已逐一对齐。
