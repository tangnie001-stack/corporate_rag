"""RAG 上下文数据类 — 单个检索结果块的封装。"""

from dataclasses import dataclass


@dataclass(slots=True)
class RAGContext:
    """单个检索上下文分块 — 包含原文内容和来源元数据。"""

    content: str
    source: str
    page: int
    doc_id: str
    chunk_id: str
    parent_content: str | None = None
    score: float = 0.0

    def to_citation(self) -> str:
        """格式化为 Markdown 引用块。"""
        snippet = self.content[:200].replace("\n", " ")
        return f"> **来源:** {self.source} (第{self.page}页)\n> {snippet}\n"

    def to_prompt_text(self) -> str:
        """渲染为喂给生成模型的单个上下文文本。

        生产 prompt（prompt.format_context）与 RAGAS 评估的 NLI 上下文
        共用此格式，保证评估时 NLI 看到的上下文与线上生成时完全一致
        （含来源/页码锚点，如文件名里的期间），避免两处实现漂移。
        """
        return f"来源: {self.source} (第{self.page}页)\n内容: {self.content}"
