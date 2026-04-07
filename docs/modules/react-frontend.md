# React Frontend

Primary code:

- `frontend/src/App.tsx`
- `frontend/src/main.tsx`
- `frontend/src/config.ts`
- `frontend/src/api/client.ts`
- `frontend/src/types.ts`
- `frontend/src/pages/AskPage.tsx`
- `frontend/src/pages/IngestPage.tsx`
- `frontend/src/pages/InspectPage.tsx`
- `frontend/src/components/`
- `frontend/src/styles.css`

## Purpose

The React app is a thin presentation layer over the Python backend.

It does not reimplement ingestion or retrieval logic. It only talks to the FastAPI adapter and renders the returned state.

## Stack

- React 18
- TypeScript
- Vite
- React Router

Configuration:

- `VITE_API_BASE_URL`
- default API base: `http://localhost:8000`

## App Structure

The app shell is organized around three workspaces:

- `Ask`
- `Ingest`
- `Inspect`

The shell also holds:

- project selection
- model selection
- runtime summary

## API Binding Layer

`frontend/src/api/client.ts` wraps the FastAPI endpoints and centralizes:

- fetch calls
- JSON and multipart request formatting
- runtime query-parameter handling
- error parsing

Primary client functions:

- `fetchProjects()`
- `deleteProject()`
- `fetchModels()`
- `fetchRuntime()`
- `runQuery()`
- `runIngestion()`
- `fetchTraces()`
- `fetchDocumentTree()`
- `fetchTrace()`
- `analyzeTrace()`

## Ask Workspace

`AskPage.tsx` owns:

- chat turns
- next-request retrieval settings
- advanced retrieval controls
- retrieval mode selector
- diagnostics panel via `InspectorPanel`

The request payload is snapshotted per send, which keeps control changes from mutating an in-flight request.

### Ask controls surfaced today

- retrieval mode
- max docs
- reasoning effort
- advanced retrieval enabled
- planner toggle
- adaptive width toggle
- node expansion toggle
- advanced docs cap
- advanced nodes cap

## Ingest Workspace

`IngestPage.tsx` owns:

- file upload
- doc ID
- doc title
- doc type
- top-sections target
- relationship mode

It also explains the ingestion stages in the sidebar so the UI reflects what the backend already does.

### Important current gap

The React ingest form does not expose `contains_images`, because the FastAPI ingestion contract does not expose it either.

That means the React frontend cannot yet trigger the new image-enriched PDF ingestion path.

## Inspect Workspace

`InspectPage.tsx` supports two inspect modes:

- document browsing
- trace browsing

It can:

- search visible docs or traces
- fetch a per-document tree
- fetch a full trace
- run post-hoc trace analysis
- delete the active project with explicit confirmation

The page uses `useDeferredValue()` to keep search interaction responsive while filtering lists.

## Styling And UI Direction

`frontend/src/styles.css` establishes a custom light theme with:

- warm paper-like backgrounds
- strong blue-green accents
- rounded panels
- shadowed cards
- a two-column shell layout

Typography favors `"Avenir Next", "Segoe UI", sans-serif`, matching the more intentional design direction already present in the Streamlit surfaces.

## Current Feature Gaps Relative To Streamlit

- no answer streaming
- no ingestion progress stream
- no image-aware ingestion toggle
- no rendering of retrieved image references

There is also a type-surface lag:

- `QueryResult` in Python now exposes `image_refs`
- `frontend/src/types.ts` does not currently model that field

So even if the backend returned image references, the React UI is not yet built to display them.

## Running The Frontend

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

Production build:

```bash
cd frontend
npm run build
```

## Design Intent

The frontend README states the core intent clearly:

- keep backend behavior unchanged
- migrate the presentation layer away from Streamlit
- remain thin rather than duplicating Python-side orchestration

That means feature parity depends on API parity first, then UI implementation.
