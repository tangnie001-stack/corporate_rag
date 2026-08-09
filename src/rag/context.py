"""RAG 上下文数据类 — 单个检索结果块的封装。"""

from dataclasses import dataclass, field

from src.config.const import ENTITY_LABELS, ENTITY_RENDER_ORDER


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
    entities: dict = field(default_factory=dict)  # 业务实体，来自 chunk.metadata

    def to_citation(self) -> str:
        """格式化为 Markdown 引用块。"""
        snippet = self.content[:200].replace("\n", " ")
        return f"> **来源:** {self.source} (第{self.page}页)\n> {snippet}\n"

    def to_prompt_text(self) -> str:
        """渲染为喂给生成模型的单个上下文文本。

        生产 prompt（prompt.format_context）与 RAGAS 评估的 NLI 上下文
        共用此格式，保证评估时 NLI 看到的上下文与线上生成时完全一致
        （含来源/页码锚点，如文件名里的期间），避免两处实现漂移。
        实体按 ENTITY_RENDER_ORDER 渲染存在的核心实体，无实体时保持原格式。
        """
        parts = [f"来源: {self.source} (第{self.page}页)"]
        entity_parts = []
        for key in ENTITY_RENDER_ORDER:
            value = self.entities.get(key)
            if value:
                label = ENTITY_LABELS.get(key, key)
                entity_parts.append(f"{label}: {value}")
        if entity_parts:
            parts.append(" ".join(entity_parts))
        parts.append(f"内容: {self.content}")
        return "\n".join(parts)
