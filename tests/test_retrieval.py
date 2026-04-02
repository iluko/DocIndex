"""Tests that describe the retrieval pipeline's expected behavior."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import retrieval.navigator as navigator_module
import retrieval.query_engine as query_engine_module
import retrieval.router as router_module
from retrieval import fetcher as fetcher_module
from arch_map.arch_map import ArchitectureMap
from master_tree.master_tree import MasterTreeStore
from master_tree.schema import MasterNode
from retrieval.fetcher import FetchResult, RetrievedChunk
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
    arch_map = ArchitectureMap(str(tmp_path / "missing_arch_map.json"))

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
            arch_map=arch_map,
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
    arch_map_path = tmp_path / "arch_map.json"
    arch_map_path.write_text(
        json.dumps(
            {
                "description": "SIP modules",
                "modules": [{"name": "Auth", "connects_to": ["RBAC"], "description": "Auth module"}],
            }
        ),
        encoding="utf-8",
    )
    arch_map = ArchitectureMap(str(arch_map_path))
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
        node_refs, storage, model_max_tokens=100000
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
            arch_map=arch_map,
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
