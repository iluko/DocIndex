"""Section-level routing inside one already-selected document tree."""

from __future__ import annotations

import logging

from dotenv import load_dotenv

from utils import (
    ConversationContext,
    collect_node_ids,
    create_chat_completion_async,
    extract_llm_text,
    get_async_client,
    get_default_model,
    iter_tree_nodes,
    parse_json_response,
    render_conversation_context,
)

load_dotenv()

logger = logging.getLogger(__name__)

_STANDARD_MAX_NODES = 3
"""Default node selection limit used in standard (non-advanced) mode."""


def _format_tree_for_navigation(per_doc_tree: dict) -> str:
    """Render the document tree as a flat, readable node list for the LLM.

    Rather than dumping raw nested JSON (which buries summaries inside objects),
    we emit one entry per node in a consistent text format so the LLM can scan
    all nodes and their summaries without having to parse JSON structure.
    """
    lines: list[str] = []
    for node in iter_tree_nodes(per_doc_tree):
        node_id = node.get("node_id", "")
        title = node.get("title", "")
        start = node.get("start_index", "")
        end = node.get("end_index", "")
        summary = (
            node.get("summary")
            or node.get("prefix_summary")
            or ""
        )

        page_info = f" [pages {start}–{end}]" if start and end else ""
        lines.append(f'Node {node_id}: "{title}"{page_info}')
        if summary:
            # Cap at 220 chars so a large document doesn't flood the prompt.
            preview = summary.strip()[:220]
            if len(summary.strip()) > 220:
                preview += "…"
            lines.append(f"  → {preview}")

    return "\n".join(lines)


async def _chat_completion(
    model: str,
    system_prompt: str,
    user_prompt: str,
    reasoning_effort: str | None = None,
) -> str:
    """Run the navigator's LLM call and return plain text content."""
    client = get_async_client()
    response = await create_chat_completion_async(
        client=client,
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0,
        reasoning_effort=reasoning_effort,
    )
    return extract_llm_text(response)


async def navigate_doc_tree(
    query: str,
    doc_id: str,
    per_doc_tree: dict,
    model: str | None = None,
    conversation_context: ConversationContext = None,
    reasoning_effort: str | None = None,
    max_nodes: int = _STANDARD_MAX_NODES,
) -> list[str]:
    """Select the most relevant node refs inside one per-document PageIndex tree.

    Returns a list of ``doc_id::node_id`` strings ordered by relevance.
    Returns an empty list when no valid nodes can be identified (caller should
    fall back to the document's pre-computed top_sections).

    Parameters
    ----------
    max_nodes:
        Upper bound on nodes to select.  Standard mode uses 3.  Advanced mode
        may pass a higher value (up to ``MAX_NODES_CAP``) based on the planner's
        recommendation.
    """
    model = model or get_default_model()
    max_nodes = max(1, max_nodes)
    valid_node_ids = collect_node_ids(per_doc_tree)
    if not valid_node_ids:
        return []

    conversation_block = render_conversation_context(conversation_context)
    node_list = _format_tree_for_navigation(per_doc_tree)

    system_prompt = f"""
You are a document navigation agent. You are given a flat list of every section
(node) in a single document. Each entry shows the node ID, section title, page
range, and a short summary of the section's content.

Your task: identify the 1–{max_nodes} nodes most likely to contain the answer to the query.
Return a JSON array of node_id strings ordered by relevance, e.g. ["0003", "0007"].
Return only the node_id values — no doc_id prefix, no markdown, no explanation.
If no node is relevant, return an empty array: []
""".strip()

    user_prompt = f"""
Document: {doc_id}
Query: {query}

{conversation_block}

Document sections:
{node_list}

Return JSON array of node_ids only.
""".strip()

    response = await _chat_completion(
        model,
        system_prompt,
        user_prompt,
        reasoning_effort=reasoning_effort,
    )
    payload = parse_json_response(response)
    if not isinstance(payload, list):
        raise ValueError("Navigator response was not a JSON array.")

    node_refs: list[str] = []
    for node_id in payload[:max_nodes]:
        if str(node_id) not in valid_node_ids:
            # We warn and skip instead of failing hard because LLM navigation is
            # probabilistic and occasionally returns a near-miss node id.
            logger.warning(
                "Skipping unknown node_id '%s' returned for doc '%s'.",
                node_id,
                doc_id,
            )
            continue
        node_ref = f"{doc_id}::{node_id}"
        if node_ref not in node_refs:
            node_refs.append(node_ref)

    return node_refs
