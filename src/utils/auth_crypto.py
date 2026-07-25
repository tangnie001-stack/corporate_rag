"""密码加密与校验工具模块 — 使用 bcrypt 对明文密码进行哈希和校验。

本模块不依赖任何项目内部的业务模块，仅依赖 bcrypt 第三方库。
"""

import bcrypt


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
    return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
