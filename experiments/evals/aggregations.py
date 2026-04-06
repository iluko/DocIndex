"""Aggregate deterministic evaluation results for reporting and dashboards."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from experiments.evals.models import EvaluationEntryResult, EvaluationSummary


def _mean(values: list[float]) -> float:
    if not values:
        return 0.0
    return float(sum(values) / len(values))


def _rate(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return float(numerator / denominator)


def _completed(entries: list[EvaluationEntryResult]) -> list[EvaluationEntryResult]:
    return [entry for entry in entries if entry.status == "completed" and entry.scorecard is not None]


def _judged(entries: list[EvaluationEntryResult]) -> list[EvaluationEntryResult]:
    return [entry for entry in _completed(entries) if entry.judge_scorecard is not None]


def _metric(entry: EvaluationEntryResult, key: str, default: float = 0.0) -> float:
    value = entry.metrics.get(key)
    if value is None:
        return default
    return float(value)


def _diag(entry: EvaluationEntryResult, key: str, default: float = 0.0) -> float:
    value = entry.diagnostics.get(key)
    if value is None:
        return default
    return float(value)


def _judge_metric(entry: EvaluationEntryResult, key: str, default: float = 0.0) -> float:
    if entry.judge_scorecard is None:
        return default
    value = getattr(entry.judge_scorecard, key, None)
    if value is None:
        return default
    return float(value)


def _group_profile_rows(entries: list[EvaluationEntryResult]) -> list[dict[str, Any]]:
    grouped: dict[str, list[EvaluationEntryResult]] = defaultdict(list)
    for entry in entries:
        grouped[entry.retrieval_profile_id].append(entry)

    rows: list[dict[str, Any]] = []
    for profile_id, group in sorted(grouped.items()):
        completed = _completed(group)
        judged = _judged(group)
        operator_wins = sum(1 for entry in group if entry.operator_winner)
        rows.append(
            {
                "retrieval_profile_id": profile_id,
                "artifact_families": sorted({entry.artifact_family for entry in group}),
                "entries": len(group),
                "completed_entries": len(completed),
                "completion_rate": round(_rate(len(completed), len(group)), 4),
                "failed_rate": round(
                    _rate(len([entry for entry in group if entry.status == "failed"]), len(group)),
                    4,
                ),
                "skipped_rate": round(
                    _rate(len([entry for entry in group if entry.status == "skipped"]), len(group)),
                    4,
                ),
                "operator_win_rate": round(_rate(operator_wins, len(group)), 4),
                "avg_technical_score": round(
                    _mean([entry.scorecard.technical_score for entry in completed]),
                    3,
                ),
                "avg_efficiency_score": round(
                    _mean([entry.scorecard.efficiency_score for entry in completed]),
                    3,
                ),
                "avg_reliability_score": round(
                    _mean([entry.scorecard.reliability_score for entry in completed]),
                    3,
                ),
                "avg_retrieval_discipline_score": round(
                    _mean([entry.scorecard.retrieval_discipline_score for entry in completed]),
                    3,
                ),
                "avg_ttft_seconds": round(_mean([_metric(entry, "ttft_seconds") for entry in completed]), 3),
                "avg_total_time_seconds": round(
                    _mean([_metric(entry, "total_time_seconds") for entry in completed]), 3
                ),
                "avg_total_tokens": round(
                    _mean([_metric(entry, "total_tokens") for entry in completed]), 3
                ),
                "avg_llm_calls": round(_mean([_metric(entry, "llm_calls") for entry in completed]), 3),
                "avg_retrieved_context_tokens": round(
                    _mean([_metric(entry, "retrieved_context_tokens") for entry in completed]),
                    3,
                ),
                "avg_selected_docs": round(
                    _mean([_metric(entry, "selected_docs_count") for entry in completed]), 3
                ),
                "avg_selected_nodes": round(
                    _mean([_metric(entry, "selected_nodes_count") for entry in completed]), 3
                ),
                "judged_entries": len(judged),
                "gold_backed_entries": len(
                    [entry for entry in group if entry.ground_truth_available or entry.gold_sources_available]
                ),
                "avg_judge_score": round(
                    _mean([_judge_metric(entry, "overall_quality_score") for entry in judged]),
                    3,
                ),
                "avg_business_score": round(
                    _mean([_judge_metric(entry, "business_quality_score") for entry in judged]),
                    3,
                ),
                "avg_groundedness_score": round(
                    _mean([_judge_metric(entry, "groundedness_score") for entry in judged]),
                    3,
                ),
                "avg_gold_alignment_score": round(
                    _mean([_judge_metric(entry, "gold_alignment_score") for entry in judged]),
                    3,
                ),
                "avg_source_match_score": round(
                    _mean([_judge_metric(entry, "source_match_score") for entry in judged]),
                    3,
                ),
                "missing_required_fact_rate": round(
                    _rate(
                        len(
                            [
                                entry
                                for entry in judged
                                if entry.judge_scorecard
                                and entry.judge_scorecard.missing_required_facts
                            ]
                        ),
                        len(judged),
                    ),
                    4,
                ),
                "forbidden_claim_violation_rate": round(
                    _rate(
                        len(
                            [
                                entry
                                for entry in judged
                                if entry.judge_scorecard
                                and entry.judge_scorecard.forbidden_claim_violations
                            ]
                        ),
                        len(judged),
                    ),
                    4,
                ),
                "broadened_routing_rate": round(
                    _rate(
                        len([entry for entry in completed if bool(entry.diagnostics.get("routing_broadened"))]),
                        len(completed),
                    ),
                    4,
                ),
                "tool_budget_exhaustion_rate": round(
                    _rate(
                        len([entry for entry in completed if bool(entry.diagnostics.get("tool_budget_exhausted"))]),
                        len(completed),
                    ),
                    4,
                ),
                "content_budget_exhaustion_rate": round(
                    _rate(
                        len([entry for entry in completed if bool(entry.diagnostics.get("content_budget_exhausted"))]),
                        len(completed),
                    ),
                    4,
                ),
                "avg_tool_calls_made": round(
                    _mean([_diag(entry, "tool_calls_made") for entry in completed]),
                    3,
                ),
                "avg_explored_docs_count": round(
                    _mean([_diag(entry, "explored_docs_count") for entry in completed]),
                    3,
                ),
            }
        )
    return rows


def _group_question_type_rows(entries: list[EvaluationEntryResult]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[EvaluationEntryResult]] = defaultdict(list)
    for entry in entries:
        grouped[(entry.question_type, entry.retrieval_profile_id)].append(entry)

    rows: list[dict[str, Any]] = []
    for (question_type, profile_id), group in sorted(grouped.items()):
        completed = _completed(group)
        judged = _judged(group)
        rows.append(
            {
                "question_type": question_type,
                "retrieval_profile_id": profile_id,
                "entries": len(group),
                "completed_entries": len(completed),
                "avg_technical_score": round(
                    _mean([entry.scorecard.technical_score for entry in completed]),
                    3,
                ),
                "avg_total_time_seconds": round(
                    _mean([_metric(entry, "total_time_seconds") for entry in completed]),
                    3,
                ),
                "avg_total_tokens": round(
                    _mean([_metric(entry, "total_tokens") for entry in completed]),
                    3,
                ),
                "operator_win_rate": round(
                    _rate(len([entry for entry in group if entry.operator_winner]), len(group)),
                    4,
                ),
                "avg_judge_score": round(
                    _mean([_judge_metric(entry, "overall_quality_score") for entry in judged]),
                    3,
                ),
                "avg_business_score": round(
                    _mean([_judge_metric(entry, "business_quality_score") for entry in judged]),
                    3,
                ),
            }
        )
    return rows


def _group_build_rows(entries: list[EvaluationEntryResult]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str | None, str], list[EvaluationEntryResult]] = defaultdict(list)
    for entry in entries:
        grouped[(entry.build_label, entry.retrieval_profile_id)].append(entry)

    rows: list[dict[str, Any]] = []
    for (build_label, profile_id), group in sorted(grouped.items()):
        completed = _completed(group)
        judged = _judged(group)
        rows.append(
            {
                "build_label": build_label or "unknown",
                "retrieval_profile_id": profile_id,
                "entries": len(group),
                "avg_technical_score": round(
                    _mean([entry.scorecard.technical_score for entry in completed]),
                    3,
                ),
                "avg_total_time_seconds": round(
                    _mean([_metric(entry, "total_time_seconds") for entry in completed]),
                    3,
                ),
                "avg_total_tokens": round(
                    _mean([_metric(entry, "total_tokens") for entry in completed]),
                    3,
                ),
                "operator_win_rate": round(
                    _rate(len([entry for entry in group if entry.operator_winner]), len(group)),
                    4,
                ),
                "avg_judge_score": round(
                    _mean([_judge_metric(entry, "overall_quality_score") for entry in judged]),
                    3,
                ),
                "avg_business_score": round(
                    _mean([_judge_metric(entry, "business_quality_score") for entry in judged]),
                    3,
                ),
            }
        )
    return rows


def _group_corpus_rows(entries: list[EvaluationEntryResult]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str | None, str], list[EvaluationEntryResult]] = defaultdict(list)
    for entry in entries:
        grouped[(entry.corpus_id, entry.retrieval_profile_id)].append(entry)

    rows: list[dict[str, Any]] = []
    for (corpus_id, profile_id), group in sorted(grouped.items()):
        completed = _completed(group)
        judged = _judged(group)
        rows.append(
            {
                "corpus_id": corpus_id or "unknown",
                "retrieval_profile_id": profile_id,
                "entries": len(group),
                "avg_technical_score": round(
                    _mean([entry.scorecard.technical_score for entry in completed]),
                    3,
                ),
                "avg_total_time_seconds": round(
                    _mean([_metric(entry, "total_time_seconds") for entry in completed]),
                    3,
                ),
                "avg_total_tokens": round(
                    _mean([_metric(entry, "total_tokens") for entry in completed]),
                    3,
                ),
                "avg_judge_score": round(
                    _mean([_judge_metric(entry, "overall_quality_score") for entry in judged]),
                    3,
                ),
                "avg_business_score": round(
                    _mean([_judge_metric(entry, "business_quality_score") for entry in judged]),
                    3,
                ),
            }
        )
    return rows


def build_evaluation_summary(entries: list[EvaluationEntryResult], source_runs_scanned: int) -> EvaluationSummary:
    completed = _completed(entries)
    judged = _judged(entries)
    profile_rows = _group_profile_rows(entries)

    def _best_profile(metric_key: str) -> str | None:
        candidates = [row for row in profile_rows if row.get(metric_key) not in (None, 0.0)]
        if not candidates:
            return None
        reverse = metric_key in {
            "avg_technical_score",
            "operator_win_rate",
            "avg_judge_score",
            "avg_business_score",
        }
        ordered = sorted(candidates, key=lambda row: row[metric_key], reverse=reverse)
        return ordered[0]["retrieval_profile_id"]

    return EvaluationSummary(
        source_runs_scanned=source_runs_scanned,
        entries_scored=len(entries),
        completed_entries=len(completed),
        failed_entries=len([entry for entry in entries if entry.status == "failed"]),
        skipped_entries=len([entry for entry in entries if entry.status == "skipped"]),
        best_profile_by_technical_score=_best_profile("avg_technical_score"),
        fastest_profile=_best_profile("avg_total_time_seconds"),
        lowest_tokens_profile=_best_profile("avg_total_tokens"),
        operator_favorite_profile=_best_profile("operator_win_rate"),
        average_technical_score=round(
            _mean([entry.scorecard.technical_score for entry in completed]),
            3,
        ),
        judge_enabled=bool(judged),
        entries_judged=len(judged),
        gold_backed_entries=len(
            [entry for entry in entries if entry.ground_truth_available or entry.gold_sources_available]
        ),
        average_judge_score=round(
            _mean([_judge_metric(entry, "overall_quality_score") for entry in judged]),
            3,
        ),
        average_business_score=round(
            _mean([_judge_metric(entry, "business_quality_score") for entry in judged]),
            3,
        ),
        average_gold_alignment_score=round(
            _mean([_judge_metric(entry, "gold_alignment_score") for entry in judged]),
            3,
        ),
        best_profile_by_judge_score=_best_profile("avg_judge_score"),
        best_profile_by_business_score=_best_profile("avg_business_score"),
    )


def aggregate_entries(entries: list[EvaluationEntryResult], *, source_runs_scanned: int) -> dict[str, Any]:
    """Build dashboard-ready aggregate payloads."""
    profile_rows = _group_profile_rows(entries)
    question_type_rows = _group_question_type_rows(entries)
    build_rows = _group_build_rows(entries)
    corpus_rows = _group_corpus_rows(entries)

    return {
        "summary": build_evaluation_summary(entries, source_runs_scanned).model_dump(mode="json"),
        "profiles": profile_rows,
        "question_types": question_type_rows,
        "builds": build_rows,
        "corpora": corpus_rows,
    }
