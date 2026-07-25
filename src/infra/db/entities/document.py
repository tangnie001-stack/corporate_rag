"""文档实体 — 对应 document 表。"""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass(slots=True)
class DocEntity:
    """文档实体，对应 document 表一行记录。

    Attributes:
        id: 文档 UUID
        kb_id: 所属知识库 ID（FK → knowledge_base.id）
        filename: 原始文件名
        file_type: 文件类型：pdf / docx / txt
        file_size: 文件大小（字节）
        user_id: 上传用户 ID
        status: 处理状态：pending / processing / ready / failed / deleted
        file_path: 文件存储路径（MinIO 或本地路径）
        hash: 文件 MD5 哈希
        processing_state: 处理阶段：chunking / vectorizing / completed
        processing_progress: 处理进度百分比（0-100）
        processing_message: 处理状态描述消息
        error_msg: 处理失败时的错误信息
        chunk_strategy: 分块策略：parent_child / qa / table_preserving
        chunk_count: 实际分块数量
        meta_info: JSON 格式的扩展元数据
        created_at: 创建时间
    """

    id: str
    """文档 UUID。"""
    kb_id: str
    """所属知识库 ID（FK → knowledge_base.id）。"""
    filename: str
    """原始文件名。"""
    file_type: str = ""
    """文件类型：pdf / docx / txt。"""
    file_size: int = 0
    """文件大小（字节）。"""
    user_id: str = ""
    """上传用户 ID。"""
    status: str = "pending"
    """处理状态：pending / processing / ready / failed / deleted。"""
    file_path: Optional[str] = None
    """文件存储路径（MinIO 或本地路径）。"""
    hash: Optional[str] = None
    """文件 MD5 哈希。"""
    processing_state: Optional[str] = None
    """处理阶段：chunking / vectorizing / completed。"""
    processing_progress: int = 0
    """处理进度百分比（0-100）。"""
    processing_message: Optional[str] = None
    """处理状态描述消息。"""
    error_msg: Optional[str] = None
    """处理失败时的错误信息。"""
    chunk_strategy: str = "parent_child"
    """分块策略：parent_child / qa / table_preserving。"""
    chunk_count: int = 0
    """实际分块数量。"""
    meta_info: Optional[str] = None
    """JSON 格式的扩展元数据。"""
    created_at: Optional[datetime] = None
    """创建时间。"""
