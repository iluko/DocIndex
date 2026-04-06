# Experiments Project Map

This document is the complete mental model for the experiments harness inside this repository.

It explains:

- what the experiments layer is
- why it exists
- how it works end to end
- what each build preset and retrieval profile means
- which parts are real shared Hybrid Approach engine behavior versus lab-only benchmarking infrastructure
- how the experiments layer maps back to the original Hybrid Approach UI
- when to use each variant, and when not to

The goal is to make the system legible enough that you can reason about any comparison run without guessing.

## 1. Short Version

The experiments folder is a controlled comparison lab built on top of the main Hybrid Approach codebase.

It does not replace the main app. It exists so you can:

- register one corpus once
- build multiple retrieval artifact variants from the same corpus
- run the same question across multiple compatible retrieval strategies
- compare answers, traces, latency, token usage, and operator preference side by side
- keep all of those experiment artifacts isolated from the main app's production/demo index

The most important distinction is:

- the PageIndex and Hybrid retrieval paths are real shared engine behavior reused from the main app
- the RAG baselines and the preset/profile comparison matrix are experiment-harness additions

## 2. What This System Actually Is

The experiments harness is a comparison framework with four layers:

1. `Corpus`
2. `Build`
3. `Retrieval profile`
4. `Answer profile`

A single comparison entry looks like this:

`corpus -> build preset -> build artifacts -> retrieval profile -> retrieved context -> shared answer profile`

The same question can be executed across many entries, for example:

- `pageindex_related_basic · hybrid`
- `pageindex_related_basic · pageindex`
- `pageindex_related_enhanced · hybrid_advanced`
- `rag_vector · rag_vector_rrf`

Those entries are then grouped into one comparison run and exported as reports.

## 3. Why The Experiments Harness Exists

The main app is optimized for using the system.

The experiments app is optimized for understanding and comparing the system.

It exists because the main app is not designed to answer questions like:

- Is `hybrid` faster than `pageindex` for a given question type?
- Does relationship enrichment help routing quality?
- Does `pageindex_related_enhanced` actually outperform `pageindex_related_basic` enough to justify ingestion cost?
- How does PageIndex compare against a local vector-RAG baseline on buried-fact questions?
- Which retrieval path wins most often for workflow questions?

The experiments layer adds:

- isolated corpus storage
- deterministic build manifests
- stable run specs and spec hashes
- reproducible build directories
- side-by-side entry execution
- persisted traces and reports
- run annotations, reruns, resume support, and aggregate summaries

It is a benchmarking and analysis shell around the underlying retrieval system.

## 4. The Mental Map

If you only remember one diagram, remember this one:

```text
Source files
    ↓
Corpus registration
    ↓
Normalized corpus manifest
    ↓
Build preset
    ↓
Artifact family
    ↓
Compatible retrieval profiles
    ↓
Retrieved context
    ↓
Shared answer prompt
    ↓
Comparison run manifest
    ↓
JSON / CSV / MD / HTML reports
```

### The important abstraction boundaries

- A `corpus` is only a normalized set of documents.
- A `build` turns a corpus into one retrievable artifact family.
- A `retrieval profile` must match the artifact family.
- An `answer profile` is separate from retrieval and is kept mostly constant so retrieval differences remain measurable.

This is the core design reason the experiments system is understandable: the knobs are separated.

## 5. Shared Engine Versus Lab-Only Infrastructure

This is the question most people get stuck on.

### Real shared Hybrid Approach engine behavior

These are real system capabilities already present in the main project:

- document ingestion through `ingest_document_with_trace(...)`
- PageIndex tree construction
- master-tree generation
- relationship maintenance modes: `off`, `basic`, `enhanced`
- `hybrid` retrieval mode
- `pageindex` retrieval mode
- advanced retrieval planning, adaptive width, and node expansion
- routing, navigation, verification, fetching, and final answering

So when the experiments app runs:

- `pageindex_base`
- `pageindex_related_basic`
- `pageindex_related_enhanced`
- `hybrid`
- `hybrid_advanced`
- `pageindex`
- `pageindex_advanced`

