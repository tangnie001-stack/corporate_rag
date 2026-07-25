"""ChromaDB 连接管理和 collection 缓存。"""

from typing import Optional
import chromadb
from chromadb.config import Settings
from loguru import logger
from src.config import CHROMA_COLLECTION_PREFIX, CHROMA_PERSIST_DIR
from src.infra.db.vector_store.embedding import DashScopeEmbeddingFunction
from src.config import EMBEDDING_MODEL


class ChromaClient:
    """ChromaDB 连接管理 + collection 缓存（单例模式）。"""

    def __init__(self, persist_dir: Optional[str] = None):
        self._persist_dir = persist_dir or CHROMA_PERSIST_DIR
        self._client: Optional[chromadb.ClientAPI] = None
        self._collection_cache: dict[str, chromadb.Collection] = {}
        self._embed_fn = DashScopeEmbeddingFunction()

    def _get_client(self) -> chromadb.ClientAPI:
        if self._client is None:
            self._client = chromadb.PersistentClient(
                path=self._persist_dir,
                settings=Settings(anonymized_telemetry=False),
            )
            logger.info(
                "ChromaDB PersistentClient created: persist_dir={} model={}",
                self._persist_dir, EMBEDDING_MODEL,
            )
        return self._client

    @staticmethod
    def _collection_name(kb_id: str) -> str:
        clean_id = kb_id.replace("-", "")
        return f"{CHROMA_COLLECTION_PREFIX}{clean_id}"

    def get_or_create_collection(self, kb_id: str) -> chromadb.Collection:
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

    def delete_collection_cache(self, kb_id: str) -> None:
        self._collection_cache.pop(kb_id, None)

    def list_collection_names(self) -> list[str]:
        client = self._get_client()
        names = client.list_collections()
        return [n.name for n in names if n.name.startswith(CHROMA_COLLECTION_PREFIX)]
