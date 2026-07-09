"""API 响应体 Pydantic model。

描述业务数据结构，不含 code/message 包装（由 ResponseEnvelopeMiddleware 统一包装）。
"""

from typing import Optional

from pydantic import BaseModel, field_validator


class LoginResponse(BaseModel):
    """登录成功响应。"""
    token: str  # 登录 token，后续请求通过 Cookie 携带
    user_id: str  # 用户 UUID


class VerifyResponse(BaseModel):
    """Token 校验响应。"""
    valid: bool  # token 是否有效
    user_id: Optional[str] = None  # 对应用户 UUID，无效时为 None


class HealthResponse(BaseModel):
    """健康检查响应。"""
    status: str  # 服务状态，固定 "ok"


class CreateKBResponse(BaseModel):
    """创建知识库的响应体。"""
    id: str  # 知识库 UUID
    created: bool  # 是否为新创建


class KBItem(BaseModel):
    """知识库列表项。"""
    id: str  # 知识库 UUID
    name: str  # 知识库名称
    doc_count: int  # 包含的文档数量


class KBDeleteResponse(BaseModel):
    """删除知识库的响应体。"""
    success: bool  # 是否删除成功
    message: str  # 删除结果描述


class UploadDocumentResponse(BaseModel):
    """文档上传响应。"""
    doc_id: str  # 文档 UUID
    status: str  # 处理状态（processing / ready / failed）
    filename: str  # 原始文件名
    dedup: bool = False  # 是否命中去重


class DocumentListResponse(BaseModel):
    """文档列表项。"""
    id: str  # 文档 UUID
    filename: str  # 文件名
    file_type: str  # 文件类型（pdf / docx / txt）
    file_size: int  # 文件大小（字节）
    status: str  # 处理状态
    created_at: str  # 上传时间
    chunk_count: int = 0  # 分块数量

    @field_validator("file_size", "chunk_count", mode="before")
    @classmethod
    def none_to_zero(cls, v):
        """将 None 转为 0，与数据库可空字段兼容。"""
        return 0 if v is None else v

    @field_validator("file_type", mode="before")
    @classmethod
    def none_to_empty(cls, v):
        """将 None 转为空字符串，与数据库可空字段兼容。"""
        return "" if v is None else v


class DocumentStatusResponse(BaseModel):
    """文档处理状态响应。"""

    status: str  # 处理状态
    chunk_count: int = 0  # 已入库分块数量
    progress: int = 0  # 处理进度百分比
    error: str = ""  # 错误信息
    processing_state: str | None = None  # 处理阶段
    processing_progress: int = 0  # 当前阶段进度
    processing_message: str = ""  # 当前阶段描述

    @field_validator("chunk_count", "progress", "processing_progress", mode="before")
    @classmethod
    def none_to_zero(cls, v):
        """将 None 转为 0，与数据库可空字段兼容。"""
        return 0 if v is None else v

    @field_validator("error", "processing_message", mode="before")
    @classmethod
    def none_to_empty(cls, v):
        """将 None 转为空字符串，与数据库可空字段兼容。"""
        return "" if v is None else v


class ChunkItem(BaseModel):
    """分块预览项。"""
    chunk_id: str  # 分块 UUID
    content: str  # 分块内容（截取前 500 字）
    page: int = 1  # 来源页码
    tokens: int = 0  # token 估算数量
    char_count: int  # 字符数
    block_type: str = "text"  # 块类型（text / table / list）
    parent_content: str | None = None  # 父级块内容

    @field_validator("page", "tokens", mode="before")
    @classmethod
    def none_to_zero(cls, v):
        """将 None 转为 0，与数据库可空字段兼容。"""
        return 0 if v is None else v

    @field_validator("block_type", mode="before")
    @classmethod
    def none_to_empty(cls, v):
        """将 None 转为空字符串，与数据库可空字段兼容。"""
        return "" if v is None else v


class ChunksResponse(BaseModel):
    """分块预览响应。"""
    items: list[ChunkItem]  # 当前页分块列表
    total: int  # 总条数
    page: int  # 当前页码
    page_size: int  # 每页条数


class DocumentDeleteResponse(BaseModel):
    """文档删除响应。"""
    success: bool  # 是否删除成功


class SessionItem(BaseModel):
    """会话列表项。"""
    id: str  # 会话 UUID
    title: str  # 会话标题（首条消息前 20 字）
    kb_id: str  # 关联知识库 UUID
    kb_name: str  # 知识库名称
    message_count: int  # 消息数量
    created_at: Optional[str] = None  # 创建时间
    updated_at: Optional[str] = None  # 最后更新时间


class MessageItem(BaseModel):
    """会话消息项。"""
    role: str  # 角色（user / assistant）
    content: str  # 消息内容
    sources: Optional[str] = None  # 引用来源（JSON 字符串）
    created_at: Optional[str] = None  # 发送时间


class SessionDeleteResponse(BaseModel):
    """会话删除响应。"""
    success: bool  # 是否删除成功
