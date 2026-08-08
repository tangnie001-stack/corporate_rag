"""评估报告表 ORM 模型。"""

from datetime import datetime

from sqlalchemy import Boolean, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from src.infra.db.base import Base, UTCDateTime


class EvalReportModel(Base):
    __tablename__ = "eval_report"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: __import__("uuid").uuid4().hex
    )
    kb_id: Mapped[str] = mapped_column(String(36), nullable=False)
    run_type: Mapped[str] = mapped_column(String(64), default="")
    qa_count: Mapped[int] = mapped_column(Integer, default=0)
    faithfulness: Mapped[float] = mapped_column(default=0.0)
    answer_relevancy: Mapped[float] = mapped_column(default=0.0)
    context_precision: Mapped[float] = mapped_column(default=0.0)
    context_recall: Mapped[float] = mapped_column(default=0.0)
    overall_score: Mapped[float] = mapped_column(default=0.0)
    passed: Mapped[bool] = mapped_column(Boolean, default=False)
    report_path: Mapped[str | None] = mapped_column(String(512))
    triggered_by: Mapped[str | None] = mapped_column(String(64))
    detail_json: Mapped[str | None] = mapped_column(Text, comment="JSON 详细评估数据")
    eval_date: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now(), nullable=False
    )
