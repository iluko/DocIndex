"""Export helpers for experiment comparison runs."""

from __future__ import annotations

import csv
from html import escape
from pathlib import Path

from experiments.models import ComparisonRunManifest
from utils import atomic_write_text


def write_run_reports(
    *,
    run: ComparisonRunManifest,
    reports_dir: str | Path,
) -> dict[str, str]:
    """Persist lightweight JSON/CSV/Markdown summaries for one comparison run."""
    reports_root = Path(reports_dir)
    run_dir = reports_root / run.run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    summary_json_path = run_dir / "summary.json"
    entries_csv_path = run_dir / "entries.csv"
    overview_md_path = run_dir / "overview.md"
    overview_html_path = run_dir / "overview.html"

    atomic_write_text(summary_json_path, run.model_dump_json(indent=2))

    with entries_csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "label",
                "status",
                "build_id",
                "artifact_family",
                "retrieval_profile_id",
                "answer_profile_id",
                "ttft_seconds",
                "total_time_seconds",
                "prompt_tokens",
                "completion_tokens",
                "total_tokens",
                "llm_calls",
                "retrieved_context_tokens",
                "selected_docs",
                "selected_nodes",
                "error",
            ]
        )
        for entry in run.entries:
            metrics = entry.metrics
            writer.writerow(
                [
                    entry.label,
                    entry.status,
                    entry.build_id,
                    entry.artifact_family,
                    entry.retrieval_profile_id,
                    entry.answer_profile_id,
                    metrics.ttft_seconds if metrics else "",
                    metrics.total_time_seconds if metrics else "",
                    metrics.prompt_tokens if metrics else "",
                    metrics.completion_tokens if metrics else "",
                    metrics.total_tokens if metrics else "",
                    metrics.llm_calls if metrics else "",
                    metrics.retrieved_context_tokens if metrics else "",
                    ", ".join(entry.selected_docs),
                    ", ".join(entry.selected_nodes),
                    entry.error or "",
                ]
            )

    lines = [
        f"# {run.title}",
        "",
        f"- Run ID: `{run.run_id}`",
        f"- Query: {run.query}",
        f"- Model: `{run.model}`",
        f"- Status: `{run.status}`",
        f"- Created: `{run.created_at}`",
    ]
    if run.completed_at:
        lines.append(f"- Completed: `{run.completed_at}`")
    if run.summary:
        lines.extend(
            [
                "",
                "## Summary",
                "",
                f"- Completed entries: {run.summary.completed_entries}",
                f"- Failed entries: {run.summary.failed_entries}",
                f"- Fastest entry: {run.summary.fastest_entry_label or 'n/a'}",
                f"- Lowest tokens: {run.summary.lowest_tokens_entry_label or 'n/a'}",
                f"- Largest context: {run.summary.largest_context_entry_label or 'n/a'}",
            ]
        )

    lines.extend(["", "## Entries", ""])
    for entry in run.entries:
        lines.extend(
            [
                f"### {entry.label}",
                "",
                f"- Status: `{entry.status}`",
                f"- Build ID: `{entry.build_id}`",
                f"- Retrieval profile: `{entry.retrieval_profile_id}`",
                f"- Answer profile: `{entry.answer_profile_id}`",
            ]
        )
        if entry.metrics:
            lines.extend(
                [
                    f"- Total time: {entry.metrics.total_time_seconds:.2f}s",
                    f"- Tokens: {entry.metrics.total_tokens}",
                    f"- Retrieved context tokens: {entry.metrics.retrieved_context_tokens}",
                ]
            )
        if entry.error:
            lines.append(f"- Error: {entry.error}")
        lines.extend(["", entry.answer or "_No answer generated._", ""])

    atomic_write_text(overview_md_path, "\n".join(lines))
    atomic_write_text(
        overview_html_path,
        _render_run_html(run),
    )
    return {
        "summary_json": str(summary_json_path),
        "entries_csv": str(entries_csv_path),
        "overview_md": str(overview_md_path),
        "overview_html": str(overview_html_path),
    }


def write_aggregate_reports(
    *,
    runs: list[ComparisonRunManifest],
    reports_dir: str | Path,
) -> dict[str, str]:
    """Persist aggregate summaries across completed experiment runs."""
    reports_root = Path(reports_dir)
    aggregate_dir = reports_root / "_aggregate"
    aggregate_dir.mkdir(parents=True, exist_ok=True)

    summary = _build_question_type_summary(runs)
    json_path = aggregate_dir / "question_type_summary.json"
    md_path = aggregate_dir / "question_type_summary.md"
    html_path = aggregate_dir / "question_type_summary.html"

    atomic_write_text(json_path, _to_json(summary))
    atomic_write_text(md_path, _render_question_type_markdown(summary))
    atomic_write_text(html_path, _render_question_type_html(summary))
    return {
        "question_type_json": str(json_path),
        "question_type_md": str(md_path),
        "question_type_html": str(html_path),
    }


def _to_json(payload: dict) -> str:
    import json

    return json.dumps(payload, indent=2, ensure_ascii=False)


