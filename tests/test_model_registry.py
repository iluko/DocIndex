"""Tests for the small persistent model/deployment registry."""

from __future__ import annotations

from pathlib import Path

from model_registry import ModelRegistry


def test_model_registry_creates_file_and_stores_unique_models(tmp_path: Path) -> None:
    """The registry should create its file and avoid duplicate model names."""
    registry = ModelRegistry(tmp_path / "model_registry.json")

    registry.ensure_model("gpt-4.1")
    registry.ensure_model("gpt-4.1")
    registry.add_model("gpt-5.2-chat")

    assert registry.list_models() == ["gpt-4.1", "gpt-5.2-chat"]


def test_model_registry_rejects_empty_model_name(tmp_path: Path) -> None:
    """Blank model names should be rejected with a clear error."""
    registry = ModelRegistry(tmp_path / "model_registry.json")

    try:
        registry.add_model("   ")
    except ValueError as exc:
        assert str(exc) == "Model name cannot be empty."
    else:
        raise AssertionError("Expected empty model registration to fail.")
