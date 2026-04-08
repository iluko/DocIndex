# Storage And Index Layout

Primary code:

- `storage/base.py`
- `storage/store.py`
- `storage/factory.py`
- `storage/mongo_store.py`
- `index_registry.py`
- `traces/store.py`
- `experiments/layout.py`

## Purpose

The storage layer persists the artifacts produced by ingestion and consumed by retrieval.

There are three different storage stories in this repository:

1. main-runtime knowledge artifacts
2. main-runtime trace artifacts
3. experiments artifacts

Keeping those separate is essential to understanding why certain delete flows, UI views, and experiment runs behave the way they do.

## Main Runtime Local Layout

```text
data/
  indexes/
    {project}/
      master_tree.json
      index_meta.json
      doc_sources.json
      doc_trees/
        {doc_id}_tree.json
      derived_markdown/
        {doc_id}.md
      images/
        {doc_id}/...
  uploads/
    {doc_id}_{random8}.{ext}
  traces/
    {project}/
      {timestamp}_{trace_id}.json
  model_registry.json
```

## What Lives Where

### `data/indexes/{project}/`

This is the active knowledge base for one project.

It stores:

- the master tree
- per-document trees
- source-path registry
- derived markdown
- image artifacts
- lightweight index metadata

### `data/uploads/`

This is a temporary operational staging area used by the FastAPI adapter when users upload files through the API.

The uploaded file is first written here, then the ingestion pipeline is run against that saved path.

### `data/traces/{project}/`

This stores one query audit trace file per completed query for that project.

Traces are not part of the knowledge index. They are execution records.

### `data/model_registry.json`

This stores user-visible model names and deployment labels.

It is global to the whole runtime, not project-scoped.

## `DocumentStore` Responsibilities

The local `DocumentStore` is the file-based implementation for knowledge artifacts.

It is responsible for:

- saving and loading per-document PageIndex trees
- saving derived markdown
- registering original and retrieval paths
- loading the retrieval-time path for fetchers
- deleting per-document artifacts
- saving and loading extracted images

The document store is not just a dumb file wrapper. It also encodes the path indirection that makes DOCX conversion and image-enriched PDFs work cleanly.

## Source Path vs Retrieval Path

This is one of the most important storage nuances in the codebase.

For each document, the store can record both:

- `source_path`
- `retrieval_path`

Why both exist:

| Case | `source_path` | `retrieval_path` |
| --- | --- | --- |
| plain markdown | original markdown file | same file |
| DOCX | original `.docx` file | derived markdown |
| standard PDF | original `.pdf` file | same file |
| image-enriched PDF | original `.pdf` file | enriched markdown |

At query time, the fetcher reads from `retrieval_path`, not necessarily from the original source.

That is how the rest of the retrieval stack stays simple even though the ingestion path may have transformed the document first.

## Portable Path Semantics

The local store tries to persist paths relative to the base `data/` directory when possible.

That means:

- if a stored path lives under the `data/` tree, it can be stored portably
- if it lives outside the `data/` tree, it is kept as an absolute path

This makes the local data directory more portable when copied between machines.

## Per-Document Trees

Each ingested document tree is stored as:

```text
doc_trees/{doc_id}_tree.json
```

This is the canonical persisted PageIndex tree used by:

- document inspection
- hybrid navigation
- PageIndex agentic tool calls
- experiments PageIndex builds

## Derived Markdown

Derived markdown is stored under:

```text
derived_markdown/{doc_id}.md
```

It can come from:

- DOCX conversion
- PDF image enrichment

It is a first-class retrieval artifact, not just a debugging convenience.

## Image Storage

When image-aware PDF ingestion runs, extracted images are stored under:

```text
images/{doc_id}/{img_id}.{ext}
```

These files are referenced indirectly by the enriched markdown’s `IMAGE_REF` blocks and then surfaced again later when the main Streamlit app renders answers with retrieved images.

## Trace Storage

Audit traces are stored separately from the knowledge index:

```text
data/traces/{project}/{YYYYMMDDTHHMMSS}_{trace_id}.json
```

The timestamp prefix is deliberate. It lets the store list files in reverse lexical order and get “newest first” behavior without a separate index.

## Experiments Artifact Layout

The experiments harness does not write into `data/indexes/`.

Instead it uses:

```text
experiments/artifacts/
  corpora/
  builds/
  runs/
  reports/
  evals/
```

That directory is resolved by `experiments/layout.py`.

### Why this separation matters

It prevents:

- experiment builds from polluting the live app index
- experiment reports from mixing with operational traces
- golden datasets from being confused with live project knowledge

## Mongo Backend

The repo can switch core storage through:

```text
STORAGE_BACKEND=mongodb
```

When that happens, the factories return Mongo-backed implementations for:

- document storage
- master-tree storage
- trace storage

### Important current-state gap

The local `DocumentStore` now includes image helpers such as:

- `save_image()`
- `load_image()`
- `list_images()`

Those helpers are not defined on the abstract base interface. That means the newest image-aware ingestion path is not fully backend-neutral today.

The practical consequence is:

- standard tree/master/source storage is backend-switchable
- image-aware PDF ingestion is currently local-store-oriented

## Project Deletion Semantics

Project deletion is easy to misunderstand because “project” spans more than one directory tree.

### What `delete_project()` currently removes

For the local backend, it removes:

```text
data/indexes/{project}/
```

For Mongo, it removes project-scoped document/master-tree records.

### What it does not remove

It does not remove:

- `data/traces/{project}/`
- global `data/model_registry.json`

This is a real mismatch with some interface copy, especially in the React Inspect page, which currently describes project deletion as also removing traces.

## Flow Guide

### Flow: query-time fetch of one node

1. query pipeline has a `doc_id::node_id`
2. store loads the tree JSON
3. store resolves the retrieval path from `doc_sources.json`
4. fetcher reads the retrieval-time source

### Flow: inspect a document tree

1. caller chooses `doc_id`
2. store loads `doc_trees/{doc_id}_tree.json`
3. UI renders tree JSON or an outline view

### Flow: ingest a transformed document

1. ingestion writes derived markdown or enriched markdown
2. store registers the original source path
3. store also registers the derived retrieval path
4. query-time fetch transparently uses the derived form

### Flow: delete a document

1. remove master-tree node
2. remove tree JSON
3. deregister source path
4. remove derived markdown if present

Image files are not currently covered by the documented per-document delete helper path, so that cleanup story is less complete than the main tree/source/delete path.

## What Is Possible In This Module

This module currently supports:

- project-scoped local knowledge persistence
- project-scoped local trace persistence
- source-vs-retrieval path indirection
- derived markdown storage
- image artifact storage for image-aware ingestion
- Mongo-backed alternatives for the core store families
- fully isolated experiments artifact storage

## Current Constraints

- Image helpers are not fully abstracted across backends.
- Project deletion does not currently clear trace files.
- Different interfaces sometimes imply broader deletion semantics than the actual backend implementation performs.
- Experiments and main-runtime artifacts intentionally live in different storage worlds, so “where is this tree?” depends on whether the document came from the live app or an experiment build.
