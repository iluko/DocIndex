# Internal Mapping

This project now exposes explicit trace objects for both ingestion and querying so you can inspect how the system is reasoning internally.

## Ingestion Flow

```text
source file
  -> validate_input
  -> build_pageindex_tree
  -> persist_tree
  -> generate_master_node
  -> update_master_tree
```

The traced API is `ingest_document_with_trace()` in [ingestion/ingest.py](/Users/iluko/Documents/Personal%20Projects/Hybrid%20Approach/ingestion/ingest.py). It returns:

- `master_node`: the final `MasterNode`
- `per_doc_tree`: the PageIndex tree JSON
- `trace`: structured step-by-step records with compact payloads

Each `IngestionStep` contains:

- `name`: stable step identifier
- `detail`: human-readable description of what happened
- `payload`: compact data snapshot for that step

## Retrieval Flow

```text
user query
  -> route_query()      -> doc_ids
  -> navigate_doc_tree() per selected doc -> node_refs
  -> fetch_multiple_nodes_detailed() -> ordered chunks
  -> answer LLM call using combined chunk context
```

The query trace is returned on `QueryResult.trace` from [retrieval/query_engine.py](/Users/iluko/Documents/Personal%20Projects/Hybrid%20Approach/retrieval/query_engine.py).

`QueryTrace` includes:

- `routed_docs`: the master-tree routing decision
- `navigation`: per-doc selected node refs
- `fetched_chunks`: ordered chunk objects with `node_ref`, `title`, page bounds, token estimate, and truncation flag
- `token_budget`: the chunk budget used before the final answer call
- `truncated`: whether chunk assembly hit the budget

## Chunk Assembly

Chunks are produced in [retrieval/fetcher.py](/Users/iluko/Documents/Personal%20Projects/Hybrid%20Approach/retrieval/fetcher.py).

Rules:

1. Each `node_ref` is resolved to a concrete node in a per-document PageIndex tree.
2. Raw text is extracted from the original source file.
3. A header is prepended in the form `[doc_id :: title :: pages start-end]`.
4. Chunks are concatenated in navigator-ranked order.
5. If the chunk budget is exceeded, the last included chunk is truncated and the process stops.

This is the internal map shown in the Streamlit frontend as well.
