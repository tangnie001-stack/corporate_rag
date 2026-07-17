"""向量存储模块 — 封装 ChromaDB 的增删查操作。

本模块封装了 ChromaDB 的 HttpClient（连接独立 chroma 服务器），
为每个知识库（knowledge_base）创建一个独立的 collection（命名规则：kb_<uuid>），
支持：
  - 批量写入分块文本（含 embedding 自动生成）
  - 语义相似度检索
  - 按知识库或文档粒度删除
  - collection 的内存缓存，避免重复初始化

在 RAG 流水线中的位置：
  文档解析 → 分块 → **VectorStore.add_chunks()** → 入库
  用户提问 → **VectorStore.similarity_search()** → 送 Reranker → 送 LLM
"""

from typing import Optional

import chromadb
from chromadb.config import Settings
from chromadb.errors import NotFoundError
from chromadb.api.types import Documents, Embeddings, EmbeddingFunction
from src.models import FixedDimDashScopeEmbeddings
from loguru import logger

from src.core.logging import LOG_MAX_BODY

from src.config import (
    CHROMA_COLLECTION_PREFIX,
    CHROMA_PERSIST_DIR,
    DASHSCOPE_API_KEY,
    EMBEDDING_MODEL,
    TOP_K_RETRIEVAL,
)
from src.parsers.base import ChunkData


class DashScopeEmbeddingFunction(EmbeddingFunction):
    """DashScope 云端 Embedding 适配器，符合 ChromaDB 0.5+ 接口规范。

    Attributes:
        _embedding: DashScopeEmbeddings 实例
    """

    def __init__(self, model: str, api_key: str):
        """初始化 DashScope 云端 Embedding 函数。

        Args:
            model: Embedding 模型名称（如 text-embedding-v3）
            api_key: DashScope API Key
        """
        self._embedding = FixedDimDashScopeEmbeddings(
            model=model, dashscope_api_key=api_key
        )

    def __call__(self, input: Documents) -> Embeddings:
        """将文档列表转为向量嵌入（入库用，text_type=document）。

        Args:
            input: 待编码的文档文本列表

        Returns:
            向量嵌入列表，每个文档对应一个向量
        """
        return self._embedding.embed_documents(list(input))

    def embed_query(self, text: str) -> list[float]:
        """将单条查询文本转为向量（检索用，text_type=query）。

        Args:
            text: 用户查询文本

        Returns:
            查询向量
        """
        return self._embedding.embed_query(text)


