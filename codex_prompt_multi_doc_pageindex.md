# Codex Prompt: Multi-Document PageIndex with LLM-Generated Master Tree

---

## Project Overview

Build a multi-document reasoning-based RAG system on top of the open-source
[PageIndex](https://github.com/VectifyAI/PageIndex) library. The system extends
PageIndex — which builds hierarchical tree indexes for single documents — with:

1. A **Master Tree**: a flat-list JSON index across all documents, LLM-generated,
   that enables document-level routing before diving into any per-doc tree.
2. A **two-hop query engine**: Master Tree → per-doc tree(s) → raw content → answer.
3. An **architecture map**: a static JSON context file injected alongside the Master
   Tree at query time, describing how SIP (Sales Intelligence Platform) modules
   relate to each other, used to widen retrieval scope intelligently.
4. A **document ingestion pipeline**: adds new documents, generates their PageIndex
   tree, generates their master node, and updates the Master Tree.
5. A **CLI and Python API** for both ingestion and query.

---

## Repository Structure to Create

```
multi_doc_pageindex/
├── README.md
├── requirements.txt
├── .env.example
├── config.yaml
│
├── ingestion/
│   ├── __init__.py
│   ├── ingest.py            # Main ingestion pipeline
│   └── master_node_gen.py   # LLM logic to generate a master tree node
│
├── master_tree/
│   ├── __init__.py
│   ├── master_tree.py       # MasterTree class: load, save, add, list
│   └── schema.py            # Pydantic models for MasterNode and MasterTree
│
├── retrieval/
│   ├── __init__.py
│   ├── router.py            # Step 1: LLM reads master tree → selects doc(s)
│   ├── navigator.py         # Step 2: LLM reads per-doc tree → selects nodes
│   ├── fetcher.py           # Step 3: Extract raw page text from selected nodes
│   └── query_engine.py      # Orchestrates router → navigator → fetcher → answer
│
├── arch_map/
│   ├── __init__.py
│   └── arch_map.py          # Loads architecture map JSON; injected at query time
│
├── storage/
│   ├── __init__.py
│   └── store.py             # File-based storage for per-doc trees + master tree
│
├── cli.py                   # CLI: `ingest`, `query`, `list-docs`, `show-master-tree`
│
├── data/
│   ├── master_tree.json     # Persisted master tree (auto-managed)
│   ├── arch_map.json        # SIP module architecture map (user-maintained)
│   └── doc_trees/           # Per-doc PageIndex trees: {doc_id}_tree.json
│
└── tests/
    ├── test_ingestion.py
    ├── test_master_tree.py
    ├── test_retrieval.py
    └── fixtures/
        ├── sample_doc_tree.json
        └── sample_master_tree.json
```

---

## Dependencies

Install via pip. Add to `requirements.txt`:

```
# PageIndex (clone separately and install, or install from source)
# git clone https://github.com/VectifyAI/PageIndex
# pip install -e ./PageIndex

openai>=1.30.0
pydantic>=2.0.0
python-dotenv>=1.0.0
PyMuPDF>=1.23.0        # fitz — used by PageIndex for PDF text extraction
tiktoken>=0.7.0
asyncio
click>=8.1.0           # CLI
rich>=13.0.0           # Pretty CLI output
```

Environment variables (`.env.example`):

```
CHATGPT_API_KEY=your_openai_key_here   # PageIndex uses this exact key name
OPENAI_MODEL=gpt-4o-2024-11-20         # Default model for all LLM calls
```

**Important**: PageIndex reads `CHATGPT_API_KEY` from the environment. Do not
rename it. Your code should load `.env` at the top of every entrypoint using
`python-dotenv`.

---

## Module Specifications

---

### `master_tree/schema.py`

Define Pydantic v2 models. These are the canonical data structures used everywhere.

```python
from pydantic import BaseModel, Field
from typing import Optional

class TopSection(BaseModel):
    """A top-level section reference from the per-doc PageIndex tree."""
    title: str
    node_ref: str           # Compound key: "{doc_id}::{node_id}" e.g. "auth_spec::0003"
    section_summary: str    # LLM-generated 1-2 sentence summary of this section

class RelevanceHints(BaseModel):
    best_for: str           # What queries this doc best answers (1-2 sentences)
    not_useful_for: str     # What this doc does NOT cover (1-2 sentences)
    key_modules: list[str]  # SIP module names this doc touches e.g. ["Auth", "RBAC"]

class MasterNode(BaseModel):
    doc_id: str                          # Unique slug e.g. "sip_auth_spec_v2"
    doc_title: str                       # Human-readable title
    doc_type: str                        # e.g. "technical_spec", "competitive_analysis", "compliance"
    file_path: str                       # Absolute path to original PDF/MD file
    tree_path: str                       # Path to {doc_id}_tree.json
    doc_summary: str                     # 2-3 sentence summary of the whole document
    key_topics: list[str]               # Top 5-8 topics/concepts this doc covers
    relevance_hints: RelevanceHints
    top_sections: list[TopSection]      # Top 3-5 sections from the per-doc tree
    related_docs: list[str]             # doc_ids of related documents (populated by LLM)
    ingested_at: str                    # ISO 8601 timestamp

class MasterTree(BaseModel):
    version: str = "1.0"
    docs: list[MasterNode] = Field(default_factory=list)
```

---

### `master_tree/master_tree.py`

`MasterTree` class handles persistence and CRUD.

```python
class MasterTreeStore:
    def __init__(self, master_tree_path: str):
        # Load from JSON if exists, else create empty MasterTree
        ...

    def load(self) -> MasterTree: ...
    def save(self, tree: MasterTree) -> None: ...
    def add_node(self, node: MasterNode) -> None:
        # If doc_id already exists, replace (upsert semantics)
        ...
    def get_node(self, doc_id: str) -> Optional[MasterNode]: ...
    def list_docs(self) -> list[MasterNode]: ...
    def to_llm_context(self) -> str:
        # Serialize the master tree into a compact string for LLM context injection.
        # Format: JSON with only the fields needed for routing decisions.
        # Omit: file_path, tree_path, ingested_at (these are operational, not semantic).
        # Include: doc_id, doc_title, doc_type, doc_summary, key_topics,
        #          relevance_hints, top_sections (title + section_summary only, no node_ref),
        #          related_docs
        ...
```

---

### `ingestion/master_node_gen.py`

This is the LLM call that generates a `MasterNode` from a per-doc PageIndex tree.
It receives the existing master tree as context so the LLM can populate
`related_docs` and write `relevance_hints` that differentiate this doc from others.

**Function signature:**
```python
async def generate_master_node(
    doc_id: str,
    doc_title: str,
    doc_type: str,
    file_path: str,
    tree_path: str,
    per_doc_tree: dict,           # The full PageIndex tree JSON for this document
    existing_master_tree: str,    # MasterTreeStore.to_llm_context() output
    model: str = "gpt-4o-2024-11-20",
) -> MasterNode:
```

**LLM prompt to use** (build this inside the function):

```
System:
You are a document intelligence analyst. Your job is to generate a structured
metadata record for a document that will be added to a multi-document index.
The index is used to route user queries to the right document(s) before performing
detailed retrieval. Your output must be a JSON object conforming exactly to the
schema below. Output only valid JSON — no markdown fences, no preamble.

Schema:
{
  "doc_summary": "<2-3 sentence summary of what this document covers>",
  "key_topics": ["<topic1>", "<topic2>", ...],   // 5-8 topics
  "doc_type": "<type>",
  "relevance_hints": {
    "best_for": "<what queries this doc best answers>",
    "not_useful_for": "<what this doc does NOT cover>",
    "key_modules": ["<SIP module name>", ...]
  },
  "top_sections": [
    {
      "title": "<section title>",
      "node_ref": "<doc_id>::<node_id>",
      "section_summary": "<1-2 sentence summary>"
    }
    // 3-5 sections only — the most important ones for routing
  ],
  "related_docs": ["<doc_id>", ...]  // doc_ids from the existing index that relate to this doc
}

User:
You are adding the following document to a multi-document index.

Document ID: {doc_id}
Document Title: {doc_title}
Document Type: {doc_type}

## PageIndex Tree Structure (this document's full hierarchical index):
{per_doc_tree_json}

## Existing Master Tree (all documents already indexed):
{existing_master_tree}

Instructions:
1. Write doc_summary as 2-3 sentences that precisely characterise what this document
   covers. Be specific — avoid generic phrases like "covers important topics".
2. For key_topics, list the 5-8 most important concepts, entities, or themes.
3. For relevance_hints.best_for, write 1-2 sentences describing the exact type of
   query this document best answers. Be specific enough that an LLM can use this
   to decide whether to select this document.
4. For relevance_hints.not_useful_for, explicitly state what this document does NOT
   cover — this prevents incorrect routing.
5. For relevance_hints.key_modules, list the SIP module names (e.g. "Auth", "RBAC",
   "API Gateway", "Feedback Engine") that this document relates to.
6. For top_sections, select the 3-5 sections from the PageIndex tree that are most
   useful for routing. Each node_ref must be "{doc_id}::{node_id}" using the exact
   node_id from the tree.
7. For related_docs, list doc_ids from the existing master tree that are meaningfully
   related (share topics, complement each other, or should be consulted together).
   If no documents are related, return an empty list.

Output only the JSON object. No markdown. No explanation.
```

Parse the LLM response as JSON, construct a `MasterNode` with `ingested_at` set
to `datetime.utcnow().isoformat()`.

---

### `ingestion/ingest.py`

Main ingestion pipeline. Orchestrates: PageIndex → master node gen → master tree update → save.

```python
async def ingest_document(
    file_path: str,
    doc_id: str,                    # User-supplied slug, e.g. "sip_auth_spec_v2"
    doc_title: str,
    doc_type: str,
    master_tree_store: MasterTreeStore,
    storage: DocumentStore,
    model: str = "gpt-4o-2024-11-20",
    pageindex_opts: dict = None,    # Passed through to PageIndex (overrides defaults)
) -> MasterNode:
```

**Steps inside `ingest_document`:**

1. **Validate** that `file_path` exists and is `.pdf` or `.md`/`.markdown`.

2. **Run PageIndex** to generate the per-doc tree:
   - For PDF: call `pageindex.page_index.page_index_main()` with the file path
     and `pageindex_opts`. Use defaults: `model=model`, `if_add_node_id="yes"`,
     `if_add_node_summary="yes"`, `if_add_doc_description="no"`,
     `max_page_num_each_node=10`, `max_token_num_each_node=20000`.
   - For Markdown: call `pageindex.page_index_md.md_to_tree()` via `asyncio.run()`
     with `ConfigLoader` defaults, same overrides.
   - Both return a dict (the tree JSON).
   - **Important**: PageIndex uses `CHATGPT_API_KEY` from env. Ensure it is loaded
     before this call.

3. **Save per-doc tree** to `data/doc_trees/{doc_id}_tree.json` via `DocumentStore`.

4. **Get existing master tree context** via `master_tree_store.to_llm_context()`.

5. **Generate master node** via `generate_master_node(...)`. Pass the full per-doc
   tree dict as `per_doc_tree`.

6. **Update master tree**: `master_tree_store.add_node(master_node)` then
   `master_tree_store.save(...)`.

7. Return the `MasterNode`.

Print progress at each step using `rich.console.Console`.

---

### `arch_map/arch_map.py`

Loads `data/arch_map.json` and provides it as a formatted string for LLM injection.

```python
class ArchitectureMap:
    def __init__(self, arch_map_path: str):
        # Load arch_map.json. If not found, return empty context gracefully.
        ...

    def to_llm_context(self) -> str:
        # Returns a compact, human-readable string describing module relationships.
        # Format: flat JSON or YAML-like block. Keep it under ~800 tokens.
        ...
```

**Expected format of `data/arch_map.json`:**

```json
{
  "description": "SIP module relationship map for query routing context",
  "modules": [
    {
      "name": "Auth",
      "connects_to": ["RBAC", "API Gateway", "Audit"],
      "description": "Handles Microsoft Entra ID SSO, JWT issuance, session management"
    },
    {
      "name": "RBAC",
      "connects_to": ["Auth", "API Gateway", "Audit"],
      "description": "Role-based access control, permission resolution, tenant isolation"
    }
  ]
}
```

Provide a starter `data/arch_map.json` with these SIP modules pre-populated as
a template (leave `connects_to` and `description` as placeholders the user fills in):
Auth, RBAC, API Gateway, Compliance & Audit, Feedback Engine, Analytics Dashboard,
CRM Integration, Knowledge Base, Agent Orchestrator, Cross-Module Amendment Tracker.

---

### `retrieval/router.py`

Step 1 of query time. The LLM reads the master tree + arch map and selects 1-3 docs.

```python
async def route_query(
    query: str,
    master_tree_store: MasterTreeStore,
    arch_map: ArchitectureMap,
    model: str = "gpt-4o-2024-11-20",
    max_docs: int = 3,
    chat_history: list[dict] = None,   # Prior turns: [{"role": "user"/"assistant", "content": "..."}]
) -> list[str]:   # Returns list of doc_ids, ordered by relevance
```

**LLM prompt:**

```
System:
You are a document routing agent. You have access to a multi-document index and
an architecture map. Your job is to identify which documents in the index are most
likely to contain the answer to the user's query. You must select between 1 and
{max_docs} documents. Return ONLY a JSON array of doc_id strings, ordered from
most to least relevant. No explanation, no markdown. Example: ["auth_spec", "rbac_overview"]

Architecture Map (use this to understand module relationships and widen scope):
{arch_map_context}

Master Tree (all indexed documents):
{master_tree_context}

User:
Query: {query}

{chat_history_block}

Select the {max_docs} most relevant doc_ids. If fewer than {max_docs} docs are
relevant, return only those that are genuinely relevant. Return JSON array only.
```

Parse response as a JSON array. Validate that each `doc_id` exists in the master
tree. Return the validated list.

---

### `retrieval/navigator.py`

Step 2. For each selected doc, the LLM reads the per-doc PageIndex tree and
selects the specific node(s) most likely to contain the answer.

```python
async def navigate_doc_tree(
    query: str,
    doc_id: str,
    per_doc_tree: dict,
    model: str = "gpt-4o-2024-11-20",
    chat_history: list[dict] = None,
) -> list[str]:   # Returns list of node_refs: "{doc_id}::{node_id}"
```

**LLM prompt:**

```
System:
You are a document navigation agent. You are given a hierarchical tree index
(table of contents with summaries) for a single document. Your task is to
identify which nodes in the tree most likely contain the answer to the query.
Return a JSON array of node_id strings from this document, ordered by relevance.
Return at most 3 node_ids. Return only the node_id values (e.g. ["0003", "0007"]),
not the full node_ref. No markdown, no explanation.

User:
Document: {doc_id}
Query: {query}

{chat_history_block}

Document Tree:
{per_doc_tree_json}

Return JSON array of node_ids only.
```

Validate that each returned `node_id` exists in the tree. Convert to full
`node_refs` as `"{doc_id}::{node_id}"` before returning.

---

### `retrieval/fetcher.py`

Step 3. Given node_refs, extract the raw text from the document.

```python
def fetch_node_content(
    node_ref: str,                  # "{doc_id}::{node_id}"
    per_doc_tree: dict,
    file_path: str,                 # Original PDF/MD path
) -> str:
```

**Implementation:**

1. Parse `node_ref` to get `doc_id` and `node_id`.
2. Traverse the `per_doc_tree` to find the node with matching `node_id`.
3. Get `start_index` and `end_index` from the node (these are 1-based page numbers
   for PDF, or character offsets for MD — match how PageIndex stores them).
4. For PDF: use `fitz` (PyMuPDF) to extract text from pages
   `start_index-1` to `end_index-1` (0-based fitz indexing).
5. For MD: read the markdown file and extract lines between the appropriate
   heading markers.
6. Return the extracted text string. Prepend a header:
   `f"[{doc_id} :: {node['title']} :: pages {start_index}–{end_index}]\n\n{text}"`.

Also implement:
```python
async def fetch_multiple_nodes(
    node_refs: list[str],
    storage: DocumentStore,
    model_max_tokens: int = 100000,
) -> str:
```

Fetches all nodes, concatenates content. If total exceeds `model_max_tokens * 0.7`
(leave room for prompt + answer), truncate to the most relevant nodes first
(preserve order from navigator output, which is already relevance-ranked).

---

### `retrieval/query_engine.py`

Orchestrates the full 3-step query loop and generates the final answer.

```python
async def query(
    user_query: str,
    master_tree_store: MasterTreeStore,
    storage: DocumentStore,
    arch_map: ArchitectureMap,
    model: str = "gpt-4o-2024-11-20",
    chat_history: list[dict] = None,
    max_docs: int = 3,
    verbose: bool = False,
) -> QueryResult:
```

**`QueryResult` dataclass:**
```python
@dataclass
class QueryResult:
    answer: str
    selected_docs: list[str]          # doc_ids selected by router
    selected_nodes: list[str]         # node_refs selected by navigator
    retrieved_context: str            # Raw text passed to answer LLM
    chat_history_updated: list[dict]  # Updated history with this turn appended
```

**Steps:**

1. **Route**: call `router.route_query(...)` → `selected_doc_ids`.
   If verbose, print: `"Routing to docs: {selected_doc_ids}"`.

2. **Navigate** (run in parallel with `asyncio.gather`):
   For each `doc_id` in `selected_doc_ids`:
   - Load per-doc tree from `storage.load_doc_tree(doc_id)`
   - Call `navigator.navigate_doc_tree(query, doc_id, tree, ...)`
   Collect all `node_refs` across all docs.

3. **Fetch**: call `fetcher.fetch_multiple_nodes(all_node_refs, storage)`.

4. **Answer**: make a final LLM call:

```
System:
You are an expert assistant for the Sales Intelligence Platform (SIP).
Answer the user's question using ONLY the retrieved document context below.
Be precise and reference specific sections when helpful.
If the context does not contain sufficient information to answer, say so clearly.

Retrieved Context:
{retrieved_context}

Architecture Map:
{arch_map_context}

User:
{user_query}

{chat_history_block}
```

5. Append this turn to `chat_history` and return `QueryResult`.

---

### `storage/store.py`

Simple file-based storage. No database.

```python
class DocumentStore:
    def __init__(self, data_dir: str):
        self.data_dir = data_dir
        self.trees_dir = os.path.join(data_dir, "doc_trees")
        os.makedirs(self.trees_dir, exist_ok=True)

    def save_doc_tree(self, doc_id: str, tree: dict) -> str:
        # Save to data/doc_trees/{doc_id}_tree.json, return path
        ...

    def load_doc_tree(self, doc_id: str) -> dict:
        # Load from data/doc_trees/{doc_id}_tree.json
        # Raise FileNotFoundError with clear message if not found
        ...

    def doc_tree_exists(self, doc_id: str) -> bool: ...

    def get_doc_file_path(self, master_node: MasterNode) -> str:
        # Return the original file path stored in master_node.file_path
        return master_node.file_path
```

---

### `cli.py`

Use `click`. Implement four commands:

**`ingest`**:
```
python cli.py ingest \
  --file path/to/doc.pdf \
  --doc-id sip_auth_spec_v2 \
  --title "SIP Auth & RBAC Technical Specification v2" \
  --doc-type technical_spec \
  [--model gpt-4o-2024-11-20]
```

**`query`**:
```
python cli.py query "What is the token refresh flow for the SIP Auth module?"
  [--max-docs 3]
  [--model gpt-4o-2024-11-20]
  [--verbose]
```

For `query`, implement a REPL loop: after the first answer is printed, prompt
`"Follow-up (or 'exit'): "` and maintain `chat_history` across turns within
the same session.

**`list-docs`**:
```
python cli.py list-docs
```
Print a `rich` table with columns: doc_id, doc_title, doc_type, ingested_at.

**`show-master-tree`**:
```
python cli.py show-master-tree [--doc-id sip_auth_spec_v2]
```
Pretty-print the full master tree JSON, or a single node if `--doc-id` given.

---

## Key Implementation Details and Constraints

### Node ID namespacing
PageIndex generates `node_id` values like `"0003"` — these collide across documents.
All internal references must use compound keys: `"{doc_id}::{node_id}"`.
The `navigator.py` LLM is asked to return raw `node_id` values; your code
wraps them into `node_refs` before storing or passing forward.

### PageIndex integration — exact API calls
- **PDF**: `from pageindex.page_index import page_index_main`
  Call signature: `page_index_main(pdf_path, opt)` where `opt` is a config
  object created by `ConfigLoader().load(user_opt_dict)`.
  Returns: the tree dict directly (not a path — do not re-read from disk).
  Note: `page_index_main` is async — call with `await` or wrap in `asyncio.run()`.

- **Markdown**: `from pageindex.page_index_md import md_to_tree`
  Call via: `asyncio.run(md_to_tree(md_path, opt))`.
  Returns: the tree dict.

- **ConfigLoader**: `from pageindex.utils import ConfigLoader`
  Usage:
  ```python
  config_loader = ConfigLoader()
  opt = config_loader.load({
      'model': model,
      'if_add_node_summary': 'yes',
      'if_add_node_id': 'yes',
      'if_add_doc_description': 'no',
  })
  ```

### LLM calls
All LLM calls use the OpenAI Python SDK (`openai.AsyncOpenAI`). Use
`CHATGPT_API_KEY` as the api_key (PageIndex convention):

```python
import os
from openai import AsyncOpenAI

client = AsyncOpenAI(api_key=os.environ["CHATGPT_API_KEY"])

response = await client.chat.completions.create(
    model=model,
    messages=[
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ],
    temperature=0,
)
return response.choices[0].message.content.strip()
```

Always `temperature=0` for all routing, navigation, and master node generation
calls. The final answer generation call may use `temperature=0.1`.

### Chat history format
Pass history as a list of `{"role": "user"/"assistant", "content": "..."}` dicts.
When building the `chat_history_block` for a prompt:

```python
def format_chat_history(history: list[dict]) -> str:
    if not history:
        return ""
    lines = ["Prior conversation context:"]
    for turn in history[-6:]:   # Last 6 turns max to avoid context bloat
        role = "User" if turn["role"] == "user" else "Assistant"
        lines.append(f"{role}: {turn['content']}")
    return "\n".join(lines)
```

### Error handling
- If PageIndex fails (LLM error, PDF parse error), catch and re-raise with a clear
  message that includes the doc_id and original error.
- If master node generation returns invalid JSON, retry once, then raise.
- If a `node_id` returned by the navigator does not exist in the tree, log a warning
  and skip it (do not crash).
- If `arch_map.json` is missing, `ArchitectureMap.to_llm_context()` returns `""`
  (graceful degradation).

### Per-doc tree serialization for LLM prompts
When passing `per_doc_tree` to LLM prompts, serialize it without `node_text`
fields (these are full page texts and would overflow the context). Strip any key
named `"node_text"` or `"text"` recursively before serializing.

```python
def strip_text_fields(tree: dict) -> dict:
    """Recursively remove full text fields to keep tree compact for LLM input."""
    stripped = {k: v for k, v in tree.items() if k not in ("node_text", "text")}
    if "nodes" in stripped:
        stripped["nodes"] = [strip_text_fields(n) for n in stripped["nodes"]]
    return stripped
```

---

## Testing

Write pytest tests in `tests/`. Use real fixture JSON files in `tests/fixtures/`.

**`test_ingestion.py`**: Mock the PageIndex call and the LLM call. Test that
`ingest_document` correctly saves the tree, generates a master node with the
right shape, and updates the master tree.

**`test_master_tree.py`**: Test `MasterTreeStore` add/get/list/save/load roundtrip.
Test `to_llm_context()` omits operational fields.

**`test_retrieval.py`**: Mock the LLM calls in router/navigator. Test that:
- Router returns valid doc_ids from the master tree
- Navigator returns valid node_refs
- `query()` correctly sequences the three steps
- Chat history is correctly appended to `QueryResult`

---

## README

Write a `README.md` covering:

1. Architecture diagram (ASCII) showing: Docs → PageIndex → per-doc trees →
   Master Node Gen → Master Tree → Query Engine (Router → Navigator → Fetcher → Answer)
2. Setup instructions (clone PageIndex, install deps, set `.env`, populate `arch_map.json`)
3. Ingestion example with CLI command
4. Query example with CLI command and REPL
5. Python API usage example (import `query_engine.query` directly)
6. Description of `arch_map.json` and how to populate it for SIP

---

## What NOT to Build

- No vector database, no embeddings, no similarity search — this is fully reasoning-based.
- No web server / API server — CLI and Python API only (can be added later).
- No authentication or multi-user support.
- No document clustering or automatic taxonomy — the master tree is a flat list.
- Do not modify the PageIndex source code — treat it as a dependency.
