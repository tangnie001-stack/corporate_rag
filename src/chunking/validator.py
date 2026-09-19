"""分块质量自动化监控模块。

提供分块数据的结构定义和质量校验功能，在文档入库前检测异常分块。
"""

from dataclasses import dataclass, field


@dataclass
class ChunkData:
    """分块数据的唯一标准类型。

    content/metadata 是解析与写入两侧共用的载荷；chunk_id 标识来源位置（解析生成，
    BM25 重建索引时复用 Chroma id 覆盖写入），tokens 供分块质量评估使用（解析阶段不填，默认 0）。
    """

    content: str  # 分块正文文本，解析侧产出、写入侧消费
    metadata: dict  # 来源元数据（source / page / doc_id / block_type 等）
    # 来源标识：解析阶段按 "{source}:{index}" 或 "{source}:p{page}:{index}" 生成，
    # 入库写入侧不填、默认空串；
    # BM25 重建索引时以 Chroma id（"{doc_id}:{index}"）覆盖写入，并读回作词法检索的文档 id
    chunk_id: str = ""
    # 分块后的 token 数，由分块质量评估填充；解析阶段不填，默认 0
    tokens: int = 0


@dataclass
class ValidationWarning:
    """校验告警记录。

    Attributes:
        type: 告警类型（"tiny_chunk" | "garbled"）
        chunk_index: 告警对应的分块索引
        message: 告警描述信息
    """

    type: str  # "tiny_chunk" | "garbled"
    chunk_index: int
    message: str


@dataclass
class ChunkQualityReport:
    """分块质量报告。

    Attributes:
        total: 总分块数量
        avg_tokens: 平均 token 数
        min_tokens: 最小 token 数
        max_tokens: 最大 token 数
        tiny_chunks: 过小分块的索引列表（tokens < 50）
        garbled_chunks: 乱码分块的索引列表（替换字符占比 > 5%）
    """

    total: int
    avg_tokens: float
    min_tokens: int
    max_tokens: int
    tiny_chunks: list[int] = field(default_factory=list)
    garbled_chunks: list[int] = field(default_factory=list)


def count_tokens(text: str) -> int:
    """估算文本 token 数量。

    中文文本约 2 字符/token 的粗略估算，用于分块时的长度判断。
    官方 tokenizer 会得到更精确的结果，此处用除法简化。

    Args:
        text: 输入文本

    Returns:
        估算的 token 数量（至少为 1，避免除零）
    """
    return max(1, len(text) // 2)


def detect_garbled(text: str) -> float:
    """检测 Unicode 替换字符 (U+FFFD) 占比。

    文档解析异常时可能产生 � 字符，占比过高说明文档格式异常。

    Args:
        text: 待检测的文本

    Returns:
        替换字符在总字符中的占比（0.0 ~ 1.0）
    """
    if not text:
        return 0.0
    garbled = sum(1 for c in text if ord(c) == 0xFFFD)
    return garbled / len(text)


def validate_chunks(chunks: list[ChunkData]) -> ChunkQualityReport:
    """校验分块质量，返回质量报告。

    规则：
      - tiny_chunk：token 数 < 50
      - garbled：Unicode 替换字符占比 > 5%

    Args:
        chunks: 待校验的分块列表

    Returns:
        包含校验结果的质量报告
    """
    issues: list[ValidationWarning] = []
    token_counts: list[int] = []

    for i, chunk in enumerate(chunks):
        tokens = count_tokens(chunk.content)
        token_counts.append(tokens)

        if tokens < 50:
            issues.append(
                ValidationWarning(
                    type="tiny_chunk",
                    chunk_index=i,
                    message=f"Only {tokens} tokens, below 50 threshold",
                )
            )

        garbled_ratio = detect_garbled(chunk.content)
        if garbled_ratio > 0.05:
            issues.append(
                ValidationWarning(
                    type="garbled",
                    chunk_index=i,
                    message=f"Garbled ratio {garbled_ratio:.1%} > 5%",
                )
            )

    return ChunkQualityReport(
        total=len(chunks),
        avg_tokens=sum(token_counts) / len(token_counts) if token_counts else 0,
        min_tokens=min(token_counts) if token_counts else 0,
        max_tokens=max(token_counts) if token_counts else 0,
        tiny_chunks=[w.chunk_index for w in issues if w.type == "tiny_chunk"],
        garbled_chunks=[w.chunk_index for w in issues if w.type == "garbled"],
    )
