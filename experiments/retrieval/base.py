"""Base retrieval-adapter contracts for experiment runs."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from experiments.models import BuildManifest, RetrievalProfileSpec


@dataclass(frozen=True)
class RetrievalExecutionContext:
    """Context passed into each retrieval adapter for one run entry."""

    build: BuildManifest
    build_dir: Path
    query: str
    model: str
    conversation_context: str | None
    retrieval_profile: RetrievalProfileSpec


@dataclass
class RetrievalExecutionResult:
    """Evidence and trace returned by a retrieval adapter before final answering."""

    selected_docs: list[str] = field(default_factory=list)
    selected_nodes: list[str] = field(default_factory=list)
    retrieved_context: str = ""
    sources: list[dict[str, Any]] = field(default_factory=list)
    trace: dict[str, Any] = field(default_factory=dict)
    retrieval_metrics: dict[str, Any] = field(default_factory=dict)


class RetrievalAdapter(ABC):
    """Interface implemented by all experiment retrieval strategies."""

    mode: str
    artifact_family: str

    @abstractmethod
    async def retrieve(self, context: RetrievalExecutionContext) -> RetrievalExecutionResult:
        """Return evidence and trace for one query against one artifact build."""
