# RAGAS 测试集自动生成 实施方案

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现从知识库文档自动生成 RAGAS 评估测试集，替换手工维护的 `ragas_pairs.py`

**Architecture:** 拆分为两个模块：`eval_ragas.py`（评估入口+评估逻辑，瘦身到 400 行内）和 `eval_ragas_generate.py`（新增，测试集生成逻辑）。生成时通过 MinIO 下载原始文档 → parser 解析 → ragas TestsetGenerator 构建知识图谱 → 生成 QA 对 → 写入 `data/ragas/` 目录。

**Tech Stack:** Python 3.12 / ragas 0.4.3 / MinIO / PyMuPDF / loguru

## 全局约束

- ragas==0.4.3
- langchain-community 显式依赖
- 单文件不超 400 行
- 所有函数写 docstring（中文）
- 不用三元表达式
- vertexai stub 自动修复放在 `eval_ragas_generate.py` 顶部
- 测试集写入使用 tmp 文件 + os.replace() 原子操作

---

## 文件结构

| 文件 | 职责 |
|------|------|
| `pyproject.toml:38` | ragas 版本锁定 0.4.3，新增 langchain-community 依赖 |
| `src/config/settings.py:47-48` | 新增 `RAGAS_TEST_SIZE` 配置项 |
| `.venv/.../langchain_community/chat_models/vertexai.py` | 手动创建的 stub → 改为 `eval_ragas_generate.py` 自动修复 |
| `src/cli/eval_ragas.py` | 核心修改：新增 `--generate`/`--size`/`--model` 参数；删除 `--check`；删除 `ragas_pairs` import；替换 QUESTIONS/GROUND_TRUTH 为从 JSON 加载 |
| `src/cli/eval_ragas_generate.py` | **新增**：测试集生成全流程 |
| `data/ragas/` | **新增目录**：存放生成的测试集 JSON |
| `src/config/ragas_pairs.py` | **删除** |

## 接口约定

```
run_generate(kb_name: str, kb_id: str, size: int, model: str) -> None
  → 返回 None，成功保存 JSON 文件，失败 sys.exit(1)

_load_latest_testset(kb_id: str) -> tuple[list[str], list[str]]
  → (questions, ground_truth)，用于替代 QUESTIONS/GROUND_TRUTH

_find_next_version(kb_id: str) -> int
  → 扫描 testset_{kb_id}_v*.json，返回最大 version+1

_ensure_vertexai_stub() -> None
  → 检查 vertexai.py stub 是否存在，不存在则创建
```

---

### Task 1: 环境依赖与配置

**Files:**
- Modify: `pyproject.toml:38`
- Modify: `src/config/settings.py:47-48`
- Create: `.venv/.../langchain_community/chat_models/vertexai.py` (手动)

**Interfaces:**
- Consumes: 无
- Produces: `settings.RAGAS_TEST_SIZE` 配置

- [ ] **Step 1: 更新 pyproject.toml 依赖**

当前内容：
```toml
    "ragas==0.3.1",
```

改为：
```toml
    "ragas==0.4.3",
    "langchain-community>=0.3.0",
```

- [ ] **Step 2: 新增 RAGAS_TEST_SIZE 配置**

在 `src/config/settings.py` 中，`RAGAS_LLM_MODEL` 旁边添加：

```python
RAGAS_TEST_SIZE: int = int(os.getenv("RAGAS_TEST_SIZE", "20"))
```

在 `src/config/__init__.py` 中确认 `RAGAS_TEST_SIZE` 已被 `from src.config.settings import *` 重导出。

- [ ] **Step 3: 安装依赖并验证**

```bash
pip install ragas==0.4.3 langchain-community
```

验证：
```bash
python3 -c "
import ragas; print('ragas', ragas.__version__)
from ragas.testset.synthesizers.generate import TestsetGenerator
from ragas.metrics import faithfulness, answer_relevancy, context_recall, context_precision
print('All imports OK')
"
```

Expected output:
```
ragas 0.4.3
All imports OK
```

注意：此处 `ChatVertexAI` import 会失败，由 Task 2 修复。

- [ ] **Step 4: 创建 data/ragas/ 目录**

```bash
mkdir -p data/ragas
touch data/ragas/.gitkeep
```

- [ ] **Step 5: 提交**

```bash
git add pyproject.toml src/config/settings.py src/config/__init__.py data/ragas/.gitkeep
git commit -m "chore: upgrade ragas to 0.4.3, add RAGAS_TEST_SIZE config"
```

