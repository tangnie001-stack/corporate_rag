# 聊天默认搜索所有知识库 — 实现计划

> **For agentic workers:** 使用 superpowers:subagent-driven-development 逐任务实施。

**目标:** 聊天时不再需要选择知识库，默认搜索所有 KB 的文档内容。

**架构:** 在 VectorStore 新增 `similarity_search_all()` 方法（遍历所有 collection 合并结果），修改 `RAGChain.chat_with_citations` 在 `kb_id=""` 时调用该方法，Gradio 下拉框增加 `("所有知识库", "")` 作为默认选项。

**决策记录（grill-me 确认）：**
1. 下拉框保留，加 `("所有知识库", "")` 为第一个选项，默认 value 为 `""`
2. 选择"所有知识库"时文档列表清空，显示"请选择一个知识库查看文档"
3. 多 KB 搜索结果合并后经 reranker 精排，取全局 top-N

**需要一并修复的 bug：** `rag_chain.py:169` 和 `:179` 引用不存在的 `kb_name` 变量（重构 `kb_id` 时遗留），会导致 NameError。

---

### Task 1: VectorStore — 新增 `similarity_search_all()`

**Files:**
- Modify: `src/vector_store.py`（在第 295 行 `list_collections` 之后添加）
- Test: `tests/test_vector_store.py`

**Interfaces:**
- Produces: `VectorStore.similarity_search_all(query, k) → list[dict]`

**注意项：** 避免在 similarity_search 内部重复创建 `PersistentClient`，应直接复用 `_get_client()`。结果按 distance 升序排列后取 top-k。

- [ ] **Step 1: 写测试**

在 `tests/test_vector_store.py` 的 `TestVectorStore` 类中添加：

```python
def test_similarity_search_all(self, vector_store: VectorStore, patch_embed_fn: None) -> None:
    """搜索所有 collection，返回按距离排序的结果。"""
    # 准备工作：创建两个 KB collection，各写入一条文档
    kb_a = str(uuid.uuid4())
    kb_b = str(uuid.uuid4())
    col_a = vector_store.get_or_create_collection(kb_a)
    col_b = vector_store.get_or_create_collection(kb_b)
    col_a.add(ids=["a:0"], documents=["苹果公司的营收情况"], metadatas=[{"source": "a.txt"}])
    col_b.add(ids=["b:0"], documents=["特斯拉的营收情况"], metadatas=[{"source": "b.txt"}])

    # 清空 _collection_cache 迫使重新从持久化读取（测试 list_collections）
    vector_store._collection_cache.clear()  # noqa: SLF001

    results = vector_store.similarity_search_all("营收", k=2)
    assert len(results) == 2
    # 结果应按 distance 升序排列
    for i in range(len(results) - 1):
        assert results[i]["distance"] <= results[i + 1]["distance"]

def test_similarity_search_all_no_collections(self, vector_store: VectorStore, patch_embed_fn: None) -> None:
    """无任何 collection 时返回空列表。"""
    results = vector_store.similarity_search_all("test", k=5)
    assert results == []
```

- [ ] **Step 2: 运行测试确认失败**

```bash
PYTHONPATH=. pytest tests/test_vector_store.py::TestVectorStore::test_similarity_search_all -v
PYTHONPATH=. pytest tests/test_vector_store.py::TestVectorStore::test_similarity_search_all_no_collections -v
```
Expected: `FAILED`（方法未定义）

- [ ] **Step 3: 实现 `similarity_search_all`**

在 `vector_store.py` 的 `list_collections` 方法之后添加：

```python
def similarity_search_all(self, query: str, k: int = TOP_K_RETRIEVAL) -> list[dict]:
    """在所有知识库中进行语义搜索，合并结果后按距离排序取 top-k。

    遍历所有以 kb_ 开头的 collection，对每个 collection 执行
    similarity_search，合并结果按 distance 升序排列后取前 k 个。

    适用于"不限定知识库"的全局搜索场景。

    Args:
        query: 用户查询文本
        k: 最终返回结果数量上限

    Returns:
        同 similarity_search() 的返回格式，按 distance 升序排列
    """
    names = self.list_collections()
    if not names:
        return []

    all_results: list[dict] = []
    for name in names:
        kb_id = name.removeprefix(CHROMA_COLLECTION_PREFIX)
        try:
            results = self.similarity_search(kb_id, query, k=k)
            all_results.extend(results)
        except Exception as e:
            logger.warning("搜索 collection '{}' 失败: {}", name, e)
            continue

    # 按 distance 升序排列（越小越相似）
    all_results.sort(key=lambda r: r.get("distance", float("inf")))
    return all_results[:k]
```

