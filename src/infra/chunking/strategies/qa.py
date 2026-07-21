import re
from loguru import logger
from src.infra.chunking.strategies.base import BaseChunker


class QAChunker(BaseChunker):
    chunk_strategy = "qa"

    def chunk(self, text: str, metadata: dict) -> list[dict]:
        qa_pattern = re.compile(r"(问[：:].*?答[：:].*?)(?=问[：:]|\Z)", re.DOTALL)
        qa_pairs = qa_pattern.findall(text)
        if not qa_pairs:
            qa_pairs = [text]
        result = []
        for i, pair in enumerate(qa_pairs):
            pair = pair.strip()
            if not pair:
                continue
            result.append(
                {
                    "content": self.inject_heading_prefix(
                        pair, metadata.get("heading_path", "")
                    ),
                    "metadata": {
                        **metadata,
                        "parent_content": None,
                        "tokens": self.count_tokens(pair),
                        "chunk_strategy": self.chunk_strategy,
                    },
                }
            )
        logger.info(
            "[qa] chunks={} qa_pairs={} tokens={}",
            len(result),
            len(qa_pairs),
            sum(c["metadata"]["tokens"] for c in result),
        )
        return result