it is using the real Hybrid Approach ingestion and retrieval engine, not a toy emulation.

### Lab-only additions

These exist only in the experiments layer:

- `rag_standard`
- `rag_vector`
- `rag_vector_rrf`
- `rag_vector_rerank`
- corpus registration and normalization manifests under `experiments/artifacts/`
- build manifests under `experiments/artifacts/builds/`
- comparison-run manifests under `experiments/artifacts/runs/`
- report exports under `experiments/artifacts/reports/`
- the preset/profile catalog and compatibility matrix

So the experiments layer is both:

- a real wrapper around the true PageIndex/Hybrid engine
- and a benchmarking shell that adds baselines and orchestration the main app does not expose

## 6. Files And Folders You Should Care About

### Core experiments layer

- `experiments/README.md`
- `experiments/models.py`
- `experiments/workflows.py`
- `experiments/corpora.py`
- `experiments/builds/registry.py`
- `experiments/runs/registry.py`
- `experiments/reports.py`
- `experiments_app.py`

### Build implementations

- `experiments/builds/presets.py`
- `experiments/builds/pageindex.py`
- `experiments/builds/rag.py`
- `experiments/builds/rag_vector.py`

### Retrieval implementations

- `experiments/profiles.py`
- `experiments/retrieval/hybrid.py`
- `experiments/retrieval/pageindex.py`
- `experiments/retrieval/rag.py`
- `experiments/retrieval/rag_vector.py`

### Shared engine code reused by experiments

- `ingestion/ingest.py`
- `retrieval/query_engine.py`
- `retrieval/pageindex_engine.py`
- `retrieval/router.py`
- `retrieval/navigator.py`
- `retrieval/verifier.py`
- `retrieval/fetcher.py`
- `retrieval/planner.py`
- `master_tree/relationships.py`
- `app.py`

## 7. End-To-End Lifecycle

This is the full lifecycle for one experiment from raw files to exported report.

### Step 1: Register a corpus

The corpus layer copies source files into:

- `experiments/artifacts/corpora/<corpus_id>/source_docs/`

and writes a manifest:

- `experiments/artifacts/corpora/<corpus_id>/manifest.json`

The corpus manifest stores:

- doc IDs
- titles
- source paths
- normalized copied paths
- file extensions
- sizes
- SHA-256 checksums

Why this matters:

- later builds are reproducible
- source files are frozen into the experiments store
- build adapters do not depend on arbitrary local paths

### Step 2: Run a build preset

A build preset turns one corpus into one artifact family.

Each build gets its own directory:

- `experiments/artifacts/builds/<build_id>/`

and its own manifest:

- `experiments/artifacts/builds/<build_id>/manifest.json`

The build ID is deterministic from:

- corpus ID
- build label
- config fingerprint

This means repeated builds with the same exact settings resolve to the same build ID unless forced.

### Step 3: Select compatible retrieval profiles

Not every retrieval profile can operate on every build.

Compatibility is enforced by artifact family:

- `rag_chunks` -> `rag_standard`
- `rag_vector` -> `rag_vector`, `rag_vector_rrf`, `rag_vector_rerank`
- `pageindex_tree` -> `hybrid`, `hybrid_advanced`, `pageindex`, `pageindex_advanced`

This is a key design decision: the harness prevents nonsense combinations.

### Step 4: Execute a comparison run

One comparison run contains:

- one question
- one model
- one question type tag
- a list of requested entries
- one answer profile

Each entry runs:

1. retrieval
2. shared answer synthesis
3. metrics collection
4. trace persistence

The runner stores the overall manifest and one trace JSON per completed entry.

### Step 5: Write reports

The harness writes per-run outputs:

- `summary.json`
- `entries.csv`
- `overview.md`
- `overview.html`

and aggregate summaries under:

- `experiments/artifacts/reports/_aggregate/`

## 8. The Canonical Concepts

### Corpus

A corpus is a reusable document set.

It is not yet retrieval-ready. It is simply the canonical input bundle used to generate downstream builds.

Use a corpus when:

- you want one stable document set
- you want to compare multiple retrieval strategies on the same exact source set

Do not think of a corpus as an index. It is the input to indexing.

### Build

A build is the concrete output of running one build preset over one corpus.

The build is where a corpus becomes retrievable.

Examples:

- flat chunks
- embeddings plus chunk metadata
- PageIndex per-document trees plus master tree

### Retrieval profile

A retrieval profile is the query-time policy.

It answers:

- how many docs to route
- whether retrieval is lexical, dense, fused, reranked, deterministic, or agentic
- whether advanced planning is enabled
- whether tool-call budgets apply

### Answer profile

The answer profile is intentionally boring right now.

There is currently one shared answer profile:

- `aligned_default`

That is deliberate. The harness is mostly trying to compare retrieval, not prompt engineering.

### Run entry

A run entry is one concrete combination:

- one build
- one retrieval profile
- one shared answer profile

Example:

- `sales_intelligence__pageindex_related_basic__2d6d4e1da7 · pageindex`

### Comparison run

A comparison run is a set of entries that all answer the same question.

This is the main evaluation object in the experiments app.

## 9. Build Presets In Detail

There are currently five build presets.

## 9.1 `rag_standard`

### What it builds

Artifact family:

- `rag_chunks`

Build output:

- one `rag_chunks.json` file containing flat chunk records

### How it works

The builder:

- extracts source blocks from each document
- uses one block per page for PDFs
- uses paragraph-like blocks for Markdown and DOCX
- merges blocks into bounded chunks with soft overlap
- stores page ranges when available

Chunk parameters:

- `chunk_size_tokens = 700`
- `chunk_overlap_tokens = 120`
- `min_chunk_tokens = 80`

### What it is good for

- simplest baseline
- quick lexical retrieval benchmark
- sanity-checking whether semantic retrieval is even necessary

### What it is not

- not embedding-based
- not vector search
- not PageIndex
- not hierarchical

### When to use it

- when you want a cheap flat baseline
- when the source wording is likely to match the user wording
- when you want interpretability over sophistication

### When not to use it

- when paraphrase-heavy semantic matching matters
- when document structure matters
- when you need cross-document reasoning over section hierarchy

## 9.2 `rag_vector`

### What it builds

Artifact family:

- `rag_vector`

Build outputs include:

- `rag_vector_chunks.json`
- `rag_vector_embeddings.npy`
- `rag_vector_meta.json`
- optionally `rag_vector_lexical.json`
- optionally `rag_vector_hnsw.index`

### How it works

This build starts from the same flat chunking idea as `rag_standard`, then adds:

- local embedding generation
- optional lexical token corpus persistence
- optional HNSW ANN indexing if `hnswlib` is available

Default embedding model:

- `sentence-transformers/all-MiniLM-L6-v2`

### What it is good for

- semantic retrieval baseline
- a proper local vector-RAG comparison target
- fusion and reranking experiments

### What it is not

- not part of the original main app
- not hierarchical
- not PageIndex

### When to use it

- when you want to compare PageIndex against a real vector baseline
- when paraphrase handling matters
- when you want dense retrieval, RRF, or reranking

### When not to use it

- when you only care about the true PageIndex/Hybrid Approach product path
- when you want zero extra optional dependencies

## 9.3 `pageindex_base`

### What it builds

Artifact family:

- `pageindex_tree`

Build outputs include:

- isolated `pageindex_index/`
- `master_tree.json`
- per-document saved trees
- per-document ingestion traces

### How it works

This build calls the real shared ingestion engine.

Each source document is ingested through `ingest_document_with_trace(...)`, which:

- converts DOCX to Markdown when needed
- builds a PageIndex-like per-document tree
- creates or updates a master node for that document
- updates the master tree

For this preset, relationship maintenance mode is:

- `off`

So it is the closest PageIndex-tree baseline to the original non-reconciled behavior.

### What it is good for

- pure tree baseline
- ablations against relationship-enriched variants
- debugging whether relationship maintenance helps or hurts

### When to use it

- when you want the simplest PageIndex build
- when you want a clean baseline before graph-like enrichment