---

### Task 2: vertexai stub 自动修复

**Files:**
- Create: `src/cli/eval_ragas_generate.py`（部分，仅 stub 修复部分）

**Interfaces:**
- Produces: `_ensure_vertexai_stub()` 函数

- [ ] **Step 1: 创建 eval_ragas_generate.py 并写入 stub 修复代码**

```python
"""RAGAS 测试集生成模块 — 从知识库文档自动生成 QA 测试集.

本模块被 eval_ragas.py 的 --generate 模式调用，包含：
  - vertexai stub 自动修复（ragas 兼容性）
  - MinIO 文档下载与解析
  - TestsetGenerator 编排
  - 测试集版本管理与 JSON 写入

运行方式：
  python -m src.cli.eval_ragas --kb-name "xxx" --generate --size 20
"""

import os
import re
import json
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional

from loguru import logger


def _ensure_vertexai_stub() -> None:
    """检查 langchain_community.chat_models.vertexai 模块是否存在，
    不存在则创建空 stub 以满足 ragas 的硬导入需求。

    ragas 0.4.x 在 llms/base.py 中:
      from langchain_community.chat_models.vertexai import ChatVertexAI
    但 langchain-community>=0.4 已移除该子模块，
    且 ragas 仅用于 __all__ re-export，不涉及实际逻辑。
    """
    try:
        from langchain_community.chat_models import vertexai  # noqa: F401
    except ImportError:
        # 确定 langchain_community.chat_models 的物理路径
        import langchain_community.chat_models as chat_models_pkg

        pkg_path = Path(chat_models_pkg.__file__).parent
        stub_path = pkg_path / "vertexai.py"
        stub_path.write_text(
            "# Auto-generated stub for ragas compatibility\n"
            "class ChatVertexAI:\n"
            "    pass\n"
            "\n"
            "class VertexAI:\n"
            "    pass\n"
        )
        logger.info("Created vertexai stub at {}", stub_path)
```

- [ ] **Step 2: 验证 stub 修复**

```bash
python3 -c "
from src.cli.eval_ragas_generate import _ensure_vertexai_stub
_ensure_vertexai_stub()
from ragas.llms import LangchainLLMWrapper
from ragas.testset.synthesizers.generate import TestsetGenerator
print('Stub fix + ragas imports OK')
"
```

Expected: `Stub fix + ragas imports OK`

- [ ] **Step 3: 提交**

```bash
git add src/cli/eval_ragas_generate.py
git commit -m "feat: add vertexai stub fix in eval_ragas_generate.py"
```

---

### Task 3: 测试集版本扫描与 JSON Schema

**Files:**
- Modify: `src/cli/eval_ragas_generate.py`（追加函数）

**Interfaces:**
- Produces: `data/ragas/testset_{kb_id}_vN.json` 的文件格式
- Produces: `_find_next_version(kb_id)` → int
- Produces: `_load_latest_testset(kb_id)` → tuple[list[str], list[str]]

- [ ] **Step 1: 实现版本扫描和测试集加载函数**

追加到 `eval_ragas_generate.py`（在 `_ensure_vertexai_stub` 之后）：

```python
# 测试集存储目录
RAGAS_DATA_DIR: str = "data/ragas"

# 测试集 JSON 结构
# {
#   "metadata": {
#     "kb_name": str,
#     "version": int,
#     "generated_at": str,        # ISO 8601
#     "llm_model": str,
#     "testset_size": int,
#     "ragas_version": str,
#     "doc_ids": list[str]
#   },
#   "samples": [
#     {
#       "user_input": str,
#       "reference": str,
#       "reference_contexts": list[str],
#       "synthesizer_name": str
#     }
#   ]
# }


def _find_next_version(kb_id: str) -> int:
    """扫描 data/ragas/ 下 testset_{kb_id}_v*.json，返回下一个版本号。

    Args:
        kb_id: 知识库 UUID

    Returns:
        下一个版本号（从 1 开始）
    """
    pattern = re.compile(rf"^testset_{re.escape(kb_id)}_v(\d+)\.json$")
    max_version = 0
    ragas_dir = Path(RAGAS_DATA_DIR)
    if ragas_dir.exists():
        for f in ragas_dir.iterdir():
            m = pattern.match(f.name)
            if m:
                ver = int(m.group(1))
                if ver > max_version:
                    max_version = ver
    return max_version + 1


def _load_latest_testset(kb_id: str) -> tuple[list[str], list[str]]:
    """加载指定知识库的最新版本测试集。

    Args:
        kb_id: 知识库 UUID

    Returns:
        (questions, ground_truth) 元组，分别对应问题和参考答案列表

    Raises:
        FileNotFoundError: 没有找到该知识库的测试集文件
    """
    pattern = re.compile(rf"^testset_{re.escape(kb_id)}_v(\d+)\.json$")
    max_version = 0
    latest_file: Optional[Path] = None
    ragas_dir = Path(RAGAS_DATA_DIR)
    if ragas_dir.exists():
        for f in ragas_dir.iterdir():
            m = pattern.match(f.name)
            if m:
                ver = int(m.group(1))
                if ver > max_version:
                    max_version = ver
                    latest_file = f

    if latest_file is None:
        raise FileNotFoundError(
            f"No testset found for kb_id={kb_id}. "
            "请先运行 --generate 生成测试集"
        )

    with open(latest_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    questions = [s["user_input"] for s in data["samples"]]
    ground_truth = [s["reference"] for s in data["samples"]]
    return questions, ground_truth
```

