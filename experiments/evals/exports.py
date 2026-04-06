"""Export helpers for evaluation snapshots."""

from __future__ import annotations

import csv
from html import escape
from pathlib import Path

from experiments.evals.models import EvaluationRunManifest
from utils import atomic_write_text


def write_evaluation_reports(
    *,
    evaluation: EvaluationRunManifest,
    eval_runs_dir: str | Path,
) -> dict[str, str]:
    """Persist JSON/CSV/Markdown/HTML outputs for one evaluation snapshot."""
    eval_root = Path(eval_runs_dir) / evaluation.eval_run_id
    eval_root.mkdir(parents=True, exist_ok=True)

    summary_json_path = eval_root / "summary.json"
    entries_csv_path = eval_root / "entries.csv"
    overview_md_path = eval_root / "overview.md"
    overview_html_path = eval_root / "overview.html"

    atomic_write_text(summary_json_path, evaluation.model_dump_json(indent=2))

    with entries_csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "source_run_id",
                "case_id",
                "question_type",
                "label",
                "build_label",
                "corpus_id",
                "artifact_family",
                "retrieval_profile_id",
                "status",
                "operator_winner",
                "technical_score",
                "judge_score",
                "business_score",
                "gold_alignment_score",
                "efficiency_score",
                "reliability_score",
                "retrieval_discipline_score",
                "total_time_seconds",
                "total_tokens",
                "llm_calls",
                "retrieved_context_tokens",
            ]
        )
        for entry in evaluation.entries:
            scorecard = entry.scorecard
            writer.writerow(
                [
                    entry.source_run_id,
                    entry.case_id,
                    entry.question_type,
                    entry.label,
                    entry.build_label or "",
                    entry.corpus_id or "",
                    entry.artifact_family,
                    entry.retrieval_profile_id,
                    entry.status,
                    "yes" if entry.operator_winner else "no",
                    scorecard.technical_score if scorecard else "",
                    entry.judge_scorecard.overall_quality_score if entry.judge_scorecard else "",
                    entry.judge_scorecard.business_quality_score if entry.judge_scorecard else "",
                    entry.judge_scorecard.gold_alignment_score if entry.judge_scorecard else "",
                    scorecard.efficiency_score if scorecard else "",
                    scorecard.reliability_score if scorecard else "",
                    scorecard.retrieval_discipline_score if scorecard else "",
                    entry.metrics.get("total_time_seconds", ""),
                    entry.metrics.get("total_tokens", ""),
                    entry.metrics.get("llm_calls", ""),
                    entry.metrics.get("retrieved_context_tokens", ""),
                ]
            )

    summary = evaluation.summary
    lines = [
        f"# {evaluation.title}",
        "",
        f"- Evaluation ID: `{evaluation.eval_run_id}`",
        f"- Status: `{evaluation.status}`",
        f"- Source runs: `{len(evaluation.source_run_ids)}`",
        f"- Suite: `{evaluation.suite.name}`",
        f"- Judge scoring: `{'enabled' if evaluation.judge_enabled else 'disabled'}`",
    ]
    if summary:
        lines.extend(
            [
                "",
                "## Summary",
                "",
                f"- Entries scored: {summary.entries_scored}",
                f"- Completed entries: {summary.completed_entries}",
                f"- Failed entries: {summary.failed_entries}",
                f"- Skipped entries: {summary.skipped_entries}",
                f"- Average technical score: {summary.average_technical_score:.2f}",
                f"- Best profile by technical score: {summary.best_profile_by_technical_score or 'n/a'}",
                f"- Entries judged: {summary.entries_judged}",
                f"- Average judge score: {summary.average_judge_score:.2f}",
                f"- Average business score: {summary.average_business_score:.2f}",
                f"- Average gold alignment score: {summary.average_gold_alignment_score:.2f}",
                f"- Best profile by judge score: {summary.best_profile_by_judge_score or 'n/a'}",
                f"- Best profile by business score: {summary.best_profile_by_business_score or 'n/a'}",
                f"- Fastest profile: {summary.fastest_profile or 'n/a'}",
                f"- Lowest tokens profile: {summary.lowest_tokens_profile or 'n/a'}",
                f"- Operator favorite profile: {summary.operator_favorite_profile or 'n/a'}",
            ]
        )

    profile_rows = evaluation.aggregate_payload.get("profiles", [])
    if profile_rows:
        lines.extend(["", "## Profile Leaderboard", ""])
        ordered = sorted(profile_rows, key=lambda row: row.get("avg_technical_score", 0.0), reverse=True)
        for row in ordered:
            lines.extend(
                [
                    f"### {row['retrieval_profile_id']}",
                    "",
                    f"- Avg technical score: {row.get('avg_technical_score', 0.0)}",
                    f"- Avg judge score: {row.get('avg_judge_score', 0.0)}",
                    f"- Avg business score: {row.get('avg_business_score', 0.0)}",
                    f"- Avg total time: {row.get('avg_total_time_seconds', 0.0)}s",
                    f"- Avg total tokens: {row.get('avg_total_tokens', 0.0)}",
                    f"- Completion rate: {row.get('completion_rate', 0.0)}",
                    f"- Operator win rate: {row.get('operator_win_rate', 0.0)}",
                    "",
                ]
            )

    atomic_write_text(overview_md_path, "\n".join(lines))
    atomic_write_text(
        overview_html_path,
        _render_evaluation_html(evaluation),
    )
    return {
        "summary_json": str(summary_json_path),
        "entries_csv": str(entries_csv_path),
        "overview_md": str(overview_md_path),
        "overview_html": str(overview_html_path),
    }


