"""RAGAS 测试集生成模块 — 从知识库文档自动生成 QA 测试集.

本模块被 eval_ragas.py 的 --generate 模式调用，包含：
  - vertexai stub 自动修复（ragas 兼容性）
  - 从 ChromaDB 读取已有分块数据
  - TestsetGenerator 编排
  - 测试集版本管理与 JSON 写入

运行方式：
  python -m src.cli.eval_ragas --kb-name "xxx" --generate --size 20
"""

import os
import re
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from loguru import logger

from src.config import RAGAS_DATA_DIR


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


def run_generate(
    kb_name: str,
    kb_id: str,
    size: int,
    model: str = "",
    use_filter: bool = False,
) -> None:
    """运行测试集生成流程：从 ChromaDB 取 chunk → TestsetGenerator → 保存 JSON.

    流程：
      1. 从白名单获取 doc_ids
      2. 从 ChromaDB 按 doc_id 取出已有分块
      3. 脱敏后传给 generate_with_chunks()
      4. 写入 data/ragas/testset_{kb_id}_vN.json

    Args:
        kb_name: 知识库名称
        kb_id: 知识库 UUID
        size: 生成的 QA 对数
        model: 生成用的 LLM 模型名（空字符串则使用 RAGAS_LLM_MODEL 或 LLM_MODEL）
        use_filter: 是否启用 LLM 节点过滤（关闭可节省约 70 次 LLM 调用）

    Raises:
        SystemExit: ChromaDB 中无数据 / 生成失败时退出进程
    """
    _ensure_vertexai_stub()

    from langchain_core.documents import Document as LCDocument
    from langchain_openai import ChatOpenAI
    from ragas.testset.synthesizers.generate import TestsetGenerator
    from src.models import get_embeddings
    from src.config import settings, DASHSCOPE_API_KEY, DASHSCOPE_BASE_URL
    from src.infra.desensitize import desensitize
    from src.config.settings import RAGAS_DOC_WHITELIST
    from src.infra.db.vector_store import VectorStore
    import ragas

    # ---- 1. 从 ChromaDB 按白名单 doc_id 取 chunk ----
    logger.info("从 ChromaDB 读取分块: kb_id={}, whitelist={}", kb_id, RAGAS_DOC_WHITELIST)
    vector_store = VectorStore()
    langchain_chunks: list[LCDocument] = []
    doc_ids: list[str] = []
    success_count = 0

    for doc_id in RAGAS_DOC_WHITELIST:
        chunks_data = vector_store.get_chunks_by_doc_id(doc_id, kb_id)
        if not chunks_data:
            logger.warning("ChromaDB 中未找到文档的 chunk: {}", doc_id)
            print(f"  ⚠ doc_id={doc_id} 在 ChromaDB 中无数据，已跳过")
            continue

        for c in chunks_data:
            safe_content = desensitize(c["content"])
            meta = dict(c.get("metadata", {}))
            meta["parent_content"] = ""  # 清空原文，避免敏感信息泄漏
            langchain_chunks.append(LCDocument(
                page_content=safe_content,
                metadata=meta,
            ))
        doc_ids.append(doc_id)
        success_count += 1
        print(f"  ✓ doc_id={doc_id} ({len(chunks_data)} 个 chunk)")

    if success_count == 0:
        logger.error("白名单中所有文档在 ChromaDB 中均无 chunk 数据")
        print("✗ 白名单中所有文档在 ChromaDB 中均无数据")
        sys.exit(1)

    logger.info("成功读取 {} 份文档，共 {} 个 chunk", success_count, len(langchain_chunks))

    # ---- 3. 初始化 RAGAS TestsetGenerator ----
    eval_model = model or settings.RAGAS_LLM_MODEL or settings.LLM_MODEL
    logger.info("初始化 TestsetGenerator (model={}, size={}, chunks={})...", eval_model, size, len(langchain_chunks))
    print(f"\n正在构建知识图谱 ({len(langchain_chunks)} 个 chunk)...")

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
    # 构建 transforms
    # LLM 步骤: SummaryExtractor + NERExtractor = 2 次/节点
    # 非 LLM 步骤: CustomNodeFilter + EmbeddingExtractor + OverlapScoreBuilder
    transforms = None
    if not use_filter:
        from ragas.testset.graph import NodeType
        from ragas.testset.transforms.filters import CustomNodeFilter
        from ragas.testset.transforms.extractors import SummaryExtractor, EmbeddingExtractor
        from ragas.testset.transforms.extractors.llm_based import NERExtractor
        from ragas.testset.transforms.relationship_builders import OverlapScoreBuilder

        def _filter_chunks(node):
            return node.type == NodeType.CHUNK

        transforms = [
            CustomNodeFilter(llm=generator.llm, filter_nodes=_filter_chunks),
            SummaryExtractor(llm=generator.llm, filter_nodes=_filter_chunks),
            EmbeddingExtractor(
                embedding_model=generator.embedding_model,
                property_name="summary_embedding",
                embed_property_name="summary",
                filter_nodes=_filter_chunks,
            ),
            NERExtractor(llm=generator.llm, filter_nodes=_filter_chunks),
            OverlapScoreBuilder(threshold=0.01),
        ]
        logger.info("使用自定义 transforms 步骤: {}", [type(t).__name__ for t in transforms])

    logger.info("开始生成测试集 ({} 条)...", size)
    print(f"正在生成测试集 ({size} 条)...")

    try:
        testset = generator.generate_with_chunks(
            chunks=langchain_chunks,
            testset_size=size,
            transforms=transforms,
        )
    except Exception as e:
        logger.exception("TestsetGenerator 调用失败")
        print(f"✗ 测试集生成失败: {e}")
        print(f"✗ 异常类型: {type(e).__name__}")
        import traceback
        traceback.print_exc()
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

    logger.info(
        "测试集已保存: {} ({} 条, v{})", output_path, len(samples_list), version
    )
    print(f"\n测试集已保存: {output_path} (v{version}, {len(samples_list)} 条)")
