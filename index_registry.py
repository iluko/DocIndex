"""Utilities for keeping each project's index artifacts in a separate folder.

Index layout:
    data/indexes/{project}/          ← one directory per project (knowledge base)
        master_tree.json
        doc_trees/
        derived_markdown/
        doc_sources.json
        index_meta.json              ← records which model last wrote here

The model is a runtime-only parameter (used for LLM calls). Switching models
does NOT change which documents are visible — only the project does that.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from arch_map.arch_map import ArchitectureMap
from master_tree.factory import create_master_tree_store
from storage.factory import create_document_store
from utils import detect_llm_provider, get_default_model


DEFAULT_PROJECT = "default"


@dataclass(frozen=True)
class IndexContext:
    """Describes the on-disk paths for one project-scoped index."""

    provider: str
    model: str
    project: str        # always set — defaults to DEFAULT_PROJECT
    index_key: str      # same as project (the directory name under indexes/)
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
    master_tree_store: Any  # MasterTreeStore or MongoMasterTreeStore
    storage: Any  # DocumentStore or MongoDocumentStore
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


def resolve_index_context(
    base_data_dir: str | Path,
    model: str | None = None,
    project: str | None = None,
) -> IndexContext:
    """Resolve every important path for the active project.

    The index directory is ``data/indexes/{project}/``. The model is recorded
    in metadata but does NOT affect which directory is used — switching models
    does not change which documents you see.
    """
    base_dir = Path(base_data_dir)
    provider = detect_llm_provider()
    resolved_model = model or get_default_model()
    project_key = sanitize_index_key_part(project) if project else DEFAULT_PROJECT
    indexes_dir = base_dir / "indexes"
    index_dir = indexes_dir / project_key

    return IndexContext(
        provider=provider,
        model=resolved_model,
        project=project_key,
        index_key=project_key,
        base_data_dir=base_dir,
        index_dir=index_dir,
        master_tree_path=index_dir / "master_tree.json",
        metadata_path=index_dir / "index_meta.json",
        arch_map_path=base_dir / "arch_map.json",
        uploads_dir=base_dir / "uploads",
    )


def list_projects(base_data_dir: str | Path) -> list[str]:
    """Return all project names found under ``data/indexes/``.

    Projects are the directory names immediately under the indexes folder.
    Returns ``[DEFAULT_PROJECT]`` when no projects exist yet so callers
    always have at least one option.
    """
    indexes_dir = Path(base_data_dir) / "indexes"
    if not indexes_dir.exists():
        return [DEFAULT_PROJECT]
    names = sorted(
        p.name for p in indexes_dir.iterdir()
        if p.is_dir() and not p.name.startswith(".")
    )
    return names if names else [DEFAULT_PROJECT]


def _read_json(path: Path) -> dict:
    """Read a JSON file into a Python dictionary."""
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict) -> None:
    """Write JSON while creating parent directories when needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def ensure_index_metadata(context: IndexContext) -> None:
    """Persist a small metadata file that describes the active index scope.

    The model is recorded here (not in the directory path) so switching models
    is transparent — the same documents remain accessible.
    """
    payload = {
        "project": context.project,
        "index_key": context.index_key,
        "last_used_provider": context.provider,
        "last_used_model": context.model,
        "index_dir": str(context.index_dir),
    }
    _write_json(context.metadata_path, payload)


def build_runtime_components(
    base_data_dir: str | Path,
    model: str | None = None,
    project: str | None = None,
) -> RuntimeComponents:
    """Construct the storage and lookup objects used by the active runtime."""
    context = resolve_index_context(base_data_dir=base_data_dir, model=model, project=project)
    context.index_dir.mkdir(parents=True, exist_ok=True)
    context.uploads_dir.mkdir(parents=True, exist_ok=True)

    ensure_index_metadata(context)

    return RuntimeComponents(
        index_context=context,
        master_tree_store=create_master_tree_store(str(context.master_tree_path), context.project),
        storage=create_document_store(str(context.index_dir), context.project),
        arch_map=ArchitectureMap(str(context.arch_map_path)),
    )
