"""背景处理任务测试。

验证 _process_document_task 相关的模块级常量。
"""

import pytest


@pytest.mark.asyncio
async def test_semaphore_value():
    """验证 _process_semaphore 初始值为 3。"""
    from src.api.documents import _process_semaphore

    assert _process_semaphore._value == 3
