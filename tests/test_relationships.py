"""Tests that document the expected behavior of related_docs relationship maintenance."""

from __future__ import annotations

import asyncio
from pathlib import Path

import master_tree.relationships as rel_module
from master_tree.relationships import (
    ReconciliationResult,
    _clean_related_docs,
    _compute_overlap_score,
    _find_incoming_links,
    _select_candidate_neighbors,
    reconcile_relationships_basic,
    reconcile_relationships_enhanced,
)
from master_tree.schema import MasterNode, RelevanceHints, RoutingFacets, TopSection


# ── Helpers ────────────────────────────────────────────────────────────────────


def _make_node(
    doc_id: str,
    *,
    key_topics: list[str] | None = None,
    key_categories: list[str] | None = None,
    related_docs: list[str] | None = None,
    workflows: list[str] | None = None,
    systems: list[str] | None = None,
    actors: list[str] | None = None,
) -> MasterNode:
    """Create a minimal MasterNode for relationship-reconciliation tests."""
    return MasterNode(
        doc_id=doc_id,
        doc_title=f"Title for {doc_id}",
        doc_type="technical_spec",
        file_path=f"/tmp/{doc_id}.md",
        tree_path=f"/tmp/{doc_id}_tree.json",
        doc_summary=f"Summary for {doc_id}.",
        key_topics=key_topics or [],
        relevance_hints=RelevanceHints(
            best_for=f"Questions about {doc_id}.",
            not_useful_for="Other questions.",
            key_categories=key_categories or [],
        ),
        routing_facets=RoutingFacets(
            workflows=workflows or [],
            systems=systems or [],
            actors=actors or [],
        ),
        top_sections=[
            TopSection(
                title=f"Section A of {doc_id}",
                node_ref=f"{doc_id}::0001",
                section_summary="A section.",
            )
        ],
        related_docs=related_docs or [],
        ingested_at="2026-04-01T00:00:00",
    )


def _ids(nodes: list[MasterNode]) -> set[str]:
    """Extract a set of doc_ids from a node list."""
    return {n.doc_id for n in nodes}


# ── _clean_related_docs ────────────────────────────────────────────────────────


def test_clean_related_docs_removes_self_reference() -> None:
    """Self-references must be stripped unconditionally."""
    result = _clean_related_docs("a", ["a", "b"], {"a", "b"})
    assert result == ["b"]


def test_clean_related_docs_removes_unknown_docs() -> None:
    """Doc IDs not present in the corpus must be stripped."""
    result = _clean_related_docs("a", ["b", "ghost_doc"], {"a", "b"})
    assert result == ["b"]


def test_clean_related_docs_deduplicates() -> None:
    """Duplicate doc IDs must be removed; first occurrence wins."""
    result = _clean_related_docs("a", ["b", "c", "b"], {"a", "b", "c"})
    assert result == ["b", "c"]


def test_clean_related_docs_enforces_bound() -> None:
    """List must be capped at max_related."""
    result = _clean_related_docs("a", ["b", "c", "d"], {"a", "b", "c", "d"}, max_related=2)
    assert result == ["b", "c"]


def test_clean_related_docs_empty_input() -> None:
    """Empty input should return an empty list without errors."""
    result = _clean_related_docs("a", [], {"a", "b"})
    assert result == []


# ── _find_incoming_links ───────────────────────────────────────────────────────


def test_find_incoming_links_identifies_references() -> None:
    """Should return doc_ids whose related_docs include the target."""
    a = _make_node("a", related_docs=["b"])
    b = _make_node("b", related_docs=[])
    c = _make_node("c", related_docs=["a", "b"])
    nodes_by_id = {"a": a, "b": b, "c": c}
    assert _find_incoming_links("a", nodes_by_id) == ["c"]


def test_find_incoming_links_excludes_self() -> None:
    """Self-reference should never appear in incoming links."""
    a = _make_node("a", related_docs=["a"])
    nodes_by_id = {"a": a}
    assert _find_incoming_links("a", nodes_by_id) == []


def test_find_incoming_links_empty_when_no_references() -> None:
    """Returns empty list when no other doc references the target."""
    a = _make_node("a")
    b = _make_node("b")
    nodes_by_id = {"a": a, "b": b}
    assert _find_incoming_links("a", nodes_by_id) == []


