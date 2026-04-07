# Runtime Foundation

Primary code:

- `index_registry.py`
- `utils.py`
- `model_registry.py`
- `api/runtime.py`
- `.env.example`
- `config.yaml`

## Purpose

The runtime foundation decides which index is active, which model/provider configuration is active, which storage backend to use, and how shared LLM helpers behave.

This layer is important because most of the application behavior is environment-driven rather than config-file-driven.

## Runtime Assembly

`index_registry.build_runtime_components()` is the canonical runtime builder for the main app, CLI, and FastAPI adapter.

It resolves:

- the logical project name
- the active model
- the provider
- the on-disk index directory
- the master tree store
- the document store
- the trace store and `TraceService`

The resulting bundle is a `RuntimeComponents` object:

- `index_context`
- `master_tree_store`
- `storage`
- `trace_service`

## Project Scoping

The central rule is:

- project changes the document universe
- model does not

`index_registry.resolve_index_context()` always maps the active runtime to:

```text
data/indexes/{project}/
```

The model is recorded into `index_meta.json`, but it is not part of the directory name.

### Alias resolution

`index_registry.py` also handles legacy or inconsistent project directory names by:

- normalizing names with `sanitize_index_key_part()`
- grouping alias directories that normalize to the same key
- preferring the alias directory that appears most populated

That logic prevents duplicate logical projects in the UI when old directories exist.

## Model Registry

`model_registry.py` stores a lightweight list of user-visible model or deployment names in:

```text
data/model_registry.json
```

The registry is used by the FastAPI adapter and UIs to populate model selectors.

It does not provision models. It only stores names.

## Provider Detection And LLM Config

`utils.py` is the shared LLM compatibility layer.

It handles:

- provider detection
- default model resolution
- sync and async OpenAI-compatible client creation
- Azure OpenAI v1 base URL derivation
- request compatibility fallbacks for unsupported parameters
- reasoning-effort normalization
- token estimation and usage tracking
- PageIndex compatibility patching

### Provider selection

Provider resolution is effectively:

1. Use `LLM_PROVIDER` if explicitly set.
2. Otherwise infer Azure when Azure endpoint/key settings are present.
3. Otherwise use the OpenAI-compatible path.

### Default model resolution

`utils.get_default_model()` resolves in this order:

- `LLM_MODEL`
- Azure deployment name from `AZURE_OPENAI_CHAT_DEPLOYMENT`
- `OPENAI_MODEL`
- fallback default

## Environment Variables

The following variables are actively read by application code.

### Core LLM/provider settings

| Variable | Purpose |
| --- | --- |
| `LLM_PROVIDER` | Explicit provider override |
| `LLM_MODEL` | Preferred default model/deployment |
| `OPENAI_API_KEY` | OpenAI key |
| `OPENAI_MODEL` | OpenAI default model |
| `OPENAI_BASE_URL` | Alternate OpenAI-compatible base URL |
| `AZURE_OPENAI_API_KEY` | Azure OpenAI key |
| `AZURE_OPENAI_ENDPOINT` | Azure endpoint |
| `AZURE_OPENAI_BASE_URL` | Explicit Azure v1 base URL |
| `AZURE_OPENAI_CHAT_DEPLOYMENT` | Azure deployment name |
| `CHATGPT_API_KEY` | Backfilled for PageIndex compatibility |

### Runtime behavior

| Variable | Purpose |
| --- | --- |
| `DOMAIN_NAME` | Domain-specific wording injected into answer prompts |
| `RETRIEVAL_MODE` | Default query mode: `hybrid` or `pageindex` |
| `NAVIGATOR_VERIFICATION` | Enable verifier pass |
| `ADVANCED_RETRIEVAL` | Master switch for advanced retrieval |
| `QUERY_PLANNING` | Enable planner when advanced retrieval is active |
| `ADAPTIVE_RETRIEVAL_WIDTH` | Allow planner-driven width changes |
| `NODE_EXPANSION` | Allow structural neighbor expansion |
| `MAX_DOCS_CAP` | Hard ceiling for advanced doc routing |
| `MAX_NODES_CAP` | Hard ceiling for advanced node selection |

### Ingestion behavior

| Variable | Purpose |
| --- | --- |
| `MASTER_TOP_SECTIONS_TARGET` | Target number of top sections stored in master nodes |
| `RELATED_DOCS_MODE` | Default relationship maintenance mode: `off`, `basic`, or `enhanced` |

### Persistence backend

| Variable | Purpose |
| --- | --- |
| `STORAGE_BACKEND` | `local` or `mongodb` |
| `MONGODB_URI` | Mongo connection string |
| `MONGODB_DATABASE` | Mongo database name |

## `config.yaml` vs runtime behavior

`config.yaml` is documentation only.

No production code loads `config.yaml` as an active config source. Actual runtime settings come from environment variables and explicit call parameters.

That file is still useful as a human-readable config reference, but it is not authoritative by itself.

## Shared Constants And Defaults

Selected shared defaults from `utils.py`:

- `INGESTION_REASONING_EFFORT = "high"`
- reasoning effort options: `minimal`, `low`, `medium`, `high`
- `MASTER_TOP_SECTIONS_DEFAULT = 4`
- `ADVANCED_RETRIEVAL_MAX_DOCS_CAP = 6`
- `ADVANCED_RETRIEVAL_MAX_NODES_CAP = 6`
- `RELATED_DOCS_MODE_DEFAULT = "basic"`
- retrieval modes: `hybrid`, `pageindex`

## Request Compatibility Layer

`utils.create_chat_completion()` and `utils.create_chat_completion_async()` protect the runtime against model/provider incompatibilities by retrying without unsupported arguments such as:

- `temperature`
- `reasoning_effort`
- `stream_options`

That layer matters because the repo tries to support both OpenAI and Azure OpenAI with one call path.

## PageIndex Compatibility Boundary

PageIndex still expects older OpenAI-style environment conventions.

`utils.ensure_pageindex_environment()` fills the required environment variables before the vendor code is called by:

- copying the resolved runtime API key into `CHATGPT_API_KEY` and `OPENAI_API_KEY`
- setting `OPENAI_BASE_URL` when needed

`ingestion/ingest.py` then patches selected PageIndex helper functions so the vendor code uses the repo's client/runtime behavior instead of bypassing it.

## Upload Persistence

The FastAPI adapter uses `api/runtime.py` to persist uploads into:

```text
data/uploads/{doc_id}_{random8}.{ext}
```

Uploads are shared across projects. The project namespace applies to index artifacts, not raw uploaded files.

## Operational Implications

- Reusing the same project with a different model will keep the same indexed documents visible.
- Changing `STORAGE_BACKEND` changes the store implementation behind the same runtime builder.
- Image-aware PDF ingestion is currently not backend-neutral because the new image helpers are implemented only on the local store path.
- If a behavior appears inconsistent between interfaces, check whether the interface is passing an explicit override or relying on env defaults.
