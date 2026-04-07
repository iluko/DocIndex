# Ingestion Pipeline

Primary code:

- `ingestion/ingest.py`
- `ingestion/image_extractor.py`
- `ingestion/image_analyzer.py`
- `ingestion/pdf_enricher.py`
- `master_tree/generator.py`
- `master_tree/relationships.py`

## Purpose

The ingestion pipeline turns one source document into:

- a per-document PageIndex tree
- a master-tree routing node
- a source-path record
- optional derived markdown
- optional extracted image artifacts
- an ingestion trace

## Supported Inputs

The current ingestion entrypoints accept:

- PDF
- Markdown
- `.md`
- `.markdown`
- DOCX

The main async entrypoints are:

- `ingest_document()`
- `ingest_document_with_trace()`

Both now accept `contains_images: bool = False`.

## End-To-End Flow

1. Validate `doc_id`, file path, and runtime inputs.
2. Resolve ingestion-time defaults such as `top_sections_target` and `relationship_mode`.
3. Preprocess the source:
   - DOCX becomes derived markdown.
   - PDF can optionally go through image extraction and enrichment.
4. Build a PageIndex tree from the resolved retrieval path.
5. Persist the per-document tree.
6. Register the source path and retrieval path.
7. Generate the master node from the tree.
8. Add the node to the master tree.
9. Reconcile `related_docs` if relationship maintenance is enabled.
10. Save the master tree and return ingestion outputs plus trace data.

## PageIndex Boundary

The repo does not hard-wire PageIndex imports at module load time.

Instead, `ingestion/ingest.py` uses a lazy dependency loader that:

- imports vendor PageIndex modules only when needed
- patches PageIndex LLM helpers to use the repo's configured clients
- patches PageIndex progress hooks so ingestion progress can be surfaced in the UI

### Tree builders used

- PDFs go through PageIndex's `page_index_main`
- markdown-derived sources go through `md_to_tree`

## DOCX Path

DOCX files are converted to markdown before tree construction.

That derived markdown is stored under the project index and becomes the retrieval-time source path. The original DOCX path is still retained in source metadata.

## Image-Enriched PDF Path

This is the newest ingestion branch in the codebase.

It is activated only when both conditions are true:

- the source is a PDF
- `contains_images=True`

### Step 1: image extraction

`ingestion/image_extractor.py` uses PyMuPDF (`fitz`) to pull embedded images from the PDF.

Current behavior:

- each extracted image gets an `img_id`
- page number, MIME type, width, and height are preserved
- images smaller than 80px in either dimension are skipped

### Step 2: image analysis

`ingestion/image_analyzer.py` sends one image at a time to a vision-capable chat completion call.

The analyzer asks for:

- image type classification
- a plain-language description
- structured content extraction
- extracted text where present

Current image types:

- `table`
- `flowchart`
- `architecture_diagram`
- `chart`
- `screenshot`
- `photograph`
- `equation`
- `other`

### Step 3: enriched markdown generation

`ingestion/pdf_enricher.py` rebuilds the PDF as page-wise markdown text and inserts structured `IMAGE_REF` blocks that point to stored image files plus the model-generated analysis.

That enriched markdown becomes the retrieval path used by PageIndex for the rest of ingestion.

### Step 4: image persistence

The local document store writes extracted images under the project's `images/` directory.

These stored paths are then referenced by the `IMAGE_REF` blocks so later query results can map retrieved content back to image files.

### Query-time effect

If retrieved context contains `IMAGE_REF` blocks, `retrieval/query_engine.py` parses them into `QueryResult.image_refs`.

The legacy Streamlit UI then renders the linked images directly in chat.

## Ingestion Trace

`ingest_document_with_trace()` returns an `IngestionResult` with:

- `master_node`
- `per_doc_tree`
- `trace`

The trace contains:

- input file path and type
- tree output path
- high-level ingestion steps
- relationship mode
- optional relationship reconciliation details

Progress callbacks can also receive event dictionaries while ingestion is running.

## Default PageIndex Build Options

The human reference in `config.yaml` documents the hard-coded PageIndex defaults used during ingestion:

- add node IDs
- add node summaries
- do not add doc descriptions
- max pages per node: `10`
- max tokens per node: `20000`

Those can still be overridden when calling ingestion directly with `pageindex_opts`.

## Outputs Written By Ingestion

| Output | Written by | Notes |
| --- | --- | --- |
| Per-document tree JSON | `storage.save_doc_tree()` | Canonical retrieval structure |
| Source registry entry | `storage.register_doc_source()` | Tracks original and retrieval-time path |
| Derived markdown | `storage.save_derived_markdown()` | Used for DOCX and image-enriched PDF paths |
| Extracted images | `storage.save_image()` | Local-store-only today |
| Master node | `master_tree_store.add_node()` | Routing metadata for the whole project |

## Relationship Maintenance

After the new master node is generated, ingestion can optionally reconcile `related_docs`.

Modes:

- `off`
- `basic`
- `enhanced`

The detailed logic is documented in `master-tree-and-relationships.md`.

## Interface Surface Differences

The code paths are not surfaced equally across interfaces.

### Exposed today

- Streamlit main app: yes, includes the `contains_images` checkbox for PDFs
- direct Python call: yes, via `contains_images` parameter

### Not exposed today

- CLI: no explicit `--contains-images` flag
- FastAPI ingestion contract: no `contains_images` form field
- React ingestion UI: no image-aware ingestion control

That means the backend capability exists, but only the Streamlit UI currently exposes it end to end.

## Important Constraints

- The image-aware path currently depends on local image storage helpers and is not fully abstracted behind `AbstractDocumentStore`.
- The PageIndex dependency is still vendor code, so the repo patches it at runtime rather than owning all of the lower-level tree-building logic.
- Relationship enrichment can add extra LLM cost at ingestion time.
- Enriched PDFs improve diagram/chart retrievability, but they add ingestion latency and extra model calls.
