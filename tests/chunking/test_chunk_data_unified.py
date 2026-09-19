"""ChunkData 只有一个定义的守卫测试。"""

from src.chunking.validator import ChunkData
from src.parsers.base import ChunkData as ParserChunkData


def test_single_definition():
    """两个 import 路径必须指向同一个类，不是两份定义。"""
    assert ChunkData is ParserChunkData


def test_parser_side_constructs_with_chunk_id():
    """三个 parser 以关键字传 chunk_id，必须仍能构造。"""
    chunk = ChunkData(
        content="营业收入同比增长",
        metadata={"source": "a.pdf", "page": 1, "block_type": "text"},
        chunk_id="a.pdf:p1:0",
    )
    assert chunk.chunk_id == "a.pdf:p1:0"
    assert chunk.tokens == 0


def test_writer_side_omits_chunk_id_and_tokens():
    """写入侧（chunking/validator 的调用方）从不传 chunk_id / tokens。"""
    chunk = ChunkData(content="文本", metadata={"source": "a.pdf"})
    assert chunk.chunk_id == ""
    assert chunk.tokens == 0


def test_positional_order_is_part_of_the_contract():
    """位置参数顺序是契约，不是实现细节。

    解析侧历史顺序是 (content, metadata, chunk_id)；写入侧历史顺序是
    (content, metadata, tokens)。统一后必须固定为一种并显式声明，
    否则位置参数调用点会静默改变含义（见陷阱 T11）。
    """
    chunk = ChunkData("c", {"k": "v"}, "cid", 7)
    assert chunk.content == "c"
    assert chunk.metadata == {"k": "v"}
    assert chunk.chunk_id == "cid"
    assert chunk.tokens == 7
