"""测试 ApiError 业务异常。"""

import pytest
from src.infra.api_error import ApiError


def test_api_error_requires_code_and_message():
    err = ApiError("TEST_ERROR", "测试错误")
    assert err.code == "TEST_ERROR"
    assert err.message == "测试错误"
    assert err.status == 400


def test_api_error_custom_status():
    err = ApiError("NOT_FOUND", "不存在", 404)
    assert err.status == 404


def test_api_error_is_exception():
    assert issubclass(ApiError, Exception)
