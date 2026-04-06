"""Embedding-backed local vector-RAG retrieval adapter."""

from __future__ import annotations

import json
from pathlib import Path
import time

from experiments.retrieval.base import (
    RetrievalAdapter,
    RetrievalExecutionContext,
    RetrievalExecutionResult,
)
from experiments.vector_rag import (
    bm25_search,
    brute_force_cosine_search,
    embed_texts_locally,
    hnsw_search,
    load_embeddings,
    reciprocal_rank_fusion,
    rerank_pairs_locally,
)
from utils import estimate_tokens


def _find_output_path(build, label: str) -> str | None:
    for output in build.outputs:
        if output.label == label:
            return output.path
    return None


class VectorRAGRetrievalAdapter(RetrievalAdapter):
    """Local vector-search retrieval with optional lexical fusion and reranking."""

    mode = "rag_vector"
    artifact_family = "rag_vector"

    async def retrieve(self, context: RetrievalExecutionContext) -> RetrievalExecutionResult:
        retrieval_started_at = time.perf_counter()
        chunks_path = _find_output_path(context.build, "rag_vector_chunks")
        embeddings_path = _find_output_path(context.build, "rag_vector_embeddings")
        metadata_path = _find_output_path(context.build, "rag_vector_meta")
        if not chunks_path or not embeddings_path or not metadata_path:
            raise FileNotFoundError(
                f"Build '{context.build.build_id}' is missing vector-RAG outputs."
            )

        chunks = json.loads(Path(chunks_path).read_text(encoding="utf-8"))
        metadata = json.loads(Path(metadata_path).read_text(encoding="utf-8"))
        embeddings = load_embeddings(embeddings_path)

        cfg = context.retrieval_profile.config
        dense_top_k = int(cfg.get("dense_top_k", 20))
        lexical_top_k = int(cfg.get("lexical_top_k", 0))
        rerank_top_n = int(cfg.get("rerank_top_n", 20))
        final_top_k = int(cfg.get("final_top_k", 6))
        use_rrf = bool(cfg.get("use_rrf", False))
        use_reranker = bool(cfg.get("use_reranker", False))
        embedding_model = str(cfg.get("embedding_model") or metadata.get("embedding_model"))
        reranker_model = str(
            cfg.get("reranker_model", "cross-encoder/ms-marco-MiniLM-L-6-v2")
        )

        embed_started_at = time.perf_counter()
        query_embedding = embed_texts_locally(
            [context.query],
            model_name=embedding_model,
            normalize_embeddings=bool(metadata.get("normalize_embeddings", True)),
        )[0]
        query_embed_elapsed = max(0.0, time.perf_counter() - embed_started_at)

        ann_started_at = time.perf_counter()
        dense_hits = self._dense_search(
            query_embedding=query_embedding,
            embeddings=embeddings,
            build=context.build,
            top_k=dense_top_k,
            backend=str(metadata.get("vector_backend", "brute_force")),
        )
        dense_elapsed = max(0.0, time.perf_counter() - ann_started_at)

        lexical_hits: list[tuple[int, float]] = []
        lexical_elapsed = 0.0
        lexical_path = _find_output_path(context.build, "rag_vector_lexical")
        if lexical_top_k > 0 and lexical_path:
            lexical_started_at = time.perf_counter()
            lexical_payload = json.loads(Path(lexical_path).read_text(encoding="utf-8"))
            tokenized_corpus = [item.get("tokens", []) for item in lexical_payload]
            lexical_hits = bm25_search(
                query=context.query,
                tokenized_corpus=tokenized_corpus,
                top_k=lexical_top_k,
            )
            lexical_elapsed = max(0.0, time.perf_counter() - lexical_started_at)

        candidate_indices, fusion_trace = self._select_candidates(
            dense_hits=dense_hits,
            lexical_hits=lexical_hits,
            use_rrf=use_rrf,
            rerank_top_n=rerank_top_n,
        )

        rerank_elapsed = 0.0
        final_ranked: list[tuple[int, float]]
        if use_reranker and candidate_indices:
            rerank_started_at = time.perf_counter()
            rerank_scores = rerank_pairs_locally(
                query=context.query,
                passages=[chunks[index]["text"] for index in candidate_indices],
                model_name=reranker_model,
            )
            rerank_elapsed = max(0.0, time.perf_counter() - rerank_started_at)
            final_ranked = sorted(
                zip(candidate_indices, rerank_scores),
                key=lambda item: item[1],
                reverse=True,
            )
        else:
            dense_score_map = {index: score for index, score in dense_hits}
            lexical_score_map = {index: score for index, score in lexical_hits}
            final_ranked = [
                (
                    index,
                    float(
                        fusion_trace.get("rrf_scores", {}).get(str(index))
                        or dense_score_map.get(index)
                        or lexical_score_map.get(index)
                        or 0.0
                    ),
                )
                for index in candidate_indices
            ]

        selected_ranked = final_ranked[:final_top_k]
        selected_docs: list[str] = []
        selected_nodes: list[str] = []
        retrieved_context_parts: list[str] = []
        sources: list[dict] = []

        for index, score in selected_ranked:
            chunk = chunks[index]
            doc_id = chunk.get("doc_id", "")
            if doc_id and doc_id not in selected_docs:
                selected_docs.append(doc_id)
            chunk_id = chunk.get("chunk_id", "")
            selected_nodes.append(chunk_id)
            page_start = chunk.get("page_start")
            page_end = chunk.get("page_end")
            page_label = (
                f"pages {page_start}-{page_end}"
                if page_start and page_end
                else "unpaged"
            )
            header = f"[{chunk_id} :: {page_label}]"
            retrieved_context_parts.append(f"{header}\n\n{chunk.get('text', '')}")
            sources.append(
                {
                    "node_ref": chunk_id,
                    "doc_id": doc_id,
                    "section": chunk_id,
                    "page_range": (
                        f"{page_start}–{page_end}" if page_start and page_end else "n/a"
                    ),
                    "score": round(float(score), 4),
                }
            )

        retrieved_context = "\n\n".join(retrieved_context_parts)
        return RetrievalExecutionResult(
            selected_docs=selected_docs,
            selected_nodes=selected_nodes,
            retrieved_context=retrieved_context,
            sources=sources,
            trace={
                "mode": "rag_vector",
                "vector_backend": metadata.get("vector_backend", "brute_force"),
                "embedding_model": embedding_model,
                "reranker_model": reranker_model if use_reranker else None,
                "dense_hits": [
                    {
                        "chunk_id": chunks[index]["chunk_id"],
                        "doc_id": chunks[index]["doc_id"],
                        "score": round(float(score), 4),
                    }
                    for index, score in dense_hits[: min(len(dense_hits), dense_top_k)]
                ],
                "lexical_hits": [
                    {
                        "chunk_id": chunks[index]["chunk_id"],
                        "doc_id": chunks[index]["doc_id"],
                        "score": round(float(score), 4),
                    }
                    for index, score in lexical_hits[: min(len(lexical_hits), lexical_top_k)]
                ],
                "fusion": fusion_trace,
                "selected_chunk_ids": selected_nodes,
            },
            retrieval_metrics={
                "retrieval_time_seconds": max(
                    0.0, time.perf_counter() - retrieval_started_at
                ),
                "retrieved_context_tokens": estimate_tokens(retrieved_context),
                "query_embedding_time_seconds": query_embed_elapsed,
                "ann_search_time_seconds": dense_elapsed,
                "lexical_search_time_seconds": lexical_elapsed,
                "rerank_time_seconds": rerank_elapsed,
                "candidate_chunks": len(candidate_indices),
                "selected_chunks": len(selected_ranked),
            },
        )

    def _dense_search(
        self,
        *,
        query_embedding,
        embeddings,
        build,
        top_k: int,
        backend: str,
    ) -> list[tuple[int, float]]:
        index_path = _find_output_path(build, "rag_vector_index")
        if backend == "hnsw" and index_path:
            hits = hnsw_search(query_embedding=query_embedding, index_path=index_path, top_k=top_k)
            if hits is not None:
                return hits
        return brute_force_cosine_search(
            query_embedding=query_embedding,
            embeddings=embeddings,
            top_k=top_k,
        )

    def _select_candidates(
        self,
        *,
        dense_hits: list[tuple[int, float]],
        lexical_hits: list[tuple[int, float]],
        use_rrf: bool,
        rerank_top_n: int,
    ) -> tuple[list[int], dict]:
        dense_rank = [index for index, _ in dense_hits]
        lexical_rank = [index for index, _ in lexical_hits]

        if use_rrf:
            fused_inputs = [dense_rank]
            if lexical_rank:
                fused_inputs.append(lexical_rank)
            fused = reciprocal_rank_fusion(fused_inputs)
            selected = [index for index, _ in fused[:rerank_top_n]]
            return selected, {
                "method": "rrf",
                "rrf_scores": {str(index): round(float(score), 6) for index, score in fused[:rerank_top_n]},
            }

        selected = dense_rank[:rerank_top_n]
        return selected, {"method": "dense_only"}
