# Runtime Foundation

Primary code:

- `index_registry.py`
- `utils.py`
- `model_registry.py`
- `api/runtime.py`
- `storage/factory.py`
- `master_tree/factory.py`
- `traces/factory.py`
- `.env.example`
- `config.yaml`

## Purpose

The runtime foundation is the assembly layer for the whole application. It decides:

- which project namespace is active
- which model/provider configuration is active
- which storage backend objects to instantiate
- which shared LLM helpers every subsystem should use
- how legacy directories, provider quirks, and runtime defaults are normalized

If ingestion and retrieval are the data plane, this module is the control plane that decides which data plane instance you are talking to.

## Core Mental Model

There are two independent selectors in the runtime:

- `project`
- `model`

They are intentionally not symmetrical.

| Selector | What it changes | What it does not change |
| --- | --- | --- |
| `project` | active index directory, visible documents, project-scoped traces | model/provider behavior |
| `model` | LLM calls, reasoning effort support, token accounting behavior | active document set |

This is the single most important runtime rule in the repo.

## Runtime Assembly Flow

The canonical builder is `index_registry.build_runtime_components()`.

It performs these steps:

1. Resolve the active project key and runtime paths with `resolve_index_context()`.
2. Ensure the project index directory and uploads directory exist.
3. Persist `index_meta.json` for the active project.
4. Create the master-tree store.
5. Create the document store.
6. Create the trace store.
7. Wrap the trace store in `TraceService`.
8. Return a `RuntimeComponents` bundle.

The resulting bundle is what the main Streamlit app, CLI, and FastAPI adapter all consume.

## `IndexContext`

`IndexContext` is the resolved filesystem identity of one active project.

It contains:

- provider
- model
- project
- index key
- base data dir
- index dir
- master tree path
- metadata path
- uploads dir

The important subtlety is that `index_dir` is derived from `project`, not from `model`.

## `RuntimeComponents`

`RuntimeComponents` bundles the objects the rest of the application needs:

- `index_context`
- `master_tree_store`
- `storage`
- `trace_service`

This object is intentionally framework-neutral. The CLI, Streamlit UI, and API adapter all build the same bundle rather than each constructing their own stores independently.

## Project Resolution

### Canonical naming

`sanitize_index_key_part()` collapses free-form names into filesystem-safe keys. That same normalization is used across:

- project directories
- corpus IDs
- suite IDs
- generated document IDs in some flows

### Alias handling

The project resolver does more than simple slugification. It also handles legacy alias directories.

Example problem:

- an older run may have written `my__project`
- a newer run may resolve the same logical name as `my_project`

`resolve_project_key()` groups directory names that normalize to the same key, then prefers the alias directory that looks most populated and most explicitly associated with the requested project.

This prevents duplicate logical projects from appearing in the UI when historical directory naming drift exists on disk.

### Default project

When nothing is specified, the runtime falls back to:

```text
default
```

That default is stable across the CLI, Streamlit app, API adapter, and React frontend.

## Model Registry

`model_registry.py` stores user-visible model or deployment names in:

```text
data/model_registry.json
```

This registry is intentionally simple.

It does:

- persist names
- populate dropdowns
- ensure a requested model exists in the registry

It does not:

- deploy a model
- validate that the remote provider really serves that model
- create provider credentials

The model registry is a UI/runtime convenience layer, not a provisioning system.

## Provider Detection And Client Setup

`utils.py` is the shared LLM compatibility layer used by ingestion, retrieval, experiments, and traces.

It owns:

- provider detection
- default model resolution
- sync and async client creation
- Azure/OpenAI-compatible URL normalization
- request fallbacks when a parameter is unsupported
- reasoning-effort normalization
- token usage tracking
- PageIndex compatibility patching

### Provider detection

Provider resolution is effectively:

1. use `LLM_PROVIDER` when present
2. otherwise infer Azure if Azure credentials/endpoints are present
3. otherwise fall back to the OpenAI-compatible path

### Default model resolution

The default model is resolved in priority order from environment state, not from `config.yaml`.

Typical precedence:

1. `LLM_MODEL`
2. Azure deployment name
3. `OPENAI_MODEL`
4. built-in fallback

### Compatibility fallbacks

The helper layer intentionally strips unsupported parameters and retries when needed.

Examples:

- remove `temperature` for models that do not support explicit temperature
- remove `reasoning_effort` when the provider rejects it
- estimate usage when the response omits token counts

This matters because the same code path may be used with GPT, Azure-hosted deployments, or other OpenAI-compatible endpoints.

