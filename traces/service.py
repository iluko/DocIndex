"""TraceService — the single interface consumed by the UI, CLI, and (future) REST API.

This layer is intentionally decoupled from both the storage backend and the
Streamlit/CLI presentation layer so the same logic can be wired up to a separate
frontend without changes.

Usage:
    service = TraceService(store)
    summaries = service.list_traces(limit=20)
    trace    = service.get_trace(trace_id)
    stats    = service.get_stats()
    analysis = await service.run_post_hoc_analysis(trace_id, model="claude-sonnet-4-6")
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
from collections import Counter
from typing import TYPE_CHECKING

from traces.schema import (
    AuditTrace,
    PostHocAnalysis,
    SectionScore,
    TraceStats,
    TraceSummary,
    _estimate_cost,
)

if TYPE_CHECKING:
    from traces.store import LocalTraceStore
    from traces.mongo_store import MongoTraceStore

logger = logging.getLogger(__name__)


class TraceService:
    """Business-logic layer for audit traces.

    Designed to be framework-agnostic:
    - Streamlit can call it directly (sync wrappers where needed).
    - A future FastAPI layer can await the async methods directly.
    - CLI commands can call it via asyncio.run().
    """

    def __init__(self, store: "LocalTraceStore | MongoTraceStore") -> None:
        self._store = store

    # ------------------------------------------------------------------
    # Async write (fire-and-forget from query pipeline)
    # ------------------------------------------------------------------

    async def write_trace(self, trace: AuditTrace) -> None:
        """Persist a trace without blocking the caller.

        Called via asyncio.create_task() so the answer is already returned
        to the user before this completes.
        """
        try:
            await asyncio.to_thread(self._store.save, trace)
        except Exception as exc:  # noqa: BLE001 — trace write must never crash the query
            logger.warning("Audit trace write failed (non-fatal): %s", exc)

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def list_traces(
        self,
        limit: int = 50,
        offset: int = 0,
        search: str | None = None,
    ) -> list[TraceSummary]:
        """Return trace summaries, newest first.

        ``search`` is a case-insensitive substring filter on the query text.
        """
        summaries = self._store.list_summaries(limit=limit if search is None else 500, offset=offset)
        if search:
            q = search.lower()
            summaries = [s for s in summaries if q in s.query_preview.lower()][:limit]
        return summaries

    def get_trace(self, trace_id: str) -> AuditTrace | None:
        return self._store.load(trace_id)

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def get_stats(self, sample_limit: int = 200) -> TraceStats:
        """Compute aggregated statistics across the most recent traces."""
        summaries = self._store.list_summaries(limit=sample_limit)
        n = len(summaries)

        if n == 0:
            return TraceStats(
                total_queries=0,
                avg_latency_seconds=0.0,
                avg_ttft_seconds=0.0,
                avg_tokens_total=0.0,
                avg_docs_routed=0.0,
                avg_sections_retrieved=0.0,
                broadened_routing_rate=0.0,
                truncation_rate=0.0,
                total_estimated_cost_usd=None,
                most_accessed_docs=[],
            )

        avg = lambda vals: sum(vals) / len(vals) if vals else 0.0  # noqa: E731

        latencies = [s.total_seconds for s in summaries]
        ttfts = [s.ttft_seconds for s in summaries]
        tokens = [s.total_tokens for s in summaries]
        docs = [s.docs_routed for s in summaries]
        sections = [s.sections_retrieved for s in summaries]
        broadened = [s for s in summaries if s.routing_broadened]

        costs = [s.estimated_cost_usd for s in summaries if s.estimated_cost_usd is not None]
        total_cost = round(sum(costs), 6) if costs else None

        # Gather most-accessed docs from full traces (best-effort, limited sample)
        doc_counter: Counter = Counter()
        for s in summaries:
            trace = self._store.load(s.trace_id)
            if trace is not None:
                for doc_id in trace.routing.selected_doc_ids:
                    doc_counter[doc_id] += 1

        return TraceStats(
            total_queries=n,
            avg_latency_seconds=round(avg(latencies), 3),
            avg_ttft_seconds=round(avg(ttfts), 3),
            avg_tokens_total=round(avg(tokens), 1),
            avg_docs_routed=round(avg(docs), 2),
            avg_sections_retrieved=round(avg(sections), 2),
            broadened_routing_rate=round(len(broadened) / n, 3),
            truncation_rate=0.0,  # derived from full traces; kept at 0 for perf
            total_estimated_cost_usd=total_cost,
            most_accessed_docs=doc_counter.most_common(10),
        )

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------

    def export_json(self, limit: int | None = None) -> list[dict]:
        """Return all traces as a list of plain dicts (for JSON serialisation)."""
        summaries = self._store.list_summaries(limit=limit or 10_000)
        results: list[dict] = []
        for s in summaries:
            trace = self._store.load(s.trace_id)
            if trace is not None:
                results.append(trace.model_dump(mode="json"))
        return results

    def export_csv(self, limit: int | None = None) -> str:
        """Return a CSV string with one row per trace (summary fields only)."""
        summaries = self._store.list_summaries(limit=limit or 10_000)
        buf = io.StringIO()
        fieldnames = [
            "trace_id", "timestamp", "model", "retrieval_mode",
            "query_preview", "docs_routed", "sections_retrieved",
            "ttft_seconds", "total_seconds", "routing_seconds",
            "total_tokens", "estimated_cost_usd", "routing_broadened",
        ]
        writer = csv.DictWriter(buf, fieldnames=fieldnames)
        writer.writeheader()
        for s in summaries:
            writer.writerow({
                "trace_id": s.trace_id,
                "timestamp": s.timestamp.isoformat(),
                "model": s.model,
                "retrieval_mode": s.retrieval_mode,
                "query_preview": s.query_preview,
                "docs_routed": s.docs_routed,
                "sections_retrieved": s.sections_retrieved,
                "ttft_seconds": s.ttft_seconds,
                "total_seconds": s.total_seconds,
                "routing_seconds": s.routing_seconds,
                "total_tokens": s.total_tokens,
                "estimated_cost_usd": s.estimated_cost_usd,
                "routing_broadened": s.routing_broadened,
            })
        return buf.getvalue()

    # ------------------------------------------------------------------
    # Post-hoc analysis (on-demand, user-initiated)
    # ------------------------------------------------------------------

    async def run_post_hoc_analysis(
        self,
        trace_id: str,
        model: str,
    ) -> PostHocAnalysis | None:
        """Run an LLM analysis explaining routing + section relevance for one trace.

        Uses only data already stored in the trace — no re-retrieval, no master
        tree consultation.  The router's exact context snapshot is replayed so the
        explanation mirrors the original decision rather than the current index state.
        """
        from utils import (
            create_chat_completion_async,
            extract_llm_text,
            get_async_client,
            managed_async_client,
            parse_json_response,
        )

        trace = self._store.load(trace_id)
        if trace is None:
            return None

        # ── Build sections text for relevance scoring ─────────────────────────
        sections_text = ""
        for i, sec in enumerate(trace.sections_used, 1):
            preview = sec.text[:600] + ("…" if len(sec.text) > 600 else "")
            sections_text += (
                f"\n--- Section {i}: {sec.title} ({sec.node_ref}) ---\n{preview}\n"
            )

        routing_mode_note = (
            "broadened mode (strict routing found no direct match — these docs are tangentially related)"
            if trace.routing.broadened
            else "strict mode (docs directly address the query)"
        )

        system_prompt = "You are an AI system auditor. Return only valid JSON, no markdown."

        user_prompt = f"""Analyze this RAG retrieval for audit purposes.