### When not to use it

- when you want the best likely retrieval quality from the PageIndex family

## 9.4 `pageindex_related_basic`

### What it builds

Artifact family:

- `pageindex_tree`

This is the same broad output type as `pageindex_base`, but with:

- `relationship_mode = basic`

### How it works

After ingestion, it runs deterministic relationship reconciliation over the local neighborhood.

That means:

- no self-links
- no duplicates
- bounded related-docs lists
- symmetric direct relationships
- zero extra LLM calls

### What it is good for

- practical default structured baseline
- more stable cross-document relationships than `pageindex_base`
- better balance of cost and enrichment than `enhanced`

### When to use it

- when you want a strong structured default
- when you want relationship enrichment without extra LLM ingestion cost

### When not to use it

- when you specifically want to test the effect of no reconciliation
- when you need the strongest available relationship enrichment

## 9.5 `pageindex_related_enhanced`

### What it builds

Artifact family:

- `pageindex_tree`

This is the same broad PageIndex artifact family, but with:

- `relationship_mode = enhanced`

### How it works

Enhanced mode:

- computes a bounded candidate shortlist using deterministic metadata overlap
- runs a focused LLM pass to refine the final related-doc set
- then applies the same deterministic cleanup and symmetry rules as `basic`
- falls back to `basic` if the LLM step fails

### What it is good for

- richest PageIndex build variant currently available
- best chance of stronger related-doc structure
- useful when retrieval depends on subtle document relationships

### When to use it

- when you are optimizing for completeness and richer related-doc maintenance
- when extra ingestion cost is acceptable

### When not to use it

- when ingestion cost or build latency matters more than incremental relationship quality

## 10. Retrieval Profiles In Detail

There are eight retrieval profiles.

## 10.1 `rag_standard`

### Artifact family

- `rag_chunks`

### Retrieval behavior

This is simple lexical scoring over flat chunks.

It uses:

- token overlap
- token density
- a small document-title bonus

Then it selects the top `max_chunks`.

### Strengths

- cheap
- simple
- easy to inspect

### Weaknesses

- brittle to paraphrase
- no semantic retrieval
- no structure awareness

## 10.2 `rag_vector`

### Artifact family

- `rag_vector`

### Retrieval behavior

Dense-only local vector retrieval:

- embed the query locally
- run brute-force cosine or HNSW ANN over stored embeddings
- select final chunks from dense hits

### Strengths

- semantic matching
- good flat baseline

### Weaknesses

- can miss exact-keyword signals that lexical retrieval would catch

## 10.3 `rag_vector_rrf`

### Artifact family

- `rag_vector`

### Retrieval behavior

This combines:

- dense retrieval
- lexical retrieval
- reciprocal rank fusion

It is often the most balanced flat retrieval profile because it combines semantic recall and keyword precision.

### Strengths

- strong practical baseline
- robust to wording differences and exact-term constraints

### Weaknesses

- still flat-chunk retrieval
- still not structure-aware

## 10.4 `rag_vector_rerank`

### Artifact family

- `rag_vector`

### Retrieval behavior

This profile:

- retrieves dense and lexical candidates
- fuses them
- reranks top candidates with a local cross-encoder

Default reranker:

- `cross-encoder/ms-marco-MiniLM-L-6-v2`

### Strengths

- usually the strongest flat RAG profile
- better final candidate ordering

### Weaknesses

- slowest flat RAG profile
- still not tree-based

## 10.5 `hybrid`

### Artifact family

- `pageindex_tree`

### Retrieval behavior

This is the deterministic Hybrid Approach retrieval pipeline.

The shared engine flow is:

1. route to likely documents
2. navigate each selected doc tree
3. optionally verify navigation results
4. optionally expand to neighboring nodes
5. fetch selected nodes
6. answer from retrieved context

The query engine describes this as:

- router
- navigator
- verifier
- fetcher
- answer

### Strengths

- bounded
- traceable
- deterministic in shape
- strong default for production-like usage

### Weaknesses

- less flexible than the agentic PageIndex loop for hard questions

