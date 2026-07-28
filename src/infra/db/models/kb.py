"""知识库表 ORM 模型。"""


from sqlalchemy import String, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from src.infra.db.base import Base, IDMixin, TimestampMixin


class KbModel(Base, IDMixin, TimestampMixin):
    __tablename__ = "knowledge_base"

    user_id: Mapped[str] = mapped_column(String(32), nullable=False, comment="所属用户")
    name: Mapped[str] = mapped_column(String(256), nullable=False, comment="知识库名称")
    description: Mapped[str] = mapped_column(String(1024), default="", comment="描述")
    doc_count: Mapped[int] = mapped_column(Integer, default=0, comment="关联文档数")
    is_deleted: Mapped[int] = mapped_column(Integer, default=0, comment="软删除标志")

    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uk_user_kb"),
    )
