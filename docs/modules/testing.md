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

## Purpose

The pytest suite in this repository acts as both verification and executable architecture documentation.

Many tests are written to describe intended behavior rather than only low-level implementation details.

## Running Tests

Install dev requirements:

```bash
pip install -r requirements-dev.txt
```

Run the full suite:

```bash
pytest
```

Run targeted subsets:

```bash
pytest tests/test_ingestion.py
pytest tests/test_retrieval.py
pytest tests/test_experiments.py
pytest tests/test_advanced_retrieval.py
```

## Test File Map

| Test file | Main responsibility |
| --- | --- |
| `test_ingestion.py` | Ingestion outputs, trace steps, progress callbacks, DOCX conversion, relationship-mode behavior |
| `test_retrieval.py` | Router, navigator, fetcher, query orchestration, retrieval-mode plumbing |
| `test_pageindex_engine.py` | Tool parsing, budgets, explored-doc tracking in PageIndex mode |
| `test_advanced_retrieval.py` | Planner behavior, routing-facet compatibility, adaptive-width metadata, expansion helpers |
| `test_relationships.py` | `related_docs` cleanup, deterministic reconciliation, enhanced-mode fallback logic |
| `test_verifier.py` | Verifier filtering and graceful-failure behavior |
| `test_master_tree.py` | Master-tree storage and schema behavior |
| `test_index_registry.py` | Project resolution, alias handling, runtime context rules |
| `test_model_registry.py` | Model registry persistence and validation |
| `test_utils.py` | Config parsing, compatibility fallbacks, usage tracking, PageIndex patches |
| `test_llm_config.py` | Provider/model config resolution |
| `test_experiments.py` | Corpus creation, build runners, vector baseline behavior, reports, compatibility rules |

Fixtures:

- `tests/fixtures/sample_doc_tree.json`
- `tests/fixtures/sample_master_tree.json`

## What The Suite Documents Well

The current suite gives strong coverage for:

- ingestion behavior
- routing and retrieval orchestration
- advanced retrieval toggles and planner behavior
- relationship maintenance
- PageIndex tool-loop mechanics
- project/runtime resolution
- experiments build and run mechanics

## Current Coverage Gaps

There are notable areas with less explicit coverage today:

- the new image extraction and image analysis modules
- enriched markdown generation and `IMAGE_REF` parsing end to end
- FastAPI endpoint integration
- React frontend behavior
- full Streamlit UI workflows
- Mongo backend parity for new image-related ingestion behavior

Those gaps matter because some of the newest changes are exactly in the image-enriched ingestion path and in interface parity.

## Suggested High-Value Additions

If test coverage is extended, the highest-value additions would be:

1. unit tests for `image_extractor.py`, `image_analyzer.py`, and `pdf_enricher.py`
2. an ingestion test proving `contains_images=True` produces enriched markdown and stored images
3. API tests for `/api/query` and `/api/ingest`
4. interface-level tests for the React frontend request payloads
5. backend tests that make the local vs Mongo image-storage mismatch explicit

## Practical Notes

- The tests are mostly designed to avoid real LLM or vendor PageIndex calls by monkeypatching the integration boundaries.
- That keeps the suite fast and deterministic.
- When reading unfamiliar code, start with the matching test file first. In this repository, the tests are often the clearest expression of intended system behavior.
