# Experiments Evaluation Framework

This document defines the recommended evaluation framework for the `experiments/` layer of the Hybrid Approach repository.

It is a design document, not an implementation report.

The intent is to answer, in one place:

- what this evaluation framework is
- why it should exist
- why it should live in `experiments/`
- how it differs from DeepEval, RAGAS, LangSmith, and TruLens
- what data it should collect
- how scoring should work
- what the dashboard should show
- how the system should be implemented in phases

This document is written to guide implementation later.

## 1. Executive Summary

The experiments harness already captures useful comparison data:

- corpus identity
- build identity
- retrieval profile
- answer profile
- answer text
- selected docs and nodes
- per-entry metrics
- retrieval traces
- operator winner labels
- reviewer notes
- question types

That is enough to justify a native evaluation framework instead of outsourcing the design to a generic third-party package.

The recommendation is:

- build a custom evaluation framework inside `experiments/`
- keep it local-first and artifact-aware
- support deterministic metrics, LLM-judge metrics, and human review together
- separate technical scoring from business scoring
- add a dashboard with both engineering and business views

The framework should not replace external tools conceptually. It should be the source of truth for this repository, while remaining compatible with external evaluators where useful.

## 2. Why Build This At All

The current experiments system is already a strong comparison harness, but it is not yet a full evaluation framework.

Today it can answer:

- which entry was faster
- which entry used fewer tokens
- which entry returned an answer
- which entry a human picked as the winner

It cannot yet answer, in a structured and repeatable way:

- which retrieval strategy is most grounded
- which strategy is best for multi-document synthesis
- which strategy is most useful for business users
- which strategy is worth its latency and token cost
- which strategy is best for a specific question type or scenario
- whether a pipeline is technically strong but business-weak
- whether a pipeline is business-useful but too expensive or unstable

The missing layer is formal evaluation.

## 3. Why It Should Live In `experiments/`

This work should live in `experiments/` because the evaluation framework is about controlled comparison, not user-facing production querying.

That matches the existing architecture:

- the main app is the product/demo interface
- the experiments app is the comparison and analysis environment

The evaluation system belongs next to:

- corpora
- builds
- retrieval profiles
- run manifests
- reports

It should not drive the main app architecture, and it should not require broad changes to the shared ingestion or retrieval engine unless a missing trace field is discovered.

## 4. Design Principles

The framework should follow these principles.

### 4.1 Local Source Of Truth

The canonical evaluation artifacts should be stored in `experiments/artifacts/`, not in a hosted external system.

Reason:

- reproducibility
- portability
- low coupling
- compatibility with existing run manifests and report paths

### 4.2 Evaluation Is Separate From Execution

A run produces evidence.

An evaluation scores that evidence.

This separation matters because:

- the same run can be evaluated multiple times under different rubric versions
- judge models can change
- human review can be layered on later
- evaluation should not force rerunning all underlying retrievals

### 4.3 Technical And Business Scoring Must Stay Separate

Do not collapse everything into one score too early.

The framework should have:

- a technical score family
- a business score family
- an optional composite only after both are stable

### 4.4 Artifact-Aware Evaluation

This repository is not a generic flat-chunk RAG repo.

It contains:

- flat lexical RAG
- local vector RAG
- deterministic PageIndex retrieval
- agentic PageIndex retrieval

The evaluation framework must understand those differences instead of pretending all pipelines are the same.

### 4.5 Human Review Is Not Optional

LLM-judge scoring is useful, but not enough.

Human review is needed for:

- trust
- business usefulness
- decision quality
- calibration of rubric prompts

### 4.6 Suites Are First-Class

Ad hoc runs should still work, but the framework should be centered around evaluation suites, not one-off questions.

This is the only way to create durable comparisons over time.

## 5. Current State Of The Experiments Layer

The experiments harness already has the right skeleton.

Existing assets include:

- corpus manifests
- build manifests
- retrieval profiles
- answer profiles
- comparison run manifests
- run entry metrics
- per-entry trace JSON
- exported reports
- question-type tags
- operator winner labels
- reviewer notes

