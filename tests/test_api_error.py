"""测试 AppError 业务异常层次。"""

from src.infra.errors import BusinessError


def test_api_error_requires_code_and_message():
    err = BusinessError("TEST_ERROR", "测试错误")
    assert err.code == "TEST_ERROR"
    assert err.message == "测试错误"
    assert err.status == 400


def test_api_error_custom_status():
    err = BusinessError("NOT_FOUND", "不存在", 404)
    assert err.status == 404


def test_api_error_is_exception():
    assert issubclass(BusinessError, Exception)
