"""Corpus normalization and manifest storage for experiment runs."""

from __future__ import annotations

import hashlib
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from index_registry import sanitize_index_key_part
from experiments.layout import resolve_experiment_paths
from experiments.models import (
    CorpusDocument,
    CorpusDocumentInput,
    CorpusManifest,
    SUPPORTED_CORPUS_EXTENSIONS,
)
from utils import atomic_write_text, validate_doc_id


def _utc_now() -> str:
    """Return an ISO-8601 timestamp in UTC."""
    return datetime.now(timezone.utc).isoformat()


def _sha256_of_file(path: Path) -> str:
    """Return the SHA-256 digest of a file without loading it all at once."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _default_doc_id(path: Path, seen: set[str]) -> str:
    """Generate a stable doc_id from a filename and avoid collisions in one corpus."""
    base = sanitize_index_key_part(path.stem)
    candidate = base
    suffix = 2
    while candidate in seen:
        candidate = f"{base}_{suffix}"
        suffix += 1
    validate_doc_id(candidate)
    return candidate


class CorpusStore:
    """Create, load, and list corpora for experiment builds.

    A corpus is the canonical normalized document set. Build adapters consume
    corpus manifests rather than arbitrary local paths so later experiment runs
    are reproducible and cacheable.
    """

    def __init__(self, root: str | Path | None = None):
        self.paths = resolve_experiment_paths(root)

    def _corpus_dir(self, corpus_id: str) -> Path:
        return self.paths.corpora_dir / corpus_id

    def _manifest_path(self, corpus_id: str) -> Path:
        return self._corpus_dir(corpus_id) / "manifest.json"

    def _normalised_docs_dir(self, corpus_id: str) -> Path:
        return self._corpus_dir(corpus_id) / "source_docs"

    def create_corpus(
        self,
        name: str,
        documents: Iterable[CorpusDocumentInput],
        *,
        description: str | None = None,
        corpus_id: str | None = None,
        overwrite: bool = False,
    ) -> CorpusManifest:
        """Copy documents into the experiments store and write a manifest."""
        resolved_id = sanitize_index_key_part(corpus_id or name)
        corpus_dir = self._corpus_dir(resolved_id)
        manifest_path = self._manifest_path(resolved_id)
        normalised_dir = self._normalised_docs_dir(resolved_id)

        if corpus_dir.exists():
            if not overwrite:
                raise FileExistsError(
                    f"Corpus '{resolved_id}' already exists at {corpus_dir}. "
                    "Pass overwrite=True to replace it."
                )
            shutil.rmtree(corpus_dir)

        normalised_dir.mkdir(parents=True, exist_ok=True)

        manifest_docs: list[CorpusDocument] = []
        seen_doc_ids: set[str] = set()

        for raw_doc in documents:
            source_path = Path(raw_doc.source_path).expanduser().resolve()
            if not source_path.exists():
                raise FileNotFoundError(f"Corpus source file not found: {source_path}")

            extension = source_path.suffix.lower()
            if extension not in SUPPORTED_CORPUS_EXTENSIONS:
                raise ValueError(
                    f"Unsupported corpus source type '{extension}'. "
                    f"Expected one of: {sorted(SUPPORTED_CORPUS_EXTENSIONS)}."
                )

            if raw_doc.doc_id:
                validate_doc_id(raw_doc.doc_id)
                doc_id = raw_doc.doc_id
                if doc_id in seen_doc_ids:
                    raise ValueError(f"Duplicate doc_id '{doc_id}' in corpus input.")
            else:
                doc_id = _default_doc_id(source_path, seen_doc_ids)

            seen_doc_ids.add(doc_id)
            normalized_path = normalised_dir / f"{doc_id}{extension}"
            shutil.copy2(source_path, normalized_path)

            manifest_docs.append(
                CorpusDocument(
                    doc_id=doc_id,
                    doc_title=raw_doc.doc_title or source_path.stem,
                    doc_type=raw_doc.doc_type,
                    source_path=str(source_path),
                    normalized_path=str(normalized_path.resolve()),
                    extension=extension,
                    size_bytes=normalized_path.stat().st_size,
                    sha256=_sha256_of_file(normalized_path),
                )
            )

        manifest = CorpusManifest(
            corpus_id=resolved_id,
            name=name,
            description=description,
            created_at=_utc_now(),
            documents=manifest_docs,
        )
        atomic_write_text(
            manifest_path,
            manifest.model_dump_json(indent=2),
        )
        return manifest

    def load_corpus(self, corpus_id: str) -> CorpusManifest:
        """Load one stored corpus manifest by id."""
        manifest_path = self._manifest_path(corpus_id)
        if not manifest_path.exists():
            raise FileNotFoundError(f"No corpus manifest found for '{corpus_id}'.")
        return CorpusManifest.model_validate_json(
            manifest_path.read_text(encoding="utf-8")
        )

    def list_corpora(self) -> list[CorpusManifest]:
        """Load every corpus manifest currently stored in the experiments root."""
        manifests: list[CorpusManifest] = []
        for manifest_path in sorted(self.paths.corpora_dir.glob("*/manifest.json")):
            manifests.append(
                CorpusManifest.model_validate_json(
                    manifest_path.read_text(encoding="utf-8")
                )
            )
        return manifests
