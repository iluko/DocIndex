"""Tests for model-scoped index path resolution and legacy migration."""

from __future__ import annotations

import json
from pathlib import Path

from index_registry import build_index_key, build_runtime_components


def test_build_index_key_sanitizes_provider_and_model() -> None:
    """Index keys should be normalized into filesystem-safe strings."""
    assert build_index_key("azure", "gpt-4.1") == "azure__gpt_4_1"
    assert build_index_key("OpenAI", "My Deployment/01") == "openai__my_deployment_01"


def test_legacy_index_is_migrated_once_into_active_model_scope(
    tmp_path: Path, monkeypatch
) -> None:
    """Legacy flat storage should migrate only once into the active index scope."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "master_tree.json").write_text('{"version":"1.0","docs":[]}', encoding="utf-8")
    (data_dir / "doc_sources.json").write_text("{}", encoding="utf-8")
    legacy_trees_dir = data_dir / "doc_trees"
    legacy_trees_dir.mkdir()
    (legacy_trees_dir / "sample_tree.json").write_text("{}", encoding="utf-8")
    legacy_markdown_dir = data_dir / "derived_markdown"
    legacy_markdown_dir.mkdir()
    (legacy_markdown_dir / "sample.md").write_text("# Sample\n", encoding="utf-8")
    (data_dir / "arch_map.json").write_text('{"description":"","modules":[]}', encoding="utf-8")

    monkeypatch.setenv("LLM_PROVIDER", "azure")
    monkeypatch.setenv("LLM_MODEL", "gpt-4.1")

    runtime = build_runtime_components(data_dir, model="gpt-4.1")

    assert runtime.index_context.index_key == "azure__gpt_4_1"
    assert runtime.index_context.master_tree_path.exists()
    assert (runtime.index_context.index_dir / "doc_trees" / "sample_tree.json").exists()
    assert (runtime.index_context.index_dir / "derived_markdown" / "sample.md").exists()
    marker = json.loads((data_dir / ".legacy_index_migration.json").read_text(encoding="utf-8"))
    assert marker["migrated_to_index_key"] == "azure__gpt_4_1"

    second_runtime = build_runtime_components(data_dir, model="gpt-4.1")
    assert second_runtime.index_context.master_tree_path.exists()
