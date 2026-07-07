from __future__ import annotations

from typing import Any, List


class RAGSystem:
    """Minimal retrieval wrapper. Full chunking/ranking can attach here later."""

    def __init__(self, long_term_memory: Any):
        self.long_term_memory = long_term_memory

    def retrieve(self, query: str, top_k: int = 3) -> List[str]:
        return self.long_term_memory.search(query, top_k)

    def add_document(self, document: str):
        self.long_term_memory.add_document(document)

    def get_document_count(self) -> int:
        return self.long_term_memory.get_document_count()

    def clear(self):
        self.long_term_memory.clear()

    def generate_answer(self, query: str, model_manager: Any, context: str = "") -> str:
        docs = self.retrieve(query)
        retrieved_context = "\n".join(docs)
        prompt = (
            "请基于给定上下文回答问题。如果上下文不足，请说明缺少信息。\n"
            f"额外上下文:\n{context}\n\n"
            f"检索上下文:\n{retrieved_context}\n\n"
            f"问题: {query}"
        )
        return model_manager.generate(prompt)