# ── _compute_overlap_score ─────────────────────────────────────────────────────


def test_compute_overlap_score_shared_topics_increase_score() -> None:
    """Each shared key_topic should contribute to the score."""
    new = _make_node("new", key_topics=["Auth", "JWT", "Session"])
    cand = _make_node("cand", key_topics=["Auth", "RBAC"])
    score = _compute_overlap_score(new, cand)
    assert score >= 1.0  # "Auth" overlaps


def test_compute_overlap_score_no_overlap_returns_zero() -> None:
    """Non-overlapping metadata should yield a score of 0."""
    new = _make_node("new", key_topics=["Auth"], key_categories=["Identity"])
    cand = _make_node("cand", key_topics=["Billing"], key_categories=["Payments"])
    assert _compute_overlap_score(new, cand) == 0.0


def test_compute_overlap_score_existing_link_adds_bonus() -> None:
    """A pre-existing link in either direction should add a bonus of 2.0."""
    new = _make_node("new", related_docs=["cand"], key_topics=[])
    cand = _make_node("cand", key_topics=[])
    score = _compute_overlap_score(new, cand)
    assert score == 2.0


def test_compute_overlap_score_routing_facets_contribute() -> None:
    """Overlapping routing facets should increase the score."""
    new = _make_node("new", key_topics=[], workflows=["user onboarding"], systems=["auth API"])
    cand = _make_node("cand", key_topics=[], workflows=["user onboarding"])
    score_with_facets = _compute_overlap_score(new, cand)
    assert score_with_facets > 0


# ── _select_candidate_neighbors ───────────────────────────────────────────────


def test_select_candidate_neighbors_returns_top_k() -> None:
    """Candidate shortlist must be capped at shortlist_size."""
    new = _make_node("new", key_topics=["X", "Y", "Z"])
    candidates = [
        _make_node(f"doc_{i}", key_topics=["X", "Y", "Z"]) for i in range(10)
    ]
    all_nodes = [new] + candidates
    result = _select_candidate_neighbors("new", new, all_nodes, shortlist_size=3)
    assert len(result) <= 3
    assert "new" not in result


def test_select_candidate_neighbors_excludes_self() -> None:
    """The new document itself must never appear in its own candidate list."""
    new = _make_node("new", key_topics=["Auth"])
    all_nodes = [new, _make_node("other", key_topics=["Auth"])]
    result = _select_candidate_neighbors("new", new, all_nodes)
    assert "new" not in result


def test_select_candidate_neighbors_excludes_zero_score() -> None:
    """Docs with no overlap at all must be excluded from the shortlist."""
    new = _make_node("new", key_topics=["Auth"])
    no_overlap = _make_node("unrelated", key_topics=["Billing"])
    result = _select_candidate_neighbors("new", new, [new, no_overlap], shortlist_size=5)
    assert "unrelated" not in result


# ── reconcile_relationships_basic ─────────────────────────────────────────────


def test_basic_no_neighbors_returns_clean_new_node() -> None:
    """With no related_docs and no incoming links, result is just the cleaned new node."""
    new = _make_node("new", related_docs=[])
    affected, result = reconcile_relationships_basic("new", [new])
    assert result.ran is True
    assert result.mode == "basic"
    assert _ids(affected) == {"new"}
    new_after = next(n for n in affected if n.doc_id == "new")
    assert new_after.related_docs == []


def test_basic_removes_self_link_from_new_node() -> None:
    """Self-references in the new node's related_docs must be removed."""
    new = _make_node("new", related_docs=["new"])
    affected, result = reconcile_relationships_basic("new", [new])
    new_after = next(n for n in affected if n.doc_id == "new")
    assert "new" not in new_after.related_docs


def test_basic_enforces_symmetry_outgoing() -> None:
    """If new doc lists B, then B should list new doc after reconciliation."""
    new = _make_node("new", related_docs=["b"])
    b = _make_node("b", related_docs=[])
    affected, result = reconcile_relationships_basic("new", [new, b])
    b_after = next(n for n in affected if n.doc_id == "b")
    assert "new" in b_after.related_docs


