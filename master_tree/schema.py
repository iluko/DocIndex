"""Canonical Pydantic models used throughout the multi-document index."""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator


class TopSection(BaseModel):
    """A routing-friendly pointer to an important section inside one document."""

    title: str
    node_ref: str
    section_summary: str


class RelevanceHints(BaseModel):
    """Short natural-language hints that help the router choose this document."""

    best_for: str
    not_useful_for: str
    # Domain-agnostic name for relevant concept areas / tags in this document.
    # Accepts the legacy field name ``key_modules`` from pre-existing JSON so
    # already-ingested indexes do not need to be rebuilt after the rename.
    key_categories: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _migrate_key_modules(cls, values: dict) -> dict:
        """Accept the legacy ``key_modules`` key and promote it to ``key_categories``.

        This runs before field assignment so both old on-disk JSON and new
        ingestion paths work without any data migration step.
        """
        if isinstance(values, dict) and "key_modules" in values and "key_categories" not in values:
            values["key_categories"] = values.pop("key_modules")
        return values


class MasterNode(BaseModel):
    """The cross-document metadata record produced for one ingested document."""

    doc_id: str
    doc_title: str
    doc_type: str
    file_path: str
    tree_path: str
    doc_summary: str
    key_topics: list[str]
    relevance_hints: RelevanceHints
    top_sections: list[TopSection]
    related_docs: list[str]
    ingested_at: str


class MasterTree(BaseModel):
    """The flat list of all ingested documents used for document-level routing."""

    version: str = "1.0"
    docs: list[MasterNode] = Field(default_factory=list)
