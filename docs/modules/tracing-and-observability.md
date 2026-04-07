# Tracing And Observability

Primary code:

- `traces/schema.py`
- `traces/service.py`
- `traces/store.py`
- `traces/mongo_store.py`
- `traces/factory.py`

## Purpose

Every completed query can emit an audit trace that records what the retrieval system actually did.

The trace layer is designed to support:

- debugging
- UI inspection
- metrics review
- cost review
- post-hoc analysis of routing and retrieved sections

## Trace Model

The root trace object is `AuditTrace`.

It stores:

- `trace_id`
- `project`
- `timestamp`
- `model`
- `query`
- `conversation_snapshot`
- `routing`
- `navigation_map`
- `sections_used`
- `answer`
- `retrieved_context_preview`
- `retrieval_mode`
- `verification_applied`
- `metrics`

### Routing snapshot

`RouterTrace` stores:

- the exact master-tree context shown to the router
- the raw router model output
- whether routing was broadened
- the final selected doc IDs

This is important because post-hoc analysis uses the stored snapshot rather than the current index state.

### Retrieved sections

In hybrid mode, `sections_used` stores the fetched sections that were sent to the answer model.

Each `TraceSection` includes:

- node ref
- doc ID
- node ID
- title
- page range
- estimated tokens
- truncation flag
- full text preview

In PageIndex mode this can be empty because the agentic loop is not storing the same fetched-section structure.

### Metrics

`TraceMetrics` records:

- TTFT
- total latency
- routing latency
- pipeline latency
- prompt, completion, and total tokens
- LLM call count
- whether token usage includes estimates
- context token count
- docs routed
- sections retrieved
- sections dropped by verifier
- truncation flag
- broadened-routing flag
- context utilization and retrieval-token ratio
- estimated USD cost

## Write Path

The query engine can receive a `TraceService`.

When present, it emits an async fire-and-forget write after the answer is ready. Trace-write failure is intentionally non-fatal so observability cannot block the user-facing query result.

## Local Storage Layout

Local traces are stored as one JSON file per query:

```text
data/traces/{project}/{YYYYMMDDTHHMMSS}_{trace_id}.json
```

The timestamp prefix gives cheap newest-first ordering.

## `TraceService`

`TraceService` is the business-logic layer above the raw store.

It exposes:

- `write_trace()`
- `list_traces()`
- `get_trace()`
- `get_stats()`
- `export_json()`
- `export_csv()`
- `run_post_hoc_analysis()`

## Post-Hoc Analysis

`run_post_hoc_analysis()` is an on-demand LLM analysis over a stored trace.

It does not rerun retrieval.

Instead, it reuses:

- the stored router context snapshot
- the raw router output
- the stored sections used in the original answer

The analysis returns:

- a routing explanation
- per-section relevance scores
- generation timestamp
- model used for the analysis

This makes the analysis an audit pass, not a second live query.

## Aggregated Stats

`TraceService.get_stats()` computes lightweight project-level summary metrics from recent traces, including:

- total queries
- average latency and TTFT
- average token usage
- average docs routed
- average sections retrieved
- broadened routing rate
- total estimated cost
- most accessed docs

## Cost Estimation

The trace schema contains static pricing and context-window lookup tables for selected model families.

Cost estimation is substring-based and best-effort:

- if the model name matches a known pricing key, cost is estimated
- otherwise `estimated_cost_usd` is `None`

This is useful for rough comparisons but should not be treated as billing-grade accounting.

## Interface Consumers

The trace layer is consumed by:

- the CLI trace commands
- the Streamlit audit tab
- the React Inspect workspace
- the FastAPI trace endpoints

That separation is deliberate. The same trace logic is reused regardless of interface.

## Practical Notes

- Trace detail is richest for hybrid retrieval because the system stores explicit fetched sections there.
- PageIndex mode still records high-level routing and query metadata, but section-level structure differs.
- Post-hoc analysis depends on stored trace quality, not on current corpus state.
- If a trace looks incomplete, check whether the query was run through an interface that actually supplied a `TraceService`.
