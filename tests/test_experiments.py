"""Tests for the isolated experiments harness scaffold and build layer."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
from pathlib import Path

import numpy as np

from experiments.builds.presets import (
    pageindex_related_basic_preset,
    rag_standard_preset,
    rag_vector_preset,
)
from experiments.builds.registry import ArtifactBuildRunner
from experiments.compare import summarize_comparison
from experiments.corpora import CorpusStore
from experiments.models import (
    BuildManifest,
    CorpusDocumentInput,
    RetrievalProfileSpec,
    RunEntryMetrics,
    RunEntrySpec,
    RunEntrySummary,
)
from experiments.reports import write_aggregate_reports
from experiments.profiles import (
    aligned_default_answer_profile,
    rag_standard_profile,
    rag_vector_rerank_profile,
)
from experiments.retrieval.base import RetrievalExecutionContext, RetrievalExecutionResult
from experiments.retrieval.rag import RAGStandardRetrievalAdapter
from experiments.retrieval.rag_vector import VectorRAGRetrievalAdapter
from experiments.runs.registry import ExperimentRunRunner
from experiments.workflows import (
    compatible_profile_ids_for_artifact_family,
    create_run_entries,
)
from ingestion.ingest import IngestionResult, IngestionStep, IngestionTrace
from master_tree.schema import MasterNode, RelevanceHints, TopSection


def test_create_corpus_normalises_and_copies_files(tmp_path: Path) -> None:
    """CorpusStore should copy source docs once and write a reusable manifest."""
    source = tmp_path / "auth_spec.md"
    source.write_text("# Auth\n\nToken refresh details.\n", encoding="utf-8")

    store = CorpusStore(tmp_path / "exp")
    manifest = store.create_corpus(
        name="Auth Corpus",
        documents=[CorpusDocumentInput(source_path=str(source), doc_type="spec")],
    )

    assert manifest.corpus_id == "auth_corpus"
    assert len(manifest.documents) == 1
    copied = Path(manifest.documents[0].normalized_path)
    assert copied.exists()
    assert copied.read_text(encoding="utf-8") == source.read_text(encoding="utf-8")

    loaded = store.load_corpus(manifest.corpus_id)
    assert loaded.corpus_id == manifest.corpus_id
    assert len(store.list_corpora()) == 1


def test_rag_build_runner_writes_flat_chunk_artifacts(tmp_path: Path) -> None:
    """The baseline RAG builder should emit a completed chunk-manifest build."""
    source = tmp_path / "process.md"
    source.write_text(
        "# Onboarding\n\n"
        "Step one is account creation.\n\n"
        "Step two is role assignment.\n\n"
        "Step three is audit verification.\n",
        encoding="utf-8",
    )

    store = CorpusStore(tmp_path / "exp")
    corpus = store.create_corpus(
        name="Workflow Corpus",
        documents=[CorpusDocumentInput(source_path=str(source))],
    )

    runner = ArtifactBuildRunner(tmp_path / "exp")
    manifest = runner.run_build(corpus.corpus_id, rag_standard_preset())

    assert manifest.status == "completed"
    assert manifest.artifact_family == "rag_chunks"
    assert manifest.metrics["docs_processed"] == 1
    assert manifest.metrics["total_chunks"] >= 1

    chunks_path = Path(manifest.outputs[0].path)
    payload = json.loads(chunks_path.read_text(encoding="utf-8"))
    assert payload
    assert payload[0]["doc_id"] == corpus.documents[0].doc_id


def test_pageindex_build_runner_uses_isolated_ingestion_pipeline(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """The PageIndex builder should reuse production ingestion without touching main data."""
    source = tmp_path / "auth.md"
    source.write_text("# Auth\n\n## Refresh\n\nDetails.\n", encoding="utf-8")

    store = CorpusStore(tmp_path / "exp")
    corpus = store.create_corpus(
        name="PageIndex Corpus",
        documents=[CorpusDocumentInput(source_path=str(source), doc_id="auth_doc")],
    )

    captured: dict[str, object] = {}

    async def fake_ingest_document_with_trace(**kwargs):
        captured["relationship_mode"] = kwargs["relationship_mode"]
        captured["top_sections_target"] = kwargs["top_sections_target"]

        tree = {
            "nodes": [
                {
                    "node_id": "0001",
                    "title": "Auth",
                    "start_index": 1,
                    "end_index": 1,
                    "nodes": [],
                }
            ]
        }
        tree_path = kwargs["storage"].save_doc_tree(kwargs["doc_id"], tree)
        kwargs["storage"].register_doc_source(kwargs["doc_id"], kwargs["file_path"])

        master_node = MasterNode(
            doc_id=kwargs["doc_id"],
            doc_title=kwargs["doc_title"],
            doc_type=kwargs["doc_type"],
            file_path=kwargs["file_path"],
            tree_path=tree_path,
            doc_summary="Authentication specification.",
            key_topics=["auth", "token refresh"],
            relevance_hints=RelevanceHints(
                best_for="Questions about authentication.",
                not_useful_for="Billing questions.",
                key_categories=["identity"],
            ),
            top_sections=[
                TopSection(
                    title="Auth",
                    node_ref=f"{kwargs['doc_id']}::0001",
                    section_summary="Auth section.",
                )
            ],
            related_docs=[],
            ingested_at="2026-04-04T00:00:00+00:00",
        )
        kwargs["master_tree_store"].add_node(master_node)
        kwargs["master_tree_store"].save(kwargs["master_tree_store"].tree)

        trace = IngestionTrace(
            doc_id=kwargs["doc_id"],
            file_path=kwargs["file_path"],
            file_type=".md",
            tree_path=tree_path,
            relationship_mode=kwargs["relationship_mode"],
            steps=[
                IngestionStep(
                    name="fake_ingest",
                    detail="Synthetic ingestion for tests.",
                )
            ],
        )
        return IngestionResult(master_node=master_node, per_doc_tree=tree, trace=trace)

    monkeypatch.setattr(
        "experiments.builds.pageindex.ingest_document_with_trace",
        fake_ingest_document_with_trace,
    )

    runner = ArtifactBuildRunner(tmp_path / "exp")
    manifest = runner.run_build(
        corpus.corpus_id,
        pageindex_related_basic_preset(top_sections_target=6),
    )

    assert manifest.status == "completed"
    assert manifest.artifact_family == "pageindex_tree"
    assert manifest.metrics["relationship_mode"] == "basic"
    assert captured["relationship_mode"] == "basic"
    assert captured["top_sections_target"] == 6

    master_tree_output = next(o for o in manifest.outputs if o.label == "master_tree")
    assert Path(master_tree_output.path).exists()
    assert manifest.documents[0].trace_path is not None
    assert Path(manifest.documents[0].trace_path).exists()


def test_build_runner_reuses_completed_manifest_when_config_matches(tmp_path: Path) -> None:
    """Repeated builds with the same corpus+config should reuse cached artifacts by default."""
    source = tmp_path / "doc.md"
    source.write_text("# Title\n\nSome content here.\n", encoding="utf-8")

    store = CorpusStore(tmp_path / "exp")
    corpus = store.create_corpus(
        name="Reuse Corpus",
        documents=[CorpusDocumentInput(source_path=str(source))],
    )
    runner = ArtifactBuildRunner(tmp_path / "exp")
    config = rag_standard_preset()

    first = runner.run_build(corpus.corpus_id, config)
    second = runner.run_build(corpus.corpus_id, config)

    assert first.build_id == second.build_id
    assert second.status == "completed"


def test_vector_rag_build_runner_writes_local_embedding_artifacts(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """The vector-RAG builder should persist local embeddings and metadata."""
    source = tmp_path / "vector.md"
    source.write_text(
        "# Auth\n\nToken refresh happens after role assignment.\n\n"
        "Audit verification follows the role update.\n",
        encoding="utf-8",
    )

    store = CorpusStore(tmp_path / "exp")
    corpus = store.create_corpus(
        name="Vector Corpus",
        documents=[CorpusDocumentInput(source_path=str(source), doc_id="vector_doc")],
    )

    def fake_embed_texts_locally(texts, *, model_name, normalize_embeddings=True):
        return np.asarray(
            [[float(index + 1), float(index + 2), float(index + 3)] for index, _ in enumerate(texts)],
            dtype=np.float32,
        )

    monkeypatch.setattr(
        "experiments.builds.rag_vector.embed_texts_locally",
        fake_embed_texts_locally,
    )
    monkeypatch.setattr(
        "experiments.builds.rag_vector.build_hnsw_index",
        lambda **kwargs: False,
    )

    runner = ArtifactBuildRunner(tmp_path / "exp")
    manifest = runner.run_build(corpus.corpus_id, rag_vector_preset())

    assert manifest.status == "completed"
    assert manifest.artifact_family == "rag_vector"
    assert manifest.metrics["embedding_model"] == "sentence-transformers/all-MiniLM-L6-v2"
    assert manifest.metrics["vector_backend"] == "brute_force"
    output_labels = {output.label for output in manifest.outputs}
    assert "rag_vector_chunks" in output_labels
    assert "rag_vector_embeddings" in output_labels
    assert "rag_vector_meta" in output_labels
    assert "rag_vector_lexical" in output_labels


def test_vector_rag_retrieval_adapter_supports_rrf_and_reranking(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """The vector-RAG adapter should retrieve locally and allow rerank overrides."""
    build_dir = tmp_path / "build"
    build_dir.mkdir(parents=True, exist_ok=True)

    chunks = [
        {
            "chunk_id": "doc::chunk_0000",
            "doc_id": "doc",
            "doc_title": "Workflow",
            "text": "Role assignment happens before audit verification.",
            "page_start": None,
            "page_end": None,
        },
        {
            "chunk_id": "doc::chunk_0001",
            "doc_id": "doc",
            "doc_title": "Workflow",
            "text": "Audit verification confirms the assignment.",
            "page_start": None,
            "page_end": None,
        },
    ]
    chunks_path = build_dir / "rag_vector_chunks.json"
    chunks_path.write_text(json.dumps(chunks), encoding="utf-8")
    np.save(build_dir / "rag_vector_embeddings.npy", np.asarray([[1.0, 0.0], [0.2, 0.9]], dtype=np.float32))
    (build_dir / "rag_vector_meta.json").write_text(
        json.dumps(
            {
                "embedding_model": "fake-local-model",
                "vector_backend": "brute_force",
                "normalize_embeddings": True,
            }
        ),
        encoding="utf-8",
    )
    (build_dir / "rag_vector_lexical.json").write_text(
        json.dumps(
            [
                {"chunk_id": "doc::chunk_0000", "doc_id": "doc", "tokens": ["role", "assignment", "before", "audit"]},
                {"chunk_id": "doc::chunk_0001", "doc_id": "doc", "tokens": ["audit", "verification", "assignment"]},
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "experiments.retrieval.rag_vector.embed_texts_locally",
        lambda texts, **kwargs: np.asarray([[1.0, 0.0]], dtype=np.float32),
    )
    monkeypatch.setattr(
        "experiments.retrieval.rag_vector.rerank_pairs_locally",
        lambda **kwargs: [0.1, 0.9],
    )

    build_manifest = BuildManifest(
        build_id="build_vector",
        corpus_id="corpus",
        build_kind="rag_vector",
        build_label="rag_vector",
        artifact_family="rag_vector",
        status="completed",
        created_at="2026-04-04T00:00:00+00:00",
        config={},
        outputs=[
            {"label": "rag_vector_chunks", "path": str(chunks_path)},
            {"label": "rag_vector_embeddings", "path": str(build_dir / "rag_vector_embeddings.npy")},
            {"label": "rag_vector_meta", "path": str(build_dir / "rag_vector_meta.json")},
            {"label": "rag_vector_lexical", "path": str(build_dir / "rag_vector_lexical.json")},
        ],
    )

    result = asyncio.run(
        VectorRAGRetrievalAdapter().retrieve(
            RetrievalExecutionContext(
                build=build_manifest,
                build_dir=build_dir,
                query="How does audit verification relate to role assignment?",
                model="test-model",
                conversation_context=None,
                retrieval_profile=rag_vector_rerank_profile(),
            )
        )
    )

    assert result.selected_docs == ["doc"]
    assert result.selected_nodes[0] == "doc::chunk_0001"
    assert result.trace["mode"] == "rag_vector"
    assert result.trace["fusion"]["method"] == "rrf"
    assert result.retrieval_metrics["selected_chunks"] >= 1


async def _fake_answer_from_context(**kwargs):
    return "Synthetic answer.", {"answer_time_seconds": 0.35, "ttft_seconds": 0.12}


@dataclass
class _FakeRetrievalAdapter:
    mode: str = "rag_standard"
    artifact_family: str = "rag_chunks"

    async def retrieve(self, context: RetrievalExecutionContext) -> RetrievalExecutionResult:
        return RetrievalExecutionResult(
            selected_docs=["doc_alpha"],
            selected_nodes=["chunk_0001"],
            retrieved_context="[chunk_0001]\n\nImportant answer evidence.",
            sources=[{"node_ref": "chunk_0001", "doc_id": "doc_alpha"}],
            trace={"mode": self.mode, "scaffold": True},
            retrieval_metrics={
                "retrieval_time_seconds": 0.25,
                "retrieved_context_tokens": 42,
            },
        )


def test_rag_retrieval_adapter_returns_ranked_chunks(tmp_path: Path) -> None:
    """The baseline retrieval adapter should return scored chunks and selected docs."""
    source = tmp_path / "process.md"
    source.write_text(
        "# Onboarding\n\n"
        "Account creation is the first step.\n\n"
        "Role assignment comes next.\n\n"
        "Audit verification is the final step.\n",
        encoding="utf-8",
    )

    store = CorpusStore(tmp_path / "exp")
    corpus = store.create_corpus(
        name="Retrieval Corpus",
        documents=[CorpusDocumentInput(source_path=str(source), doc_id="workflow_doc")],
    )
    runner = ArtifactBuildRunner(tmp_path / "exp")
    build = runner.run_build(corpus.corpus_id, rag_standard_preset())

    adapter = RAGStandardRetrievalAdapter()
    result = asyncio.run(
        adapter.retrieve(
            RetrievalExecutionContext(
                build=build,
                build_dir=runner.get_build_dir(build.build_id),
                query="How does role assignment relate to audit verification?",
                model="test-model",
                conversation_context=None,
                retrieval_profile=rag_standard_profile(),
            )
        )
    )

    assert result.selected_docs == ["workflow_doc"]
    assert result.selected_nodes
    assert result.retrieved_context
    assert result.sources
    assert result.trace["mode"] == "rag_standard"
    assert result.retrieval_metrics["selected_chunks"] >= 1


def test_experiment_run_runner_persists_manifest_trace_and_reports(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Comparison runs should persist entry traces and summary reports."""
    source = tmp_path / "doc.md"
    source.write_text("# Title\n\nUseful content.\n", encoding="utf-8")

    store = CorpusStore(tmp_path / "exp")
    corpus = store.create_corpus(
        name="Run Corpus",
        documents=[CorpusDocumentInput(source_path=str(source), doc_id="doc_alpha")],
    )
    build_runner = ArtifactBuildRunner(tmp_path / "exp")
    build = build_runner.run_build(corpus.corpus_id, rag_standard_preset())

    monkeypatch.setattr(
        "experiments.runs.registry.get_retrieval_adapter",
        lambda mode: _FakeRetrievalAdapter(mode=mode),
    )
    monkeypatch.setattr(
        "experiments.runs.registry.answer_from_context",
        _fake_answer_from_context,
    )

    run_runner = ExperimentRunRunner(tmp_path / "exp")
    manifest = run_runner.run_comparison(
        query="What is the useful content?",
        model="test-model",
        entries=[
            RunEntrySpec(
                label="RAG Baseline",
                build_id=build.build_id,
                retrieval_profile=rag_standard_profile(),
                answer_profile=aligned_default_answer_profile(),
            )
        ],
        title="Synthetic comparison",
    )

    assert manifest.status == "completed"
    assert manifest.summary is not None
    assert manifest.summary.fastest_entry_label == "RAG Baseline"
    assert manifest.spec_hash
    assert manifest.question_type == "unspecified"
    assert len(manifest.requested_entries) == 1
    assert len(manifest.entries) == 1
    entry = manifest.entries[0]
    assert entry.status == "completed"
    assert entry.answer == "Synthetic answer."
    assert entry.metrics is not None
    assert entry.metrics.total_time_seconds == 0.6
    assert entry.trace_path is not None
    assert Path(entry.trace_path).exists()
    assert manifest.report_paths
    assert Path(manifest.report_paths["summary_json"]).exists()
    assert Path(manifest.report_paths["entries_csv"]).exists()
    assert Path(manifest.report_paths["overview_md"]).exists()
    assert Path(manifest.report_paths["overview_html"]).exists()

    loaded = run_runner.load_run(manifest.run_id)
    assert loaded.run_id == manifest.run_id
    assert len(run_runner.list_runs()) == 1


