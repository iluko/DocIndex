"""Tests for project-scoped index path resolution."""

from __future__ import annotations

from pathlib import Path

from index_registry import DEFAULT_PROJECT, build_runtime_components, list_projects, resolve_index_context


def test_default_project_used_when_none_given(tmp_path: Path, monkeypatch) -> None:
    """Omitting --project should resolve to the 'default' project directory."""
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_MODEL", "gpt-4o")

    ctx = resolve_index_context(tmp_path)

    assert ctx.project == DEFAULT_PROJECT
    assert ctx.index_dir == tmp_path / "indexes" / DEFAULT_PROJECT


def test_project_sets_index_directory(tmp_path: Path, monkeypatch) -> None:
    """A named project should resolve to its own isolated directory."""
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_MODEL", "gpt-4o")

    ctx = resolve_index_context(tmp_path, project="My Legal Docs")

    assert ctx.project == "my_legal_docs"
    assert ctx.index_dir == tmp_path / "indexes" / "my_legal_docs"


def test_model_does_not_affect_index_directory(tmp_path: Path, monkeypatch) -> None:
    """Switching models must not change which project directory is used."""
    monkeypatch.setenv("LLM_PROVIDER", "azure")

    ctx_a = resolve_index_context(tmp_path, model="gpt-4o", project="hr-docs")
    ctx_b = resolve_index_context(tmp_path, model="gpt-4.1", project="hr-docs")

    assert ctx_a.index_dir == ctx_b.index_dir
    assert ctx_a.model != ctx_b.model


def test_list_projects_returns_default_when_no_indexes(tmp_path: Path) -> None:
    """list_projects should return ['default'] when no indexes directory exists."""
    projects = list_projects(tmp_path)
    assert projects == [DEFAULT_PROJECT]


def test_list_projects_returns_existing_directories(tmp_path: Path) -> None:
    """list_projects should return names of all project directories on disk."""
    indexes_dir = tmp_path / "indexes"
    (indexes_dir / "alpha").mkdir(parents=True)
    (indexes_dir / "beta").mkdir(parents=True)

    projects = list_projects(tmp_path)

    assert "alpha" in projects
    assert "beta" in projects


def test_build_runtime_components_creates_project_directory(tmp_path: Path, monkeypatch) -> None:
    """build_runtime_components must create the project directory if absent."""
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_MODEL", "gpt-4o")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    runtime = build_runtime_components(tmp_path, project="research")

    assert runtime.index_context.index_dir.exists()
    assert runtime.index_context.project == "research"
