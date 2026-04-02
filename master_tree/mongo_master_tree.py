"""MongoDB-backed master tree store.

Mirrors the public interface of MasterTreeStore so the rest of the app
can use either backend without modification.

Environment variables (same as MongoDocumentStore):
    MONGODB_URI       — connection string (default: mongodb://localhost:27017)
    MONGODB_DATABASE  — database name (default: hybrid_approach)

Collection used:
    master_trees — one document per project:
        { project, version, docs: [...] }
"""

from __future__ import annotations

import os

from master_tree.schema import MasterNode, MasterTree as MasterTreeModel
from utils import compact_json


class MongoMasterTreeStore:
    """Persist and query the master tree in a MongoDB database."""

    def __init__(self, project: str):
        try:
            from pymongo import MongoClient
        except ImportError as exc:
            raise ImportError(
                "pymongo is required for MongoDB storage. "
                "Install it with: pip install pymongo"
            ) from exc

        uri = os.environ.get("MONGODB_URI", "mongodb://localhost:27017")
        db_name = os.environ.get("MONGODB_DATABASE", "hybrid_approach")
        self._project = project

        client = MongoClient(uri)
        db = client[db_name]
        self._col = db["master_trees"]
        self._col.create_index([("project", 1)], unique=True)

        # String identifier used by trace logging (mirrors MasterTreeStore.master_tree_path).
        self.master_tree_path = f"mongodb://{db_name}/master_trees/{project}"

        self.tree = self.load()

    def _filter(self) -> dict:
        return {"project": self._project}

    def load(self) -> MasterTreeModel:
        record = self._col.find_one(self._filter())
        if record is None:
            self.tree = MasterTreeModel()
        else:
            self.tree = MasterTreeModel.model_validate(
                {"version": record.get("version", 1), "docs": record.get("docs", [])}
            )
        return self.tree

    def save(self, tree: MasterTreeModel | None = None) -> None:
        if tree is not None:
            self.tree = tree
        payload = self.tree.model_dump(mode="json")
        self._col.replace_one(
            self._filter(),
            {"project": self._project, **payload},
            upsert=True,
        )

    def add_node(self, node: MasterNode) -> None:
        docs = list(self.tree.docs)
        for index, existing in enumerate(docs):
            if existing.doc_id == node.doc_id:
                docs[index] = node
                break
        else:
            docs.append(node)
        self.tree = MasterTreeModel(version=self.tree.version, docs=docs)

    def get_node(self, doc_id: str) -> MasterNode | None:
        return next((doc for doc in self.tree.docs if doc.doc_id == doc_id), None)

    def list_docs(self) -> list[MasterNode]:
        return list(self.tree.docs)

    def remove_node(self, doc_id: str) -> bool:
        docs = [doc for doc in self.tree.docs if doc.doc_id != doc_id]
        if len(docs) == len(self.tree.docs):
            return False
        self.tree = MasterTreeModel(version=self.tree.version, docs=docs)
        return True

    def delete_all(self) -> None:
        """Remove this project's master tree document from the collection."""
        self._col.delete_one(self._filter())

    def to_llm_context(self) -> str:
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
