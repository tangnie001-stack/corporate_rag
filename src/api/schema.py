"""统一响应模型。

所有 API handler 通过此模型返回统一格式的响应体：
  {"code": "SUCCESS", "message": "操作成功", "data": ...}
"""

from typing import Any

from pydantic import BaseModel


class ResponseModel(BaseModel):
    """统一成功响应模型。

    handler 用法:
        @router.post("/xxx", response_model=ResponseModel)
        async def handler():
            return ResponseModel(data=result)

    字段说明:
        code: 业务状态码，"SUCCESS" 表示成功，其他值表示业务异常
        message: 操作结果描述文本
        data: 响应数据，可为任意可 JSON 序列化的值
    """

    code: str = "SUCCESS"
    message: str = "操作成功"
    data: Any = None
