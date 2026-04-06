"""Evaluation-suite helpers for the experiments evaluation framework."""

from __future__ import annotations

import csv
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import re

from experiments.evals.models import EvaluationCase, EvaluationSuite
from experiments.layout import resolve_experiment_paths
from experiments.models import ComparisonRunManifest
from utils import atomic_write_text


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _default_suite_id(run_ids: list[str]) -> str:
    suffix = run_ids[0][:8] if run_ids else "empty"
    return f"adhoc_{suffix}"


def _slugify(value: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "_", value.strip().lower())
    text = re.sub(r"_+", "_", text).strip("_")
    return text or "suite"


def _coerce_suite_id(value: str | None, fallback: str) -> str:
    return _slugify(value or fallback)


def _clean_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _split_pipe_list(value: object) -> list[str]:
    text = _clean_text(value)
    if not text:
        return []
    return [part.strip() for part in text.split("|") if part.strip()]


def _coerce_case_id(value: object, *, index: int, question: str) -> str:
    text = _clean_text(value)
    if text:
        return _slugify(text)
    return f"case_{index:03d}_{_slugify(question)[:48]}"


def _case_from_row(row: dict[str, object], *, index: int) -> EvaluationCase:
    question = _clean_text(row.get("question")) or ""
    if not question:
        raise ValueError(f"Row {index} is missing a question.")
    return EvaluationCase(
        case_id=_coerce_case_id(row.get("case_id"), index=index, question=question),
        question=question,
        question_type=_clean_text(row.get("question_type")) or "unspecified",
        business_scenario=_clean_text(row.get("business_scenario")),
        target_persona=_clean_text(row.get("target_persona")),
        expected_answerability=_clean_text(row.get("expected_answerability")) or "unknown",
        severity_if_wrong=_clean_text(row.get("severity_if_wrong")) or "medium",
        tags=_split_pipe_list(row.get("tags")),
        ground_truth_answer=_clean_text(row.get("ground_truth_answer")),
        must_include_facts=_split_pipe_list(row.get("must_include_facts")),
        must_not_claim=_split_pipe_list(row.get("must_not_claim")),
        gold_sources=_split_pipe_list(row.get("gold_sources")),
        notes=_clean_text(row.get("notes")),
    )


def suite_csv_template() -> str:
    return "\n".join(
        [
            "case_id,question,ground_truth_answer,question_type,expected_answerability,business_scenario,target_persona,must_include_facts,must_not_claim,gold_sources,notes",
            'gold_001,"What still works if a tenant has no transaction history?","KB search, case studies, discovery guidance, catalog browsing, email generation, and competitor cards still work. Cross-sell and data-driven recommendations degrade because transaction-driven signals are missing.",workflow_process,answerable,onboarding_degraded_state,implementation_manager,"KB search still works|case studies still work|cross-sell degrades without transaction signals","transaction history is mandatory for all functionality","nxgen_module_deepdive_m5::0026|nxgen_module_deepdive_m5::0025","Minimal gold-backed example row"',
            'gold_002,"If the documents do not answer the pricing question, what should the system do?","The system should abstain, say the answer is not supported by the provided corpus, and point the user to a human owner.",unanswerable,unanswerable,pricing_gap,sales_enablement,"abstain explicitly|state that corpus does not support the answer","invent a price|pretend the docs contain pricing","",\"Example unanswerable row\"',
        ]
    )