def test_basic_enforces_symmetry_incoming() -> None:
    """If C already references new doc, new doc should reference C after reconciliation."""
    new = _make_node("new", related_docs=[])
    c = _make_node("c", related_docs=["new"])
    affected, result = reconcile_relationships_basic("new", [new, c])
    new_after = next(n for n in affected if n.doc_id == "new")
    assert "c" in new_after.related_docs


def test_basic_symmetry_is_bidirectional() -> None:
    """Both outgoing (new→b) and incoming (c→new) symmetry must be enforced."""
    new = _make_node("new", related_docs=["b"])
    b = _make_node("b", related_docs=[])
    c = _make_node("c", related_docs=["new"])
    affected, result = reconcile_relationships_basic("new", [new, b, c])
    by_id = {n.doc_id: n for n in affected}
    assert "new" in by_id["b"].related_docs  # outgoing symmetry
    assert "c" in by_id["new"].related_docs  # incoming symmetry


def test_basic_deduplicates_related_docs() -> None:
    """Duplicate entries in related_docs must be removed after reconciliation."""
    new = _make_node("new", related_docs=["b", "b"])
    b = _make_node("b")
    affected, _ = reconcile_relationships_basic("new", [new, b])
    new_after = next(n for n in affected if n.doc_id == "new")
    assert new_after.related_docs.count("b") == 1


def test_basic_enforces_max_related_bound() -> None:
    """related_docs must not exceed RELATED_DOCS_MAX_PER_NODE after reconciliation."""
    from master_tree.relationships import RELATED_DOCS_MAX_PER_NODE
    # Create a new node that references more docs than the cap allows
    all_others = [_make_node(f"doc_{i}") for i in range(RELATED_DOCS_MAX_PER_NODE + 5)]
    new = _make_node("new", related_docs=[n.doc_id for n in all_others])
    all_nodes = [new] + all_others
    affected, _ = reconcile_relationships_basic("new", all_nodes)
    new_after = next(n for n in affected if n.doc_id == "new")
    assert len(new_after.related_docs) <= RELATED_DOCS_MAX_PER_NODE


def test_basic_returns_only_affected_neighborhood() -> None:
    """Nodes outside the local neighborhood should not be in the returned list."""
    new = _make_node("new", related_docs=["b"])
    b = _make_node("b")
    unrelated = _make_node("unrelated")
    affected, result = reconcile_relationships_basic("new", [new, b, unrelated])
    affected_ids = _ids(affected)
    assert "unrelated" not in affected_ids
    assert {"new", "b"} == affected_ids


def test_basic_records_before_and_after_in_trace() -> None:
    """ReconciliationResult must record before/after state for all affected docs."""
    new = _make_node("new", related_docs=["b"])
    b = _make_node("b", related_docs=[])
    affected, result = reconcile_relationships_basic("new", [new, b])
    assert "new" in result.before
    assert "b" in result.before
    assert "new" in result.after
    assert "b" in result.after
    # b initially had no link back; after reconciliation it should
    assert "new" in result.after["b"]
    assert "new" not in result.before["b"]


def test_basic_new_doc_not_found_returns_empty() -> None:
    """If the new doc_id is not present in nodes, return an empty list gracefully."""
    other = _make_node("other")
    affected, result = reconcile_relationships_basic("missing", [other])
    assert affected == []
    assert result.ran is False


def test_basic_does_not_mutate_input_nodes() -> None:
    """reconcile_relationships_basic must not mutate the input list."""
    new = _make_node("new", related_docs=["b"])
    b = _make_node("b", related_docs=[])
    original_b_related = list(b.related_docs)
    reconcile_relationships_basic("new", [new, b])
    # Input node should be unchanged
    assert b.related_docs == original_b_related


def test_basic_removes_unknown_related_docs() -> None:
    """References to doc_ids that don't exist in the corpus must be removed."""
    new = _make_node("new", related_docs=["ghost_doc", "b"])
    b = _make_node("b")
    affected, _ = reconcile_relationships_basic("new", [new, b])
    new_after = next(n for n in affected if n.doc_id == "new")
    assert "ghost_doc" not in new_after.related_docs
    assert "b" in new_after.related_docs


