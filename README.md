# Hybrid Approach — Multi-Document RAG

A reasoning-first, multi-document retrieval system built on top of [PageIndex](https://github.com/VectifyAI/PageIndex). Works across any domain, any industry. No embeddings. No vector database.

---

## What It Does

Most RAG systems treat retrieval as a similarity search problem. This system treats it as a reasoning problem.

When you ask a question:

1. **Router** reads a compact index of all your documents and picks the 1–3 most relevant ones using LLM reasoning.
2. **Navigator** reads each selected document's hierarchical tree (titles + summaries per section) and picks the 1–3 most relevant sections — in parallel across all selected documents.
3. **Verifier** *(optional)* does a second pass to drop sections that don't actually address the query.
4. **Fetcher** extracts the raw text for those sections within a token budget, prefixing each chunk with its parent section's context so nothing is read out of context.
5. **Answer LLM** synthesises a final answer from the retrieved content.

If the router finds nothing on the first pass, it automatically widens to tangentially related documents and flags the answer accordingly. The system never silently fails.

---

## Architecture

```
Documents (.pdf / .md / .docx)
          │
          ▼
    ┌─────────────┐
    │  PageIndex  │  builds a hierarchical section tree per document
    └─────────────┘
          │
          ▼
  Per-doc tree JSONs
          │
          ▼
  ┌──────────────────┐
  │  Master Node Gen │  LLM generates routing metadata for each document
  └──────────────────┘
          │
          ▼
  ┌──────────────────┐
  │   Master Tree    │  flat index of all documents with routing hints
  └──────────────────┘
          │
          ▼
  ┌──────────────────────────────────────────────┐
  │               Query Engine                   │
  │                                              │
  │  Router → Navigator → Verifier → Fetcher     │
  │                                              │
  │  (or PageIndex agentic tool-use loop)        │
  └──────────────────────────────────────────────┘
          │
          ▼
     Final Answer
```

---

## Two Retrieval Modes

Set `RETRIEVAL_MODE` in `.env` to switch:

| Mode | How it works | Best for |
|---|---|---|
| `hybrid` (default) | Fixed pipeline: router → navigator → verifier → fetcher → answer. Deterministic, fully traceable, fast. | Production use, observability required |
| `pageindex` | Agentic loop: LLM calls `get_document_structure` and `get_node_content` iteratively until it has enough to answer. Can self-correct and backtrack. | Complex multi-hop queries, exploratory use |

Both modes share the same router and the same broadened fallback.

---

## Setup

### 1. Clone PageIndex

PageIndex is the document parsing layer. Clone it alongside this project:

```bash
git clone https://github.com/VectifyAI/PageIndex
pip install -r PageIndex/requirements.txt
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

For running tests:

```bash
pip install -r requirements-dev.txt
```

### 3. Configure `.env`

Copy `.env.example` to `.env` and fill in your credentials:

```bash
cp .env.example .env
```

**Azure OpenAI:**
```env
LLM_PROVIDER=azure
LLM_MODEL=your-deployment-name
AZURE_OPENAI_API_KEY=...
AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com
```

**OpenAI:**
```env
LLM_PROVIDER=openai
OPENAI_API_KEY=...
OPENAI_MODEL=gpt-4o-2024-11-20
```

**Optional settings:**
```env
# Human-readable name for your knowledge base
# e.g. "Acme Legal Docs", "Product Engineering Wiki", "HR Policy Hub"
DOMAIN_NAME=

# Ingestion-only: target number of stored master-node top sections.
# The prompt range becomes target-1 to target+1, capped at 12.
# Higher values improve routing detail but increase master-tree prompt size.
MASTER_TOP_SECTIONS_TARGET=4

# Retrieval pipeline: "hybrid" (default) or "pageindex"
RETRIEVAL_MODE=hybrid

# Post-navigation self-correction pass (adds ~1 LLM call per doc, runs in parallel)
NAVIGATOR_VERIFICATION=false
```

### 4. Optionally populate the domain context map

`data/arch_map.json` is injected into the router to help it widen scope when a query spans multiple concepts. Fill in `entities` for your domain if your documents are interdependent:

```json
{
  "description": "My knowledge base domain",
  "entities": [
    {
      "name": "Authentication",
      "connects_to": ["Authorisation", "Session Management"],
      "description": "Handles identity verification and token issuance"
    }
  ]
}
```

Leave `entities` as an empty array if your documents are independent — the router works fine without it.

---

## Ingest Documents

```bash
python cli.py ingest \
  --file path/to/document.pdf \
  --doc-id my_doc \
  --title "My Document Title" \
  --doc-type technical_spec \
  --model your-model-name
```

Supported file types: `.pdf`, `.md`, `.markdown`, `.docx`

DOCX files are automatically converted to Markdown so heading structure is preserved.

**`--doc-id` format:** alphanumeric, hyphens, and underscores only — must start with a letter or digit (e.g. `auth_spec_v2`, `onboarding-guide`).

**List ingested documents:**
```bash
python cli.py list-docs
```

**Delete a document:**
```bash
python cli.py delete-doc my_doc_id
```

**Replace/update a document:**
```bash
python cli.py reingest \
  --file updated_document.pdf \
  --doc-id my_doc \
  --title "My Document Title" \
  --doc-type technical_spec
```

---

## Query

**CLI (interactive, supports follow-ups):**
```bash
python cli.py query "Your question here" --verbose
```

`--verbose` shows which documents were routed to, which sections were selected, and whether routing was broadened or verification applied.

**Web UI:**
```bash
streamlit run app.py
```

The UI provides:
- Document upload and ingestion with live step-by-step progress
- Advanced ingestion controls such as the master top-sections target
- Interactive Q&A with full trace inspection
- View of routing decisions, selected nodes, fetched chunks, and retrieved context

---

## Document Lifecycle

```bash
# Ingest
python cli.py ingest --file doc.pdf --doc-id my_doc --title "Title" --doc-type spec

# Ingest with a higher top-section target (prompt range becomes 4-6)
python cli.py ingest --file doc.pdf --doc-id my_doc --title "Title" --doc-type spec \
  --top-sections-target 5

# See what's indexed
python cli.py list-docs

# Inspect one document's routing metadata
python cli.py show-master-tree --doc-id my_doc

# Delete
python cli.py delete-doc my_doc --yes

# Update (delete + reingest in one step)
python cli.py reingest --file updated.pdf --doc-id my_doc --title "Title" --doc-type spec --yes
```

---

## Projects

Every CLI command and the web UI supports an optional `--project` flag (CLI) or project selector (UI) that scopes all operations to a named knowledge base. Different projects are completely isolated — documents, trees, and routing metadata never mix.

```bash
# Ingest into a named project
python cli.py ingest --file doc.pdf --doc-id my_doc --title "Title" --doc-type spec \
  --project acme_legal

# Query that project
python cli.py query "What is the indemnity clause?" --project acme_legal

# List docs in a project
python cli.py list-docs --project acme_legal
```

If `--project` is omitted, operations go to the `default` project.

Index layout on disk:

```
data/
  arch_map.json              ← shared domain context (tracked in git)
  uploads/                   ← original uploaded files
  indexes/
    default/                 ← default project
      master_tree.json
      doc_sources.json
      doc_trees/
      derived_markdown/
      index_meta.json
    acme_legal/              ← a named project
      master_tree.json
      ...
```

The active model is recorded in `index_meta.json` but does **not** determine which directory is used — you can switch models without losing your indexed documents.

---

## Storage Backends

By default all index artifacts are written to the local `data/` directory. Set `STORAGE_BACKEND=mongodb` to persist to MongoDB instead, which is useful for containerised or multi-instance deployments.

```env
STORAGE_BACKEND=mongodb
MONGODB_URI=mongodb://localhost:27017
MONGODB_DATABASE=hybrid_approach
```

When using local storage, source paths inside `data/` are stored relative to the data root so the entire `data/` folder can be moved or copied to another machine without breaking retrieval.

Both backends implement the same interface — no application code changes are needed when switching.

---

## Python API

```python
import asyncio
from index_registry import build_runtime_components
from retrieval.query_engine import query

async def main():
    runtime = build_runtime_components("data", model="gpt-4o-2024-11-20")

    result = await query(
        user_query="How does the authentication flow work?",
        master_tree_store=runtime.master_tree_store,
        storage=runtime.storage,
        arch_map=runtime.arch_map,
        model="gpt-4o-2024-11-20",
        verbose=True,
    )

    print(result.answer)
    print("Docs used:", result.selected_docs)
    print("Sections used:", result.selected_nodes)
    print("Routing broadened:", result.trace.routing_broadened)
    print("Verification applied:", result.trace.verification_applied)

asyncio.run(main())
```

Pass `project="my_project"` to `build_runtime_components` to scope the runtime to a named project.

---

## QueryTrace Fields

Every `QueryResult` carries a `trace` object for full observability:

| Field | Type | Meaning |
|---|---|---|
| `routed_docs` | `list[str]` | Documents selected by the router |
| `navigation` | `dict[str, list[str]]` | Sections selected per document |
| `fetched_chunks` | `list[RetrievedChunk]` | Raw content fetched with token estimates |
| `token_budget` | `int` | Token budget enforced during fetch |
| `truncated` | `bool` | Whether content was cut to fit budget |
| `retrieval_mode` | `str` | `"hybrid"` or `"pageindex"` |
| `routing_broadened` | `bool` | Whether strict routing found nothing and fallback was used |
| `verification_applied` | `bool` | Whether the verifier pass ran and filtered sections |

---

## Environment Variables Reference

| Variable | Default | Description |
|---|---|---|
| `LLM_PROVIDER` | `openai` | `openai` or `azure` |
| `LLM_MODEL` | — | Model or deployment name (overrides `OPENAI_MODEL` / `AZURE_OPENAI_CHAT_DEPLOYMENT`) |
| `OPENAI_API_KEY` | — | OpenAI API key |
| `OPENAI_MODEL` | — | OpenAI model name, e.g. `gpt-4o-2024-11-20` |
| `AZURE_OPENAI_API_KEY` | — | Azure OpenAI API key |
| `AZURE_OPENAI_ENDPOINT` | — | Azure endpoint URL |
| `AZURE_OPENAI_BASE_URL` | *(derived)* | Full base URL; derived from `AZURE_OPENAI_ENDPOINT` if omitted |
| `AZURE_OPENAI_CHAT_DEPLOYMENT` | — | Azure deployment name |
| `CHATGPT_API_KEY` | *(auto-filled)* | PageIndex key; auto-filled from Azure key in Azure mode |
| `DOMAIN_NAME` | *(blank)* | Name shown in answer prompt, e.g. `"Acme Docs"` |
| `MASTER_TOP_SECTIONS_TARGET` | `4` | Ingestion-only target for stored master-node top sections. Prompt range becomes target-1 to target+1, capped at 12. Higher values improve routing detail but increase prompt size. |
| `RETRIEVAL_MODE` | `hybrid` | `hybrid` or `pageindex` |
| `NAVIGATOR_VERIFICATION` | `false` | `true` to enable post-navigation self-correction pass |
| `STORAGE_BACKEND` | `local` | `local` (filesystem) or `mongodb` |
| `MONGODB_URI` | `mongodb://localhost:27017` | MongoDB connection string (when `STORAGE_BACKEND=mongodb`) |
| `MONGODB_DATABASE` | `hybrid_approach` | MongoDB database name (when `STORAGE_BACKEND=mongodb`) |