## 10.6 `hybrid_advanced`

### Artifact family

- `pageindex_tree`

### Retrieval behavior

This is the same `hybrid` path plus optional advanced features:

- query planning
- adaptive width
- node expansion

The planner can recommend:

- `recommended_max_docs`
- `recommended_max_nodes`
- whether node expansion is useful

The engine then applies those recommendations under caps.

### Strengths

- better recall for broad or workflow-style questions
- still more structured and bounded than agentic PageIndex

### Weaknesses

- more latency and cost than base `hybrid`

## 10.7 `pageindex`

### Artifact family

- `pageindex_tree`

### Retrieval behavior

This is the agentic PageIndex loop.

It works like this:

1. router selects likely docs
2. the model receives tool access
3. it can inspect document structures
4. it can fetch specific node contents
5. it can iterate and self-correct
6. when it stops calling tools, it emits the final answer

Important detail:

- it is not vector search
- it is not chunk ranking
- it is tool-driven exploration over document trees

### Budgets

The default experiments profile includes:

- `max_docs = 3`
- `max_tool_calls = 12`

and the underlying engine also has a content-token budget.

### Strengths

- more flexible than `hybrid`
- can explore structure iteratively
- best fit for hard multi-document reasoning

### Weaknesses

- higher latency
- higher variability
- higher cost

## 10.8 `pageindex_advanced`

### Artifact family

- `pageindex_tree`

### Retrieval behavior

This is `pageindex` plus advanced planning and adaptive routing policy.

So it combines:

- the agentic PageIndex tool loop
- planner-guided width recommendations
- capped broader exploration

### Strengths

- most flexible PageIndex retrieval variant in the experiments harness

### Weaknesses

- highest complexity and cost among the PageIndex retrieval profiles

## 11. Current Variant Matrix

There are currently five build presets and eight retrieval profiles, but only certain combinations are valid.

### Compatible combinations

#### `rag_standard` build

- `rag_standard`

#### `rag_vector` build

- `rag_vector`
- `rag_vector_rrf`
- `rag_vector_rerank`

#### `pageindex_base` build

- `hybrid`
- `hybrid_advanced`
- `pageindex`
- `pageindex_advanced`

#### `pageindex_related_basic` build

- `hybrid`
- `hybrid_advanced`
- `pageindex`
- `pageindex_advanced`

#### `pageindex_related_enhanced` build

- `hybrid`
- `hybrid_advanced`
- `pageindex`
- `pageindex_advanced`

### Total currently possible entry combinations

With the default catalog:

- `1` from `rag_standard`
- `3` from `rag_vector`
- `12` from the three `pageindex_*` builds

Total:

- `16` compatible build/retrieval combinations

Since the answer profile is currently fixed, those 16 combinations are the main comparison surface.

## 12. How The Shared Answer Layer Works

All retrieval variants feed into the same answer adapter.

That matters because it keeps the retrieval comparison fair.

The current answer profile:

- `aligned_default`

The answer layer:

- takes the user query
- takes retrieved context
- tells the model to answer using only that retrieved context
- asks for inline citations using visible chunk IDs or node refs
- returns answer text plus timing

This means the harness is mostly comparing:

- what got retrieved
- how much context was gathered
- how long retrieval took

not radically different answer prompt families.

## 13. What `hybrid` Versus `pageindex` Really Means

This distinction is the single most important one to understand on the PageIndex side.

### `hybrid`

`hybrid` is a fixed pipeline.

The system decides in advance:

- route docs
- navigate docs
- optionally verify nodes
- fetch nodes
- answer

This is structured, bounded, and easy to trace.

### `pageindex`

`pageindex` is an agentic tool loop.

The model decides:

- which document structure to inspect
- which nodes to open
- whether it needs more evidence
- when it is ready to answer

So:

- `hybrid` is pipeline-first
- `pageindex` is tool-loop-first

Both still depend on PageIndex artifacts. The difference is query-time control style.

## 14. What `advanced` Actually Means

The word `advanced` in this repo does not mean a different model family or a different artifact family.

