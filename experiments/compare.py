"""Comparison helpers for experiment run manifests."""

from __future__ import annotations

from experiments.models import ComparisonSummary, RunEntrySummary


def summarize_comparison(entries: list[RunEntrySummary]) -> ComparisonSummary:
    """Compute simple winner highlights across completed entries."""
    completed = [entry for entry in entries if entry.status == "completed" and entry.metrics]
    if not completed:
        return ComparisonSummary(
            completed_entries=0,
            failed_entries=len([entry for entry in entries if entry.status == "failed"]),
        )

    fastest = min(completed, key=lambda entry: entry.metrics.total_time_seconds)
    lowest_tokens = min(completed, key=lambda entry: entry.metrics.total_tokens)
    largest_context = max(completed, key=lambda entry: entry.metrics.retrieved_context_tokens)

    return ComparisonSummary(
        fastest_entry_label=fastest.label,
        lowest_tokens_entry_label=lowest_tokens.label,
        largest_context_entry_label=largest_context.label,
        completed_entries=len(completed),
        failed_entries=len([entry for entry in entries if entry.status == "failed"]),
    )
