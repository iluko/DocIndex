"""Experiment harness for corpus builds, retrieval variants, and evaluations.

This package is intentionally separate from the main app/CLI orchestration.
It reuses the production ingestion and retrieval modules, but stores its own
artifacts under ``experiments/artifacts/`` so comparisons do not disturb the
main project's active indexes.
"""

from experiments.corpora import CorpusStore
from experiments.models import (
    AnswerProfileSpec,
    ArtifactOutput,
    BuildManifest,
    BuiltDocumentArtifact,
    CorpusDocument,
    CorpusDocumentInput,
    CorpusManifest,
    EvalRunSpec,
    PageIndexBuildConfig,
    RAGBuildConfig,
    RetrievalProfileSpec,
)
from experiments.builds.registry import ArtifactBuildRunner

__all__ = [
    "AnswerProfileSpec",
    "ArtifactBuildRunner",
    "ArtifactOutput",
    "BuildManifest",
    "BuiltDocumentArtifact",
    "CorpusDocument",
    "CorpusDocumentInput",
    "CorpusManifest",
    "CorpusStore",
    "EvalRunSpec",
    "PageIndexBuildConfig",
    "RAGBuildConfig",
    "RetrievalProfileSpec",
]
