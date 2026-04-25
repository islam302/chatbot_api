"""RAG (Retrieval Augmented Generation) service.

Builds a FAISS vector store from the active uploaded documents and answers
questions against it using OpenAI chat models. The vector store is cached in
memory and only rebuilt when the set of active documents changes.

The OpenAI dependency is imported lazily so the rest of the API remains
usable even when the OpenAI/LangChain stack is not installed.
"""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass, field
from typing import Iterable

from django.conf import settings

from ..models import UploadedDocument

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """You are a smart assistant. Answer using only the provided context.
- For questions in the context, answer using only that information.
- For questions outside the context, reply: "Sorry, your question is outside my scope. Please rephrase or stay within the topic."
- Always answer in the language of the question.
- When sharing URLs, use plain markdown links.
"""


class RagUnavailable(RuntimeError):
    """Raised when the RAG stack cannot be initialised (missing deps or key)."""


@dataclass
class RagAnswer:
    answer: str
    sources: list[dict] = field(default_factory=list)


class RagService:
    """Singleton-style service maintaining a FAISS index over active documents."""

    _instance: "RagService | None" = None
    _lock = threading.Lock()

    def __init__(self):
        self._vectorstore = None
        self._documents_signature: tuple | None = None
        self._embeddings = None
        self._llm = None

    @classmethod
    def instance(cls) -> "RagService":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def invalidate(self):
        """Force the index to rebuild on the next request."""
        with self._lock:
            self._vectorstore = None
            self._documents_signature = None

    def answer(self, question: str, history: Iterable[dict] | None = None) -> RagAnswer:
        history = list(history or [])
        self._ensure_index()

        if self._vectorstore is None:
            raise RagUnavailable("No active documents are available for retrieval.")

        results = self._vectorstore.similarity_search_with_score(question, k=8)
        threshold = float(getattr(settings, "RAG_SIMILARITY_THRESHOLD", 0.5))
        relevant = [(doc, score) for doc, score in results if score >= threshold]

        if not relevant:
            return RagAnswer(
                answer="Sorry, your question is outside the scope of the available documents."
            )

        context = "\n\n".join(doc.page_content for doc, _ in relevant)
        history_text = self._render_history(history)

        prompt = (
            f"{SYSTEM_PROMPT}\n\n"
            f"Conversation history:\n{history_text}\n\n"
            f"Available context:\n{context}\n\n"
            f"Current question:\n{question}\n"
        )

        response = self._llm.invoke(prompt)
        sources = [
            {
                "filename": doc.metadata.get("source"),
                "document_id": doc.metadata.get("doc_id"),
                "score": float(score),
            }
            for doc, score in relevant
        ]
        return RagAnswer(answer=response.content, sources=sources)

    def _ensure_index(self):
        signature = self._compute_signature()
        if self._vectorstore is not None and self._documents_signature == signature:
            return

        with self._lock:
            signature = self._compute_signature()
            if self._vectorstore is not None and self._documents_signature == signature:
                return

            documents = self._load_documents()
            if not documents:
                self._vectorstore = None
                self._documents_signature = None
                return

            embeddings = self._get_embeddings()
            from langchain_community.vectorstores import FAISS  # type: ignore

            self._vectorstore = FAISS.from_documents(documents, embeddings)
            self._documents_signature = signature
            self._get_llm()

    def _compute_signature(self) -> tuple:
        ids = (
            UploadedDocument.objects.filter(
                is_active=True, processing_status="completed"
            )
            .order_by("created_at")
            .values_list("id", "updated_at")
        )
        return tuple((str(i), ts.isoformat()) for i, ts in ids)

    def _load_documents(self):
        try:
            from langchain.text_splitter import RecursiveCharacterTextSplitter  # type: ignore
            import docx2txt  # type: ignore
        except ImportError as exc:
            raise RagUnavailable(
                "RAG dependencies are not installed (langchain, docx2txt)."
            ) from exc

        splitter = RecursiveCharacterTextSplitter(chunk_size=1500, chunk_overlap=200)
        out = []
        active = UploadedDocument.objects.filter(
            is_active=True, processing_status="completed"
        )
        for doc in active:
            try:
                path = doc.file.path
                if not os.path.exists(path):
                    logger.warning("Document file missing on disk: %s", path)
                    continue
                text = docx2txt.process(path)
                chunks = splitter.create_documents(
                    [text],
                    metadatas=[{"source": doc.filename, "doc_id": str(doc.id)}],
                )
                out.extend(chunks)
            except Exception:
                logger.exception("Failed to load document %s", doc.id)
        return out

    def _get_embeddings(self):
        if self._embeddings is not None:
            return self._embeddings
        try:
            from langchain_openai import OpenAIEmbeddings  # type: ignore
        except ImportError as exc:
            raise RagUnavailable("langchain-openai not installed.") from exc

        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RagUnavailable("OPENAI_API_KEY is not configured.")

        self._embeddings = OpenAIEmbeddings(
            openai_api_key=api_key, model="text-embedding-3-small"
        )
        return self._embeddings

    def _get_llm(self):
        if self._llm is not None:
            return self._llm
        try:
            from langchain_openai import ChatOpenAI  # type: ignore
        except ImportError as exc:
            raise RagUnavailable("langchain-openai not installed.") from exc

        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RagUnavailable("OPENAI_API_KEY is not configured.")

        self._llm = ChatOpenAI(
            model_name=os.getenv("RAG_MODEL", "gpt-4o"),
            temperature=0,
            openai_api_key=api_key,
        )
        return self._llm

    @staticmethod
    def _render_history(history: list[dict]) -> str:
        out = []
        for msg in history:
            role = msg.get("role", "user")
            content = (msg.get("content") or "").strip()
            if not content:
                continue
            label = "User" if role == "user" else "Assistant"
            out.append(f"{label}: {content}")
        return "\n".join(out)
