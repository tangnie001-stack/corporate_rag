"""测试密码加密与校验工具模块。"""

import hashlib

from src.utils.auth_crypto import (
    hash_password,
    is_bcrypt_hash,
    verify_password,
    verify_password_sha256,
)


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


def test_verify_password_sha256_ok():
    """SHA-256 旧版哈希校验正确。"""
    password = "admin123"
    sha256_hash = hashlib.sha256(password.encode("utf-8")).hexdigest()
    assert verify_password_sha256(password, sha256_hash) is True


def test_verify_password_sha256_wrong():
    """SHA-256 旧版哈希校验错误。"""
    password = "admin123"
    sha256_hash = hashlib.sha256(password.encode("utf-8")).hexdigest()
    assert verify_password_sha256("wrong_password", sha256_hash) is False


def test_is_bcrypt_hash_true():
    """bcrypt 格式检测应返回 True。"""
    hashed = hash_password("test")
    assert is_bcrypt_hash(hashed) is True


def test_is_bcrypt_hash_false():
    """SHA-256 格式检测应返回 False。"""
    sha256_hash = hashlib.sha256(b"test").hexdigest()
    assert is_bcrypt_hash(sha256_hash) is False


def test_verify_password_fallback_sha256():
    """verify_password 遇到 SHA-256 哈希应返回 False 不抛异常。"""
    sha256_hash = hashlib.sha256(b"admin123").hexdigest()
    result = verify_password("admin123", sha256_hash)
    assert result is False  # bcrypt 无法校验 SHA-256，返回 False
