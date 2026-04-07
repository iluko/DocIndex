# Master Tree And Relationships

Primary code:

- `master_tree/schema.py`
- `master_tree/master_tree.py`
- `master_tree/relationships.py`
- `master_tree/factory.py`
- `master_tree/mongo_master_tree.py`

## Purpose

The master tree is the project-level routing index.

It does not store every document chunk. Instead, it stores one summary node per document so the router can decide which documents are worth opening during query time.

## Master Node Schema

Each `MasterNode` stores:

- `doc_id`
- `doc_title`
- `doc_type`
- `file_path`
- `tree_path`
- `doc_summary`
- `key_topics`
- `relevance_hints`
- `top_sections`
- `related_docs`
- `ingested_at`
- `routing_facets` optional bundle

### `relevance_hints`

This sub-structure encodes:

- best use cases
- known non-use cases
- key categories

### `top_sections`

These are pre-selected, high-value sections from the document tree. They serve two purposes:

- richer routing context for the document as a whole
- a fallback when the navigator cannot confidently select nodes

### `routing_facets`

This newer optional metadata bundle adds more structured routing cues:

- workflows
- actors
- systems
- edge cases
- authority hints

It makes the master-tree prompt more informative without loading the full per-document tree.

## Store Responsibilities

`MasterTreeStore` is the local JSON-backed store for:

- loading the project master tree
- adding or updating nodes
- removing nodes
- listing documents
- rendering the tree into the LLM-facing routing context

The factory layer can also return a Mongo-backed implementation when the storage backend is switched.

## What The Router Sees

`MasterTreeStore.to_llm_context()` serializes the master nodes into the routing prompt.

The router therefore sees document-level metadata only, not full node text.

The context can include:

- document title and type
- document summary
- key topics
- relevance hints
- top sections
- related docs
- routing facets when present

This is why the quality of master-node generation matters so much: it is the document-selection layer for the whole project.

## Relationship Maintenance Modes

`master_tree/relationships.py` defines three ingestion-time modes.

### `off`

- no reconciliation
- closest to the original behavior
- preserves whatever links exist after master-node generation

### `basic`

Deterministic cleanup only.

It:

- removes self-links
- removes unknown docs
- deduplicates while preserving order
- caps `related_docs` length
- enforces symmetric direct relationships inside the affected neighborhood

It makes zero extra LLM calls.

### `enhanced`

Enhanced mode adds a bounded LLM-assisted refinement pass before basic cleanup.

The current algorithm is:

1. score candidate neighbors deterministically from metadata overlap
2. shortlist the best candidates
3. ask the LLM which candidates should remain related
4. apply the selected set to the new document
5. run basic reconciliation for symmetry and cleanup

If the LLM step fails, the code falls back to basic mode.

## Candidate Scoring In Enhanced Mode

Candidate ranking is deterministic before the LLM is called.

Signals:

- exact overlap in `key_topics`
- exact overlap in relevance-hint categories
- overlap in routing facets such as workflows, systems, and actors
- bonus when a link already exists in either direction

Hard-coded bounds:

- shortlist computed: `8`
- max candidates shown to LLM: `6`
- max related docs retained per node: `10`

## Reconciliation Trace

Relationship maintenance returns a `ReconciliationResult` that can be embedded in ingestion traces.

It records:

- requested mode
- whether reconciliation actually ran
- affected doc IDs
- before/after `related_docs`
- candidate shortlist
- whether enhanced mode ran
- whether fallback was used

That trace is useful when debugging why links changed after an ingestion run.

## Current Non-Features

The file explicitly calls out future work that is not implemented yet:

- query-time graph-aware expansion over `related_docs`
- richer edge semantics such as strength or dependency type

This is important because `related_docs` today is mostly routing metadata and inspection metadata. It is not yet a first-class graph traversal subsystem in the main query pipeline.

## Practical Implications

- Better master nodes usually matter more than adding more documents to the corpus, because routing starts here.
- `related_docs` maintenance improves metadata hygiene, but it does not automatically widen retrieval at query time.
- `top_sections` gives the runtime a stable fallback when navigation is uncertain.
- `routing_facets` allow the router and experiments planner to encode richer document purpose without loading raw section text.