def test_basic_already_symmetric_links_unchanged() -> None:
    """If new↔b already has symmetric links, reconciliation should not duplicate them."""
    new = _make_node("new", related_docs=["b"])
    b = _make_node("b", related_docs=["new"])
    affected, result = reconcile_relationships_basic("new", [new, b])
    by_id = {n.doc_id: n for n in affected}
    assert by_id["new"].related_docs.count("b") == 1
    assert by_id["b"].related_docs.count("new") == 1


# ── reconcile_relationships_enhanced ──────────────────────────────────────────


def test_enhanced_uses_llm_to_select_related_docs(monkeypatch) -> None:
    """Enhanced mode should call the LLM and use its output for the new node's links."""
    new = _make_node("new", key_topics=["Auth"])
    b = _make_node("b", key_topics=["Auth"])
    c = _make_node("c", key_topics=["Billing"])

    async def fake_chat_completion(model: str, system_prompt: str, user_prompt: str) -> str:
        return '["b"]'

    monkeypatch.setattr(rel_module, "_chat_completion", fake_chat_completion)

    affected, result = asyncio.run(
        reconcile_relationships_enhanced("new", [new, b, c], model="test-model")
    )
    assert result.enhanced_ran is True
    assert result.fallback_used is False
    new_after = next(n for n in affected if n.doc_id == "new")
    assert "b" in new_after.related_docs
    # c was not selected by LLM and has no overlap via incoming links
    assert "c" not in new_after.related_docs


def test_enhanced_falls_back_to_basic_on_llm_failure(monkeypatch) -> None:
    """If the LLM call raises, enhanced mode must fall back to basic."""
    new = _make_node("new", related_docs=["b"])
    b = _make_node("b", related_docs=[])

    async def fake_chat_completion(model: str, system_prompt: str, user_prompt: str) -> str:
        raise RuntimeError("LLM unavailable")

    monkeypatch.setattr(rel_module, "_chat_completion", fake_chat_completion)

    affected, result = asyncio.run(
        reconcile_relationships_enhanced("new", [new, b], model="test-model")
    )
    assert result.mode == "enhanced"
    assert result.ran is True
    assert result.enhanced_ran is False
    assert result.fallback_used is True
    # Basic reconciliation should still have run and enforced symmetry
    b_after = next(n for n in affected if n.doc_id == "b")
    assert "new" in b_after.related_docs


def test_enhanced_falls_back_to_basic_on_bad_llm_response(monkeypatch) -> None:
    """A non-list LLM response should trigger the same basic fallback."""
    new = _make_node("new", related_docs=["b"])
    b = _make_node("b")

    async def fake_chat_completion(model: str, system_prompt: str, user_prompt: str) -> str:
        return '"this is a string not a list"'

    monkeypatch.setattr(rel_module, "_chat_completion", fake_chat_completion)

    affected, result = asyncio.run(
        reconcile_relationships_enhanced("new", [new, b], model="test-model")
    )
    assert result.fallback_used is True


def test_enhanced_candidate_shortlist_is_bounded(monkeypatch) -> None:
    """The candidate shortlist shown to the LLM must be bounded."""
    from master_tree.relationships import _ENHANCED_CANDIDATE_SHORTLIST_SIZE

    new = _make_node("new", key_topics=["Auth", "JWT"])
    others = [_make_node(f"doc_{i}", key_topics=["Auth", "JWT"]) for i in range(20)]
    all_nodes = [new] + others

    captured_candidates: list[list[str]] = []

    async def fake_chat_completion(model: str, system_prompt: str, user_prompt: str) -> str:
        # Extract the candidate shortlist from the prompt is complex; instead capture via
        # the select function directly (tested separately). Just return empty list.
        return "[]"

    monkeypatch.setattr(rel_module, "_chat_completion", fake_chat_completion)

    _, result = asyncio.run(
        reconcile_relationships_enhanced("new", all_nodes, model="test-model")
    )
    assert len(result.candidate_neighbors) <= _ENHANCED_CANDIDATE_SHORTLIST_SIZE


