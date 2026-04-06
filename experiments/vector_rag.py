"""Local vector-RAG utilities for the experiments harness.

This module stays inside ``experiments/`` so the production/demo app remains
unchanged. It supports:
- local embedding generation via sentence-transformers
- local cosine search over stored embeddings
- optional HNSW acceleration when ``hnswlib`` is installed
- optional BM25 lexical retrieval
- optional local cross-encoder reranking
"""

from __future__ import annotations

from functools import lru_cache
import json
from pathlib import Path
import re
from typing import Any


try:
    import numpy as np
except ImportError:  # pragma: no cover - dependency guidance handled at runtime
    np = None


_WORD_RE = re.compile(r"[a-zA-Z0-9_]{2,}")


def _require_numpy():
    if np is None:  # pragma: no cover - runtime dependency guard
        raise ImportError(
            "numpy is required for experiments vector-RAG builds. "
            "Install the optional experiments dependencies."
        )
    return np


@lru_cache(maxsize=4)
def load_embedding_model(model_name: str):
    """Load one local sentence-transformers embedding model and cache it."""
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:  # pragma: no cover - runtime dependency guard
        raise ImportError(
            "sentence-transformers is required for local vector-RAG builds. "
            "Install the optional experiments dependencies."
        ) from exc
    return SentenceTransformer(model_name)


@lru_cache(maxsize=4)
def load_cross_encoder(model_name: str):
    """Load one local cross-encoder reranker and cache it."""
    try:
        from sentence_transformers import CrossEncoder
    except ImportError as exc:  # pragma: no cover - runtime dependency guard
        raise ImportError(
            "sentence-transformers is required for local reranking. "
            "Install the optional experiments dependencies."
        ) from exc
    return CrossEncoder(model_name)


def embed_texts_locally(
    texts: list[str],
    *,
    model_name: str,
    normalize_embeddings: bool = True,
):
    """Encode texts into a local dense matrix."""
    np_mod = _require_numpy()
    model = load_embedding_model(model_name)
    embeddings = model.encode(
        texts,
        normalize_embeddings=normalize_embeddings,
        convert_to_numpy=True,
        show_progress_bar=False,
    )
    return np_mod.asarray(embeddings, dtype=np_mod.float32)


def rerank_pairs_locally(
    *,
    query: str,
    passages: list[str],
    model_name: str,
) -> list[float]:
    """Score query/passage pairs with a local cross-encoder reranker."""
    model = load_cross_encoder(model_name)
    pairs = [(query, passage) for passage in passages]
    scores = model.predict(pairs)
    return [float(score) for score in scores]


def tokenize_for_bm25(text: str) -> list[str]:
    """Tokenize text into simple lowercase word pieces."""
    return [token.lower() for token in _WORD_RE.findall(text)]


def build_hnsw_index(
    *,
    embeddings,
    index_path: str | Path,
    space: str = "cosine",
) -> bool:
    """Optionally build a local HNSW index when ``hnswlib`` is available."""
    try:
        import hnswlib
    except ImportError:  # pragma: no cover - optional acceleration
        return False

    np_mod = _require_numpy()
    matrix = np_mod.asarray(embeddings, dtype=np_mod.float32)
    if matrix.size == 0:
        return False

    index = hnswlib.Index(space=space, dim=int(matrix.shape[1]))
    index.init_index(max_elements=int(matrix.shape[0]), ef_construction=200, M=16)
    index.add_items(matrix, ids=np_mod.arange(matrix.shape[0], dtype=np_mod.int32))
    index.set_ef(min(100, max(16, int(matrix.shape[0]))))
    index.save_index(str(index_path))
    return True


