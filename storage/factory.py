"""Factory that returns the correct DocumentStore based on STORAGE_BACKEND.

Supported backends (set via the STORAGE_BACKEND environment variable):
    local    (default) — file-based store rooted at data_dir
    mongodb            — MongoDB store scoped to the given project name

MongoDB additionally requires MONGODB_URI and optionally MONGODB_DATABASE.
"""

from __future__ import annotations

import os

from storage.base import AbstractDocumentStore


def create_document_store(data_dir: str, project: str) -> AbstractDocumentStore:
    """Return a DocumentStore for the active backend.

    Args:
        data_dir: Root directory used by the local backend (ignored for MongoDB).
        project:  Project name used to scope MongoDB documents.
    """
    backend = os.environ.get("STORAGE_BACKEND", "local").lower()

    if backend == "mongodb":
        from storage.mongo_store import MongoDocumentStore
        return MongoDocumentStore(project=project)

    from storage.store import DocumentStore
    return DocumentStore(data_dir)