def test_experiment_run_runner_skips_incompatible_build_profile_pair(
    tmp_path: Path,
) -> None:
    """Run entries should be skipped when the build/artifact family does not match."""
    source = tmp_path / "doc.md"
    source.write_text("# Title\n\nUseful content.\n", encoding="utf-8")

    store = CorpusStore(tmp_path / "exp")
    corpus = store.create_corpus(
        name="Mismatch Corpus",
        documents=[CorpusDocumentInput(source_path=str(source), doc_id="doc_alpha")],
    )
    build_runner = ArtifactBuildRunner(tmp_path / "exp")
    build = build_runner.run_build(corpus.corpus_id, rag_standard_preset())
    run_runner = ExperimentRunRunner(tmp_path / "exp")

    manifest = run_runner.run_comparison(
        query="Does this skip cleanly?",
        model="test-model",
        entries=[
            RunEntrySpec(
                label="Incompatible Hybrid",
                build_id=build.build_id,
                retrieval_profile=RetrievalProfileSpec(
                    profile_id="hybrid",
                    artifact_family="pageindex_tree",
                    mode="hybrid",
                ),
                answer_profile=aligned_default_answer_profile(),
            )
        ],
    )

    assert manifest.status == "failed"
    assert len(manifest.entries) == 1
    entry = manifest.entries[0]
    assert entry.status == "skipped"
    assert "Incompatible artifact family" in (entry.error or "")


