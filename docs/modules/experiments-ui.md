# Experiments UI

Primary code:

- `experiments_app.py`

Supporting modules (read-only from the UI perspective):

- `experiments/corpora.py`
- `experiments/builds/registry.py`
- `experiments/runs/registry.py`
- `experiments/evals/registry.py`
- `experiments/evals/suites.py`
- `experiments/workflows.py`
- `experiments/models.py`

## Purpose

`experiments_app.py` is the Streamlit front-end for the experiments harness.

It is a completely separate Streamlit app from `app.py`. It does not share the main app's index, project state, or production corpus. Its only job is to let you:

- register and normalize corpora
- build artifact families from those corpora
- execute comparison runs across build/profile combinations
- review raw answers, traces, and metrics for individual run entries
- run evaluations over completed runs
- inspect deterministic and LLM-judge scores side by side
- export reports and golden-dataset artifacts

This app is the controlled lab. The main app is the production interface.

## Application Entry Point

The app starts at `main()` which:

1. configures page layout as wide
2. injects the custom CSS theme via `_inject_styles()`
3. instantiates four runner objects: `CorpusStore`, `ArtifactBuildRunner`, `ExperimentRunRunner`, `EvaluationRunner`
4. loads all corpora, builds, runs, and evaluations at startup
5. renders a sidebar with live counts
6. creates eight tabs

## Tabs At A Glance

| Tab | Function | Purpose |
| --- | --- | --- |
| Overview | `_render_overview` | High-level snapshot cards |
| What Is It | `_render_what_is_it_tab` | Reference tables and artifact explorer |
| Corpora | `_render_corpora_tab` | Register and inspect corpora |
| Builds | `_render_builds_tab` | Trigger and inspect artifact builds |
| Compare | `_render_compare_tab` | Run a single question or golden-dataset batch |
| Runs | `_render_runs_tab` | Browse and inspect past runs |
| Evaluation | `_render_evaluation_tab` | Score runs and analyse results |
| Reports | `_render_reports_tab` | Browse and download generated report files |

## Tab Details

### Overview

Shows four metric cards:

- Corpora count
- Builds count with completed sub-count
- Runs count with completed sub-count
- Retrieval modes count

Also shows a warning callout reminding that advanced retrieval modes increase latency and token use.

### What Is It

A reference tab for operators and newcomers. It does not mutate state.

Sections:

- **Build presets table** — lists every available preset from `build_preset_catalog()` with artifact family and description
- **Retrieval profiles table** — lists every profile from `retrieval_profile_catalog()` with compatible artifact families and behavior description
- **Compatibility matrix** — lists every valid (build preset × profile) combination and its total count
- **Artifact explorer** — lets you drill into persisted manifests for corpora, builds, and runs as raw JSON, with an optional compact tree view for PageIndex artifacts

The compatibility count shown in the header is derived from `_compatibility_rows()` and is the canonical source of truth for how many runnable combinations exist.

### Corpora

Left column: upload form to register a new corpus.

Accepts one or more files. On submit, calls `CorpusStore.create_corpus()` to copy the source files into `experiments/artifacts/corpora/`.

Right column: table of all persisted corpora with document counts and sizes.

### Builds

Upper section: multiselect of corpora, multiselect of build presets to run.

Triggering a build calls `ArtifactBuildRunner.run_build()` for each selected (corpus, preset) pair.

Build progress is shown as a live table that updates on each entry's completion event.

Lower section: table of all existing builds with status, artifact family, and a "View" option to inspect the build manifest and output artifacts.

Important note about the build table: the same corpus can appear multiple times if multiple presets were run against it. That is expected, not duplicates.

### Compare

The primary query-execution tab.

**Setup controls:**

- Corpus filter — narrows which builds are shown
- Build multiselect — choose which builds to include
- Retrieval reasoning effort selector — `default`, `low`, `medium`, `high`
- Answer reasoning effort selector — same options
- Retrieval profile multiselect — auto-populated from the compatible profiles of the selected builds
- Model input — defaults to `get_default_model()`
- Max concurrency slider — only visible when more than one compatible entry is planned
- Resume-matching toggle — reuses an in-progress run if a matching one exists

**Planned entries preview:** a table showing every (build, profile) combination that will be executed before you submit.

**Question source radio:**

- `Single question` — one free-text question and a question-type selector
- `Golden dataset batch` — draws the next N cases from a persisted evaluation suite

