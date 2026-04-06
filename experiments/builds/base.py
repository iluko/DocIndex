"""Base classes for experiment artifact builds."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from experiments.models import ArtifactOutput, BuildConfig, BuiltDocumentArtifact, CorpusManifest


@dataclass(frozen=True)
class BuildExecutionContext:
    """All state an artifact-build adapter needs for one run."""

    build_id: str
    build_dir: Path
    corpus: CorpusManifest
    config: BuildConfig


@dataclass
class BuildAdapterResult:
    """Outputs emitted by one build adapter."""

    outputs: list[ArtifactOutput]
    documents: list[BuiltDocumentArtifact]
    metrics: dict


class ArtifactBuildAdapter(ABC):
    """Interface implemented by all artifact-build strategies."""

    kind: str
    artifact_family: str

    @abstractmethod
    async def build(self, context: BuildExecutionContext) -> BuildAdapterResult:
        """Produce one isolated artifact set for the supplied corpus."""
