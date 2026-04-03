"""MongoDB-backed audit trace store.

Collection: query_traces
    One document per query, scoped by (project, trace_id).

Environment variables (same as other Mongo stores):
    MONGODB_URI       — connection string (default: mongodb://localhost:27017)
    MONGODB_DATABASE  — database name    (default: hybrid_approach)
"""

from __future__ import annotations

import os

from traces.schema import AuditTrace, TraceSummary


class MongoTraceStore:
    """Store audit traces in a MongoDB collection."""

    def __init__(self, project: str) -> None:
        try:
            from pymongo import MongoClient, DESCENDING
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
        self._col = db["query_traces"]
        self._col.create_index([("project", 1), ("timestamp", DESCENDING)])
        self._col.create_index([("project", 1), ("trace_id", 1)], unique=True)

    def _filter(self, trace_id: str | None = None) -> dict:
        f: dict = {"project": self._project}
        if trace_id is not None:
            f["trace_id"] = trace_id
        return f

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def save(self, trace: AuditTrace) -> None:
        payload = trace.model_dump(mode="json")
        self._col.replace_one(
            self._filter(trace.trace_id),
            {"project": self._project, **payload},
            upsert=True,
        )

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def load(self, trace_id: str) -> AuditTrace | None:
        record = self._col.find_one(self._filter(trace_id))
        if record is None:
            return None
        record.pop("_id", None)
        record.pop("project", None)
        return AuditTrace.model_validate(record)

    def list_summaries(self, limit: int = 50, offset: int = 0) -> list[TraceSummary]:
        cursor = (
            self._col.find(
                self._filter(),
                {
                    "trace_id": 1, "timestamp": 1, "query": 1, "retrieval_mode": 1,
                    "model": 1, "metrics": 1, "_id": 0,
                },
            )
            .sort("timestamp", -1)
            .skip(offset)
            .limit(limit)
        )
        results: list[TraceSummary] = []
        for record in cursor:
            try:
                # Reconstruct a minimal AuditTrace-like object via full load
                full = self.load(record["trace_id"])
                if full is not None:
                    results.append(TraceSummary.from_trace(full))
            except Exception:  # noqa: BLE001
                continue
        return results

    def delete(self, trace_id: str) -> bool:
        return self._col.delete_one(self._filter(trace_id)).deleted_count > 0

    def delete_all(self) -> None:
        """Remove every trace for this project."""
        self._col.delete_many(self._filter())
