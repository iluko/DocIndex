# Hybrid Approach — Multi-Document RAG

A reasoning-first, multi-document retrieval system built on top of [PageIndex](https://github.com/VectifyAI/PageIndex). Works across any domain, any industry. No embeddings. No vector database.

---

## What It Does

Most RAG systems treat retrieval as a similarity search problem. This system treats it as a reasoning problem.

When you ask a question:

1. **[Optional] Planner** *(advanced mode)* classifies the query intent and recommends retrieval width before routing starts.
2. **Router** reads a compact index of all your documents and picks the 1–N most relevant ones using LLM reasoning.
3. **Navigator** reads each selected document's hierarchical tree (titles + summaries per section) and picks the 1–N most relevant sections — in parallel across all selected documents.
4. **[Optional] Expander** *(advanced mode)* adds bounded neighboring nodes (siblings, first child, parent) around the primary selection to capture surrounding context for workflow/process questions.
5. **Verifier** *(optional)* does a second pass to drop sections that don't actually address the query.
6. **Fetcher** extracts the raw text for those sections within a token budget, prefixing each chunk with its parent section's context so nothing is read out of context.
7. **Answer LLM** synthesises a final answer from the retrieved content.

If the router finds nothing on the first pass, it automatically widens to tangentially related documents and flags the answer accordingly. The system never silently fails.

**Standard mode** (default) is fast and deterministic: fixed 3-doc routing, 3-node navigation, no expansion.
**Advanced mode** (opt-in) trades latency and cost for better completeness on broad, workflow, or cross-document questions.

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
  │  [Planner] → Router → Navigator → [Expander] │
  │  → Verifier → Fetcher → Answer LLM           │
  │                                              │
  │  (or PageIndex agentic tool-use loop)        │
  │  Planner and Expander are opt-in (advanced)  │
  └──────────────────────────────────────────────┘
          │
          ▼
     Final Answer
```

---

## What Changed in v1.1

### Richer master-node representation (always on)

Every newly ingested document now generates **routing facets** alongside the existing summary and key topics. These are short phrase lists — not prose — that help the router identify documents for workflow, actor, or system queries that might not surface clearly from summaries alone:

| Facet | Contents |
|---|---|
| `workflows` | Named end-to-end processes documented in this file |
| `actors` | Roles, teams, or stakeholders mentioned |
| `systems` | Major systems, modules, APIs, or services |
| `edge_cases` | Error conditions, failure modes, non-happy-path coverage |
| `authority_hints` | Version, scope, or jurisdiction qualifiers |

**Backward compatibility:** old master-tree records load normally — `routing_facets` defaults to `None` and the router works identically using the existing fields. Re-ingestion is recommended (but not required) to populate the new facets.

### Advanced retrieval (opt-in)

Three optional query-time features that can be enabled together or individually:

| Feature | What it does | Extra cost |
|---|---|---|
| **Query planning** | Classifies query intent and recommends retrieval width | +1 LLM call |
| **Adaptive width** | Uses planner output to widen max_docs / max_nodes | Depends on width |
| **Node expansion** | Adds bounded neighboring context around selected nodes | Depends on neighbors |

**Standard mode is unchanged.** Advanced features are disabled by default and must be explicitly enabled. See *Advanced Retrieval* section below for configuration details.

### Related-docs relationship hardening (v1.2, opt-in per ingestion)

`related_docs` on each master-tree node is a list of other doc IDs the router uses as a soft cross-document hint. Before v1.2 these links were generated once per ingestion and never reconciled — links could be asymmetric, order-dependent, or stale.

v1.2 adds **ingestion-time relationship maintenance modes** to fix this. The mode is chosen per-ingestion (not per-query) and has no impact on retrieval latency.

| Mode | What it does | Extra cost |
|---|---|---|
| `off` | Near-current behavior. No reconciliation after master-node generation. | None |
| `basic` (default) | Deterministic: enforces no self-links, dedup, bounded length, and symmetric direct relationships across the local neighborhood. | Negligible |
| `enhanced` | Adds a bounded LLM-assisted pass: builds a candidate shortlist from metadata overlap (topics, categories, routing facets), then uses a narrow LLM call to refine the new doc's related_docs. Falls back to basic on failure. | +1 LLM call per ingestion |

**Query-time graph-aware traversal is NOT yet implemented.** Related-docs links are currently used only as a soft hint in the router prompt. A future Level 3 feature may use them for bounded 1-hop expansion at query time.

**Backward compatibility:** existing corpora continue to work. No migration required. The mode is selected fresh on each ingest or reingest call.

---

## Two Retrieval Modes

`hybrid` is the default and recommended mode for most use cases. `pageindex` is an optional advanced mode for harder multi-document questions.

| Mode | How it works | Latency | Cost | Predictability | Best for |
|---|---|---|---|---|---|
| `hybrid` (default) | Fixed pipeline: router → navigator → verifier → fetcher → answer. Deterministic, fully traceable, bounded. | Low | Low | High | Targeted questions, production use, observability required |
| `pageindex` | Agentic tool-use loop: LLM calls `get_document_structure` and `get_node_content` iteratively until it has enough context to answer. Can explore across multiple docs. | Higher | Higher | Lower | Complex multi-hop questions, exploratory research, hard multi-document reasoning |

Both modes share the same router, including the broadened fallback pass.

### Selecting the retrieval mode

**Via environment variable (applies to all queries):**
```env
RETRIEVAL_MODE=hybrid     # default
RETRIEVAL_MODE=pageindex  # opt-in agentic mode
```

**Via CLI (per query, overrides env var):**
```bash
python cli.py query "Which documents cover both auth and RBAC?" --retrieval-mode pageindex
python cli.py query "What is the login flow?" --retrieval-mode hybrid
```

**Via Streamlit UI:** Use the "Retrieval mode" selector in the Query sidebar section. The selector defaults to the env-var setting and can be changed per-session.

### PageIndex mode traceability

When using `pageindex` mode, the query trace includes:
- `pageindex_tool_calls_made` / `pageindex_tool_call_budget` — how many tool calls were made vs. the cap
- `pageindex_content_tokens_used` / `pageindex_content_token_budget` — content retrieved vs. budget
- `pageindex_explored_docs` — which documents had their structure inspected
- `pageindex_tool_budget_exhausted` — whether the tool-call cap was hit
- `pageindex_content_budget_exhausted` — whether the content token budget was hit

These metrics are visible in the Streamlit trace panel and the `--verbose` CLI flag.

### When to use each mode

Use **hybrid** when:
- You want fast, predictable answers
- The question is targeted (one or two specific facts)
- You need full auditability of every retrieval step
- Latency and cost matter

Use **pageindex** when:
- The question requires reading across many sections of multiple documents
- You want the LLM to decide what to read next rather than following a fixed plan
- You are doing exploratory research and completeness matters more than latency
- The query is complex or multi-hop (e.g. "Compare how auth and RBAC handle token expiry")

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

To control relationship maintenance at ingestion time:

```bash
# Use the default (basic — deterministic symmetry, no LLM cost)
python cli.py ingest --file doc.pdf --doc-id my_doc --title "Title" --doc-type spec

# Explicitly disable reconciliation (off — near-original behavior)
python cli.py ingest --file doc.pdf --doc-id my_doc --title "Title" --doc-type spec \
  --relationship-mode off

# Use LLM-assisted candidate scoring (enhanced — best link quality, +1 LLM call)
python cli.py ingest --file doc.pdf --doc-id my_doc --title "Title" --doc-type spec \
  --relationship-mode enhanced
```

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

**With advanced retrieval:**
```bash
# Enable all advanced features
python cli.py query "Walk me through the onboarding workflow" --advanced --verbose

# Fine-grained control
python cli.py query "Compare auth approaches" \
  --advanced \
  --no-expansion \
  --max-docs-cap 4 \
  --max-nodes-cap 4
```

Advanced retrieval enables query planning, adaptive routing width, and node-neighborhood expansion. Use it for broad, workflow, or multi-document questions. Expect higher latency and token cost.

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
from retrieval.query_engine import AdvancedRetrievalConfig, query

async def main():
    runtime = build_runtime_components("data", model="gpt-4o-2024-11-20")

    # Standard mode (default — fast, predictable)
    result = await query(
        user_query="How does the authentication flow work?",
        master_tree_store=runtime.master_tree_store,
        storage=runtime.storage,
        model="gpt-4o-2024-11-20",
        verbose=True,
    )

    # Advanced mode (opt-in — better for broad/workflow questions)
    result = await query(
        user_query="Walk me through the full onboarding workflow",
        master_tree_store=runtime.master_tree_store,
        storage=runtime.storage,
        model="gpt-4o-2024-11-20",
        advanced_retrieval=AdvancedRetrievalConfig(
            enabled=True,
            enable_planning=True,
            enable_adaptive_width=True,
            enable_node_expansion=True,
            max_docs_cap=6,
            max_nodes_cap=6,
        ),
    )

    print(result.answer)
    print("Docs used:", result.selected_docs)
    print("Sections used:", result.selected_nodes)
    if result.trace:
        print("Routing broadened:", result.trace.routing_broadened)
        print("Verification applied:", result.trace.verification_applied)
        if result.trace.advanced_retrieval_enabled:
            plan = result.trace.planner_output
            if plan:
                print("Query type:", plan.query_type)
            print("Expanded nodes:", result.trace.expanded_node_refs)

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
| `advanced_retrieval_enabled` | `bool` | Whether advanced retrieval was active for this query |
| `planner_output` | `QueryPlan \| None` | Planner classification result (when planning was run) |
| `effective_max_docs` | `int` | Actual max_docs used after planner adjustment |
| `effective_max_nodes` | `int` | Actual max_nodes used after planner adjustment |
| `node_expansion_applied` | `bool` | Whether node-neighborhood expansion added extra chunks |
| `primary_node_refs` | `list[str]` | Nodes selected by the navigator (primary) |
| `expanded_node_refs` | `list[str]` | Nodes added by expansion (secondary) |
| `pageindex_tool_calls_made` | `int` | *(pageindex mode only)* Tool calls made during exploration |
| `pageindex_tool_call_budget` | `int` | *(pageindex mode only)* Maximum tool calls allowed |
| `pageindex_content_tokens_used` | `int` | *(pageindex mode only)* Content tokens retrieved |
| `pageindex_content_token_budget` | `int` | *(pageindex mode only)* Content token budget |
| `pageindex_explored_docs` | `list[str]` | *(pageindex mode only)* Docs whose structure was inspected |
| `pageindex_tool_budget_exhausted` | `bool` | *(pageindex mode only)* True if tool-call cap was reached |
| `pageindex_content_budget_exhausted` | `bool` | *(pageindex mode only)* True if content token budget was reached |

`RetrievedChunk.is_expansion` is `True` for chunks that came from expansion rather than primary navigation.

PageIndex-prefixed fields are zero/empty for `hybrid` mode queries.

---

## Advanced Retrieval

Advanced retrieval is opt-in. Standard mode is unchanged when it is disabled.

### Enable via environment

```env
# Top-level toggle
ADVANCED_RETRIEVAL=true

# Individual sub-features (default: all true when advanced retrieval is on)
QUERY_PLANNING=true
ADAPTIVE_RETRIEVAL_WIDTH=true
NODE_EXPANSION=true

# Hard caps (applied in advanced mode only)
MAX_DOCS_CAP=6
MAX_NODES_CAP=6
```

### Enable via CLI

```bash
# All features on
python cli.py query "Walk me through onboarding" --advanced

# Fine-grained
python cli.py query "Compare auth and RBAC" \
  --advanced --no-expansion --max-docs-cap 4
```

### Enable via Streamlit UI

Toggle **Advanced Retrieval** in the sidebar. Expand "Advanced options" to control individual sub-features and caps.

### Tradeoffs

| | Standard | Advanced |
|---|---|---|
| Latency | Lowest | Higher (+1 LLM call for planner + wider retrieval) |
| Cost | Lowest | Higher (more tokens, more LLM calls) |
| Coverage | Good for narrow/specific queries | Better for broad/workflow/multi-doc queries |
| Noise | Minimal | Slightly more context may introduce noise |

Recommendation: keep standard mode as the default. Enable advanced retrieval selectively for questions that are explicitly broad, process-oriented, or cross-document.

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
| `ADVANCED_RETRIEVAL` | `false` | `true` to enable advanced retrieval globally |
| `QUERY_PLANNING` | `true` | `false` to disable query planning when advanced retrieval is on |
| `ADAPTIVE_RETRIEVAL_WIDTH` | `true` | `false` to disable adaptive width when advanced retrieval is on |
| `NODE_EXPANSION` | `true` | `false` to disable node-neighborhood expansion when advanced retrieval is on |
| `MAX_DOCS_CAP` | `6` | Hard cap on documents routed in advanced mode |
| `MAX_NODES_CAP` | `6` | Hard cap on nodes selected per document in advanced mode |
| `RELATED_DOCS_MODE` | `basic` | Ingestion-time relationship maintenance: `off`, `basic`, or `enhanced`. Overridden per-call by `--relationship-mode` (CLI) or the UI selector. |





## Notes: 
<!-- Next thing 

Multiple Doc Ingestion
Evaluation Metrics 
Comparisons 
Input of images 
Is there a way to dynamically allot number of documents required per doc
Auditability and Traceability
Make retreival and answering decoupled. Should be able to retireve onyl retreival is needed

1. Add Images 
2. Fix Hybrid UI 
3. Fix Experiments UI 
4. Create Golden Dataset-->