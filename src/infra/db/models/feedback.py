"""答案反馈表 ORM 模型。"""

from datetime import datetime

from sqlalchemy import Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from src.infra.db.base import Base, IDMixin, UTCDateTime


class FeedbackModel(Base, IDMixin):
    """用户对单条答案的反馈记录（点赞/点踩 + 可选评论）。"""

    __tablename__ = "feedback"

    # 会话 ID，用于定位反馈所属会话
    session_id: Mapped[str] = mapped_column(
        String(36), nullable=False, comment="会话 ID"
    )
    # 会话内消息序号（前端消息数组索引，从 0 起），非数据库自增 ID
    message_index: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="会话内消息序号"
    )
    # 评分：positive（点赞）/ negative（点踩）
    rating: Mapped[str] = mapped_column(
        String(16), nullable=False, comment="评分: positive/negative"
    )
    # 用户评论，可为空字符串
    comment: Mapped[str] = mapped_column(
        Text, nullable=False, default="", comment="用户评论"
    )
    # 全链路追踪 ID（前端从 SSE done 事件记录并随反馈回传，用于还原生成链路）
    trace_id: Mapped[str] = mapped_column(
        String(64), nullable=False, default="", comment="全链路追踪 ID"
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now(), nullable=False
    )