Current data model anchors already exist in:

- `experiments/models.py`
- `experiments/runs/registry.py`
- `experiments/reports.py`
- `experiments_app.py`

The existing `EvalRunSpec` in `experiments/models.py` is the clearest sign that the repo was already moving toward a more formal eval layer.

## 6. What This Evaluation Framework Should Evaluate

The framework should evaluate four broad things.

### 6.1 Retrieval Behavior

Examples:

- did the system route to the right docs?
- did it retrieve sufficient evidence?
- did it over-fetch?
- did it waste context on noisy sections?
- did it need broadened routing?
- did advanced planning help?

### 6.2 Answer Behavior

Examples:

- is the answer grounded?
- is it complete?
- is it direct?
- are citations plausible and useful?
- does it abstain correctly when the answer is unavailable?

### 6.3 Operational Efficiency

Examples:

- latency
- tokens
- LLM calls
- cost proxies
- budget exhaustion
- stability and failure rates

### 6.4 Business Utility

Examples:

- would a human operator trust this answer?
- would this answer be actionable?
- is it good enough for stakeholder-facing usage?
- does it help decision-making?

## 7. Evaluation Object Model

The evaluation framework should introduce a new layer of persistent models under `experiments/`.

Recommended object model:

### 7.1 Evaluation Suite

A suite is a named collection of questions and metadata.

Suggested fields:

```json
{
  "suite_id": "sales_workflow_core_v1",
  "name": "Sales Workflow Core",
  "description": "Core business and technical workflow questions.",
  "version": "1.0",
  "created_at": "2026-04-05T00:00:00Z",
  "cases": []
}
```

### 7.2 Evaluation Case

Each case should contain:

```json
{
  "case_id": "wf_001",
  "question": "What is the onboarding flow and what happens if transaction history is missing?",
  "question_type": "workflow_process",
  "business_scenario": "customer_onboarding",
  "target_persona": "implementation_manager",
  "expected_answerability": "answerable",
  "severity_if_wrong": "high",
  "must_include_facts": [
    "onboarding goal",
    "degraded mode when transaction history is missing"
  ],
  "must_not_claim": [
    "transaction history is strictly required for all functionality"
  ],
  "gold_answer": null,
  "gold_sources": []
}
```

### 7.3 Evaluation Run

An evaluation run should point to:

- one suite
- one corpus
- one set of build/profile entries
- either existing comparison runs or a fresh execution path

### 7.4 Evaluation Result

Each evaluated answer should store:

- run reference
- case reference
- deterministic metrics snapshot
- judge scores
- human annotations
- derived technical score
- derived business score

## 8. Metric Families

The framework should score three classes of metrics.

## 8.1 Deterministic Metrics

These come directly from existing manifests and traces.

### Shared metrics

- completion rate
- failure rate
- skipped rate
- TTFT
- total time
- retrieval time
- answer time
- prompt tokens
- completion tokens
- total tokens
- LLM calls
- retrieved context tokens
- selected doc count
- selected node count

### `hybrid`-specific diagnostics

- routing broadened
- effective max docs
- effective max nodes
- verification applied
- node expansion applied
- planner output

### `pageindex`-specific diagnostics

- tool calls made
- tool-call budget
- content tokens used
- content-token budget
- explored docs
- tool budget exhausted
- content budget exhausted

### `rag_vector`-specific diagnostics

- vector backend
- query embedding time
- dense search time
- lexical search time
- rerank time
- candidate chunk count
- selected chunk count

These should be available in phase 1.

## 8.2 LLM-Judge Metrics

These require prompt-based evaluation.

Recommended first-wave dimensions:

### Answer quality

- groundedness
- completeness
- directness
- citation usefulness
- hallucination risk
- abstention quality

### Retrieval quality

- evidence adequacy
- evidence relevance
- evidence sufficiency
- evidence noise level

### Business quality

