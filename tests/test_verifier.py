"""Tests for the post-navigation verification pass."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import retrieval.verifier as verifier_module
from retrieval.verifier import verify_navigation, verify_navigation_batch


# ── Helpers ───────────────────────────────────────────────────────────────────

_SAMPLE_TREE = {
    "nodes": [
        {"node_id": "0001", "title": "Auth Overview", "summary": "Covers auth flow."},
        {"node_id": "0002", "title": "Token Refresh", "summary": "Details token refresh."},
        {"node_id": "0003", "title": "Logout", "summary": "Session termination."},
    ]
}

_DOC_ID = "auth_doc"


def _fake_llm_response(content: str) -> dict:
    """Build a minimal dict-format LLM response carrying *content*."""
    return {
        "choices": [
            {"message": {"content": content}, "finish_reason": "stop"}
        ]
    }


def _patch_llm(monkeypatch, response_content: str) -> None:
    """Make create_chat_completion_async return a fixed text response."""
    async def fake_create(**kwargs):
        return _fake_llm_response(response_content)

    monkeypatch.setattr(verifier_module, "get_async_client", lambda: SimpleNamespace())
    monkeypatch.setattr(verifier_module, "create_chat_completion_async", fake_create)


# ── verify_navigation ─────────────────────────────────────────────────────────


def test_verify_navigation_empty_input_returns_empty() -> None:
    """An empty node list should come back empty without touching the LLM."""
    result = asyncio.run(
        verify_navigation(query="anything", doc_id=_DOC_ID, node_refs=[], per_doc_tree=_SAMPLE_TREE)
    )
    assert result == []


def test_verify_navigation_keeps_approved_nodes(monkeypatch) -> None:
    """Nodes marked true in the verdict map should be kept."""
    verdict = json.dumps({"0001": True, "0002": True})
    _patch_llm(monkeypatch, verdict)

    result = asyncio.run(
        verify_navigation(
            query="How does auth work?",
            doc_id=_DOC_ID,
            node_refs=[f"{_DOC_ID}::0001", f"{_DOC_ID}::0002"],
            per_doc_tree=_SAMPLE_TREE,
        )
    )

    assert result == [f"{_DOC_ID}::0001", f"{_DOC_ID}::0002"]


def test_verify_navigation_drops_false_nodes(monkeypatch) -> None:
    """Nodes marked false in the verdict map should be removed."""
    verdict = json.dumps({"0001": True, "0002": False, "0003": False})
    _patch_llm(monkeypatch, verdict)

    result = asyncio.run(
        verify_navigation(
            query="How does auth work?",
            doc_id=_DOC_ID,
            node_refs=[
                f"{_DOC_ID}::0001",
                f"{_DOC_ID}::0002",
                f"{_DOC_ID}::0003",
            ],
            per_doc_tree=_SAMPLE_TREE,
        )
    )

    assert result == [f"{_DOC_ID}::0001"]


def test_verify_navigation_safety_net_keeps_first_when_all_rejected(monkeypatch) -> None:
    """If every node is rejected the first original pick must be returned."""
    verdict = json.dumps({"0001": False, "0002": False})
    _patch_llm(monkeypatch, verdict)

    result = asyncio.run(
        verify_navigation(
            query="How does auth work?",
            doc_id=_DOC_ID,
            node_refs=[f"{_DOC_ID}::0001", f"{_DOC_ID}::0002"],
            per_doc_tree=_SAMPLE_TREE,
        )
    )

    assert result == [f"{_DOC_ID}::0001"]


def test_verify_navigation_treats_missing_verdict_as_keep(monkeypatch) -> None:
    """Nodes absent from the verdict map should be kept (prefer false-negatives)."""
    # Verdict only covers 0001; 0002 and 0003 are absent.
    verdict = json.dumps({"0001": True})
    _patch_llm(monkeypatch, verdict)

    result = asyncio.run(
        verify_navigation(
            query="How does auth work?",
            doc_id=_DOC_ID,
            node_refs=[
                f"{_DOC_ID}::0001",
                f"{_DOC_ID}::0002",
                f"{_DOC_ID}::0003",
            ],
            per_doc_tree=_SAMPLE_TREE,
        )
    )

    assert f"{_DOC_ID}::0001" in result
    assert f"{_DOC_ID}::0002" in result
    assert f"{_DOC_ID}::0003" in result


def test_verify_navigation_degrades_gracefully_on_llm_error(monkeypatch) -> None:
    """An LLM failure should return all original picks unchanged."""
    async def fail(**kwargs):
        raise RuntimeError("network error")

    monkeypatch.setattr(verifier_module, "get_async_client", lambda: SimpleNamespace())
    monkeypatch.setattr(verifier_module, "create_chat_completion_async", fail)

    node_refs = [f"{_DOC_ID}::0001", f"{_DOC_ID}::0002"]
    result = asyncio.run(
        verify_navigation(
            query="How does auth work?",
            doc_id=_DOC_ID,
            node_refs=node_refs,
            per_doc_tree=_SAMPLE_TREE,
        )
    )

    assert result == node_refs


def test_verify_navigation_degrades_gracefully_on_non_dict_response(monkeypatch) -> None:
    """A non-dict JSON response (e.g. a list) should keep all original picks."""
    _patch_llm(monkeypatch, '["0001", "0002"]')  # valid JSON but not a dict

    node_refs = [f"{_DOC_ID}::0001", f"{_DOC_ID}::0002"]
    result = asyncio.run(
        verify_navigation(
            query="How does auth work?",
            doc_id=_DOC_ID,
            node_refs=node_refs,
            per_doc_tree=_SAMPLE_TREE,
        )
    )

    assert result == node_refs


# ── verify_navigation_batch ───────────────────────────────────────────────────


def test_verify_navigation_batch_processes_all_docs(monkeypatch, tmp_path: Path) -> None:
    """The batch helper should verify every document in the navigation map."""
    from storage.store import DocumentStore

    storage = DocumentStore(str(tmp_path / "data"))
    storage.save_doc_tree("doc_a", _SAMPLE_TREE)
    storage.save_doc_tree("doc_b", _SAMPLE_TREE)

    verdict = json.dumps({"0001": True, "0002": False, "0003": True})
    _patch_llm(monkeypatch, verdict)

    nav_map = {
        "doc_a": ["doc_a::0001", "doc_a::0002"],
        "doc_b": ["doc_b::0002", "doc_b::0003"],
    }
    result = asyncio.run(
        verify_navigation_batch(
            query="How does auth work?",
            navigation_map=nav_map,
            storage=storage,
        )
    )

    # 0002 is false → dropped from both docs
    assert result["doc_a"] == ["doc_a::0001"]
    assert result["doc_b"] == ["doc_b::0003"]


def test_verify_navigation_batch_skips_missing_tree(monkeypatch, tmp_path: Path) -> None:
    """A doc whose tree is missing should pass through unverified."""
    from storage.store import DocumentStore

    storage = DocumentStore(str(tmp_path / "data"))
    # doc_b tree deliberately not saved

    verdict = json.dumps({"0001": False})
    _patch_llm(monkeypatch, verdict)

    nav_map = {"doc_b": ["doc_b::0001"]}
    result = asyncio.run(
        verify_navigation_batch(
            query="query",
            navigation_map=nav_map,
            storage=storage,
        )
    )

    # Without a tree the verifier keeps the original refs
    assert result["doc_b"] == ["doc_b::0001"]