## Environment Variables That Matter

### Core provider and model settings

| Variable | Purpose |
| --- | --- |
| `LLM_PROVIDER` | explicit provider override |
| `LLM_MODEL` | preferred default model/deployment |
| `OPENAI_API_KEY` | OpenAI key |
| `OPENAI_MODEL` | OpenAI default model |
| `OPENAI_BASE_URL` | custom OpenAI-compatible endpoint |
| `AZURE_OPENAI_API_KEY` | Azure key |
| `AZURE_OPENAI_ENDPOINT` | Azure endpoint |
| `AZURE_OPENAI_BASE_URL` | explicit Azure v1 base URL |
| `AZURE_OPENAI_CHAT_DEPLOYMENT` | Azure deployment name |
| `CHATGPT_API_KEY` | compatibility backfill for PageIndex |

### Query-time behavior

| Variable | Purpose |
| --- | --- |
| `RETRIEVAL_MODE` | default query mode |
| `NAVIGATOR_VERIFICATION` | enable verifier pass |
| `ADVANCED_RETRIEVAL` | master switch for advanced retrieval |
| `QUERY_PLANNING` | enable planner when advanced retrieval is on |
| `ADAPTIVE_RETRIEVAL_WIDTH` | enable planner-driven width changes |
| `NODE_EXPANSION` | enable structural neighbor expansion |
| `MAX_DOCS_CAP` | hard cap for advanced doc routing |
| `MAX_NODES_CAP` | hard cap for advanced node selection |
| `DOMAIN_NAME` | domain-specific answer wording context |

### Ingestion behavior

| Variable | Purpose |
| --- | --- |
| `MASTER_TOP_SECTIONS_TARGET` | target number of stored top sections in master nodes |
| `RELATED_DOCS_MODE` | default relationship maintenance mode |

### Persistence

| Variable | Purpose |
| --- | --- |
| `STORAGE_BACKEND` | `local` or `mongodb` |
| `MONGODB_URI` | Mongo connection string |
| `MONGODB_DATABASE` | Mongo database name |

## Factories And Backend Selection

The runtime does not instantiate stores directly in most interfaces. It goes through factories:

- `storage.factory.create_document_store()`
- `master_tree.factory.create_master_tree_store()`
- `traces.factory.create_trace_store()`

This allows the runtime to switch between local JSON/file storage and Mongo-backed implementations for the core stores.

### Important current nuance

The image-aware PDF ingestion path uses image helpers defined only on the local `DocumentStore`. That means the storage abstraction is not fully complete for the newest image branch even though the core tree/source/master-tree path is backend-switchable.

## `config.yaml` Is Not Live Runtime Configuration

This is easy to misunderstand.

`config.yaml` is documentation/reference material. Production code does not load it as the source of truth for runtime settings. The live sources of truth are:

- explicit function arguments
- environment variables
- persisted runtime metadata files such as `index_meta.json`

## Flow Guide

### Flow: boot a runtime

1. Choose or receive a `project`.
2. Choose or infer a `model`.
3. Resolve the canonical project key.
4. Build stores for that project.
5. Return `RuntimeComponents`.

This is what every interface does, even if the UI hides some of those steps.

### Flow: switch projects

1. Resolve the new project key.
2. Rebuild runtime components against `data/indexes/{project}/`.
3. The document universe changes.
4. The model can remain the same.

### Flow: switch models

1. Keep the same project key.
2. Rebuild runtime components with a different `model`.
3. The visible documents stay the same.
4. Only LLM behavior changes.

### Flow: delete a project

1. Resolve project aliases.
2. Remove the project’s index namespace.
3. Do not recreate directories while deleting.

Important nuance:

- local project deletion removes `data/indexes/{project}/`
- it does not remove `data/traces/{project}/`

That is a real code-level mismatch with some interface copy.

## What Is Possible In This Module

This layer supports:

- project-scoped knowledge isolation
- model-agnostic knowledge visibility
- provider selection at runtime
- local or Mongo-backed core storage
- transparent alias cleanup for historical project directories

This layer does not support:

- model deployment or provisioning
- remote provider validation during model registration
- full backend parity for the newest image-ingestion helpers

## Current Constraints And Gotchas

- `delete_project()` does not delete traces, even though some UI text implies that it does.
- Image-aware ingestion currently assumes local image persistence helpers.
- `config.yaml` is descriptive, not authoritative.
- If you are debugging why “the wrong docs” appear after a model switch, the bug is almost never model-related. It is almost always project resolution or stale artifacts.
