# Interfaces And Operations

Primary code:

- `cli.py`
- `app.py`
- `api/app.py`
- `api/contracts.py`
- `api/runtime.py`
- `experiments_app.py`
- `.streamlit/config.toml`

## Purpose

This document maps the repository's user-facing entrypoints:

- CLI
- main Streamlit app
- FastAPI adapter
- experiments Streamlit app

## Local Run Commands

Install core dependencies:

```bash
pip install -r requirements.txt
```

Run the main Streamlit app:

```bash
streamlit run app.py
```

Run the FastAPI adapter:

```bash
uvicorn api.app:app --reload
```

Run the CLI:

```bash
python cli.py --help
```

Run the experiments UI:

```bash
pip install -r requirements-experiments.txt
streamlit run experiments_app.py
```

## CLI

`cli.py` is the main operational interface for local ingestion, querying, inspection, and trace access.

### Command groups

| Command | Purpose |
| --- | --- |
| `init` | Write a `.env` file interactively |
| `ingest` | Ingest one document into the active project |
| `query` | Run a query with optional follow-up turns |
| `list-docs` | List documents in the active project |
| `show-master-tree` | Print the whole master tree or one node |
| `delete-doc` | Delete one document's artifacts |
| `delete-project` | Delete an entire project namespace |
| `reingest` | Replace a document by deleting then ingesting again |
| `traces list` | List recent traces |
| `traces show` | Show a full trace |
| `traces stats` | Summarize trace metrics |
| `traces export` | Export traces as JSON or CSV |

### Common examples

Ingest:

```bash
python cli.py ingest \
  --file ./docs/spec.pdf \
  --doc-id auth_spec \
  --title "Auth Spec" \
  --doc-type technical_spec \
  --project default
```

Query:

```bash
python cli.py query "How does token refresh work?" \
  --project default \
  --retrieval-mode hybrid \
  --advanced \
  --max-docs 3 \
  --reasoning-effort medium
```

Inspect traces:

```bash
python cli.py traces list --project default
python cli.py traces stats --project default
```

### Important CLI limitations

- The CLI supports `relationship_mode`, advanced retrieval flags, and retrieval mode selection.
- The CLI does not currently expose the new `contains_images` ingestion flag.

## FastAPI Adapter

The API is a thin adapter over the same runtime used by Streamlit and the CLI.

### Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/health` | Health check |
| `GET` | `/api/projects` | List projects |
| `POST` | `/api/projects` | Create or resolve project runtime |
| `DELETE` | `/api/projects/{project}` | Delete a project |
| `GET` | `/api/models` | List model names |
| `POST` | `/api/models` | Register a model name |
| `GET` | `/api/runtime` | Return runtime summary for project/model |
| `GET` | `/api/documents` | List document records |
| `GET` | `/api/documents/{doc_id}` | Get one master-node document record |
| `GET` | `/api/documents/{doc_id}/tree` | Load one per-document tree |
| `POST` | `/api/query` | Execute a query |
| `POST` | `/api/ingest` | Upload and ingest one document |
| `GET` | `/api/traces` | List traces |
| `GET` | `/api/traces/stats` | Trace stats |
| `GET` | `/api/traces/{trace_id}` | Full trace detail |
| `POST` | `/api/traces/{trace_id}/analysis` | Run post-hoc analysis |

### Query request shape

`POST /api/query` accepts JSON like:

```json
{
  "project": "default",
  "model": "gpt-4o",
  "user_query": "How does token refresh work?",
  "conversation_context": [
    { "role": "user", "content": "Earlier question" }
  ],
  "max_docs": 3,
  "reasoning_effort": "medium",
  "retrieval_mode": "hybrid",
  "advanced_retrieval": {
    "enabled": true,
    "enable_planning": true,
    "enable_adaptive_width": true,
    "enable_node_expansion": true,
    "max_docs_cap": 6,
    "max_nodes_cap": 6
  }
}
```

### Ingestion request shape

`POST /api/ingest` is multipart form data:

- `file`
- `project`
- `model` optional
- `doc_id`
- `doc_title`
- `doc_type`
- `top_sections_target` optional
- `relationship_mode`

Current limitation:

- there is no `contains_images` form field yet

### Example API calls

Query:

```bash
curl -X POST http://localhost:8000/api/query \
  -H "Content-Type: application/json" \
  -d '{
    "project": "default",
    "user_query": "Summarize the onboarding workflow",
    "max_docs": 3,
    "retrieval_mode": "hybrid",
    "advanced_retrieval": {
      "enabled": false,
      "enable_planning": true,
      "enable_adaptive_width": true,
      "enable_node_expansion": true,
      "max_docs_cap": 6,
      "max_nodes_cap": 6
    }
  }'
```

Ingest:

```bash
curl -X POST http://localhost:8000/api/ingest \
  -F project=default \
  -F doc_id=auth_spec \
  -F doc_title="Auth Spec" \
  -F doc_type=technical_spec \
  -F relationship_mode=basic \
  -F file=@./auth_spec.pdf
```

## Main Streamlit App

`app.py` is the original full-stack UI over the runtime.

### Main tabs

- `Upload`
- `Ask`
- `Docs`
- `Map`
- `Audit`

### Key behaviors

- Project and model selectors are part of the runtime shell.
- Ingestion runs through a background-thread async bridge with progress rendering.
- Querying supports streaming answer tokens in the UI.
- Retrieved image references can be rendered inline when present.

### Streamlit-only features today

- PDF ingestion checkbox: `Document contains meaningful images`
- rendering stored image files from `QueryResult.image_refs`

### Main session state keys

The app stores important runtime/session objects in Streamlit state, including:

- `chat_history`
- `latest_query`
- `latest_ingestion`
- `selected_doc_id`
- `active_index_key`
- `retrieval_reasoning_effort`
- `project_name`

### UI and styling notes

- The app uses custom CSS injected directly from Python.
- The `.streamlit/config.toml` file forces `fileWatcherType = "poll"` to avoid noisy lazy-import watcher issues from transformer-related modules.

## Experiments Streamlit App

`experiments_app.py` is a separate UI for the experiments harness.

It is intentionally isolated from the main app and focuses on:

- corpus registration
- build creation
- comparison runs
- reports
- aggregate summaries

It also carries its own custom visual theme and should be treated as a separate lab surface, not as a runtime control panel for the main app.

## Operational Mismatches To Remember

- Streamlit can drive image-aware PDF ingestion; CLI, FastAPI, and React currently cannot.
- Streamlit streams answer tokens; React currently waits for full responses.
- Streamlit renders retrieved image references; React currently does not expose them.
- The API is thin and current-state accurate, but not every backend capability has been surfaced through it yet.
- The Inspect UI copy suggests project deletion also removes traces, but the current backend `delete_project()` path removes index artifacts only.
