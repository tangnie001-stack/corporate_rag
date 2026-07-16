"""分块校验器模块的单元测试。"""

from src.infra.chunking.validator import (
    validate_chunks,
    ChunkData,
    count_tokens,
    detect_garbled,
)


def test_count_tokens_returns_positive_int():
    assert count_tokens("") == 1
    assert count_tokens("hello") == 2  # 5 chars // 2
    assert count_tokens("A" * 100) == 50


def test_detect_garbled_normal_text():
    assert detect_garbled("正常文本") == 0.0
    assert detect_garbled("") == 0.0


def test_detect_garbled_high_ratio():
    garbled = "\ufffd\ufffd\ufffd" * 50
    assert detect_garbled(garbled) > 0.5


def test_normal_chunks_pass():
    chunks = [
        ChunkData(content="正常财务数据" * 100, metadata={"source": "a.pdf", "page": 1})
    ]
    report = validate_chunks(chunks)
    assert report.total == 1
    assert len(report.tiny_chunks) == 0
    assert len(report.garbled_chunks) == 0


def test_tiny_chunk_detected():
    chunks = [ChunkData(content="短", metadata={"source": "a.pdf", "page": 1})]
    report = validate_chunks(chunks)
    assert len(report.tiny_chunks) == 1


def test_garbled_chunk_detected():
    garbled = "\ufffd\ufffd\ufffd" * 50  # Unicode replacement chars
    chunks = [ChunkData(content=garbled, metadata={})]
    report = validate_chunks(chunks)
    assert len(report.garbled_chunks) == 1
