"""评估报告 Repo — eval_report 表 CRUD。"""

import uuid
import json
from typing import Optional
from src.config.queries import (
    CREATE_EVAL_REPORT_TABLE,
    INSERT_EVAL_REPORT,
    SELECT_LATEST_EVAL_REPORT,
)
from src.infra.db.entities import EvalReportEntity


class EvalRepo:
    def __init__(self, mysql_db):
        self._pool_getter = mysql_db._get_pool

    async def ensure_table(self) -> None:
        pool = await self._pool_getter()
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(CREATE_EVAL_REPORT_TABLE)
            await conn.commit()

    async def insert_report(self, report: EvalReportEntity) -> None:
        await self.ensure_table()
        pool = await self._pool_getter()
        detail_str = (
            json.dumps(report.detail_json, ensure_ascii=False)
            if report.detail_json
            else None
        )
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    INSERT_EVAL_REPORT,
                    (
                        str(uuid.uuid4()),
                        report.kb_id,
                        report.run_type,
                        report.qa_count,
                        report.faithfulness,
                        report.answer_relevancy,
                        report.context_precision,
                        report.context_recall,
                        report.overall_score,
                        1 if report.passed else 0,
                        report.report_path,
                        report.triggered_by,
                        detail_str,
                    ),
                )
            await conn.commit()

    async def get_latest_report(self, kb_id: str) -> Optional[EvalReportEntity]:
        await self.ensure_table()
        pool = await self._pool_getter()
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(SELECT_LATEST_EVAL_REPORT, (kb_id,))
                row = await cursor.fetchone()
        if not row:
            return None
        detail = json.loads(row["detail_json"]) if row.get("detail_json") else None
        return EvalReportEntity(
            id=row["id"],
            kb_id=row["kb_id"],
            run_type=row["run_type"],
            qa_count=row["qa_count"],
            faithfulness=row["faithfulness"],
            answer_relevancy=row["answer_relevancy"],
            context_precision=row["context_precision"],
            context_recall=row["context_recall"],
            overall_score=row["overall_score"],
            passed=bool(row["passed"]),
            report_path=row["report_path"],
            triggered_by=row["triggered_by"],
            detail_json=detail,
            eval_date=row["eval_date"],
        )