def test_rerun_failed_entries_creates_derived_run(
    tmp_path: Path,
) -> None:
    """Rerunning failed/skipped entries should create a new derived manifest."""
    source = tmp_path / "doc.md"
    source.write_text("# Title\n\nUseful content.\n", encoding="utf-8")

    store = CorpusStore(tmp_path / "exp")
    corpus = store.create_corpus(
        name="Rerun Corpus",
        documents=[CorpusDocumentInput(source_path=str(source), doc_id="doc_alpha")],
    )
    build_runner = ArtifactBuildRunner(tmp_path / "exp")
    build = build_runner.run_build(corpus.corpus_id, rag_standard_preset())
    run_runner = ExperimentRunRunner(tmp_path / "exp")

    original = run_runner.run_comparison(
        query="Does this rerun cleanly?",
        model="test-model",
        question_type="compare_contrast",
        entries=[
            RunEntrySpec(
                label="Incompatible Hybrid",
                build_id=build.build_id,
                retrieval_profile=RetrievalProfileSpec(
                    profile_id="hybrid",
                    artifact_family="pageindex_tree",
                    mode="hybrid",
                ),
                answer_profile=aligned_default_answer_profile(),
            )
        ],
    )

    rerun = run_runner.rerun_entries(original.run_id, failed_only=True)

    assert rerun.run_id != original.run_id
    assert rerun.source_run_id == original.run_id
    assert rerun.question_type == "compare_contrast"
    assert len(rerun.requested_entries) == 1
    assert rerun.entries[0].label == "Incompatible Hybrid"


