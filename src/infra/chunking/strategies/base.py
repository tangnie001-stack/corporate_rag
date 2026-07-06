"""分块器基类。"""

from abc import ABC, abstractmethod


class BaseChunker(ABC):
    """分块器抽象基类。所有分块器必须继承此类并实现 chunk()。"""

    chunk_strategy: str = ""

    @abstractmethod
    def chunk(self, text: str, metadata: dict) -> list[dict]:
        ...

    @staticmethod
    def count_tokens(text: str) -> int:
        return max(1, len(text) // 2)

    @staticmethod
    def inject_heading_prefix(content: str, heading_path: str) -> str:
        if not heading_path:
            return content
        parts = heading_path.split(" > ")
        prefix = " > ".join(parts[-2:]) if len(parts) >= 2 else parts[-1]
        return f"【{prefix}】{content}"
