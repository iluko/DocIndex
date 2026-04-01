# Multi-Document PageIndex

A reasoning-first, multi-document RAG system built on top of [PageIndex](https://github.com/VectifyAI/PageIndex). It adds a master routing layer across documents, keeps per-document PageIndex trees intact, and answers questions through a two-hop retrieval flow without embeddings or a vector database.

## Architecture

```text
Documents (.pdf/.md/.docx)
        |
        v
   +------------+
   | PageIndex  |
   +------------+
        |
        v
 Per-doc tree JSONs --------------------+
        |                               |
        v                               v
 +------------------+         +------------------+
 | Master Node Gen  | ------> |  Master Tree     |
 +------------------+         +------------------+
                                       |
                                       v
                                +--------------+
                                | Query Engine |
                                +--------------+
                                       |
                 +---------------------+----------------------+
                 v                     v                      v
             Router              Navigator                Fetcher
          (select docs)       (select nodes)         (raw content pull)
                 \_____________________  _____________________/
                                       \/
                                  Final Answer
```

## Setup

1. Clone and install PageIndex into the same virtual environment. It does not need a separate app repo, but keeping it as a sibling checkout is the cleanest approach:

```bash
git clone https://github.com/VectifyAI/PageIndex
pip install -r PageIndex/requirements.txt
```

2. Install this project’s dependencies:

```bash
pip install -r requirements.txt
```

3. Create `.env` from [.env.example](/Users/iluko/Documents/Personal Projects/Hybrid Approach/.env.example).

For Azure OpenAI, a typical setup is:

```env
LLM_PROVIDER=azure
LLM_MODEL=sip-gpt4o-deployment
AZURE_OPENAI_API_KEY=...
AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com
```

For OpenAI, set:

```env
LLM_PROVIDER=openai
OPENAI_API_KEY=...
OPENAI_MODEL=gpt-4o-2024-11-20
```

`CHATGPT_API_KEY` remains relevant because PageIndex reads it directly. This project now backfills it automatically from the active provider config before running PageIndex.

4. Populate the architecture map in [data/arch_map.json](/Users/iluko/Documents/Personal Projects/Hybrid Approach/data/arch_map.json). Fill in each SIP module’s `connects_to` and `description` fields so routing can widen scope intelligently.

## Frontend

A local Streamlit frontend is included for upload + Q&A + internal trace inspection:

```bash
streamlit run app.py
```

The UI supports:

- uploading PDF, Markdown, and DOCX documents into local storage
- running ingestion with a step-by-step internal trace
- asking questions against the indexed corpus
- inspecting routing decisions, selected nodes, fetched chunks, and final retrieved context

The frontend uses the same local JSON/file storage as the CLI. No Supabase or other database is required for the current implementation.

## Azure OpenAI Notes

- Azure is supported for this project’s own LLM calls through the OpenAI-compatible Azure v1 base URL.
- For Azure, the `model` value should be your Azure deployment name, not the raw foundation model identifier.
- PageIndex itself is still an upstream dependency. This project exports compatible environment variables before calling it so it can use the same Azure-backed OpenAI client path.

## Model-Scoped Indexes

Indexes are now scoped by provider + model. That means:

- ingesting with `azure` + `gpt-4.1` writes to a different index than `azure` + `gpt-4o`
- querying only sees documents ingested under the currently selected model scope
- switching models effectively switches to a different corpus unless you ingest or rebuild documents for that model

Stored layout:

```text
data/
  arch_map.json
  uploads/
  indexes/
    azure__gpt_4_1/
      master_tree.json
      doc_sources.json
      doc_trees/
      derived_markdown/
      index_meta.json
```

## Ingestion

Use the CLI to add a document, generate its per-document PageIndex tree, generate the LLM-authored master node, and update the master tree:

```bash
python cli.py ingest \
  --file path/to/doc.pdf \
  --doc-id sip_auth_spec_v2 \
  --title "SIP Auth & RBAC Technical Specification v2" \
  --doc-type technical_spec \
  --model gpt-4o-2024-11-20
```

DOCX files are converted to derived Markdown before PageIndex runs so Word headings can still become navigable tree nodes.

Artifacts are written to:

- [data/master_tree.json](/Users/iluko/Documents/Personal Projects/Hybrid Approach/data/master_tree.json)
- [data/doc_trees](/Users/iluko/Documents/Personal Projects/Hybrid Approach/data/doc_trees)

## Querying

Run a query through the master-tree router, per-document navigator, and raw content fetcher:

```bash
python cli.py query "What is the token refresh flow for the SIP Auth module?" \
  --max-docs 3 \
  --model gpt-4o-2024-11-20 \
  --verbose
```

After the first answer, the CLI keeps a lightweight REPL open:

```text
Follow-up (or 'exit'):
```

Chat history is carried into routing, navigation, and final answer prompts for that session.

## Internal Mapping

If you want the system’s internal reasoning map, see [docs/internal_mapping.md](/Users/iluko/Documents/Personal Projects/Hybrid Approach/docs/internal_mapping.md). The backend now exposes:

- `ingest_document_with_trace()` for ingestion step tracing
- `QueryResult.trace` for routing, navigation, and chunk assembly details

## Python API

You can call the query engine directly:

```python
import asyncio

from arch_map.arch_map import ArchitectureMap
from master_tree.master_tree import MasterTreeStore
from retrieval.query_engine import query
from storage.store import DocumentStore


async def main():
    master_tree_store = MasterTreeStore("data/master_tree.json")
    storage = DocumentStore("data")
    arch_map = ArchitectureMap("data/arch_map.json")

    result = await query(
        user_query="How does SIP handle refresh token rotation?",
        master_tree_store=master_tree_store,
        storage=storage,
        arch_map=arch_map,
        verbose=True,
    )
    print(result.answer)


asyncio.run(main())
```

## Architecture Map

[data/arch_map.json](/Users/iluko/Documents/Personal Projects/Hybrid Approach/data/arch_map.json) is static context injected alongside the master tree during routing and final answer generation. It should describe how SIP modules connect, for example:

- `Auth` connects to `RBAC`, `API Gateway`, and `Compliance & Audit`
- `Agent Orchestrator` connects to `Knowledge Base` and `Feedback Engine`
- `CRM Integration` connects to `Analytics Dashboard` and `Cross-Module Amendment Tracker`

The more accurately you maintain this file, the better the router can widen retrieval beyond a single obvious module when a query crosses subsystem boundaries.
