# Storage And Index Layout

Primary code:

- `storage/base.py`
- `storage/store.py`
- `storage/factory.py`
- `storage/mongo_store.py`
- `index_registry.py`
- `traces/store.py`

## Purpose

The storage layer persists the artifacts produced by ingestion and consumed by retrieval.

There are two persistence families in the main runtime:

- document/index artifacts
- query audit traces

## Local Filesystem Layout

For the main runtime, the local layout is:

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

## `DocumentStore`

The local `DocumentStore` is responsible for:

- saving and loading per-document trees
- saving derived markdown
- registering source paths
- loading the retrieval-time source path
- deleting document artifacts
- saving and loading extracted images

The source registry is important because the retrieval path is not always the original file path.

Examples:

- DOCX original path, but markdown retrieval path
- PDF original path, but enriched markdown retrieval path when image analysis is enabled

## Local Path Semantics

`register_doc_source()` tracks:

- the original source path
- an optional retrieval path

The fetcher uses `load_doc_source_path()` to find the actual text source it should read during query time.

That indirection is why image-enriched PDFs and DOCX conversion work without changing the rest of the retrieval stack.

## Local Image Storage

The local store now includes an image directory for extracted PDF images.

Those helpers are implemented in `storage/store.py`:

- `save_image()`
- `load_image()`
- `list_images()`

They are used by the image-aware ingestion branch to persist extracted images and later support UI rendering.

## Abstract Interface Gap

`storage/base.py` still defines an abstract interface for:

- trees
- source paths
- derived markdown
- deletion

It does not yet define abstract image storage methods.

That means the new image-enriched ingestion path is not fully backend-abstracted today.

## MongoDB Backend

`storage/factory.py` can switch the document store with:

```text
STORAGE_BACKEND=mongodb
```

The Mongo document store mirrors the core tree/source/derived-markdown responsibilities, but the new local image helpers are not part of the abstract base and are not documented as mirrored in Mongo.

Practically, that means:

- standard ingestion/query storage is backend-switchable
- image-aware PDF ingestion is currently local-store-oriented

## Index Metadata

`index_meta.json` stores lightweight runtime metadata about the active project scope:

- project
- index key
- last used provider
- last used model
- index directory

This file is informational. It does not change index routing behavior, which is still project-driven.

## Project Deletion

Deleting a project removes the entire artifact namespace for that project.

Local backend behavior:

- remove `data/indexes/{project}/`

Mongo backend behavior:

- delete project-scoped documents across the relevant collections

The model registry is not project-scoped and is not deleted with a project.

Important current-state note:

- the current `delete_project()` implementation removes index artifacts, not traces
- local trace files under `data/traces/{project}/` are stored separately and are not deleted by that code path today
- the Mongo delete path likewise targets document and master-tree collections, not trace records

## Trace Storage

Audit traces are stored separately from the document index because they represent query executions, not source knowledge.

Local trace filenames are timestamp-prefixed so simple reverse sort gives newest-first ordering:

```text
data/traces/{project}/{YYYYMMDDTHHMMSS}_{trace_id}.json
```

That format is used by trace listing, stats, and export functions.

## Practical Consequences

- The storage layer preserves the distinction between original source files and retrieval-time text.
- Project isolation is a physical storage boundary, not just a filter in memory.
- The local store is the most complete backend today because it already includes the new image artifact helpers.
- If you add a new ingestion artifact type, the storage abstraction likely needs to be updated in parallel or backend support will drift.