class VectorStore:
    """ChromaDB 向量存储封装 — 每个知识库对应一个独立 collection。

    采用延迟初始化策略：ChromaDB client 在首次调用时才创建，
    并通过 _collection_cache 缓存已打开的 collection，减少重复开销。

    使用 PersistentClient（内嵌模式），向量数据持久化到本地磁盘，
    无需依赖独立的 ChromaDB 服务器容器。

    使用 DashScope 云端 Embedding API（text-embedding-v3），无需下载本地模型。
    PersistentClient 自身是线程安全的，_collection_cache 是 CPython GIL
    保护的 dict 操作，不存在竞态。
    """

    def __init__(self, persist_dir: Optional[str] = None):
        """初始化 VectorStore。

        Args:
            persist_dir: ChromaDB 数据持久化目录，默认使用 config 中的 CHROMA_PERSIST_DIR
        """
        self._persist_dir = persist_dir or CHROMA_PERSIST_DIR
        self._client: Optional[chromadb.ClientAPI] = None
        self._collection_cache: dict[str, chromadb.Collection] = {}
        # ChromaDB 要求的 EmbeddingFunction 适配器
        self._embed_fn = DashScopeEmbeddingFunction(
            model=EMBEDDING_MODEL,
            api_key=DASHSCOPE_API_KEY,
        )

    def _get_client(self) -> chromadb.ClientAPI:
        """获取或创建 ChromaDB PersistentClient（单例模式）。

        首次调用时创建 PersistentClient，后续调用直接返回缓存实例。
        关闭 anonymized_telemetry 避免发送匿名使用数据。

        Returns:
            ChromaDB ClientAPI 实例
        """
        if self._client is None:
            self._client = chromadb.PersistentClient(
                path=self._persist_dir,
                settings=Settings(anonymized_telemetry=False),
            )
            logger.info(
                "ChromaDB PersistentClient created: persist_dir={} model={}",
                self._persist_dir,
                EMBEDDING_MODEL,
            )
        return self._client

    def _collection_name(self, kb_id: str) -> str:
        """根据知识库 ID 生成 collection 名称。

        将 UUID 中的短横线去掉，拼接前缀，例如：
        kb_id = "a1b2c3d4-..." → "kb_a1b2c3d4..."

        Args:
            kb_id: 知识库的唯一标识（UUID 格式）

        Returns:
            collection 名称字符串
        """
        clean_id = kb_id.replace("-", "")
        return f"{CHROMA_COLLECTION_PREFIX}{clean_id}"

    def get_or_create_collection(self, kb_id: str) -> chromadb.Collection:
        """获取已有 collection 或创建新的 collection。

        优先从内存缓存中查找，命中则直接返回；
        未命中则通过 ChromaDB 的 get_or_create_collection API 获取或创建新的 collection。

        使用 get_or_create_collection（而非 try/except get_collection → create_collection）
        确保 embedding_function 在获取已有 collection 时也被正确设置。
        ChromaDB 的 get_collection 不会返回创建时设置的 embedding_function，
        导致 query 时使用默认 embedding（384维），与已存储的 1024 维向量不匹配。

        使用 DashScope 云端 embedding 函数，无需下载本地模型。

        HNSW 索引参数说明：
          - space: cosine（余弦相似度，适合文本检索）
          - M: 8（每个节点的最大连接数，越大越精准但越慢）
          - construction_ef: 64（构建索引时的搜索宽度）

        Args:
            kb_id: 知识库的唯一标识

        Returns:
            chromadb.Collection 实例
        """
        cache_key = kb_id
        if cache_key in self._collection_cache:
            return self._collection_cache[cache_key]

        name = self._collection_name(kb_id)
        client = self._get_client()
        collection = client.get_or_create_collection(
            name=name,
            embedding_function=self._embed_fn,
            metadata={
                "hnsw:space": "cosine",
                "hnsw:M": 8,
                "hnsw:construction_ef": 64,
            },
        )
        logger.debug("Got or created collection '{}' for kb_id={}", name, kb_id)

        self._collection_cache[cache_key] = collection
        return collection

    def add_chunks(self, kb_id: str, chunks: list[ChunkData], doc_id: str) -> int:
        """将分块文本批量写入知识库的 collection。

        ChromaDB 会自动调用 embedding 函数将文本转为向量（需配置 collection 时指定）。
        每个 chunk 的 ID 格式为 "{doc_id}:{chunk_index}"，便于后续按文档删除。

        metadata 包含：
          - source: 原始文件名
          - page: 页码（TXT 固定为 1，PDF 为实际页码）
          - chunk_index: 当前分块在文档中的序号
          - chunk_total: 文档总分块数
          - doc_id: 所属文档的 ID（用于按文档删除）

        Args:
            kb_id: 知识库 ID
            chunks: 解析后的分块数据列表
            doc_id: 文档 ID（UUID 格式）

        Returns:
            实际写入的分块数量
        """
        if not chunks:
            return 0

        collection = self.get_or_create_collection(kb_id)
        ids = []
        documents = []
        metadatas = []

        for i, chunk in enumerate(chunks):
            # ID 格式：doc_id:chunk_index，确保同一文档内的 chunk ID 唯一
            chunk_id = f"{doc_id}:{i}"
            ids.append(chunk_id)
            documents.append(chunk.content)
            # 保留 chunker 传入的所有元数据字段（如 chunk_strategy, heading_path,
            # parent_content, tokens, entities），只覆盖路由字段。
            meta = dict(chunk.metadata)
            meta.update(
                {"chunk_index": i, "chunk_total": len(chunks), "doc_id": doc_id}
            )
            meta.setdefault("source", "")
            meta.setdefault("page", 0)
            metadatas.append(meta)

        # 批量写入：ChromaDB 自动计算 embedding 并持久化
        try:
            collection.add(
                ids=ids,
                documents=documents,
                metadatas=metadatas,
            )
        except Exception as e:
            logger.exception(
                "ChromaDB add_chunks failed: kb_id={} doc_id={} chunks={} "
                "model={} first_content_preview={} error={}",
                kb_id,
                doc_id,
                len(chunks),
                EMBEDDING_MODEL,
                chunks[0].content[:100] if chunks else "",
                e,
            )
            raise
        logger.info(
            "ChromaDB add_chunks success: kb_id={} doc_id={} count={} model={}",
            kb_id,
            doc_id,
            len(chunks),
            EMBEDDING_MODEL,
        )
        return len(ids)

    def similarity_search(self, kb_id: str, query: str, k: int = 5) -> list[dict]:
        """对知识库进行语义相似度检索。

        将用户查询文本转为向量，在 collection 中搜索最相似的 k 个分块。
        返回的结果包含原文、metadata 和距离分数。

        Args:
            kb_id: 知识库 ID
            query: 用户查询文本
            k: 返回结果数量上限（最大 100）

        Returns:
            列表，每个元素为 dict：
            {
                "id": chunk ID,
                "content": 原文内容,
                "metadata": {source, page, doc_id, ...},
                "distance": 余弦距离（越小越相似）
            }
        """
        collection = self.get_or_create_collection(kb_id)
        # 预先算 query embedding（text_type=query），传向量而非文本给 ChromaDB
        query_vec = self._embed_fn.embed_query(query)
        results = collection.query(
            query_embeddings=[query_vec],
            n_results=min(k, 100),
        )

        # 将 ChromaDB 返回的嵌套列表格式展平为 dict 列表
        formatted = []
        if results["ids"] and results["ids"][0]:
            for i in range(len(results["ids"][0])):
                formatted.append(
                    {
                        "id": results["ids"][0][i],
                        "content": results["documents"][0][i]
                        if results["documents"]
                        else "",
                        "metadata": results["metadatas"][0][i]
                        if results["metadatas"]
                        else {},
                        "distance": results["distances"][0][i]
                        if results.get("distances")
                        else None,
                    }
                )
        logger.info(
            "ChromaDB search: kb_id={} query_len={} results={} model={}",
            kb_id,
            len(query),
            len(formatted),
            EMBEDDING_MODEL,
        )
        try:
            data_str = str(formatted)
            if len(data_str) > LOG_MAX_BODY:
                data_str = (
                    data_str[:LOG_MAX_BODY]
                    + f"... (truncated, total={len(data_str)} chars)"
                )
            logger.info(
                "[CHROMA] method=similarity_search | kb_id={} | query_len={} | rows={} | data={}",
                kb_id,
                len(query),
                len(formatted),
                data_str,
            )
        except Exception:
            logger.info(
                "[CHROMA] method=similarity_search | kb_id={} | query_len={} | rows={} | data=<serialization_error>",
                kb_id,
                len(query),
                len(formatted),
            )
        return formatted

    def delete_collection(self, kb_id: str) -> bool:
        """删除整个知识库的 collection（包括所有向量数据）。

        删除知识库时调用，会级联清除该知识库下的所有向量数据。

        Args:
            kb_id: 知识库 ID

        Returns:
            True 表示删除成功，False 表示 collection 不存在
        """
        name = self._collection_name(kb_id)
        client = self._get_client()
        try:
            client.delete_collection(name)
            # 同时从内存缓存中移除
            self._collection_cache.pop(kb_id, None)
            logger.info("Deleted collection '{}'", name)
            return True
        except (NotFoundError, ValueError):
            logger.warning("Collection '{}' not found for deletion", name)
            return False

    def get_chunks_by_doc_id(self, doc_id: str, kb_id: str) -> list[dict]:
        """查询指定文档的所有分块数据。

        通过 ChromaDB 的 where 条件按 doc_id 过滤，
        返回该文档下所有分块的 id、content 和 metadata。
        由分块预览端点调用以展示文档的分块内容。

        Args:
            doc_id: 文档 UUID
            kb_id: 知识库 UUID（限定搜索范围）

        Returns:
            分块字典列表，每项含 id、content、metadata 三个键；
            文档不存在或无分块时返回空列表
        """
        try:
            collection = self.get_or_create_collection(kb_id)
            results = collection.get(where={"doc_id": doc_id})
            if not results["ids"]:
                logger.info(
                    "[CHROMA] method=get_chunks_by_doc_id | doc_id={} | rows=0 | data=[]",
                    doc_id,
                )
                return []
            chunks = []
            for i in range(len(results["ids"])):
                chunks.append(
                    {
                        "id": results["ids"][i],
                        "content": results["documents"][i]
                        if results["documents"]
                        else "",
                        "metadata": results["metadatas"][i]
                        if results["metadatas"]
                        else {},
                    }
                )
            try:
                data_str = str(chunks)
                if len(data_str) > LOG_MAX_BODY:
                    data_str = (
                        data_str[:LOG_MAX_BODY]
                        + f"... (truncated, total={len(data_str)} chars)"
                    )
                logger.info(
                    "[CHROMA] method=get_chunks_by_doc_id | doc_id={} | rows={} | data={}",
                    doc_id,
                    len(chunks),
                    data_str,
                )
            except Exception:
                logger.info(
                    "[CHROMA] method=get_chunks_by_doc_id | doc_id={} | rows={} | data=<serialization_error>",
                    doc_id,
                    len(chunks),
                )
            return chunks
        except Exception as e:
            logger.warning("Failed to get chunks for doc_id={}: {}", doc_id, e)
            return []

    def get_chunks_paginated(
        self,
        doc_id: str,
        kb_id: str,
        page: int = 1,
        page_size: int = 50,
    ) -> dict:
        """分页查询指定文档的分块数据。

        先通过 ID-only 查询获取总量，再用 limit/offset 返回当前页数据。
        避免每次翻页都加载全量嵌入向量。

        Args:
            doc_id: 文档 UUID
            kb_id: 知识库 UUID（限定搜索范围）
            page: 页码，从 1 开始
            page_size: 每页条数

        Returns:
            dict: 含 items（当前页分块列表）、total（总量）、
            page（当前页码）、page_size（每页条数）
        """
        try:
            collection = self.get_or_create_collection(kb_id)

            # 先获取总量（只返回 IDs，不加载 documents/embeddings）
            all_ids = collection.get(where={"doc_id": doc_id}, include=[])
            total = len(all_ids["ids"]) if all_ids.get("ids") else 0
            if total == 0:
                logger.info(
                    "[CHROMA] method=get_chunks_paginated | doc_id={} | rows=0 | data=[]",
                    doc_id,
                )
                return {"items": [], "total": 0, "page": page, "page_size": page_size}

            # 获取当前页
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
                    {
                        "id": results["ids"][i],
                        "content": results["documents"][i]
                        if results["documents"]
                        else "",
                        "metadata": results["metadatas"][i]
                        if results["metadatas"]
                        else {},
                    }
                )
            result = {
                "items": items,
                "total": total,
                "page": page,
                "page_size": page_size,
            }
            try:
                data_str = str(items)
                if len(data_str) > LOG_MAX_BODY:
                    data_str = (
                        data_str[:LOG_MAX_BODY]
                        + f"... (truncated, total={len(data_str)} chars)"
                    )
                logger.info(
                    "[CHROMA] method=get_chunks_paginated | doc_id={} | page={} | total={} | data={}",
                    doc_id,
                    page,
                    total,
                    data_str,
                )
            except Exception:
                logger.info(
                    "[CHROMA] method=get_chunks_paginated | doc_id={} | page={} | data=<serialization_error>",
                    doc_id,
                    page,
                )
            return result
        except Exception as e:
            logger.warning(
                "Failed to get paginated chunks for doc_id={}: {}", doc_id, e
            )
            return {"items": [], "total": 0, "page": page, "page_size": page_size}

    def delete_document(self, kb_id: str, doc_id: str) -> int:
        """删除指定文档的所有分块向量数据。

        通过 metadata 中的 doc_id 过滤，精确删除属于某个文档的所有 chunk，
        不影响同一知识库下其他文档的数据。

        Args:
            kb_id: 知识库 ID
            doc_id: 要删除的文档 ID

        Returns:
            实际删除的分块数量
        """
        try:
            collection = self.get_or_create_collection(kb_id)
            # 通过 where 条件过滤出属于该文档的所有 chunk
            results = collection.get(where={"doc_id": doc_id})
            if results["ids"]:
                collection.delete(ids=results["ids"])
                count = len(results["ids"])
                logger.info(
                    "ChromaDB delete_document: kb_id={} doc_id={} deleted={}",
                    kb_id,
                    doc_id,
                    count,
                )
                return count
            return 0
        except NotFoundError:
            return 0

    def list_collections(self) -> list[str]:
        """列出所有知识库 collection 的名称。

        只返回以 CHROMA_COLLECTION_PREFIX 开头的 collection，
        过滤掉其他非业务 collection（如 ChromaDB 内部使用的）。

        Returns:
            collection 名称列表
        """
        client = self._get_client()
        names = client.list_collections()
        return [n.name for n in names if n.name.startswith(CHROMA_COLLECTION_PREFIX)]

    def similarity_search_all(self, query: str, k: int = TOP_K_RETRIEVAL) -> list[dict]:
        """在所有知识库中进行语义搜索，合并结果后按距离排序取 top-k。

        遍历所有以 kb_ 开头的 collection，对每个 collection 执行
        similarity_search，合并结果按 distance 升序排列后取前 k 个。

        适用于"不限定知识库"的全局搜索场景。

        Args:
            query: 用户查询文本
            k: 最终返回结果数量上限

        Returns:
            同 similarity_search() 的返回格式，按 distance 升序排列
        """
        names = self.list_collections()
        if not names:
            return []

        all_results: list[dict] = []
        for name in names:
            # 去掉前缀获取原始 kb_id，用于调用 similarity_search
            kb_id = name.removeprefix(CHROMA_COLLECTION_PREFIX)
            try:
                results = self.similarity_search(kb_id, query, k=k)
                all_results.extend(results)
            except Exception as e:
                logger.warning("搜索 collection '{}' 失败: {}", name, e)
                continue

        # 按 distance 升序排列（数值越小表示越相似）
        all_results.sort(key=lambda r: r.get("distance", float("inf")))
        result = all_results[:k]
        try:
            data_str = str(result)
            if len(data_str) > LOG_MAX_BODY:
                data_str = (
                    data_str[:LOG_MAX_BODY]
                    + f"... (truncated, total={len(data_str)} chars)"
                )
            logger.info(
                "[CHROMA] method=similarity_search_all | rows={} | data={}",
                len(result),
                data_str,
            )
        except Exception:
            logger.info(
                "[CHROMA] method=similarity_search_all | rows={} | data=<serialization_error>",
                len(result),
            )
        return result
