"""Document-level routing using the master tree plus domain context.

Two routing modes are available:

- ``strict`` (default) — returns only documents highly likely to contain the
  answer. The LLM is instructed to be conservative.
- ``broad`` — returns documents tangentially related to the query when strict
  routing found nothing. The caller should tell the answer LLM it is working
  with best-effort context.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from dotenv import load_dotenv

from master_tree.master_tree import MasterTreeStore
from utils import (
    ConversationContext,
    create_chat_completion_async,
    extract_llm_text,
    get_async_client,
    get_default_model,
    managed_async_client,
    parse_json_response,
    render_conversation_context,
)

load_dotenv()

logger = logging.getLogger(__name__)


@dataclass
class RouterCapture:
    """Side-channel populated by route_query / route_query_broadened for audit traces.

    Pass an instance to either routing function; it will be populated with the
    exact context the router LLM received and its raw response.  This lets a
    post-hoc analysis replay the original routing decision faithfully even if the
    master tree changes later.
    """

    context_snapshot: str = field(default="")
    raw_response: str = field(default="")


async def _chat_completion(
    model: str,
    system_prompt: str,
    user_prompt: str,
    reasoning_effort: str | None = None,
) -> str:
    """Run the router's LLM call and return plain text content."""
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
    return extract_llm_text(response)


async def route_query(
    query: str,
    master_tree_store: MasterTreeStore,
    model: str | None = None,
    max_docs: int = 3,
    conversation_context: ConversationContext = None,
    reasoning_effort: str | None = None,
    capture: RouterCapture | None = None,
) -> list[str]:
    """Choose the most relevant document ids for a user query (strict mode).

    Returns an empty list when no document clearly matches the query.
    Callers should follow up with ``route_query_broadened`` if this returns
    empty and a best-effort answer is acceptable.
    """
    model = model or get_default_model()
    max_docs = max(1, max_docs)
    available_doc_ids = {doc.doc_id for doc in master_tree_store.list_docs()}
    if not available_doc_ids:
        return []

    master_tree_context = master_tree_store.to_llm_context()
    conversation_block = render_conversation_context(conversation_context)

    system_prompt = f"""
You are a document routing agent. You have access to a multi-document index.
Your job is to identify which documents in the index are most likely to contain
a direct, substantive answer to the user's query.

Rules:
- Select between 1 and {max_docs} documents.
- Only include documents that are genuinely relevant — do not pad with loosely
  related documents just to reach {max_docs}.
- If no document is a strong match, return an empty array: []
- Return ONLY a JSON array of doc_id strings, ordered from most to least
  relevant. No explanation, no markdown.
- Example: ["auth_spec", "rbac_overview"]

Master Tree (all indexed documents):
{master_tree_context}
""".strip()

    user_prompt = f"""
Query: {query}

{conversation_block}

Select up to {max_docs} doc_ids. Return JSON array only.
""".strip()

    response = await _chat_completion(
        model, system_prompt, user_prompt, reasoning_effort=reasoning_effort
    )

    if capture is not None:
        capture.context_snapshot = f"Master Tree:\n{master_tree_context}"
        capture.raw_response = response

    payload = parse_json_response(response)
    if not isinstance(payload, list):
        raise ValueError("Router response was not a JSON array.")

    validated: list[str] = []
    for doc_id in payload:
        if doc_id in available_doc_ids and doc_id not in validated:
            validated.append(doc_id)

    return validated[:max_docs]


async def route_query_broadened(
    query: str,
    master_tree_store: MasterTreeStore,
    model: str | None = None,
    max_docs: int = 3,
    conversation_context: ConversationContext = None,
    reasoning_effort: str | None = None,
    capture: RouterCapture | None = None,
) -> list[str]:
    """Broadened fallback routing for queries that strict routing could not match.

    Lowers the bar from "directly answers the query" to "is tangentially related
    and might contain useful context." The caller is responsible for informing
    the answer LLM that this is a best-effort retrieval.

    Returns an empty list only when the index is empty or no document has any
    connection to the query whatsoever.
    """
    model = model or get_default_model()
    max_docs = max(1, max_docs)
    available_doc_ids = {doc.doc_id for doc in master_tree_store.list_docs()}
    if not available_doc_ids:
        return []

    master_tree_context = master_tree_store.to_llm_context()
    conversation_block = render_conversation_context(conversation_context)

    system_prompt = f"""
You are a document routing agent performing a broadened, best-effort search.
A previous strict routing pass found NO documents that directly answer the query.
Your job now is to find documents that are TANGENTIALLY related — documents that
share relevant concepts, background context, or domain overlap with the query,
even if they do not contain a direct answer.

Rules:
- Select between 1 and {max_docs} documents.
- Prefer documents with the most topical overlap, even if indirect.
- Return ONLY a JSON array of doc_id strings, ordered from most to least
  relevant. No explanation, no markdown.
- If truly nothing is related at all, return an empty array: []
- Example: ["overview_doc", "related_spec"]

Master Tree (all indexed documents):
{master_tree_context}
""".strip()

    user_prompt = f"""
Query: {query}

{conversation_block}

Select up to {max_docs} tangentially related doc_ids. Return JSON array only.
""".strip()

    response = await _chat_completion(
        model, system_prompt, user_prompt, reasoning_effort=reasoning_effort
    )

    if capture is not None:
        capture.context_snapshot = f"Master Tree:\n{master_tree_context}"
        capture.raw_response = response

    payload = parse_json_response(response)
    if not isinstance(payload, list):
        logger.warning(
            "Broadened router response was not a JSON array; returning empty."
        )
        return []

    validated: list[str] = []
    for doc_id in payload:
        if doc_id in available_doc_ids and doc_id not in validated:
            validated.append(doc_id)

    if validated:
        logger.info(
            "Broadened routing selected %d doc(s) for query '%s…': %s",
            len(validated),
            query[:60],
            validated,
        )

    return validated[:max_docs]
