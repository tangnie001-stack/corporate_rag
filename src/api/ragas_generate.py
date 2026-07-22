"""RAGAS 测试集生成 API — 在 Docker 容器内触发生成流程。"""

import json
import os

from fastapi import APIRouter
from loguru import logger
from pydantic import BaseModel

from src.api.model.response import BaseResponse
from src.cli.eval_ragas_generate import _find_next_version, run_generate
from src.config import RAGAS_DATA_DIR, RAGAS_USER_ID
from src.services.app_service import AppService

router = APIRouter()


class RagasGenerateRequest(BaseModel):
    """RAGAS 测试集生成请求。"""

    kb_name: str  # 知识库名称
    size: int = 20  # 生成的 QA 对数


@router.post("/ragas/generate")
async def ragas_generate(body: RagasGenerateRequest) -> BaseResponse:
    """触发 RAGAS 测试集生成（同步，等待生成完成后返回）。

    Args:
        kb_name: 知识库名称
        size: QA 对数（默认 20，从 settings.RAGAS_TEST_SIZE 读取）

    Returns:
        BaseResponse: data 包含 version 和 testset_size
    """
    logger.info("RAGAS generate requested: kb_name={} size={}", body.kb_name, body.size)

    try:
        # ---- 查询 kb_id ----
        svc = AppService()
        kb_id = await svc.db.get_kb_by_name(RAGAS_USER_ID, body.kb_name)
        if not kb_id:
            logger.warning("Knowledge base '{}' not found", body.kb_name)
            return BaseResponse(
                code=1,
                message=f"知识库 '{body.kb_name}' 不存在",
                data=None,
            )

        # ---- 预检版本号 ----
        version = _find_next_version(kb_id)

        # ---- 触发生成 ----
        run_generate(body.kb_name, kb_id, body.size, model="")

        # ---- 从生成的 JSON 中读取测试集信息 ----
        output_path = os.path.join(RAGAS_DATA_DIR, f"testset_{kb_id}_v{version}.json")
        if os.path.exists(output_path):
            with open(output_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            testset_size = len(data.get("samples", []))
            logger.info(
                "Testset generated: kb_name={} version={} size={}",
                body.kb_name,
                version,
                testset_size,
            )
            return BaseResponse(
                data={
                    "version": version,
                    "testset_size": testset_size,
                }
            )

        # 理论上不应走到这里
        logger.warning("Testset file not found after generation: {}", output_path)
        return BaseResponse(
            code=1,
            message="生成完成但未找到测试集文件",
            data=None,
        )

    except Exception as e:
        logger.exception("RAGAS generate failed: {}", e)
        return BaseResponse(
            code=1,
            message=str(e),
            data=None,
        )
