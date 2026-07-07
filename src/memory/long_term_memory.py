from __future__ import annotations

import pickle
from pathlib import Path
from typing import List

from src.core.config import get_settings


class LongTermMemory:
    """Simple persistent document memory used before a full vector store is added."""

    def __init__(self, vector_store_path: str | None = None):
        settings = get_settings()
        self.store_path = Path(vector_store_path).resolve() if vector_store_path else settings.vector_store_path
        self.documents: List[str] = []
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        self.load()

    def add_document(self, document: str):
        if document:
            self.documents.append(document)
            self.save()

    def add_documents(self, documents: List[str]):
        self.documents.extend([doc for doc in documents if doc])
        self.save()

    def search(self, query: str, top_k: int = 3) -> List[str]:
        if not query or not self.documents:
            return []

        query_terms = set(query.lower().split())
        scored = []
        for doc in self.documents:
            doc_terms = set(doc.lower().split())
            score = len(query_terms & doc_terms)
            if query.lower() in doc.lower():
                score += 3
            scored.append((score, doc))

        scored.sort(key=lambda item: item[0], reverse=True)
        return [doc for score, doc in scored[:top_k] if score > 0] or self.documents[:top_k]

    def save(self):
        with self.store_path.open("wb") as handle:
            pickle.dump({"documents": self.documents}, handle)

    def load(self):
        if not self.store_path.exists():
            return
        try:
            with self.store_path.open("rb") as handle:
                data = pickle.load(handle)
            self.documents = list(data.get("documents", []))
        except Exception:
            self.documents = []

    def clear(self):
        self.documents = []
        self.save()

    def get_document_count(self) -> int:
        return len(self.documents)
