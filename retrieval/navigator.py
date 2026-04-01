"""Section-level routing inside one already-selected document tree."""

from __future__ import annotations

import logging

from dotenv import load_dotenv

from utils import (
    collect_node_ids,
    create_chat_completion_async,
    extract_llm_text,
    format_chat_history,
    get_async_client,
    get_default_model,
    iter_tree_nodes,
    parse_json_response,
)

load_dotenv()

logger = logging.getLogger(__name__)


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
    chat_history: list[dict] | None = None,
    reasoning_effort: str | None = None,
) -> list[str]:
    """Select the most relevant node refs inside one per-document PageIndex tree.

    Returns a list of ``doc_id::node_id`` strings ordered by relevance.
    Returns an empty list when no valid nodes can be identified (caller should
    fall back to the document's pre-computed top_sections).
    """
    model = model or get_default_model()
    valid_node_ids = collect_node_ids(per_doc_tree)
    if not valid_node_ids:
        return []

    chat_history_block = format_chat_history(chat_history)
    node_list = _format_tree_for_navigation(per_doc_tree)

    system_prompt = """
You are a document navigation agent. You are given a flat list of every section
(node) in a single document. Each entry shows the node ID, section title, page
range, and a short summary of the section's content.

Your task: identify the 1–3 nodes most likely to contain the answer to the query.
Return a JSON array of node_id strings ordered by relevance, e.g. ["0003", "0007"].
Return only the node_id values — no doc_id prefix, no markdown, no explanation.
If no node is relevant, return an empty array: []
""".strip()

    user_prompt = f"""
Document: {doc_id}
Query: {query}

{chat_history_block}

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
    for node_id in payload[:3]:
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
