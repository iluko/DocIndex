"""Local file-based audit trace store.

Layout:
    data/traces/{project}/{YYYYMMDDTHHMMSS}_{trace_id}.json

Each query produces one JSON file. Listing reads files in reverse-chronological
order (newest first) by sorting on the timestamp-prefixed filename.
"""

from __future__ import annotations

from pathlib import Path

from traces.schema import AuditTrace, TraceSummary


class LocalTraceStore:
    """Store audit traces as individual JSON files under data/traces/{project}/."""

    def __init__(self, project: str, base_data_dir: str | Path) -> None:
        self._project = project
        self._dir = Path(base_data_dir) / "traces" / project
        self._dir.mkdir(parents=True, exist_ok=True)

    def _filename(self, trace: AuditTrace) -> Path:
        ts = trace.timestamp.strftime("%Y%m%dT%H%M%S")
        return self._dir / f"{ts}_{trace.trace_id}.json"

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def save(self, trace: AuditTrace) -> None:
        path = self._filename(trace)
        path.write_text(trace.model_dump_json(indent=2), encoding="utf-8")

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def load(self, trace_id: str) -> AuditTrace | None:
        """Load a full trace by its UUID.  Searches by filename suffix first."""
        # Fast path: filename ends with _{trace_id}.json
        matches = list(self._dir.glob(f"*_{trace_id}.json"))
        if matches:
            return AuditTrace.model_validate_json(matches[0].read_text(encoding="utf-8"))
        return None

    def list_summaries(self, limit: int = 50, offset: int = 0) -> list[TraceSummary]:
        """Return lightweight summary rows, newest first."""
        files = sorted(self._dir.glob("*.json"), reverse=True)
        results: list[TraceSummary] = []
        for path in files[offset : offset + limit]:
            try:
                trace = AuditTrace.model_validate_json(path.read_text(encoding="utf-8"))
                results.append(TraceSummary.from_trace(trace))
            except Exception:  # noqa: BLE001 — skip corrupt files
                continue
        return results

    def delete(self, trace_id: str) -> bool:
        matches = list(self._dir.glob(f"*_{trace_id}.json"))
        if not matches:
            return False
        matches[0].unlink()
        return True

    def delete_all(self) -> None:
        """Remove every trace file for this project."""
        for path in self._dir.glob("*.json"):
            path.unlink()
