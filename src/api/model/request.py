"""API 请求体 Pydantic model。"""

from pydantic import BaseModel


class LoginRequest(BaseModel):
    """登录请求体。"""
    account: str  # 账号
    password: str  # 密码（明文，服务端做 hash 比对）


class CreateKBRequest(BaseModel):
    """创建知识库的请求体。"""
    name: str  # 知识库名称（必填）
    description: str = ""  # 知识库描述（可选）


class KBDeleteRequest(BaseModel):
    """删除知识库请求体。"""
    kb_id: str  # 要删除的知识库 UUID


class DocumentListRequest(BaseModel):
    """文档列表请求体。"""
    kb_id: str  # 知识库 UUID


class DocumentStatusRequest(BaseModel):
    """文档状态请求体。"""
    kb_id: str  # 知识库 UUID
    doc_id: str  # 文档 UUID


class DocumentChunksRequest(BaseModel):
    """分块预览请求体。"""
    kb_id: str  # 知识库 UUID
    doc_id: str  # 文档 UUID
    page: int = 1  # 页码，从 1 开始
    page_size: int = 50  # 每页条数


class DocumentDeleteRequest(BaseModel):
    """文档删除请求体。"""
    kb_id: str  # 知识库 UUID
    doc_id: str  # 文档 UUID


class SessionMessagesRequest(BaseModel):
    """会话消息请求体。"""
    session_id: str  # 会话 UUID


class SessionDeleteRequest(BaseModel):
    """会话删除请求体。"""
    session_id: str  # 会话 UUID