def test_enhanced_records_candidate_neighbors_in_trace(monkeypatch) -> None:
    """The trace must include the candidate shortlist for inspectability."""
    new = _make_node("new", key_topics=["Auth"])
    b = _make_node("b", key_topics=["Auth"])

    async def fake_chat_completion(model: str, system_prompt: str, user_prompt: str) -> str:
        return "[]"

    monkeypatch.setattr(rel_module, "_chat_completion", fake_chat_completion)

    _, result = asyncio.run(
        reconcile_relationships_enhanced("new", [new, b], model="test-model")
    )
    # b has overlapping topic "Auth", so it should appear in candidates
    assert "b" in result.candidate_neighbors


def test_enhanced_applies_basic_symmetry_after_llm(monkeypatch) -> None:
    """Even after the LLM selects links, basic reconciliation must enforce symmetry."""
    new = _make_node("new")
    b = _make_node("b")  # LLM will select b as related

    async def fake_chat_completion(model: str, system_prompt: str, user_prompt: str) -> str:
        return '["b"]'

    monkeypatch.setattr(rel_module, "_chat_completion", fake_chat_completion)

    affected, _ = asyncio.run(
        reconcile_relationships_enhanced("new", [new, b], model="test-model")
    )
    b_after = next((n for n in affected if n.doc_id == "b"), None)
    if b_after is not None:
        assert "new" in b_after.related_docs  # symmetry enforced


def test_enhanced_new_doc_not_found_returns_empty(monkeypatch) -> None:
    """If the new doc_id is not in nodes, return gracefully without LLM calls."""
    other = _make_node("other")
    called = []

    async def fake_chat_completion(model: str, system_prompt: str, user_prompt: str) -> str:
        called.append(True)
        return "[]"

    monkeypatch.setattr(rel_module, "_chat_completion", fake_chat_completion)

    affected, result = asyncio.run(
        reconcile_relationships_enhanced("missing", [other], model="test-model")
    )
    assert affected == []
    assert result.ran is False
    assert called == []  # no LLM call when doc is missing


# ── ReconciliationResult.to_dict ──────────────────────────────────────────────


def test_reconciliation_result_to_dict_is_serializable() -> None:
    """to_dict() must produce a JSON-serializable plain dict."""
    import json

    r = ReconciliationResult(
        mode="basic",
        ran=True,
        affected_doc_ids=["a", "b"],
        before={"a": [], "b": ["a"]},
        after={"a": ["b"], "b": ["a"]},
    )
    d = r.to_dict()
    # Should not raise
    json.dumps(d)
    assert d["mode"] == "basic"
    assert d["ran"] is True
    assert d["affected_doc_ids"] == ["a", "b"]


# ── Mode constant ─────────────────────────────────────────────────────────────


def test_relationship_modes_constant_contains_expected_values() -> None:
    """RELATIONSHIP_MODES frozenset should contain exactly the three valid modes."""
    from master_tree.relationships import RELATIONSHIP_MODES
    assert RELATIONSHIP_MODES == frozenset({"off", "basic", "enhanced"})


# ── Config helper ─────────────────────────────────────────────────────────────


def test_get_related_docs_mode_defaults_to_basic(monkeypatch) -> None:
    """The default mode should be 'basic' when the env var is not set."""
    monkeypatch.delenv("RELATED_DOCS_MODE", raising=False)
    from utils import get_related_docs_mode
    assert get_related_docs_mode() == "basic"


def test_get_related_docs_mode_reads_env_var(monkeypatch) -> None:
    """RELATED_DOCS_MODE env var should control the returned mode."""
    monkeypatch.setenv("RELATED_DOCS_MODE", "enhanced")
    from importlib import reload
    import utils as utils_module
    # Call directly rather than reloading to avoid side-effects
    from utils import get_related_docs_mode
    # We patch the env var and call again
    import os
    original = os.environ.get("RELATED_DOCS_MODE")
    os.environ["RELATED_DOCS_MODE"] = "enhanced"
    try:
        assert get_related_docs_mode() == "enhanced"
    finally:
        if original is None:
            os.environ.pop("RELATED_DOCS_MODE", None)
        else:
            os.environ["RELATED_DOCS_MODE"] = original


def test_get_related_docs_mode_rejects_invalid_value(monkeypatch) -> None:
    """An invalid env var value should fall back to the default 'basic'."""
    monkeypatch.setenv("RELATED_DOCS_MODE", "bogus_mode")
    from utils import get_related_docs_mode
    assert get_related_docs_mode() == "basic"