For golden dataset batch mode, the tab also shows suite case counts, remaining cases, and a batch-size input.

**Execution:** hitting run creates a `ComparisonRunManifest`, then streams per-entry progress events to a live table. A `_render_run_detail()` view is shown inline as entries complete.

After a golden-dataset batch completes, the newly created run IDs are stored in session state under `experiments_last_suite_run_ids` and `experiments_last_suite_id` so the Evaluation tab can auto-detect them.

### Runs

Browse and re-inspect all past comparison runs.

Controls:

- Run selector with `_run_label()` display (shows query, question type, status, and date)
- Optional resume/rerun button for unfinished runs

Selected run is passed to `_render_run_detail()`.

`_render_run_detail()` shows:

- Hero strip with question, status, model, and date
- Summary metric cards: completed entries, failed entries, fastest label, lowest-tokens label
- Entries table with per-entry status, latency, token use, and operator winner
- For each entry: answer preview, sources table, trace JSON expander, export button

### Evaluation

The largest tab. Has two main phases: setup, and results.

**Setup phase (form):**

- Run multiselect with auto-population from `experiments_last_suite_run_ids` session state
- Suite selector with auto-detection when all selected runs share the same `suite_id`
- Judge model selector
- Judge reasoning effort selector
- Submit button

**Golden Dataset Setup expander (always visible):**

- CSV and JSON template download buttons
- Suite import form (accepts `.csv` or `.json`)
- Table of all persisted suites

**Results phase (after loading or generating an evaluation snapshot):**

Summary cards:

| Card | What it shows |
| --- | --- |
| Source Runs | Comparison runs scanned |
| Entries | Total evaluated entries |
| Avg Tech Score | Deterministic technical quality |
| Avg Judge Score | LLM-judge overall quality |
| Avg Business | Business usefulness |
| Gold Cases | Entries with ground truth or gold sources |
| Best Tech Profile | Highest avg technical score |
| Best Business Profile | Highest avg business score |
| Operator Favorite | Most human winner selections |

**Profile Recommendation block:**

Derived automatically from the profile leaderboard. Proposes four candidates:

- Speed-First: fastest profile within 15% of peak quality score
- Cost-First: lowest token profile within 15% of peak quality score
- Quality-First: highest overall quality
- Balanced: highest score weighted across speed, cost, and quality

**Active Evaluation Suite expander:** shows the attached suite's cases.

**Analysis sections (in order):**

1. **Profile Leaderboard** — table sorted by avg judge score with all aggregated columns
2. **Profile Quality Scores bar chart** — tech score vs judge score vs business score per profile
3. **Latency chart** — avg total time per profile
4. **Token chart** — avg total tokens per profile
5. **Completion rate chart** — success rate per profile
6. **Efficiency scatter** — total time vs total tokens, colored by profile
7. **Business quality scatter** — judge score vs business score
8. **Question-type breakdown** — bar charts of judge/business score sliced by question type
9. **Judge sub-scores by profile table** — all 9 rubric dimensions per profile
10. **Sub-score heatmaps** — judge dimension heatmaps by profile and question type
11. **Build comparison** — technical score per build
12. **Corpus comparison** — technical score per corpus
13. **Failure Mode Taxonomy** — top-10 missing required facts and top-10 risk strings across all judged entries
14. **Routing Strategy** — per-question-type best-profile recommendation derived from scores, with confidence tier and score gap
15. **Snapshot comparison** — side-by-side diff of two evaluation snapshots
16. **Entry Inspector** — filterable view of individual entries with full scorecard
17. **Case Analysis** — per-case multi-answer comparison for suite-backed evaluations

**Entry Inspector detail:**

The entry inspector has four filter controls: question type, run, retrieval profile, and minimum judge score. Filtered entries are listed in a selectbox using `_entry_label()`.

When an entry is selected, the inspector renders:

- Context header strip: question + case ID, retrieval profile, question type, run title
- Technical Scorecard: four score tiles (technical, efficiency, reliability, retrieval discipline) plus diagnostic note bullets
- Judge Scorecard: composite row (overall quality, technical quality, business quality, gold quality), sub-dimensions row (groundedness, completeness, directness, actionability, business fit, abstention), rationale text, side-by-side Strengths/Risks columns, side-by-side Missing Required Facts/Forbidden Claim Violations columns
- Developer details expander: raw metrics JSON, raw diagnostics JSON, golden case ground truth (if any), raw judge JSON