- [ ] **Step 2: 提交**

```bash
git add src/cli/eval_ragas_generate.py
git commit -m "feat: add version scan and testset loader"
```

---

### Task 4: 测试集生成核心逻辑

**Files:**
- Modify: `src/cli/eval_ragas_generate.py`（追加 `run_generate` 函数）

**Interfaces:**
- Consumes: `_ensure_vertexai_stub()`, `_find_next_version(kb_id)`
- Produces: `run_generate(kb_name, kb_id, size, model)` → None

- [ ] **Step 1: 实现 run_generate 入口函数**

```python
def run_generate(
    kb_name: str,
    kb_id: str,
    size: int,
    model: str = "",
) -> None:
    """运行测试集生成流程：从 MinIO 取文档 → 解析 → TestsetGenerator → 保存 JSON。

    流程：
      1. 从 MySQL 查询知识库的所有已入库文档
      2. 从 MinIO 逐一下载原始文件
      3. parser 解析为完整文本
      4. 调用 ragas TestsetGenerator.generate_with_langchain_docs()
      5. 写入 data/ragas/testset_{kb_id}_vN.json

    Args:
        kb_name: 知识库名称
        kb_id: 知识库 UUID
        size: 生成的 QA 对数
        model: 生成用的 LLM 模型名（空字符串则使用 RAGAS_LLM_MODEL 或 LLM_MODEL）
    """
    _ensure_vertexai_stub()

    from src.services.app_service import AppService
    from src.infra.db.file_store import FileStore
    from src.parsers.router import ChunkRouter
    from langchain_core.documents import Document as LCDocument
    from langchain_openai import ChatOpenAI
    from ragas.testset.synthesizers.generate import TestsetGenerator
    from src.models import get_embeddings
    from src.config import settings, DASHSCOPE_API_KEY, DASHSCOPE_BASE_URL
    import ragas

    # ---- 1. 查询文档列表 ----
    logger.info("查询知识库文档列表: kb_name={}", kb_name)
    svc = AppService()

    async def _fetch_docs():
        return await svc.db.get_documents(kb_id)

    import asyncio
    docs = asyncio.run(_fetch_docs())
    # 只取状态为 ready 的文档
    ready_docs = [d for d in docs if d.get("status") == "ready"]
    if not ready_docs:
        logger.error("知识库 '{}' 中没有已就绪的文档", kb_name)
        print("✗ 知识库中没有已入库的文档")
        sys.exit(1)

    logger.info("找到 {} 份已入库文档", len(ready_docs))

    # ---- 2. 从 MinIO 下载并解析 ----
    file_store = FileStore()
    router = ChunkRouter()
    full_texts: list[str] = []
    doc_ids: list[str] = []

    for i, doc in enumerate(ready_docs):
        doc_id = doc["id"]
        file_path_key = doc["file_path"]
        filename = doc.get("filename", "unknown")
        logger.info("正在处理 [{}/{}]: {}...", i + 1, len(ready_docs), filename)
        print(f"  下载中: {filename}")

        try:
            data = file_store.download(file_path_key)
            if data is None:
                logger.warning("MinIO 中未找到文件: {}", file_path_key)
                print(f"  ⚠ {filename} 在 MinIO 中不存在，已跳过")
                continue

            # 写入临时文件供 parser 读取
            with tempfile.NamedTemporaryFile(suffix="." + doc["file_type"], delete=False) as tmp:
                tmp.write(data)
                tmp_path = tmp.name

            try:
                parse_result = router.parse(tmp_path)
                if parse_result.is_scanned:
                    logger.warning("文档 '{}' 为扫描件，跳过", filename)
                    print(f"  ⚠ {filename} 是扫描件，已跳过")
                    continue

                # 拼完整文本
                full_text = "\n\n".join(c.content for c in parse_result.chunks)
                full_texts.append(full_text)
                doc_ids.append(doc_id)
                print(f"  ✓ {filename} ({parse_result.total_chars} 字符)")
            except Exception as e:
                logger.warning("文档 '{}' 解析失败: {}", filename, e)
                print(f"  ⚠ {filename} 解析失败，已跳过")
                continue
            finally:
                os.unlink(tmp_path)

        except Exception as e:
            logger.exception("文档 '{}' 处理异常: {}", filename, e)
            print(f"  ✗ {filename} 处理异常，已跳过")
            continue

    if not full_texts:
        logger.error("没有成功解析任何文档，无法生成测试集")
        print("✗ 没有成功解析任何文档")
        sys.exit(1)

    # ---- 3. 初始化 RAGAS TestsetGenerator ----
    eval_model = model or settings.RAGAS_LLM_MODEL or settings.LLM_MODEL
    logger.info("初始化 TestsetGenerator (model={}, size={})...", eval_model, size)
    print(f"\n正在构建知识图谱 ({len(full_texts)} 份文档)...")

    llm = ChatOpenAI(
        model=eval_model,
        temperature=0,
        api_key=DASHSCOPE_API_KEY,
        base_url=DASHSCOPE_BASE_URL,
    )
    embeddings = get_embeddings()

    generator = TestsetGenerator.from_langchain(
        llm=llm,
        embedding_model=embeddings,
    )

    # ---- 4. 生成测试集 ----
    langchain_docs = [
        LCDocument(page_content=text) for text in full_texts
    ]
    logger.info("开始生成测试集 ({} 条)...", size)
    print(f"正在生成测试集 ({size} 条)...")

    try:
        testset = generator.generate_with_langchain_docs(
            documents=langchain_docs,
            testset_size=size,
        )
    except Exception as e:
        logger.exception("TestsetGenerator 调用失败")
        print(f"✗ 测试集生成失败: 请检查 LLM 配置和网络连接")
        sys.exit(1)

    # ---- 5. 序列化为 JSON ----
    samples_list = testset.to_list()
    version = _find_next_version(kb_id)

    output = {
        "metadata": {
            "kb_name": kb_name,
            "version": version,
            "generated_at": datetime.now().isoformat(),
            "llm_model": eval_model,
            "testset_size": len(samples_list),
            "ragas_version": ragas.__version__,
            "doc_ids": doc_ids,
        },
        "samples": samples_list,
    }

    # ---- 6. 原子写入 ----
    os.makedirs(RAGAS_DATA_DIR, exist_ok=True)
    output_path = os.path.join(RAGAS_DATA_DIR, f"testset_{kb_id}_v{version}.json")
    tmp_path = output_path + ".tmp"

    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, output_path)

    logger.info("测试集已保存: {} ({} 条, v{})", output_path, len(samples_list), version)
    print(f"\n测试集已保存: {output_path} (v{version}, {len(samples_list)} 条)")
```

