# Hybrid Approach Documentation

This folder is the current-state architecture map for the repository as it exists now, including the newer ingestion and UI/runtime changes.

Use these docs in this order:

1. `architecture-map.md`
   The end-to-end system map, artifact taxonomy, and the main execution flows.
2. `modules/runtime-foundation.md`
   Runtime assembly, project/model scoping, environment variables, provider selection, and shared utilities.
3. `modules/ingestion-pipeline.md`
   How source files become PageIndex trees, master-tree nodes, and optional image-enriched markdown.
4. `modules/master-tree-and-relationships.md`
   The routing metadata layer and how `related_docs` is maintained.
5. `modules/storage-and-index-layout.md`
   Local and Mongo storage backends, path layout, and artifact persistence rules.
6. `modules/retrieval-and-answering.md`
   Hybrid retrieval, PageIndex agentic retrieval, advanced retrieval overlays, and answer synthesis.
7. `modules/tracing-and-observability.md`
   Audit traces, metrics, post-hoc analysis, and trace storage.
8. `modules/interfaces-and-operations.md`
   CLI commands, FastAPI endpoints, Streamlit surfaces, and operational run commands.
9. `modules/react-frontend.md`
   The Vite/React frontend, its API bindings, and current feature gaps relative to Streamlit.
10. `modules/experiments-harness.md`
    The isolated experiments pipeline, presets, profiles, reports, and experiments UI.
11. `modules/testing.md`
    Pytest coverage map, useful commands, and known test gaps.

## Key Architectural Rules

- The main app stores knowledge by project, not by model. `project` chooses which index artifacts exist; `model` only changes LLM behavior at runtime.
- The canonical main-app artifact family is `pageindex_tree`.
- `hybrid` and `pageindex` are query-time retrieval modes over those artifacts.
- Advanced retrieval does not create a new build format. It only changes query-time policy.
- The new image-aware PDF ingestion path is currently surfaced only in the legacy Streamlit UI.
- The experiments harness is isolated under `experiments/artifacts/` and does not overwrite `data/indexes/`.

## Common Local Commands

Main Streamlit app:

```bash
streamlit run app.py
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

Experiments UI:

```bash
streamlit run experiments_app.py
```

Tests:

```bash
pytest
```
