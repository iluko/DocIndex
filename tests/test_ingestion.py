"""Tests that document the intended ingestion behavior."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import ingestion.ingest as ingest_module
import master_tree.relationships as rel_module
from master_tree.master_tree import MasterTreeStore
from master_tree.relationships import ReconciliationResult
from master_tree.schema import MasterNode, RelevanceHints, TopSection
from storage.store import DocumentStore


FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _load_fixture(name: str) -> dict:
    """Load one JSON fixture from the shared test-fixtures directory."""
    return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))


def test_ingest_document_saves_tree_and_updates_master_tree(
    tmp_path: Path, monkeypatch
) -> None:
    """Ingestion should save the tree, source path, and master-tree entry."""
    master_tree_store = MasterTreeStore(str(tmp_path / "master_tree.json"))
    storage = DocumentStore(str(tmp_path / "data"))
    sample_tree = _load_fixture("sample_doc_tree.json")
    document_path = tmp_path / "auth_spec.md"
    document_path.write_text("# Auth\n\nToken refresh details.\n", encoding="utf-8")

    async def fake_run_pageindex(file_path: str, model: str, pageindex_opts: dict | None):
        """Return the fixture tree instead of calling the real PageIndex dependency."""
        assert file_path == str(document_path.resolve())
        assert model == "test-model"
        assert pageindex_opts == {"max_page_num_each_node": 5}
        return sample_tree

    async def fake_generate_master_node(**kwargs):
        """Return a deterministic master node for the test."""
        assert kwargs["doc_id"] == "sip_auth_spec_v2"
        assert kwargs["per_doc_tree"] == sample_tree
        assert kwargs["tree_path"].endswith("sip_auth_spec_v2_tree.json")
        assert kwargs["top_sections_target"] == 5
        return MasterNode(
            doc_id="sip_auth_spec_v2",
            doc_title="SIP Auth Spec",
            doc_type="technical_spec",
            file_path=kwargs["file_path"],
            tree_path=kwargs["tree_path"],
            doc_summary="Auth document summary.",
            key_topics=["Authentication", "JWT"],
            relevance_hints=RelevanceHints(
                best_for="Auth questions.",
                not_useful_for="Analytics questions.",
                key_categories=["Auth"],
            ),
            top_sections=[
                TopSection(
                    title="Authentication Overview",
                    node_ref="sip_auth_spec_v2::0001",
                    section_summary="Overview section.",
                )
            ],
            related_docs=[],
            ingested_at="2026-03-17T00:00:00",
        )

    monkeypatch.setattr(ingest_module, "_run_pageindex", fake_run_pageindex)
    monkeypatch.setattr(ingest_module, "generate_master_node", fake_generate_master_node)

    result = asyncio.run(
        ingest_module.ingest_document(
            file_path=str(document_path),
            doc_id="sip_auth_spec_v2",
            doc_title="SIP Auth Spec",
            doc_type="technical_spec",
            master_tree_store=master_tree_store,
            storage=storage,
            model="test-model",
            pageindex_opts={"max_page_num_each_node": 5},
            top_sections_target=5,
        )
    )

    assert result.doc_id == "sip_auth_spec_v2"
    assert storage.load_doc_tree("sip_auth_spec_v2") == sample_tree
    assert storage.load_doc_source_path("sip_auth_spec_v2") == str(document_path.resolve())
    assert master_tree_store.get_node("sip_auth_spec_v2") is not None


def test_ingest_document_with_trace_exposes_internal_mapping(
    tmp_path: Path, monkeypatch
) -> None:
    """The traced ingestion API should expose the expected milestone sequence."""
    master_tree_store = MasterTreeStore(str(tmp_path / "master_tree.json"))
    storage = DocumentStore(str(tmp_path / "data"))
    sample_tree = _load_fixture("sample_doc_tree.json")
    document_path = tmp_path / "auth_spec.md"
    document_path.write_text("# Auth\n\nToken refresh details.\n", encoding="utf-8")

    async def fake_run_pageindex(file_path: str, model: str, pageindex_opts: dict | None):
        """Return the fixture tree without touching the real dependency."""
        return sample_tree

    async def fake_generate_master_node(**kwargs):
        """Return a deterministic master node so the trace is predictable."""
        return MasterNode(
            doc_id="sip_auth_spec_v2",
            doc_title="SIP Auth Spec",
            doc_type="technical_spec",
            file_path=kwargs["file_path"],
            tree_path=kwargs["tree_path"],
            doc_summary="Auth document summary.",
            key_topics=["Authentication", "JWT"],
            relevance_hints=RelevanceHints(
                best_for="Auth questions.",
                not_useful_for="Analytics questions.",
                key_categories=["Auth"],
            ),
            top_sections=[
                TopSection(
                    title="Authentication Overview",
                    node_ref="sip_auth_spec_v2::0001",
                    section_summary="Overview section.",
                )
            ],
            related_docs=[],
            ingested_at="2026-03-17T00:00:00",
        )

    monkeypatch.setattr(ingest_module, "_run_pageindex", fake_run_pageindex)
    monkeypatch.setattr(ingest_module, "generate_master_node", fake_generate_master_node)

    result = asyncio.run(
        ingest_module.ingest_document_with_trace(
            file_path=str(document_path),
            doc_id="sip_auth_spec_v2",
            doc_title="SIP Auth Spec",
            doc_type="technical_spec",
            master_tree_store=master_tree_store,
            storage=storage,
            relationship_mode="off",  # preserve exact step sequence from before v1.2
        )
    )

    assert result.master_node.doc_id == "sip_auth_spec_v2"
    assert result.trace.tree_path.endswith("sip_auth_spec_v2_tree.json")
    assert [step.name for step in result.trace.steps] == [
        "validate_input",
        "build_pageindex_tree",
        "persist_tree",
        "generate_master_node",
        "update_master_tree",
    ]


def test_ingest_document_with_trace_emits_progress_events(
    tmp_path: Path, monkeypatch
) -> None:
    """Progress callbacks should receive high-level ingestion lifecycle events."""
    master_tree_store = MasterTreeStore(str(tmp_path / "master_tree.json"))
    storage = DocumentStore(str(tmp_path / "data"))
    sample_tree = _load_fixture("sample_doc_tree.json")
    document_path = tmp_path / "auth_spec.md"
    document_path.write_text("# Auth\n\nToken refresh details.\n", encoding="utf-8")
    progress_events: list[dict] = []

    async def fake_run_pageindex(file_path: str, model: str, pageindex_opts: dict | None):
        """Return the fixture tree so we can focus on progress events only."""
        return sample_tree

    async def fake_generate_master_node(**kwargs):
        """Return a deterministic master node for the progress-callback test."""
        return MasterNode(
            doc_id="sip_auth_spec_v2",
            doc_title="SIP Auth Spec",
            doc_type="technical_spec",
            file_path=kwargs["file_path"],
            tree_path=kwargs["tree_path"],
            doc_summary="Auth document summary.",
            key_topics=["Authentication", "JWT"],
            relevance_hints=RelevanceHints(
                best_for="Auth questions.",
                not_useful_for="Analytics questions.",
                key_categories=["Auth"],
            ),
            top_sections=[
                TopSection(
                    title="Authentication Overview",
                    node_ref="sip_auth_spec_v2::0001",
                    section_summary="Overview section.",
                )
            ],
            related_docs=[],
            ingested_at="2026-03-17T00:00:00",
        )

    monkeypatch.setattr(ingest_module, "_run_pageindex", fake_run_pageindex)
    monkeypatch.setattr(ingest_module, "generate_master_node", fake_generate_master_node)

    result = asyncio.run(
        ingest_module.ingest_document_with_trace(
            file_path=str(document_path),
            doc_id="sip_auth_spec_v2",
            doc_title="SIP Auth Spec",
            doc_type="technical_spec",
            master_tree_store=master_tree_store,
            storage=storage,
            progress_callback=progress_events.append,
        )
    )

    assert result.master_node.doc_id == "sip_auth_spec_v2"
    assert any(event["event"] == "pageindex_started" for event in progress_events)
    assert any(
        event["event"] == "ingestion_step" and event["step"] == "persist_tree"
        for event in progress_events
    )
    assert any(event["event"] == "ingestion_complete" for event in progress_events)


def test_ingest_docx_converts_to_markdown_before_pageindex(
    tmp_path: Path, monkeypatch
) -> None:
    """DOCX ingestion should convert to markdown before calling PageIndex."""
    master_tree_store = MasterTreeStore(str(tmp_path / "master_tree.json"))
    storage = DocumentStore(str(tmp_path / "data"))
    sample_tree = _load_fixture("sample_doc_tree.json")
    document_path = tmp_path / "auth_spec.docx"
    document_path.write_bytes(b"fake-docx")

    async def fake_run_pageindex(file_path: str, model: str, pageindex_opts: dict | None):
        """Assert that DOCX ingestion feeds derived markdown into PageIndex."""
        assert file_path.endswith("data/derived_markdown/sip_auth_spec_v2.md")
        assert Path(file_path).read_text(encoding="utf-8").startswith("# Auth")
        return sample_tree

    async def fake_generate_master_node(**kwargs):
        """Return a deterministic master node for the DOCX conversion test."""
        return MasterNode(
            doc_id="sip_auth_spec_v2",
            doc_title="SIP Auth Spec",
            doc_type="technical_spec",
            file_path=kwargs["file_path"],
            tree_path=kwargs["tree_path"],
            doc_summary="Auth document summary.",
            key_topics=["Authentication", "JWT"],
            relevance_hints=RelevanceHints(
                best_for="Auth questions.",
                not_useful_for="Analytics questions.",
                key_categories=["Auth"],
            ),
            top_sections=[
                TopSection(
                    title="Authentication Overview",
                    node_ref="sip_auth_spec_v2::0001",
                    section_summary="Overview section.",
                )
            ],
            related_docs=[],
            ingested_at="2026-03-17T00:00:00",
        )

    monkeypatch.setattr(ingest_module, "_run_pageindex", fake_run_pageindex)
    monkeypatch.setattr(
        ingest_module,
        "generate_master_node",
        fake_generate_master_node,
    )
    monkeypatch.setattr(
        ingest_module,
        "docx_to_markdown",
        lambda file_path: "# Auth\n\n## Token Refresh Flow\n\nDetails.\n",
    )

    result = asyncio.run(
        ingest_module.ingest_document_with_trace(
            file_path=str(document_path),
            doc_id="sip_auth_spec_v2",
            doc_title="SIP Auth Spec",
            doc_type="technical_spec",
            master_tree_store=master_tree_store,
            storage=storage,
            model="test-model",
            relationship_mode="off",  # preserve exact step sequence from before v1.2
        )
    )

    assert result.master_node.file_path == str(document_path.resolve())
    assert storage.load_doc_source_path("sip_auth_spec_v2").endswith(
        "data/derived_markdown/sip_auth_spec_v2.md"
    )
    assert [step.name for step in result.trace.steps] == [
        "validate_input",
        "convert_docx",
        "build_pageindex_tree",
        "persist_tree",
        "generate_master_node",
        "update_master_tree",
    ]


# ── Relationship-mode tests ────────────────────────────────────────────────────


def _make_fake_master_node(doc_id: str, related_docs: list[str], kwargs: dict) -> MasterNode:
    """Build a deterministic MasterNode for relationship reconciliation tests."""
    return MasterNode(
        doc_id=doc_id,
        doc_title=f"Title {doc_id}",
        doc_type="technical_spec",
        file_path=kwargs["file_path"],
        tree_path=kwargs["tree_path"],
        doc_summary="Summary.",
        key_topics=["Auth"],
        relevance_hints=RelevanceHints(
            best_for="Auth questions.",
            not_useful_for="Other.",
            key_categories=["Auth"],
        ),
        top_sections=[
            TopSection(
                title="Overview",
                node_ref=f"{doc_id}::0001",
                section_summary="Overview.",
            )
        ],
        related_docs=related_docs,
        ingested_at="2026-04-01T00:00:00",
    )


def test_ingest_with_mode_off_skips_reconcile_step(
    tmp_path: Path, monkeypatch
) -> None:
    """Ingestion with relationship_mode='off' must not add a reconcile step."""
    master_tree_store = MasterTreeStore(str(tmp_path / "master_tree.json"))
    storage = DocumentStore(str(tmp_path / "data"))
    sample_tree = _load_fixture("sample_doc_tree.json")
    document_path = tmp_path / "auth_spec.md"
    document_path.write_text("# Auth\n\nDetails.\n", encoding="utf-8")

    async def fake_run_pageindex(**kwargs):
        return sample_tree

    async def fake_generate_master_node(**kwargs):
        return _make_fake_master_node("doc_a", [], kwargs)

    monkeypatch.setattr(ingest_module, "_run_pageindex", fake_run_pageindex)
    monkeypatch.setattr(ingest_module, "generate_master_node", fake_generate_master_node)

    result = asyncio.run(
        ingest_module.ingest_document_with_trace(
            file_path=str(document_path),
            doc_id="doc_a",
            doc_title="Doc A",
            doc_type="technical_spec",
            master_tree_store=master_tree_store,
            storage=storage,
            relationship_mode="off",
        )
    )

    step_names = [s.name for s in result.trace.steps]
    assert "reconcile_relationships" not in step_names
    assert result.trace.relationship_mode == "off"
    assert result.trace.relationship_reconciliation is None


def test_ingest_with_mode_basic_adds_reconcile_step(
    tmp_path: Path, monkeypatch
) -> None:
    """Ingestion with relationship_mode='basic' must include a reconcile_relationships step."""
    master_tree_store = MasterTreeStore(str(tmp_path / "master_tree.json"))
    storage = DocumentStore(str(tmp_path / "data"))
    sample_tree = _load_fixture("sample_doc_tree.json")
    document_path = tmp_path / "auth_spec.md"
    document_path.write_text("# Auth\n\nDetails.\n", encoding="utf-8")

    async def fake_run_pageindex(**kwargs):
        return sample_tree

    async def fake_generate_master_node(**kwargs):
        return _make_fake_master_node("doc_a", [], kwargs)

    monkeypatch.setattr(ingest_module, "_run_pageindex", fake_run_pageindex)
    monkeypatch.setattr(ingest_module, "generate_master_node", fake_generate_master_node)

    result = asyncio.run(
        ingest_module.ingest_document_with_trace(
            file_path=str(document_path),
            doc_id="doc_a",
            doc_title="Doc A",
            doc_type="technical_spec",
            master_tree_store=master_tree_store,
            storage=storage,
            relationship_mode="basic",
        )
    )

    step_names = [s.name for s in result.trace.steps]
    assert "reconcile_relationships" in step_names
    assert result.trace.relationship_mode == "basic"
    assert result.trace.relationship_reconciliation is not None
    assert result.trace.relationship_reconciliation.ran is True
    assert result.trace.relationship_reconciliation.mode == "basic"


def test_ingest_basic_mode_enforces_symmetry_across_corpus(
    tmp_path: Path, monkeypatch
) -> None:
    """basic reconciliation should make links symmetric when a second doc is ingested."""
    master_tree_store = MasterTreeStore(str(tmp_path / "master_tree.json"))
    storage = DocumentStore(str(tmp_path / "data"))
    sample_tree = _load_fixture("sample_doc_tree.json")

    # Pre-seed doc_b into the master tree (it references doc_a which doesn't exist yet)
    from master_tree.schema import RoutingFacets
    pre_existing = MasterNode(
        doc_id="doc_b",
        doc_title="Doc B",
        doc_type="technical_spec",
        file_path="/tmp/doc_b.md",
        tree_path="/tmp/doc_b_tree.json",
        doc_summary="Summary B.",
        key_topics=["Auth"],
        relevance_hints=RelevanceHints(
            best_for="B questions.",
            not_useful_for="Other.",
            key_categories=["Auth"],
        ),
        top_sections=[
            TopSection(title="Overview B", node_ref="doc_b::0001", section_summary="Overview.")
        ],
        related_docs=["doc_a"],  # doc_a doesn't exist yet
        ingested_at="2026-04-01T00:00:00",
    )
    master_tree_store.add_node(pre_existing)
    master_tree_store.save()

    document_path = tmp_path / "doc_a.md"
    document_path.write_text("# Doc A\n\nDetails.\n", encoding="utf-8")

    async def fake_run_pageindex(**kwargs):
        return sample_tree

    async def fake_generate_master_node(**kwargs):
        # doc_a does NOT list doc_b in its related_docs (LLM omitted it)
        return _make_fake_master_node("doc_a", [], kwargs)

    monkeypatch.setattr(ingest_module, "_run_pageindex", fake_run_pageindex)
    monkeypatch.setattr(ingest_module, "generate_master_node", fake_generate_master_node)

    asyncio.run(
        ingest_module.ingest_document(
            file_path=str(document_path),
            doc_id="doc_a",
            doc_title="Doc A",
            doc_type="technical_spec",
            master_tree_store=master_tree_store,
            storage=storage,
            relationship_mode="basic",
        )
    )

    # After basic reconciliation: doc_a should reference doc_b (incoming symmetry)
    doc_a_node = master_tree_store.get_node("doc_a")
    assert doc_a_node is not None
    assert "doc_b" in doc_a_node.related_docs


def test_ingest_mode_basic_validate_input_step_records_mode(
    tmp_path: Path, monkeypatch
) -> None:
    """The validate_input trace step payload should include the relationship_mode."""
    master_tree_store = MasterTreeStore(str(tmp_path / "master_tree.json"))
    storage = DocumentStore(str(tmp_path / "data"))
    sample_tree = _load_fixture("sample_doc_tree.json")
    document_path = tmp_path / "auth_spec.md"
    document_path.write_text("# Auth\n\nDetails.\n", encoding="utf-8")

    async def fake_run_pageindex(**kwargs):
        return sample_tree

    async def fake_generate_master_node(**kwargs):
        return _make_fake_master_node("doc_a", [], kwargs)

    monkeypatch.setattr(ingest_module, "_run_pageindex", fake_run_pageindex)
    monkeypatch.setattr(ingest_module, "generate_master_node", fake_generate_master_node)

    result = asyncio.run(
        ingest_module.ingest_document_with_trace(
            file_path=str(document_path),
            doc_id="doc_a",
            doc_title="Doc A",
            doc_type="technical_spec",
            master_tree_store=master_tree_store,
            storage=storage,
            relationship_mode="enhanced",
        )
    )

    validate_step = next(s for s in result.trace.steps if s.name == "validate_input")
    assert validate_step.payload is not None
    assert validate_step.payload.get("relationship_mode") == "enhanced"


def test_ingest_enhanced_mode_falls_back_to_basic_on_llm_failure(
    tmp_path: Path, monkeypatch
) -> None:
    """Enhanced mode must fall back to basic reconciliation when the LLM fails."""
    master_tree_store = MasterTreeStore(str(tmp_path / "master_tree.json"))
    storage = DocumentStore(str(tmp_path / "data"))
    sample_tree = _load_fixture("sample_doc_tree.json")
    document_path = tmp_path / "auth_spec.md"
    document_path.write_text("# Auth\n\nDetails.\n", encoding="utf-8")

    from master_tree.schema import RoutingFacets

    # Pre-seed a neighbor with overlapping topics so the LLM path is triggered
    neighbor = MasterNode(
        doc_id="doc_neighbor",
        doc_title="Neighbor",
        doc_type="technical_spec",
        file_path="/tmp/doc_neighbor.md",
        tree_path="/tmp/doc_neighbor_tree.json",
        doc_summary="Neighbor summary.",
        key_topics=["Auth"],  # same as doc_a → overlap → LLM will be called
        relevance_hints=RelevanceHints(
            best_for="Neighbor questions.",
            not_useful_for="Other.",
            key_categories=["Auth"],
        ),
        top_sections=[
            TopSection(title="Overview", node_ref="doc_neighbor::0001", section_summary="Overview.")
        ],
        related_docs=[],
        ingested_at="2026-04-01T00:00:00",
    )
    master_tree_store.add_node(neighbor)
    master_tree_store.save()

    async def fake_run_pageindex(**kwargs):
        return sample_tree

    async def fake_generate_master_node(**kwargs):
        return _make_fake_master_node("doc_a", [], kwargs)

    async def fake_chat_completion(model, system_prompt, user_prompt):
        raise RuntimeError("LLM unavailable during test")

    monkeypatch.setattr(ingest_module, "_run_pageindex", fake_run_pageindex)
    monkeypatch.setattr(ingest_module, "generate_master_node", fake_generate_master_node)
    monkeypatch.setattr(rel_module, "_chat_completion", fake_chat_completion)

    result = asyncio.run(
        ingest_module.ingest_document_with_trace(
            file_path=str(document_path),
            doc_id="doc_a",
            doc_title="Doc A",
            doc_type="technical_spec",
            master_tree_store=master_tree_store,
            storage=storage,
            relationship_mode="enhanced",
        )
    )

    assert result.trace.relationship_mode == "enhanced"
    recon = result.trace.relationship_reconciliation
    assert recon is not None
    assert recon.mode == "enhanced"
    assert recon.fallback_used is True
    assert recon.enhanced_ran is False
