"""评估报告 Repo — eval_report 表 CRUD。"""

import json
import uuid
from typing import Optional
from sqlalchemy import select
from src.infra.db.models.eval_report import EvalReportModel


class EvalRepo:
    """评估报告 CRUD 仓库。"""

    def __init__(self, session_factory):
        self._sf = session_factory

    async def insert_report(self, report: EvalReportModel) -> None:
        async with self._sf() as session:
            detail_str = (
                json.dumps(report.detail_json, ensure_ascii=False)
                if report.detail_json
                else None
            )
            record = EvalReportModel(
                id=str(uuid.uuid4()),
                kb_id=report.kb_id,
                run_type=report.run_type,
                qa_count=report.qa_count,
                faithfulness=report.faithfulness,
                answer_relevancy=report.answer_relevancy,
                context_precision=report.context_precision,
                context_recall=report.context_recall,
                overall_score=report.overall_score,
                passed=report.passed,
                report_path=report.report_path,
                triggered_by=report.triggered_by,
                detail_json=detail_str,
                eval_date=report.eval_date,
            )
            session.add(record)
            await session.commit()

    async def get_latest_report(self, kb_id: str) -> Optional[EvalReportModel]:
        async with self._sf() as session:
            stmt = (
                select(EvalReportModel)
                .where(EvalReportModel.kb_id == kb_id)
                .order_by(EvalReportModel.created_at.desc())
                .limit(1)
            )
            result = await session.execute(stmt)
            return result.scalar_one_or_none()
