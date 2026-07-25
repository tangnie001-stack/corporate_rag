"""ChromaDB 查询操作，返回类型化的 ChunkResult/ChunkQueryResult。"""

from loguru import logger
from src.core.logging import LOG_MAX_BODY
from src.config import TOP_K_RETRIEVAL, EMBEDDING_MODEL
from src.infra.db.entities.search import ChunkResult, ChunkQueryResult


def similarity_search(collection, embed_fn, kb_id, query, k=5) -> list[ChunkResult]:
    """语义相似度检索，返回 list[ChunkResult]。"""
    query_vec = embed_fn.embed_query(query)
    results = collection.query(
        query_embeddings=[query_vec],
        n_results=min(k, 100),
    )

    formatted = []
    if results["ids"] and results["ids"][0]:
        for i in range(len(results["ids"][0])):
            formatted.append(
                ChunkResult(
                    id=results["ids"][0][i],
                    content=results["documents"][0][i] if results["documents"] else "",
                    metadata=results["metadatas"][0][i] if results["metadatas"] else {},
                    distance=results["distances"][0][i]
                    if results.get("distances")
                    else None,
                )
            )
    logger.info(
        "ChromaDB search: kb_id={} query_len={} results={} model={}",
        kb_id,
        len(query),
        len(formatted),
        EMBEDDING_MODEL,
    )
    logger.debug(
        "[CHROMA] method=similarity_search | kb_id={} | rows={} | data={}",
        kb_id,
        len(formatted),
        str(formatted)[:LOG_MAX_BODY] if formatted else "[]",
    )
    return formatted


def similarity_search_all(
    collections_dict, embed_fn, query, k=TOP_K_RETRIEVAL
) -> list[ChunkResult]:
    """在所有 collection 中进行语义搜索，合并后排序取 top-k。"""
    all_results: list[ChunkResult] = []
    for kb_id in collections_dict:
        try:
            col = collections_dict[kb_id]
            results = similarity_search(col, embed_fn, kb_id, query, k=k)
            all_results.extend(results)
        except Exception as e:
            logger.warning("搜索 collection '{}' 失败: {}", kb_id, e)
            continue

    all_results.sort(
        key=lambda r: r.distance if r.distance is not None else float("inf")
    )
    result = all_results[:k]
    logger.info(
        "ChromaDB search_all: collections={} query_len={} results={}",
        len(collections_dict),
        len(query),
        len(result),
    )
    return result


def get_chunks_by_doc_id(collection, doc_id: str) -> list[ChunkResult]:
    """查询指定文档的所有分块。"""
    try:
        results = collection.get(where={"doc_id": doc_id})
        if not results["ids"]:
            return []
        chunks = []
        for i in range(len(results["ids"])):
            chunks.append(
                ChunkResult(
                    id=results["ids"][i],
                    content=results["documents"][i] if results["documents"] else "",
                    metadata=results["metadatas"][i] if results["metadatas"] else {},
                )
            )
        logger.info(
            "[CHROMA] method=get_chunks_by_doc_id | doc_id={} | rows={}",
            doc_id,
            len(chunks),
        )
        return chunks
    except Exception as e:
        logger.warning("Failed to get chunks for doc_id={}: {}", doc_id, e)
        return []


def get_chunks_paginated(
    collection, doc_id: str, page: int = 1, page_size: int = 50
) -> ChunkQueryResult:
    """分页查询指定文档的分块。"""
    try:
        all_ids = collection.get(where={"doc_id": doc_id}, include=[])
        total = len(all_ids["ids"]) if all_ids.get("ids") else 0
        if total == 0:
            return ChunkQueryResult(items=[], total=0, page=page, page_size=page_size)

        offset = (page - 1) * page_size
        results = collection.get(
            where={"doc_id": doc_id},
            limit=page_size,
            offset=offset,
            include=["documents", "metadatas"],
        )
        items = []
        for i in range(len(results["ids"])):
            items.append(
                ChunkResult(
                    id=results["ids"][i],
                    content=results["documents"][i] if results["documents"] else "",
                    metadata=results["metadatas"][i] if results["metadatas"] else {},
                )
            )
        logger.info(
            "[CHROMA] method=get_chunks_paginated | doc_id={} | page={} | total={}",
            doc_id,
            page,
            total,
        )
        return ChunkQueryResult(
            items=items, total=total, page=page, page_size=page_size
        )
    except Exception as e:
        logger.warning("Failed to get paginated chunks for doc_id={}: {}", doc_id, e)
        return ChunkQueryResult(items=[], total=0, page=page, page_size=page_size)
