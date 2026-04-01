"""Persistence and CRUD helpers for the master tree JSON file."""

from __future__ import annotations

from pathlib import Path

from master_tree.schema import MasterNode, MasterTree as MasterTreeModel
from utils import compact_json


class MasterTreeStore:
    """Load, update, and serialize the master tree used by the router."""

    def __init__(self, master_tree_path: str):
        """Point the store at one JSON file and load it immediately."""
        self.master_tree_path = Path(master_tree_path)
        self.master_tree_path.parent.mkdir(parents=True, exist_ok=True)
        self.tree = self.load()

    def load(self) -> MasterTreeModel:
        """Load the master tree from disk, or create an empty one if absent."""
        if self.master_tree_path.exists():
            self.tree = MasterTreeModel.model_validate_json(
                self.master_tree_path.read_text(encoding="utf-8")
            )
        else:
            self.tree = MasterTreeModel()
        return self.tree

    def save(self, tree: MasterTreeModel | None = None) -> None:
        """Persist the current in-memory tree back to disk."""
        if tree is not None:
            self.tree = tree
        self.master_tree_path.write_text(
            self.tree.model_dump_json(indent=2), encoding="utf-8"
        )

    def add_node(self, node: MasterNode) -> None:
        """Upsert one document into the master tree by `doc_id`."""
        docs = list(self.tree.docs)
        for index, existing in enumerate(docs):
            if existing.doc_id == node.doc_id:
                docs[index] = node
                break
        else:
            docs.append(node)

        self.tree = MasterTreeModel(version=self.tree.version, docs=docs)

    def get_node(self, doc_id: str) -> MasterNode | None:
        """Return one document node or `None` if the doc id is unknown."""
        return next((doc for doc in self.tree.docs if doc.doc_id == doc_id), None)

    def list_docs(self) -> list[MasterNode]:
        """Return a shallow copy of all stored document nodes."""
        return list(self.tree.docs)

    def remove_node(self, doc_id: str) -> bool:
        """Remove one document from the master tree by ``doc_id``.

        Returns ``True`` if the document was found and removed, ``False`` if
        it was not present (so the caller can decide whether to error or warn).
        The change is applied to the in-memory tree only; call ``save()`` to
        persist it.
        """
        docs = [doc for doc in self.tree.docs if doc.doc_id != doc_id]
        if len(docs) == len(self.tree.docs):
            return False
        self.tree = MasterTreeModel(version=self.tree.version, docs=docs)
        return True

    def to_llm_context(self) -> str:
        """Serialize only routing-relevant fields for prompt injection.

        Junior note:
        We intentionally omit operational fields like file paths and timestamps
        so the router focuses on meaning, not storage details.
        """
        routing_docs = []
        for doc in self.tree.docs:
            routing_docs.append(
                {
                    "doc_id": doc.doc_id,
                    "doc_title": doc.doc_title,
                    "doc_type": doc.doc_type,
                    "doc_summary": doc.doc_summary,
                    "key_topics": doc.key_topics,
                    "relevance_hints": doc.relevance_hints.model_dump(),
                    "top_sections": [
                        {
                            "title": section.title,
                            "section_summary": section.section_summary,
                        }
                        for section in doc.top_sections
                    ],
                    "related_docs": doc.related_docs,
                }
            )

        return compact_json({"version": self.tree.version, "docs": routing_docs})
