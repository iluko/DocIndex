# Master Tree And Relationships

Primary code:

- `master_tree/schema.py`
- `master_tree/master_tree.py`
- `master_tree/relationships.py`
- `master_tree/factory.py`
- `master_tree/mongo_master_tree.py`
- `ingestion/master_node_gen.py`

## Purpose

The master tree is the cross-document routing layer.

It does not store full document content. It stores one compact, high-signal `MasterNode` per ingested document so the router can decide which documents are worth opening for a given query.

If per-document PageIndex trees are the document-detail index, the master tree is the project-level directory.

## What A Master Node Represents

A `MasterNode` is the routing identity of one document.

It packages together:

- stable document identity
- a concise document summary
- topic and category cues
- selected “top sections” from the document tree
- optional routing facets
- related-document hints

This is the representation the router sees before any document tree is opened.

## Why The Master Tree Exists At All

Without the master tree, the system would have to inspect every document tree for every query, which would be too slow and too expensive.

The master tree solves that by separating retrieval into two stages:

1. document-level routing
2. within-document navigation

That design is why master-node generation quality matters so much. Weak master nodes produce weak document routing even if the underlying PageIndex trees are good.

## Core Schema Fields

The exact schema lives in `master_tree/schema.py`, but the most important fields are:

| Field | Role |
| --- | --- |
| `doc_id` | stable machine identifier |
| `doc_title` | human-readable label |
| `doc_type` | descriptive category |
| `file_path` | original source path |
| `tree_path` | persisted per-document tree path |
| `doc_summary` | dense routing summary of the whole document |
| `key_topics` | fast topical cues |
| `relevance_hints` | best-use and not-use guidance |
| `top_sections` | selected high-value node refs and summaries |
| `related_docs` | cross-document adjacency hints |
| `routing_facets` | structured workflow/system/actor/edge-case metadata |
| `ingested_at` | timestamp of node creation |

## `relevance_hints`

This field gives the router more operational guidance than a plain summary can.

It can encode:

- what kinds of questions the document is useful for
- what kinds of questions it is not useful for
- high-level key categories

That lets the router reason about fit, not just similarity.

## `top_sections`

`top_sections` are the most important node refs surfaced from the document tree into the master node.

They serve two purposes:

1. they make the document’s most relevant internal regions visible at routing time
2. they provide a fallback when the navigator later fails to choose nodes confidently

This makes `top_sections` a bridge between document-level routing and node-level retrieval.

## `routing_facets`

This is newer structured metadata designed to make routing more precise without dumping the full tree into the router prompt.

Possible facet groups include:

- workflows
- actors
- systems
- edge cases
- authority hints

The code intentionally includes only non-empty facet lists in the LLM context so the prompt stays compact.

### Backward compatibility nuance

Older master nodes may not have `routing_facets`. The store and serializer are written so older nodes still load and route correctly.

## What The Router Actually Sees

`MasterTreeStore.to_llm_context()` serializes only routing-relevant fields.

It intentionally omits operational noise such as:

- raw file paths
- timestamps
- storage bookkeeping

The router sees a condensed representation built from:

- document identity
- document summary
- key topics
- relevance hints
- top sections
- related docs
- routing facets when present

That serializer is one of the most important boundaries in the system because it controls the quality and size of the router prompt.

## `MasterTreeStore`

The store is a thin persistence and CRUD layer over the master tree JSON file.

It supports:

- load
- save
- add or upsert a node by `doc_id`
- get one node
- list all docs
- remove one node
- render the tree into LLM routing context

The local implementation writes the full tree to one JSON file. The factory can return a Mongo-backed implementation when the runtime backend is switched.

## Relationship Maintenance

`related_docs` is maintained at ingestion time. It is not primarily a query-time feature today.

The repository currently defines three maintenance modes.

### `off`

Do no reconciliation after master-node generation.

This is the closest behavior to the older implementation and is useful as a baseline.

### `basic`

Run deterministic cleanup over the new document’s local neighborhood.

The affected neighborhood is:

- the new document
- documents it points to
- documents that already point to it

The basic reconciler enforces:

- no self-links
- no unknown doc IDs
- deduplication while preserving order
- bounded list length
- symmetry when capacity allows

This mode uses zero extra LLM calls.

### `enhanced`

Run a bounded LLM-assisted refinement for the new document’s `related_docs`, then run basic cleanup and symmetry.

The algorithm is:

1. score candidate neighbors deterministically from overlap signals
2. shortlist the strongest candidates
3. ask an LLM which candidates are meaningfully related
4. apply the selected set to the new document
5. run basic reconciliation for symmetry and cleanup

If the LLM call fails or the response is invalid, the code falls back to basic mode.

## Deterministic Candidate Scoring In Enhanced Mode

Before the LLM is called, candidate ranking is based on metadata overlap.

Signals include:

- shared `key_topics`
- shared relevance-hint categories
- shared routing facets such as workflows, systems, and actors
- existing links in either direction

Important caps:

- shortlist size: `8`
- max candidates shown to the LLM: `6`
- max related docs per node after cleanup: `10`

These caps are part of the design. Enhanced mode is intentionally bounded rather than open-ended.

## Reconciliation Trace

The reconciliation layer returns a `ReconciliationResult` that is attached to ingestion traces.

It records:

- requested mode
- whether reconciliation ran
- affected document IDs
- before-state links
- after-state links
- enhanced candidate shortlist
- whether the enhanced LLM pass actually ran
- whether fallback to basic was used

This is what lets operators inspect not only that links changed, but why they changed.

## What This Module Does Not Do

This is the important subtlety many readers miss:

- the module builds and cleans `related_docs`
- the module does not yet drive a first-class query-time graph walk over those links

That future direction is explicitly noted in `master_tree/relationships.py` as later-level work.

So `related_docs` is currently:

- router-visible metadata
- operator-visible relationship context
- future expansion scaffolding

It is not yet a dedicated graph-retrieval subsystem.

## Flow Guide

### Flow: ingest one document

1. master-node generation produces an initial `related_docs` list
2. the node is upserted into the master tree
3. optional relationship reconciliation adjusts the local neighborhood
4. the master tree is saved

### Flow: route a query

1. the router receives the serialized master-tree context
2. it chooses documents using document summaries, hints, top sections, and optional routing facets
3. only after that does the system open any document trees

### Flow: delete a document

1. remove the master node by `doc_id`
2. save the master tree
3. remove associated tree/source/derived-markdown artifacts from the document store

### Flow: compare PageIndex relationship variants in experiments

1. `pageindex_base` leaves `related_docs` unreconciled
2. `pageindex_related_basic` applies deterministic cleanup
3. `pageindex_related_enhanced` applies bounded LLM refinement plus cleanup

All three still emit the same artifact family: `pageindex_tree`.

## What Is Possible In This Module

This module can currently:

- persist one compact routing node per document
- expose router-friendly summaries of the whole project
- store structured routing facets
- provide top-section fallback hints
- maintain related-doc metadata deterministically or with a bounded LLM assist
- preserve backward compatibility with older master-tree nodes

## Current Constraints

- Query-time graph traversal over `related_docs` is not yet implemented as a first-class retrieval step.
- Relationship maintenance happens only when a document is ingested or rebuilt.
- The router relies heavily on the quality of the generated master node, so bad summarization here can degrade routing even when the underlying tree is correct.
