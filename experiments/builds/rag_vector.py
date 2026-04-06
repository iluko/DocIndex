"""Local vector-RAG artifact builder for experiment-only baselines."""

from __future__ import annotations

import json
from pathlib import Path
import time

from experiments.builds.base import ArtifactBuildAdapter, BuildAdapterResult, BuildExecutionContext
from experiments.builds.rag import RAGBuildAdapter
from experiments.models import ArtifactOutput, BuiltDocumentArtifact, VectorRAGBuildConfig
from experiments.vector_rag import (
    build_hnsw_index,
    embed_texts_locally,
    save_embeddings,
    tokenize_for_bm25,
    write_hnsw_metadata,
)
from utils import atomic_write_text


class VectorRAGBuildAdapter(RAGBuildAdapter, ArtifactBuildAdapter):
    """Build local embedding-backed artifacts for experiment comparisons."""

    kind = "rag_vector"
    artifact_family = "rag_vector"

    async def build(self, context: BuildExecutionContext) -> BuildAdapterResult:
        config = context.config
        if not isinstance(config, VectorRAGBuildConfig):
            raise TypeError("VectorRAGBuildAdapter requires VectorRAGBuildConfig.")

        chunks_path = context.build_dir / "rag_vector_chunks.json"
        embeddings_path = context.build_dir / "rag_vector_embeddings.npy"
        lexical_path = context.build_dir / "rag_vector_lexical.json"
        index_path = context.build_dir / "rag_vector_hnsw.index"
        metadata_path = context.build_dir / "rag_vector_meta.json"

        built_docs: list[BuiltDocumentArtifact] = []
        all_chunks: list[dict] = []

        for document in context.corpus.documents:
            source_blocks = self._extract_source_blocks(Path(document.normalized_path))
            doc_chunks = self._chunk_blocks(
                doc_id=document.doc_id,
                doc_title=document.doc_title,
                blocks=source_blocks,
                chunk_size_tokens=config.chunk_size_tokens,
                overlap_tokens=config.chunk_overlap_tokens,
                min_chunk_tokens=config.min_chunk_tokens,
            )
            all_chunks.extend(doc_chunks)
            built_docs.append(
                BuiltDocumentArtifact(
                    doc_id=document.doc_id,
                    artifact_path=str(chunks_path),
                    metadata={"chunk_count": len(doc_chunks)},
                )
            )

        atomic_write_text(chunks_path, json.dumps(all_chunks, indent=2, ensure_ascii=False))

        embedding_started_at = time.perf_counter()
        embeddings = embed_texts_locally(
            [chunk["text"] for chunk in all_chunks],
            model_name=config.embedding_model,
            normalize_embeddings=config.normalize_embeddings,
        )
        embedding_elapsed = max(0.0, time.perf_counter() - embedding_started_at)
        save_embeddings(embeddings_path, embeddings)

        lexical_payload: list[dict] = []
        if config.persist_lexical_corpus:
            lexical_payload = [
                {
                    "chunk_id": chunk["chunk_id"],
                    "doc_id": chunk["doc_id"],
                    "tokens": tokenize_for_bm25(chunk["text"]),
                }
                for chunk in all_chunks
            ]
            atomic_write_text(lexical_path, json.dumps(lexical_payload, indent=2, ensure_ascii=False))

        index_started_at = time.perf_counter()
        index_backend = "brute_force"
        if config.vector_backend != "brute_force" and len(all_chunks) > 0:
            created = build_hnsw_index(embeddings=embeddings, index_path=index_path)
            if created:
                write_hnsw_metadata(
                    index_path=index_path,
                    dim=int(embeddings.shape[1]),
                    size=int(embeddings.shape[0]),
                )
                index_backend = "hnsw"
            elif config.vector_backend == "hnsw":
                raise ImportError(
                    "Vector build requested HNSW, but hnswlib is not installed."
                )
        index_elapsed = max(0.0, time.perf_counter() - index_started_at)

        metadata = {
            "embedding_model": config.embedding_model,
            "embedding_dim": int(embeddings.shape[1]) if len(all_chunks) > 0 else 0,
            "vector_backend": index_backend,
            "normalize_embeddings": config.normalize_embeddings,
            "lexical_corpus_persisted": config.persist_lexical_corpus,
            "chunk_count": len(all_chunks),
        }
        atomic_write_text(metadata_path, json.dumps(metadata, indent=2, ensure_ascii=False))

        outputs = [
            ArtifactOutput(
                label="rag_vector_chunks",
                path=str(chunks_path),
                description="Flat chunk set for local vector-RAG experiments.",
                metadata={"chunk_count": len(all_chunks)},
            ),
            ArtifactOutput(
                label="rag_vector_embeddings",
                path=str(embeddings_path),
                description="Local embedding matrix stored on disk.",
                metadata={"embedding_model": config.embedding_model},
            ),
            ArtifactOutput(
                label="rag_vector_meta",
                path=str(metadata_path),
                description="Metadata describing the local vector-RAG artifact build.",
            ),
        ]
        if config.persist_lexical_corpus:
            outputs.append(
                ArtifactOutput(
                    label="rag_vector_lexical",
                    path=str(lexical_path),
                    description="Tokenized corpus for local BM25 or lexical fallback retrieval.",
                )
            )
        if index_backend == "hnsw":
            outputs.append(
                ArtifactOutput(
                    label="rag_vector_index",
                    path=str(index_path),
                    description="Optional local HNSW ANN index.",
                    metadata={"backend": "hnsw"},
                )
            )

        return BuildAdapterResult(
            outputs=outputs,
            documents=built_docs,
            metrics={
                "docs_processed": len(context.corpus.documents),
                "total_chunks": len(all_chunks),
                "embedding_model": config.embedding_model,
                "embedding_dimensions": int(embeddings.shape[1]) if len(all_chunks) > 0 else 0,
                "vector_backend": index_backend,
                "embedding_build_time_seconds": embedding_elapsed,
                "index_build_time_seconds": index_elapsed,
            },
        )
