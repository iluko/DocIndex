"""Preset retrieval and answer profiles for experiment runs."""

from __future__ import annotations

from experiments.models import AnswerProfileSpec, RetrievalProfileSpec


def rag_standard_profile(
    *,
    reasoning_effort: str | None = None,
    max_chunks: int = 6,
) -> RetrievalProfileSpec:
    """Baseline flat-chunk retrieval over ``rag_chunks`` artifacts."""
    return RetrievalProfileSpec(
        profile_id="rag_standard",
        artifact_family="rag_chunks",
        mode="rag_standard",
        reasoning_effort=reasoning_effort,
        advanced_retrieval=False,
        config={"max_chunks": max_chunks},
    )


def rag_vector_profile(
    *,
    reasoning_effort: str | None = None,
    dense_top_k: int = 20,
    final_top_k: int = 6,
) -> RetrievalProfileSpec:
    """Dense local vector retrieval over ``rag_vector`` artifacts."""
    return RetrievalProfileSpec(
        profile_id="rag_vector",
        artifact_family="rag_vector",
        mode="rag_vector",
        reasoning_effort=reasoning_effort,
        advanced_retrieval=False,
        config={
            "dense_top_k": dense_top_k,
            "final_top_k": final_top_k,
            "lexical_top_k": 0,
            "use_rrf": False,
            "use_reranker": False,
        },
    )


def rag_vector_rrf_profile(
    *,
    reasoning_effort: str | None = None,
    dense_top_k: int = 20,
    lexical_top_k: int = 20,
    final_top_k: int = 6,
) -> RetrievalProfileSpec:
    """Dense+lexical reciprocal-rank fusion over ``rag_vector`` artifacts."""
    return RetrievalProfileSpec(
        profile_id="rag_vector_rrf",
        artifact_family="rag_vector",
        mode="rag_vector",
        reasoning_effort=reasoning_effort,
        advanced_retrieval=False,
        config={
            "dense_top_k": dense_top_k,
            "lexical_top_k": lexical_top_k,
            "rerank_top_n": max(dense_top_k, lexical_top_k),
            "final_top_k": final_top_k,
            "use_rrf": True,
            "use_reranker": False,
        },
    )


def rag_vector_rerank_profile(
    *,
    reasoning_effort: str | None = None,
    dense_top_k: int = 20,
    lexical_top_k: int = 20,
    rerank_top_n: int = 20,
    final_top_k: int = 6,
    reranker_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2",
) -> RetrievalProfileSpec:
    """Dense+lexical retrieval with local cross-encoder reranking."""
    return RetrievalProfileSpec(
        profile_id="rag_vector_rerank",
        artifact_family="rag_vector",
        mode="rag_vector",
        reasoning_effort=reasoning_effort,
        advanced_retrieval=False,
        config={
            "dense_top_k": dense_top_k,
            "lexical_top_k": lexical_top_k,
            "rerank_top_n": rerank_top_n,
            "final_top_k": final_top_k,
            "use_rrf": True,
            "use_reranker": True,
            "reranker_model": reranker_model,
        },
    )


def hybrid_profile(
    *,
    reasoning_effort: str | None = None,
    max_docs: int = 3,
) -> RetrievalProfileSpec:
    """Standard hybrid PageIndex retrieval over ``pageindex_tree`` artifacts."""
    return RetrievalProfileSpec(
        profile_id="hybrid",
        artifact_family="pageindex_tree",
        mode="hybrid",
        reasoning_effort=reasoning_effort,
        advanced_retrieval=False,
        config={"max_docs": max_docs},
    )


def hybrid_advanced_profile(
    *,
    reasoning_effort: str | None = None,
    max_docs: int = 3,
    max_docs_cap: int = 6,
    max_nodes_cap: int = 6,
) -> RetrievalProfileSpec:
    """Hybrid PageIndex retrieval with advanced query-time features enabled."""
    return RetrievalProfileSpec(
        profile_id="hybrid_advanced",
        artifact_family="pageindex_tree",
        mode="hybrid",
        reasoning_effort=reasoning_effort,
        advanced_retrieval=True,
        config={
            "max_docs": max_docs,
            "max_docs_cap": max_docs_cap,
            "max_nodes_cap": max_nodes_cap,
        },
    )


def pageindex_profile(
    *,
    reasoning_effort: str | None = None,
    max_docs: int = 3,
    max_tool_calls: int = 12,
) -> RetrievalProfileSpec:
    """Agentic PageIndex retrieval over ``pageindex_tree`` artifacts."""
    return RetrievalProfileSpec(
        profile_id="pageindex",
        artifact_family="pageindex_tree",
        mode="pageindex",
        reasoning_effort=reasoning_effort,
        advanced_retrieval=False,
        config={"max_docs": max_docs, "max_tool_calls": max_tool_calls},
    )


def pageindex_advanced_profile(
    *,
    reasoning_effort: str | None = None,
    max_docs: int = 3,
    max_tool_calls: int = 12,
    max_docs_cap: int = 6,
    max_nodes_cap: int = 6,
) -> RetrievalProfileSpec:
    """Agentic PageIndex retrieval plus advanced routing/planning policy."""
    return RetrievalProfileSpec(
        profile_id="pageindex_advanced",
        artifact_family="pageindex_tree",
        mode="pageindex",
        reasoning_effort=reasoning_effort,
        advanced_retrieval=True,
        config={
            "max_docs": max_docs,
            "max_tool_calls": max_tool_calls,
            "max_docs_cap": max_docs_cap,
            "max_nodes_cap": max_nodes_cap,
        },
    )


def aligned_default_answer_profile(
    *,
    reasoning_effort: str | None = None,
) -> AnswerProfileSpec:
    """Shared answer profile intended to keep prompt semantics aligned."""
    return AnswerProfileSpec(
        profile_id="aligned_default",
        prompt_family="aligned_default",
        reasoning_effort=reasoning_effort,
        config={"citations": "inline"},
    )


def list_default_retrieval_profiles(
    *,
    reasoning_effort: str | None = None,
) -> list[RetrievalProfileSpec]:
    """Return the default comparison retrieval profiles."""
    return [
        rag_standard_profile(reasoning_effort=reasoning_effort),
        rag_vector_profile(reasoning_effort=reasoning_effort),
        rag_vector_rrf_profile(reasoning_effort=reasoning_effort),
        rag_vector_rerank_profile(reasoning_effort=reasoning_effort),
        hybrid_profile(reasoning_effort=reasoning_effort),
        hybrid_advanced_profile(reasoning_effort=reasoning_effort),
        pageindex_profile(reasoning_effort=reasoning_effort),
        pageindex_advanced_profile(reasoning_effort=reasoning_effort),
    ]