- actionability
- decision usefulness
- stakeholder fit
- trustworthiness
- clarity for non-technical users

All of these should be rubric-scored, not open-ended only.

## 8.3 Human Review Metrics

These are the highest-trust metrics and should be supported explicitly.

Examples:

- operator winner
- pass/fail business usefulness
- pass/fail technical sufficiency
- reviewer notes
- rank ordering of entries
- confidence score from reviewer

## 9. Proposed Scoring System

The framework should expose separate scorecards.

## 9.1 Technical Scorecard

Suggested dimensions:

- retrieval effectiveness
- grounded answer quality
- efficiency
- robustness
- traceability

Suggested weighting:

- retrieval effectiveness: 30
- grounded answer quality: 30
- efficiency: 20
- robustness: 10
- traceability: 10

## 9.2 Business Scorecard

Suggested dimensions:

- decision usefulness
- actionability
- trust
- audience fit
- abstention correctness

Suggested weighting:

- decision usefulness: 30
- actionability: 25
- trust: 20
- audience fit: 15
- abstention correctness: 10

## 9.3 Composite Score

Composite score should be optional and secondary.

If introduced, it should be clearly labeled as derived, not primary.

Example:

- technical score: 0-100
- business score: 0-100
- composite: weighted blend only for ranking convenience

## 10. How Rubrics Should Work

Rubrics should be explicit, versioned, and stored with results.

Recommended pattern:

- score each dimension on `1-5`
- store rationale
- store rubric version
- normalize to `0-100` only at aggregation time

Example rubric output shape:

```json
{
  "rubric_version": "v1",
  "dimension": "groundedness",
  "score_1_to_5": 4,
  "normalized_score": 80,
  "rationale": "Answer is mostly grounded in retrieved sources but one claim is weaker than the cited evidence."
}
```

This is better than a single opaque number because:

- it is debuggable
- it supports rubric evolution
- it lets humans dispute a dimension instead of the whole score

## 11. Evaluation Suites

Suites should be organized around realistic usage patterns.

Suggested suite families:

- factual
- multi-doc synthesis
- workflow/process
- compare/contrast
- buried fact
- contradictory docs
- unanswerable
- executive summary
- operator assist
- technical deep dive

These should align with the existing `QUESTION_TYPES` where possible.

## 12. Dashboard Requirements

The dashboard should serve two audiences at once:

- engineering / retrieval / research
- business / product / operator stakeholders

The right approach is one dashboard with clearly separated sections.

## 12.1 Overview Section

This section should answer:

- what is winning overall?
- what is regressing?
- what is expensive?
- what is most trusted?

Recommended visuals:

- KPI cards
- leaderboard tables
- trend lines
- win-rate summaries

## 12.2 Technical Section

This section should focus on system behavior.

Recommended data points:

- TTFT distribution by profile
- total time distribution by profile
- token usage by profile
- LLM calls by profile
- retrieved context size by profile
- failure and skipped rates
- tool-budget exhaustion rate
- content-budget exhaustion rate
- routing broadened rate
- effective routed docs
- effective selected nodes
- selected docs vs selected nodes scatter
- tokens vs answer quality scatter
- latency vs business usefulness scatter
- question type by profile heatmap

Recommended visuals:

- box plots
- violin plots
- stacked bars
- heatmaps
- scatter plots
- line charts over time

## 12.3 Business Section

This section should focus on human usefulness.

Recommended data points:

- operator win rate by profile
- business score by profile
- actionability by question type
- trust score by scenario
- correct abstention rate on unanswerables
- best profile by target persona
- time to trusted answer
- cost per trusted answer
- cost per human-selected win

Recommended visuals:

- bar charts
- heatmaps
- radar charts
- scenario-level leaderboards

## 12.4 Deep-Dive Section

This section should support debugging.

Recommended drill-down views:

- by suite
- by case
- by run
- by entry

For one entry, show:

- answer
- sources
- trace
- deterministic metrics
- rubric scores
- human notes

## 13. Comparison With Existing Frameworks

