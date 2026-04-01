"""Tests that describe master-tree persistence and prompt serialization."""

from __future__ import annotations

import json
from pathlib import Path

from master_tree.master_tree import MasterTreeStore
from master_tree.schema import MasterNode


FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _load_fixture(name: str) -> dict:
    """Load one shared JSON fixture."""
    return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))


def test_master_tree_roundtrip_and_upsert(tmp_path: Path) -> None:
    """Saving and reloading should preserve data, including upserts."""
    store_path = tmp_path / "master_tree.json"
    store = MasterTreeStore(str(store_path))
    fixture_tree = _load_fixture("sample_master_tree.json")

    for node_payload in fixture_tree["docs"]:
        store.add_node(MasterNode.model_validate(node_payload))

    replacement = MasterNode.model_validate(
        {
            **fixture_tree["docs"][0],
            "doc_summary": "Updated summary for auth doc.",
        }
    )
    store.add_node(replacement)
    store.save(store.tree)

    reloaded = MasterTreeStore(str(store_path))
    assert len(reloaded.list_docs()) == 2
    assert reloaded.get_node("auth_doc") is not None
    assert reloaded.get_node("auth_doc").doc_summary == "Updated summary for auth doc."


def test_to_llm_context_omits_operational_fields(tmp_path: Path) -> None:
    """Prompt serialization should exclude non-semantic operational fields."""
    store = MasterTreeStore(str(tmp_path / "master_tree.json"))
    fixture_tree = _load_fixture("sample_master_tree.json")

    for node_payload in fixture_tree["docs"]:
        store.add_node(MasterNode.model_validate(node_payload))

    llm_context = json.loads(store.to_llm_context())
    auth_doc = llm_context["docs"][0]

    assert "file_path" not in auth_doc
    assert "tree_path" not in auth_doc
    assert "ingested_at" not in auth_doc
    assert "node_ref" not in auth_doc["top_sections"][0]