- [ ] **Step 4: 运行测试确认通过**

```bash
PYTHONPATH=. pytest tests/test_vector_store.py::TestVectorStore::test_similarity_search_all -v
PYTHONPATH=. pytest tests/test_vector_store.py::TestVectorStore::test_similarity_search_all_no_collections -v
```
Expected: `PASSED`

- [ ] **Step 5: 提交**

```bash
git add src/vector_store.py tests/test_vector_store.py
git commit -m "feat: add similarity_search_all for cross-KB search"
```

---

### Task 2: RAGChain — 支持空 kb_id 搜索全部 + 修复 kb_name bug

**Files:**
- Modify: `src/rag_chain.py:132-201`
- Test: `tests/test_rag_chain.py`

**Interfaces:**
- Consumes: `VectorStore.similarity_search_all(query, k) → list[dict]`
- Modifies: `RAGChain.chat_with_citations(kb_id, session_id, query)` — 空 `kb_id` 时改为搜全部而非返回"不存在"

- [ ] **Step 1: 写测试**

在 `tests/test_rag_chain.py` 的 `TestRAGChainChat` 类中添加：

```python
@patch("src.rag_chain.get_rerank")
@patch("src.rag_chain.get_llm")
@patch("src.rag_chain.get_embeddings")
def test_chat_search_all(self, mock_get_emb, mock_get_llm, mock_get_rerank):
    """kb_id="" 时调用 similarity_search_all 而非返回不存在。"""
    mock_rerank = MagicMock()
    mock_rerank.rerank.return_value = [{"index": 0, "relevance_score": 0.9}]
    mock_get_rerank.return_value = mock_rerank
    # mock similarity_search_all 返回匹配结果
    chain = RAGChain()
    chain.vector_store.similarity_search_all = MagicMock(
        return_value=[{"id": "a:0", "content": "苹果2024年营收为3910亿美元。",
                       "metadata": {"source": "a.txt", "page": 1, "doc_id": "doc1"},
                       "distance": 0.1}]
    )
    # mock LLM 流式输出
    mock_llm = MagicMock()
    mock_llm.stream.return_value = iter(["这是", "一个", "回答"])
    mock_get_llm.return_value = mock_llm

    gen, citations = chain.chat_with_citations(
        kb_id="",
        session_id="sess_all",
        query="苹果营收",
    )
    result = "".join(gen)
    assert "回答" in result
    assert len(citations) > 0
    chain.vector_store.similarity_search_all.assert_called_once()

@patch("src.rag_chain.get_rerank")
@patch("src.rag_chain.get_llm")
@patch("src.rag_chain.get_embeddings")
def test_chat_search_all_no_kbs(self, mock_get_emb, mock_get_llm, mock_get_rerank):
    """kb_id="" 且无任何 KB 时返回"未找到"而非报错。"""
    chain = RAGChain()
    chain.vector_store.similarity_search_all = MagicMock(return_value=[])
    mock_llm = MagicMock()
    mock_get_llm.return_value = mock_llm

    gen, citations = chain.chat_with_citations(
        kb_id="",
        session_id="sess_all_empty",
        query="test",
    )
    result = "".join(gen)
    assert "未找到" in result or "相关" in result
    assert len(citations) == 0
```

同时也需要修复 `test_kb_not_found`：

```python
# 修改 test_kb_not_found，现有测试不再适用——空 kb_id 不再返回"不存在"
# 而是走 similarity_search_all。这个测试应该删除或改为：
# 现在 kb_id="" 是全量搜索，不会触发"知识库不存在"路径了。
```

- [ ] **Step 2: 运行测试确认失败**

