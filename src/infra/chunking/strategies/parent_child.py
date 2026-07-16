from langchain_text_splitters import RecursiveCharacterTextSplitter
from src.infra.chunking.strategies.base import BaseChunker


class ParentChildChunker(BaseChunker):
    chunk_strategy = "parent_child"
    CHILD_SIZE = 256
    PARENT_SIZE = 1024
    OVERLAP = 25
    SEPARATORS = ["\n\n", "\n", "。", ".", " "]

    def chunk(self, text: str, metadata: dict) -> list[dict]:
        parent_splitter = RecursiveCharacterTextSplitter(
            chunk_size=self.PARENT_SIZE,
            chunk_overlap=self.OVERLAP,
            length_function=self.count_tokens,
            separators=self.SEPARATORS,
        )
        parent_docs = parent_splitter.create_documents([text])
        child_splitter = RecursiveCharacterTextSplitter(
            chunk_size=self.CHILD_SIZE,
            chunk_overlap=self.OVERLAP,
            length_function=self.count_tokens,
            separators=self.SEPARATORS,
        )
        result = []
        for pi, parent in enumerate(parent_docs):
            child_docs = child_splitter.create_documents([parent.page_content])
            for ci, child in enumerate(child_docs):
                result.append(
                    {
                        "content": self.inject_heading_prefix(
                            child.page_content, metadata.get("heading_path", "")
                        ),
                        "metadata": {
                            **metadata,
                            "parent_content": parent.page_content,
                            "tokens": self.count_tokens(child.page_content),
                            "chunk_strategy": self.chunk_strategy,
                        },
                    }
                )
        return result