def suite_json_template() -> str:
    payload = {
        "version": "2.0",
        "suite_id": "golden_eval_sample",
        "name": "Golden Eval Sample",
        "description": "Minimal gold-backed suite template for the experiments evaluation dashboard.",
        "created_at": _utc_now(),
        "cases": [
            {
                "case_id": "gold_001",
                "question": "What still works if a tenant has no transaction history?",
                "question_type": "workflow_process",
                "business_scenario": "onboarding_degraded_state",
                "target_persona": "implementation_manager",
                "expected_answerability": "answerable",
                "severity_if_wrong": "high",
                "ground_truth_answer": "KB search, case studies, discovery guidance, catalog browsing, email generation, and competitor cards still work. Cross-sell and data-driven recommendations degrade because transaction-driven signals are missing.",
                "must_include_facts": [
                    "KB search still works",
                    "case studies still work",
                    "cross-sell degrades without transaction signals",
                ],
                "must_not_claim": [
                    "transaction history is mandatory for all functionality",
                ],
                "gold_sources": [
                    "nxgen_module_deepdive_m5::0026",
                    "nxgen_module_deepdive_m5::0025",
                ],
                "tags": ["golden", "workflow_process"],
                "notes": "Minimal gold-backed example case.",
            }
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def build_suite_from_runs(
    runs: list[ComparisonRunManifest],
    *,
    suite_id: str | None = None,
    name: str | None = None,
    description: str | None = None,
) -> EvaluationSuite:
    """Create an ad hoc suite snapshot from comparison runs."""
    ordered_runs = sorted(runs, key=lambda run: run.created_at)
    cases = [
        EvaluationCase(
            case_id=f"run_{run.run_id}",
            question=run.query,
            question_type=run.question_type,
            business_scenario=run.question_type.replace("_", " "),
            target_persona="mixed",
            expected_answerability="unknown",
            severity_if_wrong="medium",
            tags=[run.question_type, "adhoc"],
        )
        for run in ordered_runs
    ]
    resolved_suite_id = suite_id or _default_suite_id([run.run_id for run in ordered_runs])
    return EvaluationSuite(
        suite_id=resolved_suite_id,
        name=name or f"Ad Hoc Evaluation {resolved_suite_id}",
        description=description or "Evaluation suite derived from existing comparison runs.",
        created_at=_utc_now(),
        source_run_ids=[run.run_id for run in ordered_runs],
        cases=cases,
    )


class EvaluationSuiteStore:
    """Persist and load evaluation suites under experiments artifacts."""

    def __init__(self, root: str | Path | None = None):
        self.paths = resolve_experiment_paths(root)

    def _suite_path(self, suite_id: str) -> Path:
        return self.paths.eval_suites_dir / f"{suite_id}.json"

    def save_suite(self, suite: EvaluationSuite) -> EvaluationSuite:
        atomic_write_text(self._suite_path(suite.suite_id), suite.model_dump_json(indent=2))
        return suite

    def load_suite(self, suite_id: str) -> EvaluationSuite:
        path = self._suite_path(suite_id)
        if not path.exists():
            raise FileNotFoundError(f"No evaluation suite found for '{suite_id}'.")
        return EvaluationSuite.model_validate_json(path.read_text(encoding="utf-8"))

    def list_suites(self) -> list[EvaluationSuite]:
        suites: list[EvaluationSuite] = []
        for suite_path in sorted(self.paths.eval_suites_dir.glob("*.json")):
            suites.append(
                EvaluationSuite.model_validate_json(suite_path.read_text(encoding="utf-8"))
            )
        return suites

    def create_suite_from_runs(
        self,
        runs: list[ComparisonRunManifest],
        *,
        suite_id: str | None = None,
        name: str | None = None,
        description: str | None = None,
        persist: bool = True,
    ) -> EvaluationSuite:
        suite = build_suite_from_runs(
            runs,
            suite_id=suite_id,
            name=name,
            description=description,
        )
        if persist:
            self.save_suite(suite)
        return suite

    def import_suite_from_csv_text(
        self,
        csv_text: str,
        *,
        suite_id: str | None = None,
        name: str | None = None,
        description: str | None = None,
        persist: bool = True,
    ) -> EvaluationSuite:
        reader = csv.DictReader(io.StringIO(csv_text))
        rows = [row for row in reader]
        if not rows:
            raise ValueError("The uploaded CSV does not contain any rows.")
        if "question" not in (reader.fieldnames or []):
            raise ValueError("The uploaded CSV must contain a 'question' column.")

        cases = [_case_from_row(row, index=index + 1) for index, row in enumerate(rows)]
        resolved_suite_id = _coerce_suite_id(suite_id, name or "golden_dataset")
        suite = EvaluationSuite(
            version="2.0",
            suite_id=resolved_suite_id,
            name=name or f"Imported Suite {resolved_suite_id}",
            description=description or "Imported from CSV for the experiments evaluation harness.",
            created_at=_utc_now(),
            cases=cases,
        )
        if persist:
            self.save_suite(suite)
        return suite

    def import_suite_from_json_text(
        self,
        json_text: str,
        *,
        suite_id: str | None = None,
        name: str | None = None,
        description: str | None = None,
        persist: bool = True,
    ) -> EvaluationSuite:
        payload = json.loads(json_text)
        if isinstance(payload, list):
            cases = [_case_from_row(row, index=index + 1) for index, row in enumerate(payload)]
            resolved_suite_id = _coerce_suite_id(suite_id, name or "golden_dataset")
            suite = EvaluationSuite(
                version="2.0",
                suite_id=resolved_suite_id,
                name=name or f"Imported Suite {resolved_suite_id}",
                description=description or "Imported from JSON for the experiments evaluation harness.",
                created_at=_utc_now(),
                cases=cases,
            )
        else:
            payload = dict(payload)
            if suite_id:
                payload["suite_id"] = _coerce_suite_id(suite_id, suite_id)
            elif payload.get("suite_id"):
                payload["suite_id"] = _coerce_suite_id(str(payload["suite_id"]), "golden_dataset")
            else:
                payload["suite_id"] = _coerce_suite_id(None, name or "golden_dataset")
            if name:
                payload["name"] = name
            elif not payload.get("name"):
                payload["name"] = f"Imported Suite {payload['suite_id']}"
            if description:
                payload["description"] = description
            payload.setdefault("created_at", _utc_now())
            payload.setdefault("version", "2.0")
            suite = EvaluationSuite.model_validate(payload)
        if persist:
            self.save_suite(suite)
        return suite
