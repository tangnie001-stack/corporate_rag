"""chunks 行 ↔ ChunkResult 的映射 —— metadata 回填契约的唯一归属。

读取时 ChunkResult.metadata 由【列值 + jsonb 平铺合并】得到，冲突以列为准，
至少含 doc_id / chunk_index / chunk_total / source / page；jsonb 侧原样承载
chunker 产出的全部自定义键（parent_content / block_type / heading_path / ...）。

写入侧（split_metadata / build_rows）、读取侧（row_to_chunk_result）与
搬迁脚本必须共用本模块：三处各自实现会让契约漂移，而漂移不会报错 ——
它只会让按 doc_id 去重、引用渲染（source/page）与实体透传静默失效。
"""

from dataclasses import dataclass

from src.chunking.validator import ChunkData
from src.infra.db.vector_store.types import ChunkResult

CONTRACT_KEYS: tuple[str, ...] = (
    "doc_id",
    "chunk_index",
    "chunk_total",
    "source",
    "page",
)


@dataclass
class SplitMetadata:
    """把 chunker 的 metadata 拆成「上列的契约键」与「进 jsonb 的自定义键」。"""

    extra: dict
    """进 jsonb 的自定义键（不含任何契约键）。"""
    source: str
    """来源文件名列值；缺失时为空串。"""
    page: int
    """页码列值；缺失时为 0。"""


@dataclass
class ChunkRow:
    """chunks 表的一行 —— 读、写两侧共用的形状。"""

    id: str
    """分块 ID，格式 {doc_id}:{chunk_index}。"""
    kb_id: str
    """所属知识库 ID（外键 → knowledge_base.id）。"""
    doc_id: str
    """所属文档 ID。"""
    chunk_index: int
    """该文档内的分块序号，0 起。"""
    chunk_total: int
    """该文档的分块总数。"""
    content: str
    """分块正文原文。"""
    content_seg: str
    """词法检索文本；P2 写正文原值作占位，P3 换成分词输出并全量重写。"""
    embedding: list[float] | None
    """1024 维向量；None 表示尚未算好（不应入库）。"""
    source: str
    """来源文件名（契约键，升为列）。"""
    page: int
    """页码（契约键，升为列）。"""
    extra: dict
    """chunker 的自定义键（jsonb 列 metadata 的内容）。"""


def split_metadata(metadata: dict) -> SplitMetadata:
    """把 metadata 拆成契约键（上列）与自定义键（进 jsonb）。

    Args:
        metadata: chunker 或搬迁脚本给出的元数据字典

    Returns:
        SplitMetadata(extra=自定义键, source=文件名, page=页码)

    Note:
        契约键一律**从 extra 中剔除**（含 doc_id / chunk_index / chunk_total，
        它们以函数参数为准，不从 metadata 取），避免 jsonb 里出现与列冲突的副本。
    """
    extra = dict(metadata)
    source = extra.pop("source", "")
    page_raw = extra.pop("page", 0)
    for key in CONTRACT_KEYS:
        extra.pop(key, None)
    if not isinstance(source, str):
        source = str(source)
    if isinstance(page_raw, bool) or page_raw is None:
        page = 0
    elif isinstance(page_raw, int):
        page = page_raw
    elif isinstance(page_raw, float):
        page = int(page_raw)
    else:
        page = 0
    return SplitMetadata(extra=extra, source=source, page=page)


def build_rows(
    kb_id: str,
    doc_id: str,
    chunks: list[ChunkData],
    embeddings: list[list[float]],
) -> list[ChunkRow]:
    """把分块与向量组装成待写入的 chunks 行。

    Args:
        kb_id: 所属知识库 ID
        doc_id: 所属文档 ID
        chunks: 分块列表
        embeddings: 与 chunks 一一对应的 1024 维向量

    Returns:
        待写入的 ChunkRow 列表（顺序即 chunk_index 顺序）

    Raises:
        ValueError: chunks 与 embeddings 长度不一致时
    """
    if len(chunks) != len(embeddings):
        raise ValueError(
            f"chunks 与 embeddings 数量不一致: {len(chunks)} != {len(embeddings)}"
        )
    total = len(chunks)
    rows: list[ChunkRow] = []
    for i, chunk in enumerate(chunks):
        split = split_metadata(chunk.metadata)
        rows.append(
            ChunkRow(
                id=f"{doc_id}:{i}",
                kb_id=kb_id,
                doc_id=doc_id,
                chunk_index=i,
                chunk_total=total,
                content=chunk.content,
                # P2 占位：content_seg 写正文原值，P3 换成 jieba 分词输出并全量重写
                content_seg=chunk.content,
                embedding=embeddings[i],
                source=split.source,
                page=split.page,
                extra=split.extra,
            )
        )
    return rows


def row_to_chunk_row(model) -> ChunkRow:
    """把 ChunkModel（或任何具备同名属性的对象）转成 ChunkRow。"""
    return ChunkRow(
        id=model.id,
        kb_id=model.kb_id,
        doc_id=model.doc_id,
        chunk_index=model.chunk_index,
        chunk_total=model.chunk_total,
        content=model.content,
        content_seg=model.content_seg,
        embedding=list(model.embedding) if model.embedding is not None else None,
        source=model.source,
        page=model.page,
        extra=dict(model.extra or {}),
    )


def row_to_chunk_result(
    row: ChunkRow,
    *,
    distance: float | None = None,
    lexical_score: float | None = None,
    dense_rank: int | None = None,
    sparse_rank: int | None = None,
) -> ChunkResult:
    """把一行映射成 ChunkResult，并按契约回填 metadata（列值优先）。

    Args:
        row: chunks 行
        distance: 余弦距离（dense 路才有）
        lexical_score: 词法得分（词法路才有）
        dense_rank: dense 路排名
        sparse_rank: 词法路排名

    Returns:
        ChunkResult，其 metadata 含 5 个契约键 + jsonb 的全部自定义键
    """
    metadata = dict(row.extra)
    # 列值后写 = 冲突以列为准
    metadata["doc_id"] = row.doc_id
    metadata["chunk_index"] = row.chunk_index
    metadata["chunk_total"] = row.chunk_total
    metadata["source"] = row.source
    metadata["page"] = row.page
    return ChunkResult(
        id=row.id,
        content=row.content,
        metadata=metadata,
        distance=distance,
        lexical_score=lexical_score,
        dense_rank=dense_rank,
        sparse_rank=sparse_rank,
    )
