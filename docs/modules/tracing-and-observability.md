# Tracing And Observability

Primary code:

- `traces/schema.py`
- `traces/service.py`
- `traces/store.py`
- `traces/mongo_store.py`
- `traces/factory.py`
- `retrieval/query_engine.py`

## Purpose

The tracing subsystem records what the retrieval pipeline actually did for a query.

It exists for:

- debugging
- operator inspection
- performance review
- cost review
- post-hoc explanation of routing and section usefulness

The design goal is observability without making the user-facing query path fragile.

## Core Design Principle

Trace writing is intentionally non-blocking and non-fatal.

The query engine can return an answer even if trace persistence later fails. The trace write is treated as best-effort observability, not as part of the critical path for user-facing correctness.

## `AuditTrace`

The root persisted object is `AuditTrace`.

It stores:

- trace ID
- project
- timestamp
- model
- original query
- conversation snapshot
- router trace
- navigation map
- sections used
- final answer
- retrieved-context preview
- retrieval mode
- verification flag
- metrics

This is a deliberately rich object. It is not just a log line or a latency sample.

## Routing Snapshot

`RouterTrace` is especially important because it stores:

- the exact router context snapshot
- the raw router model output
- whether broadened routing was used
- the final selected doc IDs

This matters for post-hoc analysis. The system can later explain the original routing decision using the exact context the router actually saw, rather than re-running analysis against a possibly changed master tree.

## `sections_used`

In hybrid mode, traces can include the actual retrieved sections that were fed into answer generation.

Each `TraceSection` includes:

- `node_ref`
- `doc_id`
- `node_id`
- title
- page start/end
- estimated tokens
- truncation flag
- full stored text

This is what makes later section-level relevance scoring possible.

### Important nuance for PageIndex mode

PageIndex mode does not produce the same section-centric retrieval structure, so `sections_used` may be empty even for successful answers. That is a representation difference, not necessarily a failure.

## `TraceMetrics`

The metrics model records three kinds of information.

### Latency

- TTFT
- total end-to-end time
- routing time
- pipeline time

### Token usage

- prompt tokens
- completion tokens
- total tokens
- LLM calls
- whether usage included estimates

### Retrieval efficiency

- retrieved context tokens
- docs routed
- sections retrieved
- sections dropped by verifier
- whether truncation occurred
- whether routing was broadened
- context-utilization ratio
- retrieval-token ratio
- estimated USD cost

## Cost Estimation

The schema contains static pricing and context-window tables for selected model families.

Important nuance:

- cost estimation is substring-based and best-effort
- if the model name does not match a known family, cost may be `None`

This is intended for approximate observability, not billing accuracy.

## Storage Backends

The trace layer supports:

- local file-based storage
- Mongo-backed storage

The active store is chosen by `traces.factory.create_trace_store()` based on `STORAGE_BACKEND`.

## Local Trace Layout

Local traces are stored as one JSON file per query:

```text
data/traces/{project}/{YYYYMMDDTHHMMSS}_{trace_id}.json
```

That timestamp-prefixed filename supports simple reverse-sort listing.

## `TraceService`

`TraceService` is the business-logic layer over the raw store.

It exposes:

- `write_trace()`
- `list_traces()`
- `get_trace()`
- `get_stats()`
- `export_json()`
- `export_csv()`
- `run_post_hoc_analysis()`

This keeps higher-level logic out of the storage implementation and lets multiple interfaces reuse the same behavior.

## Post-Hoc Analysis

`run_post_hoc_analysis()` is an on-demand LLM analysis over a stored trace.

It does not rerun retrieval. Instead it reuses:

- the stored router context snapshot
- the raw router output
- the stored retrieved sections
- the original answer

It returns:

- a routing explanation
- per-section relevance scores
- analysis timestamp
- model used for the analysis

This is important. Post-hoc analysis is an audit pass over the historical trace, not a second live query against the current index state.

## Aggregated Statistics

`TraceService.get_stats()` computes lightweight project-level summaries from recent traces.

Examples:

- total query count
- average total latency
- average TTFT
- average token usage
- average docs routed
- average sections retrieved
- broadened-routing rate
- total estimated cost
- most accessed documents

This gives operators a cheap view of system behavior without loading every full trace into the UI.

## Interfaces That Use Traces

### Main Streamlit app

Supports:

- trace browsing
- trace detail inspection
- post-hoc analysis

### CLI

Supports:

- trace listing
- full trace inspection
- stats
- JSON and CSV export

### FastAPI

Supports:

- list traces
- fetch one trace
- fetch stats
- run post-hoc analysis

### React frontend

Supports:

- trace list browsing
- full trace detail
- post-hoc analysis

### Experiments harness

Uses a different trace/report story:

- per-entry run traces
- evaluation snapshots
- reports

Those are conceptually similar but not stored in the same `data/traces/` location as main-runtime audit traces.

## Flow Guide

### Flow: write a trace

1. query engine completes answer generation
2. query engine constructs `AuditTrace`
3. `TraceService.write_trace()` is scheduled
4. trace store persists it in the background
5. user already has the answer even if persistence fails

### Flow: inspect a trace

1. caller lists summaries
2. caller selects `trace_id`
3. full trace is loaded
4. UI or CLI renders routing, sections, answer, and metrics

### Flow: run post-hoc analysis

1. caller requests analysis for a stored trace
2. service loads the trace
3. LLM receives only stored trace artifacts
4. analysis result explains routing and scores retrieved sections

### Flow: export traces

1. service lists summaries or full traces
2. output is serialized as JSON or CSV
3. export can be used for offline analysis or reporting

## What Is Possible In This Module

This module can currently:

- persist per-query audit traces
- summarize them cheaply
- export them
- estimate latency and cost behavior
- run an LLM-based audit explanation over a stored trace
- support both local and Mongo-backed stores

## Current Constraints

- PageIndex traces are less section-centric than hybrid traces.
- Cost estimation is approximate and model-name dependent.
- Trace writing is intentionally best-effort, so trace absence does not always imply query failure.
- Project deletion does not currently clean up local trace files, so observability data can outlive the corresponding project index.
