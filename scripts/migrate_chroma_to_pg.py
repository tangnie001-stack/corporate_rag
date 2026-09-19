"""一次性搬迁：ChromaDB 的分块（含 embedding）→ PostgreSQL 的 chunks 表。

用途：让 dense 迁移等价性成为**可判定的差分** —— 语料与查询都不变，只换存储。
若走「重新入库」，分块与 embedding 都会重算，等价性就失去依据。

幂等：KB 行 ON CONFLICT DO NOTHING，chunks 行按 (kb_id, doc_id, chunk_index) 覆盖写，
因此可以反复重跑（例如等价性不达标、修完再搬）。

kb_id 的还原限制：Chroma 的 collection 名是 `kb_<hex>`，`<hex>` 是 kb_id 去掉连字符
后的 32 位串（`ChromaClient._collection_name` 做 `kb_id.replace("-", "")`），该变换
**不可逆** —— 原始 UUID 的连字符位置无法还原。故 `kb_id` 取「collection 名去掉前缀的
32 位串」，并由本脚本补建对应的 knowledge_base 行使外键自洽（Ruling 3）。等价性验收
只要求「同一批分块在两侧可比」，不依赖 kb_id 与原 UUID 相同。

用法：
    POSTGRES_HOST=localhost .venv/bin/python scripts/migrate_chroma_to_pg.py
    POSTGRES_HOST=localhost .venv/bin/python scripts/migrate_chroma_to_pg.py --dry-run
"""

import argparse
import asyncio
import os
import sys
from dataclasses import dataclass

import chromadb
from loguru import logger
from sqlalchemy import text

# 直接以 `python scripts/migrate_chroma_to_pg.py` 运行时 sys.path[0] 是 scripts/，
# 仓库根不在其中（editable 安装只把 src/ 内容暴露为顶层包）；补上仓库根以便
# import src.*。`python -m scripts.migrate_chroma_to_pg` 下仓库根已在 path 中，
# 重复插入无副作用。
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config.settings import (
    CHROMA_COLLECTION_PREFIX,
    CHROMA_PERSIST_DIR,
    EMBEDDING_DIMENSION,
)
from src.infra.db.engine import session_factory
from src.infra.db.mysql_db.chunk_repo import ChunkRepo
from src.infra.db.vector_store.mapping import ChunkRow, split_metadata

# 本次验收语料的期望口径 —— 任一不符即中止（Task 8 等价性判据的前提）
EXPECTED_COLLECTIONS = 691
"""Chroma 里的全部 collection 数（含空 collection）。"""
EXPECTED_NON_EMPTY_COLLECTIONS = 5
"""含分块的 collection 数。"""
EXPECTED_CHUNKS = 176
"""待搬迁的分块总数。"""


@dataclass
class ChromaRecord:
    """Chroma 侧的一条分块记录（搬迁的输入形状）。"""

    id: str
    """Chroma 的 id，格式 {doc_id}:{chunk_index}。"""
    document: str
    """分块正文。"""
    metadata: dict
    """Chroma metadata（含 5 个契约键 + chunker 自定义键）。"""
    embedding: list[float] | None
    """EMBEDDING_DIMENSION 维向量；None 表示 Chroma 侧缺向量。"""


@dataclass
class Corpus:
    """直读 Chroma 得到的搬迁语料及其自证计数。"""

    total_collections: int
    """Chroma 里的全部 collection 数（含空 collection）。"""
    non_empty_collections: int
    """含分块的 collection 数。"""
    entries: list[tuple[str, ChromaRecord]]
    """(collection 名, 记录) 列表，按 collection 名升序。"""
    dimension_mismatch: int
    """维度不等于 EMBEDDING_DIMENSION 的记录数。"""
    none_embedding: int
    """embedding 为 None 的记录数。"""


