"""Query-intent planning stage for the advanced retrieval pipeline.

When advanced retrieval is enabled, the planner runs before routing and
classifies the query into a small controlled set of intent types.  It then
produces bounded retrieval-strategy hints that the router and navigator can
use to widen or narrow their selections.

This stage is:
- Optional and disabled by default.
- One extra lightweight LLM call (no tools, low token cost).
- Gracefully degraded: if it fails for any reason the pipeline continues
  with standard defaults.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from dotenv import load_dotenv

from utils import (
    create_chat_completion_async,
    extract_llm_text,
    get_async_client,
    get_default_model,
    managed_async_client,
    parse_json_response,
)

load_dotenv()

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

QUERY_TYPES = frozenset(
    {
        "fact_lookup",
        "compare",
        "workflow_or_process",
        "policy_or_compliance",
        "troubleshooting",
        "architecture_or_design",
    }
)

_SYSTEM_PROMPT = """
You are a query-intent classifier for a multi-document retrieval system.
Given a user query, output a JSON object with the following fields:

{
  "query_type": "<one of: fact_lookup | compare | workflow_or_process | policy_or_compliance | troubleshooting | architecture_or_design>",
  "is_broad": <true if the query spans multiple topics/documents/processes, false if narrow/specific>,
  "recommended_max_docs": <integer 1-6, how many documents are likely needed>,
  "recommended_max_nodes": <integer 1-6, how many sections per document are likely needed>,
  "use_node_expansion": <true if surrounding context around selected sections is important>
}

Guidelines:
- fact_lookup: single specific fact, date, value, or definition. Narrow, 1 doc, 1-2 nodes.
- compare: comparing two or more items, documents, or approaches. 2-4 docs, 2-3 nodes each.
- workflow_or_process: end-to-end procedures, steps, flows. Often broad, 2-4 docs, 2-4 nodes, expand=true.
- policy_or_compliance: rules, regulations, requirements. 1-3 docs, 2-4 nodes.
- troubleshooting: diagnosing errors, failures, or exceptions. 1-3 docs, 2-4 nodes, expand=true.
- architecture_or_design: system design, component relationships, data flows. Often broad, 2-4 docs, 3-5 nodes, expand=true.

Output only valid JSON. No markdown. No explanation.
""".strip()


# ── Data model ────────────────────────────────────────────────────────────────


@dataclass
class QueryPlan:
    """Structured retrieval strategy produced by the planner for one query."""

    query_type: str
    """One of the QUERY_TYPES constants."""

    is_broad: bool
    """True when the query is expected to span multiple documents or topics."""

    recommended_max_docs: int
    """Planner's recommended upper bound on documents to route."""

    recommended_max_nodes: int
    """Planner's recommended upper bound on nodes to navigate per document."""

    use_node_expansion: bool
    """Whether bounded neighborhood expansion around selected nodes is advised."""

    raw_response: str = field(default="", repr=False)
    """The raw LLM response, kept for traceability."""


# ── Implementation ────────────────────────────────────────────────────────────


async def _chat_completion(model: str, user_prompt: str) -> str:
    """Run the planner LLM call — no reasoning effort needed, keep it fast."""
    async with managed_async_client(get_async_client()) as client:
        response = await create_chat_completion_async(
            client=client,
            model=model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0,
            reasoning_effort=None,
        )
    return extract_llm_text(response)


def _parse_plan(raw: str, hard_max_docs: int, hard_max_nodes: int) -> QueryPlan:
    """Parse and validate the LLM response into a QueryPlan.

    All integer fields are clamped to [1, hard_max] so the plan can never
    exceed the caller's configured caps regardless of what the LLM returns.
    """
    payload = parse_json_response(raw)
    if not isinstance(payload, dict):
        raise ValueError("Planner response was not a JSON object.")

    query_type = str(payload.get("query_type", "fact_lookup")).strip()
    if query_type not in QUERY_TYPES:
        logger.warning(
            "Planner returned unknown query_type '%s'; defaulting to 'fact_lookup'.",
            query_type,
        )
        query_type = "fact_lookup"

    def _clamp(key: str, default: int, lo: int, hi: int) -> int:
        try:
            return max(lo, min(hi, int(payload.get(key, default))))
        except (TypeError, ValueError):
            return default

    return QueryPlan(
        query_type=query_type,
        is_broad=bool(payload.get("is_broad", False)),
        recommended_max_docs=_clamp("recommended_max_docs", 2, 1, hard_max_docs),
        recommended_max_nodes=_clamp("recommended_max_nodes", 3, 1, hard_max_nodes),
        use_node_expansion=bool(payload.get("use_node_expansion", False)),
        raw_response=raw,
    )


async def plan_query(
    query: str,
    model: str | None = None,
    hard_max_docs: int = 6,
    hard_max_nodes: int = 6,
) -> QueryPlan | None:
    """Classify the query and return a retrieval strategy plan.

    Returns ``None`` on any failure so the caller can fall back to standard
    defaults without disrupting the query pipeline.

    Parameters
    ----------
    query:
        The raw user query string.
    model:
        LLM model name.  Defaults to the system default.
    hard_max_docs:
        Hard cap applied to ``recommended_max_docs`` in the returned plan.
    hard_max_nodes:
        Hard cap applied to ``recommended_max_nodes`` in the returned plan.
    """
    model = model or get_default_model()
    user_prompt = f"Query: {query}"
    try:
        raw = await _chat_completion(model, user_prompt)
        plan = _parse_plan(raw, hard_max_docs, hard_max_nodes)
        logger.debug(
            "Planner classified query as '%s' (broad=%s, docs=%d, nodes=%d, expand=%s).",
            plan.query_type,
            plan.is_broad,
            plan.recommended_max_docs,
            plan.recommended_max_nodes,
            plan.use_node_expansion,
        )
        return plan
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Query planner failed; falling back to standard defaults. Reason: %s", exc
        )
        return None
