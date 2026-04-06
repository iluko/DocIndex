"""Helpers for resolving API runtime dependencies from the existing core."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from fastapi import UploadFile

from index_registry import build_runtime_components
from model_registry import ModelRegistry


BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
MODEL_REGISTRY_PATH = DATA_DIR / "model_registry.json"


def get_model_registry() -> ModelRegistry:
    return ModelRegistry(MODEL_REGISTRY_PATH)


def build_runtime(project: str, model: str | None = None):
    registry = get_model_registry()
    if model:
        registry.ensure_model(model)
    return build_runtime_components(DATA_DIR, model=model, project=project)


async def persist_upload(upload: UploadFile, uploads_dir: Path, doc_id: str) -> Path:
    uploads_dir.mkdir(parents=True, exist_ok=True)
    suffix = Path(upload.filename or "").suffix
    destination = uploads_dir / f"{doc_id}_{uuid4().hex[:8]}{suffix}"
    content = await upload.read()
    destination.write_bytes(content)
    return destination

