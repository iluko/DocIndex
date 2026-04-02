"""Canonical Pydantic models used throughout the multi-document index."""

from __future__ import annotations

from pydantic import BaseModel, Field


class TopSection(BaseModel):
    """A routing-friendly pointer to an important section inside one document."""

    title: str
    node_ref: str
    section_summary: str


class RelevanceHints(BaseModel):
    """Short natural-language hints that help the router choose this document."""

    best_for: str
    not_useful_for: str
    key_categories: list[str] = Field(default_factory=list)


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
