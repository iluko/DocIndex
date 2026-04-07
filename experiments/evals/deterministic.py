"""Deterministic phase-1 evaluation helpers for experiment run manifests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from experiments.evals.models import (
    DeterministicScorecard,
    EvaluationCase,
    EvaluationEntryResult,
    EvaluationSuite,
)
from experiments.models import BuildManifest, ComparisonRunManifest, RunEntrySummary


ProgressCallback = Callable[[dict[str, Any]], None]


def _emit_progress(
    progress_callback: ProgressCallback | None,
    event: str,
    **payload: Any,
) -> None:
    if progress_callback is None:
        return
    try:
        progress_callback({"event": event, **payload})
    except Exception:
        return


def _safe_trace_payload(path: str | None) -> dict[str, Any]:
    if not path:
        return {}
    trace_path = Path(path)
    if not trace_path.exists():
        return {}
    try:
        return json.loads(trace_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _normalize_inverse(value: float | int | None, values: list[float]) -> float:
    if value is None:
        return 0.0
    if not values:
        return 100.0
    lo = min(values)
    hi = max(values)
    if hi <= lo:
        return 100.0
    return max(0.0, min(100.0, 100.0 * (hi - float(value)) / (hi - lo)))


def _clip_score(value: float) -> float:
    return max(0.0, min(100.0, value))


def _normalize_question(text: str) -> str:
    return " ".join(text.strip().lower().split())


def _matched_case(run: ComparisonRunManifest, suite: EvaluationSuite) -> EvaluationCase | None:
    if run.suite_id == suite.suite_id and run.case_id:
        matched = suite.case_lookup().get(run.case_id)
        if matched is not None:
            return matched
    expected_case_id = f"run_{run.run_id}"
    for case in suite.cases:
        if case.case_id == expected_case_id:
            return case
    normalized_query = _normalize_question(run.query)
    for case in suite.cases:
        if (
            _normalize_question(case.question) == normalized_query
            and case.question_type == run.question_type
        ):
            return case
    for case in suite.cases:
        if _normalize_question(case.question) == normalized_query:
            return case
    return None


def _shared_metrics(entry: RunEntrySummary) -> dict[str, Any]:
    metrics = entry.metrics
    return {
        "ttft_seconds": float(metrics.ttft_seconds) if metrics else None,
        "total_time_seconds": float(metrics.total_time_seconds) if metrics else None,
        "retrieval_time_seconds": float(metrics.retrieval_time_seconds) if metrics else None,
        "answer_time_seconds": float(metrics.answer_time_seconds) if metrics else None,
        "prompt_tokens": int(metrics.prompt_tokens) if metrics else None,
        "completion_tokens": int(metrics.completion_tokens) if metrics else None,
        "total_tokens": int(metrics.total_tokens) if metrics else None,
        "llm_calls": int(metrics.llm_calls) if metrics else None,
        "retrieved_context_tokens": int(metrics.retrieved_context_tokens) if metrics else None,
        "selected_docs_count": len(entry.selected_docs),
        "selected_nodes_count": len(entry.selected_nodes),
        "source_count": len(entry.sources),
    }


def _diagnostics(
    entry: RunEntrySummary,
    trace_payload: dict[str, Any],
) -> dict[str, Any]:
    retrieval_trace = trace_payload.get("retrieval_trace", {}) if trace_payload else {}
    retrieval_metrics = trace_payload.get("retrieval_metrics", {}) if trace_payload else {}
    profile = trace_payload.get("retrieval_profile", {}) if trace_payload else {}
    mode = (
        retrieval_trace.get("mode")
        or profile.get("mode")
        or entry.retrieval_profile_id
    )

    diagnostics: dict[str, Any] = {
        "mode": mode,
        "advanced_retrieval": bool(
            retrieval_trace.get("advanced_retrieval")
            or profile.get("advanced_retrieval")
        ),
        "routing_broadened": bool(retrieval_trace.get("routing_broadened", False)),
        "effective_max_docs": retrieval_trace.get("effective_max_docs"),
        "effective_max_nodes": retrieval_trace.get("effective_max_nodes"),
        "verification_applied": bool(retrieval_trace.get("verification_applied", False)),
        "tool_calls_made": retrieval_trace.get("tool_calls_made"),
        "tool_call_budget": retrieval_trace.get("tool_call_budget"),
        "content_tokens_used": retrieval_trace.get("content_tokens_used"),
        "content_token_budget": retrieval_trace.get("content_token_budget"),
        "tool_budget_exhausted": bool(retrieval_trace.get("tool_budget_exhausted", False)),
        "content_budget_exhausted": bool(
            retrieval_trace.get("content_budget_exhausted", False)
        ),
        "explored_docs_count": len(retrieval_trace.get("explored_docs", []) or []),
        "vector_backend": retrieval_trace.get("vector_backend"),
        "query_embedding_time_seconds": retrieval_metrics.get("query_embedding_time_seconds"),
        "ann_search_time_seconds": retrieval_metrics.get("ann_search_time_seconds"),
        "lexical_search_time_seconds": retrieval_metrics.get("lexical_search_time_seconds"),
        "rerank_time_seconds": retrieval_metrics.get("rerank_time_seconds"),
        "candidate_chunks": retrieval_metrics.get("candidate_chunks"),
        "selected_chunks": retrieval_metrics.get("selected_chunks"),
        "expanded_node_refs_count": len(retrieval_trace.get("expanded_node_refs", []) or []),
        "primary_node_refs_count": len(retrieval_trace.get("primary_node_refs", []) or []),
        "truncated": bool(retrieval_trace.get("truncated", False)),
    }
    return diagnostics


def _build_scorecard(
    *,
    entry: RunEntrySummary,
    metrics: dict[str, Any],
    diagnostics: dict[str, Any],
    peer_bounds: dict[str, list[float]],
) -> DeterministicScorecard:
    notes: list[str] = []

    if entry.status != "completed" or entry.metrics is None:
        reliability = 20.0 if entry.status == "skipped" else 0.0
        technical = 0.35 * reliability
        if entry.error:
            notes.append(entry.error)
        return DeterministicScorecard(
            technical_score=round(technical, 2),
            efficiency_score=0.0,
            reliability_score=round(reliability, 2),
            retrieval_discipline_score=0.0,
            notes=notes,
        )

    efficiency = (
        _normalize_inverse(metrics.get("ttft_seconds"), peer_bounds["ttft_seconds"])
        + _normalize_inverse(metrics.get("total_time_seconds"), peer_bounds["total_time_seconds"])
        + _normalize_inverse(metrics.get("total_tokens"), peer_bounds["total_tokens"])
        + _normalize_inverse(metrics.get("llm_calls"), peer_bounds["llm_calls"])
    ) / 4.0

    reliability = 100.0
    if diagnostics.get("tool_budget_exhausted"):
        reliability -= 12.0
        notes.append("Tool-call budget exhausted.")
    if diagnostics.get("content_budget_exhausted"):
        reliability -= 12.0
        notes.append("Content-token budget exhausted.")
    if diagnostics.get("routing_broadened"):
        reliability -= 4.0
        notes.append("Broadened routing fallback was needed.")
    if diagnostics.get("truncated"):
        reliability -= 6.0
        notes.append("Fetched context was truncated.")
    reliability = _clip_score(reliability)

    retrieval_discipline = (
        _normalize_inverse(
            metrics.get("retrieved_context_tokens"),
            peer_bounds["retrieved_context_tokens"],
        )
        + _normalize_inverse(
            metrics.get("selected_docs_count"),
            peer_bounds["selected_docs_count"],
        )
        + _normalize_inverse(
            metrics.get("selected_nodes_count"),
            peer_bounds["selected_nodes_count"],
        )
    ) / 3.0
    if diagnostics.get("routing_broadened"):
        retrieval_discipline -= 4.0
    if diagnostics.get("tool_budget_exhausted") or diagnostics.get("content_budget_exhausted"):
        retrieval_discipline -= 4.0
    retrieval_discipline = _clip_score(retrieval_discipline)

    technical = (
        (0.45 * efficiency)
        + (0.35 * reliability)
        + (0.20 * retrieval_discipline)
    )
    return DeterministicScorecard(
        technical_score=round(_clip_score(technical), 2),
        efficiency_score=round(_clip_score(efficiency), 2),
        reliability_score=round(reliability, 2),
        retrieval_discipline_score=round(retrieval_discipline, 2),
        notes=notes,
    )


def evaluate_runs_deterministically(
    *,
    runs: list[ComparisonRunManifest],
    suite: EvaluationSuite,
    build_lookup: dict[str, BuildManifest] | None = None,
    progress_callback: ProgressCallback | None = None,
) -> list[EvaluationEntryResult]:
    """Convert comparison-run manifests into deterministic evaluation results."""
    build_lookup = build_lookup or {}
    total_entries = sum(len(run.entries) for run in runs)
    _emit_progress(
        progress_callback,
        "deterministic_started",
        total_entries=total_entries,
        total_runs=len(runs),
    )
    completed_entries = [
        entry
        for run in runs
        for entry in run.entries
        if entry.status == "completed" and entry.metrics is not None
    ]
    peer_bounds = {
        "ttft_seconds": [float(entry.metrics.ttft_seconds) for entry in completed_entries],
        "total_time_seconds": [float(entry.metrics.total_time_seconds) for entry in completed_entries],
        "total_tokens": [float(entry.metrics.total_tokens) for entry in completed_entries],
        "llm_calls": [float(entry.metrics.llm_calls) for entry in completed_entries],
        "retrieved_context_tokens": [
            float(entry.metrics.retrieved_context_tokens) for entry in completed_entries
        ],
        "selected_docs_count": [float(len(entry.selected_docs)) for entry in completed_entries],
        "selected_nodes_count": [float(len(entry.selected_nodes)) for entry in completed_entries],
    }

    results: list[EvaluationEntryResult] = []
    processed_entries = 0
    for run in runs:
        case = _matched_case(run, suite)
        case_id = case.case_id if case else f"run_{run.run_id}"
        for entry in run.entries:
            trace_payload = _safe_trace_payload(entry.trace_path)
            metrics = _shared_metrics(entry)
            diagnostics = _diagnostics(entry, trace_payload)
            build = build_lookup.get(entry.build_id)
            scorecard = _build_scorecard(
                entry=entry,
                metrics=metrics,
                diagnostics=diagnostics,
                peer_bounds=peer_bounds,
            )
            result = EvaluationEntryResult(
                    source_run_id=run.run_id,
                    source_run_title=run.title,
                    case_id=case_id,
                    question=run.query,
                    question_type=case.question_type if case else run.question_type,
                    business_scenario=case.business_scenario if case else None,
                    target_persona=case.target_persona if case else None,
                    expected_answerability=(
                        case.expected_answerability if case else "unknown"
                    ),
                    ground_truth_available=bool(case.has_ground_truth) if case else False,
                    gold_sources_available=bool(case.has_gold_sources) if case else False,
                    build_id=entry.build_id,
                    build_label=build.build_label if build else None,
                    corpus_id=build.corpus_id if build else None,
                    artifact_family=entry.artifact_family,
                    retrieval_profile_id=entry.retrieval_profile_id,
                    answer_profile_id=entry.answer_profile_id,
                    label=entry.label,
                    status=entry.status,
                    operator_winner=(run.operator_winner_label == entry.label),
                    fastest_proxy=(run.summary.fastest_entry_label == entry.label)
                    if run.summary
                    else False,
                    lowest_tokens_proxy=(run.summary.lowest_tokens_entry_label == entry.label)
                    if run.summary
                    else False,
                    answer_preview=(entry.answer or "")[:500],
                    trace_path=entry.trace_path,
                    source_count=len(entry.sources),
                    metrics=metrics,
                    diagnostics=diagnostics,
                    scorecard=scorecard,
                    error=entry.error,
                )
            results.append(result)
            processed_entries += 1
            _emit_progress(
                progress_callback,
                "deterministic_entry_completed",
                processed_entries=processed_entries,
                total_entries=total_entries,
                source_run_id=result.source_run_id,
                case_id=result.case_id,
                label=result.label,
                status=result.status,
                technical_score=(
                    result.scorecard.technical_score if result.scorecard is not None else None
                ),
            )
    _emit_progress(
        progress_callback,
        "deterministic_completed",
        total_entries=total_entries,
        total_runs=len(runs),
    )
    return results
