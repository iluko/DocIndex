"""Load and compact the domain context map that widens query routing scope.

The domain context map is an optional JSON file that describes the key concepts,
areas, or entities in your knowledge domain and how they relate to each other.
The router receives this map so it can widen its document selection when a query
touches concepts that span multiple documents.

Schema:
  {
    "description": "Brief description of the domain",
    "entities": [
      {
        "name": "Concept Name",
        "connects_to": ["Other Concept", "..."],
        "description": "What this concept covers"
      }
    ]
  }

The file is optional. When absent or empty the router works without it.
"""

from __future__ import annotations

import json
from pathlib import Path

from utils import compact_json


class ArchitectureMap:
    """Read the domain context map JSON and expose it in LLM-friendly form.

    The class name is kept as ``ArchitectureMap`` for import compatibility.
    Externally it represents any domain context map, not just software architecture.
    """

    def __init__(self, arch_map_path: str):
        """Load the domain context map immediately so later reads are cheap."""
        self.arch_map_path = Path(arch_map_path)
        self.data = self._load()

    def _load(self) -> dict:
        """Load JSON safely and degrade to an empty map on missing/bad files."""
        if not self.arch_map_path.exists():
            return {}

        try:
            return json.loads(self.arch_map_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}

    def to_llm_context(self) -> str:
        """Return a compact JSON string containing only the routing-relevant fields.

        Returns an empty string when the map is empty or has no entities so
        callers can gate on truthiness without special-casing None.
        """
        if not self.data:
            return ""

        entities = self.data.get("entities") or []
        if not entities:
            return ""

        compact_entities = [
            {
                "name": entity.get("name", ""),
                "connects_to": entity.get("connects_to", []),
                "description": entity.get("description", ""),
            }
            for entity in entities
        ]

        return compact_json(
            {
                "description": self.data.get("description", ""),
                "entities": compact_entities,
            }
        )