It means optional query-time policy enhancements:

- query planning
- adaptive width
- node expansion

The planner classifies query intent and emits bounded recommendations like:

- fact lookup
- compare
- workflow/process
- policy/compliance
- troubleshooting
- architecture/design

Then it recommends:

- how many docs might be needed
- how many nodes per doc might be needed
- whether surrounding context is important

That output can dynamically change effective retrieval width under hard caps.

So `hybrid_advanced` and `pageindex_advanced` are really:

- the same base retrieval family
- plus broader and smarter query-time policy

## 15. What The Original Hybrid Approach UI Already Has

The main app already has the real shared engine behavior for:

- PageIndex ingestion
- relationship mode selection
- `hybrid` retrieval mode
- `pageindex` retrieval mode
- optional advanced retrieval controls

So the original app can already do the true non-RAG PageIndex work.

The experiments harness does not invent those capabilities. It repackages them into a comparison lab.

## 16. What The Original Hybrid Approach UI Does Not Have

The main app does not currently expose the full experiments framework as first-class product UX.

Specifically, the main UI does not provide:

- reusable corpora as explicit objects
- isolated build catalogs
- build manifests as a lab feature
- side-by-side comparison runs
- RAG baseline variants
- retrieval profile matrices
- per-run report exports in the same evaluation format
- manual winner annotation and rerun workflows

So the original app has the engine, but not the evaluation harness.

## 17. How To Duplicate Experiments Behavior In The Original UI

If you wanted the main app UI to fully duplicate the experiments layer, you would need to add at least these concepts:

### 17.1 Corpus registration

The main app currently ingests directly into the active project index.

To match experiments, the main app would need a separate corpus layer that:

- copies source documents into a stable corpus directory
- writes corpus manifests
- lets multiple builds reuse the same normalized document set

### 17.2 Explicit build runner

The main app would need a build screen that can create:

- `rag_standard`
- `rag_vector`
- `pageindex_base`
- `pageindex_related_basic`
- `pageindex_related_enhanced`

in isolated build directories instead of mutating one active app index.

### 17.3 Retrieval profile selector

The main app would need a retrieval-profile abstraction, not just a simple retrieval-mode toggle.

That means selecting:

- flat lexical RAG
- vector RAG
- fusion
- reranking
- deterministic PageIndex
- agentic PageIndex
- advanced variants

### 17.4 Comparison-run execution

The main app would need a run orchestration layer that:

- constructs entry lists
- executes compatible entries
- persists manifests
- stores traces
- exports reports

### 17.5 Reporting and annotation

The main app would need:

- JSON / CSV / Markdown / HTML exports
- operator winner labels
- notes
- question-type rollups

### What can already be duplicated today

The main app can already duplicate much of the non-RAG behavior manually by:

- ingesting docs with different relationship settings
- switching between `hybrid` and `pageindex`
- enabling or disabling advanced retrieval

What it cannot duplicate cleanly today is:

- isolated side-by-side artifact families
- RAG baseline builds
- reproducible comparison runs
- report exports and run manifests in the experiments format

## 18. Recommendations By Goal

### If the goal is production-like PageIndex usage

Prefer:

- `pageindex_related_basic + hybrid`

Why:

- good structure
- good related-doc maintenance
- deterministic retrieval
- lower cost and variability than agentic mode

### If the goal is hard multi-document reasoning

Prefer:

- `pageindex_related_basic + pageindex`
- or `pageindex_related_enhanced + pageindex_advanced`

Why:

- the model can inspect trees and fetch more evidence iteratively

### If the goal is strongest flat baseline

Prefer:

- `rag_vector + rag_vector_rrf`
- or `rag_vector + rag_vector_rerank`

Why:

- they are the strongest non-PageIndex local baselines in the harness

### If the goal is ablation and debugging

Prefer:

- `pageindex_base + hybrid`
- `pageindex_related_basic + hybrid`
- `pageindex_related_enhanced + hybrid`

Why:

- this isolates the impact of relationship maintenance without changing the retrieval controller family

### If the goal is cost-sensitive quick checks