def _build_question_type_summary(runs: list[ComparisonRunManifest]) -> dict:
    by_type: dict[str, dict] = {}
    for run in runs:
        if run.status not in {"completed", "failed"}:
            continue
        question_type = run.question_type or "unspecified"
        bucket = by_type.setdefault(
            question_type,
            {
                "runs": 0,
                "scored_runs": 0,
                "operator_wins": {},
                "fastest_wins": {},
                "lowest_token_wins": {},
            },
        )
        bucket["runs"] += 1
        if run.operator_winner_label:
            bucket["scored_runs"] += 1
            wins = bucket["operator_wins"]
            wins[run.operator_winner_label] = wins.get(run.operator_winner_label, 0) + 1
        if run.summary and run.summary.fastest_entry_label:
            fastest = bucket["fastest_wins"]
            label = run.summary.fastest_entry_label
            fastest[label] = fastest.get(label, 0) + 1
        if run.summary and run.summary.lowest_tokens_entry_label:
            lowest = bucket["lowest_token_wins"]
            label = run.summary.lowest_tokens_entry_label
            lowest[label] = lowest.get(label, 0) + 1
    return {
        "total_runs": len(runs),
        "question_types": by_type,
    }


def _render_question_type_markdown(summary: dict) -> str:
    lines = [
        "# Question Type Summary",
        "",
        f"- Total runs scanned: {summary.get('total_runs', 0)}",
        "",
    ]
    question_types = summary.get("question_types", {})
    if not question_types:
        lines.append("_No completed runs available yet._")
        return "\n".join(lines)

    for question_type, payload in question_types.items():
        lines.extend(
            [
                f"## {question_type}",
                "",
                f"- Runs: {payload.get('runs', 0)}",
                f"- Scored runs: {payload.get('scored_runs', 0)}",
                f"- Operator winners: {payload.get('operator_wins', {})}",
                f"- Fastest winners: {payload.get('fastest_wins', {})}",
                f"- Lowest token winners: {payload.get('lowest_token_wins', {})}",
                "",
            ]
        )
    return "\n".join(lines)


def _render_question_type_html(summary: dict) -> str:
    items = []
    for question_type, payload in summary.get("question_types", {}).items():
        items.append(
            f"""
            <section class="card">
              <h2>{escape(question_type)}</h2>
              <ul>
                <li>Runs: {payload.get('runs', 0)}</li>
                <li>Scored runs: {payload.get('scored_runs', 0)}</li>
                <li>Operator winners: <code>{escape(str(payload.get('operator_wins', {})))}</code></li>
                <li>Fastest winners: <code>{escape(str(payload.get('fastest_wins', {})))}</code></li>
                <li>Lowest token winners: <code>{escape(str(payload.get('lowest_token_wins', {})))}</code></li>
              </ul>
            </section>
            """
        )
    body = "\n".join(items) or "<p>No completed runs available yet.</p>"
    return f"""
    <html>
      <head>
        <meta charset="utf-8" />
        <title>Question Type Summary</title>
        <style>
          body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #f7f3eb; color: #173042; padding: 24px; }}
          .card {{ background: #fffdf8; border: 1px solid #d9cfbf; border-radius: 16px; padding: 16px 18px; margin-bottom: 16px; }}
          code {{ background: #f1eadc; padding: 2px 6px; border-radius: 6px; }}
        </style>
      </head>
      <body>
        <h1>Question Type Summary</h1>
        <p>Total runs scanned: {summary.get('total_runs', 0)}</p>
        {body}
      </body>
    </html>
    """


def _render_run_html(run: ComparisonRunManifest) -> str:
    entry_blocks = []
    for entry in run.entries:
        metrics = entry.metrics
        answer = escape(entry.answer or "No answer generated.")
        entry_blocks.append(
            f"""
            <section class="card">
              <h2>{escape(entry.label)}</h2>
              <p>Status: <strong>{escape(entry.status)}</strong></p>
              <p>Build: <code>{escape(entry.build_id)}</code></p>
              <p>Retrieval profile: <code>{escape(entry.retrieval_profile_id)}</code></p>
              <p>Total time: {metrics.total_time_seconds:.2f}s | Tokens: {metrics.total_tokens}</p>
              <pre>{answer}</pre>
            </section>
            """
            if metrics
            else f"""
            <section class="card">
              <h2>{escape(entry.label)}</h2>
              <p>Status: <strong>{escape(entry.status)}</strong></p>
              <p>{escape(entry.error or 'No metrics available.')}</p>
            </section>
            """
        )
    return f"""
    <html>
      <head>
        <meta charset="utf-8" />
        <title>{escape(run.title)}</title>
        <style>
          body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #f7f3eb; color: #173042; padding: 24px; }}
          .card {{ background: #fffdf8; border: 1px solid #d9cfbf; border-radius: 16px; padding: 16px 18px; margin-bottom: 16px; }}
          code {{ background: #f1eadc; padding: 2px 6px; border-radius: 6px; }}
          pre {{ white-space: pre-wrap; background: #f8f4ec; border-radius: 12px; padding: 12px; }}
        </style>
      </head>
      <body>
        <h1>{escape(run.title)}</h1>
        <p><strong>Query:</strong> {escape(run.query)}</p>
        <p><strong>Question type:</strong> {escape(run.question_type)}</p>
        <p><strong>Model:</strong> <code>{escape(run.model)}</code></p>
        <p><strong>Status:</strong> {escape(run.status)}</p>
        {''.join(entry_blocks)}
      </body>
    </html>
    """
