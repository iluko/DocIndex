"""Generate the cross-document master-tree node for one ingested document."""

from __future__ import annotations

import json
from datetime import datetime

from dotenv import load_dotenv
from pydantic import ValidationError

from master_tree.schema import MasterNode, RelevanceHints, TopSection
from utils import (
    collect_node_ids,
    create_chat_completion_async,
    extract_llm_text,
    get_default_model,
    get_async_client,
    INGESTION_REASONING_EFFORT,
    iter_tree_nodes,
    get_master_top_sections_range,
    get_master_top_sections_target,
    parse_json_response,
    strip_text_fields,
)

load_dotenv()


SYSTEM_PROMPT = """
You are a document intelligence analyst. Your job is to generate a structured
metadata record for a document that will be added to a multi-document index.
The index is used to route user queries to the right document(s) before performing
detailed retrieval. Your output must be a JSON object conforming exactly to the
schema below. Output only valid JSON - no markdown fences, no preamble.

Schema:
{
  "doc_summary": "<3-5 sentence summary of what this document covers>",
  "key_topics": ["<topic1>", "<topic2>", "..."],
  "doc_type": "<type>",
  "relevance_hints": {
    "best_for": "<what queries this doc best answers>",
    "not_useful_for": "<what this doc does NOT cover>",
    "key_categories": ["<relevant category, concept area, or tag>", "..."]
  },
  "top_sections": [
    {
      "title": "<section title>",
      "node_ref": "<doc_id>::<node_id>",
      "section_summary": "<2-4 sentence summary>"
    }
  ],
  "related_docs": ["<doc_id>", "..."]
}
""".strip()


async def _chat_completion(model: str, system_prompt: str, user_prompt: str) -> str:
    """Run the master-node LLM call with ingestion-grade reasoning settings."""
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


def _existing_doc_ids(existing_master_tree: str) -> set[str]:
    """Extract known doc ids from the current master-tree prompt context."""
    if not existing_master_tree:
        return set()
    try:
        payload = parse_json_response(existing_master_tree)
    except json.JSONDecodeError:
        return set()
    docs = payload.get("docs", []) if isinstance(payload, dict) else []
    return {str(doc.get("doc_id")) for doc in docs if doc.get("doc_id")}


def _fallback_top_sections(
    doc_id: str,
    per_doc_tree: dict,
    *,
    top_sections_target: int,
) -> list[TopSection]:
    """Build a safe fallback when the LLM returns unusable top-section metadata."""
    fallback_sections: list[TopSection] = []
    _, max_sections = get_master_top_sections_range(top_sections_target)

    top_level_nodes = per_doc_tree.get("nodes", [])
    if not top_level_nodes:
        top_level_nodes = list(iter_tree_nodes(per_doc_tree))

    for node in top_level_nodes[:max_sections]:
        node_id = node.get("node_id")
        if not node_id:
            continue
        title = node.get("title", f"Section {node_id}")
        fallback_sections.append(
            TopSection(
                title=title,
                node_ref=f"{doc_id}::{node_id}",
                section_summary=f"Relevant section titled '{title}'.",
            )
        )

    return fallback_sections[:max_sections]


def _normalize_top_sections(
    doc_id: str,
    per_doc_tree: dict,
    top_sections_payload: list[dict],
    *,
    top_sections_target: int,
) -> list[TopSection]:
    """Validate LLM-selected sections against the actual PageIndex node ids."""
    valid_node_ids = collect_node_ids(per_doc_tree)
    normalized: list[TopSection] = []
    _, max_sections = get_master_top_sections_range(top_sections_target)

    for section in top_sections_payload[:max_sections]:
        raw_node_ref = str(section.get("node_ref", "")).strip()
        node_id = raw_node_ref.split("::", 1)[-1] if raw_node_ref else ""
        if node_id not in valid_node_ids:
            continue

        normalized.append(
            TopSection(
                title=section.get("title", f"Section {node_id}"),
                node_ref=f"{doc_id}::{node_id}",
                section_summary=section.get(
                    "section_summary", f"Relevant section with node_id {node_id}."
                ),
            )
        )

    return normalized or _fallback_top_sections(
        doc_id,
        per_doc_tree,
        top_sections_target=top_sections_target,
    )


