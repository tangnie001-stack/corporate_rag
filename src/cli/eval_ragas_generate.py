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


def run_generate(
    kb_name: str,
    kb_id: str,
    size: int,
    model: str = "",
) -> None:
    """运行测试集生成流程：从 MinIO 取文档 → 解析 → TestsetGenerator → 保存 JSON.

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

    Raises:
        SystemExit: 知识库无就绪文档 / 无成功解析的文档 / 生成失败时退出进程
    """
    _ensure_vertexai_stub()

    from src.services.app_service import AppService
    from src.infra.db.file_store import FileStore
    from src.parsers.router import DocRouter
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
    router = DocRouter()
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
            with tempfile.NamedTemporaryFile(
                suffix="." + doc["file_type"], delete=False
            ) as tmp:
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
    except Exception:
        logger.exception("TestsetGenerator 调用失败")
        print("✗ 测试集生成失败: 请检查 LLM 配置和网络连接")
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