- [ ] **Step 2: 提交**

```bash
git add src/cli/eval_ragas_generate.py
git commit -m "feat: implement run_generate with MinIO+parser+ragas pipeline"
```

---

### Task 5: 改造 eval_ragas.py

**Files:**
- Modify: `src/cli/eval_ragas.py`（多处修改）
- Delete: `src/config/ragas_pairs.py`

**Interfaces:**
- Consumes: `_load_latest_testset(kb_id)` → (questions, truth)
- Consumes: `run_generate(kb_name, kb_id, size, model)` → None

- [ ] **Step 1: 修改 import 和参数解析**

删除第 28 行：
```python
from src.config.ragas_pairs import QUESTIONS, GROUND_TRUTH
```

在 `parse_args()` 中添加 `--generate`、`--size`、`--model` 参数：

```python
    parser.add_argument(
        "--generate",
        action="store_true",
        help="生成测试集模式（从文档自动生成 QA 对）",
    )
    parser.add_argument(
        "--size",
        type=int,
        default=None,
        help="生成测试集的 QA 对数（默认: settings.RAGAS_TEST_SIZE）",
    )
    parser.add_argument(
        "--model",
        type=str,
        default=None,
        help="生成测试集用的 LLM 模型名（默认: RAGAS_LLM_MODEL 或 LLM_MODEL）",
    )
```

