# Interfaces And Operations

Primary code:

- `app.py`
- `cli.py`
- `api/app.py`
- `api/contracts.py`
- `api/runtime.py`
- `experiments_app.py`
- `.streamlit/config.toml`

## Purpose

This document maps the repository’s human-facing and programmatic entrypoints:

- main Streamlit app
- CLI
- FastAPI adapter
- experiments Streamlit app

It focuses on what each interface can actually do today, which backend path it exercises, and where the current parity gaps are.

## Run Commands

Main app:

```bash
streamlit run app.py
```

Experiments app:

```bash
streamlit run experiments_app.py
```

API:

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

## Interface Capability Matrix

| Capability | Main Streamlit | CLI | FastAPI | React | Experiments UI |
| --- | --- | --- | --- | --- | --- |
| create/select project | yes | yes | yes | yes | n/a |
| select model | yes | yes | yes | yes | yes |
| ingest PDF/MD/DOCX | yes | yes | yes | yes | corpus upload only |
| image-aware PDF ingest | yes | no | no | no | no |
| query hybrid | yes | yes | yes | yes | yes |
| query pageindex | yes | yes | yes | yes | yes |
| advanced retrieval controls | yes | yes | yes | yes | yes |
| inspect PageIndex tree | yes | indirect | yes | yes | yes for experiment builds |
| inspect traces | yes | yes | yes | yes | run/eval artifacts only |
| post-hoc trace analysis | yes | yes | yes | yes | n/a |
| create corpora/builds/runs/evals | no | no | no | no | yes |

## Main Streamlit App

`app.py` is the richest operational surface for the main runtime.

### What it exposes

- project selection and creation
- model selection and registration
- query controls
- retrieval mode selection
- advanced retrieval toggles
- ingestion form
- image-aware PDF ingestion
- document inspection
- PageIndex outline and raw tree JSON
- trace browsing and analysis
- project deletion

### Why it matters

This app is currently the only interface that surfaces the full image-aware ingestion path end to end.

### Notable UI behavior

- The sidebar controls global runtime context such as project and model.
- Query controls are explicit and can toggle advanced retrieval.
- The docs area can render both a human-readable outline and raw PageIndex tree JSON.
- The app includes custom CSS and a strong visual treatment rather than default Streamlit styling.

### Important nuance

The main Streamlit app can render retrieved image references in answers. That capability is not currently mirrored in React.

## CLI

`cli.py` is the operational shell for local workflows and scripting-friendly inspection.

### Main commands

| Command | Purpose |
| --- | --- |
| `init` | create `.env` interactively |
| `ingest` | ingest one document into a project |
| `query` | run a query |
| `list-docs` | list project documents |
| `show-master-tree` | print the master tree or one node |
| `delete-doc` | delete one document’s artifacts |
| `delete-project` | delete a whole project index |
| `reingest` | replace a document by delete + ingest |
| `traces list` | list recent traces |
| `traces show` | show full trace detail |
| `traces stats` | show aggregated stats |
| `traces export` | export traces |

### Important CLI strengths

- exposes retrieval mode selection
- exposes advanced retrieval toggles
- exposes relationship maintenance mode
- exposes trace access without any UI

### Important CLI gaps

- no `contains_images` flag for image-aware PDF ingestion
- no live ingestion progress stream as rich as the main Streamlit app

## FastAPI Adapter

The API is intentionally thin. It does not reimplement business logic. It calls the same runtime, ingestion, retrieval, and trace layers used elsewhere.

