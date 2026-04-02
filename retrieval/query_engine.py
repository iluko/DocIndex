"""High-level orchestration for route -> navigate -> verify -> fetch -> answer.

Hybrid pipeline (RETRIEVAL_MODE=hybrid):

  1. Router           — pick 1–N most relevant documents (strict).
  1b. Broadened router — if strict routing returns empty, widen to tangentially
                         related documents and flag the answer as best-effort.
  2. Navigator        — for each selected doc, pick 1–3 relevant sections
                         (parallel, summary-first prompts).
  2b. Top-sections fallback — if navigator returns nothing for a doc, fall back
                               to the pre-computed top_sections from ingestion.
  3. Verifier         — (optional, NAVIGATOR_VERIFICATION=true) drop sections
                         that the LLM confirms do not address the query.
  4. Fetcher          — extract raw text within token budget; each chunk is
                         prefixed with its parent section context when available.
  5. Answer LLM       — synthesise the final answer from retrieved context.

PageIndex agentic mode (RETRIEVAL_MODE=pageindex):

  Router selects documents; an agentic tool-use loop then decides what to read
  and answers directly. See retrieval/pageindex_engine.py.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from dotenv import load_dotenv
from rich.console import Console

from arch_map.arch_map import ArchitectureMap
from master_tree.master_tree import MasterTreeStore
from retrieval.fetcher import RetrievedChunk, fetch_multiple_nodes_detailed
from retrieval.navigator import navigate_doc_tree
from retrieval.pageindex_engine import run_pageindex_retrieval
from retrieval.router import route_query, route_query_broadened
from retrieval.verifier import verify_navigation_batch
from storage.store import DocumentStore
from utils import (
    ConversationContext,
    create_chat_completion_async,
    extract_usage,
    extract_llm_text,
    get_async_client,
    get_default_model,
    get_domain_name,
    get_llm_usage_tracker,
    get_navigator_verification_enabled,
    get_retrieval_mode,
    is_reasoning_effort_unsupported_error,
    is_stream_options_unsupported_error,
    is_temperature_unsupported_error,
    llm_usage_context,
    model_supports_explicit_temperature,
    normalize_reasoning_effort,
    record_llm_usage,
    render_conversation_context,
)

load_dotenv()

logger = logging.getLogger(__name__)
console = Console()


# ── Public data structures ────────────────────────────────────────────────────


@dataclass
class QueryTrace:
    """Internal retrieval decisions captured for debugging and UI inspection."""

    routed_docs: list[str]
    navigation: dict[str, list[str]]
    fetched_chunks: list[RetrievedChunk]
    token_budget: int
    truncated: bool
    retrieval_mode: str = "hybrid"
    routing_broadened: bool = False
    verification_applied: bool = False


@dataclass
class SourceReference:
    """One document section cited in the answer."""

    node_ref: str
    doc_id: str
    section: str
    page_range: str  # e.g. "12-15" or "7"


@dataclass
class QueryResult:
    """Full query result, excluding any caller-owned conversation/session state."""

    answer: str
    selected_docs: list[str]
    selected_nodes: list[str]
    retrieved_context: str
    trace: QueryTrace | None = None
    sources: list[SourceReference] = field(default_factory=list)
    metrics: QueryMetrics | None = None


@dataclass
class QueryMetrics:
    """Latency and token-usage numbers for one completed query."""

    ttft_seconds: float
    total_time_seconds: float
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    llm_calls: int
    estimated_token_usage: bool = False


# ── Internal helpers ──────────────────────────────────────────────────────────


async def _answer_query(
    model: str,
    system_prompt: str,
    user_prompt: str,
    reasoning_effort: str | None = None,
) -> str:
    """Run the final answer-generation LLM call."""
    client = get_async_client()
    response = await create_chat_completion_async(
        client=client,
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.1,
        reasoning_effort=reasoning_effort,
    )
    return extract_llm_text(response)


async def _answer_query_streaming(
    model: str,
    system_prompt: str,
    user_prompt: str,
    reasoning_effort: str | None,
    on_token: Callable[[str], None],
) -> str:
    """Stream the answer call, forwarding tokens and returning the full answer."""
    client = get_async_client()
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    request_kwargs: dict = {
        "model": model,
        "messages": messages,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if model_supports_explicit_temperature(model):
        request_kwargs["temperature"] = 0.1
    normalized_effort = normalize_reasoning_effort(reasoning_effort)
    if normalized_effort:
        request_kwargs["reasoning_effort"] = normalized_effort

    while True:
        try:
            stream = await client.chat.completions.create(**request_kwargs)
            break
        except Exception as exc:
            stripped = False
            if "reasoning_effort" in request_kwargs and is_reasoning_effort_unsupported_error(exc):
                request_kwargs.pop("reasoning_effort")
                stripped = True
            if "temperature" in request_kwargs and is_temperature_unsupported_error(exc):
                request_kwargs.pop("temperature")
                stripped = True
            if "stream_options" in request_kwargs and is_stream_options_unsupported_error(exc):
                request_kwargs.pop("stream_options")
                stripped = True
            if not stripped:
                raise

    collected: list[str] = []
    usage_recorded = False
    async for chunk in stream:
        usage = extract_usage(chunk)
        if usage is not None:
            record_llm_usage(messages=messages, usage=usage)
            usage_recorded = True
        if chunk.choices and chunk.choices[0].delta.content:
            token: str = chunk.choices[0].delta.content
            on_token(token)
            collected.append(token)
    answer = "".join(collected)
    if not usage_recorded:
        record_llm_usage(messages=messages, completion_text=answer)
    return answer


def _build_answer_system_prompt(
    retrieved_context: str,
    arch_map_context: str,
    truncated: bool,
    routing_broadened: bool,
) -> str:
    """Compose the system prompt for the final answer LLM call.

    - Uses the DOMAIN_NAME env var to personalise the assistant introduction.
    - Injects explicit caveats when retrieval quality is degraded so the model
      calibrates confidence rather than asserting false certainty.
    """
    domain_name = get_domain_name()
    if domain_name:
        intro = (
            f"You are an expert assistant for {domain_name}. "
            "Answer the user's question using ONLY the retrieved document context "
            "provided. Use inline citations like [doc_id, p.N–M] when drawing on specific sections. End your answer with a **Sources** block. "
            "If the context does not contain sufficient information to answer, "
            "say so clearly."
        )
    else:
        intro = (
            "You are a knowledgeable assistant. Answer the user's question using "
            "ONLY the retrieved document context provided. Use inline citations like [doc_id, p.N–M] when drawing on specific sections. End your answer with a **Sources** block. "
            "If the context does not contain "
            "sufficient information to answer, say so clearly."
        )

    caveats: list[str] = []

    if truncated:
        caveats.append(
            "Retrieved context was truncated due to token limits. "
            "If your answer may be incomplete because of missing content, "
            "say so explicitly."
        )

    if routing_broadened:
        caveats.append(
            "No documents directly matched this query. The retrieved context "
            "comes from tangentially related documents — it may provide partial "
            "or background information only. Clearly state if you cannot give a "
            "definitive answer from the available context."
        )

    caveat_block = ""
    if caveats:
        caveat_block = "\n\nImportant:\n" + "\n".join(f"- {c}" for c in caveats)

    arch_block = f"\n\nDomain Context:\n{arch_map_context}" if arch_map_context else ""

    return (
        f"{intro}{caveat_block}"
        f"\n\nRetrieved Context:\n{retrieved_context}"
        f"{arch_block}"
    )


# ── Hybrid pipeline ───────────────────────────────────────────────────────────


async def _navigate_with_fallback(
    user_query: str,
    doc_id: str,
    conversation_context: ConversationContext,
    master_tree_store: MasterTreeStore,
    storage: DocumentStore,
    model: str,
    reasoning_effort: str | None,
) -> list[str]:
    """Navigate within one document, falling back to top_sections when needed."""
    tree = storage.load_doc_tree(doc_id)
    node_refs = await navigate_doc_tree(
        query=user_query,
        doc_id=doc_id,
        per_doc_tree=tree,
        model=model,
        conversation_context=conversation_context,
        reasoning_effort=reasoning_effort,
    )

    if not node_refs:
        master_node = master_tree_store.get_node(doc_id)
        if master_node and master_node.top_sections:
            node_refs = [s.node_ref for s in master_node.top_sections[:2]]
            logger.info(
                "Navigator returned empty for '%s'; using %d pre-computed "
                "top_section(s) as fallback.",
                doc_id,
                len(node_refs),
            )

    return node_refs


async def _run_hybrid(
    user_query: str,
    conversation_context: ConversationContext,
    selected_doc_ids: list[str],
    routing_broadened: bool,
    master_tree_store: MasterTreeStore,
    storage: DocumentStore,
    arch_map: ArchitectureMap,
    model: str,
    verbose: bool,
    reasoning_effort: str | None,
    answer_token_callback: Callable[[str], None] | None = None,
) -> tuple[
    str,            # answer
    list[str],      # selected_nodes
    dict[str, list[str]],  # navigation_map
    list[RetrievedChunk],  # chunks
    int,            # token_budget
    bool,           # fetch_truncated
    str,            # retrieved_context
    bool,           # verification_applied
]:
    """Execute the full hybrid deterministic pipeline for pre-selected documents."""

    # ── Step 1: Navigation (parallel across all selected docs) ────────────────
    nav_results = await asyncio.gather(
        *[
            _navigate_with_fallback(
                user_query=user_query,
                doc_id=doc_id,
                conversation_context=conversation_context,
                master_tree_store=master_tree_store,
                storage=storage,
                model=model,
                reasoning_effort=reasoning_effort,
            )
            for doc_id in selected_doc_ids
        ]
    )

    navigation_map: dict[str, list[str]] = {
        doc_id: nav_results[i] for i, doc_id in enumerate(selected_doc_ids)
    }

    if verbose:
        console.print(f"Navigation map: {navigation_map}")

    # ── Step 2: Verification (optional, parallel across docs) ─────────────────
    verification_applied = False
    if get_navigator_verification_enabled():
        pre_verification_count = sum(len(v) for v in navigation_map.values())
        navigation_map = await verify_navigation_batch(
            query=user_query,
            navigation_map=navigation_map,
            storage=storage,
            model=model,
            reasoning_effort=reasoning_effort,
        )
        post_verification_count = sum(len(v) for v in navigation_map.values())
        verification_applied = True
        if verbose:
            console.print(
                f"Verification: {pre_verification_count} → "
                f"{post_verification_count} node(s) retained."
            )

    selected_nodes = [ref for refs in navigation_map.values() for ref in refs]

    if verbose:
        console.print(f"Nodes to fetch: {selected_nodes}")

    # ── Step 3: Fetch ─────────────────────────────────────────────────────────
    fetch_result = await fetch_multiple_nodes_detailed(selected_nodes, storage)
    retrieved_context = fetch_result.combined_text
    arch_map_context = arch_map.to_llm_context()
    conversation_block = render_conversation_context(conversation_context)

    # ── Step 4: Answer ────────────────────────────────────────────────────────
    system_prompt = _build_answer_system_prompt(
        retrieved_context=retrieved_context,
        arch_map_context=arch_map_context,
        truncated=fetch_result.truncated,
        routing_broadened=routing_broadened,
    )
    user_prompt = f"{user_query}\n\n{conversation_block}".strip()

    if answer_token_callback is not None:
        answer = await _answer_query_streaming(
            model, system_prompt, user_prompt, reasoning_effort, answer_token_callback
        )
    else:
        answer = await _answer_query(model, system_prompt, user_prompt, reasoning_effort)

    return (
        answer,
        selected_nodes,
        navigation_map,
        fetch_result.chunks,
        fetch_result.token_budget,
        fetch_result.truncated,
        retrieved_context,
        verification_applied,
    )


# ── PageIndex agentic pipeline ────────────────────────────────────────────────


async def _run_pageindex_mode(
    user_query: str,
    conversation_context: ConversationContext,
    selected_doc_ids: list[str],
    master_tree_store: MasterTreeStore,
    storage: DocumentStore,
    arch_map: ArchitectureMap,
    model: str,
    verbose: bool,
    reasoning_effort: str | None,
) -> tuple[str, list[str], dict[str, list[str]], str]:
    """Execute the PageIndex agentic loop for pre-selected documents."""
    answer, accessed_nodes, combined_context = await run_pageindex_retrieval(
        user_query=user_query,
        selected_doc_ids=selected_doc_ids,
        master_tree_store=master_tree_store,
        storage=storage,
        arch_map=arch_map,
        model=model,
        conversation_context=conversation_context,
        reasoning_effort=reasoning_effort,
    )

    if verbose:
        console.print(f"PageIndex agent accessed nodes: {accessed_nodes}")

    navigation_map: dict[str, list[str]] = {doc_id: [] for doc_id in selected_doc_ids}
    for node_ref in accessed_nodes:
        if "::" in node_ref:
            doc_id, _ = node_ref.split("::", 1)
            if doc_id in navigation_map:
                navigation_map[doc_id].append(node_ref)

    return answer, accessed_nodes, navigation_map, combined_context


def _build_query_metrics(
    query_started_at: float,
    *,
    ttft_seconds: float | None = None,
) -> QueryMetrics:
    """Snapshot end-to-end latency plus aggregated LLM usage for the query."""
    total_time_seconds = max(0.0, time.perf_counter() - query_started_at)
    if ttft_seconds is None:
        # Non-streamed answers become visible only once the full answer returns.
        resolved_ttft = total_time_seconds
    else:
        resolved_ttft = max(0.0, ttft_seconds)
    usage = get_llm_usage_tracker()
    return QueryMetrics(
        ttft_seconds=resolved_ttft,
        total_time_seconds=total_time_seconds,
        prompt_tokens=usage.prompt_tokens if usage is not None else 0,
        completion_tokens=usage.completion_tokens if usage is not None else 0,
        total_tokens=usage.total_tokens if usage is not None else 0,
        llm_calls=usage.llm_calls if usage is not None else 0,
        estimated_token_usage=bool(usage and usage.estimated_calls),
    )


# ── Public entry point ────────────────────────────────────────────────────────


async def query(
    user_query: str,
    master_tree_store: MasterTreeStore,
    storage: DocumentStore,
    arch_map: ArchitectureMap,
    model: str | None = None,
    conversation_context: ConversationContext = None,
    max_docs: int = 3,
    verbose: bool = False,
    reasoning_effort: str | None = None,
    answer_token_callback: Callable[[str], None] | None = None,
) -> QueryResult:
    """Execute the complete retrieval workflow for one user question.

    Retrieval mode is read from the ``RETRIEVAL_MODE`` env var:
    - ``hybrid``    — deterministic navigator + verifier + fetcher pipeline.
    - ``pageindex`` — agentic tool-use loop.

    Both modes share the same router, including the broadened-fallback pass.

    The caller may optionally pass ``conversation_context`` as either:
    - a pre-formatted string summary/block, or
    - a list of prior chat turns for convenience.

    The query engine consumes that context but does not mutate, persist, or
    return conversation state.
    """
    model = model or get_default_model()
    retrieval_mode = get_retrieval_mode()
    query_started_at = time.perf_counter()
    first_token_elapsed: float | None = None

    def _wrapped_answer_token(token: str) -> None:
        """Capture end-to-end TTFT once, then forward streamed text onward."""
        nonlocal first_token_elapsed
        if first_token_elapsed is None:
            first_token_elapsed = max(0.0, time.perf_counter() - query_started_at)
        if answer_token_callback is not None:
            answer_token_callback(token)

    with llm_usage_context():
        # ── Routing (shared by both modes) ────────────────────────────────────
        selected_doc_ids = await route_query(
            query=user_query,
            master_tree_store=master_tree_store,
            arch_map=arch_map,
            model=model,
            max_docs=max_docs,
            conversation_context=conversation_context,
            reasoning_effort=reasoning_effort,
        )

        routing_broadened = False
        if not selected_doc_ids:
            # Strict routing found nothing — try the broadened pass before giving up.
            selected_doc_ids = await route_query_broadened(
                query=user_query,
                master_tree_store=master_tree_store,
                arch_map=arch_map,
                model=model,
                max_docs=max_docs,
                conversation_context=conversation_context,
                reasoning_effort=reasoning_effort,
            )
            if selected_doc_ids:
                routing_broadened = True
                logger.info(
                    "Strict routing found no matches; broadened routing selected: %s",
                    selected_doc_ids,
                )

        if verbose:
            console.print(
                f"Routing → {selected_doc_ids} "
                f"[mode={retrieval_mode}, broadened={routing_broadened}]"
            )

        # ── Hard no-match path ────────────────────────────────────────────────
        if not selected_doc_ids:
            answer = (
                "I couldn't identify any indexed documents relevant to this query, "
                "even after a broadened search. Please check that the relevant "
                "documents have been ingested."
            )
            return QueryResult(
                answer=answer,
                selected_docs=[],
                selected_nodes=[],
                retrieved_context="",
                sources=[],
                trace=QueryTrace(
                    routed_docs=[],
                    navigation={},
                    fetched_chunks=[],
                    token_budget=0,
                    truncated=False,
                    retrieval_mode=retrieval_mode,
                    routing_broadened=False,
                    verification_applied=False,
                ),
                metrics=_build_query_metrics(
                    query_started_at,
                    ttft_seconds=first_token_elapsed,
                ),
            )

        # ── PageIndex agentic mode ────────────────────────────────────────────
        if retrieval_mode == "pageindex":
            answer, selected_nodes, navigation_map, retrieved_context = (
                await _run_pageindex_mode(
                    user_query=user_query,
                    conversation_context=conversation_context,
                    selected_doc_ids=selected_doc_ids,
                    master_tree_store=master_tree_store,
                    storage=storage,
                    arch_map=arch_map,
                    model=model,
                    verbose=verbose,
                    reasoning_effort=reasoning_effort,
                )
            )
            return QueryResult(
                answer=answer,
                selected_docs=selected_doc_ids,
                selected_nodes=selected_nodes,
                retrieved_context=retrieved_context,
                sources=[],
                trace=QueryTrace(
                    routed_docs=selected_doc_ids,
                    navigation=navigation_map,
                    fetched_chunks=[],
                    token_budget=0,
                    truncated=False,
                    retrieval_mode="pageindex",
                    routing_broadened=routing_broadened,
                    verification_applied=False,
                ),
                metrics=_build_query_metrics(
                    query_started_at,
                    ttft_seconds=first_token_elapsed,
                ),
            )

        # ── Hybrid deterministic mode ─────────────────────────────────────────
        (
            answer,
            selected_nodes,
            navigation_map,
            chunks,
            token_budget,
            fetch_truncated,
            retrieved_context,
            verification_applied,
        ) = await _run_hybrid(
            user_query=user_query,
            conversation_context=conversation_context,
            selected_doc_ids=selected_doc_ids,
            routing_broadened=routing_broadened,
            master_tree_store=master_tree_store,
            storage=storage,
            arch_map=arch_map,
            model=model,
            verbose=verbose,
            reasoning_effort=reasoning_effort,
            answer_token_callback=(
                _wrapped_answer_token if answer_token_callback is not None else None
            ),
        )

        sources = [
            SourceReference(
                node_ref=chunk.node_ref,
                doc_id=chunk.doc_id,
                section=chunk.title,
                page_range=f"{chunk.start_index}–{chunk.end_index}",
            )
            for chunk in chunks
        ]
        return QueryResult(
            answer=answer,
            selected_docs=selected_doc_ids,
            selected_nodes=selected_nodes,
            retrieved_context=retrieved_context,
            sources=sources,
            trace=QueryTrace(
                routed_docs=selected_doc_ids,
                navigation=navigation_map,
                fetched_chunks=chunks,
                token_budget=token_budget,
                truncated=fetch_truncated,
                retrieval_mode="hybrid",
                routing_broadened=routing_broadened,
                verification_applied=verification_applied,
            ),
            metrics=_build_query_metrics(
                query_started_at,
                ttft_seconds=first_token_elapsed,
            ),
        )
