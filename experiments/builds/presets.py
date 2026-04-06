"""Preset build configurations for the initial experiment matrix."""

from __future__ import annotations

from experiments.models import PageIndexBuildConfig, RAGBuildConfig, VectorRAGBuildConfig


def rag_standard_preset() -> RAGBuildConfig:
    """Return the default flat-chunk baseline build config."""
    return RAGBuildConfig(
        label="rag_standard",
        chunk_size_tokens=700,
        chunk_overlap_tokens=120,
        min_chunk_tokens=80,
    )


def rag_vector_preset(
    *,
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2",
    vector_backend: str = "auto",
) -> VectorRAGBuildConfig:
    """Return the default local vector-RAG build config for experiments."""
    return VectorRAGBuildConfig(
        label="rag_vector",
        embedding_model=embedding_model,
        vector_backend=vector_backend,
        chunk_size_tokens=700,
        chunk_overlap_tokens=120,
        min_chunk_tokens=80,
    )


def pageindex_base_preset(*, top_sections_target: int = 4) -> PageIndexBuildConfig:
    """Return a PageIndex build with relationship maintenance disabled."""
    return PageIndexBuildConfig(
        label="pageindex_base",
        relationship_mode="off",
        top_sections_target=top_sections_target,
    )


def pageindex_related_basic_preset(
    *,
    top_sections_target: int = 4,
) -> PageIndexBuildConfig:
    """Return a PageIndex build with deterministic relationship maintenance."""
    return PageIndexBuildConfig(
        label="pageindex_related_basic",
        relationship_mode="basic",
        top_sections_target=top_sections_target,
    )


def pageindex_related_enhanced_preset(
    *,
    top_sections_target: int = 4,
) -> PageIndexBuildConfig:
    """Return a PageIndex build with enhanced relationship maintenance."""
    return PageIndexBuildConfig(
        label="pageindex_related_enhanced",
        relationship_mode="enhanced",
        top_sections_target=top_sections_target,
    )
