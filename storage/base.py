"""Abstract base class for document storage backends."""

from __future__ import annotations

from abc import ABC, abstractmethod

from master_tree.schema import MasterNode


class AbstractDocumentStore(ABC):
    """Protocol for storing document trees, source paths, and derived markdown."""

    @abstractmethod
    def save_doc_tree(self, doc_id: str, tree: dict) -> str:
        """Save a PageIndex tree and return a location identifier string."""

    @abstractmethod
    def load_doc_tree(self, doc_id: str) -> dict:
        """Load a previously saved PageIndex tree for one document."""

    @abstractmethod
    def doc_tree_exists(self, doc_id: str) -> bool:
        """Return True if a tree has already been stored for this doc_id."""

    @abstractmethod
    def save_derived_markdown(self, doc_id: str, markdown: str) -> str:
        """Persist markdown generated from a DOCX source and return its location."""

    @abstractmethod
    def register_doc_source(
        self, doc_id: str, file_path: str, retrieval_path: str | None = None
    ) -> None:
        """Record both the original source path and the best retrieval-time path."""

    @abstractmethod
    def load_doc_source_path(self, doc_id: str) -> str:
        """Return the path the fetcher should read when it needs raw content."""

    def get_doc_file_path(self, master_node: MasterNode) -> str:
        """Return the original source path from a master-tree node."""
        return master_node.file_path

    @abstractmethod
    def delete_doc_tree(self, doc_id: str) -> bool:
        """Delete the stored tree for one document. Returns True if it existed."""

    @abstractmethod
    def deregister_doc_source(self, doc_id: str) -> bool:
        """Remove a document's source-path record. Returns True if it existed."""

    @abstractmethod
    def delete_derived_markdown(self, doc_id: str) -> bool:
        """Delete derived markdown for a DOCX-sourced document. Returns True if it existed."""
