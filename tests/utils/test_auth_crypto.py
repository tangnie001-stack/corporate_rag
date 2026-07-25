"""测试密码加密与校验工具模块。"""

from src.utils.auth_crypto import hash_password, verify_password


def test_hash_password_returns_string():
    """hash_password 应该返回字符串。"""
    result = hash_password("mypassword123")
    assert isinstance(result, str)
    assert len(result) > 0


def test_hash_password_uses_salt():
    """每次调用应该返回不同的哈希值（含随机 salt）。"""
    h1 = hash_password("samepassword")
    h2 = hash_password("samepassword")
    assert h1 != h2


def test_verify_password_correct():
    """正确的密码应该验证通过。"""
    hashed = hash_password("correct_password")
    assert verify_password("correct_password", hashed) is True


def test_verify_password_wrong():
    """错误的密码应该验证失败。"""
    hashed = hash_password("real_password")
    assert verify_password("wrong_password", hashed) is False


def test_verify_password_empty():
    """空密码验证。"""
    hashed = hash_password("")
    assert verify_password("", hashed) is True
    assert verify_password("x", hashed) is False
