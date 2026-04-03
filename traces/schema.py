"""Pydantic models for audit traces.

Every query that passes through the retrieval pipeline produces one AuditTrace.
The trace captures:
  - The exact router inputs/outputs (so post-hoc analysis can mirror the original decision)
  - Every document section that was retrieved, with its full text (for relevance scoring)
  - The conversation context that was active at query time
  - Rich latency and token-usage metrics (TTFT, phase timings, cost estimate, etc.)
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


# ── Pricing / context-window lookup tables ────────────────────────────────────


_MODEL_PRICES_PER_1K: dict[str, tuple[float, float]] = {
    # (input USD/1K tokens, output USD/1K tokens)
    "claude-opus-4-6":          (0.015,    0.075),
    "claude-opus-4":            (0.015,    0.075),
    "claude-sonnet-4-6":        (0.003,    0.015),
    "claude-sonnet-4":          (0.003,    0.015),
    "claude-haiku-4-5":         (0.00025,  0.00125),
    "claude-haiku-4":           (0.00025,  0.00125),
    "gpt-4o":                   (0.005,    0.015),
    "gpt-4o-mini":              (0.000150, 0.000600),
    "o1":                       (0.015,    0.060),
    "o1-mini":                  (0.003,    0.012),
    "o3":                       (0.010,    0.040),
    "o3-mini":                  (0.0011,   0.0044),
    "o4-mini":                  (0.0011,   0.0044),
    "gpt-4-turbo":              (0.010,    0.030),
}

_MODEL_CONTEXT_WINDOWS: dict[str, int] = {
    "claude": 200_000,
    "gpt-4o": 128_000,
    "gpt-4o-mini": 128_000,
    "o1": 200_000,
    "o1-mini": 128_000,
    "o3": 200_000,
    "o3-mini": 200_000,
    "o4-mini": 200_000,
    "gpt-4-turbo": 128_000,
}


def _estimate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float | None:
    """Return estimated USD cost using substring matching against the price table."""
    key = model.lower()
    for known, (inp_price, out_price) in _MODEL_PRICES_PER_1K.items():
        if known in key:
            return round((prompt_tokens * inp_price + completion_tokens * out_price) / 1000, 6)
    return None


def _context_window(model: str) -> int | None:
    key = model.lower()
    for prefix, window in _MODEL_CONTEXT_WINDOWS.items():
        if prefix in key:
            return window
    return None


# ── Sub-models ─────────────────────────────────────────────────────────────────


class TraceSection(BaseModel):
    """One retrieved document section (maps from RetrievedChunk)."""

    node_ref: str
    doc_id: str
    node_id: str
    title: str
    page_start: int
    page_end: int
    estimated_tokens: int
    truncated: bool
    text: str   # stored for post-hoc relevance scoring


class RouterTrace(BaseModel):
    """Exact inputs/outputs of the routing LLM call."""

    context_snapshot: str   # master-tree + arch-map context the router saw
    raw_response: str       # raw LLM text returned by the router
    broadened: bool         # True when strict routing found nothing
    selected_doc_ids: list[str]


class TraceMetrics(BaseModel):
    """Latency, token usage, and derived efficiency metrics."""

    # ── Latency ───────────────────────────────────────────────────────────────
    ttft_seconds: float             # time to first answer token
    total_seconds: float            # end-to-end wall-clock time
    routing_seconds: float          # time spent solely on routing LLM calls
    pipeline_seconds: float         # total − routing (navigation + fetch + generation)

    # ── Token usage ───────────────────────────────────────────────────────────
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    llm_calls: int
    estimated_token_usage: bool     # True when token counts include estimates

    # ── Retrieval efficiency ──────────────────────────────────────────────────
    context_tokens: int             # tokens in the retrieved context fed to answer LLM
    docs_routed: int
    sections_retrieved: int
    sections_dropped_by_verifier: int
    truncation_occurred: bool
    routing_broadened: bool

    # ── Derived ───────────────────────────────────────────────────────────────
    context_utilization_pct: float | None   # context_tokens / model_context_window
    retrieval_token_ratio: float | None     # context_tokens / prompt_tokens
    estimated_cost_usd: float | None


class AuditTrace(BaseModel):
    """Complete record of one query's retrieval pipeline execution."""

    trace_id: str = Field(default_factory=lambda: str(uuid4()))
    project: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    model: str
    query: str
    conversation_snapshot: list[dict[str, Any]] = Field(default_factory=list)
    routing: RouterTrace
    navigation_map: dict[str, list[str]]
    sections_used: list[TraceSection]       # empty for pageindex mode
    answer: str
    retrieved_context_preview: str          # first 500 chars of combined context
    retrieval_mode: str
    verification_applied: bool
    metrics: TraceMetrics


# ── Read-optimised views ───────────────────────────────────────────────────────


class TraceSummary(BaseModel):
    """Lightweight summary row — used for list views and stats computation."""

    trace_id: str
    timestamp: datetime
    query_preview: str      # first 120 characters
    docs_routed: int
    sections_retrieved: int
    ttft_seconds: float
    total_seconds: float
    routing_seconds: float
    total_tokens: int
    estimated_cost_usd: float | None
    routing_broadened: bool
    retrieval_mode: str
    model: str

    @classmethod
    def from_trace(cls, trace: AuditTrace) -> "TraceSummary":
        return cls(
            trace_id=trace.trace_id,
            timestamp=trace.timestamp,
            query_preview=trace.query[:120],
            docs_routed=trace.metrics.docs_routed,
            sections_retrieved=trace.metrics.sections_retrieved,
            ttft_seconds=trace.metrics.ttft_seconds,
            total_seconds=trace.metrics.total_seconds,
            routing_seconds=trace.metrics.routing_seconds,
            total_tokens=trace.metrics.total_tokens,
            estimated_cost_usd=trace.metrics.estimated_cost_usd,
            routing_broadened=trace.metrics.routing_broadened,
            retrieval_mode=trace.retrieval_mode,
            model=trace.model,
        )


class TraceStats(BaseModel):
    """Aggregated statistics across all traces for a project."""

    total_queries: int
    avg_latency_seconds: float
    avg_ttft_seconds: float
    avg_tokens_total: float
    avg_docs_routed: float
    avg_sections_retrieved: float
    broadened_routing_rate: float       # 0–1
    truncation_rate: float              # 0–1
    total_estimated_cost_usd: float | None
    most_accessed_docs: list[tuple[str, int]]   # (doc_id, query_count)


class SectionScore(BaseModel):
    node_ref: str
    title: str
    score: int          # 1–10
    explanation: str


class PostHocAnalysis(BaseModel):
    """Result of an on-demand post-hoc analysis for one trace."""

    model_config = {"protected_namespaces": ()}

    trace_id: str
    routing_explanation: str
    section_scores: list[SectionScore]
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    model_used: str