QUERY: "{trace.query}"

=== ROUTING ANALYSIS ===
The router operated in {routing_mode_note}.
The document index the router received:
{trace.routing.context_snapshot}

Router's raw output: {trace.routing.raw_response}
Final selected doc IDs: {trace.routing.selected_doc_ids}

=== SECTION RELEVANCE SCORING ===
The following sections were retrieved and fed to the answer LLM:
{sections_text if sections_text else "(No sections available — PageIndex mode)"}

=== TASK ===
Return JSON with exactly this structure:
{{
  "routing_explanation": "2-4 sentence explanation of WHY these specific docs were selected. Reference specific content from the document index (doc titles, summaries, key topics). Be precise.",
  "section_scores": [
    {{"node_ref": "...", "title": "...", "score": <1-10>, "explanation": "one sentence"}}
  ]
}}

For section_scores: score 10 = directly answers the query, 1 = completely irrelevant.
If no sections are available, return an empty section_scores array."""

        async with managed_async_client(get_async_client()) as client:
            response = await create_chat_completion_async(
                client=client,
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0,
            )
        raw = extract_llm_text(response)
        payload = parse_json_response(raw)

        if not isinstance(payload, dict):
            logger.warning("Post-hoc analysis returned unexpected JSON shape for trace %s", trace_id)
            return None

        section_scores = [
            SectionScore(
                node_ref=item.get("node_ref", ""),
                title=item.get("title", ""),
                score=int(item.get("score", 0)),
                explanation=item.get("explanation", ""),
            )
            for item in (payload.get("section_scores") or [])
            if isinstance(item, dict)
        ]

        return PostHocAnalysis(
            trace_id=trace_id,
            routing_explanation=payload.get("routing_explanation", ""),
            section_scores=section_scores,
            model_used=model,
        )