def collection_name_to_kb_id(name: str) -> str:
    """从 collection 名还原 kb_id（去掉前缀的 32 位十六进制串）。

    Note:
        collection 名由 kb_id 去掉连字符后加前缀得到，该变换不可逆 ——
        因此这里得到的是「搬迁自造的 kb_id」，并由本脚本创建对应的
        knowledge_base 行使其外键自洽（见模块 docstring）。
    """
    if name.startswith(CHROMA_COLLECTION_PREFIX):
        return name[len(CHROMA_COLLECTION_PREFIX) :]
    return name


def _chunk_index_from_id(chunk_id: str) -> int:
    """从 id 的 {doc_id}:{i} 后缀还原 chunk_index；无法解析时返回 0。"""
    _, _, suffix = chunk_id.rpartition(":")
    if suffix.isdigit():
        return int(suffix)
    return 0


def chroma_record_to_row(record: ChromaRecord) -> ChunkRow:
    """把一条 Chroma 记录转成 chunks 行（契约键升列、自定义键进 jsonb）。

    Args:
        record: Chroma 记录

    Returns:
        ChunkRow；kb_id 由调用方在写出前补齐（此处留空串占位）
    """
    split = split_metadata(record.metadata)
    chunk_index = record.metadata.get("chunk_index")
    if not isinstance(chunk_index, int) or isinstance(chunk_index, bool):
        chunk_index = _chunk_index_from_id(record.id)
    chunk_total = record.metadata.get("chunk_total")
    if not isinstance(chunk_total, int) or isinstance(chunk_total, bool):
        chunk_total = 0
    doc_id = record.metadata.get("doc_id")
    if not isinstance(doc_id, str) or not doc_id:
        doc_id = record.id.rpartition(":")[0]
    embedding = None
    if record.embedding is not None:
        embedding = list(record.embedding)
    return ChunkRow(
        id=record.id,
        kb_id="",  # 由 migrate() 回填
        doc_id=doc_id,
        chunk_index=chunk_index,
        chunk_total=chunk_total,
        content=record.document,
        content_seg=record.document,  # P2 占位：原文（P3 换分词输出并全量重写）
        embedding=embedding,
        source=split.source,
        page=split.page,
        extra=split.extra,
    )


def _read_corpus() -> Corpus:
    """直读 Chroma 的全部 collection，组装搬迁语料。

    Returns:
        Corpus（含空/非空 collection 计数、维度不符数与缺向量数）

    Note:
        Chroma 的 embeddings 是 numpy 数组，**不得**对它取真值（会抛
        `ValueError: truth value of an array ... is ambiguous`）——
        一律用 `is None` / `len(...)` 判断。
    """
    client = chromadb.PersistentClient(path=CHROMA_PERSIST_DIR)
    names = sorted(c.name for c in client.list_collections())
    entries: list[tuple[str, ChromaRecord]] = []
    non_empty = 0
    dimension_mismatch = 0
    none_embedding = 0
    for name in names:
        collection = client.get_collection(name)
        if collection.count() == 0:
            continue
        non_empty += 1
        got = collection.get(include=["documents", "metadatas", "embeddings"])
        ids = got["ids"]
        documents = got["documents"]
        metadatas = got["metadatas"]
        embeddings = got["embeddings"]
        for i, chunk_id in enumerate(ids):
            document = ""
            if documents is not None and documents[i] is not None:
                document = documents[i]
            metadata: dict = {}
            if metadatas is not None and metadatas[i] is not None:
                metadata = dict(metadatas[i])
            embedding = None
            if embeddings is None or embeddings[i] is None:
                none_embedding += 1
            else:
                embedding = [float(v) for v in embeddings[i]]
                if len(embedding) != EMBEDDING_DIMENSION:
                    dimension_mismatch += 1
            entries.append(
                (
                    name,
                    ChromaRecord(
                        id=chunk_id,
                        document=document,
                        metadata=metadata,
                        embedding=embedding,
                    ),
                )
            )
    return Corpus(
        total_collections=len(names),
        non_empty_collections=non_empty,
        entries=entries,
        dimension_mismatch=dimension_mismatch,
        none_embedding=none_embedding,
    )


