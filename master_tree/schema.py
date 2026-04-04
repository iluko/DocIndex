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


class RoutingFacets(BaseModel):
    """Structured routing signals that improve recall for broad/process queries.

    These are short phrase lists — not prose — extracted at ingestion time.
    They help the router identify documents for workflow, actor, or system queries
    that may not surface clearly from the prose summary alone.

    Old master-tree records without this field will load with ``None`` and the
    router degrades gracefully to the existing summary/hint fields.
    """

    workflows: list[str] = Field(
        default_factory=list,
        description="Named workflows or processes covered by this document.",
    )
    actors: list[str] = Field(
        default_factory=list,
        description="Roles, teams, stakeholders, or actors mentioned.",
    )
    systems: list[str] = Field(
        default_factory=list,
        description="Major systems, modules, services, or entities discussed.",
    )
    edge_cases: list[str] = Field(
        default_factory=list,
        description="Non-happy-path coverage: error conditions, exceptions, failure modes.",
    )
    authority_hints: list[str] = Field(
        default_factory=list,
        description="Scope, version, or authority qualifiers (e.g. 'v2 only', 'EMEA region').",
    )


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
    routing_facets: RoutingFacets | None = None
    """Optional structured routing facets. None for nodes ingested before v1.1."""


class MasterTree(BaseModel):
    """The flat list of all ingested documents used for document-level routing."""

    version: str = "1.0"
    docs: list[MasterNode] = Field(default_factory=list)