### Reports

Lists all report files under `experiments/artifacts/reports/` and provides download buttons for each.

Also shows a deduplicated table of distinct questions that have been run, grouped from run manifests.

## Key UI Components

### `_render_metric_card(label, value, note)`

Renders a single card with a large value, a label, and a small note below. Used throughout all tabs as the primary KPI display.

### `_render_table(df)`

Renders a styled `pd.DataFrame` using a custom HTML template. Columns with `_score` or `_rate` suffixes get right-aligned. Replaces `st.dataframe` everywhere to match the app's custom theme.

### `_render_bar_chart(df, x, y, title, color)`

Wraps Altair bar chart creation with the app's custom color scheme, `-45°` label angle, bounded label width, and `_configured_chart()` styling.

### `_render_scatter_chart(df, x, y, color, title, tooltip)`

Wraps Altair scatter chart with point sizing and the same configured styling.

### `_render_heatmap(df, x, y, color, title)`

Wraps Altair rect mark heatmap with text overlays and configured styling.

### `_configured_chart(chart)`

Applies consistent Altair theme configuration to all charts:

- custom color scheme derived from CSS variables
- font family
- SVG-level viewport padding (`top=5, bottom=72, left=5, right=5`) to prevent x-axis label clipping
- background set to transparent

The `bottom=72` padding is critical. Without it, rotated x-axis labels are clipped by the SVG boundary.

### `_entry_label(entry)`

Formats a selectbox label for the entry inspector:

```
{question_short} [{judge_score}]  —  {retrieval_profile_id} · {question_type}
```

The profile and question type are always visible in the label so you never have to click an entry to find out which variant you are looking at.

### `_entry_passes_filters(entry, ...)`

Returns `True` if an entry matches all four inspector filters. Called on every entry before populating the inspector selectbox.

## Styling System

All custom CSS is injected once at startup via `_inject_styles()`.

The theme uses CSS custom properties defined on `:root`:

| Variable | Role |
| --- | --- |
| `--bg` | Page background warm parchment |
| `--surface` | Card and panel background |
| `--surface-strong` | Elevated surface, hover states |
| `--ink` | Primary text color |
| `--ink-soft` | Secondary text |
| `--muted` | Captions and metadata |
| `--line` | Borders and dividers |
| `--accent` | Interactive and highlight color (teal) |
| `--amber` | Warning-state accent |
| `--rose` | Error-state accent |
| `--success` | Pass/ok accent |

Named component classes defined in the same block:

- `.hero` — page-level hero banners with gradient background
- `.hero-kicker` — small overline label above hero titles
- `.metric-card` — KPI tile used by `_render_metric_card`
- `.score-tile` — sub-metric tile used in scorecard rows
- `.warning-copy` — amber callout block
- `.small-note` — muted small-print text

## Session State Keys

| Key | Written by | Read by | Purpose |
| --- | --- | --- | --- |
| `experiments_last_suite_run_ids` | Compare tab (after golden batch) | Evaluation tab | Auto-populate run selector |
| `experiments_last_suite_id` | Compare tab (after golden batch) | Evaluation tab | Auto-select attached suite |
| `experiments_last_eval_suite_id` | Evaluation tab (after suite import) | Evaluation tab | Pre-select the just-imported suite |

## How Scores Are Calculated

### Phase 1: Deterministic Evaluation

Deterministic scoring runs entirely from stored manifests and trace JSON. No LLM calls are made.

#### Peer Normalization

Many sub-scores use inverse peer normalization. This means a lower raw value produces a higher score, and the scale is relative to the other entries being evaluated together:

```
normalize_inverse(value, peer_values) =
    100 × (max(peers) − value) / (max(peers) − min(peers))
```

When all peers are equal, every entry gets 100. This matters when comparing a single entry in isolation — its technical score may look perfect because there are no peers to normalize against.

#### Efficiency Score (0–100)

```
efficiency = mean([
    normalize_inverse(ttft_seconds, peers),
    normalize_inverse(total_time_seconds, peers),
    normalize_inverse(total_tokens, peers),
    normalize_inverse(llm_calls, peers),
])
```

All four components are equally weighted. Faster and cheaper entries score higher.

#### Reliability Score (0–100)

Starts at 100 and deducts for operational problems:

| Condition | Deduction |
| --- | --- |
| Tool-call budget exhausted | −12 |
| Content-token budget exhausted | −12 |
| Routing broadened (fallback activated) | −4 |
| Fetched context was truncated | −6 |

Each deduction also appends a note to the scorecard that appears in the entry inspector.

Failed or skipped entries do not go through this calculation. Failed entries get 0, skipped entries get 20.

#### Retrieval Discipline Score (0–100)

```
retrieval_discipline = mean([
    normalize_inverse(retrieved_context_tokens, peers),
    normalize_inverse(selected_docs_count, peers),
    normalize_inverse(selected_nodes_count, peers),
]) − penalties
```

Penalties (same conditions as reliability):

- −4 if routing was broadened
- −4 if either budget was exhausted

This score rewards entries that retrieved what they needed without pulling in excessive context or selecting too many nodes.

#### Technical Score (0–100)

```
technical_score = (0.45 × efficiency) + (0.35 × reliability) + (0.20 × retrieval_discipline)
```

Efficiency is the dominant factor. Reliability matters more than retrieval discipline. This reflects the judgment that a fast and stable entry is more valuable than one that merely retrieved a tight context window.

---

### Phase 2: LLM Judge Evaluation

The judge phase requires an LLM call per entry. It reads the stored trace JSON for each entry to access the full answer text and retrieved context, then asks the judge model to score nine rubric dimensions.

#### Rubric Dimensions (each 0–5, converted to 0–100 by ×20)

| Dimension | What it asks |
| --- | --- |
| Groundedness | Is the answer supported by the retrieved context? |
| Completeness | Does the answer cover all aspects of the question? |
| Directness | Is the answer focused and not padded with unnecessary content? |
| Actionability | Could a business user act on this answer? |
| Business Fit | Is the answer relevant to the stated business scenario and persona? |
| Abstention Quality | If the system said it could not answer, was that the right call? |
| Gold Alignment | Does the answer align with the ground-truth answer? (null if no ground truth) |
| Required Fact Coverage | Were all required facts from the golden case included? (null if none) |
| Contradiction Score | Does the answer contradict the ground truth? (5 = no contradiction; null if no ground truth) |

The judge is also asked to return lists of:

- `missing_required_facts` — strings naming facts the answer failed to include
- `forbidden_claim_violations` — strings naming claims the answer made that contradict `must_not_claim`
- `strengths` — brief positive observations
- `risks` — brief concern observations
- `rationale` — a short paragraph summarizing the overall judgment

#### Composite Scores

```
technical_quality = mean([groundedness, completeness, directness, abstention])
business_quality  = mean([groundedness, actionability, business_fit])
gold_quality      = mean([gold_alignment, required_fact_coverage, contradiction, source_match_score])
```

`source_match_score` is computed deterministically from the trace (not by the judge):

```
source_match_score = (matched_gold_sources / total_gold_sources) × 100
```

A gold source is "matched" when its `doc_id` or `node_ref` appears in the entry's retrieved sources list, after normalized string comparison.

#### Overall Judge Score

When a golden case with ground truth or gold sources is present:

```
overall = (0.45 × technical_quality) + (0.35 × business_quality) + (0.20 × gold_quality)
```

When no golden case exists (ad hoc runs without ground truth):

```
overall = mean([technical_quality, technical_quality, business_quality, business_quality, business_quality])
        = (2 × technical_quality + 3 × business_quality) / 5
```

This gives business quality a slightly higher weight in the no-gold-truth case because technical quality alone cannot distinguish good answers from bad ones without a reference.

#### What the Judge Actually Sees

The judge prompt includes:

- The full question, question type, business scenario, and target persona
- The candidate answer (truncated at 14 000 characters)
- The retrieved context (truncated at 18 000 characters)
- Retrieved source refs (up to 20 sources, with `node_ref`, `doc_id`, section, page range)
- Ground-truth answer (if present)
- Must-include facts and must-not-claim strings (if present)
- Gold sources list (if present)

The judge model is called with temperature 0 when the model supports explicit temperature. It returns a strict JSON object conforming to the output schema.

---

### Aggregation

After individual entries are scored, `experiments/evals/aggregations.py` computes per-profile, per-question-type, per-build, and per-corpus aggregate tables.

Key aggregate columns and their formulas:

| Column | Formula |
| --- | --- |
| `completion_rate` | completed entries / total entries |
| `operator_win_rate` | entries marked operator winner / total entries |
| `avg_tool_calls_made` | mean of `diagnostics.tool_calls_made` across entries |
| `broadened_routing_rate` | entries where routing was broadened / total |
| `tool_budget_exhaustion_rate` | entries where tool budget was exhausted / total |
| `content_budget_exhaustion_rate` | entries where content budget was exhausted / total |
| `missing_required_fact_rate` | entries with at least one missing required fact / judged entries |
| `forbidden_claim_violation_rate` | entries with at least one forbidden claim violation / judged entries |
| `avg_gold_alignment_score` | mean of `gold_alignment_score` across judged entries |
| `avg_groundedness_score` | mean of `groundedness_score` across judged entries |
| `avg_source_match_score` | mean of `source_match_score` across entries where gold sources were provided |

All averages exclude entries where the relevant score is `None` (i.e. entries that were not judged or where ground truth was absent).

---

## Score Interpretation Quick Reference

| Score | Range | Source | What it measures |
| --- | --- | --- | --- |
| Technical score | 0–100 | Deterministic | Weighted blend of efficiency, reliability, retrieval discipline |
| Efficiency score | 0–100 | Deterministic | Inverse-normalized latency, tokens, and LLM call count vs peers |
| Reliability score | 0–100 | Deterministic | Starts at 100, deducts for budget exhaustion, broadened routing, truncation |
| Retrieval discipline | 0–100 | Deterministic | Inverse-normalized context tokens and doc/node selection counts vs peers |
| Judge overall | 0–100 | LLM judge | Weighted blend of technical quality, business quality, and gold quality |
| Business score | 0–100 | LLM judge | Mean of groundedness, actionability, business fit sub-scores |
| Technical quality | 0–100 | LLM judge | Mean of groundedness, completeness, directness, abstention sub-scores |
| Gold quality | 0–100 | LLM judge | Mean of gold alignment, required fact coverage, contradiction, source match |
| Source match score | 0–100 | Deterministic | Fraction of gold sources present in retrieved sources |

All 0–5 rubric scores from the judge are multiplied by 20 to bring them into the 0–100 range before aggregation.

## Flow Guide

### Flow: first-time setup

1. Open the Corpora tab, upload source documents, register a corpus
2. Open the Builds tab, select the new corpus, pick one or more build presets, run
3. Once builds complete, open Compare, select the new builds, choose retrieval profiles
4. Run a single test question to verify the pipeline end to end
5. Inspect the run in the Runs tab

### Flow: evaluate an existing run

1. Open the Evaluation tab
2. Select one or more completed runs from the multiselect
3. Choose or import a golden-dataset suite (optional)
4. Select a judge model, run the evaluation
5. Inspect leaderboard, charts, and the entry inspector

### Flow: golden-dataset batch

1. Import a suite in the Evaluation tab's "Golden Dataset Setup" expander
2. Switch to the Compare tab
3. Set question source to "Golden dataset batch"
4. Select the suite, set batch size, pick builds and profiles
5. Run the batch
6. Switch back to Evaluation — runs from the batch are auto-populated in the selector

### Flow: compare two evaluation snapshots

1. Generate at least two evaluation snapshots
2. Open the Evaluation tab, load any snapshot
3. Scroll to "Snapshot comparison"
4. Select snapshot A and snapshot B from the two selectors
5. The diff table highlights score deltas between the two

## What Is Possible In This UI

- Register corpora from uploaded files
- Trigger artifact builds for any preset against any registered corpus
- Run single-question or batch golden-dataset comparisons
- Resume in-progress or failed runs
- Score runs deterministically without any LLM calls
- Add LLM-judge scoring over completed runs
- Filter and inspect individual entries with full technical and judge scorecards
- Download CSV and JSON exports of evaluation results
- Compare two evaluation snapshots side by side
- Get auto-generated profile recommendations based on evaluation data

## Current Constraints

- The app loads all runs and evaluations at startup. Large artifact directories will slow the initial page render.
- Evaluation is still sequential per entry for the judge phase. Large selections take proportionally long.
- The entry inspector selectbox shows entries one at a time. There is no bulk entry view.
- Charts are Altair-based and do not support click-through navigation back to source runs.
- Suite import and suite execution live in separate tabs, which requires manual context switching.
- The app has no authentication. It is intended for local or internal lab use only.
