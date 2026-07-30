"""文档表 ORM 模型。"""

from sqlalchemy import String, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.infra.db.base import Base, IDMixin, TimestampMixin


class DocModel(Base, IDMixin, TimestampMixin):
    __tablename__ = "document"

    kb_id: Mapped[str] = mapped_column(String(36), nullable=False, comment="所属知识库")
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    file_type: Mapped[str] = mapped_column(String(32), default="", comment="文件类型")
    file_size: Mapped[int] = mapped_column(Integer, default=0)
    file_path: Mapped[str | None] = mapped_column(String(1024), comment="MinIO 路径")
    user_id: Mapped[str] = mapped_column(String(36), default="", comment="上传用户")
    md5: Mapped[str | None] = mapped_column(String(64), comment="文件 MD5")
    hash: Mapped[str | None] = mapped_column(String(64), comment="备用哈希")
    status: Mapped[str] = mapped_column(
        String(32), default="pending", comment="pending/processing/ready/failed"
    )
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    chunk_strategy: Mapped[str | None] = mapped_column(String(64))
    processing_state: Mapped[str | None] = mapped_column(String(64))
    processing_progress: Mapped[int] = mapped_column(Integer, default=0)
    processing_message: Mapped[str | None] = mapped_column(String(512))
    error_msg: Mapped[str | None] = mapped_column(String(1024))
    meta_info: Mapped[str | None] = mapped_column(Text, comment="JSON 扩展信息")
    is_deleted: Mapped[int] = mapped_column(Integer, default=0)