async def generate_master_node(
    doc_id: str,
    doc_title: str,
    doc_type: str,
    file_path: str,
    tree_path: str,
    per_doc_tree: dict,
    existing_master_tree: str,
    model: str | None = None,
    top_sections_target: int | None = None,
) -> MasterNode:
    """Create a `MasterNode` from a document tree plus existing corpus context.

    Junior note:
    The model is asked for JSON only, but we still validate and normalize its
    output because LLMs are not guaranteed to follow instructions perfectly.
    """
    model = model or get_default_model()
    top_sections_target = get_master_top_sections_target(top_sections_target)
    min_top_sections, max_top_sections = get_master_top_sections_range(
        top_sections_target
    )
    per_doc_tree_json = json.dumps(
        strip_text_fields(per_doc_tree), indent=2, ensure_ascii=False
    )

    user_prompt = f"""
You are adding the following document to a multi-document index.

Document ID: {doc_id}
Document Title: {doc_title}
Document Type: {doc_type}

## PageIndex Tree Structure (this document's full hierarchical index):
{per_doc_tree_json}

## Existing Master Tree (all documents already indexed):
{existing_master_tree}

Instructions:
1. Write doc_summary as 3-5 sentences that precisely characterise what this document
   covers. Be specific - avoid generic phrases like "covers important topics".
2. For key_topics, list the 8-12 most important concepts, entities, or themes.
3. For relevance_hints.best_for, write 2-4 sentences describing the exact type of
   query this document best answers. Be specific enough that an LLM can use this
   to decide whether to select this document.
4. For relevance_hints.not_useful_for, explicitly state what this document does NOT
   cover - this prevents incorrect routing.
5. For relevance_hints.key_categories, list the relevant concept areas, topics, or
   domain tags that describe what this document relates to. These should be specific
   enough for a router to distinguish this document from others (e.g. "authentication",
   "error handling", "data pipeline", "compliance", "pricing model").
6. For top_sections, select between {min_top_sections} and {max_top_sections} sections from the PageIndex tree that are most
   useful for routing. Each node_ref must be "{doc_id}::<node_id>" using the exact
   node_id from the tree.
7. For related_docs, list doc_ids from the existing master tree that are meaningfully
   related (share topics, complement each other, or should be consulted together).
   If no documents are related, return an empty list.

Output only the JSON object. No markdown. No explanation.
""".strip()

    valid_related_docs = _existing_doc_ids(existing_master_tree)

    last_error: Exception | None = None
    for _ in range(2):
        # We retry once because JSON-formatting failures are common enough to be
        # worth a second attempt, but not common enough to justify infinite retries.
        try:
            raw_response = await _chat_completion(model, SYSTEM_PROMPT, user_prompt)
            payload = parse_json_response(raw_response)
            if not isinstance(payload, dict):
                raise ValueError("Master node generation did not return a JSON object.")

            related_docs = [
                related_doc
                for related_doc in payload.get("related_docs", [])
                if related_doc in valid_related_docs and related_doc != doc_id
            ]

            master_node = MasterNode(
                doc_id=doc_id,
                doc_title=doc_title,
                doc_type=payload.get("doc_type", doc_type),
                file_path=file_path,
                tree_path=tree_path,
                doc_summary=payload.get("doc_summary", ""),
                key_topics=list(payload.get("key_topics", [])),
                relevance_hints=RelevanceHints(
                    best_for=payload.get("relevance_hints", {}).get("best_for", ""),
                    not_useful_for=payload.get("relevance_hints", {}).get(
                        "not_useful_for", ""
                    ),
                    key_categories=list(
                        payload.get("relevance_hints", {}).get("key_categories", [])
                    ),
                ),
                top_sections=_normalize_top_sections(
                    doc_id,
                    per_doc_tree,
                    payload.get("top_sections", []),
                    top_sections_target=top_sections_target,
                ),
                related_docs=related_docs,
                ingested_at=datetime.utcnow().isoformat(),
            )
            return master_node
        except (json.JSONDecodeError, ValidationError, ValueError) as exc:
            last_error = exc

    raise ValueError(
        f"Failed to generate valid master node JSON for '{doc_id}': {last_error}"
    )
