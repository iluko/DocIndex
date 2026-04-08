# Testing

Primary code:

- `tests/test_ingestion.py`
- `tests/test_retrieval.py`
- `tests/test_pageindex_engine.py`
- `tests/test_advanced_retrieval.py`
- `tests/test_relationships.py`
- `tests/test_verifier.py`
- `tests/test_master_tree.py`
- `tests/test_index_registry.py`
- `tests/test_model_registry.py`
- `tests/test_utils.py`
- `tests/test_llm_config.py`
- `tests/test_experiments.py`
- `tests/fixtures/sample_doc_tree.json`
- `tests/fixtures/sample_master_tree.json`

## Purpose

The test suite is both a verification layer and an executable architecture guide.

In this repository, many of the most useful behavioral contracts are easiest to understand by reading the tests that pin them down.

That is especially true for:

- project-vs-model runtime rules
- relationship maintenance behavior
- advanced retrieval planning and caps
- experiments run/evaluation semantics

## Recommended Test Commands

Run the full suite:

```bash
pytest
```

Run the highest-signal targeted subsets:

```bash
pytest tests/test_ingestion.py
pytest tests/test_retrieval.py
pytest tests/test_advanced_retrieval.py
pytest tests/test_experiments.py
```

Run a specific module’s contract tests:

```bash
pytest tests/test_pageindex_engine.py
pytest tests/test_relationships.py
pytest tests/test_index_registry.py
```

## Coverage Map By Test File

| Test file | What it protects |
| --- | --- |
| `test_ingestion.py` | ingestion outputs, trace steps, progress callbacks, DOCX conversion, relationship-mode behavior, enhanced fallback |
| `test_retrieval.py` | router/navigator plumbing, query orchestration, explicit mode routing, PageIndex-vs-hybrid trace behavior |
| `test_pageindex_engine.py` | tool parsing, tool-loop behavior, budget exhaustion, explored-doc tracking |
| `test_advanced_retrieval.py` | routing facets, planner parsing, adaptive-width metadata, expansion helpers, advanced config defaults |
| `test_relationships.py` | deterministic cleanup, enhanced candidate behavior, symmetry, bounds, fallback handling |
| `test_verifier.py` | verification decisions and graceful degradation |
| `test_master_tree.py` | master-tree roundtrip and LLM-context serialization |
| `test_index_registry.py` | default project behavior, alias resolution, model-not-index rule, delete-project alias handling |
| `test_model_registry.py` | model registry persistence and validation |
| `test_utils.py` | client compatibility fallbacks, usage tracking, PageIndex patching, env-driven helpers |
| `test_llm_config.py` | provider/model config derivation |
| `test_experiments.py` | corpus/build/run/eval flows, compatibility rules, suite batching, suite metadata attachment, incremental progress |

## High-Value Behaviors The Suite Documents

### Runtime contracts

The suite explicitly protects:

- default project handling
- project alias collapse
- the rule that model choice must not change the index directory

These tests matter because that distinction is central to the whole architecture.

### Ingestion contracts

The suite documents:

- tree persistence
- master-tree updates
- traced ingestion outputs
- progress callback emission
- DOCX conversion behavior
- relationship maintenance behavior for `off`, `basic`, and `enhanced`

### Retrieval contracts

The suite documents:

- router validation of known doc IDs
- navigator output shape
- hybrid-vs-PageIndex execution selection
- query-trace population differences by mode
- fetch behavior across file types

### Advanced retrieval contracts

The suite documents:

- planner parsing and clamping
- advanced config defaults
- routing-facet serialization
- navigator max-node behavior
- structural expansion helpers

### Experiments contracts

The experiments suite is especially important right now because the experiments system has changed a lot.

It documents:

- corpus normalization
- isolated build creation
- PageIndex build reuse of the shared ingestion pipeline
- vector-RAG artifact creation
- compatible entry generation
- suite-linked run metadata persistence
- one-run-per-case batch execution
- incremental progress events for runs and evaluations
- auto use of attached suite/case IDs during evaluation

## Newer Behavior Already Covered

The tests now explicitly cover newer experiments behavior such as:

- suite-driven run metadata via `suite_id` and `case_id`
- incremental suite-batch progress events
- incremental evaluation progress events

Those tests are important because UI responsiveness and batch/eval orchestration are now part of the intended product behavior, not just internal implementation details.

## How To Read Tests As Architecture Docs

If you are trying to understand a module quickly, use this reading order:

1. read the module doc in `docs/modules/`
2. read the matching test file
3. only then dive into the implementation

That order works well in this codebase because the tests often state the intended behavior more directly than the production code.

## Current Gaps

The suite still has meaningful blind spots.

### Lower coverage areas

- image extraction and image analysis internals
- enriched markdown generation and `IMAGE_REF` end-to-end behavior
- FastAPI endpoint integration tests
- React frontend behavior
- full Streamlit UI flows
- Mongo parity for the newer image-related paths

### Why these gaps matter

Many of the newest changes in the repository are exactly in those less-tested areas:

- image-aware ingestion
- interface parity
- live UI progress behavior

So even though the core pipeline is well-tested, some of the newest user-facing branches still rely more on manual validation than ideal.

## Suggested Next Additions

Highest-value test additions would be:

1. unit tests for `image_extractor.py`, `image_analyzer.py`, and `pdf_enricher.py`
2. an end-to-end ingestion test proving `contains_images=True` produces enriched markdown and stored images
3. FastAPI route tests for `/api/query` and `/api/ingest`
4. React request-shape tests around the API client and workspace pages
5. tests that make the current local-vs-Mongo image-storage gap explicit
6. a test that captures the current delete-project vs trace-deletion mismatch so it stops being silently reintroduced

## Practical Notes

- The test suite is designed to avoid real LLM calls whenever possible by monkeypatching model boundaries.
- That keeps tests deterministic and fast.
- If you are changing behavior in `utils.py`, `retrieval/`, `ingestion/`, or `experiments/`, you should almost always update tests in the matching file at the same time.