The purpose of this section is not to dismiss external frameworks. It is to explain why this repository still needs a native evaluation layer.

## 13.1 DeepEval

DeepEval positions itself as a broad LLM evaluation framework with:

- Pytest integration
- LLM-as-a-judge metrics
- deterministic metrics
- multi-turn evaluation
- synthetic data generation
- prompt optimization

Why it is attractive:

- excellent testing-oriented posture
- strong CI and regression-testing fit
- broad metric ecosystem
- good default mental model for LLM app testing

Why it is not sufficient as the primary framework here:

- it would not naturally become the source of truth for our corpus/build/profile/run artifact model
- it does not know our PageIndex-specific diagnostics or run manifests out of the box
- it would still require a local translation layer from our experiment artifacts into DeepEval test cases
- it is broader than what this repo specifically needs, but less aware of this repo's retrieval architecture

Best role in this repository:

- optional adapter
- judge metric backend
- regression test hook for curated subsets

Not the core evaluation data model.

## 13.2 RAGAS

RAGAS is especially strong for RAG-centric metric design.

From its official docs, it supports metrics across:

- RAG
- agent/tool-use cases
- natural-language comparison
- SQL
- rubric-based scoring

Why it is attractive:

- strong library of RAG evaluation metrics
- good vocabulary for faithfulness, relevancy, context precision, and context recall
- useful paradigms for rubric scoring and component-level measurement

Why it is not sufficient as the primary framework here:

- the repository is not only flat RAG; it includes deterministic and agentic PageIndex retrieval
- our evaluation target is not just "RAG quality", but "technical plus business usefulness" across different artifact families
- RAGAS would still need adaptation for our run manifests, profile matrix, and PageIndex-specific trace fields
- it is a useful metric toolbox, but not the full evaluation operating model we need

Best role in this repository:

- inspiration for retrieval and grounding metrics
- optional scorer backend for some rubric families

Not the full framework of record.

## 13.3 LangSmith

LangSmith emphasizes:

- observability
- evals over application traffic
- dashboards
- human feedback
- prompt iteration

Why it is attractive:

- strong hosted tracing and experiment comparison
- good human-feedback and evaluation workflow posture
- mature operational UX

Why it is not sufficient as the primary framework here:

- this repository already stores local manifests and traces
- a hosted external system should not become the canonical schema for our experiments layer
- the project needs a local, portable, repo-native evaluation model

Best role in this repository:

- optional future export target
- hosted observability complement

Not the core local evaluation contract.

## 13.4 TruLens

TruLens is strong around:

- feedback functions
- composable evaluation providers and implementations
- dashboards
- application-level trace-linked evaluation

Why it is attractive:

- strong concept of evaluation as composable feedback functions
- supports both human and model-driven evaluations
- useful inspiration for judge composition and dashboarding

Why it is not sufficient as the primary framework here:

- our system already has its own artifact hierarchy and run model
- we need PageIndex- and profile-aware evaluation semantics
- we need evaluation objects centered on suites, runs, and business review, not only feedback functions attached to traces

Best role in this repository:

- inspiration for metric composition
- possible adapter later

Not the canonical framework for this codebase.

## 13.5 Recommendation

Use a native experiments evaluation framework as the source of truth.

Then optionally support:

- DeepEval-style regression hooks
- RAGAS-inspired retrieval metrics
- TruLens-style feedback composition
- LangSmith export later if external observability becomes useful

## 14. Why A Custom Framework Is The Right Recommendation

This repository has two properties generic frameworks do not naturally model:

### 14.1 Variant-Aware Experiment Topology

The system already revolves around:

- corpora
- builds
- artifact families
- retrieval profiles
- answer profiles
- comparison runs

The evaluation framework should attach to those directly.

### 14.2 Mixed Evaluation Objective

The repository is trying to answer both:

- engineering questions
- business questions

That is not just a metrics problem. It is a product-specific evaluation design problem.

## 15. Proposed Implementation Shape

Recommended new module area:

