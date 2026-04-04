"""Relationship maintenance helpers for the master tree.

Kept separate from prompt-generation and storage code so each layer can be
tested and reasoned about independently.

Three maintenance levels are available at ingestion time:

    off       No post-generation reconciliation.  Behavior is closest to the
              original implementation.

    basic     Deterministic reconciliation over the local neighborhood (the new
              doc, docs it references, and docs that already reference it).
              Enforces no self-links, deduplication, bounded list length, and
              symmetric direct relationships.  Zero extra LLM calls.

    enhanced  Adds a bounded LLM-assisted pass.  Candidate neighbors are scored
              cheaply via structured metadata overlap (key topics, categories,
              routing facets), then a focused LLM call refines the final
              related_docs set for the new document.  Basic reconciliation is
              applied afterward for symmetry and cleanup.  Falls back to basic
              on any LLM failure.

Future levels (not implemented here):

    Level 3   Query-time graph-aware expansion using related_docs as 1-hop
              candidates.  Separate query-time feature; not bundled with
              ingestion-time relationship building.

    Level 4   Edge semantics (relatedness strength, complement / dependency /
              overlap types, authority preferences).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from master_tree.schema import MasterNode

logger = logging.getLogger(__name__)

# ── Public constants ───────────────────────────────────────────────────────────

RELATIONSHIP_MODES: frozenset[str] = frozenset({"off", "basic", "enhanced"})

# Hard cap on how many related_docs any single node may hold after reconciliation.
RELATED_DOCS_MAX_PER_NODE: int = 10

# How many candidates to compute during enhanced-mode candidate selection.
_ENHANCED_CANDIDATE_SHORTLIST_SIZE: int = 8

# How many candidates to show the LLM (must be ≤ shortlist size).
_ENHANCED_PROMPT_MAX_CANDIDATES: int = 6


# ── Trace dataclass ───────────────────────────────────────────────────────────


@dataclass
class ReconciliationResult:
    """Trace of what the relationship reconciliation step did.

    Included in the ingestion trace so operators can inspect before/after states
    and verify that the chosen mode ran as expected.
    """

    mode: str
    """The relationship maintenance mode that was requested (off/basic/enhanced)."""

    ran: bool
    """Whether reconciliation actually executed (False when new_doc_id was not found)."""

    affected_doc_ids: list[str] = field(default_factory=list)
    """Doc IDs in the local neighborhood that were inspected and potentially modified."""

    before: dict[str, list[str]] = field(default_factory=dict)
    """related_docs for each affected doc *before* reconciliation."""

    after: dict[str, list[str]] = field(default_factory=dict)
    """related_docs for each affected doc *after* reconciliation."""

    candidate_neighbors: list[str] = field(default_factory=list)
    """Candidate shortlist computed in enhanced mode (empty for basic/off)."""

    enhanced_ran: bool = False
    """Whether the enhanced LLM pass executed successfully."""

    fallback_used: bool = False
    """Whether enhanced mode fell back to basic because the LLM call failed."""

    def to_dict(self) -> dict:
        """Serialize to a plain dict suitable for trace payloads and JSON output."""
        return {
            "mode": self.mode,
            "ran": self.ran,
            "affected_doc_ids": self.affected_doc_ids,
            "before": self.before,
            "after": self.after,
            "candidate_neighbors": self.candidate_neighbors,
            "enhanced_ran": self.enhanced_ran,
            "fallback_used": self.fallback_used,
        }


# ── Private helpers ────────────────────────────────────────────────────────────


def _clean_related_docs(
    doc_id: str,
    related: list[str],
    all_doc_ids: set[str],
    max_related: int = RELATED_DOCS_MAX_PER_NODE,
) -> list[str]:
    """Remove self-links and unknown docs, deduplicate, and enforce the bound.

    Preserves the original ordering; the first occurrence of each doc_id wins
    when deduplicating.
    """
    seen: set[str] = set()
    result: list[str] = []
    for r in related:
        if r == doc_id or r in seen or r not in all_doc_ids:
            continue
        seen.add(r)
        result.append(r)
        if len(result) >= max_related:
            break
    return result


def _find_incoming_links(
    new_doc_id: str,
    nodes_by_id: dict[str, "MasterNode"],
) -> list[str]:
    """Return the doc_ids of nodes whose related_docs currently include ``new_doc_id``."""
    return [
        doc_id
        for doc_id, node in nodes_by_id.items()
        if doc_id != new_doc_id and new_doc_id in node.related_docs
    ]


def _compute_overlap_score(
    new_node: "MasterNode",
    candidate: "MasterNode",
) -> float:
    """Score the metadata overlap between two nodes for candidate ranking.

    Entirely deterministic — no LLM calls.  A higher score means stronger
    evidence that the documents are related.

    Scoring weights
    ---------------
    key_topics intersection         1.0 per shared term
    key_categories intersection     1.0 per shared category
    routing_facet intersection      0.5 per shared phrase (workflows/systems/actors)
    existing link in either dir.    +2.0 bonus
    """
    score: float = 0.0

    # Key-topic overlap (exact match, case-normalised)
    new_topics = {t.lower() for t in new_node.key_topics}
    cand_topics = {t.lower() for t in candidate.key_topics}
    score += len(new_topics & cand_topics)

    # Category overlap
    new_cats = {c.lower() for c in new_node.relevance_hints.key_categories}
    cand_cats = {c.lower() for c in candidate.relevance_hints.key_categories}
    score += len(new_cats & cand_cats)

    # Routing-facet overlap (weighted lower than explicit topics)
    if new_node.routing_facets and candidate.routing_facets:
        for facet_name in ("workflows", "systems", "actors"):
            nf = {v.lower() for v in getattr(new_node.routing_facets, facet_name)}
            cf = {v.lower() for v in getattr(candidate.routing_facets, facet_name)}
            score += 0.5 * len(nf & cf)

    # Existing-link bonus in either direction
    if (
        candidate.doc_id in new_node.related_docs
        or new_node.doc_id in candidate.related_docs
    ):
        score += 2.0

    return score


def _select_candidate_neighbors(
    new_doc_id: str,
    new_node: "MasterNode",
    all_nodes: list["MasterNode"],
    shortlist_size: int = _ENHANCED_CANDIDATE_SHORTLIST_SIZE,
) -> list[str]:
    """Return a bounded shortlist of candidate neighbors ranked by overlap score.

    Only nodes with a positive score are included.  The shortlist is sorted
    highest-first and capped at ``shortlist_size``.
    """
    scored: list[tuple[str, float]] = []
    for node in all_nodes:
        if node.doc_id == new_doc_id:
            continue
        s = _compute_overlap_score(new_node, node)
        if s > 0:
            scored.append((node.doc_id, s))
    scored.sort(key=lambda x: x[1], reverse=True)
    return [doc_id for doc_id, _ in scored[:shortlist_size]]


# ── LLM helper (module-level so tests can monkeypatch it) ─────────────────────


async def _chat_completion(model: str, system_prompt: str, user_prompt: str) -> str:
    """Run the relationship-refinement LLM call at ingestion-grade settings."""
    from utils import (
        INGESTION_REASONING_EFFORT,
        create_chat_completion_async,
        extract_llm_text,
        get_async_client,
    )

    client = get_async_client()
    response = await create_chat_completion_async(
        client=client,
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0,
        reasoning_effort=INGESTION_REASONING_EFFORT,
    )
    return extract_llm_text(response)


async def _refine_related_docs_with_llm(
    new_doc_id: str,
    new_node: "MasterNode",
    candidate_ids: list[str],
    nodes_by_id: dict[str, "MasterNode"],
    model: str,
    max_related: int,
) -> list[str] | None:
    """Use an LLM to select which candidates should be related to ``new_doc_id``.

    Returns the selected doc_id list (a subset of ``candidate_ids``), or
    ``None`` if the LLM call fails or the response cannot be parsed.  The
    caller is responsible for running basic reconciliation afterward.
    """
    from utils import parse_json_response

    if not candidate_ids:
        return []

    shown = candidate_ids[:_ENHANCED_PROMPT_MAX_CANDIDATES]
    new_summary = {
        "doc_id": new_doc_id,
        "doc_summary": new_node.doc_summary,
        "key_topics": new_node.key_topics,
        "key_categories": new_node.relevance_hints.key_categories,
    }
    candidate_summaries = [
        {
            "doc_id": doc_id,
            "doc_summary": nodes_by_id[doc_id].doc_summary,
            "key_topics": nodes_by_id[doc_id].key_topics,
            "key_categories": nodes_by_id[doc_id].relevance_hints.key_categories,
        }
        for doc_id in shown
        if doc_id in nodes_by_id
    ]

    system_prompt = (
        "You are a document relationship analyst. "
        "Identify which candidate documents are meaningfully related to the new document. "
        "Output only a JSON array of doc_id strings — no other text."
    )
    user_prompt = (
        f"New document:\n{json.dumps(new_summary, ensure_ascii=False)}\n\n"
        f"Candidate documents:\n{json.dumps(candidate_summaries, indent=2, ensure_ascii=False)}\n\n"
        f"Which candidates are meaningfully related to the new document?\n"
        f"A document is related if it shares important topics, complements the new document,\n"
        f"or should typically be consulted together with it.\n"
        f"Return at most {max_related} doc_ids.\n"
        f"Output only the JSON array."
    )

    try:
        raw = await _chat_completion(
            model=model, system_prompt=system_prompt, user_prompt=user_prompt
        )
        parsed = parse_json_response(raw)
        if not isinstance(parsed, list):
            logger.warning(
                "Enhanced LLM refinement for %s returned non-list: %r", new_doc_id, parsed
            )
            return None
        valid_ids = set(shown)
        return [
            doc_id
            for doc_id in parsed
            if isinstance(doc_id, str) and doc_id in valid_ids
        ][:max_related]
    except Exception as exc:
        logger.warning(
            "Enhanced relationship refinement LLM call failed for %s: %s", new_doc_id, exc
        )
        return None


# ── Public reconciliation functions ───────────────────────────────────────────


def reconcile_relationships_basic(
    new_doc_id: str,
    nodes: list["MasterNode"],
    max_related: int = RELATED_DOCS_MAX_PER_NODE,
) -> tuple[list["MasterNode"], ReconciliationResult]:
    """Deterministically reconcile ``related_docs`` for the new doc and its neighbors.

    The local neighborhood is defined as:
    - the new doc itself
    - docs the new doc lists in its ``related_docs`` (outgoing)
    - docs that currently list the new doc in their ``related_docs`` (incoming)

    Rules applied to every node in the neighborhood:
    - No self-references
    - No references to doc_ids that are not in the master tree
    - Deduplication (first occurrence wins)
    - Bounded list length (``max_related``)
    - Symmetry: if A→B, then B→A is added when capacity allows

    This function is a pure function — it does not mutate its inputs.  The
    caller should upsert the returned affected nodes back into the master-tree
    store and then call ``save()``.
    """
    nodes_by_id: dict[str, MasterNode] = {n.doc_id: n for n in nodes}  # type: ignore[assignment]
    all_doc_ids: set[str] = set(nodes_by_id.keys())

    new_node = nodes_by_id.get(new_doc_id)
    if new_node is None:
        return [], ReconciliationResult(mode="basic", ran=False)

    # Identify the affected neighborhood
    outgoing: list[str] = [
        d for d in new_node.related_docs if d in all_doc_ids and d != new_doc_id
    ]
    incoming: list[str] = _find_incoming_links(new_doc_id, nodes_by_id)
    affected_ids: set[str] = {new_doc_id} | set(outgoing) | set(incoming)

    # Capture before-state for the trace
    before: dict[str, list[str]] = {
        doc_id: list(nodes_by_id[doc_id].related_docs) for doc_id in affected_ids
    }

    # Work on mutable copies; never mutate the input nodes directly
    updated: dict[str, MasterNode] = dict(nodes_by_id)  # type: ignore[assignment]

    # ── Step 1: add incoming links to the new doc's list (symmetry for incoming) ──
    new_related: list[str] = list(updated[new_doc_id].related_docs)
    for inc in incoming:
        if inc not in new_related:
            new_related.append(inc)
    new_related = _clean_related_docs(new_doc_id, new_related, all_doc_ids, max_related)
    updated[new_doc_id] = updated[new_doc_id].model_copy(update={"related_docs": new_related})

    # ── Step 2: outgoing neighbors → add back-link to new doc (symmetry for outgoing) ──
    for neighbor_id in outgoing:
        neighbor = updated[neighbor_id]
        neighbor_related: list[str] = list(neighbor.related_docs)
        if new_doc_id not in neighbor_related:
            neighbor_related.append(new_doc_id)
        neighbor_related = _clean_related_docs(
            neighbor_id, neighbor_related, all_doc_ids, max_related
        )
        updated[neighbor_id] = neighbor.model_copy(update={"related_docs": neighbor_related})

    # ── Step 3: clean up incoming docs' lists (they already reference new_doc) ──
    for inc_id in incoming:
        neighbor = updated[inc_id]
        cleaned = _clean_related_docs(
            inc_id, list(neighbor.related_docs), all_doc_ids, max_related
        )
        if cleaned != list(neighbor.related_docs):
            updated[inc_id] = neighbor.model_copy(update={"related_docs": cleaned})

    # Capture after-state
    after: dict[str, list[str]] = {
        doc_id: list(updated[doc_id].related_docs) for doc_id in affected_ids
    }

    affected_nodes: list[MasterNode] = [  # type: ignore[type-arg]
        updated[doc_id] for doc_id in affected_ids
    ]
    return affected_nodes, ReconciliationResult(
        mode="basic",
        ran=True,
        affected_doc_ids=sorted(affected_ids),
        before=before,
        after=after,
    )


async def reconcile_relationships_enhanced(
    new_doc_id: str,
    nodes: list["MasterNode"],
    model: str,
    max_related: int = RELATED_DOCS_MAX_PER_NODE,
    shortlist_size: int = _ENHANCED_CANDIDATE_SHORTLIST_SIZE,
) -> tuple[list["MasterNode"], ReconciliationResult]:
    """Bounded LLM-assisted relationship refresh for the newly ingested document.

    Workflow
    --------
    1. Build a candidate shortlist from deterministic metadata-overlap scoring.
    2. Run a narrow LLM prompt to refine the new doc's related_docs from that
       shortlist.
    3. Apply basic reconciliation for symmetry and full neighborhood cleanup.

    Falls back to basic reconciliation transparently if the LLM call fails.
    The returned node list should be upserted into the master-tree store.
    """
    nodes_by_id: dict[str, MasterNode] = {n.doc_id: n for n in nodes}  # type: ignore[assignment]
    new_node = nodes_by_id.get(new_doc_id)

    if new_node is None:
        return [], ReconciliationResult(mode="enhanced", ran=False)

    all_doc_ids: set[str] = set(nodes_by_id.keys())

    # Step 1: deterministic candidate shortlist
    candidate_ids = _select_candidate_neighbors(new_doc_id, new_node, nodes, shortlist_size)

    # Step 2: LLM-based refinement
    llm_related = await _refine_related_docs_with_llm(
        new_doc_id=new_doc_id,
        new_node=new_node,
        candidate_ids=candidate_ids,
        nodes_by_id=nodes_by_id,
        model=model,
        max_related=max_related,
    )

    enhanced_ran = llm_related is not None

    if not enhanced_ran:
        # LLM call failed — fall back to basic
        logger.warning("Enhanced reconciliation falling back to basic for %s", new_doc_id)
        basic_nodes, basic_result = reconcile_relationships_basic(
            new_doc_id, nodes, max_related
        )
        return basic_nodes, ReconciliationResult(
            mode="enhanced",
            ran=True,
            affected_doc_ids=basic_result.affected_doc_ids,
            before=basic_result.before,
            after=basic_result.after,
            candidate_neighbors=candidate_ids,
            enhanced_ran=False,
            fallback_used=True,
        )

    # Step 3: Apply LLM result to the new node's related_docs
    updated = dict(nodes_by_id)
    new_related = _clean_related_docs(new_doc_id, list(llm_related), all_doc_ids, max_related)
    updated[new_doc_id] = updated[new_doc_id].model_copy(update={"related_docs": new_related})

    # Step 4: Apply basic reconciliation for symmetry and neighborhood cleanup
    updated_list = list(updated.values())
    basic_nodes, basic_result = reconcile_relationships_basic(
        new_doc_id, updated_list, max_related
    )

    # Use original pre-ingestion state as the "before" in the trace
    original_before: dict[str, list[str]] = {
        doc_id: list(nodes_by_id[doc_id].related_docs)
        for doc_id in basic_result.affected_doc_ids
        if doc_id in nodes_by_id
    }

    return basic_nodes, ReconciliationResult(
        mode="enhanced",
        ran=True,
        affected_doc_ids=basic_result.affected_doc_ids,
        before=original_before,
        after=basic_result.after,
        candidate_neighbors=candidate_ids,
        enhanced_ran=True,
        fallback_used=False,
    )
