"""数据实体 — 所有数据源（MySQL / ChromaDB / BM25）的实体类型。"""

from src.infra.db.entities.search import ChunkResult, ChunkQueryResult
from src.infra.db.entities.kb import KbEntity, KbListItem
from src.infra.db.entities.document import DocEntity
from src.infra.db.entities.chat import SessionEntity, SessionListItem, MessageEntity
from src.infra.db.entities.user import UserEntity
from src.infra.db.entities.eval_report import EvalReportEntity

__all__ = [
    "ChunkResult",
    "ChunkQueryResult",
    "KbEntity",
    "KbListItem",
    "DocEntity",
    "SessionEntity",
    "SessionListItem",
    "MessageEntity",
    "UserEntity",
    "EvalReportEntity",
]
