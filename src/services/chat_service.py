"""对话问答服务 — 执行 RAG 问答并保存历史。"""

from src.rag.chain import RAGChain, RAGContext


class ChatService:
    """RAG 问答服务。

    封装 RAG 问答流程和对话历史保存。
    """

    def __init__(self, rag_chain: RAGChain) -> None:
        self.rag_chain = rag_chain

    def chat(
        self,
        kb_id: str,
        session_id: str,
        query: str,
    ) -> tuple[str, list[RAGContext]]:
        """执行一轮 RAG 问答。

        Returns:
            (answer_text, citations_list) 元组
        """
        token_gen, citations = self.rag_chain.chat_with_citations(
            kb_id, session_id, query,
        )
        full_answer = "".join([t for t in token_gen])
        sources = [f"{c.source} (第{c.page}页)" for c in citations]
        self.rag_chain.chat_manager.add_message(
            session_id, "assistant", full_answer, sources=sources,
        )
        return full_answer, citations