```bash
PYTHONPATH=. pytest tests/test_rag_chain.py::TestRAGChainChat::test_chat_search_all -v
PYTHONPATH=. pytest tests/test_rag_chain.py::TestRAGChainChat::test_chat_search_all_no_kbs -v
```
Expected: `FAILED`

- [ ] **Step 3: 修改 `chat_with_citations`**

将 `rag_chain.py` 中 `if not kb_id:` 分支改为调用 `similarity_search_all`，同时修复 `kb_name` 变量 bug：

```python
# 原有空 kb_id 分支（~153-160 行）替换为：
if not kb_id:
    logger.info("kb_id 为空，搜索所有知识库")
    try:
        results = self.vector_store.similarity_search_all(query, k=TOP_K_RETRIEVAL)
    except Exception as e:
        error_msg = str(e)
        logger.error("全局搜索失败: {}", error_msg)
        citations = []

        def _search_err_gen() -> Generator[str, None, None]:
            yield f"检索失败: {error_msg}"

        return _search_err_gen(), citations
else:
    # 原有单 KB 检索逻辑...
    try:
        results = self.vector_store.similarity_search(
            kb_id, query, k=TOP_K_RETRIEVAL,
        )
    except Exception as e:
        error_msg = str(e)
        logger.error("Vector search failed for kb_id={}: {}", kb_id, error_msg)
        # ...原有异常处理
```

修复 `kb_name` → `kb_id`（第 169、179 行）：

```python
# 第 169 行：
logger.error("Vector search failed for kb_id={}: {}", kb_id, error_msg)

# 第 179 行：
logger.info("No results found for query from kb_id='{}'", kb_id)
```

- [ ] **Step 4: 调整现有 `test_kb_not_found` 测试**

`test_kb_not_found` 测试现在不再匹配——`kb_id=""` 走的是全量搜索路径，不会返回"知识库不存在"。将该测试用例改为验证 `similarity_search_all` 被调用且返回空时给出提示：

```python
@patch("src.rag_chain.get_rerank")
@patch("src.rag_chain.get_llm")
@patch("src.rag_chain.get_embeddings")
def test_kb_not_found(self, mock_get_emb, mock_get_llm, mock_get_rerank):
    """kb_id="" 时调用 similarity_search_all 且无结果时返回"未找到"提示。"""
    chain = RAGChain()
    chain.vector_store.similarity_search_all = MagicMock(return_value=[])

    gen, citations = chain.chat_with_citations(
        kb_id="",
        session_id="sess1",
        query="test query",
    )
    result = "".join(gen)
    assert "未找到" in result or "相关" in result
    assert len(citations) == 0
```

- [ ] **Step 5: 运行测试确认通过**

```bash
PYTHONPATH=. pytest tests/test_rag_chain.py::TestRAGChainChat -v
```
Expected: 全部 PASSED

- [ ] **Step 6: 提交**

```bash
git add src/rag_chain.py tests/test_rag_chain.py
git commit -m "feat: chat_with_citations supports search-all mode when kb_id is empty; fix kb_name bug"
```

---

### Task 3: 前端 UI — 下拉框增加"所有知识库"选项 + 适配空 kb_id

**Files:**
- Modify: `src/app.py`（`refresh_kb_dropdown`、`handle_select_kb`、`handle_chat`）
- Test: `tests/test_app.py`

**注意项：** Gradio Dropdown 的 value 设为 `""` 对应"所有知识库"选项。`refresh_kb_dropdown` 返回列表的第一个元素必须是 `("所有知识库", "")`。`allow_custom_value=True` 保留，因为用户可能在 dropdown 中输入。

- [ ] **Step 1: 写测试**

```python
# test_app.py — 修改 test_refresh_kb_dropdown
def test_refresh_kb_dropdown(self, svc_mock: MagicMock):
    """下拉框包含"所有知识库"选项，其后为各知识库。"""
    app._service = svc_mock
    svc_mock.list_knowledge_bases.return_value = [("id1", "KB1"), ("id2", "KB2")]

    choices = app.refresh_kb_dropdown()
    assert choices[0] == ("所有知识库", "")
    assert ("KB1", "id1") in choices
    assert ("KB2", "id2") in choices
    assert len(choices) == 3

def test_handle_select_kb_all(self, svc_mock: MagicMock):
    """选择"所有知识库"时文档列表为空。"""
    app._service = svc_mock

    docs, status = app.handle_select_kb("")
    assert docs == []
    assert "选择" in status or "知识库" in status
```

