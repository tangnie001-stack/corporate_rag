"""ChromaDB 写入和删除操作。"""

from chromadb.errors import NotFoundError
from loguru import logger
from src.parsers.base import ChunkData


def add_chunks(collection, kb_id: str, chunks: list[ChunkData], doc_id: str) -> int:
    """批量写入分块到 collection。"""
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

    try:
        collection.add(ids=ids, documents=documents, metadatas=metadatas)
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
    """删除指定文档的所有分块。"""
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
    """删除整个 collection。"""
    try:
        chroma_client.delete_collection(name)
        cache.pop(cache_key, None)
        logger.info("Deleted collection '{}'", name)
        return True
    except (NotFoundError, ValueError):
        logger.warning("Collection '{}' not found for deletion", name)
        return False
