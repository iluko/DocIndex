"""Factory that returns the correct TraceStore based on STORAGE_BACKEND."""

from __future__ import annotations

import os


def create_trace_store(base_data_dir: str, project: str):
    """Return a TraceStore for the active backend.

    Args:
        base_data_dir: Root data directory (used by the local backend).
        project:       Project name used to scope traces.
    """
    backend = os.environ.get("STORAGE_BACKEND", "local").lower()

    if backend == "mongodb":
        from traces.mongo_store import MongoTraceStore
        return MongoTraceStore(project=project)

    from traces.store import LocalTraceStore
    return LocalTraceStore(project=project, base_data_dir=base_data_dir)
