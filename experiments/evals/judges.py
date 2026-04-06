"""LLM-judge scoring for phase-2 experiment evaluations."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from experiments.evals.models import EvaluationCase, EvaluationEntryResult, JudgeScorecard
from utils import (
    compact_json,
    create_chat_completion,
    extract_llm_text,
    get_sync_client,
    model_supports_explicit_temperature,
    normalize_reasoning_effort,
    parse_json_response,
)


def _safe_trace_payload(path: str | None) -> dict[str, Any]:
    if not path:
        return {}
    trace_path = Path(path)
    if not trace_path.exists():
        return {}
    try:
        return json.loads(trace_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _truncate(text: str, limit: int) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n...[truncated for evaluation]"


def _normalize_rubric_score(value: Any) -> float | None:
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    numeric = max(0.0, min(5.0, numeric))
    return round(numeric * 20.0, 2)


def _mean(values: list[float | None]) -> float | None:
    usable = [float(value) for value in values if value is not None]
    if not usable:
        return None
    return round(sum(usable) / len(usable), 2)


def _normalize_ref(text: str) -> str:
    return " ".join(text.strip().lower().split())


def _matched_gold_sources(
    gold_sources: list[str],
    actual_sources: list[dict[str, Any]],
) -> list[str]:
    if not gold_sources or not actual_sources:
        return []

    actual_node_refs = {
        _normalize_ref(str(source.get("node_ref")))
        for source in actual_sources
        if source.get("node_ref")
    }
    actual_doc_ids = {
        _normalize_ref(str(source.get("doc_id")))
        for source in actual_sources
        if source.get("doc_id")
    }

    matched: list[str] = []
    for candidate in gold_sources:
        normalized = _normalize_ref(candidate)
        if not normalized:
            continue
        if "::" in normalized:
            if normalized in actual_node_refs:
                matched.append(candidate)
        elif normalized in actual_doc_ids:
            matched.append(candidate)
    return matched


def _judge_prompt_payload(
    *,
    case: EvaluationCase | None,
    entry: EvaluationEntryResult,
    trace_payload: dict[str, Any],
) -> dict[str, Any]:
    retrieved_context = trace_payload.get("retrieved_context") or entry.answer_preview or ""
    sources = trace_payload.get("sources") or []
    return {
        "question": entry.question,
        "question_type": entry.question_type,
        "business_scenario": entry.business_scenario,
        "target_persona": entry.target_persona,
        "expected_answerability": entry.expected_answerability,
        "candidate_answer": _truncate(trace_payload.get("answer") or entry.answer_preview, 14000),
        "retrieved_context": _truncate(retrieved_context, 18000),
        "retrieved_sources": [
            {
                "node_ref": source.get("node_ref"),
                "doc_id": source.get("doc_id"),
                "section": source.get("section"),
                "page_range": source.get("page_range"),
            }
            for source in sources[:20]
        ],
        "ground_truth_answer": case.ground_truth_answer if case else None,
        "must_include_facts": case.must_include_facts if case else [],
        "must_not_claim": case.must_not_claim if case else [],
        "gold_sources": case.gold_sources if case else [],
        "notes": case.notes if case else None,
    }


def _scorecard_from_payload(
    *,
    judge_model: str,
    case: EvaluationCase | None,
    trace_payload: dict[str, Any],
    payload: dict[str, Any],
) -> JudgeScorecard:
    actual_sources = trace_payload.get("sources") or []
    gold_sources = case.gold_sources if case else []
    matched_gold_sources = _matched_gold_sources(gold_sources, actual_sources)
    source_match_score = None
    if gold_sources:
        source_match_score = round((len(matched_gold_sources) / len(gold_sources)) * 100.0, 2)

    groundedness = _normalize_rubric_score(payload.get("groundedness_score"))
    completeness = _normalize_rubric_score(payload.get("completeness_score"))
    directness = _normalize_rubric_score(payload.get("directness_score"))
    actionability = _normalize_rubric_score(payload.get("actionability_score"))
    business_fit = _normalize_rubric_score(payload.get("business_fit_score"))
    abstention = _normalize_rubric_score(payload.get("abstention_quality_score"))
    gold_alignment = _normalize_rubric_score(payload.get("gold_alignment_score"))
    required_fact_coverage = _normalize_rubric_score(payload.get("required_fact_coverage_score"))
    contradiction = _normalize_rubric_score(payload.get("contradiction_score"))

    technical_quality = _mean([groundedness, completeness, directness, abstention])
    business_quality = _mean([groundedness, actionability, business_fit])
    gold_quality = _mean(
        [gold_alignment, required_fact_coverage, contradiction, source_match_score]
    )
    if gold_quality is None:
        overall_quality = _mean(
            [
                technical_quality,
                technical_quality,
                business_quality,
                business_quality,
                business_quality,
            ]
        )
    else:
        overall_quality = round(
            (0.45 * float(technical_quality or 0.0))
            + (0.35 * float(business_quality or 0.0))
            + (0.20 * float(gold_quality)),
            2,
        )

    return JudgeScorecard(
        judge_model=judge_model,
        groundedness_score=groundedness,
        completeness_score=completeness,
        directness_score=directness,
        actionability_score=actionability,
        business_fit_score=business_fit,
        abstention_quality_score=abstention,
        gold_alignment_score=gold_alignment,
        required_fact_coverage_score=required_fact_coverage,
        contradiction_score=contradiction,
        source_match_score=source_match_score,
        technical_quality_score=technical_quality,
        business_quality_score=business_quality,
        overall_quality_score=overall_quality,
        used_ground_truth=bool(case and case.has_ground_truth),
        used_gold_sources=bool(gold_sources),
        missing_required_facts=[
            str(item).strip()
            for item in payload.get("missing_required_facts", []) or []
            if str(item).strip()
        ],
        forbidden_claim_violations=[
            str(item).strip()
            for item in payload.get("forbidden_claim_violations", []) or []
            if str(item).strip()
        ],
        matched_gold_sources=matched_gold_sources,
        strengths=[
            str(item).strip()
            for item in payload.get("strengths", []) or []
            if str(item).strip()
        ],
        risks=[
            str(item).strip()
            for item in payload.get("risks", []) or []
            if str(item).strip()
        ],
        rationale=str(payload.get("rationale", "")).strip() or None,
    )


def _judge_one_entry(
    *,
    client: Any,
    judge_model: str,
    judge_reasoning_effort: str | None,
    case: EvaluationCase | None,
    entry: EvaluationEntryResult,
    trace_payload: dict[str, Any],
) -> JudgeScorecard:
    system_prompt = (
        "You are grading retrieval-augmented QA outputs for an experiments dashboard. "
        "Return JSON only. Score each rubric dimension from 0 to 5 where 0 means very poor "
        "and 5 means excellent. Judge groundedness primarily against the retrieved context. "
        "If a ground-truth answer is present, use it to judge alignment, fact coverage, and "
        "contradiction. If no ground truth is present, return null for those gold-specific scores. "
        "If there are no gold sources, return null for source-based considerations. "
        "Be strict about unsupported claims."
    )
    output_schema = {
        "groundedness_score": "0-5 number",
        "completeness_score": "0-5 number",
        "directness_score": "0-5 number",
        "actionability_score": "0-5 number",
        "business_fit_score": "0-5 number",
        "abstention_quality_score": "0-5 number",
        "gold_alignment_score": "0-5 number or null",
        "required_fact_coverage_score": "0-5 number or null",
        "contradiction_score": "0-5 number or null; 5 means no contradiction with gold answer",
        "missing_required_facts": ["short string"],
        "forbidden_claim_violations": ["short string"],
        "strengths": ["short string"],
        "risks": ["short string"],
        "rationale": "short paragraph",
    }
    user_prompt = (
        "Grade this answer payload.\n\n"
        f"Expected JSON schema:\n{compact_json(output_schema)}\n\n"
        f"Evaluation payload:\n{compact_json(_judge_prompt_payload(case=case, entry=entry, trace_payload=trace_payload))}"
    )
    request_kwargs: dict[str, Any] = {
        "client": client,
        "model": judge_model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "reasoning_effort": normalize_reasoning_effort(judge_reasoning_effort),
    }
    if model_supports_explicit_temperature(judge_model):
        request_kwargs["temperature"] = 0.0
    response = create_chat_completion(**request_kwargs)
    payload = parse_json_response(extract_llm_text(response))
    if not isinstance(payload, dict):
        raise ValueError("Judge response did not return a JSON object.")
    return _scorecard_from_payload(
        judge_model=judge_model,
        case=case,
        trace_payload=trace_payload,
        payload=payload,
    )


def score_entries_with_judge(
    *,
    entries: list[EvaluationEntryResult],
    suite_lookup: dict[str, EvaluationCase],
    judge_model: str,
    judge_reasoning_effort: str | None = None,
) -> tuple[list[EvaluationEntryResult], str | None]:
    """Layer judge-based scoring onto already evaluated entries."""
    try:
        client = get_sync_client()
    except Exception as exc:  # pragma: no cover - depends on local env and provider config
        error = f"Judge scoring unavailable: {exc}"
        for entry in entries:
            entry.judge_error = error
        return entries, error

    judged_count = 0
    first_error: str | None = None
    for entry in entries:
        if entry.status != "completed":
            continue
        trace_payload = _safe_trace_payload(entry.trace_path)
        if not trace_payload:
            entry.judge_error = "No trace payload was available for judge scoring."
            first_error = first_error or entry.judge_error
            continue
        try:
            entry.judge_scorecard = _judge_one_entry(
                client=client,
                judge_model=judge_model,
                judge_reasoning_effort=judge_reasoning_effort,
                case=suite_lookup.get(entry.case_id),
                entry=entry,
                trace_payload=trace_payload,
            )
            entry.judge_error = None
            judged_count += 1
        except Exception as exc:  # pragma: no cover - depends on provider behavior
            entry.judge_error = str(exc)
            first_error = first_error or str(exc)

    if judged_count == 0 and first_error:
        return entries, f"Judge scoring did not complete for any entry: {first_error}"
    return entries, first_error
