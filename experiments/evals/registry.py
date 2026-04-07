"""Runner and persistence layer for evaluation snapshots."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from experiments.builds.registry import ArtifactBuildRunner
from experiments.evals.aggregations import aggregate_entries, build_evaluation_summary
from experiments.evals.deterministic import evaluate_runs_deterministically
from experiments.evals.exports import write_evaluation_reports
from experiments.evals.judges import score_entries_with_judge
from experiments.evals.models import EvaluationRunManifest
from experiments.evals.suites import EvaluationSuiteStore, build_suite_from_runs
from experiments.layout import resolve_experiment_paths
from experiments.runs.registry import ExperimentRunRunner
from utils import atomic_write_text


ProgressCallback = Callable[[dict[str, Any]], None]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


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


class EvaluationRunner:
    """Generate deterministic evaluation snapshots from existing experiment runs."""

    def __init__(self, root: str | Path | None = None):
        self.paths = resolve_experiment_paths(root)
        self.runs = ExperimentRunRunner(self.paths.root)
        self.builds = ArtifactBuildRunner(self.paths.root)
        self.suites = EvaluationSuiteStore(self.paths.root)

    def _eval_run_dir(self, eval_run_id: str) -> Path:
        return self.paths.eval_runs_dir / eval_run_id

    def _manifest_path(self, eval_run_id: str) -> Path:
        return self._eval_run_dir(eval_run_id) / "manifest.json"

    def load_evaluation(self, eval_run_id: str) -> EvaluationRunManifest:
        path = self._manifest_path(eval_run_id)
        if not path.exists():
            raise FileNotFoundError(f"No evaluation snapshot found for '{eval_run_id}'.")
        return EvaluationRunManifest.model_validate_json(path.read_text(encoding="utf-8"))

    def list_evaluations(self) -> list[EvaluationRunManifest]:
        evaluations: list[EvaluationRunManifest] = []
        for manifest_path in sorted(self.paths.eval_runs_dir.glob("*/manifest.json")):
            evaluations.append(
                EvaluationRunManifest.model_validate_json(
                    manifest_path.read_text(encoding="utf-8")
                )
            )
        return evaluations

    def run_evaluation(
        self,
        *,
        source_run_ids: list[str] | None = None,
        title: str | None = None,
        suite_id: str | None = None,
        persist_suite: bool = True,
        judge_enabled: bool = False,
        judge_model: str | None = None,
        judge_reasoning_effort: str | None = None,
        progress_callback: ProgressCallback | None = None,
    ) -> EvaluationRunManifest:
        if judge_enabled and not judge_model:
            raise ValueError("Judge scoring was enabled, but no judge model was provided.")
        selected_runs = self._selected_runs(source_run_ids)
        if not selected_runs:
            raise ValueError("No eligible comparison runs were found for evaluation.")

        if suite_id:
            suite = self.suites.load_suite(suite_id)
        else:
            attached_suite_ids = {run.suite_id for run in selected_runs if run.suite_id}
            if len(attached_suite_ids) == 1:
                attached_suite_id = next(iter(attached_suite_ids))
                try:
                    suite = self.suites.load_suite(attached_suite_id)
                except FileNotFoundError:
                    suite = build_suite_from_runs(selected_runs)
                    if persist_suite:
                        self.suites.save_suite(suite)
            else:
                suite = build_suite_from_runs(selected_runs)
                if persist_suite:
                    self.suites.save_suite(suite)

        eval_run_id = str(uuid4())
        manifest = EvaluationRunManifest(
            eval_run_id=eval_run_id,
            title=title
            or (
                f"{'Judge-backed' if judge_enabled else 'Deterministic'} evaluation {eval_run_id[:8]}"
            ),
            created_at=_utc_now(),
            status="running",
            judge_enabled=judge_enabled,
            judge_model=judge_model,
            suite=suite,
            source_run_ids=[run.run_id for run in selected_runs],
        )
        self._persist_manifest(manifest)
        total_entries = sum(len(run.entries) for run in selected_runs)
        _emit_progress(
            progress_callback,
            "evaluation_started",
            eval_run_id=manifest.eval_run_id,
            title=manifest.title,
            total_runs=len(selected_runs),
            total_entries=total_entries,
            judge_enabled=judge_enabled,
            suite_id=manifest.suite.suite_id,
        )

        build_ids = {entry.build_id for run in selected_runs for entry in run.entries}
        build_lookup = {}
        for build_id in build_ids:
            try:
                build_lookup[build_id] = self.builds.load_build(build_id)
            except FileNotFoundError:
                continue

        entries = evaluate_runs_deterministically(
            runs=selected_runs,
            suite=suite,
            build_lookup=build_lookup,
            progress_callback=progress_callback,
        )
        manifest.entries = entries
        self._persist_manifest(manifest)
        if judge_enabled and judge_model:
            entries, judge_error = score_entries_with_judge(
                entries=entries,
                suite_lookup=suite.case_lookup(),
                judge_model=judge_model,
                judge_reasoning_effort=judge_reasoning_effort,
                progress_callback=progress_callback,
            )
            manifest.judge_error = judge_error
        aggregate_payload = aggregate_entries(entries, source_runs_scanned=len(selected_runs))
        manifest.entries = entries
        manifest.summary = build_evaluation_summary(entries, len(selected_runs))
        manifest.aggregate_payload = aggregate_payload
        manifest.status = "completed"
        manifest.completed_at = _utc_now()
        manifest.report_paths = write_evaluation_reports(
            evaluation=manifest,
            eval_runs_dir=self.paths.eval_runs_dir,
        )
        self._persist_manifest(manifest)
        _emit_progress(
            progress_callback,
            "evaluation_completed",
            eval_run_id=manifest.eval_run_id,
            title=manifest.title,
            status=manifest.status,
            total_runs=len(selected_runs),
            total_entries=len(entries),
            judge_enabled=judge_enabled,
        )
        return manifest

    def _selected_runs(self, source_run_ids: list[str] | None = None):
        runs = self.runs.list_runs()
        eligible = [run for run in runs if run.status in {"completed", "failed"}]
        if not source_run_ids:
            return eligible
        selected = set(source_run_ids)
        return [run for run in eligible if run.run_id in selected]

    def _persist_manifest(self, manifest: EvaluationRunManifest) -> None:
        manifest_path = self._manifest_path(manifest.eval_run_id)
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(manifest_path, manifest.model_dump_json(indent=2))
