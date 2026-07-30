"""会话和消息表 ORM 模型。"""

from datetime import datetime

from sqlalchemy import String, Text, Integer, func
from sqlalchemy.orm import Mapped, mapped_column

from src.infra.db.base import Base, IDMixin, TimestampMixin, UTCDateTime


class SessionModel(Base, IDMixin, TimestampMixin):
    __tablename__ = "sessions"

    user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    kb_id: Mapped[str] = mapped_column(String(36), default="")
    title: Mapped[str] = mapped_column(String(256), default="新对话")
    is_deleted: Mapped[int] = mapped_column(Integer, default=0)


class MessageModel(Base):
    __tablename__ = "conversation_history"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: __import__("uuid").uuid4().hex
    )
    session_id: Mapped[str] = mapped_column(String(36), nullable=False)
    kb_id: Mapped[str] = mapped_column(String(36), default="")
    role: Mapped[str] = mapped_column(
        String(32), nullable=False, comment="user/assistant/system"
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    sources: Mapped[str | None] = mapped_column(Text, comment="来源引用 JSON")
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    model_name: Mapped[str | None] = mapped_column(String(64), comment="模型名称")
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now(), nullable=False
    )
