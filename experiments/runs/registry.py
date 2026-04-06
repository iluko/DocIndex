"""Persisted comparison-run execution for the experiments harness."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Iterable
from uuid import uuid4

from experiments.answering import answer_from_context
from experiments.builds.registry import ArtifactBuildRunner
from experiments.compare import summarize_comparison
from experiments.layout import resolve_experiment_paths
from experiments.models import (
    AnswerProfileSpec,
    ComparisonRunManifest,
    RunEntryMetrics,
    RunEntrySpec,
    RunEntrySummary,
)
from experiments.reports import write_aggregate_reports, write_run_reports
from experiments.retrieval.base import RetrievalExecutionContext
from experiments.retrieval.registry import get_retrieval_adapter
from utils import (
    atomic_write_text,
    get_default_model,
    get_llm_usage_tracker,
    llm_usage_context,
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _spec_hash(
    *,
    query: str,
    entries: list[RunEntrySpec],
    model: str,
    conversation_context: str | None,
    question_type: str,
    suite_id: str | None,
) -> str:
    payload = {
        "query": query,
        "model": model,
        "conversation_context": conversation_context or "",
        "question_type": question_type,
        "suite_id": suite_id or "",
        "entries": [entry.model_dump(mode="json") for entry in entries],
    }
    rendered = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()[:16]


class ExperimentRunRunner:
    """Execute and persist side-by-side manual comparison runs."""

    def __init__(self, root: str | Path | None = None):
        self.paths = resolve_experiment_paths(root)
        self.builds = ArtifactBuildRunner(self.paths.root)

    def _run_dir(self, run_id: str) -> Path:
        return self.paths.runs_dir / run_id

    def _manifest_path(self, run_id: str) -> Path:
        return self._run_dir(run_id) / "manifest.json"

    def _entry_trace_path(self, run_id: str, index: int, label: str) -> Path:
        safe_label = "".join(ch if ch.isalnum() else "_" for ch in label.lower()).strip("_") or f"entry_{index}"
        return self._run_dir(run_id) / f"{index:02d}_{safe_label}_trace.json"

    def load_run(self, run_id: str) -> ComparisonRunManifest:
        path = self._manifest_path(run_id)
        if not path.exists():
            raise FileNotFoundError(f"No experiment run found for '{run_id}'.")
        return ComparisonRunManifest.model_validate_json(path.read_text(encoding="utf-8"))

    def list_runs(self) -> list[ComparisonRunManifest]:
        manifests: list[ComparisonRunManifest] = []
        for manifest_path in sorted(self.paths.runs_dir.glob("*/manifest.json")):
            manifests.append(
                ComparisonRunManifest.model_validate_json(
                    manifest_path.read_text(encoding="utf-8")
                )
            )
        return manifests

    def find_run_by_spec_hash(self, spec_hash: str) -> ComparisonRunManifest | None:
        """Return the most recent run that matches one deterministic input spec."""
        matches = [run for run in self.list_runs() if run.spec_hash == spec_hash]
        if not matches:
            return None
        matches.sort(key=lambda run: run.created_at, reverse=True)
        return matches[0]

    def run_comparison(
        self,
        *,
        query: str,
        entries: Iterable[RunEntrySpec],
        model: str | None = None,
        conversation_context: str | None = None,
        title: str | None = None,
        question_type: str = "unspecified",
        suite_id: str | None = None,
        max_concurrency: int | None = None,
        resume_existing: bool = False,
    ) -> ComparisonRunManifest:
        return asyncio.run(
            self.run_comparison_async(
                query=query,
                entries=list(entries),
                model=model,
                conversation_context=conversation_context,
                title=title,
                question_type=question_type,
                suite_id=suite_id,
                max_concurrency=max_concurrency,
                resume_existing=resume_existing,
            )
        )

    async def run_comparison_async(
        self,
        *,
        query: str,
        entries: list[RunEntrySpec],
        model: str | None = None,
        conversation_context: str | None = None,
        title: str | None = None,
        question_type: str = "unspecified",
        suite_id: str | None = None,
        max_concurrency: int | None = None,
        resume_existing: bool = False,
        source_run_id: str | None = None,
    ) -> ComparisonRunManifest:
        resolved_model = model or get_default_model()
        spec_hash = _spec_hash(
            query=query,
            entries=entries,
            model=resolved_model,
            conversation_context=conversation_context,
            question_type=question_type,
            suite_id=suite_id,
        )
        if resume_existing:
            existing = self.find_run_by_spec_hash(spec_hash)
            if existing and existing.status in {"pending", "running", "failed"}:
                return await self.resume_run(existing.run_id, max_concurrency=max_concurrency)

        run_id = str(uuid4())
        manifest = ComparisonRunManifest(
            run_id=run_id,
            title=title or f"Comparison run {run_id[:8]}",
            query=query,
            conversation_context=conversation_context,
            question_type=question_type,
            suite_id=suite_id,
            created_at=_utc_now(),
            model=resolved_model,
            max_concurrency=max_concurrency,
            spec_hash=spec_hash,
            requested_entries=entries,
            source_run_id=source_run_id,
            status="running",
            entries=[
                RunEntrySummary(
                    label=entry.label,
                    build_id=entry.build_id,
                    artifact_family=entry.retrieval_profile.artifact_family,
                    retrieval_profile_id=entry.retrieval_profile.profile_id,
                    answer_profile_id=entry.answer_profile.profile_id,
                    status="pending",
                    error="Pending execution.",
                )
                for entry in entries
            ],
        )
        await self._persist_manifest(manifest)
        manifest = await self._execute_manifest_entries(
            manifest,
            max_concurrency=max_concurrency,
        )
        manifest.status = "completed" if all(r.status == "completed" for r in manifest.entries) else "failed"
        manifest.completed_at = _utc_now()
        manifest.summary = summarize_comparison(manifest.entries)
        manifest.report_paths = write_run_reports(run=manifest, reports_dir=self.paths.reports_dir)
        write_aggregate_reports(runs=self.list_runs_with(manifest), reports_dir=self.paths.reports_dir)
        await self._persist_manifest(manifest)
        return manifest

    async def resume_run(
        self,
        run_id: str,
        *,
        max_concurrency: int | None = None,
    ) -> ComparisonRunManifest:
        """Resume a previously started run by executing unfinished entries only."""
        manifest = self.load_run(run_id)
        manifest.status = "running"
        await self._persist_manifest(manifest)
        manifest = await self._execute_manifest_entries(
            manifest,
            max_concurrency=max_concurrency,
        )
        manifest.status = "completed" if all(r.status == "completed" for r in manifest.entries) else "failed"
        manifest.completed_at = _utc_now()
        manifest.summary = summarize_comparison(manifest.entries)
        manifest.report_paths = write_run_reports(run=manifest, reports_dir=self.paths.reports_dir)
        write_aggregate_reports(runs=self.list_runs_with(manifest), reports_dir=self.paths.reports_dir)
        await self._persist_manifest(manifest)
        return manifest

    def rerun_entries(
        self,
        run_id: str,
        *,
        failed_only: bool = False,
        labels: Iterable[str] | None = None,
        max_concurrency: int | None = None,
    ) -> ComparisonRunManifest:
        """Create a fresh run from a subset of an existing run's requested entries."""
        return asyncio.run(
            self.rerun_entries_async(
                run_id,
                failed_only=failed_only,
                labels=labels,
                max_concurrency=max_concurrency,
            )
        )

    def resume_existing_run(
        self,
        run_id: str,
        *,
        max_concurrency: int | None = None,
    ) -> ComparisonRunManifest:
        """Synchronous wrapper around ``resume_run`` for UI callers."""
        return asyncio.run(self.resume_run(run_id, max_concurrency=max_concurrency))

    def annotate_run(
        self,
        run_id: str,
        *,
        operator_winner_label: str | None = None,
        notes: str | None = None,
    ) -> ComparisonRunManifest:
        """Synchronous wrapper around ``update_run_annotation`` for UI callers."""
        return asyncio.run(
            self.update_run_annotation(
                run_id,
                operator_winner_label=operator_winner_label,
                notes=notes,
            )
        )

    async def rerun_entries_async(
        self,
        run_id: str,
        *,
        failed_only: bool = False,
        labels: Iterable[str] | None = None,
        max_concurrency: int | None = None,
    ) -> ComparisonRunManifest:
        """Create and execute a derived run from matching requested entries."""
        original = self.load_run(run_id)
        selected_labels = set(labels or [])
        status_by_label = {entry.label: entry.status for entry in original.entries}

        entries: list[RunEntrySpec] = []
        for requested in original.requested_entries:
            status = status_by_label.get(requested.label)
            if failed_only and status not in {"failed", "skipped"}:
                continue
            if selected_labels and requested.label not in selected_labels:
                continue
            entries.append(requested)

        if not entries:
            raise ValueError("No matching entries were selected for rerun.")

        return await self.run_comparison_async(
            query=original.query,
            entries=entries,
            model=original.model,
            conversation_context=original.conversation_context,
            title=f"{original.title} · rerun",
            question_type=original.question_type,
            suite_id=original.suite_id,
            max_concurrency=max_concurrency,
            source_run_id=original.run_id,
        )

    async def update_run_annotation(
        self,
        run_id: str,
        *,
        operator_winner_label: str | None = None,
        notes: str | None = None,
    ) -> ComparisonRunManifest:
        """Update manual annotations for one run and refresh aggregate reports."""
        manifest = self.load_run(run_id)
        manifest.operator_winner_label = operator_winner_label
        manifest.notes = notes
        manifest.report_paths = write_run_reports(run=manifest, reports_dir=self.paths.reports_dir)
        write_aggregate_reports(runs=self.list_runs_with(manifest), reports_dir=self.paths.reports_dir)
        await self._persist_manifest(manifest)
        return manifest

    async def _persist_manifest(self, manifest: ComparisonRunManifest) -> None:
        manifest_path = self._manifest_path(manifest.run_id)
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(manifest_path, manifest.model_dump_json(indent=2))

    async def _execute_manifest_entries(
        self,
        manifest: ComparisonRunManifest,
        *,
        max_concurrency: int | None = None,
    ) -> ComparisonRunManifest:
        concurrency = max(1, min(max_concurrency or len(manifest.requested_entries) or 1, len(manifest.requested_entries) or 1))
        semaphore = asyncio.Semaphore(concurrency)

        async def _wrapped(index: int, entry: RunEntrySpec) -> tuple[int, RunEntrySummary]:
            async with semaphore:
                result = await self._execute_entry(
                    run_id=manifest.run_id,
                    index=index,
                    query=manifest.query,
                    entry=entry,
                    model=manifest.model,
                    conversation_context=manifest.conversation_context,
                )
                return index, result

        pending_indices = [
            index
            for index, current in enumerate(manifest.entries)
            if current.status == "pending" and current.error == "Pending execution."
        ]
        if not pending_indices:
            return manifest

        tasks = [
            asyncio.create_task(_wrapped(index, manifest.requested_entries[index]))
            for index in pending_indices
        ]
        for completed in asyncio.as_completed(tasks):
            index, result = await completed
            manifest.entries[index] = result
            manifest.summary = summarize_comparison(manifest.entries)
            await self._persist_manifest(manifest)
        return manifest

    def list_runs_with(self, manifest: ComparisonRunManifest) -> list[ComparisonRunManifest]:
        """Return all stored runs plus one in-memory manifest replacement."""
        runs = [run for run in self.list_runs() if run.run_id != manifest.run_id]
        runs.append(manifest)
        runs.sort(key=lambda run: run.created_at)
        return runs

    async def _execute_entry(
        self,
        *,
        run_id: str,
        index: int,
        query: str,
        entry: RunEntrySpec,
        model: str,
        conversation_context: str | None,
    ) -> RunEntrySummary:
        try:
            build = self.builds.load_build(entry.build_id)
        except Exception as exc:
            return RunEntrySummary(
                label=entry.label,
                build_id=entry.build_id,
                artifact_family="unknown",
                retrieval_profile_id=entry.retrieval_profile.profile_id,
                answer_profile_id=entry.answer_profile.profile_id,
                status="failed",
                error=str(exc),
            )

        if build.artifact_family != entry.retrieval_profile.artifact_family:
            return RunEntrySummary(
                label=entry.label,
                build_id=entry.build_id,
                artifact_family=build.artifact_family,
                retrieval_profile_id=entry.retrieval_profile.profile_id,
                answer_profile_id=entry.answer_profile.profile_id,
                status="skipped",
                error=(
                    f"Incompatible artifact family '{build.artifact_family}' for retrieval "
                    f"profile '{entry.retrieval_profile.profile_id}' "
                    f"(expects '{entry.retrieval_profile.artifact_family}')."
                ),
            )

        adapter = get_retrieval_adapter(entry.retrieval_profile.mode)
        if adapter.artifact_family != build.artifact_family:
            return RunEntrySummary(
                label=entry.label,
                build_id=entry.build_id,
                artifact_family=build.artifact_family,
                retrieval_profile_id=entry.retrieval_profile.profile_id,
                answer_profile_id=entry.answer_profile.profile_id,
                status="skipped",
                error=(
                    f"Adapter mode '{entry.retrieval_profile.mode}' does not support "
                    f"artifact family '{build.artifact_family}'."
                ),
            )

        with llm_usage_context():
            try:
                retrieval_result = await adapter.retrieve(
                    RetrievalExecutionContext(
                        build=build,
                        build_dir=Path(self.builds.get_build_dir(build.build_id)),
                        query=query,
                        model=model,
                        conversation_context=conversation_context,
                        retrieval_profile=entry.retrieval_profile,
                    )
                )
                answer, answer_metrics = await answer_from_context(
                    query=query,
                    retrieved_context=retrieval_result.retrieved_context,
                    model=model,
                    answer_profile=entry.answer_profile,
                    conversation_context=conversation_context,
                )
            except Exception as exc:
                return RunEntrySummary(
                    label=entry.label,
                    build_id=build.build_id,
                    artifact_family=build.artifact_family,
                    retrieval_profile_id=entry.retrieval_profile.profile_id,
                    answer_profile_id=entry.answer_profile.profile_id,
                    status="failed",
                    error=str(exc),
                )

            usage = get_llm_usage_tracker()
            metrics = RunEntryMetrics(
                ttft_seconds=float(answer_metrics.get("ttft_seconds", 0.0)),
                total_time_seconds=(
                    float(retrieval_result.retrieval_metrics.get("retrieval_time_seconds", 0.0))
                    + float(answer_metrics.get("answer_time_seconds", 0.0))
                ),
                retrieval_time_seconds=float(
                    retrieval_result.retrieval_metrics.get("retrieval_time_seconds", 0.0)
                ),
                answer_time_seconds=float(answer_metrics.get("answer_time_seconds", 0.0)),
                prompt_tokens=usage.prompt_tokens if usage else 0,
                completion_tokens=usage.completion_tokens if usage else 0,
                total_tokens=usage.total_tokens if usage else 0,
                llm_calls=usage.llm_calls if usage else 0,
                estimated_token_usage=bool(usage and usage.estimated_calls),
                retrieved_context_tokens=int(
                    retrieval_result.retrieval_metrics.get("retrieved_context_tokens", 0)
                ),
            )

        trace_payload = {
            "label": entry.label,
            "query": query,
            "model": model,
            "build_id": build.build_id,
            "artifact_family": build.artifact_family,
            "retrieval_profile": entry.retrieval_profile.model_dump(mode="json"),
            "answer_profile": entry.answer_profile.model_dump(mode="json"),
            "selected_docs": retrieval_result.selected_docs,
            "selected_nodes": retrieval_result.selected_nodes,
            "retrieved_context": retrieval_result.retrieved_context,
            "sources": retrieval_result.sources,
            "retrieval_trace": retrieval_result.trace,
            "retrieval_metrics": retrieval_result.retrieval_metrics,
            "answer": answer,
            "metrics": metrics.model_dump(mode="json"),
        }
        trace_path = self._entry_trace_path(run_id, index, entry.label)
        atomic_write_text(trace_path, json.dumps(trace_payload, indent=2, ensure_ascii=False))

        return RunEntrySummary(
            label=entry.label,
            build_id=build.build_id,
            artifact_family=build.artifact_family,
            retrieval_profile_id=entry.retrieval_profile.profile_id,
            answer_profile_id=entry.answer_profile.profile_id,
            status="completed",
            answer=answer,
            selected_docs=retrieval_result.selected_docs,
            selected_nodes=retrieval_result.selected_nodes,
            retrieved_context_preview=retrieval_result.retrieved_context[:1200],
            sources=retrieval_result.sources,
            metrics=metrics,
            trace_path=str(trace_path),
        )
