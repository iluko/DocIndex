"""File-based persistence for document trees and source-path metadata."""

from __future__ import annotations

import json
from pathlib import Path

from master_tree.schema import MasterNode


class DocumentStore:
    """Small storage wrapper around the project's JSON files and folders."""

    def __init__(self, data_dir: str):
        """Initialize the directory structure used by one active index."""
        self.data_dir = Path(data_dir)
        self.trees_dir = self.data_dir / "doc_trees"
        self.derived_markdown_dir = self.data_dir / "derived_markdown"
        self.sources_path = self.data_dir / "doc_sources.json"
        self.trees_dir.mkdir(parents=True, exist_ok=True)
        self.derived_markdown_dir.mkdir(parents=True, exist_ok=True)

    def _tree_path(self, doc_id: str) -> Path:
        """Compute the JSON path for one document's saved PageIndex tree."""
        return self.trees_dir / f"{doc_id}_tree.json"

    def _load_sources(self) -> dict[str, str | dict[str, str]]:
        """Load the source-path registry, or return an empty mapping if absent."""
        if not self.sources_path.exists():
            return {}
        return json.loads(self.sources_path.read_text(encoding="utf-8"))

    def _save_sources(self, sources: dict[str, str | dict[str, str]]) -> None:
        """Persist the source-path registry to disk."""
        self.sources_path.write_text(
            json.dumps(sources, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    def save_doc_tree(self, doc_id: str, tree: dict) -> str:
        """Save one PageIndex tree and return the file path that was written."""
        path = self._tree_path(doc_id)
        path.write_text(json.dumps(tree, indent=2, ensure_ascii=False), encoding="utf-8")
        return str(path)

    def load_doc_tree(self, doc_id: str) -> dict:
        """Load a previously saved PageIndex tree for one document."""
        path = self._tree_path(doc_id)
        if not path.exists():
            raise FileNotFoundError(
                f"No PageIndex tree found for doc_id='{doc_id}' at {path}."
            )
        return json.loads(path.read_text(encoding="utf-8"))

    def doc_tree_exists(self, doc_id: str) -> bool:
        """Report whether a PageIndex tree has already been stored for a doc id."""
        return self._tree_path(doc_id).exists()

    def save_derived_markdown(self, doc_id: str, markdown: str) -> str:
        """Persist markdown generated from a DOCX source and return its path."""
        path = self.derived_markdown_dir / f"{doc_id}.md"
        path.write_text(markdown, encoding="utf-8")
        return str(path)

    def register_doc_source(
        self, doc_id: str, file_path: str, retrieval_path: str | None = None
    ) -> None:
        """Record both the original source path and the best retrieval-time path."""
        sources = self._load_sources()
        sources[doc_id] = {
            "source_path": file_path,
            "retrieval_path": retrieval_path or file_path,
        }
        self._save_sources(sources)

    def load_doc_source_path(self, doc_id: str) -> str:
        """Return the path the fetcher should read when it needs raw content."""
        sources = self._load_sources()
        if doc_id not in sources:
            raise FileNotFoundError(
                f"No source file path recorded for doc_id='{doc_id}' in {self.sources_path}."
            )
        record = sources[doc_id]
        if isinstance(record, str):
            return record
        return record.get("retrieval_path") or record.get("source_path", "")

    def get_doc_file_path(self, master_node: MasterNode) -> str:
        """Expose the original source path from a master-tree node."""
        return master_node.file_path

    def delete_doc_tree(self, doc_id: str) -> bool:
        """Delete the on-disk PageIndex tree for one document.

        Returns ``True`` if the file existed and was deleted, ``False`` if it
        was already absent (idempotent so reingest can call this safely).
        """
        path = self._tree_path(doc_id)
        if not path.exists():
            return False
        path.unlink()
        return True

    def deregister_doc_source(self, doc_id: str) -> bool:
        """Remove a document's source-path record from the registry.

        Returns ``True`` if the entry was found and removed, ``False`` if it
        was not present.
        """
        sources = self._load_sources()
        if doc_id not in sources:
            return False
        del sources[doc_id]
        self._save_sources(sources)
        return True

    def delete_derived_markdown(self, doc_id: str) -> bool:
        """Delete the derived Markdown file for a DOCX-sourced document, if any.

        Returns ``True`` if the file existed and was deleted.
        """
        path = self.derived_markdown_dir / f"{doc_id}.md"
        if not path.exists():
            return False
        path.unlink()
        return True
