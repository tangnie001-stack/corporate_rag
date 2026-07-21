# 脱敏函数 + 分块优化实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 解决 RAGAS 测试集生成时阿里云百炼内容安全审核（DataInspectionFailed）问题，同时优化传入方式

**Architecture:** 新增脱敏函数对 parser chunks 做敏感词替换，改用 `generate_with_chunks` 替代 `generate_with_langchain_docs`，增加白名单过滤只处理指定文档

**Tech Stack:** Python 3.11+, RAGAS 0.4.3, re（标准库正则）

## 全局约束

- 不引入第三方依赖（脱敏函数只用 `re` 标准库）
- 所有改动在 `eval_ragas_generate.py` 完成，不修改其他 CLI 或 API 代码
- 脱敏函数放在 `src/infra/desensitize.py`，纯函数，无副作用
- Docker 部署：改 `src/` 下 `.py` 文件只需 `docker compose restart app`

---

### 任务 1: 创建脱敏函数

**文件:**
- Create: `src/infra/desensitize.py`
- Test: （可选，手动验证）

**接口:**
- Produces: `desensitize(text: str) -> str` — 对文本进行脱敏替换

- [ ] **步骤 1: 创建脱敏函数文件**

写入 `src/infra/desensitize.py`：

```python
"""文本脱敏工具 — 替换可能触发内容安全审核的敏感内容。

替换规则（全量替换阶段）：
  - 金额        → [金额]
  - 日期        → [日期]
  - 百分比       → [比例]
  - 同比增长/下降 → 同比[变化]
  - 违规/处罚/违法 → [合规事项]
  - 诉讼/纠纷    → [合规事项]
  - 监管/整改    → [监管事项]
保留：公司名、人名、地名（供 NERExtractor 使用）
"""
import re


# 规则顺序：先替换长模式，后替换短模式，避免被短模式截胡
_DESENSITIZE_RULES: list[tuple[str, str]] = [
    # ---- 高风险：违规/处罚/诉讼类 ----
    (r'违规\S{0,10}', '[合规事项]'),
    (r'违法\S{0,10}', '[合规事项]'),
    (r'处罚\S{0,10}', '[合规事项]'),
    (r'诉讼\S{0,10}', '[合规事项]'),
    (r'纠纷\S{0,10}', '[合规事项]'),
    (r'监管\S{0,5}', '[监管事项]'),
    (r'整改\S{0,5}', '[监管事项]'),
    # ---- 金额 ----
    (r'\d+\.?\d*\s*(万亿|亿|万|千|百)?\s*(元|美元|欧元|港元|港币)', '[金额]'),
    # ---- 百分比 ----
    (r'\d+\.?\d*%', '[比例]'),
    # ---- 同比增长/下降 ----
    (r'同比\S{0,10}', '同比[变化]'),
    # ---- 具体日期 ----
    (r'\d{4}年\d{1,2}月(\d{1,2}日)?', '[日期]'),
]


def desensitize(text: str) -> str:
    """对文本进行脱敏处理，替换可能触发内容安全审核的内容。

    Args:
        text: 原始文本

    Returns:
        脱敏后的文本
    """
    for pattern, replacement in _DESENSITIZE_RULES:
        text = re.sub(pattern, replacement, text)
    return text
```

- [ ] **步骤 2: 验证脱敏函数**

```python
# 手动测试
python3 -c "
from src.infra.desensitize import desensitize

# 测试金额
assert desensitize('营收45.78亿元') == '营收[金额]'
# 测试日期
assert desensitize('2023年6月30日') == '[日期]'
# 测试百分比
assert desensitize('同比增长23.5%') == '同比[变化]'
# 测试违规
assert desensitize('违规担保事项') == '[合规事项]'
# 测试公司名保留
assert desensitize('腾讯公司') == '腾讯公司'
# 测试人名保留
assert desensitize('马化腾') == '马化腾'
print('所有脱敏规则验证通过')
"
```

- [ ] **步骤 3: 提交**

```bash
git add src/infra/desensitize.py
git commit -m "feat: add desensitization function for DashScope content inspection"
```

---

### 任务 2: 修改 settings.py 添加白名单

**文件:**
- Modify: `src/config/settings.py`（在 RAGAS 配置区追加）

- [ ] **步骤 1: 添加白名单配置**

在 `src/config/settings.py` 的 RAGAS 配置区（`RAGAS_USER_ID` 之后）追加：

