"""基于 token 计数的 Parent-Child 分块器 — 使用 RecursiveCharacterTextSplitter。

本模块提供 ParentChildChunker 类，将长文档按 token 数递归切分为
"父分块 → 子分块" 的层级结构，子分块在 metadata 中携带父段落上下文，
供检索阶段获取更丰富的上下文信息。

核心导出:
    ParentChildChunker: Parent-Child 分块器
    count_tokens: 启发式 token 计数函数（CJK 文本约 2 字符/token）
"""

from langchain_text_splitters import RecursiveCharacterTextSplitter


def count_tokens(text: str) -> int:
    """估算文本的 token 数量（用于文本分块时的长度函数）。

    Args:
        text: 输入文本

    Returns:
        估算的 token 数量（至少为 1）
    """
    return max(1, len(text) // 2)


class ParentChildChunker:
    """将文本切为子分块，并在 metadata 中携带父段落上下文。

    每个子分块在 metadata 中携带其父段落（更大的上下文段落）全文，
    使检索阶段能获取更丰富的上下文信息。

    Attributes:
        CHILD_SIZE: 子分块目标 token 数（256）
        PARENT_SIZE: 父段落目标 token 数（1024）
        OVERLAP: 分块间重叠 token 数（25）
    """

    CHILD_SIZE = 256
    PARENT_SIZE = 1024
    OVERLAP = 25

    def chunk(self, text: str, metadata: dict) -> list[dict]:
        """将文本切分为 Parent-Child 层次的分块列表。

        先按 PARENT_SIZE 切出父段落，再在每个父段落内按 CHILD_SIZE
        切出子分块，每个子分块的 metadata 中携带父段落的全文。

        Args:
            text: 原始文档文本
            metadata: 文档级元数据（source / page / doc_id 等），会复制到每个分块

        Returns:
            分块字典列表，每项含 "content"（子文本）和 "metadata"（含原始元数据
            外加 parent_content / parent_chunk_id / child_index / parent_index / tokens）
        """
        parent_splitter = RecursiveCharacterTextSplitter(
            chunk_size=self.PARENT_SIZE,
            chunk_overlap=self.OVERLAP,
            length_function=count_tokens,
            separators=["\n\n", "\n", "。", ".", " "],
        )
        parent_docs = parent_splitter.create_documents([text])

        child_splitter = RecursiveCharacterTextSplitter(
            chunk_size=self.CHILD_SIZE,
            chunk_overlap=self.OVERLAP,
            length_function=count_tokens,
            separators=["\n\n", "\n", "。", ".", " "],
        )

        result: list[dict] = []
        for pi, parent in enumerate(parent_docs):
            child_docs = child_splitter.create_documents([parent.page_content])
            for ci, child in enumerate(child_docs):
                result.append(
                    {
                        "content": child.page_content,
                        "metadata": {
                            **metadata,
                            "parent_content": parent.page_content,
                            "parent_chunk_id": f"{metadata.get('doc_id', '')}:parent:{pi}",
                            "child_index": ci,
                            "parent_index": pi,
                            "tokens": count_tokens(child.page_content),
                        },
                    }
                )
        return result
