"""Tests that describe the retrieval pipeline's expected behavior."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import retrieval.navigator as navigator_module
import retrieval.pageindex_engine as pageindex_module
import retrieval.query_engine as query_engine_module
import retrieval.router as router_module
from retrieval import fetcher as fetcher_module
from master_tree.master_tree import MasterTreeStore
from master_tree.schema import MasterNode
from retrieval.fetcher import FetchResult, RetrievedChunk
from retrieval.pageindex_engine import PageIndexEngineResult
from storage.store import DocumentStore


FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _load_fixture(name: str) -> dict:
    """Load one JSON fixture used by retrieval tests."""
    return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))


def _seed_master_tree(store: MasterTreeStore) -> None:
    """Populate a store with the sample master-tree fixture."""
    fixture_tree = _load_fixture("sample_master_tree.json")
    for node_payload in fixture_tree["docs"]:
        store.add_node(MasterNode.model_validate(node_payload))
    store.save(store.tree)


def test_router_returns_only_known_doc_ids(tmp_path: Path, monkeypatch) -> None:
    """The router should filter out doc ids that are not in the master tree."""
    master_tree_store = MasterTreeStore(str(tmp_path / "master_tree.json"))
    _seed_master_tree(master_tree_store)

    async def fake_chat_completion(
        model: str,
        system_prompt: str,
        user_prompt: str,
        reasoning_effort: str | None = None,
    ) -> str:
        """Pretend the router LLM selected two known docs and one bad id."""
        assert "Master Tree" in system_prompt
        assert reasoning_effort is None
        return '["auth_doc", "missing_doc", "rbac_doc"]'

    monkeypatch.setattr(router_module, "_chat_completion", fake_chat_completion)

    routed_docs = asyncio.run(
        router_module.route_query(
            query="How does auth connect to RBAC?",
            master_tree_store=master_tree_store,
        )
    )

    assert routed_docs == ["auth_doc", "rbac_doc"]


def test_navigator_returns_compound_node_refs(tmp_path: Path, monkeypatch) -> None:
    """Navigator output should be normalized into compound `doc_id::node_id` refs."""
    per_doc_tree = _load_fixture("sample_doc_tree.json")

    async def fake_chat_completion(
        model: str,
        system_prompt: str,
        user_prompt: str,
        reasoning_effort: str | None = None,
    ) -> str:
        """Pretend the navigator selected one good node, one bad node, then another good node."""
        assert "What is the refresh flow?" in user_prompt
        assert reasoning_effort is None
        return '["0002", "9999", "0001"]'

    monkeypatch.setattr(navigator_module, "_chat_completion", fake_chat_completion)

    node_refs = asyncio.run(
        navigator_module.navigate_doc_tree(
            query="What is the refresh flow?",
            doc_id="auth_doc",
            per_doc_tree=per_doc_tree,
        )
    )

    assert node_refs == ["auth_doc::0002", "auth_doc::0001"]


def test_query_sequences_router_navigator_fetch_and_context(
    tmp_path: Path, monkeypatch
) -> None:
    """The query engine should consume caller context without owning history state."""
    master_tree_store = MasterTreeStore(str(tmp_path / "master_tree.json"))
    storage = DocumentStore(str(tmp_path / "data"))
    sample_tree = _load_fixture("sample_doc_tree.json")

    _seed_master_tree(master_tree_store)
    storage.save_doc_tree("auth_doc", sample_tree)
    storage.save_doc_tree("rbac_doc", sample_tree)

    routed_calls: list[str] = []
    navigated_docs: list[str] = []

    async def fake_route_query(**kwargs) -> list[str]:
        """Pretend the router selected two docs in a fixed order."""
        routed_calls.append(kwargs["query"])
        assert kwargs["reasoning_effort"] == "high"
        return ["auth_doc", "rbac_doc"]

    async def fake_navigate_doc_tree(**kwargs) -> list[str]:
        """Pretend each document contributes the same target node."""
        navigated_docs.append(kwargs["doc_id"])
        assert kwargs["reasoning_effort"] == "high"
        return [f"{kwargs['doc_id']}::0002"]

    async def fake_fetch_multiple_nodes_detailed(
        node_refs, storage, model_max_tokens=100000, expansion_refs=None
    ) -> FetchResult:
        """Return a deterministic fetch result so the query engine can be isolated."""
        assert node_refs == ["auth_doc::0002", "rbac_doc::0002"]
        return FetchResult(
            combined_text="retrieved context",
            chunks=[
                RetrievedChunk(
                    node_ref="auth_doc::0002",
                    doc_id="auth_doc",
                    node_id="0002",
                    title="Token Refresh Flow",
                    start_index=25,
                    end_index=80,
                    text="[auth_doc :: Token Refresh Flow :: pages 25-80]\n\nchunk one",
                    estimated_tokens=20,
                ),
                RetrievedChunk(
                    node_ref="rbac_doc::0002",
                    doc_id="rbac_doc",
                    node_id="0002",
                    title="Token Refresh Flow",
                    start_index=25,
                    end_index=80,
                    text="[rbac_doc :: Token Refresh Flow :: pages 25-80]\n\nchunk two",
                    estimated_tokens=20,
                ),
            ],
            truncated=False,
            token_budget=70000,
        )

    async def fake_answer_query(
        model: str,
        system_prompt: str,
        user_prompt: str,
        reasoning_effort: str | None = None,
    ) -> str:
        """Return a deterministic final answer for the orchestration test."""
        assert "retrieved context" in system_prompt
        assert "What is the token refresh flow?" in user_prompt
        assert reasoning_effort == "high"
        return "The refresh flow is documented in the auth spec."

    monkeypatch.setattr(query_engine_module, "route_query", fake_route_query)
    monkeypatch.setattr(query_engine_module, "navigate_doc_tree", fake_navigate_doc_tree)
    monkeypatch.setattr(
        query_engine_module,
        "fetch_multiple_nodes_detailed",
        fake_fetch_multiple_nodes_detailed,
    )
    monkeypatch.setattr(query_engine_module, "_answer_query", fake_answer_query)

    result = asyncio.run(
        query_engine_module.query(
            user_query="What is the token refresh flow?",
            master_tree_store=master_tree_store,
            storage=storage,
            conversation_context=[{"role": "user", "content": "Earlier question"}],
            reasoning_effort="high",
        )
    )

    assert routed_calls == ["What is the token refresh flow?"]
    assert navigated_docs == ["auth_doc", "rbac_doc"]
    assert result.selected_docs == ["auth_doc", "rbac_doc"]
    assert result.selected_nodes == ["auth_doc::0002", "rbac_doc::0002"]
    assert result.retrieved_context == "retrieved context"
    assert result.answer == "The refresh flow is documented in the auth spec."
    assert result.trace is not None
    assert result.metrics is not None
    assert result.metrics.ttft_seconds >= 0
    assert result.metrics.total_time_seconds >= 0
    assert not hasattr(result, "chat_history_updated")
    assert result.trace.navigation == {
        "auth_doc": ["auth_doc::0002"],
        "rbac_doc": ["rbac_doc::0002"],
    }
    assert [chunk.node_ref for chunk in result.trace.fetched_chunks] == [
        "auth_doc::0002",
        "rbac_doc::0002",
    ]


def test_fetch_node_content_supports_docx_via_markdown_conversion(
    tmp_path: Path, monkeypatch
) -> None:
    """DOCX fetches should work by converting Word content into markdown text."""
    docx_path = tmp_path / "auth_spec.docx"
    docx_path.write_bytes(b"fake-docx")
    tree = {
        "nodes": [
            {
                "node_id": "0001",
                "title": "Token Refresh Flow",
                "line_num": 1,
                "nodes": [],
            }
        ]
    }

    monkeypatch.setattr(
        fetcher_module,
        "docx_to_markdown",
        lambda file_path: "# Token Refresh Flow\n\nRefresh details.\n",
    )

    content = fetcher_module.fetch_node_content(
        "auth_doc::0001",
        tree,
        str(docx_path),
    )

    assert "Token Refresh Flow" in content
    assert "Refresh details." in content


# ── Retrieval mode plumbing ───────────────────────────────────────────────────


def _make_fake_pageindex_engine_result(**kwargs) -> PageIndexEngineResult:
    defaults = dict(
        tool_calls_made=3,
        tool_call_budget=12,
        content_tokens_used=1200,
        content_token_budget=70000,
        explored_docs=["auth_doc"],
        tool_budget_exhausted=False,
        content_budget_exhausted=False,
    )
    defaults.update(kwargs)
    return PageIndexEngineResult(**defaults)


def test_query_explicit_hybrid_mode_uses_hybrid_path(tmp_path: Path, monkeypatch) -> None:
    """Passing retrieval_mode='hybrid' should route through the hybrid pipeline."""
    master_tree_store = MasterTreeStore(str(tmp_path / "master_tree.json"))
    storage = DocumentStore(str(tmp_path / "data"))
    sample_tree = _load_fixture("sample_doc_tree.json")
    _seed_master_tree(master_tree_store)
    storage.save_doc_tree("auth_doc", sample_tree)

    pageindex_calls: list[str] = []

    async def fake_route_query(**kwargs) -> list[str]:
        return ["auth_doc"]

    async def fake_navigate_doc_tree(**kwargs) -> list[str]:
        return ["auth_doc::0001"]

    async def fake_fetch(node_refs, storage, model_max_tokens=100000, expansion_refs=None) -> FetchResult:
        return FetchResult(
            combined_text="ctx", chunks=[], truncated=False, token_budget=70000
        )

    async def fake_answer(model, system_prompt, user_prompt, reasoning_effort=None) -> str:
        return "Hybrid answer."

    async def fake_pageindex_retrieval(**kwargs):
        pageindex_calls.append("called")

    monkeypatch.setattr(query_engine_module, "route_query", fake_route_query)
    monkeypatch.setattr(query_engine_module, "navigate_doc_tree", fake_navigate_doc_tree)
    monkeypatch.setattr(query_engine_module, "fetch_multiple_nodes_detailed", fake_fetch)
    monkeypatch.setattr(query_engine_module, "_answer_query", fake_answer)
    monkeypatch.setattr(query_engine_module, "run_pageindex_retrieval", fake_pageindex_retrieval)

    result = asyncio.run(
        query_engine_module.query(
            user_query="Q?",
            master_tree_store=master_tree_store,
            storage=storage,
            retrieval_mode="hybrid",
        )
    )

    assert result.trace is not None
    assert result.trace.retrieval_mode == "hybrid"
    assert pageindex_calls == []


def test_query_explicit_pageindex_mode_uses_pageindex_path(tmp_path: Path, monkeypatch) -> None:
    """Passing retrieval_mode='pageindex' should route through the agentic pipeline."""
    master_tree_store = MasterTreeStore(str(tmp_path / "master_tree.json"))
    storage = DocumentStore(str(tmp_path / "data"))
    _seed_master_tree(master_tree_store)

    hybrid_calls: list[str] = []

    async def fake_route_query(**kwargs) -> list[str]:
        return ["auth_doc"]

    async def fake_navigate_doc_tree(**kwargs) -> list[str]:
        hybrid_calls.append("navigate")
        return []

    fake_er = _make_fake_pageindex_engine_result()

    async def fake_pageindex_retrieval(**kwargs):
        return "PageIndex answer.", ["auth_doc::0001"], "ctx", fake_er

    monkeypatch.setattr(query_engine_module, "route_query", fake_route_query)
    monkeypatch.setattr(query_engine_module, "navigate_doc_tree", fake_navigate_doc_tree)
    monkeypatch.setattr(query_engine_module, "run_pageindex_retrieval", fake_pageindex_retrieval)

    result = asyncio.run(
        query_engine_module.query(
            user_query="Q?",
            master_tree_store=master_tree_store,
            storage=storage,
            retrieval_mode="pageindex",
        )
    )

    assert result.answer == "PageIndex answer."
    assert result.trace is not None
    assert result.trace.retrieval_mode == "pageindex"
    assert hybrid_calls == []  # navigator was NOT called


def test_query_pageindex_trace_fields_populated(tmp_path: Path, monkeypatch) -> None:
    """PageIndex trace fields on QueryTrace should be populated from engine result."""
    master_tree_store = MasterTreeStore(str(tmp_path / "master_tree.json"))
    storage = DocumentStore(str(tmp_path / "data"))
    _seed_master_tree(master_tree_store)

    async def fake_route_query(**kwargs) -> list[str]:
        return ["auth_doc"]

    fake_er = _make_fake_pageindex_engine_result(
        tool_calls_made=5,
        tool_call_budget=12,
        content_tokens_used=4000,
        content_token_budget=70000,
        explored_docs=["auth_doc", "rbac_doc"],
        tool_budget_exhausted=False,
        content_budget_exhausted=True,
    )

    async def fake_pageindex_retrieval(**kwargs):
        return "Answer.", ["auth_doc::0001"], "ctx", fake_er

    monkeypatch.setattr(query_engine_module, "route_query", fake_route_query)
    monkeypatch.setattr(query_engine_module, "run_pageindex_retrieval", fake_pageindex_retrieval)

    result = asyncio.run(
        query_engine_module.query(
            user_query="Q?",
            master_tree_store=master_tree_store,
            storage=storage,
            retrieval_mode="pageindex",
        )
    )

    t = result.trace
    assert t is not None
    assert t.pageindex_tool_calls_made == 5
    assert t.pageindex_tool_call_budget == 12
    assert t.pageindex_content_tokens_used == 4000
    assert t.pageindex_content_token_budget == 70000
    assert t.pageindex_explored_docs == ["auth_doc", "rbac_doc"]
    assert t.pageindex_tool_budget_exhausted is False
    assert t.pageindex_content_budget_exhausted is True


def test_query_invalid_retrieval_mode_falls_back_to_env(tmp_path: Path, monkeypatch) -> None:
    """An invalid retrieval_mode value should fall back to the env-var default (hybrid)."""
    master_tree_store = MasterTreeStore(str(tmp_path / "master_tree.json"))
    storage = DocumentStore(str(tmp_path / "data"))
    sample_tree = _load_fixture("sample_doc_tree.json")
    _seed_master_tree(master_tree_store)
    storage.save_doc_tree("auth_doc", sample_tree)

    async def fake_route_query(**kwargs) -> list[str]:
        return ["auth_doc"]

    async def fake_navigate_doc_tree(**kwargs) -> list[str]:
        return ["auth_doc::0001"]

    async def fake_fetch(node_refs, storage, model_max_tokens=100000, expansion_refs=None) -> FetchResult:
        return FetchResult(combined_text="ctx", chunks=[], truncated=False, token_budget=70000)

    async def fake_answer(model, system_prompt, user_prompt, reasoning_effort=None) -> str:
        return "Hybrid answer."

    monkeypatch.setattr(query_engine_module, "route_query", fake_route_query)
    monkeypatch.setattr(query_engine_module, "navigate_doc_tree", fake_navigate_doc_tree)
    monkeypatch.setattr(query_engine_module, "fetch_multiple_nodes_detailed", fake_fetch)
    monkeypatch.setattr(query_engine_module, "_answer_query", fake_answer)
    monkeypatch.setenv("RETRIEVAL_MODE", "hybrid")

    result = asyncio.run(
        query_engine_module.query(
            user_query="Q?",
            master_tree_store=master_tree_store,
            storage=storage,
            retrieval_mode="not_a_mode",  # invalid → falls back to env → hybrid
        )
    )

    assert result.trace is not None
    assert result.trace.retrieval_mode == "hybrid"


def test_query_hybrid_mode_pageindex_trace_fields_are_zero(tmp_path: Path, monkeypatch) -> None:
    """Hybrid mode should leave all pageindex trace fields at their zero defaults."""
    master_tree_store = MasterTreeStore(str(tmp_path / "master_tree.json"))
    storage = DocumentStore(str(tmp_path / "data"))
    sample_tree = _load_fixture("sample_doc_tree.json")
    _seed_master_tree(master_tree_store)
    storage.save_doc_tree("auth_doc", sample_tree)

    async def fake_route_query(**kwargs) -> list[str]:
        return ["auth_doc"]

    async def fake_navigate_doc_tree(**kwargs) -> list[str]:
        return ["auth_doc::0001"]

    async def fake_fetch(node_refs, storage, model_max_tokens=100000, expansion_refs=None) -> FetchResult:
        return FetchResult(combined_text="ctx", chunks=[], truncated=False, token_budget=70000)

    async def fake_answer(model, system_prompt, user_prompt, reasoning_effort=None) -> str:
        return "Answer."

    monkeypatch.setattr(query_engine_module, "route_query", fake_route_query)
    monkeypatch.setattr(query_engine_module, "navigate_doc_tree", fake_navigate_doc_tree)
    monkeypatch.setattr(query_engine_module, "fetch_multiple_nodes_detailed", fake_fetch)
    monkeypatch.setattr(query_engine_module, "_answer_query", fake_answer)

    result = asyncio.run(
        query_engine_module.query(
            user_query="Q?",
            master_tree_store=master_tree_store,
            storage=storage,
            retrieval_mode="hybrid",
        )
    )

    t = result.trace
    assert t is not None
    assert t.pageindex_tool_calls_made == 0
    assert t.pageindex_explored_docs == []
    assert t.pageindex_tool_budget_exhausted is False
    assert t.pageindex_content_budget_exhausted is False
