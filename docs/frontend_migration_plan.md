# Frontend Migration Plan

This document defines the migration from the current Streamlit frontend to a React-based product UI without changing the core Hybrid Approach ingestion, retrieval, tracing, or storage architecture.

The goal is not a backend rewrite. The goal is to replace the presentation layer and interaction model.

## 1. Constraints

These constraints are non-negotiable:

- the ingestion pipeline in `ingestion/` remains the system of record
- the retrieval pipeline in `retrieval/` remains the system of record
- project-scoped storage in `data/indexes/{project}` remains unchanged
- trace generation and post-hoc analysis in `traces/` remain unchanged
- PageIndex and Hybrid Approach behavior must not be altered by the frontend migration

The frontend may improve:

- information architecture
- state management
- user flow
- visibility of settings
- assistant ergonomics
- diagnostics presentation

## 2. Product Information Architecture

The new product surface should have three first-class workspaces:

1. `Ask`
2. `Ingest`
3. `Inspect`

This replaces the current equal-weight tab model of `Upload / Ask / Docs / Map / Audit`.

### 2.1 Ask

Purpose:

- conversational assistant experience
- query settings scoped to the active conversation
- answer streaming
- optional retrieval inspector

The Ask workspace should be the default landing surface.

### 2.2 Ingest

Purpose:

- add documents
- configure ingestion-only settings
- monitor ingestion status
- review the resulting master-node and tree artifacts

Ingestion controls should never be mixed with query controls.

### 2.3 Inspect

Purpose:

- browse indexed documents
- inspect document trees
- inspect audit traces
- run post-hoc trace analysis
- view system/storage details

This is the engineering and observability workspace.

## 3. Frontend Component Model

The React app should be assembled from a small number of stable layout and domain components.

### 3.1 App Shell

Responsibilities:

- global layout
- top navigation
- project/model switchers
- route-level shell
- workspace-aware side panel

Behavior:

- top-level navigation changes the workspace
- shell keeps global app state such as current project and current model
- per-workspace controls are injected into the contextual sidebar instead of living in one permanent global drawer

### 3.2 Context Sidebar

Responsibilities:

- show only controls relevant to the current workspace
- make scope explicit
- explain what a setting affects

Behavior by workspace:

- `Ask`: query controls only
- `Ingest`: ingestion controls only
- `Inspect`: filters and drill-down controls only

### 3.3 Ask Workspace

Subcomponents:

- `ConversationHeader`
- `QuerySettingsPanel`
- `ChatThread`
- `Composer`
- `InspectorRail`

Behavior:

- user sends a message
- the UI snapshots the current query settings into the request payload
- settings lock while the query is in flight
- answer streams into the thread
- the answer becomes a conversation turn
- diagnostics open in a side rail, not below the composer

### 3.4 Ingest Workspace

Subcomponents:

- `UploadPanel`
- `IngestionSettingsPanel`
- `IngestionProgressPanel`
- `IngestionResultPanel`

Behavior:

- document and ingestion metadata are entered together
- advanced ingestion settings are visible only here
- upload triggers ingestion through the API
- progress events render in a dedicated status panel
- resulting trace and master-node snapshot are shown in a dedicated results region

### 3.5 Inspect Workspace

Subcomponents:

- `DocumentBrowser`
- `TreeOutline`
- `TraceList`
- `TraceDetail`
- `PostHocAnalysisPanel`

Behavior:

- the user can browse documents and traces without affecting conversation state
- raw artifacts remain accessible, but are visually secondary to structured views

## 4. Backend Adapter Plan

The Python core should gain a thin API adapter, not a second orchestration system.

### 4.1 Existing Seams We Will Use

- `index_registry.build_runtime_components(...)`
- `ingestion.ingest.ingest_document_with_trace(...)`
- `retrieval.query_engine.query(...)`
- `traces.service.TraceService`

### 4.2 API Responsibilities

The API layer will:

- resolve project/model runtime state
- expose project and model management
- expose document and tree inspection
- execute ingestion
- execute querying
- expose traces and post-hoc analysis

The API layer will not:

- rewrite ingestion logic
- rewrite retrieval logic
- maintain a parallel data model
- interpret answers differently than the current app

## 5. Planned API Surface

Initial routes:

- `GET /api/health`
- `GET /api/projects`
- `POST /api/projects`
- `DELETE /api/projects/{project}`
- `GET /api/models`
- `POST /api/models`
- `GET /api/runtime`
- `GET /api/documents`
- `GET /api/documents/{doc_id}`
- `GET /api/documents/{doc_id}/tree`
- `POST /api/ingest`
- `POST /api/query`
- `GET /api/traces`
- `GET /api/traces/stats`
- `GET /api/traces/{trace_id}`
- `POST /api/traces/{trace_id}/analysis`

Streaming routes are intentionally deferred to the next pass.

Phase 1 of the migration should prove:

- route parity
- query parity
- ingestion parity
- inspect parity

Then we add:

- answer streaming via SSE
- ingestion progress via SSE
- cancel/retry actions

## 6. State Model

The React app should separate state into four layers.

### 6.1 Global Runtime State

- current project
- current model
- available projects
- available models
- API health

### 6.2 Ask Workspace State

- conversation turns
- current draft
- snapped query settings
- request in-flight state
- latest answer diagnostics
- inspector open/closed state

### 6.3 Ingest Workspace State

- selected file
- ingestion metadata
- advanced ingestion config
- upload status
- current progress events
- last ingestion result

### 6.4 Inspect Workspace State

- selected doc
- selected trace
- trace search/filter settings
- post-hoc analysis state

## 7. UX Rules

These rules are the core of the redesign.

### 7.1 Settings Must Be Scoped

- ingestion settings must never appear in Ask
- query settings must never appear in Ingest
- internal diagnostics must never dominate the assistant surface

### 7.2 Requests Must Snapshot Configuration

When the user sends a query:

- retrieval mode
- max docs
- reasoning effort
- advanced retrieval config

must be frozen into the request payload.

The UI can be edited later, but the in-flight request must not be invalidated.

### 7.3 Diagnostics Must Be Secondary

The assistant view should prioritize:

- user question
- answer
- citations
- follow-up

Detailed routing and chunk traces belong in the inspector rail.

### 7.4 Global Destructive Actions Must Be Explicit

Project deletion and similar actions should live in clearly separated settings surfaces with confirmation, not beside daily-use controls.

## 8. Rollout Plan

### Phase A: Foundation

- add FastAPI adapter
- add React app scaffold
- keep Streamlit as the current primary UI

### Phase B: Feature Parity

- Ask workspace wired to live query endpoint
- Ingest workspace wired to live ingest endpoint
- Inspect workspace wired to live trace/doc endpoints

### Phase C: Streaming And Polish

- answer streaming
- ingestion progress streaming
- better empty states
- keyboard shortcuts
- responsive layout polish

### Phase D: Cutover

- React app becomes primary frontend
- Streamlit remains as an internal/debug console until confidence is high

## 9. What “10/10” Means Here

For this project, a high-quality frontend means:

- scoped settings
- durable in-flight behavior
- clear workspace separation
- modern assistant ergonomics
- readable typography and contrast
- diagnostics that help instead of interrupting
- no hidden coupling between unrelated controls

It does not mean changing the backend retrieval architecture.

## 10. Initial Implementation Deliverables

The first implementation pass should add:

- this migration plan
- a Python API scaffold
- a React app scaffold
- typed contracts between them
- the new `Ask / Ingest / Inspect` shell

That gives the repo a serious migration foundation without destabilizing the core system.
