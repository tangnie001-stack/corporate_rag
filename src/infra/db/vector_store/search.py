"""ChromaDB 查询操作，返回类型化的 ChunkResult/ChunkQueryResult。"""

from loguru import logger
from src.core.logging import LOG_MAX_BODY
from src.config import TOP_K_RETRIEVAL, EMBEDDING_MODEL
from src.infra.db.entities.search import ChunkResult, ChunkQueryResult


def similarity_search(collection, embed_fn, kb_id, query, k=5) -> list[ChunkResult]:
    """语义相似度检索，返回最相似的 k 个分块。

    将查询文本转为嵌入向量后在指定 collection 中进行余弦相似度搜索。

    Args:
        collection: ChromaDB Collection 实例
        embed_fn: 嵌入函数（需实现 embed_query 方法）
        kb_id: 知识库 ID（仅用于日志）
        query: 查询文本
        k: 返回结果数量上限，默认 5（最大 100）

    Returns:
        检索结果列表，按相关性降序排列（距离越小越相关）
    """
    col_count = -1
    try:
        col_count = collection.count()
        logger.info(
            "[DIAG] similarity_search: kb_id={} collection_name={} collection_count={} k={}",
            kb_id,
            collection.name,
            col_count,
            min(k, 100),
        )
    except Exception:
        pass

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
    if not formatted:
        logger.info(
            "[DIAG] ChromaDB search returned 0 results! kb_id={} collection_count={} query_len={}",
            kb_id,
            col_count,
            len(query),
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
    """在所有 collection 中进行语义搜索，合并后排序取 top-k。

    遍历所有知识库逐一执行 similarity_search，汇总后按距离升序排列。

    Args:
        collections_dict: 知识库 ID 到 Collection 的映射字典
        embed_fn: 嵌入函数
        query: 查询文本
        k: 最终返回结果上限，默认使用全局配置 TOP_K_RETRIEVAL

    Returns:
        合并排序后的检索结果列表
    """
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
    """查询指定文档的所有分块。

    通过 doc_id 元数据过滤获取全部关联分块。

    Args:
        collection: ChromaDB Collection 实例
        doc_id: 文档 ID

    Returns:
        分块结果列表（查不到时返回空列表）
    """
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
    """分页查询指定文档的分块。

    先统计总数，再按 offset/limit 获取指定页数据。

    Args:
        collection: ChromaDB Collection 实例
        doc_id: 文档 ID
        page: 页码，从 1 开始，默认 1
        page_size: 每页数量，默认 50

    Returns:
        分页查询结果（items / total / page / page_size），查询失败时返回空结果
    """
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
