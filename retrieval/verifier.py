"""Post-navigation self-correction pass.

After the navigator picks node IDs, this module runs a single, batched LLM call
per document that asks: "Does each of these sections actually address the query?"

Nodes that score false are dropped. If the verifier rejects *every* node for a
document, the top-1 original pick is kept as a safety net — we'd rather return
something than nothing.

Design choices:
- One LLM call per document (not per node) — keeps latency low.
- Uses node summaries, never full text — keeps token cost negligible.
- Run concurrently across documents via asyncio.gather in query_engine.
- Can be disabled via NAVIGATOR_VERIFICATION=false (default) for latency-
  sensitive deployments.
"""

from __future__ import annotations

import asyncio
import logging

from utils import (
    create_chat_completion_async,
    extract_llm_text,
    find_tree_node,
    get_async_client,
    get_default_model,
    managed_async_client,
    parse_json_response,
)

logger = logging.getLogger(__name__)


def _build_verification_prompt(
    query: str,
    candidates: list[tuple[str, str, str]],  # (node_id, title, summary)
) -> tuple[str, str]:
    """Build the system and user prompts for one verification batch."""

    system_prompt = (
        "You are a relevance verification assistant. "
        "You will be given a user query and a list of document sections "
        "(each with an ID, title, and short summary). "
        "For EACH section decide: does this section likely contain information "
        "that directly helps answer the query? "
        'Return ONLY a JSON object mapping each node_id to true or false. '
        'Example: {"0003": true, "0017": false}. '
        "No explanation, no markdown."
    )

    section_lines = []
    for node_id, title, summary in candidates:
        summary_part = f"\n   Summary: {summary.strip()[:300]}" if summary.strip() else ""
        section_lines.append(f'- node_id "{node_id}": "{title}"{summary_part}')

    sections_block = "\n".join(section_lines)
    user_prompt = (
        f"Query: {query}\n\n"
        f"Sections to verify:\n{sections_block}\n\n"
        "Return the JSON relevance map."
    )

    return system_prompt, user_prompt


async def verify_navigation(
    query: str,
    doc_id: str,
    node_refs: list[str],
    per_doc_tree: dict,
    model: str | None = None,
    reasoning_effort: str | None = None,
) -> list[str]:
    """Filter navigator picks for one document to those that address the query.

    Parameters
    ----------
    query:
        The original user query.
    doc_id:
        The document these node_refs belong to.
    node_refs:
        ``doc_id::node_id`` strings returned by the navigator.
    per_doc_tree:
        The full PageIndex tree for this document (used to pull summaries).
    model:
        LLM model name; defaults to the globally configured model.
    reasoning_effort:
        Optional reasoning effort hint passed to the LLM.

    Returns
    -------
    list[str]
        Filtered node_refs. Guaranteed non-empty when ``node_refs`` is
        non-empty — at minimum the first original pick is kept.
    """
    if not node_refs:
        return []

    model = model or get_default_model()

    # Build (node_id, title, summary) tuples from the tree.
    candidates: list[tuple[str, str, str]] = []
    for node_ref in node_refs:
        if "::" not in node_ref:
            continue
        _, node_id = node_ref.split("::", 1)
        node = find_tree_node(per_doc_tree, node_id)
        if node is None:
            # Already validated upstream; treat missing as unverifiable, keep it.
            logger.debug(
                "Verifier: node '%s' not found in tree for '%s', keeping.",
                node_id,
                doc_id,
            )
            candidates.append((node_id, node_id, ""))
            continue
        title = node.get("title") or node_id
        summary = node.get("summary") or node.get("prefix_summary") or ""
        candidates.append((node_id, title, summary))

    if not candidates:
        return node_refs

    system_prompt, user_prompt = _build_verification_prompt(query, candidates)

    try:
        async with managed_async_client(get_async_client()) as client:
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
        raw = extract_llm_text(response)
        verdict_map: dict = parse_json_response(raw)
    except Exception as exc:  # noqa: BLE001
        # Verification is a best-effort enhancement. Any failure degrades
        # gracefully to keeping all navigator picks.
        logger.warning(
            "Verifier LLM call failed for doc '%s' — keeping all picks. Error: %s",
            doc_id,
            exc,
        )
        return node_refs

    if not isinstance(verdict_map, dict):
        logger.warning(
            "Verifier returned non-dict for doc '%s' — keeping all picks.",
            doc_id,
        )
        return node_refs

    kept: list[str] = []
    rejected: list[str] = []
    for node_ref in node_refs:
        if "::" not in node_ref:
            kept.append(node_ref)
            continue
        _, node_id = node_ref.split("::", 1)
        verdict = verdict_map.get(node_id)
        # Treat missing verdict as true — prefer false negatives over false positives
        # in the "keep or drop" direction.
        if verdict is False:
            rejected.append(node_ref)
        else:
            kept.append(node_ref)

    if kept:
        if rejected:
            logger.info(
                "Verifier dropped %d/%d node(s) for '%s': %s",
                len(rejected),
                len(node_refs),
                doc_id,
                rejected,
            )
        return kept

    # All nodes rejected — safety net: keep the first original pick to ensure
    # the fetcher still has something to work with.
    logger.info(
        "Verifier rejected ALL nodes for '%s'; keeping top pick '%s' as fallback.",
        doc_id,
        node_refs[0],
    )
    return node_refs[:1]


async def verify_navigation_batch(
    query: str,
    navigation_map: dict[str, list[str]],
    storage: "DocumentStore",  # type: ignore[name-defined]  # noqa: F821
    model: str | None = None,
    reasoning_effort: str | None = None,
) -> dict[str, list[str]]:
    """Run verify_navigation concurrently for every document in navigation_map.

    Parameters
    ----------
    query:
        The original user query.
    navigation_map:
        ``{doc_id: [node_refs]}`` from the navigation step.
    storage:
        DocumentStore used to load per-doc trees.
    model / reasoning_effort:
        Passed through to each per-document verifier call.

    Returns
    -------
    dict[str, list[str]]
        Verified (filtered) navigation map with the same shape as the input.
    """
    from storage.store import DocumentStore  # local import to avoid circularity

    async def _verify_one(doc_id: str, refs: list[str]) -> tuple[str, list[str]]:
        if not refs:
            return doc_id, refs
        try:
            tree = storage.load_doc_tree(doc_id)
        except FileNotFoundError:
            logger.warning(
                "Verifier: tree not found for '%s', skipping verification.", doc_id
            )
            return doc_id, refs

        verified = await verify_navigation(
            query=query,
            doc_id=doc_id,
            node_refs=refs,
            per_doc_tree=tree,
            model=model,
            reasoning_effort=reasoning_effort,
        )
        return doc_id, verified

    results = await asyncio.gather(
        *[_verify_one(doc_id, refs) for doc_id, refs in navigation_map.items()]
    )
    return dict(results)
