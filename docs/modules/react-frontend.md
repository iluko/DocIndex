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

The React frontend is a thin product-style shell over the FastAPI adapter.

It does not implement ingestion, retrieval, or tracing logic itself. Its job is to:

- present the main runtime through a more structured workspace UI
- bind frontend controls to API request payloads
- keep chat, ingestion, and inspection concerns separated

Because it is thin, the frontend’s capabilities are tightly bounded by the FastAPI contract.

## Stack

- React
- TypeScript
- Vite
- React Router

The API base URL is configured through `VITE_API_BASE_URL`, with a local default of `http://localhost:8000`.

## Shell-Level Structure

`App.tsx` owns the global shell.

It is responsible for:

- loading projects
- loading models
- loading runtime summaries
- loading trace summaries
- switching between workspaces
- passing global project/model context into those workspaces

The shell treats:

- project as the active knowledge base
- model as the active runtime model

That mirrors the same project-vs-model distinction used everywhere else in the repo.

## Workspace Model

The frontend is organized around three workspaces.

### Ask

Primary conversation workspace.

### Ingest

Document upload and ingestion setup workspace.

### Inspect

Tree, trace, and project-maintenance workspace.

This split is architectural, not just visual. The frontend deliberately keeps:

- conversational work
- ingestion work
- diagnostic work

in separate flows so the user is not forced to mix debugging UI with the chat surface.

## API Binding Layer

`frontend/src/api/client.ts` centralizes:

- fetch requests
- JSON vs multipart handling
- query-parameter construction for project/model
- error parsing

Main client functions:

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

This file is the frontend’s contract boundary. If the backend adds or omits a feature here, the UI inherits that behavior.

## Ask Workspace

`AskPage.tsx` is the main question-answering surface.

### What it owns

- conversation turns
- draft state
- pending/error state
- last request and last response
- retrieval mode selector
- max docs slider
- reasoning-effort selector
- advanced retrieval toggles and caps

### Important behavior

The request payload is snapshotted when the user sends a message.

That means:

- changing controls during an in-flight request does not mutate the active request
- the visible “last request” semantics stay deterministic

This is a subtle but good design choice because retrieval controls are supposed to be per-message, not globally mutating hidden state.

### What the Ask page can do

- choose `hybrid` or `pageindex`
- set base doc width
- set reasoning effort
- enable advanced retrieval
- toggle planning, adaptive width, and node expansion
- set advanced doc and node caps

### What it cannot yet do

- stream answer tokens
- render backend `image_refs`

The first is a transport/UI limitation. The second is both a type-surface and rendering limitation.

## Ingest Workspace

`IngestPage.tsx` owns document upload and ingestion settings.

### What it exposes

- file upload
- `doc_id`
- title
- `doc_type`
- top-sections target
- relationship maintenance mode

### What it explains in the UI

The sidebar explicitly describes the current backend ingestion stages:

1. preprocess source
2. build per-document tree
3. generate master node
4. reconcile relationships

That is useful because the React UI is intentionally presenting the existing backend rather than inventing a different ingestion model.

### Important current gap

The React ingest form does not expose `contains_images`.

That is because:

1. the FastAPI ingestion route does not expose it
2. the frontend request type does not model it

So even though the backend has an image-aware PDF branch, the React UI cannot trigger it today.

## Inspect Workspace

`InspectPage.tsx` separates browsing into two modes:

- documents
- traces

### Document mode

Can:

- filter visible docs
- select a doc
- fetch the raw PageIndex tree JSON

### Trace mode

Can:

- filter recent trace summaries
- select a trace
- fetch full trace detail
- run post-hoc analysis

### Important UX detail

The page uses `useDeferredValue()` for search filtering so typing stays responsive while large lists are being filtered.

### Project deletion

The Inspect workspace also owns the delete-project action with explicit typed confirmation.

Important mismatch:

- the UI copy says deletion removes indexed artifacts and traces
- the backend `delete_project()` path currently removes project index artifacts only, not trace files

That mismatch should be treated as a documentation and product bug, not ignored.

## Type Surface

`frontend/src/types.ts` mirrors the backend contracts used by the React app.

Important current-state nuance:

- `QueryResponse` does not currently model backend `image_refs`
- `IngestionRequest` does not model `contains_images`

Those missing fields are one reason frontend capability lags the Python backend.

## Styling And UI Direction

The frontend is not using a generic component-library look by default.

The styling emphasizes:

- a structured workspace shell
- strong sectioning
- intentional cards and panels
- a custom visual identity rather than stock browser defaults

That design direction matches the repo’s stated preference for more deliberate frontend surfaces.

## Flow Guide

### Flow: ask a question

1. choose project and model in the shell
2. set per-message retrieval controls in Ask
3. send query
4. inspect the answer and last-response diagnostics
5. switch to Inspect if deeper trace or tree debugging is needed

### Flow: ingest a document

1. choose project and model in the shell
2. go to Ingest
3. upload file and set metadata
4. run ingestion
5. refresh runtime and inspect the new doc in Inspect mode

### Flow: inspect a trace

1. go to Inspect
2. switch to Traces mode
3. filter and select trace
4. read trace detail
5. optionally run post-hoc analysis

## What Is Possible In This Module

The React frontend currently supports:

- project and model switching
- hybrid and PageIndex querying
- advanced retrieval controls
- document ingestion for the standard API contract
- tree browsing
- trace browsing and analysis
- project deletion

## Current Constraints

- no image-aware ingestion toggle
- no answer streaming
- no ingestion progress stream
- no inline image rendering from retrieved `IMAGE_REF`
- some UI copy currently overstates delete-project behavior relative to the backend