- [ ] **Step 2: 运行测试确认失败**

```bash
PYTHONPATH=. pytest tests/test_app.py::TestApp::test_refresh_kb_dropdown -v
PYTHONPATH=. pytest tests/test_app.py::TestApp::test_handle_select_kb_all -v
```
Expected: `FAILED`

- [ ] **Step 3: 修改 `refresh_kb_dropdown`**

```python
def refresh_kb_dropdown() -> list[tuple[str, str]]:
    """刷新知识库下拉菜单的选项列表。

    第一个选项为"所有知识库"（value=""），表示不限定知识库的全局搜索。
    后续选项为各具体知识库，value 使用 UUID。
    """
    svc = get_service()
    kbs = svc.list_knowledge_bases()
    choices = [("所有知识库", "")]
    choices.extend((name, kid) for kid, name in kbs)
    return choices
```

- [ ] **Step 4: 修改 `handle_select_kb`，适配空 kb_id**

```python
def handle_select_kb(kb_id: str) -> tuple[list[list], str]:
    """选择知识库时：刷新文档列表。选择"所有知识库"时文档列表为空。"""
    if not kb_id:
        return [], "请选择一个知识库查看文档，或直接在聊天输入框提问。"
    svc = get_service()
    docs = svc.get_documents(kb_id)
    return format_docs_for_display(docs), "已选择知识库"
```

- [ ] **Step 5: 修改 `handle_chat` 中的空 kb_id 提示信息**

```python
# 第 192-194 行：将"请先选择一个知识库"改为更合适的提示
if not kb_id:
    # 空 kb_id 现在是合法的（搜索全部），不需要提示选择知识库了
    # 但是 message 为空时仍然需要处理
    if not message or not message.strip():
        yield history, ""
        return
    # 走全量搜索逻辑（由 RAGChain 处理）
    # 下面正常走 chat_with_citations 即可
```

实际上不需要修改 `handle_chat` 中的这个判断——空 `kb_id` + 空 message 返回 history，空 `kb_id` + 有 message 时直接传给 `chat_with_citations`，后者会走全量搜索。只需删掉 `"请先选择一个知识库"` 这个提示分支：

```python
# 删除这两行（~192-194）：
if not kb_id:
    yield history + [[message, "请先选择一个知识库"]], ""
    return

# 替换为：
if not message or not message.strip():
    yield history, ""
    return
```

- [ ] **Step 6: 运行测试确认通过**

```bash
PYTHONPATH=. pytest tests/test_app.py::TestApp -v
```
Expected: 全部 PASSED

- [ ] **Step 7: 提交**

```bash
git add src/app.py tests/test_app.py
git commit -m "feat: add '所有知识库' default option in dropdown, adapt handlers"
```

---

### Task 4: 验证全量测试 + 重启容器

- [ ] **Step 1: 运行全量 TDD 测试（排除集成测试和 RAGAS）**

```bash
PYTHONPATH=. pytest tests/ --ignore=tests/test_kb_page.py --ignore=tests/test_upload_page.py --ignore=tests/test_chat_page.py --ignore=tests/test_eval_ragas.py -v
```
Expected: 全部 PASSED

- [ ] **Step 2: 重启 app 容器**

```bash
docker restart financial-qa-app
```
Expected: 容器正常启动后可在 http://localhost:7860 访问

- [ ] **Step 3: 手动验证**

打开浏览器访问 http://localhost:7860，验证：
- 下拉框默认显示"所有知识库"
- 创建 KB 后，下拉框包含"所有知识库" + 新建的 KB
- 选择具体 KB 时右侧文档列表正常
- 选择"所有知识库"时文档列表清空
- 不选 KB，直接在聊天框提问，能搜到所有 KB 的内容
- 选择具体 KB 提问，只搜该 KB 的内容