def _render_evaluation_html(evaluation: EvaluationRunManifest) -> str:
    summary = evaluation.summary
    profile_rows = evaluation.aggregate_payload.get("profiles", [])
    leaderboard = "".join(
        f"""
        <section class="card">
          <h2>{escape(str(row['retrieval_profile_id']))}</h2>
          <ul>
            <li>Avg technical score: {row.get('avg_technical_score', 0.0)}</li>
            <li>Avg judge score: {row.get('avg_judge_score', 0.0)}</li>
            <li>Avg business score: {row.get('avg_business_score', 0.0)}</li>
            <li>Avg total time: {row.get('avg_total_time_seconds', 0.0)}s</li>
            <li>Avg total tokens: {row.get('avg_total_tokens', 0.0)}</li>
            <li>Completion rate: {row.get('completion_rate', 0.0)}</li>
            <li>Operator win rate: {row.get('operator_win_rate', 0.0)}</li>
          </ul>
        </section>
        """
        for row in sorted(profile_rows, key=lambda item: item.get("avg_technical_score", 0.0), reverse=True)
    ) or "<p>No profile aggregates available yet.</p>"

    summary_block = (
        f"""
        <section class="card">
          <h2>Summary</h2>
          <ul>
            <li>Entries scored: {summary.entries_scored}</li>
            <li>Completed entries: {summary.completed_entries}</li>
            <li>Failed entries: {summary.failed_entries}</li>
            <li>Skipped entries: {summary.skipped_entries}</li>
            <li>Average technical score: {summary.average_technical_score:.2f}</li>
            <li>Entries judged: {summary.entries_judged}</li>
            <li>Average judge score: {summary.average_judge_score:.2f}</li>
            <li>Average business score: {summary.average_business_score:.2f}</li>
            <li>Average gold alignment score: {summary.average_gold_alignment_score:.2f}</li>
            <li>Best profile: {escape(summary.best_profile_by_technical_score or 'n/a')}</li>
            <li>Best profile by judge score: {escape(summary.best_profile_by_judge_score or 'n/a')}</li>
            <li>Best profile by business score: {escape(summary.best_profile_by_business_score or 'n/a')}</li>
            <li>Fastest profile: {escape(summary.fastest_profile or 'n/a')}</li>
            <li>Lowest tokens profile: {escape(summary.lowest_tokens_profile or 'n/a')}</li>
            <li>Operator favorite profile: {escape(summary.operator_favorite_profile or 'n/a')}</li>
          </ul>
        </section>
        """
        if summary
        else "<p>No evaluation summary available yet.</p>"
    )

    return f"""
    <html>
      <head>
        <meta charset="utf-8" />
        <title>{escape(evaluation.title)}</title>
        <style>
          body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #f7f3eb; color: #173042; padding: 24px; }}
          .card {{ background: #fffdf8; border: 1px solid #d9cfbf; border-radius: 16px; padding: 16px 18px; margin-bottom: 16px; }}
          code {{ background: #f1eadc; padding: 2px 6px; border-radius: 6px; }}
        </style>
      </head>
      <body>
        <h1>{escape(evaluation.title)}</h1>
        <p><strong>Evaluation ID:</strong> <code>{escape(evaluation.eval_run_id)}</code></p>
        <p><strong>Suite:</strong> {escape(evaluation.suite.name)}</p>
        <p><strong>Source runs:</strong> {len(evaluation.source_run_ids)}</p>
        <p><strong>Judge scoring:</strong> {escape('enabled' if evaluation.judge_enabled else 'disabled')}</p>
        {summary_block}
        <h2>Profile Leaderboard</h2>
        {leaderboard}
      </body>
    </html>
    """