删除 `--check` 参数区块：
```python
    parser.add_argument(
        "--check",
        action="store_true",
        help="检查 QA 对数 >= 50，不满足则退出码为 1",
    )
```

- [ ] **Step 2: 重构 main() 入口逻辑**

将整个 `main()` 函数替换为：

```python
def main() -> None:
    """主入口 — 路由到生成模式或评估模式。"""
    args = parse_args()

    # ---- list-kbs 模式 ----
    if args.list_kbs:
        _list_knowledge_bases()
        return

    # ---- kb-name 校验 ----
    if not args.kb_name:
        print("error: --kb-name is required")
        print("Use --list-kbs to see available knowledge bases")
        sys.exit(1)

    kb_name = args.kb_name

    # ---- 查询 kb_id ----
    from src.services.app_service import AppService

    svc = AppService()
    kb_id = svc.db.get_kb_by_name(kb_name)  # 注意：这是 async 的，看下面处理
```

注意：`svc.db.get_kb_by_name()` 是 async 方法（定义在 mysql_db.py:626）。当前代码在 `_list_knowledge_bases` 和 `_save_eval_report` 中用了 `asyncio.run()` 包装。这里也需要同样的方式调用。

保持现有的 `asyncio.run(_list_knowledge_bases())` 模式不变，`kb_id` 查询也走 `asyncio.run()`：

```python
    async def _get_kb_id(name: str) -> Optional[str]:
        return await svc.db.get_kb_by_name(name)

    kb_id = asyncio.run(_get_kb_id(kb_name))
    if not kb_id:
        logger.error("Knowledge base '{}' not found", kb_name)
        print(f"error: 知识库 '{kb_name}' 不存在")
        sys.exit(1)
```

完整 main() 修改：

```python
def main() -> None:
    """主入口 — 路由到生成模式或评估模式。"""
    args = parse_args()

    # ---- list-kbs 模式 ----
    if args.list_kbs:
        asyncio.run(_list_knowledge_bases())
        return

    # ---- kb-name 校验 ----
    if not args.kb_name:
        print("error: --kb-name is required")
        print("Use --list-kbs to see available knowledge bases")
        sys.exit(1)

    kb_name = args.kb_name

    # ---- 查询 kb_id ----
    from src.services.app_service import AppService

    svc = AppService()

    async def _get_kb_id(name: str) -> Optional[str]:
        return await svc.db.get_kb_by_name("", name)

    kb_id = asyncio.run(_get_kb_id(kb_name))
    if not kb_id:
        logger.error("Knowledge base '{}' not found", kb_name)
        print(f"error: 知识库 '{kb_name}' 不存在")
        sys.exit(1)

    # ---- generate 模式 ----
    if args.generate:
        from src.cli.eval_ragas_generate import run_generate

        size = args.size or settings.RAGAS_TEST_SIZE
        model = args.model or ""
        run_generate(kb_name, kb_id, size, model)
        return

    # ---- 评估模式（原有流程改造）----
    session_id = args.session_id
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = (
        args.output or f"{DEFAULT_OUTPUT_DIR}/ragas_eval_{timestamp}.csv"
    )

    # 从 JSON 加载测试集
    try:
        questions, ground_truth = _load_latest_testset(kb_id)
    except FileNotFoundError as e:
        print(f"error: {e}")
        sys.exit(1)

    logger.info("加载测试集: {} 条 QA 对", len(questions))
    logger.info("Evaluating KB '{}'", kb_name)

    # ---- 初始化 RAG 组件 ----
    from datasets import Dataset
    from ragas import evaluate
    from ragas.llms import LangchainLLMWrapper
    from ragas.embeddings import LangchainEmbeddingsWrapper
    from ragas.metrics import (
        faithfulness,
        answer_relevancy,
        context_recall,
        context_precision,
    )
    from langchain_openai import ChatOpenAI
    from src.models import get_embeddings
    from src.rag.chain import RAGChain

    logger.info("Initializing RAGChain...")
    rag_chain = RAGChain()

    logger.info("Checking KB vector store...")
    if rag_chain.vector_store._collection.count() == 0:
        logger.error("Knowledge base '{}' vector store is empty", kb_name)
        print("Knowledge base is empty")
        sys.exit(1)

    eval_model = settings.RAGAS_LLM_MODEL or settings.LLM_MODEL
    logger.info("Initializing RAGAS evaluator ({})...", eval_model)
    llm = ChatOpenAI(
        model=eval_model,
        temperature=0,
        api_key=DASHSCOPE_API_KEY,
        base_url=DASHSCOPE_BASE_URL,
    )
    embeddings = get_embeddings()
    llm_wrapper = LangchainLLMWrapper(llm)
    embeddings_wrapper = LangchainEmbeddingsWrapper(embeddings)

    logger.info("Generating answers for {} questions...", len(questions))
    answers, contexts = generate_answers_and_contexts(
        rag_chain, kb_id, session_id, questions,
    )

    result = run_evaluation(
        questions, ground_truth, answers, contexts,
        llm_wrapper, embeddings_wrapper,
    )

    output_path = save_results_csv(result, questions, ground_truth, output_path)
    save_markdown_report(result, questions, output_path)

    # _save_eval_report 需要 questions 长度
    _save_eval_report(kb_name, result, len(questions), output_path)

    if args.gate:
        check_gate(result, questions)

    logger.info("Evaluation complete.")
```