def test_aggregate_reports_capture_question_type_and_operator_wins(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Aggregate reports should summarize runs by question type and human winner."""
    source = tmp_path / "doc.md"
    source.write_text("# Title\n\nUseful content.\n", encoding="utf-8")

    store = CorpusStore(tmp_path / "exp")
    corpus = store.create_corpus(
        name="Aggregate Corpus",
        documents=[CorpusDocumentInput(source_path=str(source), doc_id="doc_alpha")],
    )
    build_runner = ArtifactBuildRunner(tmp_path / "exp")
    build = build_runner.run_build(corpus.corpus_id, rag_standard_preset())

    monkeypatch.setattr(
        "experiments.runs.registry.get_retrieval_adapter",
        lambda mode: _FakeRetrievalAdapter(mode=mode),
    )
    monkeypatch.setattr(
        "experiments.runs.registry.answer_from_context",
        _fake_answer_from_context,
    )

    run_runner = ExperimentRunRunner(tmp_path / "exp")
    run = run_runner.run_comparison(
        query="Which baseline wins?",
        model="test-model",
        question_type="factual",
        entries=[
            RunEntrySpec(
                label="RAG Baseline",
                build_id=build.build_id,
                retrieval_profile=rag_standard_profile(),
                answer_profile=aligned_default_answer_profile(),
            )
        ],
    )
    annotated = run_runner.annotate_run(
        run.run_id,
        operator_winner_label="RAG Baseline",
        notes="Clearer and cheaper.",
    )

    report_paths = write_aggregate_reports(
        runs=run_runner.list_runs_with(annotated),
        reports_dir=run_runner.paths.reports_dir,
    )
    payload = json.loads(Path(report_paths["question_type_json"]).read_text(encoding="utf-8"))

    assert payload["question_types"]["factual"]["runs"] >= 1
    assert payload["question_types"]["factual"]["operator_wins"]["RAG Baseline"] >= 1
    assert Path(report_paths["question_type_html"]).exists()


def test_summarize_comparison_selects_fastest_lowest_and_largest() -> None:
    """Comparison rollups should highlight latency, token, and context winners."""
    summary = summarize_comparison(
        [
            RunEntrySummary(
                label="A",
                build_id="build_a",
                artifact_family="rag_chunks",
                retrieval_profile_id="rag",
                answer_profile_id="aligned_default",
                metrics=RunEntryMetrics(
                    ttft_seconds=0.2,
                    total_time_seconds=1.0,
                    retrieval_time_seconds=0.4,
                    answer_time_seconds=0.6,
                    prompt_tokens=30,
                    completion_tokens=10,
                    total_tokens=40,
                    llm_calls=1,
                    retrieved_context_tokens=120,
                ),
            ),
            RunEntrySummary(
                label="B",
                build_id="build_b",
                artifact_family="pageindex_tree",
                retrieval_profile_id="hybrid",
                answer_profile_id="aligned_default",
                metrics=RunEntryMetrics(
                    ttft_seconds=0.3,
                    total_time_seconds=1.5,
                    retrieval_time_seconds=0.7,
                    answer_time_seconds=0.8,
                    prompt_tokens=20,
                    completion_tokens=5,
                    total_tokens=25,
                    llm_calls=1,
                    retrieved_context_tokens=200,
                ),
            ),
        ]
    )

    assert summary.fastest_entry_label == "A"
    assert summary.lowest_tokens_entry_label == "B"
    assert summary.largest_context_entry_label == "B"


def test_create_run_entries_only_materializes_compatible_pairs(tmp_path: Path) -> None:
    """The workflow helper should only pair builds with compatible retrieval modes."""
    source = tmp_path / "doc.md"
    source.write_text("# Title\n\nUseful content.\n", encoding="utf-8")

    store = CorpusStore(tmp_path / "exp")
    corpus = store.create_corpus(
        name="Compatibility Corpus",
        documents=[CorpusDocumentInput(source_path=str(source), doc_id="doc_alpha")],
    )
    runner = ArtifactBuildRunner(tmp_path / "exp")
    rag_build = runner.run_build(corpus.corpus_id, rag_standard_preset())

    entries = create_run_entries(
        builds=[rag_build],
        selected_profile_ids=["rag_standard", "hybrid", "pageindex"],
        retrieval_reasoning_effort="medium",
        answer_reasoning_effort="low",
    )

    assert len(entries) == 1
    assert entries[0].retrieval_profile.profile_id == "rag_standard"
    assert entries[0].answer_profile.reasoning_effort == "low"
    assert compatible_profile_ids_for_artifact_family("rag_chunks") == ["rag_standard"]