```text
experiments/
  evals/
    __init__.py
    models.py
    suites.py
    registry.py
    deterministic.py
    judges.py
    human_review.py
    aggregations.py
    exports.py
    scorecards.py
```

### Module responsibilities

#### `experiments/evals/models.py`

Persistent eval models:

- `EvaluationSuite`
- `EvaluationCase`
- `EvaluationRunManifest`
- `EvaluationEntryResult`
- `RubricScore`
- `TechnicalScorecard`
- `BusinessScorecard`

#### `experiments/evals/suites.py`

Suite helpers:

- load suite
- list suites
- validate suite cases

#### `experiments/evals/registry.py`

Execution and persistence:

- evaluate existing comparison runs
- optionally execute suites directly
- persist eval manifests

#### `experiments/evals/deterministic.py`

Pure derived metrics from:

- comparison run manifests
- entry traces

#### `experiments/evals/judges.py`

Judge prompts and score normalization:

- answer-quality judge
- retrieval-quality judge
- business-usefulness judge

#### `experiments/evals/human_review.py`

Human evaluation structures:

- reviewer labels
- confidence
- notes
- business-usefulness overrides

#### `experiments/evals/aggregations.py`

Aggregations across:

- entry
- profile
- build
- question type
- suite
- corpus
- time

#### `experiments/evals/exports.py`

Outputs:

- summary JSON
- detailed CSV
- Markdown summaries
- dashboard-ready aggregate JSON

#### `experiments/evals/scorecards.py`

Technical and business score composition logic.

## 16. Data Storage Strategy

Recommended artifact layout:

```text
experiments/artifacts/evals/
  suites/
    <suite_id>.json
  runs/
    <eval_run_id>/
      manifest.json
      entry_scores.json
      suite_snapshot.json
      technical_summary.json
      business_summary.json
      overview.md
      overview.html
```

This mirrors the existing experiments artifact design and keeps eval outputs isolated.

## 17. Implementation Phases

Implementation should be staged.

## Phase 1: Deterministic Foundation

Scope:

- eval models
- suite model
- evaluation runner
- deterministic metrics
- aggregate technical reports
- first technical dashboard

Outcome:

- strong engineering view without judge dependency

## Phase 2: Judge Scoring

Scope:

- rubric definitions
- judge prompts
- judge result persistence
- technical and business scorecards
- richer exports

Outcome:

- structured quality scoring beyond latency and token counts

## Phase 3: Human Review And Business Dashboard

Scope:

- business review workflow
- human overrides
- scenario-level dashboards
- persona-based reporting

Outcome:

- decision-ready business evaluation layer

## 18. Recommended First Version Scope

The first implementation should not try to do everything.

Recommended initial scope:

- deterministic evaluation only
- suite support
- aggregate technical dashboard
- operator winner analytics
- business placeholders in the model

Then add judge scoring second.

This avoids shipping a flashy but brittle first version.

## 19. Risks And Failure Modes

### Risk 1: Overfitting To One Composite Score

Mitigation:

- keep technical and business scorecards separate

### Risk 2: Judge Scores Becoming Arbitrary

Mitigation:

- use explicit rubrics
- version judge prompts
- compare against human review

### Risk 3: Too Much Complexity Too Early

Mitigation:

- phase the implementation
- ship deterministic scoring first

### Risk 4: Breaking The Main Codebase

Mitigation:

- keep the implementation almost entirely inside `experiments/`
- touch shared engine code only if a trace gap is truly blocking

## 20. Final Recommendation

The right evaluation strategy for this repository is:

- custom, local, experiments-native framework
- suite-based
- artifact-aware
- technical and business scorecards separated
- deterministic metrics first
- judge scoring second
- human review preserved as a first-class signal

This gives the project something generic frameworks do not: a single coherent evaluation layer that truly understands the repository's build matrix, retrieval modes, traces, and business goals.

## 21. Official References

The external-framework comparisons above were based on official project documentation:

