"""Filesystem layout helpers for the experiments harness."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


DEFAULT_EXPERIMENTS_ROOT = Path("experiments") / "artifacts"


@dataclass(frozen=True)
class ExperimentPaths:
    """Resolved paths used by the experiments harness."""

    root: Path
    corpora_dir: Path
    builds_dir: Path
    runs_dir: Path
    reports_dir: Path
    evals_dir: Path
    eval_runs_dir: Path
    eval_suites_dir: Path


def resolve_experiment_paths(root: str | Path | None = None) -> ExperimentPaths:
    """Resolve and create the shared experiments artifact directories."""
    resolved_root = Path(root or DEFAULT_EXPERIMENTS_ROOT)
    paths = ExperimentPaths(
        root=resolved_root,
        corpora_dir=resolved_root / "corpora",
        builds_dir=resolved_root / "builds",
        runs_dir=resolved_root / "runs",
        reports_dir=resolved_root / "reports",
        evals_dir=resolved_root / "evals",
        eval_runs_dir=(resolved_root / "evals" / "runs"),
        eval_suites_dir=(resolved_root / "evals" / "suites"),
    )
    for directory in (
        paths.root,
        paths.corpora_dir,
        paths.builds_dir,
        paths.runs_dir,
        paths.reports_dir,
        paths.evals_dir,
        paths.eval_runs_dir,
        paths.eval_suites_dir,
    ):
        directory.mkdir(parents=True, exist_ok=True)
    return paths
