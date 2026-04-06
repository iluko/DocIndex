"""Registry and runner for experiment artifact builds."""

from __future__ import annotations

import asyncio
import hashlib
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil

from index_registry import sanitize_index_key_part
from experiments.builds.base import ArtifactBuildAdapter, BuildExecutionContext
from experiments.builds.pageindex import PageIndexBuildAdapter
from experiments.builds.rag import RAGBuildAdapter
from experiments.builds.rag_vector import VectorRAGBuildAdapter
from experiments.corpora import CorpusStore
from experiments.layout import resolve_experiment_paths
from experiments.models import BuildConfig, BuildManifest
from utils import atomic_write_text


def _utc_now() -> str:
    """Return an ISO-8601 timestamp in UTC."""
    return datetime.now(timezone.utc).isoformat()


def _config_fingerprint(config: BuildConfig) -> str:
    """Create a short stable fingerprint for a build configuration."""
    payload = json.dumps(config.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:10]


class ArtifactBuildRunner:
    """Execute artifact builds in isolated directories under experiments/artifacts."""

    def __init__(self, root: str | Path | None = None):
        self.paths = resolve_experiment_paths(root)
        self.corpora = CorpusStore(self.paths.root)
        self._adapters: dict[str, ArtifactBuildAdapter] = {
            "pageindex": PageIndexBuildAdapter(),
            "rag": RAGBuildAdapter(),
            "rag_vector": VectorRAGBuildAdapter(),
        }

    def _build_id(self, corpus_id: str, config: BuildConfig) -> str:
        """Create a deterministic build identifier from corpus + label + config."""
        label = sanitize_index_key_part(config.label)
        return f"{corpus_id}__{label}__{_config_fingerprint(config)}"

    def _build_dir(self, build_id: str) -> Path:
        return self.paths.builds_dir / build_id

    def _manifest_path(self, build_id: str) -> Path:
        return self._build_dir(build_id) / "manifest.json"

    def get_build_dir(self, build_id: str) -> Path:
        """Return the root directory that stores one build's isolated artifacts."""
        return self._build_dir(build_id)

    def load_build(self, build_id: str) -> BuildManifest:
        """Load a previously recorded build manifest by id."""
        manifest_path = self._manifest_path(build_id)
        if not manifest_path.exists():
            raise FileNotFoundError(f"No build manifest found for '{build_id}'.")
        return BuildManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))

    def list_builds(self) -> list[BuildManifest]:
        """Return every build manifest currently stored in the experiments root."""
        manifests: list[BuildManifest] = []
        for manifest_path in sorted(self.paths.builds_dir.glob("*/manifest.json")):
            manifests.append(
                BuildManifest.model_validate_json(
                    manifest_path.read_text(encoding="utf-8")
                )
            )
        return manifests

    def run_build(
        self,
        corpus_id: str,
        config: BuildConfig,
        *,
        force: bool = False,
    ) -> BuildManifest:
        """Synchronous convenience wrapper around ``run_build_async``."""
        return asyncio.run(self.run_build_async(corpus_id, config, force=force))

    async def run_build_async(
        self,
        corpus_id: str,
        config: BuildConfig,
        *,
        force: bool = False,
    ) -> BuildManifest:
        """Execute one artifact build and persist its manifest."""
        corpus = self.corpora.load_corpus(corpus_id)
        build_id = self._build_id(corpus.corpus_id, config)
        build_dir = self._build_dir(build_id)
        manifest_path = self._manifest_path(build_id)

        if manifest_path.exists() and not force:
            return self.load_build(build_id)

        if build_dir.exists() and force:
            shutil.rmtree(build_dir)
        build_dir.mkdir(parents=True, exist_ok=True)

        manifest = BuildManifest(
            build_id=build_id,
            corpus_id=corpus.corpus_id,
            build_kind=config.kind,
            build_label=config.label,
            artifact_family=config.artifact_family,
            status="running",
            created_at=_utc_now(),
            config=config.model_dump(mode="json"),
        )
        atomic_write_text(manifest_path, manifest.model_dump_json(indent=2))

        adapter = self._adapters.get(config.kind)
        if adapter is None:
            raise ValueError(f"No artifact-build adapter registered for '{config.kind}'.")

        try:
            result = await adapter.build(
                BuildExecutionContext(
                    build_id=build_id,
                    build_dir=build_dir,
                    corpus=corpus,
                    config=config,
                )
            )
        except Exception as exc:
            manifest.status = "failed"
            manifest.completed_at = _utc_now()
            manifest.error = str(exc)
            atomic_write_text(manifest_path, manifest.model_dump_json(indent=2))
            raise

        manifest.status = "completed"
        manifest.completed_at = _utc_now()
        manifest.outputs = result.outputs
        manifest.documents = result.documents
        manifest.metrics = result.metrics
        atomic_write_text(manifest_path, manifest.model_dump_json(indent=2))
        return manifest
