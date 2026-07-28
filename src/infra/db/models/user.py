"""用户表 ORM 模型。"""


from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from src.infra.db.base import Base, IDMixin, TimestampMixin


class UserModel(Base, IDMixin, TimestampMixin):
    __tablename__ = "users"

    account: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, comment="登录账号")
    password: Mapped[str] = mapped_column(String(256), nullable=False, comment="bcrypt 哈希")
    token: Mapped[str | None] = mapped_column(String(256), comment="当前登录 token")
