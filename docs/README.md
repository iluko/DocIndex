# Hybrid Approach Documentation

This folder is the current-state architecture guide for the repository. It is written against the live code, not the original design intent, so it documents both the system shape and the places where the interfaces have drifted apart.

## How To Use These Docs

Use the docs in one of these reading paths.

### If you are new to the system

1. `architecture-map.md`
2. `modules/runtime-foundation.md`
3. `modules/ingestion-pipeline.md`
4. `modules/retrieval-and-answering.md`
5. `modules/interfaces-and-operations.md`

### If you are debugging retrieval quality

1. `modules/master-tree-and-relationships.md`
2. `modules/retrieval-and-answering.md`
3. `modules/tracing-and-observability.md`
4. `modules/testing.md`

### If you are working on experiments or golden-dataset evaluation

1. `modules/experiments-harness.md`
2. `modules/experiments-ui.md`
3. `modules/retrieval-and-answering.md`
4. `modules/testing.md`

### If you are working on product surfaces

1. `modules/interfaces-and-operations.md`
2. `modules/react-frontend.md`
3. `modules/tracing-and-observability.md`

## Module Index

- `architecture-map.md`
  End-to-end map of the main runtime, experiments harness, artifact families, interface surfaces, and the most important architectural distinctions.
- `modules/runtime-foundation.md`
  How runtime components are assembled, how project and model scoping work, which environment variables matter, and where provider compatibility logic lives.
- `modules/ingestion-pipeline.md`
  How source files become PageIndex trees, master-tree nodes, related-doc metadata, traces, derived markdown, and optional image-enriched artifacts.
- `modules/master-tree-and-relationships.md`
  The cross-document routing layer, what a master node means, and how `related_docs` maintenance actually behaves.
- `modules/storage-and-index-layout.md`
  Local and Mongo persistence, on-disk layout, source-path indirection, trace storage, experiments artifact directories, and current backend gaps.
- `modules/retrieval-and-answering.md`
  The hybrid staged pipeline, the PageIndex agentic loop, advanced retrieval overlays, answer generation, source references, and image reference handling.
- `modules/tracing-and-observability.md`
  Audit traces, metrics, post-hoc trace analysis, summaries, exports, and how observability is intentionally kept non-blocking.
- `modules/interfaces-and-operations.md`
  CLI commands, main Streamlit app behavior, FastAPI endpoints, experiments Streamlit flows, and the real interface parity gaps.
- `modules/react-frontend.md`
  The React shell, page-level flows, API binding layer, styling direction, and the capabilities the frontend still does not expose.
- `modules/experiments-harness.md`
  Corpus normalization, isolated builds, retrieval profiles, run manifests, suite batches, golden-dataset imports, evaluations, reports, and progress behavior.
- `modules/experiments-ui.md`
  All eight tabs of the experiments Streamlit app, UI components, scoring formulas (deterministic and LLM-judge), aggregate column definitions, session state keys, and styling system.
- `modules/testing.md`
  Pytest coverage map, what each test file protects, recommended commands, and the most important gaps that remain.

## Architectural Rules To Keep In Mind

- The main runtime is project-scoped, not model-scoped.
  `project` changes the visible knowledge base. `model` only changes LLM behavior.
- The main runtime’s canonical persisted artifact family is `pageindex_tree`.
- `hybrid` and `pageindex` are query-time behaviors over the same stored PageIndex artifacts.
- `advanced` retrieval is a runtime policy overlay, not a new artifact family.
- Image-aware ingestion is currently a PDF-only branch and is only surfaced in the main Streamlit app.
- The experiments harness is deliberately isolated under `experiments/artifacts/` and does not mutate `data/indexes/`.
- Some UI copy still implies stronger parity than the code currently provides. The module docs call those mismatches out explicitly.

## Common Commands

Main Streamlit app:

```bash
streamlit run app.py
```

Experiments Streamlit app:

```bash
streamlit run experiments_app.py
```

FastAPI adapter:

```bash
uvicorn api.app:app --reload
```

CLI help:

```bash
python cli.py --help
```

React frontend:

```bash
cd frontend
npm install
npm run dev
```

Tests:

```bash
pytest
```
