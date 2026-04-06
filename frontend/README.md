# Hybrid UI Frontend

This folder contains the React frontend migration for Hybrid Approach.

It is intentionally a thin presentation layer over the existing Python backend:

- ingestion logic remains in `ingestion/`
- retrieval logic remains in `retrieval/`
- runtime assembly remains in `index_registry.py`
- traces remain in `traces/`

## Current Shape

The UI is organized into three workspaces:

1. `Ask`
   - conversational querying
   - per-message retrieval settings
   - secondary inspector rail for diagnostics

2. `Ingest`
   - file upload
   - ingestion-only settings
   - ingestion result review

3. `Inspect`
   - indexed document browsing
   - document tree inspection
   - trace drill-down
   - post-hoc trace analysis

## API Contract

The frontend talks to the FastAPI adapter in `api/` and expects it at:

- `http://localhost:8000` by default
- override via `VITE_API_BASE_URL`

## Running

Backend:

```bash
uvicorn api.app:app --reload
```

Frontend:

```bash
cd frontend
npm install
npm run dev
```

## Notes

- This migration should not change the system's retrieval or ingestion behavior.
- The goal is to replace the Streamlit interaction model, not the backend architecture.
- Streaming answers and ingestion progress are intentionally deferred to a later pass.
