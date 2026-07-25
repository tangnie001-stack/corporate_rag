"""密码加密与校验工具模块 — 使用 bcrypt 对明文密码进行哈希和校验。

本模块不依赖任何项目内部的业务模块，仅依赖 bcrypt 和 hashlib 标准库。
"""

import bcrypt
import hashlib


def hash_password(password: str) -> str:
    """对明文密码进行 bcrypt 哈希（自动生成随机 salt）。

    Args:
        password: 明文密码

    Returns:
        哈希后的密码字符串（包含 salt，可直接存入数据库）
    """
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    """校验明文密码是否匹配 bcrypt 哈希。

    Args:
        password: 待校验的明文密码
        password_hash: 数据库中存储的 bcrypt 哈希

    Returns:
        True 表示匹配，False 表示不匹配
    """
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        # 不是 bcrypt 格式（可能是旧版 SHA-256 哈希），返回 False
        return False


def verify_password_sha256(password: str, password_hash: str) -> bool:
    """校验明文密码是否匹配 SHA-256 哈希（旧版兼容）。

    Args:
        password: 待校验的明文密码
        password_hash: 数据库中存储的 SHA-256 哈希

    Returns:
        True 表示匹配，False 表示不匹配
    """
    return hashlib.sha256(password.encode("utf-8")).hexdigest() == password_hash


def is_bcrypt_hash(password_hash: str) -> bool:
    """判断密码哈希是否为 bcrypt 格式。

    bcrypt 哈希以 $2b$ 或 $2a$ 开头。

    Args:
        password_hash: 待检测的哈希字符串

    Returns:
        True 表示是 bcrypt 哈希
    """
    return password_hash.startswith("$2b$") or password_hash.startswith("$2a$")
