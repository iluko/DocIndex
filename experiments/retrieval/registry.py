"""Registry for experiment retrieval adapters."""

from __future__ import annotations

from experiments.retrieval.base import RetrievalAdapter
from experiments.retrieval.hybrid import HybridRetrievalAdapter
from experiments.retrieval.pageindex import PageIndexAgenticRetrievalAdapter
from experiments.retrieval.rag import RAGStandardRetrievalAdapter
from experiments.retrieval.rag_vector import VectorRAGRetrievalAdapter


_ADAPTERS: dict[str, RetrievalAdapter] = {
    "rag_standard": RAGStandardRetrievalAdapter(),
    "rag_vector": VectorRAGRetrievalAdapter(),
    "hybrid": HybridRetrievalAdapter(),
    "pageindex": PageIndexAgenticRetrievalAdapter(),
}


def get_retrieval_adapter(mode: str) -> RetrievalAdapter:
    """Return the retrieval adapter registered for ``mode``."""
    try:
        return _ADAPTERS[mode]
    except KeyError as exc:
        raise ValueError(f"No retrieval adapter registered for mode '{mode}'.") from exc
