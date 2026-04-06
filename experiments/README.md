# Experiments Harness

This package is a separate lab environment for evaluating variants of the main
system without disturbing the production/demo orchestration.

Current phases implemented:
- Phase 1: scaffold and domain models
- Phase 2: corpus normalization and manifest storage
- Phase 3: artifact build layer
- Phase 4: retrieval and shared answer adapters
- Phase 5: manual comparison runs, metrics, and exported reports
- Phase 6: separate Streamlit experiments UI
- Phase 7: reproducibility controls, resumable runs, and rerun support
- Phase 8: aggregate reporting by question type

What exists right now:
- reusable corpus registration under `experiments/artifacts/corpora/`
- isolated artifact builds under `experiments/artifacts/builds/`
- initial build families:
  - `rag_standard`
  - `rag_vector`
  - `pageindex_base`
  - `pageindex_related_basic`
  - `pageindex_related_enhanced`
- retrieval profiles:
  - `rag_standard`
  - `rag_vector`
  - `rag_vector_rrf`
  - `rag_vector_rerank`
  - `hybrid`
  - `hybrid_advanced`
  - `pageindex`
  - `pageindex_advanced`
- a shared answer profile:
  - `aligned_default`
- persisted manual comparison runs under `experiments/artifacts/runs/`
- exported JSON / CSV / Markdown reports under `experiments/artifacts/reports/`
- HTML exports for each run plus aggregate question-type summaries
- deterministic run spec hashes and stored requested-entry manifests
- resume and rerun support for experiment runs
- question-type tagging and manual winner annotations for later rollups
- a separate UI entrypoint at `experiments_app.py`

What does *not* exist yet:
- dataset-driven evaluation suites
- auto-grading / gold-answer scoring
- broader report aggregation across many runs
- full benchmark datasets and batch experiment orchestration

The harness is intentionally decoupled:
- register a corpus once
- build multiple artifact variants from that same corpus
- attach retrieval and answer profiles independently
- compare compatible build/profile combinations side by side

Generated experiment artifacts are ignored by git and live under:
- `experiments/artifacts/corpora/`
- `experiments/artifacts/builds/`
- `experiments/artifacts/runs/`
- `experiments/artifacts/reports/`

## Running The Experiments UI

Use Python 3.10:

```bash
cd "/Users/iluko/Documents/Personal Projects/Hybrid Approach"
source .venv310/bin/activate
pip install -r requirements-experiments.txt
streamlit run experiments_app.py
```

The extra install is required only if you want the local vector-RAG baseline.
The lexical `rag_standard` and the PageIndex-based experiment modes do not need
those optional dependencies.

The UI is separate from the main app. It lets you:
- register corpora once
- build multiple isolated artifact variants
- run one question across multiple build/profile combinations
- inspect answer metrics, traces, and exported reports
- tag runs by question type and record a human winner after review
- rerun failed/skipped entries without rerunning the whole comparison

## RAG Baselines In The Harness

The harness now has two different RAG baselines.

### `rag_standard`

This path is intentionally simple:
- ingestion builds flat text chunks into `rag_chunks.json`
- retrieval scores chunks with lexical term overlap and density
- the shared answer adapter then answers from the selected chunk text

It is **not**:
- embedding-based
- vector-search-backed
- reranker-backed

The relevant code is:
- build path: `experiments/builds/rag.py`
- retrieval path: `experiments/retrieval/rag.py`

### `rag_vector`

This is the proper local semantic RAG baseline:
- ingestion builds flat chunks
- local embeddings are generated and stored on disk
- embeddings are persisted locally in `.npy`
- local semantic retrieval runs over the stored vectors
- optional BM25-style lexical retrieval can be fused with dense retrieval
- optional local cross-encoder reranking can rerank the top candidates

Stored local artifacts include:
- chunk metadata JSON
- embedding matrix
- vector build metadata
- optional lexical token corpus
- optional local HNSW index if `hnswlib` is installed

The relevant code is:
- build path: `experiments/builds/rag_vector.py`
- retrieval path: `experiments/retrieval/rag_vector.py`
- local vector helpers: `experiments/vector_rag.py`

### Current local vector-search details

Semantic retrieval is fully local.

Today the implementation supports:
- local sentence-transformers embeddings
- local brute-force cosine search over stored embeddings
- optional local HNSW acceleration if `hnswlib` is installed
- optional BM25 / lexical fusion
- optional local cross-encoder reranking

So yes: embeddings and vectors are stored locally, semantic retrieval is local,
and reranking can also be local.

## Manual Comparison Flow

1. Create a corpus from uploaded documents.
2. Run one or more build presets for that corpus.
3. In the `Compare` tab, choose builds plus compatible retrieval profiles.
4. Tag the question type and submit one question to create a comparison run.
5. Inspect:
   - per-entry answers
   - TTFT and total latency
   - token usage and LLM-call counts
   - selected docs / nodes
   - trace JSON
   - CSV / Markdown / JSON / HTML exports
6. In the `Runs` tab, optionally:
   - resume an incomplete run
   - rerun failed/skipped entries only
   - mark the winning entry
   - add reviewer notes
7. In the `Reports` tab, inspect aggregate summaries by question type.

## Quick Practical Notes

- Use the experiments app with Python 3.10 via `.venv310`.
- This experiments layer is separate from the main `app.py` UI.
- Experiment artifacts are isolated under `experiments/artifacts/` and do not overwrite the main app’s index artifacts.
- `hybrid` and `pageindex` experiment runs reuse the project’s core retrieval logic, but only through the separate experiments harness.
- `rag_vector` needs optional dependencies from `requirements-experiments.txt`.
- If `hnswlib` is not installed, vector retrieval still works via brute-force cosine search.
