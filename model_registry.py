"""Persistent registry for model/deployment names shown in the UI dropdown."""

from __future__ import annotations

import json
from pathlib import Path


class ModelRegistry:
    """Store a small list of user-registered model names in JSON."""

    def __init__(self, registry_path: str | Path):
        """Create the registry object and make sure the backing file exists."""
        self.registry_path = Path(registry_path)
        self.registry_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_file()

    def _ensure_file(self) -> None:
        """Create an empty registry file the first time the app runs."""
        if not self.registry_path.exists():
            self.registry_path.write_text(
                json.dumps({"models": []}, indent=2),
                encoding="utf-8",
            )

    def _load(self) -> dict:
        """Load the registry payload from disk."""
        self._ensure_file()
        return json.loads(self.registry_path.read_text(encoding="utf-8"))

    def _save(self, payload: dict) -> None:
        """Persist the full registry payload back to disk."""
        self.registry_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def list_models(self) -> list[str]:
        """Return the cleaned list of model names the user has registered."""
        payload = self._load()
        models = payload.get("models", [])
        return [model for model in models if isinstance(model, str) and model.strip()]

    def ensure_model(self, model_name: str) -> None:
        """Insert a model name only if it is non-empty and not already present."""
        normalized = model_name.strip()
        if not normalized:
            return

        models = self.list_models()
        if normalized in models:
            return

        models.append(normalized)
        self._save({"models": models})

    def add_model(self, model_name: str) -> str:
        """Validate and add a model name, returning the normalized value."""
        normalized = model_name.strip()
        if not normalized:
            raise ValueError("Model name cannot be empty.")

        self.ensure_model(normalized)
        return normalized