- DeepEval: [https://deepeval.com/](https://deepeval.com/)
- RAGAS metrics overview: [https://docs.ragas.io/en/latest/concepts/metrics/overview/](https://docs.ragas.io/en/latest/concepts/metrics/overview/)
- RAGAS available metrics: [https://docs.ragas.io/en/latest/concepts/metrics/available_metrics/](https://docs.ragas.io/en/latest/concepts/metrics/available_metrics/)
- LangSmith docs: [https://docs.smith.langchain.com/](https://docs.smith.langchain.com/)
- TruLens evaluation docs: [https://www.trulens.org/component_guides/evaluation/](https://www.trulens.org/component_guides/evaluation/)
- TruLens dashboard docs: [https://www.trulens.org/getting_started/dashboard/](https://www.trulens.org/getting_started/dashboard/)

## 22. How To Stand Up A Golden Dataset

The golden dataset is the bridge between ad hoc comparison and durable evaluation.

In this repo, a golden dataset is just a persisted evaluation suite with richer reference fields.

Recommended rollout:

1. Pick a stable corpus or corpus family.
2. Curate a question set that represents the real workloads you care about.
3. For each question, add as much reference material as you can support.
4. Import the suite into the experiments UI.
5. Run evaluation snapshots against that suite over time.

### 22.1 Minimum Viable Golden Dataset

The smallest useful schema is:

- `question`
- `ground_truth_answer`

That is enough to unlock basic gold-aware scoring.

### 22.2 Recommended Golden Dataset Schema

The recommended schema is:

- `case_id`
- `question`
- `ground_truth_answer`
- `question_type`
- `expected_answerability`
- `business_scenario`
- `target_persona`
- `must_include_facts`
- `must_not_claim`
- `gold_sources`
- `notes`

List-like fields use pipe-delimited values in CSV:

- `must_include_facts`: `fact one|fact two`
- `must_not_claim`: `bad claim one|bad claim two`
- `gold_sources`: `doc_id::node_ref|doc_id`

### 22.3 Recommended Authoring Workflow

Use this workflow:

1. Export or collect real user questions.
2. Deduplicate and cluster them by question type.
3. Pick representative cases across:
   - factual lookup
   - workflow/process questions
   - synthesis questions
   - compare/contrast questions
   - buried-fact questions
   - unanswerables
4. Draft a short, high-quality reference answer for each question.
5. Add required facts and forbidden claims for the cases where precision matters.
6. Add gold source refs when you know the exact expected nodes or docs.
7. Import the suite and keep it versioned.

### 22.4 What To Put In `gold_sources`

Use:

- exact node refs when you know the expected node, for example `nxgen_module_deepdive_m5::0026`
- document IDs when you only care that the system lands in the correct document

Do not over-specify gold sources unless the exact evidence path matters. If several nodes are all acceptable evidence, keep the reference broader.

### 22.5 When Ground Truth Should Be Strict

Be strict when:

- the question has a stable factual answer
- omission is risky
- a business user could take the wrong action from a bad answer

Be lighter when:

- the task is open-ended synthesis
- there are multiple acceptable phrasings
- the answer should be evaluated more on usefulness than literal wording

### 22.6 Supported Import Formats

For this phase:

- supported: `CSV`
- supported: `JSON`
- intentionally not supported: `XLSX`

This is deliberate. CSV and JSON are easier to validate, version, diff, and keep reproducible in-repo.

## 23. Phase 2 Implementation Approach

The recommended phase 2 build is:

- keep phase 1 deterministic evaluation intact
- add suite import for CSV and JSON
- add optional judge scoring on top of stored run artifacts
- add gold-aware scoring when the suite contains reference answers or gold sources
- add business-facing views in the dashboard

The key implementation constraint is that phase 2 should still evaluate existing runs without rerunning retrieval.

That is possible because the current run traces already contain:

- the full answer text
- the retrieved context
- the source refs
- retrieval diagnostics

That makes phase 2 a scoring-layer extension, not a retrieval-engine rewrite.
