"""Baseline flat-chunk RAG retrieval adapter."""

from __future__ import annotations

import json
import math
import re
import time
from pathlib import Path

from experiments.retrieval.base import (
    RetrievalAdapter,
    RetrievalExecutionContext,
    RetrievalExecutionResult,
)
from utils import estimate_tokens


_WORD_RE = re.compile(r"[a-zA-Z0-9_]{2,}")


def _find_output_path(build, label: str) -> str:
    for output in build.outputs:
        if output.label == label:
            return output.path
    raise FileNotFoundError(f"Build '{build.build_id}' has no output labeled '{label}'.")


def _tokenize(text: str) -> list[str]:
    return [token.lower() for token in _WORD_RE.findall(text)]


class RAGStandardRetrievalAdapter(RetrievalAdapter):
    """Simple lexical baseline over flat chunk artifacts."""

    mode = "rag_standard"
    artifact_family = "rag_chunks"

    async def retrieve(self, context: RetrievalExecutionContext) -> RetrievalExecutionResult:
        retrieval_started_at = time.perf_counter()
        chunks_path = Path(_find_output_path(context.build, "rag_chunks"))
        chunks = json.loads(chunks_path.read_text(encoding="utf-8"))

        query_tokens = _tokenize(context.query)
        if not query_tokens:
            return RetrievalExecutionResult(
                retrieved_context="",
                trace={"mode": "rag_standard", "scored_chunks": []},
                retrieval_metrics={
                    "retrieval_time_seconds": max(
                        0.0, time.perf_counter() - retrieval_started_at
                    ),
                    "retrieved_context_tokens": 0,
                },
            )

        scored: list[tuple[float, dict]] = []
        query_set = set(query_tokens)
        for chunk in chunks:
            text = chunk.get("text", "")
            chunk_tokens = _tokenize(text)
            if not chunk_tokens:
                continue
            chunk_counts: dict[str, int] = {}
            for token in chunk_tokens:
                chunk_counts[token] = chunk_counts.get(token, 0) + 1
            overlap = sum(chunk_counts.get(token, 0) for token in query_set)
            if overlap == 0:
                continue
            density = overlap / max(1, len(chunk_tokens))
            title_bonus = 0.5 if any(
                token in _tokenize(chunk.get("doc_title", "")) for token in query_set
            ) else 0.0
            score = overlap + (density * 10) + title_bonus
            scored.append((score, chunk))

        scored.sort(key=lambda item: item[0], reverse=True)
        max_chunks = int(context.retrieval_profile.config.get("max_chunks", 6))
        selected = [chunk for _, chunk in scored[:max_chunks]]
        selected_docs = []
        for chunk in selected:
            doc_id = chunk.get("doc_id", "")
            if doc_id and doc_id not in selected_docs:
                selected_docs.append(doc_id)

        retrieved_context_parts = []
        sources = []
        for chunk in selected:
            chunk_id = chunk.get("chunk_id", "")
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
                    "doc_id": chunk.get("doc_id", ""),
                    "section": chunk.get("chunk_id", ""),
                    "page_range": (
                        f"{page_start}–{page_end}" if page_start and page_end else "n/a"
                    ),
                    "score": round(next(score for score, c in scored if c is chunk), 3),
                }
            )

        retrieved_context = "\n\n".join(retrieved_context_parts)
        return RetrievalExecutionResult(
            selected_docs=selected_docs,
            selected_nodes=[chunk.get("chunk_id", "") for chunk in selected],
            retrieved_context=retrieved_context,
            sources=sources,
            trace={
                "mode": "rag_standard",
                "scored_chunks": [
                    {
                        "chunk_id": chunk.get("chunk_id", ""),
                        "doc_id": chunk.get("doc_id", ""),
                        "score": round(score, 3),
                    }
                    for score, chunk in scored[: max_chunks * 2]
                ],
            },
            retrieval_metrics={
                "retrieval_time_seconds": max(
                    0.0, time.perf_counter() - retrieval_started_at
                ),
                "retrieved_context_tokens": estimate_tokens(retrieved_context),
                "candidate_chunks": len(scored),
                "selected_chunks": len(selected),
            },
        )