- [ ] **Step 3: 更新 _save_eval_report 签名**

将 `_save_eval_report(kb_name, result, questions, output_path)` 改为接收整数 `qa_count`：

原函数签名：
```python
def _save_eval_report(
    kb_name: str,
    result,
    questions: list[str],
    output_path: str,
) -> None:
```

改为：
```python
def _save_eval_report(
    kb_name: str,
    result,
    qa_count: int,
    output_path: str,
) -> None:
```

函数体内将 `len(questions)` 替换为 `qa_count`（不含 `detail` 中遍历 questions 的逻辑）。

- [ ] **Step 4: 更新模块 docstring**

删除 `--check` 示例行，改为：

```python
运行方式：
  python -m src.cli.eval_ragas --kb-name "我的知识库"              # 评估指定知识库
  python -m src.cli.eval_ragas --kb-name "我的知识库" --gate        # 评估并检查质量门禁
  python -m src.cli.eval_ragas --kb-name "我的知识库" --generate    # 生成测试集
  python -m src.cli.eval_ragas --kb-name "我的知识库" --generate --size 30  # 生成 30 ��
  python -m src.cli.eval_ragas --list-kbs                          # 列出可用知识库
```

- [ ] **Step 5: 删除 ragas_pairs.py 和 check_qa_count**

删除 `src/config/ragas_pairs.py` 文件：
```bash
rm src/config/ragas_pairs.py
```

删除 `check_qa_count()` 函数定义和 `--check` 相关代码分支。

- [ ] **Step 6: 提交**

```bash
git add src/cli/eval_ragas.py src/config/ragas_pairs.py
git rm src/config/ragas_pairs.py
git commit -m "feat: refactor eval_ragas.py with --generate mode, remove ragas_pairs.py"
```

---

### Task 6: 验证

- [ ] **Step 1: 代码风格检查**

```bash
ruff check . --no-fix
```

Expected: 0 errors

- [ ] **Step 2: 现有测试**

```bash
pytest tests/ -v
```

Expected: 全部通过（如果有测试引用已删除模块则先修复）

- [ ] **Step 3: 测试集生成**

```bash
python -m src.cli.eval_ragas --list-kbs
```

记下目标知识库的名称。

```bash
python -m src.cli.eval_ragas --kb-name "我的知识库" --generate --size 10
```

Expected output:
```
查询知识库文档列表: kb_name=我的知识库
找到 3 份已入库文档
正在处理 [1/3]: tencent_2024_annual.pdf...
  下载中: tencent_2024_annual.pdf
  ✓ tencent_2024_annual.pdf (302415 字符)
...
正在构建知识图谱 (3 份文档)...
正在生成测试集 (10 条)...
测试集已保存: data/ragas/testset_a1b2..._v1.json (v1, 10 条)
```

- [ ] **Step 4: 使用生成的测试集进行评估**

```bash
python -m src.cli.eval_ragas --kb-name "我的知识库"
```

Expected: 正常加载测试集、生成答案、运行评估、保存报告。

- [ ] **Step 5: 确认无遗留问题**

```bash
grep -rn "from src.config.ragas_pairs\|import.*QUESTIONS\|import.*GROUND_TRUTH" src/
```

Expected: 0 results（确认所有旧引用已清理）
