"""Canonical models for the experiments evaluation framework."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from experiments.models import QUESTION_TYPES


EVAL_ANSWERABILITY = ("answerable", "unanswerable", "unknown")
EVAL_SEVERITY = ("low", "medium", "high", "critical")


class EvaluationCase(BaseModel):
    """One question inside an evaluation suite."""

    case_id: str
    question: str
    question_type: str = "unspecified"
    business_scenario: str | None = None
    target_persona: str | None = None
    expected_answerability: Literal["answerable", "unanswerable", "unknown"] = "unknown"
    severity_if_wrong: Literal["low", "medium", "high", "critical"] = "medium"
    tags: list[str] = Field(default_factory=list)
    ground_truth_answer: str | None = None
    must_include_facts: list[str] = Field(default_factory=list)
    must_not_claim: list[str] = Field(default_factory=list)
    gold_sources: list[str] = Field(default_factory=list)
    notes: str | None = None

    @field_validator("question_type")
    @classmethod
    def _validate_question_type(cls, value: str) -> str:
        normalized = value.strip().lower().replace("/", "_").replace("-", "_")
        if normalized not in QUESTION_TYPES:
            raise ValueError(
                f"Invalid question_type '{value}'. Expected one of: {list(QUESTION_TYPES)}."
            )
        return normalized

    @field_validator("expected_answerability", mode="before")
    @classmethod
    def _normalize_answerability(cls, value: Any) -> str:
        text = str(value or "unknown").strip().lower()
        return text if text in EVAL_ANSWERABILITY else "unknown"

    @field_validator("severity_if_wrong", mode="before")
    @classmethod
    def _normalize_severity(cls, value: Any) -> str:
        text = str(value or "medium").strip().lower()
        return text if text in EVAL_SEVERITY else "medium"

    @field_validator("tags", "must_include_facts", "must_not_claim", "gold_sources", mode="before")
    @classmethod
    def _normalize_list_fields(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            raw_items = value.split("|")
        elif isinstance(value, (list, tuple, set)):
            raw_items = list(value)
        else:
            raw_items = [value]
        items: list[str] = []
        for item in raw_items:
            text = str(item).strip()
            if text:
                items.append(text)
        return items

    @property
    def has_ground_truth(self) -> bool:
        return bool((self.ground_truth_answer or "").strip())

    @property
    def has_gold_sources(self) -> bool:
        return bool(self.gold_sources)


class EvaluationSuite(BaseModel):
    """A reusable collection of evaluation cases."""

    version: str = "1.0"
    suite_id: str
    name: str
    description: str | None = None
    created_at: str
    source_run_ids: list[str] = Field(default_factory=list)
    cases: list[EvaluationCase] = Field(default_factory=list)

    def case_lookup(self) -> dict[str, EvaluationCase]:
        return {case.case_id: case for case in self.cases}

    @property
    def gold_case_count(self) -> int:
        return sum(1 for case in self.cases if case.has_ground_truth or case.has_gold_sources)


class JudgeScorecard(BaseModel):
    """Phase-2 LLM-judge scorecard with optional gold/reference checks."""

    judge_model: str
    judge_prompt_version: str = "phase2_v1"
    groundedness_score: float | None = None
    completeness_score: float | None = None
    directness_score: float | None = None
    actionability_score: float | None = None
    business_fit_score: float | None = None
    abstention_quality_score: float | None = None
    gold_alignment_score: float | None = None
    required_fact_coverage_score: float | None = None
    contradiction_score: float | None = None
    source_match_score: float | None = None
    technical_quality_score: float | None = None
    business_quality_score: float | None = None
    overall_quality_score: float | None = None
    used_ground_truth: bool = False
    used_gold_sources: bool = False
    missing_required_facts: list[str] = Field(default_factory=list)
    forbidden_claim_violations: list[str] = Field(default_factory=list)
    matched_gold_sources: list[str] = Field(default_factory=list)
    strengths: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    rationale: str | None = None


class DeterministicScorecard(BaseModel):
    """Phase-1 technical scorecard built only from deterministic signals."""

    technical_score: float
    efficiency_score: float
    reliability_score: float
    retrieval_discipline_score: float
    notes: list[str] = Field(default_factory=list)


class EvaluationEntryResult(BaseModel):
    """Deterministic evaluation output for one source run entry."""

    source_run_id: str
    source_run_title: str
    case_id: str
    question: str
    question_type: str
    business_scenario: str | None = None
    target_persona: str | None = None
    expected_answerability: Literal["answerable", "unanswerable", "unknown"] = "unknown"
    ground_truth_available: bool = False
    gold_sources_available: bool = False
    build_id: str
    build_label: str | None = None
    corpus_id: str | None = None
    artifact_family: str
    retrieval_profile_id: str
    answer_profile_id: str
    label: str
    status: Literal["pending", "running", "completed", "failed", "skipped"] = "completed"
    operator_winner: bool = False
    fastest_proxy: bool = False
    lowest_tokens_proxy: bool = False
    answer_preview: str = ""
    trace_path: str | None = None
    source_count: int = 0
    metrics: dict[str, Any] = Field(default_factory=dict)
    diagnostics: dict[str, Any] = Field(default_factory=dict)
    scorecard: DeterministicScorecard | None = None
    judge_scorecard: JudgeScorecard | None = None
    error: str | None = None
    judge_error: str | None = None


class EvaluationSummary(BaseModel):
    """Top-level roll-up for one deterministic evaluation run."""

    source_runs_scanned: int = 0
    entries_scored: int = 0
    completed_entries: int = 0
    failed_entries: int = 0
    skipped_entries: int = 0
    best_profile_by_technical_score: str | None = None
    fastest_profile: str | None = None
    lowest_tokens_profile: str | None = None
    operator_favorite_profile: str | None = None
    average_technical_score: float = 0.0
    judge_enabled: bool = False
    entries_judged: int = 0
    gold_backed_entries: int = 0
    average_judge_score: float = 0.0
    average_business_score: float = 0.0
    average_gold_alignment_score: float = 0.0
    best_profile_by_judge_score: str | None = None
    best_profile_by_business_score: str | None = None


class EvaluationRunManifest(BaseModel):
    """Persisted deterministic evaluation snapshot for one set of runs."""

    version: str = "2.0"
    eval_run_id: str
    title: str
    created_at: str
    completed_at: str | None = None
    status: Literal["pending", "running", "completed", "failed"] = "pending"
    judge_enabled: bool = False
    judge_model: str | None = None
    suite: EvaluationSuite
    source_run_ids: list[str] = Field(default_factory=list)
    entries: list[EvaluationEntryResult] = Field(default_factory=list)
    summary: EvaluationSummary | None = None
    aggregate_payload: dict[str, Any] = Field(default_factory=dict)
    report_paths: dict[str, str] = Field(default_factory=dict)
    error: str | None = None
    judge_error: str | None = None