```python
# RAGAS 文档白名单：只处理白名单中的文档 ID
# 用于跳过不需要参与测试集生成的文档（如扫描件、不相关文档）
RAGAS_DOC_WHITELIST: list[str] = [
    "fa7d700e-f093-45be-a78f-73fbdfd1801d",   # neusoft_2025_q1.pdf
    # "7d3f573c-d810-46d4-b0e7-42fc14b73bf4",  # tencent_2024_annual.pdf（暂放）
]
```

- [ ] **步骤 2: 提交**

```bash
git add src/config/settings.py
git commit -m "feat: add RAGAS_DOC_WHITELIST config for document filtering"
```

---

### 任务 3: 重构 run_generate — 脱敏 + 白名单 + generate_with_chunks

**文件:**
- Modify: `src/cli/eval_ragas_generate.py`（`run_generate` 函数）

**接口:**
- Consumes: `desensitize()` from `src.infra.desensitize`, `RAGAS_DOC_WHITELIST` from `src.config.settings`
- Consumes: `generate_with_chunks()` from `ragas.testset.synthesizers.generate.TestsetGenerator`

**改动点:**
1. 导入 `desensitize` 和 `RAGAS_DOC_WHITELIST`
2. 白名单过滤：只在 `doc["id"] in RAGAS_DOC_WHITELIST` 时处理
3. 不拼接 chunks：每个 parser chunk 独立作为 LCDocument
4. 脱敏：每个 chunk 过 `desensitize()`
5. 改用 `generate_with_chunks()`
6. 更新 skip_keywords 为 `["ThemesExtractor", "SummaryExtractor"]`

- [ ] **步骤 1: 修改导入部分**

在 `run_generate` 函数顶部（line 166-173）添加导入：

```python
    from src.infra.desensitize import desensitize
    from src.config.settings import RAGAS_DOC_WHITELIST
```

- [ ] **步骤 2: 修改白名单过滤（line 192-193 附近）**

将：

```python
    # 只取状态为 ready 的文档
    ready_docs = [d for d in docs if d.get("status") == "ready"]
```

改为：

```python
    # 只取状态为 ready 且在白名单中的文档
    ready_docs = [
        d for d in docs
        if d.get("status") == "ready"
        and d["id"] in RAGAS_DOC_WHITELIST
    ]
    # 记录被白名单过滤掉的文档
    skipped = [d for d in docs if d.get("status") == "ready" and d["id"] not in RAGAS_DOC_WHITELIST]
    for d in skipped:
        logger.info("文档不在白名单中，跳过: {} ({})", d.get("filename", "unknown"), d["id"])
```

- [ ] **步骤 3: 修改 parser 循环，不拼接 chunks 并脱敏（line 228-249）**

将原来的：

```python
                # 拼完整文本
                full_text = "\n\n".join(c.content for c in parse_result.chunks)
                full_texts.append(full_text)
                doc_ids.append(doc_id)
```

改为：

```python
                # 每个 parser chunk 独立作为 Document，不拼接
                for chunk in parse_result.chunks:
                    safe_content = desensitize(chunk.content)
                    langchain_chunks.append(LCDocument(page_content=safe_content))
                doc_ids.append(doc_id)
```

并在循环之前初始化 `langchain_chunks`（替换原来的 `full_texts`）：

将 line 203-204：

```python
    full_texts: list[str] = []
    doc_ids: list[str] = []
```

改为：

```python
    langchain_chunks: list[LCDocument] = []
    doc_ids: list[str] = []
```

- [ ] **步骤 4: 修改 condition 检查（line 251-254）**

将：

```python
    if not full_texts:
        logger.error("没有成功解析任何文档，无法生成测试集")
        print("✗ 没有成功解析任何文档")
        sys.exit(1)
```

改为：

```python
    if not langchain_chunks:
        logger.error("没有成功解析任何文档，无法生成测试集")
        print("✗ 没有成功解析任何文档（或白名单中无匹配文档）")
        sys.exit(1)
```

- [ ] **步骤 5: 修改初始化日志（line 258-259）**

将：

```python
    logger.info("初始化 TestsetGenerator (model={}, size={})...", eval_model, size)
    print(f"\n正在构建知识图谱 ({len(full_texts)} 份文档)...")
```

改为：