def hnsw_search(
    *,
    query_embedding,
    index_path: str | Path,
    top_k: int,
) -> list[tuple[int, float]] | None:
    """Search an HNSW index if the dependency is installed and the file exists."""
    try:
        import hnswlib
    except ImportError:  # pragma: no cover - optional acceleration
        return None

    np_mod = _require_numpy()
    path = Path(index_path)
    if not path.exists():
        return None

    metadata = path.with_suffix(".meta.json")
    if not metadata.exists():
        return None

    payload = json.loads(metadata.read_text(encoding="utf-8"))
    index = hnswlib.Index(space=payload.get("space", "cosine"), dim=int(payload["dim"]))
    index.load_index(str(path), max_elements=int(payload["size"]))
    labels, distances = index.knn_query(
        np_mod.asarray(query_embedding, dtype=np_mod.float32).reshape(1, -1),
        k=min(top_k, int(payload["size"])),
    )
    results: list[tuple[int, float]] = []
    for idx, distance in zip(labels[0], distances[0]):
        results.append((int(idx), float(1.0 - distance)))
    return results


def write_hnsw_metadata(
    *,
    index_path: str | Path,
    dim: int,
    size: int,
    space: str = "cosine",
) -> None:
    """Write a small sidecar so the HNSW index can be reopened later."""
    from utils import atomic_write_text

    payload = {"dim": int(dim), "size": int(size), "space": space}
    atomic_write_text(Path(index_path).with_suffix(".meta.json"), json.dumps(payload, indent=2))


def brute_force_cosine_search(
    *,
    query_embedding,
    embeddings,
    top_k: int,
) -> list[tuple[int, float]]:
    """Search one local embedding matrix with cosine similarity."""
    np_mod = _require_numpy()
    query = np_mod.asarray(query_embedding, dtype=np_mod.float32).reshape(1, -1)
    matrix = np_mod.asarray(embeddings, dtype=np_mod.float32)
    if matrix.size == 0:
        return []
    scores = (matrix @ query.T).reshape(-1)
    k = min(top_k, int(scores.shape[0]))
    if k <= 0:
        return []
    indices = np_mod.argpartition(-scores, kth=k - 1)[:k]
    ranked = sorted(((int(idx), float(scores[idx])) for idx in indices), key=lambda item: item[1], reverse=True)
    return ranked


def bm25_search(
    *,
    query: str,
    tokenized_corpus: list[list[str]],
    top_k: int,
) -> list[tuple[int, float]]:
    """Run local BM25 when the optional dependency is available, else lexical fallback."""
    query_tokens = tokenize_for_bm25(query)
    if not query_tokens or not tokenized_corpus:
        return []

    try:
        from rank_bm25 import BM25Okapi

        scorer = BM25Okapi(tokenized_corpus)
        scores = scorer.get_scores(query_tokens)
    except ImportError:  # pragma: no cover - fallback path
        scores = []
        query_set = set(query_tokens)
        for doc_tokens in tokenized_corpus:
            counts: dict[str, int] = {}
            for token in doc_tokens:
                counts[token] = counts.get(token, 0) + 1
            overlap = sum(counts.get(token, 0) for token in query_set)
            density = overlap / max(1, len(doc_tokens))
            scores.append(float(overlap + (density * 10)))

    ranked = sorted(
        ((index, float(score)) for index, score in enumerate(scores) if float(score) > 0),
        key=lambda item: item[1],
        reverse=True,
    )
    return ranked[:top_k]


def reciprocal_rank_fusion(
    rankings: list[list[int]],
    *,
    rrf_k: int = 60,
) -> list[tuple[int, float]]:
    """Fuse multiple ranked candidate lists with reciprocal rank fusion."""
    scores: dict[int, float] = {}
    for ranking in rankings:
        for position, index in enumerate(ranking, start=1):
            scores[index] = scores.get(index, 0.0) + (1.0 / (rrf_k + position))
    return sorted(scores.items(), key=lambda item: item[1], reverse=True)


def load_embeddings(path: str | Path):
    """Load a stored embedding matrix."""
    np_mod = _require_numpy()
    return np_mod.load(Path(path))


def save_embeddings(path: str | Path, embeddings) -> None:
    """Persist one embedding matrix to disk."""
    np_mod = _require_numpy()
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    np_mod.save(Path(path), np_mod.asarray(embeddings, dtype=np_mod.float32))
