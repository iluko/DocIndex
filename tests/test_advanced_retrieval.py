"""Tests for the advanced retrieval features added in v1.1.

Covers:
- RoutingFacets schema backward compatibility
- Richer master-node generation normalization (_normalize_routing_facets)
- Planner output parsing and graceful fallback
- Navigator max_nodes parameter
- Node expansion logic (collect_expansion_node_refs)
- query() trace metadata for advanced retrieval
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import retrieval.planner as planner_module
from ingestion.master_node_gen import _normalize_routing_facets
from master_tree.schema import MasterNode, MasterTree, RoutingFacets
from master_tree.master_tree import MasterTreeStore
from retrieval.fetcher import (
    RetrievedChunk,
    collect_expansion_node_refs,
    _find_first_child_node_id,
    _find_sibling_node_ids,
    _find_parent_node_id,
)
from retrieval.planner import QueryPlan, _parse_plan, plan_query
import retrieval.query_engine as qe_module
from retrieval.query_engine import AdvancedRetrievalConfig, QueryTrace


FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))


# ── 1. Schema backward compatibility ─────────────────────────────────────────


def test_master_node_loads_without_routing_facets() -> None:
    """Old master-tree JSON without routing_facets should load with routing_facets=None."""
    payload = {
        "doc_id": "legacy_doc",
        "doc_title": "Legacy",
        "doc_type": "spec",
        "file_path": "/tmp/f.md",
        "tree_path": "/tmp/f_tree.json",
        "doc_summary": "A legacy document.",
        "key_topics": ["topic"],
        "relevance_hints": {
            "best_for": "anything",
            "not_useful_for": "nothing",
            "key_categories": [],
        },
        "top_sections": [],
        "related_docs": [],
        "ingested_at": "2025-01-01T00:00:00",
    }
    node = MasterNode.model_validate(payload)
    assert node.routing_facets is None


def test_master_node_loads_with_routing_facets() -> None:
    """New master-tree JSON with routing_facets should deserialize correctly."""
    payload = {
        "doc_id": "new_doc",
        "doc_title": "New",
        "doc_type": "spec",
        "file_path": "/tmp/f.md",
        "tree_path": "/tmp/f_tree.json",
        "doc_summary": "A new document.",
        "key_topics": ["topic"],
        "relevance_hints": {
            "best_for": "anything",
            "not_useful_for": "nothing",
            "key_categories": [],
        },
        "top_sections": [],
        "related_docs": [],
        "ingested_at": "2025-01-01T00:00:00",
        "routing_facets": {
            "workflows": ["user onboarding"],
            "actors": ["admin"],
            "systems": ["auth service"],
            "edge_cases": ["token expiry"],
            "authority_hints": ["v2 only"],
        },
    }
    node = MasterNode.model_validate(payload)
    assert node.routing_facets is not None
    assert node.routing_facets.workflows == ["user onboarding"]
    assert node.routing_facets.actors == ["admin"]
    assert node.routing_facets.edge_cases == ["token expiry"]


def test_master_tree_roundtrip_with_routing_facets(tmp_path: Path) -> None:
    """Save and reload a master tree node that includes routing_facets."""
    store_path = tmp_path / "master_tree.json"
    store = MasterTreeStore(str(store_path))

    node = MasterNode(
        doc_id="test_doc",
        doc_title="Test",
        doc_type="spec",
        file_path="/tmp/f.md",
        tree_path="/tmp/f_tree.json",
        doc_summary="Test doc.",
        key_topics=["a"],
        relevance_hints={"best_for": "x", "not_useful_for": "y", "key_categories": []},
        top_sections=[],
        related_docs=[],
        ingested_at="2025-01-01T00:00:00",
        routing_facets=RoutingFacets(
            workflows=["onboarding"],
            systems=["billing-api"],
        ),
    )
    store.add_node(node)
    store.save()

    reloaded = MasterTreeStore(str(store_path))
    loaded_node = reloaded.get_node("test_doc")
    assert loaded_node is not None
    assert loaded_node.routing_facets is not None
    assert loaded_node.routing_facets.workflows == ["onboarding"]
    assert loaded_node.routing_facets.systems == ["billing-api"]
    assert loaded_node.routing_facets.actors == []  # defaulted


def test_to_llm_context_includes_routing_facets_when_present(tmp_path: Path) -> None:
    """to_llm_context() should include non-empty routing facets in the serialization."""
    store = MasterTreeStore(str(tmp_path / "mt.json"))
    node = MasterNode(
        doc_id="d1",
        doc_title="D1",
        doc_type="spec",
        file_path="/tmp/f.md",
        tree_path="/tmp/f_tree.json",
        doc_summary="A doc.",
        key_topics=["x"],
        relevance_hints={"best_for": "q", "not_useful_for": "n", "key_categories": []},
        top_sections=[],
        related_docs=[],
        ingested_at="2025-01-01T00:00:00",
        routing_facets=RoutingFacets(workflows=["flow_a"], edge_cases=["error_b"]),
    )
    store.add_node(node)
    context = store.to_llm_context()
    parsed = json.loads(context)
    doc_entry = parsed["docs"][0]
    assert "routing_facets" in doc_entry
    assert doc_entry["routing_facets"]["workflows"] == ["flow_a"]
    assert doc_entry["routing_facets"]["edge_cases"] == ["error_b"]
    assert "actors" not in doc_entry["routing_facets"]  # empty, should be omitted


def test_to_llm_context_omits_routing_facets_when_none(tmp_path: Path) -> None:
    """to_llm_context() should omit routing_facets for old nodes (routing_facets=None)."""
    store = MasterTreeStore(str(tmp_path / "mt.json"))
    node = MasterNode(
        doc_id="d2",
        doc_title="D2",
        doc_type="spec",
        file_path="/tmp/f.md",
        tree_path="/tmp/f_tree.json",
        doc_summary="Legacy.",
        key_topics=[],
        relevance_hints={"best_for": "q", "not_useful_for": "n", "key_categories": []},
        top_sections=[],
        related_docs=[],
        ingested_at="2025-01-01T00:00:00",
        routing_facets=None,
    )
    store.add_node(node)
    context = store.to_llm_context()
    parsed = json.loads(context)
    assert "routing_facets" not in parsed["docs"][0]


# ── 2. _normalize_routing_facets ─────────────────────────────────────────────


def test_normalize_routing_facets_full() -> None:
    """All routing facet fields should be extracted from a well-formed payload."""
    payload = {
        "routing_facets": {
            "workflows": ["onboarding", "token refresh"],
            "actors": ["admin", "user"],
            "systems": ["auth-service"],
            "edge_cases": ["expired token"],
            "authority_hints": ["v2 only"],
        }
    }
    facets = _normalize_routing_facets(payload)
    assert facets.workflows == ["onboarding", "token refresh"]
    assert facets.actors == ["admin", "user"]
    assert facets.edge_cases == ["expired token"]


def test_normalize_routing_facets_missing_field() -> None:
    """Missing routing_facets key should return empty RoutingFacets, not raise."""
    facets = _normalize_routing_facets({})
    assert facets.workflows == []
    assert facets.actors == []


def test_normalize_routing_facets_invalid_type() -> None:
    """routing_facets being a non-dict (e.g. a string) should return empty RoutingFacets."""
    facets = _normalize_routing_facets({"routing_facets": "bad"})
    assert facets.workflows == []


def test_normalize_routing_facets_strips_whitespace() -> None:
    """Leading/trailing whitespace in list items should be stripped."""
    payload = {"routing_facets": {"workflows": ["  flow a  ", "  flow b"]}}
    facets = _normalize_routing_facets(payload)
    assert facets.workflows == ["flow a", "flow b"]


def test_normalize_routing_facets_skips_empty_strings() -> None:
    """Empty strings in lists should be filtered out."""
    payload = {"routing_facets": {"actors": ["admin", "", "  "]}}
    facets = _normalize_routing_facets(payload)
    assert facets.actors == ["admin"]


# ── 3. Planner ────────────────────────────────────────────────────────────────


def test_parse_plan_valid_json() -> None:
    """Valid planner JSON should parse into a QueryPlan with correct fields."""
    raw = json.dumps({
        "query_type": "workflow_or_process",
        "is_broad": True,
        "recommended_max_docs": 3,
        "recommended_max_nodes": 4,
        "use_node_expansion": True,
    })
    plan = _parse_plan(raw, hard_max_docs=6, hard_max_nodes=6)
    assert plan.query_type == "workflow_or_process"
    assert plan.is_broad is True
    assert plan.recommended_max_docs == 3
    assert plan.recommended_max_nodes == 4
    assert plan.use_node_expansion is True


def test_parse_plan_clamps_to_hard_max() -> None:
    """recommended_max_docs / nodes must be clamped to their hard caps."""
    raw = json.dumps({
        "query_type": "compare",
        "is_broad": False,
        "recommended_max_docs": 99,
        "recommended_max_nodes": 99,
        "use_node_expansion": False,
    })
    plan = _parse_plan(raw, hard_max_docs=4, hard_max_nodes=5)
    assert plan.recommended_max_docs == 4
    assert plan.recommended_max_nodes == 5


def test_parse_plan_unknown_query_type_defaults_to_fact_lookup() -> None:
    """An unrecognised query_type should fall back to 'fact_lookup'."""
    raw = json.dumps({
        "query_type": "something_made_up",
        "is_broad": False,
        "recommended_max_docs": 1,
        "recommended_max_nodes": 1,
        "use_node_expansion": False,
    })
    plan = _parse_plan(raw, hard_max_docs=6, hard_max_nodes=6)
    assert plan.query_type == "fact_lookup"


def test_plan_query_returns_none_on_failure(monkeypatch) -> None:
    """plan_query must return None on any LLM/network failure, not raise."""
    async def _fail(**kwargs):
        raise RuntimeError("Network error")

    monkeypatch.setattr(planner_module, "get_async_client", lambda: SimpleNamespace())
    monkeypatch.setattr(planner_module, "create_chat_completion_async", _fail)
    monkeypatch.setattr(planner_module, "get_default_model", lambda: "test-model")

    result = asyncio.run(plan_query("What is authentication?"))
    assert result is None


def test_plan_query_returns_plan_on_success(monkeypatch) -> None:
    """plan_query should return a valid QueryPlan when the LLM responds correctly."""
    raw = json.dumps({
        "query_type": "fact_lookup",
        "is_broad": False,
        "recommended_max_docs": 1,
        "recommended_max_nodes": 2,
        "use_node_expansion": False,
    })

    async def _fake_create(**kwargs):
        return SimpleNamespace(
            choices=[SimpleNamespace(
                message=SimpleNamespace(content=raw, tool_calls=[]),
                finish_reason="stop",
            )]
        )

    monkeypatch.setattr(planner_module, "get_async_client", lambda: SimpleNamespace())
    monkeypatch.setattr(planner_module, "create_chat_completion_async", _fake_create)
    monkeypatch.setattr(planner_module, "get_default_model", lambda: "test-model")

    plan = asyncio.run(plan_query("What is auth?"))
    assert plan is not None
    assert plan.query_type == "fact_lookup"
    assert plan.recommended_max_docs == 1


# ── 4. Navigator max_nodes ────────────────────────────────────────────────────


def test_navigator_respects_max_nodes(monkeypatch) -> None:
    """navigate_doc_tree should never return more nodes than max_nodes."""
    import retrieval.navigator as nav_module

    tree = {
        "nodes": [
            {"node_id": "0001", "title": "A"},
            {"node_id": "0002", "title": "B"},
            {"node_id": "0003", "title": "C"},
            {"node_id": "0004", "title": "D"},
            {"node_id": "0005", "title": "E"},
        ]
    }

    async def _fake_chat(**kwargs):
        return SimpleNamespace(
            choices=[SimpleNamespace(
                message=SimpleNamespace(content='["0001","0002","0003","0004","0005"]', tool_calls=[]),
                finish_reason="stop",
            )]
        )

    monkeypatch.setattr(nav_module, "get_async_client", lambda: SimpleNamespace())
    monkeypatch.setattr(nav_module, "create_chat_completion_async", _fake_chat)
    monkeypatch.setattr(nav_module, "get_default_model", lambda: "test-model")

    from retrieval.navigator import navigate_doc_tree

    refs = asyncio.run(
        navigate_doc_tree(
            query="What is X?",
            doc_id="doc1",
            per_doc_tree=tree,
            max_nodes=2,
        )
    )
    assert len(refs) <= 2


def test_navigator_standard_max_nodes_is_three(monkeypatch) -> None:
    """Default (no max_nodes arg) should limit to 3 nodes."""
    import retrieval.navigator as nav_module

    tree = {
        "nodes": [
            {"node_id": str(i).zfill(4), "title": f"Section {i}"}
            for i in range(1, 8)
        ]
    }
    all_ids = [str(i).zfill(4) for i in range(1, 8)]

    async def _fake_chat(**kwargs):
        return SimpleNamespace(
            choices=[SimpleNamespace(
                message=SimpleNamespace(
                    content=json.dumps(all_ids),
                    tool_calls=[],
                ),
                finish_reason="stop",
            )]
        )

    monkeypatch.setattr(nav_module, "get_async_client", lambda: SimpleNamespace())
    monkeypatch.setattr(nav_module, "create_chat_completion_async", _fake_chat)
    monkeypatch.setattr(nav_module, "get_default_model", lambda: "test-model")

    from retrieval.navigator import navigate_doc_tree

    refs = asyncio.run(
        navigate_doc_tree(query="X?", doc_id="doc1", per_doc_tree=tree)
    )
    assert len(refs) <= 3


# ── 5. Node expansion tree helpers ───────────────────────────────────────────


def _make_tree() -> dict:
    """Build a small multi-level tree for testing."""
    return {
        "nodes": [
            {
                "node_id": "0001",
                "title": "Chapter 1",
                "nodes": [
                    {"node_id": "0002", "title": "Section 1.1", "nodes": []},
                    {"node_id": "0003", "title": "Section 1.2", "nodes": [
                        {"node_id": "0004", "title": "Subsection 1.2.1", "nodes": []},
                    ]},
                ],
            },
            {
                "node_id": "0005",
                "title": "Chapter 2",
                "nodes": [],
            },
        ]
    }


def test_find_sibling_node_ids_for_child() -> None:
    """A child node should see its sibling IDs (not itself)."""
    tree = _make_tree()
    siblings = _find_sibling_node_ids(tree, "0002")
    assert "0003" in siblings
    assert "0002" not in siblings


def test_find_sibling_node_ids_for_root() -> None:
    """Root-level nodes should see other root siblings."""
    tree = _make_tree()
    siblings = _find_sibling_node_ids(tree, "0001")
    assert "0005" in siblings
    assert "0001" not in siblings


def test_find_first_child_node_id() -> None:
    """First child should be returned for a node with children."""
    tree = _make_tree()
    child = _find_first_child_node_id(tree, "0001")
    assert child == "0002"


def test_find_first_child_node_id_leaf() -> None:
    """Leaf nodes with no children should return None."""
    tree = _make_tree()
    assert _find_first_child_node_id(tree, "0002") is None


def test_find_parent_node_id() -> None:
    """A child node should have its parent id returned."""
    tree = _make_tree()
    parent = _find_parent_node_id(tree, "0002")
    assert parent == "0001"


def test_find_parent_node_id_root() -> None:
    """Root-level nodes should return None as parent."""
    tree = _make_tree()
    assert _find_parent_node_id(tree, "0001") is None


def test_collect_expansion_no_duplicates_with_primary() -> None:
    """Expansion should never include any node already in the primary list."""
    tree = _make_tree()
    per_doc_trees = {"doc1": tree}
    primary = ["doc1::0001", "doc1::0005"]
    expansion = collect_expansion_node_refs(primary, per_doc_trees)
    primary_set = set(primary)
    for ref in expansion:
        assert ref not in primary_set, f"Expansion ref {ref!r} was in primary list"


def test_collect_expansion_includes_child_of_primary() -> None:
    """Expansion should include the first child of a primary node."""
    tree = _make_tree()
    per_doc_trees = {"doc1": tree}
    primary = ["doc1::0001"]
    expansion = collect_expansion_node_refs(primary, per_doc_trees)
    assert "doc1::0002" in expansion  # first child of 0001


def test_collect_expansion_empty_primary_returns_empty() -> None:
    """With no primary nodes, expansion should return empty."""
    assert collect_expansion_node_refs([], {}) == []


def test_fetch_multiple_nodes_marks_expansion_chunks(tmp_path: Path, monkeypatch) -> None:
    """Chunks fetched via expansion_refs should have is_expansion=True."""
    import retrieval.fetcher as fetcher_module
    from retrieval.fetcher import fetch_multiple_nodes_detailed

    tree = {"nodes": [{"node_id": "0001", "title": "S1"}, {"node_id": "0002", "title": "S2"}]}
    primary_chunk = RetrievedChunk(
        node_ref="doc1::0001", doc_id="doc1", node_id="0001",
        title="S1", start_index=1, end_index=5, text="Primary text",
        estimated_tokens=10, is_expansion=False,
    )
    expansion_chunk = RetrievedChunk(
        node_ref="doc1::0002", doc_id="doc1", node_id="0002",
        title="S2", start_index=6, end_index=10, text="Expansion text",
        estimated_tokens=10, is_expansion=True,
    )

    call_count = [0]
    def _build_chunk(node_ref, tree, file_path, is_expansion=False):
        call_count[0] += 1
        if node_ref == "doc1::0001":
            return primary_chunk
        return expansion_chunk

    monkeypatch.setattr(fetcher_module, "_build_retrieved_chunk", _build_chunk)

    storage_stub = SimpleNamespace(
        load_doc_tree=lambda doc_id: tree,
        load_doc_source_path=lambda doc_id: "/fake/path.md",
    )

    result = asyncio.run(
        fetch_multiple_nodes_detailed(
            node_refs=["doc1::0001"],
            storage=storage_stub,
            expansion_refs=["doc1::0002"],
        )
    )

    primary_chunks = [c for c in result.chunks if not c.is_expansion]
    expanded_chunks = [c for c in result.chunks if c.is_expansion]
    assert len(primary_chunks) == 1
    assert len(expanded_chunks) == 1
    assert expanded_chunks[0].node_ref == "doc1::0002"


# ── 6. AdvancedRetrievalConfig ────────────────────────────────────────────────


def test_advanced_retrieval_config_defaults() -> None:
    """Default AdvancedRetrievalConfig should have enabled=False."""
    config = AdvancedRetrievalConfig()
    assert config.enabled is False
    assert config.enable_planning is True
    assert config.max_docs_cap == 6


def test_advanced_retrieval_config_from_env_disabled(monkeypatch) -> None:
    """AdvancedRetrievalConfig.from_env() should reflect env var defaults."""
    monkeypatch.setenv("ADVANCED_RETRIEVAL", "false")
    config = AdvancedRetrievalConfig.from_env()
    assert config.enabled is False


def test_advanced_retrieval_config_from_env_enabled(monkeypatch) -> None:
    """ADVANCED_RETRIEVAL=true env var should produce enabled=True config."""
    monkeypatch.setenv("ADVANCED_RETRIEVAL", "true")
    config = AdvancedRetrievalConfig.from_env()
    assert config.enabled is True


# ── 7. QueryTrace includes advanced retrieval metadata ────────────────────────


def test_query_trace_has_advanced_fields() -> None:
    """QueryTrace should carry all advanced retrieval metadata fields."""
    plan = QueryPlan(
        query_type="workflow_or_process",
        is_broad=True,
        recommended_max_docs=4,
        recommended_max_nodes=4,
        use_node_expansion=True,
    )
    trace = QueryTrace(
        routed_docs=["doc1"],
        navigation={"doc1": ["doc1::0001"]},
        fetched_chunks=[],
        token_budget=70000,
        truncated=False,
        advanced_retrieval_enabled=True,
        planner_output=plan,
        effective_max_docs=4,
        effective_max_nodes=4,
        node_expansion_applied=True,
        primary_node_refs=["doc1::0001"],
        expanded_node_refs=["doc1::0002"],
    )
    assert trace.advanced_retrieval_enabled is True
    assert trace.planner_output is plan
    assert trace.effective_max_docs == 4
    assert trace.node_expansion_applied is True
    assert trace.expanded_node_refs == ["doc1::0002"]


def test_query_trace_defaults_are_off() -> None:
    """A vanilla QueryTrace (no advanced kwargs) should have advanced features off."""
    trace = QueryTrace(
        routed_docs=[],
        navigation={},
        fetched_chunks=[],
        token_budget=0,
        truncated=False,
    )
    assert trace.advanced_retrieval_enabled is False
    assert trace.planner_output is None
    assert trace.node_expansion_applied is False
    assert trace.expanded_node_refs == []