def _assert_corpus(corpus: Corpus) -> None:
    """核对语料口径；任一不符即抛错，避免带着错的语料往下走。

    Args:
        corpus: 直读 Chroma 得到的语料

    Raises:
        RuntimeError: 计数与期望口径不符时
    """
    problems: list[str] = []
    if corpus.total_collections != EXPECTED_COLLECTIONS:
        problems.append(
            f"collection 总数 {corpus.total_collections} != {EXPECTED_COLLECTIONS}"
        )
    if corpus.non_empty_collections != EXPECTED_NON_EMPTY_COLLECTIONS:
        problems.append(
            f"非空 collection 数 {corpus.non_empty_collections}"
            f" != {EXPECTED_NON_EMPTY_COLLECTIONS}"
        )
    if len(corpus.entries) != EXPECTED_CHUNKS:
        problems.append(f"分块总数 {len(corpus.entries)} != {EXPECTED_CHUNKS}")
    if corpus.dimension_mismatch != 0:
        problems.append(
            f"维度不等于 {EMBEDDING_DIMENSION} 的分块数 {corpus.dimension_mismatch} != 0"
        )
    if corpus.none_embedding != 0:
        problems.append(f"缺 embedding 的分块数 {corpus.none_embedding} != 0")
    if problems:
        raise RuntimeError("语料自证失败，中止搬迁: " + "; ".join(problems))
    logger.info(
        "corpus ok: collections={} non_empty={} chunks={} dim={} none_embedding=0",
        corpus.total_collections,
        corpus.non_empty_collections,
        len(corpus.entries),
        EMBEDDING_DIMENSION,
    )


async def ensure_kb_row(kb_id: str) -> None:
    """为该 kb_id 补建 knowledge_base 行（幂等）。

    仅用于搬迁：这些 KB 是**验收用的合成归属**（见 Ruling 3）。
    knowledge_base.user_id 无外键，故用一个标注性占位账号；description /
    doc_count / is_deleted 只有 Python 侧默认值、没有 server_default，
    裸 SQL 必须显式给值。
    """
    async with session_factory() as s:
        await s.execute(
            text(
                "INSERT INTO knowledge_base (id, user_id, name, description, doc_count, is_deleted)"
                " VALUES (:k, 'p2-migration', :n, :d, 0, 0)"
                " ON CONFLICT (id) DO NOTHING"
            ),
            {
                "k": kb_id,
                "n": f"p2-equiv-{kb_id[:8]}",
                "d": "P2 dense 等价性验收用的合成知识库",
            },
        )
        await s.commit()


async def migrate(dry_run: bool = False) -> dict:
    """执行搬迁。

    Args:
        dry_run: 为真时只统计与校验，不写库

    Returns:
        统计字典：collections / total_collections / records / kb_ids /
        written / dimension_mismatch / none_embedding / dry_run
    """
    repo = ChunkRepo(session_factory)
    corpus = _read_corpus()
    _assert_corpus(corpus)
    by_kb: dict[str, list[ChunkRow]] = {}
    for name, record in corpus.entries:
        kb_id = collection_name_to_kb_id(name)
        row = chroma_record_to_row(record)
        row.kb_id = kb_id
        by_kb.setdefault(kb_id, []).append(row)

    written = 0
    if not dry_run:
        for kb_id, rows in by_kb.items():
            await ensure_kb_row(kb_id)
            written += await repo.upsert_chunks(rows)

    stats = {
        "collections": len(by_kb),
        "total_collections": corpus.total_collections,
        "records": len(corpus.entries),
        "kb_ids": sorted(by_kb.keys()),
        "written": written,
        "dimension_mismatch": corpus.dimension_mismatch,
        "none_embedding": corpus.none_embedding,
        "dry_run": dry_run,
    }
    logger.info("migrate chroma->pg done: {}", stats)
    return stats


def main() -> None:
    """命令行入口。"""
    parser = argparse.ArgumentParser(description="Chroma → PostgreSQL chunks 搬迁")
    parser.add_argument("--dry-run", action="store_true", help="只统计不写库")
    args = parser.parse_args()
    asyncio.run(migrate(dry_run=args.dry_run))


if __name__ == "__main__":
    main()
