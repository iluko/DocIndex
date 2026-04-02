"""Factory that returns the correct MasterTreeStore based on STORAGE_BACKEND.

Supported backends (set via the STORAGE_BACKEND environment variable):
    local    (default) — file-based store at master_tree_path
    mongodb            — MongoDB store scoped to the given project name
"""

from __future__ import annotations

import os


def create_master_tree_store(master_tree_path: str, project: str):
    """Return a MasterTreeStore for the active backend.

    Args:
        master_tree_path: JSON file path used by the local backend (ignored for MongoDB).
        project:          Project name used to scope MongoDB documents.
    """
    backend = os.environ.get("STORAGE_BACKEND", "local").lower()

    if backend == "mongodb":
        from master_tree.mongo_master_tree import MongoMasterTreeStore
        return MongoMasterTreeStore(project=project)

    from master_tree.master_tree import MasterTreeStore
    return MasterTreeStore(master_tree_path)