Prefer:

- `rag_standard + rag_standard`
- or `pageindex_related_basic + hybrid`

Why:

- simpler, cheaper, and easier to reason about than advanced/agentic variants

## 19. When Not To Use Certain Variants

### Avoid `rag_standard` when

- semantics matter more than keywords
- questions are paraphrase-heavy
- document structure matters

### Avoid `rag_vector` family when

- you only care about the real PageIndex product path
- you do not want optional local embedding dependencies

### Avoid `pageindex_base` when

- you want the best likely structured retrieval quality
- you are past the baseline-debugging phase

### Avoid `pageindex` when

- latency needs to stay tight
- cost predictability matters
- you do not need iterative exploration

### Avoid `advanced` variants when

- you want simplest possible tracing
- query breadth is low
- the added planner and exploration overhead is not justified

## 20. Important Metrics To Interpret

Each run entry captures:

- TTFT
- total time
- prompt tokens
- completion tokens
- total tokens
- LLM call count
- retrieved context tokens
- selected docs
- selected nodes
- retrieval trace

For PageIndex agentic mode, you should care especially about:

- tool calls made
- tool-call budget
- content tokens used
- content-token budget
- explored docs
- whether the tool budget was exhausted
- whether the content budget was exhausted

For `hybrid`, you should care especially about:

- routed docs
- selected nodes
- whether navigation verification was applied
- whether node expansion was applied
- whether retrieval width was widened by advanced planning

For RAG, you should care especially about:

- candidate chunk count
- selected chunk count
- whether dense-only, RRF, or reranking was used

## 21. The Biggest Conceptual Differences Between Variant Families

### Flat RAG family

Mental model:

- split text into chunks
- rank chunks
- answer from chunks

This family is chunk-centric.

### PageIndex family

Mental model:

- build document trees
- route to likely docs
- navigate sections or inspect structure
- fetch exact sections
- answer from section evidence

This family is structure-centric.

### `hybrid` versus `pageindex`

Mental model:

- `hybrid`: the system decides the retrieval choreography
- `pageindex`: the model decides the retrieval choreography

## 22. The Real Answer To "What Is Going On Here?"

This project has two overlapping identities:

### Identity 1: the original Hybrid Approach product

A PageIndex-based multi-document retrieval system with:

- real ingestion
- real routing
- real structured retrieval
- real `hybrid` and `pageindex` modes

### Identity 2: the experiments lab

A controlled evaluation environment that:

- wraps the real engine
- adds baseline alternatives
- adds reproducibility
- adds side-by-side comparison
- adds reporting

The confusion usually comes from mixing those two layers together.

The clean mental split is:

- the main app is the system you use
- the experiments app is the lab you use to understand and compare the system

## 23. Practical Defaults

If you need defaults and do not want to think too hard:

- Best practical PageIndex default:
  `pageindex_related_basic + hybrid`

- Best exploratory PageIndex default:
  `pageindex_related_basic + pageindex`

- Best flat baseline:
  `rag_vector + rag_vector_rrf`

- Best strongest flat baseline:
  `rag_vector + rag_vector_rerank`

- Best relationship ablation trio:
  `pageindex_base + hybrid`
  `pageindex_related_basic + hybrid`
  `pageindex_related_enhanced + hybrid`

## 24. What Does Not Exist Yet

The experiments harness is already useful, but it is not a full evaluation platform yet.

Missing or intentionally deferred pieces include:

- dataset-driven benchmark suites
- automatic grading against gold answers
- large batch orchestration
- broader statistical benchmarking over many corpora and many question sets
- richer answer-profile families

So this is currently a powerful manual comparison lab, not a full automated benchmark framework.

## 25. Final Takeaway

If you remember nothing else:

- `experiments` is not fake
- it reuses the real Hybrid Approach engine for all the PageIndex and Hybrid retrieval work
- it adds RAG baselines and comparison/reporting infrastructure on top
- the main app already contains the real non-RAG engine behavior
- the experiments app exists so you can compare variants cleanly instead of arguing about them abstractly

That is the whole map.