```python
    logger.info("初始化 TestsetGenerator (model={}, size={}, chunks={})...", eval_model, size, len(langchain_chunks))
    print(f"\n正在构建知识图谱 ({len(langchain_chunks)} 个 chunk)...")
```

- [ ] **步骤 6: 修改 langchain_docs 和 transforms 代码（line 274-295）**

将：

```python
    # ---- 4. 生成测试集 ----
    langchain_docs = [
        LCDocument(page_content=text) for text in full_texts
    ]

    # 构建 transforms：跳过 LLM 节点过滤（默认关闭，节省约 70 次 LLM 调用）
    transforms = None
    if not use_filter:
        from ragas.testset.transforms.default import default_transforms

        full_transforms = default_transforms(langchain_docs, generator.llm, generator.embedding_model)
        # 排除 LLM 密集的步骤，只保留提取/嵌入等不需要 LLM 的步骤
        skip_keywords = ["NodeFilter", "ThemesExtractor", "KeyphraseExtractor", "NERExtractor"]
        transforms = [
            t for t in full_transforms
            if not any(k in type(t).__name__ for k in skip_keywords)
        ]
        logger.info(
            "跳过 LLM 密集步骤: {}，节省约 70+ 次 LLM 调用",
            [k for k in skip_keywords],
        )
        logger.info("保留的 transforms 步骤: {}", [type(t).__name__ for t in transforms])
```

改为：

```python
    # ---- 4. 生成测试集 ----
    # 构建 transforms：跳过 LLM 密集步骤，保留 NERExtractor 支持多跳
    transforms = None
    if not use_filter:
        from ragas.testset.transforms.default import default_transforms_for_prechunked

        full_transforms = default_transforms_for_prechunked(
            llm=generator.llm,
            embedding_model=generator.embedding_model,
        )
        skip_keywords = ["ThemesExtractor", "SummaryExtractor"]
        transforms = [
            t for t in full_transforms
            if not any(k in type(t).__name__ for k in skip_keywords)
        ]
        logger.info("跳过的步骤: {}，保留的步骤: {}",
                     [k for k in skip_keywords],
                     [type(t).__name__ for t in transforms])
```

- [ ] **步骤 7: 修改调用行（line 300-305）**

将：

```python
        testset = generator.generate_with_langchain_docs(
            documents=langchain_docs,
            testset_size=size,
            transforms=transforms,
        )
```

改为：

```python
        testset = generator.generate_with_chunks(
            chunks=langchain_chunks,
            testset_size=size,
            transforms=transforms,
        )
```

- [ ] **步骤 8: 运行测试验证**

```bash
# 检查语法
python3 -c "import py_compile; py_compile.compile('src/cli/eval_ragas_generate.py', doraise=True)"
python3 -c "import py_compile; py_compile.compile('src/infra/desensitize.py', doraise=True)"

# 运行单元测试
pytest tests/ -v
```

- [ ] **步骤 9: 提交**

```bash
git add src/cli/eval_ragas_generate.py src/infra/desensitize.py src/config/settings.py
git commit -m "refactor: add desensitization, whitelist filter, switch to generate_with_chunks"
```

---

### 任务 4: Docker 重启验证

**文件:** 无（纯操作）

- [ ] **步骤 1: 重启 app 容器**

```bash
docker compose restart app
```

- [ ] **步骤 2: 运行生成验证**

```bash
python -m src.cli.eval_ragas --kb-name test123 --generate --size 5 --use-filter
```

预期：只处理 `neusoft_2025_q1.pdf`（1 份文档），脱敏后不再触发内容审核，成功生成 5 条 QA。

- [ ] **步骤 3: 确认生成的测试集**

```bash
ls -la data/ragas/
cat data/ragas/testset_*.json | python3 -c "import json,sys; d=json.load(sys.stdin); print(f'版本: v{d[\"metadata\"][\"version\"]}, 条数: {d[\"metadata\"][\"testset_size\"]}')"
```

- [ ] **步骤 4: 恢复到只保留高风险替换（可选）**

如果全量替换通过审核，修改 `src/infra/desensitize.py`，只保留：

```python
_DESENSITIZE_RULES = [
    (r'违规\S{0,10}', '[合规事项]'),
    (r'违法\S{0,10}', '[合规事项]'),
    (r'处罚\S{0,10}', '[合规事项]'),
    (r'诉讼\S{0,10}', '[合规事项]'),
    (r'纠纷\S{0,10}', '[合规事项]'),
]
```

重启 Docker 再跑一次确认。
