"""响应码常量测试。"""

from src.config.response_codes import Code


def test_success_constants():
    assert Code.SUCCESS == "SUCCESS"
    assert Code.SUCCESS_MSG == "操作成功"


def test_error_codes_have_messages():
    """每个错误码都有对应的 _MSG 常量。"""
    codes = [attr for attr in dir(Code) if attr.isupper() and not attr.endswith("_MSG")]
    for code in codes:
        msg_attr = f"{code}_MSG"
        assert hasattr(Code, msg_attr), f"{code} missing {msg_attr}"
        assert getattr(Code, msg_attr), f"{code} has empty message"
