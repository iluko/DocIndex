"""Canonical experiment-harness models.

These models define the stable contracts between:
- corpus normalization
- artifact building / ingestion
- future retrieval and answer adapters
- evaluation and comparison reports

The intent is to keep the experiments framework modular. Later phases can add
new retrieval or answer profiles without having to redesign the build layer.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, Field, field_validator

from utils import (
    RELATED_DOCS_MODE_DEFAULT,
    RELATED_DOCS_MODES,
    clamp_master_top_sections_target,
)


SUPPORTED_CORPUS_EXTENSIONS: frozenset[str] = frozenset(
    {".pdf", ".md", ".markdown", ".docx"}
)

ARTIFACT_FAMILIES: frozenset[str] = frozenset({"pageindex_tree", "rag_chunks", "rag_vector"})
QUESTION_TYPES: tuple[str, ...] = (
    "unspecified",
    "factual",
    "multi_doc_synthesis",
    "workflow_process",
    "compare_contrast",
    "buried_fact",
    "contradictory_docs",
    "unanswerable",
)


class CorpusDocumentInput(BaseModel):
    """Input metadata used when registering one source file in a corpus."""

    source_path: str
    doc_id: str | None = None
    doc_title: str | None = None
    doc_type: str = "technical_spec"


class CorpusDocument(BaseModel):
    """Normalized, copied document record stored in a corpus manifest."""

    doc_id: str
    doc_title: str
    doc_type: str
    source_path: str
    normalized_path: str
    extension: str
    size_bytes: int
    sha256: str


class CorpusManifest(BaseModel):
    """One reusable document set that downstream builds can consume."""

    version: str = "1.0"
    corpus_id: str
    name: str
    description: str | None = None
    created_at: str
    documents: list[CorpusDocument] = Field(default_factory=list)


class ArtifactOutput(BaseModel):
    """One output path emitted by a build."""

    label: str
    path: str
    description: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class BuiltDocumentArtifact(BaseModel):
    """Per-document artifact bookkeeping for one completed build."""

    doc_id: str
    artifact_path: str | None = None
    trace_path: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class PageIndexBuildConfig(BaseModel):
    """Build one PageIndex/master-tree artifact set for a corpus."""

    kind: Literal["pageindex"] = "pageindex"
    label: str = "pageindex"
    artifact_family: Literal["pageindex_tree"] = "pageindex_tree"
    model: str | None = None
    relationship_mode: str = RELATED_DOCS_MODE_DEFAULT
    top_sections_target: int = 4
    pageindex_opts: dict[str, Any] = Field(default_factory=dict)

    @field_validator("relationship_mode")
    @classmethod
    def _validate_relationship_mode(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in RELATED_DOCS_MODES:
            raise ValueError(
                f"Invalid relationship_mode '{value}'. "
                f"Expected one of: {sorted(RELATED_DOCS_MODES)}."
            )
        return normalized

    @field_validator("top_sections_target")
    @classmethod
    def _clamp_top_sections_target(cls, value: int) -> int:
        return clamp_master_top_sections_target(value)


class RAGBuildConfig(BaseModel):
    """Build a chunked, flat RAG artifact set for a corpus."""

    kind: Literal["rag"] = "rag"
    label: str = "rag_standard"
    artifact_family: Literal["rag_chunks"] = "rag_chunks"
    chunk_size_tokens: int = 700
    chunk_overlap_tokens: int = 120
    min_chunk_tokens: int = 80

    @field_validator("chunk_size_tokens")
    @classmethod
    def _validate_chunk_size(cls, value: int) -> int:
        if value < 100:
            raise ValueError("chunk_size_tokens must be at least 100.")
        return value

    @field_validator("chunk_overlap_tokens")
    @classmethod
    def _validate_chunk_overlap(cls, value: int, info) -> int:
        chunk_size = info.data.get("chunk_size_tokens", 700)
        if value < 0:
            raise ValueError("chunk_overlap_tokens cannot be negative.")
        if value >= chunk_size:
            raise ValueError("chunk_overlap_tokens must be smaller than chunk_size_tokens.")
        return value

    @field_validator("min_chunk_tokens")
    @classmethod
    def _validate_min_chunk_tokens(cls, value: int) -> int:
        if value < 1:
            raise ValueError("min_chunk_tokens must be positive.")
        return value


class VectorRAGBuildConfig(BaseModel):
    """Build a local vector-RAG artifact family for experiments only."""

    kind: Literal["rag_vector"] = "rag_vector"
    label: str = "rag_vector"
    artifact_family: Literal["rag_vector"] = "rag_vector"
    chunk_size_tokens: int = 700
    chunk_overlap_tokens: int = 120
    min_chunk_tokens: int = 80
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    normalize_embeddings: bool = True
    vector_backend: Literal["auto", "brute_force", "hnsw"] = "auto"
    persist_lexical_corpus: bool = True

    @field_validator("chunk_size_tokens")
    @classmethod
    def _validate_chunk_size(cls, value: int) -> int:
        if value < 100:
            raise ValueError("chunk_size_tokens must be at least 100.")
        return value

    @field_validator("chunk_overlap_tokens")
    @classmethod
    def _validate_chunk_overlap(cls, value: int, info) -> int:
        chunk_size = info.data.get("chunk_size_tokens", 700)
        if value < 0:
            raise ValueError("chunk_overlap_tokens cannot be negative.")
        if value >= chunk_size:
            raise ValueError("chunk_overlap_tokens must be smaller than chunk_size_tokens.")
        return value

    @field_validator("min_chunk_tokens")
    @classmethod
    def _validate_min_chunk_tokens(cls, value: int) -> int:
        if value < 1:
            raise ValueError("min_chunk_tokens must be positive.")
        return value


BuildConfig = Annotated[
    Union[PageIndexBuildConfig, RAGBuildConfig, VectorRAGBuildConfig],
    Field(discriminator="kind"),
]


class BuildManifest(BaseModel):
    """Persistent record of one completed or in-progress artifact build."""

    version: str = "1.0"
    build_id: str
    corpus_id: str
    build_kind: str
    build_label: str
    artifact_family: str
    status: Literal["pending", "running", "completed", "failed"] = "pending"
    created_at: str
    completed_at: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)
    outputs: list[ArtifactOutput] = Field(default_factory=list)
    documents: list[BuiltDocumentArtifact] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None

    @field_validator("artifact_family")
    @classmethod
    def _validate_artifact_family(cls, value: str) -> str:
        if value not in ARTIFACT_FAMILIES:
            raise ValueError(
                f"Invalid artifact_family '{value}'. "
                f"Expected one of: {sorted(ARTIFACT_FAMILIES)}."
            )
        return value


class RetrievalProfileSpec(BaseModel):
    """Future retrieval profile description for experiment runs.

    Added now so the experiments harness has stable contracts before the
    retrieval layer is implemented in later phases.
    """

    profile_id: str
    artifact_family: str
    mode: str
    reasoning_effort: str | None = None
    advanced_retrieval: bool = False
    config: dict[str, Any] = Field(default_factory=dict)


class AnswerProfileSpec(BaseModel):
    """Future answer-generation profile description."""

    profile_id: str
    prompt_family: str = "aligned_default"
    reasoning_effort: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)


class EvalRunSpec(BaseModel):
    """Future evaluation-run contract.

    Not used yet, but defined now so later retrieval/eval work plugs into a
    stable run manifest shape instead of inventing one ad hoc.
    """

    run_id: str
    corpus_id: str
    build_id: str
    retrieval_profile_id: str
    answer_profile_id: str
    suite_id: str
    status: Literal["pending", "running", "completed", "failed"] = "pending"
    created_at: str
    completed_at: str | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)
    outputs: dict[str, Any] = Field(default_factory=dict)


class RunEntrySpec(BaseModel):
    """One build/profile combination to execute inside a comparison run."""

    label: str
    build_id: str
    retrieval_profile: RetrievalProfileSpec
    answer_profile: AnswerProfileSpec


class RunEntryMetrics(BaseModel):
    """Comparable latency and token metrics for one completed run entry."""

    ttft_seconds: float
    total_time_seconds: float
    retrieval_time_seconds: float
    answer_time_seconds: float
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    llm_calls: int
    estimated_token_usage: bool = False
    retrieved_context_tokens: int = 0


class RunEntrySummary(BaseModel):
    """High-level retrieval/answer output for one executed entry."""

    label: str
    build_id: str
    artifact_family: str
    retrieval_profile_id: str
    answer_profile_id: str
    status: Literal["pending", "running", "completed", "failed", "skipped"] = "completed"
    answer: str = ""
    selected_docs: list[str] = Field(default_factory=list)
    selected_nodes: list[str] = Field(default_factory=list)
    retrieved_context_preview: str = ""
    sources: list[dict[str, Any]] = Field(default_factory=list)
    metrics: RunEntryMetrics | None = None
    trace_path: str | None = None
    error: str | None = None


class ComparisonSummary(BaseModel):
    """Aggregate roll-up for one comparison run."""

    fastest_entry_label: str | None = None
    lowest_tokens_entry_label: str | None = None
    largest_context_entry_label: str | None = None
    completed_entries: int = 0
    failed_entries: int = 0


class ComparisonRunManifest(BaseModel):
    """Persisted manual-comparison run across multiple entries."""

    version: str = "1.1"
    run_id: str
    title: str
    query: str
    conversation_context: str | None = None
    question_type: str = "unspecified"
    suite_id: str | None = None
    case_id: str | None = None
    created_at: str
    completed_at: str | None = None
    model: str
    max_concurrency: int | None = None
    spec_hash: str | None = None
    requested_entries: list[RunEntrySpec] = Field(default_factory=list)
    source_run_id: str | None = None
    status: Literal["pending", "running", "completed", "failed"] = "pending"
    entries: list[RunEntrySummary] = Field(default_factory=list)
    summary: ComparisonSummary | None = None
    report_paths: dict[str, str] = Field(default_factory=dict)
    operator_winner_label: str | None = None
    notes: str | None = None
    error: str | None = None

    @field_validator("question_type")
    @classmethod
    def _validate_question_type(cls, value: str) -> str:
        normalized = value.strip().lower().replace("/", "_").replace("-", "_")
        if normalized not in QUESTION_TYPES:
            raise ValueError(
                f"Invalid question_type '{value}'. Expected one of: {list(QUESTION_TYPES)}."
            )
        return normalized
