"""评估报告实体 — 对应 eval_report 表。"""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass(slots=True)
class EvalReportEntity:
    """评估报告实体，对应 eval_report 表一行记录。"""

    id: str
    """报告 UUID。"""
    kb_id: str
    """关联的知识库 ID。"""
    run_type: str = "manual"
    """运行类型：manual / auto。"""
    qa_count: int = 0
    """QA 对数量。"""
    faithfulness: Optional[float] = None
    """忠实度评分。"""
    answer_relevancy: Optional[float] = None
    """答案相关性评分。"""
    context_precision: Optional[float] = None
    """上下文精确度。"""
    context_recall: Optional[float] = None
    """上下文召回率。"""
    overall_score: Optional[float] = None
    """综合评分。"""
    passed: bool = False
    """是否通过评估。"""
    report_path: Optional[str] = None
    """报告文件路径。"""
    triggered_by: Optional[str] = None
    """触发者（用户 ID）。"""
    detail_json: Optional[dict] = None
    """详细评估数据（JSON 可序列化）。"""
    eval_date: Optional[datetime] = None
    """评估日期。"""