### Endpoint map

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/health` | health check |
| `GET` | `/api/projects` | list projects |
| `POST` | `/api/projects` | create or resolve project runtime |
| `DELETE` | `/api/projects/{project}` | delete project index artifacts |
| `GET` | `/api/models` | list registered model names |
| `POST` | `/api/models` | register a model name |
| `GET` | `/api/runtime` | runtime summary for project/model |
| `GET` | `/api/documents` | list documents |
| `GET` | `/api/documents/{doc_id}` | get one master node |
| `GET` | `/api/documents/{doc_id}/tree` | get one PageIndex tree |
| `POST` | `/api/query` | execute a query |
| `POST` | `/api/ingest` | upload and ingest one document |
| `GET` | `/api/traces` | list traces |
| `GET` | `/api/traces/stats` | trace stats |
| `GET` | `/api/traces/{trace_id}` | get one full trace |
| `POST` | `/api/traces/{trace_id}/analysis` | run post-hoc trace analysis |

### Query request shape

The query API supports:

- project
- optional model
- user query
- conversation context
- max docs
- reasoning effort
- retrieval mode
- advanced retrieval bundle

That makes the API broadly capable for query-time experimentation.

### Ingestion request shape

The ingestion API currently supports:

- file
- project
- optional model
- `doc_id`
- `doc_title`
- `doc_type`
- `top_sections_target`
- `relationship_mode`

### Important API gap

The ingestion API does not currently expose `contains_images`, which means the image-aware PDF branch cannot be triggered through FastAPI today.

## React Frontend As An Operational Surface

The React frontend is covered in detail in its own module doc, but operationally it is a thin client over the FastAPI adapter.

That means every API-level omission becomes a React omission.

Current important examples:

- React cannot trigger image-aware PDF ingestion because the API cannot.
- React does not have answer streaming because the API flow it uses is still request/response.

## Experiments Streamlit App

`experiments_app.py` is the operational surface for the experiments harness.

### Main tabs and jobs

- overview
- corpora
- builds
- compare
- runs
- evaluation
- reports

### Important split flows

Some of the most important experiments flows span multiple tabs.

Golden dataset workflow:

1. import the suite in `Evaluation`
2. run suite cases from `Compare`
3. inspect resulting suite-linked runs in `Compare` or `Runs`
4. evaluate those runs back in `Evaluation`

That flow is workable, but it is not obvious unless you already know how the experiments app is partitioned.

### What it can do

- register corpora from uploaded files
- build artifact variants
- create comparison runs across builds and retrieval profiles
- execute a single-question comparison
- execute a golden-dataset batch where each case becomes its own run
- import and persist evaluation suites from CSV or JSON
- evaluate selected runs deterministically and optionally with an LLM judge
- export reports

### Important newer behavior

The experiments UI now shows progress for:

- comparison runs
- golden-dataset suite batches
- evaluation snapshots

This matters because long-running suite and evaluation actions are no longer opaque spinner-only actions.

The newer progress model is especially useful because:

- suite batches execute one case at a time
- evaluation time scales with run entries and judge calls, not just with top-level run count

## Flow Guide

### Flow: operational document QA through main app

1. choose project
2. choose model
3. ingest documents
4. inspect document trees if needed
5. run queries
6. inspect traces when retrieval quality looks wrong

### Flow: script or terminal-first use

1. use `python cli.py ingest ...`
2. use `python cli.py query ...`
3. inspect `traces` or `show-master-tree` as needed

### Flow: API-backed product surface

1. client fetches runtime summary
2. client uploads or queries via FastAPI
3. client optionally browses document trees and traces

### Flow: experiments-driven evaluation

1. register corpus
2. run build presets
3. create comparison entries
4. choose single question or golden-dataset batch
5. run evaluation snapshot
6. inspect reports

## Operational Mismatches To Know

- Main Streamlit supports image-aware ingestion; CLI, API, and React do not.
- React Inspect says project deletion removes traces, but `delete_project()` only removes project index artifacts.
- The experiments app is not just a UI skin over the main app. It uses a different persistence root and a different orchestration model.

## What Is Possible In This Module

Across all interfaces, the repository currently supports:

- local operational QA against project-scoped indexes
- document ingestion and reingestion
- inspection of PageIndex trees
- inspection and export of traces
- controlled experiment execution and evaluation

The exact surface area depends strongly on which interface you use.
