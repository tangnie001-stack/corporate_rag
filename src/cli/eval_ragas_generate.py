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
