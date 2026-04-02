"""MongoDB-backed document storage.

Environment variables:
    STORAGE_BACKEND=mongodb   — activate this backend
    MONGODB_URI               — connection string (default: mongodb://localhost:27017)
    MONGODB_DATABASE          — database name (default: hybrid_approach)

Collections used (shared across projects, scoped by a ``project`` field):
    doc_trees         — PageIndex trees keyed by (project, doc_id)
    doc_sources       — source / retrieval paths keyed by (project, doc_id)
    derived_markdown  — DOCX-converted markdown keyed by (project, doc_id)
"""

from __future__ import annotations

import os

from storage.base import AbstractDocumentStore


class MongoDocumentStore(AbstractDocumentStore):
    """Store document trees and metadata in a MongoDB database."""

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
        self._db_name = db_name
        self._trees = db["doc_trees"]
        self._sources = db["doc_sources"]
        self._markdown = db["derived_markdown"]

        # Ensure fast project+doc_id lookups.
        self._trees.create_index([("project", 1), ("doc_id", 1)], unique=True)
        self._sources.create_index([("project", 1), ("doc_id", 1)], unique=True)
        self._markdown.create_index([("project", 1), ("doc_id", 1)], unique=True)

    def _key(self, doc_id: str) -> dict:
        return {"project": self._project, "doc_id": doc_id}

    def _uri(self, collection: str, doc_id: str) -> str:
        return f"mongodb://{self._db_name}/{collection}/{self._project}/{doc_id}"

    # ------------------------------------------------------------------
    # DocumentStore interface
    # ------------------------------------------------------------------

    def save_doc_tree(self, doc_id: str, tree: dict) -> str:
        self._trees.replace_one(
            self._key(doc_id),
            {**self._key(doc_id), "tree": tree},
            upsert=True,
        )
        return self._uri("doc_trees", doc_id)

    def load_doc_tree(self, doc_id: str) -> dict:
        record = self._trees.find_one(self._key(doc_id))
        if record is None:
            raise FileNotFoundError(
                f"No PageIndex tree found for doc_id='{doc_id}' "
                f"in project='{self._project}'."
            )
        return record["tree"]

    def doc_tree_exists(self, doc_id: str) -> bool:
        return (
            self._trees.count_documents(self._key(doc_id), limit=1) > 0
        )

    def save_derived_markdown(self, doc_id: str, markdown: str) -> str:
        self._markdown.replace_one(
            self._key(doc_id),
            {**self._key(doc_id), "content": markdown},
            upsert=True,
        )
        return self._uri("derived_markdown", doc_id)

    def register_doc_source(
        self, doc_id: str, file_path: str, retrieval_path: str | None = None
    ) -> None:
        self._sources.replace_one(
            self._key(doc_id),
            {
                **self._key(doc_id),
                "source_path": file_path,
                "retrieval_path": retrieval_path or file_path,
            },
            upsert=True,
        )

    def load_doc_source_path(self, doc_id: str) -> str:
        record = self._sources.find_one(self._key(doc_id))
        if record is None:
            raise FileNotFoundError(
                f"No source file path recorded for doc_id='{doc_id}' "
                f"in project='{self._project}'."
            )
        return record.get("retrieval_path") or record.get("source_path", "")

    def delete_doc_tree(self, doc_id: str) -> bool:
        return self._trees.delete_one(self._key(doc_id)).deleted_count > 0

    def deregister_doc_source(self, doc_id: str) -> bool:
        return self._sources.delete_one(self._key(doc_id)).deleted_count > 0

    def delete_derived_markdown(self, doc_id: str) -> bool:
        return self._markdown.delete_one(self._key(doc_id)).deleted_count > 0

    def delete_all(self) -> None:
        """Remove every stored artifact for this project from all collections."""
        project_filter = {"project": self._project}
        self._trees.delete_many(project_filter)
        self._sources.delete_many(project_filter)
        self._markdown.delete_many(project_filter)
