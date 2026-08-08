"""RAGAS 测试集生成 API — 在 Docker 容器内触发生成流程。"""

import asyncio
import json
import os

from fastapi import APIRouter
from loguru import logger
from pydantic import BaseModel

from src.api.schema import ResponseModel
from src.cli.eval_ragas_generate import _find_next_version, run_generate
from src.services.app_service import AppService

router = APIRouter()


class RagasGenerateRequest(BaseModel):
    """RAGAS 测试集生成请求。"""

    kb_name: str  # 知识库名称
    size: int = 20  # 生成的 QA 对数


@router.post("/ragas/generate", response_model=ResponseModel)
async def ragas_generate(body: RagasGenerateRequest):
    """触发 RAGAS 测试集生成（同步，等待生成完成后返回）。

    Args:
        kb_name: 知识库名称
        size: QA 对数（默认 20，从 settings.RAGAS_TEST_SIZE 读取）

    Returns:
        ResponseModel: data 包含 version 和 testset_size
    """
    logger.info("RAGAS generate requested: kb_name={} size={}", body.kb_name, body.size)

    try:
        # ---- 查询 kb_id ----
        svc = AppService()
        kb_id = await svc.get_kb_by_name(svc.settings.RAGAS_USER_ID, body.kb_name)
        if not kb_id:
            logger.warning("Knowledge base '{}' not found", body.kb_name)
            return ResponseModel(
                code="ERROR",
                message=f"知识库 '{body.kb_name}' 不存在",
                data=None,
            )

        # ---- 预检版本号 ----
        version = _find_next_version(kb_id)

        # ---- 触发生成 ----
        run_generate(kb_id, body.size, model="")

        # ---- 从生成的 JSON 中读取测试集信息 ----
        output_path = os.path.join(
            svc.settings.RAGAS_DATA_DIR, f"testset_{kb_id}_v{version}.json"
        )
        if os.path.exists(output_path):

            def _read_testset(path: str) -> dict:
                """同步读取测试集 JSON（在 to_thread 中执行，避免阻塞事件循环）。"""
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)

            data = await asyncio.to_thread(_read_testset, output_path)
            testset_size = len(data.get("samples", []))
            logger.info(
                "Testset generated: kb_name={} version={} size={}",
                body.kb_name,
                version,
                testset_size,
            )
            return ResponseModel(
                data={
                    "version": version,
                    "testset_size": testset_size,
                }
            )

        # 理论上不应走到这里
        logger.warning("Testset file not found after generation: {}", output_path)
        return ResponseModel(
            code="ERROR",
            message="生成完成但未找到测试集文件",
            data=None,
        )

    except Exception as e:  # noqa: BLE001
        logger.exception("RAGAS generate failed: {}", e)
        return ResponseModel(
            code="ERROR",
            message=str(e),
            data=None,
        )
