"""Typed API contracts for the React frontend."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class CreateProjectRequest(BaseModel):
    name: str


class RegisterModelRequest(BaseModel):
    name: str


class RuntimeSelection(BaseModel):
    project: str
    model: str | None = None


class AdvancedRetrievalPayload(BaseModel):
    enabled: bool = False
    enable_planning: bool = True
    enable_adaptive_width: bool = True
    enable_node_expansion: bool = True
    max_docs_cap: int = 6
    max_nodes_cap: int = 6


class QueryRequest(BaseModel):
    project: str
    model: str | None = None
    user_query: str
    conversation_context: list[dict[str, Any]] | str | None = None
    max_docs: int = 3
    reasoning_effort: str | None = None
    retrieval_mode: Literal["hybrid", "pageindex"] = "hybrid"
    advanced_retrieval: AdvancedRetrievalPayload = Field(
        default_factory=AdvancedRetrievalPayload
    )
    retrieval_only: bool = False


class IngestionMetadata(BaseModel):
    project: str
    model: str | None = None
    doc_id: str
    doc_title: str
    doc_type: str
    top_sections_target: int | None = None
    relationship_mode: Literal["off", "basic", "enhanced"] = "basic"

