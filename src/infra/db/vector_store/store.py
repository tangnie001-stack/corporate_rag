"""ChromaDB 写入和删除操作。"""

from typing import Optional

from chromadb.errors import NotFoundError
from loguru import logger

from src.chunking.validator import ChunkData


def add_chunks(
    collection,
    kb_id: str,
    chunks: list[ChunkData],
    doc_id: str,
    embeddings: Optional[list[list[float]]] = None,
) -> int:
    """批量写入分块到 collection。

    为每个分块生成唯一 ID（格式 {doc_id}:{index}），并在 metadata 中注入
    chunk_index / chunk_total / doc_id 字段。

    Args:
        collection: ChromaDB Collection 实例
        kb_id: 知识库 ID（仅用于日志）
        chunks: 分块数据列表
        doc_id: 文档 ID
        embeddings: 可选的预计算 embedding 列表，传入后跳过 embedding 模型调用

    Returns:
        实际写入的分块数量

    Raises:
        Exception: ChromaDB 写入失败时向上抛出
    """
    if not chunks:
        return 0

    ids = []
    documents = []
    metadatas = []

    for i, chunk in enumerate(chunks):
        chunk_id = f"{doc_id}:{i}"
        ids.append(chunk_id)
        documents.append(chunk.content)
        meta = dict(chunk.metadata)
        meta.update({"chunk_index": i, "chunk_total": len(chunks), "doc_id": doc_id})
        meta.setdefault("source", "")
        meta.setdefault("page", 0)
        metadatas.append(meta)

    kwargs = {"ids": ids, "documents": documents, "metadatas": metadatas}
    if embeddings is not None:
        kwargs["embeddings"] = embeddings

    try:
        collection.add(**kwargs)
    except Exception as e:
        logger.exception(
            "ChromaDB add_chunks failed: kb_id={} doc_id={} error={}", kb_id, doc_id, e
        )
        raise
    logger.info(
        "ChromaDB add_chunks success: kb_id={} doc_id={} count={}",
        kb_id,
        doc_id,
        len(ids),
    )
    return len(ids)


def delete_document(collection, doc_id: str) -> int:
    """删除指定文档的所有分块。

    先通过 doc_id 查询获取所有分块 ID，再批量删除。

    Args:
        collection: ChromaDB Collection 实例
        doc_id: 文档 ID

    Returns:
        删除的分块数量

    Raises:
        NotFoundError: collection 不存在（静默返回 0）
    """
    try:
        results = collection.get(where={"doc_id": doc_id})
        if results["ids"]:
            collection.delete(ids=results["ids"])
            count = len(results["ids"])
            logger.info("ChromaDB delete_document: doc_id={} deleted={}", doc_id, count)
            return count
        return 0
    except NotFoundError:
        return 0


def delete_collection(chroma_client, name: str, cache_key: str, cache: dict) -> bool:
    """删除整个 collection。

    同时清理内存缓存中的对应条目。

    Args:
        chroma_client: ChromaDB 客户端实例
        name: collection 完整名称
        cache_key: 缓存键（知识库 ID）
        cache: 要清理的内存缓存字典

    Returns:
        是否删除成功（collection 不存在时返回 False）
    """
    try:
        chroma_client.delete_collection(name)
        cache.pop(cache_key, None)
        logger.info("Deleted collection '{}'", name)
        return True
    except (NotFoundError, ValueError):
        logger.warning("Collection '{}' not found for deletion", name)
        return False
