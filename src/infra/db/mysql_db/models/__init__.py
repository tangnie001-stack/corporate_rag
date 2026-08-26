"""SQLAlchemy ORM 模型统一导出。"""

# alembic env 以本包为 target_metadata 来源；FeedbackModel 复用
# src.infra.db.models 的定义，避免同一表两份类声明
from src.infra.db.models.feedback import FeedbackModel
from src.infra.db.mysql_db.models.chat import MessageModel, SessionModel
from src.infra.db.mysql_db.models.document import DocModel
from src.infra.db.mysql_db.models.eval_report import EvalReportModel
from src.infra.db.mysql_db.models.kb import KbModel
from src.infra.db.mysql_db.models.user import UserModel

__all__ = [
    "DocModel",
    "EvalReportModel",
    "FeedbackModel",
    "KbModel",
    "MessageModel",
    "SessionModel",
    "UserModel",
]
