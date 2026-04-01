"""Utilities for keeping each model's index artifacts in a separate folder.

This module answers questions like:
- "Which directory should the current model write into?"
- "Where does the active master tree live?"
- "Should we migrate legacy flat storage into the new model-scoped layout?"
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from arch_map.arch_map import ArchitectureMap
from master_tree.master_tree import MasterTreeStore
from storage.store import DocumentStore
from utils import detect_llm_provider, get_default_model


LEGACY_MIGRATION_MARKER = ".legacy_index_migration.json"


@dataclass(frozen=True)
class IndexContext:
    """Describes the on-disk paths for one provider/model-specific index."""

    provider: str
    model: str
    index_key: str
    base_data_dir: Path
    index_dir: Path
    master_tree_path: Path
    metadata_path: Path
    arch_map_path: Path
    uploads_dir: Path


@dataclass
class RuntimeComponents:
    """Bundles the objects the UI/CLI need for one active index scope."""

    index_context: IndexContext
    master_tree_store: MasterTreeStore
    storage: DocumentStore
    arch_map: ArchitectureMap


def sanitize_index_key_part(value: str) -> str:
    """Turn free-form text into a filesystem-safe key fragment.

    Junior note:
    We collapse runs of non-alphanumeric characters into a single underscore so
    values like "gpt-5.2-chat" and "gpt 5.2 chat" end up as stable directory names.
    """
    sanitized = []
    previous_was_separator = False

    for char in value.strip().lower():
        if char.isalnum():
            sanitized.append(char)
            previous_was_separator = False
        else:
            if not previous_was_separator:
                sanitized.append("_")
            previous_was_separator = True

    result = "".join(sanitized).strip("_")
    return result or "default"


def build_index_key(provider: str, model: str) -> str:
    """Combine provider and model into the directory name for one index."""
    return f"{sanitize_index_key_part(provider)}__{sanitize_index_key_part(model)}"


def resolve_index_context(base_data_dir: str | Path, model: str | None = None) -> IndexContext:
    """Resolve every important path for the current runtime model."""
    base_dir = Path(base_data_dir)
    provider = detect_llm_provider()
    resolved_model = model or get_default_model()
    index_key = build_index_key(provider, resolved_model)
    indexes_dir = base_dir / "indexes"
    index_dir = indexes_dir / index_key

    return IndexContext(
        provider=provider,
        model=resolved_model,
        index_key=index_key,
        base_data_dir=base_dir,
        index_dir=index_dir,
        master_tree_path=index_dir / "master_tree.json",
        metadata_path=index_dir / "index_meta.json",
        arch_map_path=base_dir / "arch_map.json",
        uploads_dir=base_dir / "uploads",
    )


def _read_json(path: Path) -> dict:
    """Read a JSON file into a Python dictionary."""
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict) -> None:
    """Write JSON while creating parent directories when needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def ensure_index_metadata(context: IndexContext) -> None:
    """Persist a small metadata file that describes the active index scope."""
    payload = {
        "provider": context.provider,
        "model": context.model,
        "index_key": context.index_key,
        "index_dir": str(context.index_dir),
    }
    _write_json(context.metadata_path, payload)


def _copy_if_exists(source: Path, destination: Path) -> None:
    """Copy a file or directory only when the source exists.

    Junior note:
    `shutil.copytree(..., dirs_exist_ok=True)` lets us merge into an existing
    directory instead of failing the way older Python examples often do.
    """
    if not source.exists():
        return

    if source.is_dir():
        destination.mkdir(parents=True, exist_ok=True)
        for child in source.iterdir():
            target = destination / child.name
            if child.is_dir():
                shutil.copytree(child, target, dirs_exist_ok=True)
            else:
                shutil.copy2(child, target)
        return

    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def migrate_legacy_index_if_needed(context: IndexContext) -> bool:
    """Copy the pre-model-scoped data layout into the active index once.

    We keep a marker file so this migration does not run repeatedly on every app start.
    """
    marker_path = context.base_data_dir / LEGACY_MIGRATION_MARKER
    if marker_path.exists():
        return False

    if context.master_tree_path.exists():
        return False

    legacy_master_tree = context.base_data_dir / "master_tree.json"
    legacy_doc_trees = context.base_data_dir / "doc_trees"
    legacy_doc_sources = context.base_data_dir / "doc_sources.json"
    legacy_derived_markdown = context.base_data_dir / "derived_markdown"

    if not any(
        path.exists()
        for path in (
            legacy_master_tree,
            legacy_doc_trees,
            legacy_doc_sources,
            legacy_derived_markdown,
        )
    ):
        return False

    _copy_if_exists(legacy_master_tree, context.master_tree_path)
    _copy_if_exists(legacy_doc_trees, context.index_dir / "doc_trees")
    _copy_if_exists(legacy_doc_sources, context.index_dir / "doc_sources.json")
    _copy_if_exists(legacy_derived_markdown, context.index_dir / "derived_markdown")

    _write_json(
        marker_path,
        {
            "migrated_to_index_key": context.index_key,
            "provider": context.provider,
            "model": context.model,
        },
    )
    return True


def build_runtime_components(base_data_dir: str | Path, model: str | None = None) -> RuntimeComponents:
    """Construct the storage and lookup objects used by the active runtime."""
    context = resolve_index_context(base_data_dir=base_data_dir, model=model)
    context.index_dir.mkdir(parents=True, exist_ok=True)
    context.uploads_dir.mkdir(parents=True, exist_ok=True)

    migrate_legacy_index_if_needed(context)
    ensure_index_metadata(context)

    return RuntimeComponents(
        index_context=context,
        master_tree_store=MasterTreeStore(str(context.master_tree_path)),
        storage=DocumentStore(str(context.index_dir)),
        arch_map=ArchitectureMap(str(context.arch_map_path)),
    )
