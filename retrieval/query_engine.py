"""High-level orchestration for route -> navigate -> verify -> fetch -> answer.

Hybrid pipeline (RETRIEVAL_MODE=hybrid):

  1. [Optional] Planner    — classify query intent and recommend retrieval width.
  1. Router           — pick 1–N most relevant documents (strict).
  1b. Broadened router — if strict routing returns empty, widen to tangentially
                         related documents and flag the answer as best-effort.
  2. Navigator        — for each selected doc, pick 1–N relevant sections
                         (parallel, summary-first prompts).
  2b. Top-sections fallback — if navigator returns nothing for a doc, fall back
                               to the pre-computed top_sections from ingestion.
  3. [Optional] Expander — add bounded neighboring nodes around primary selection.
  4. Verifier         — (optional, NAVIGATOR_VERIFICATION=true) drop sections
                         that the LLM confirms do not address the query.
  5. Fetcher          — extract raw text within token budget; each chunk is
                         prefixed with its parent section context when available.
  6. Answer LLM       — synthesise the final answer from retrieved context.

PageIndex agentic mode (RETRIEVAL_MODE=pageindex):

  Router selects documents; an agentic tool-use loop then decides what to read
  and answers directly. See retrieval/pageindex_engine.py.

Advanced retrieval (opt-in, ADVANCED_RETRIEVAL=true):

  - Query planning classifies intent and recommends retrieval width.
  - Adaptive width uses planner recommendations to set max_docs / max_nodes.
  - Node-neighborhood expansion adds bounded context beyond primary nodes.
  Standard mode is unchanged unless advanced retrieval is enabled.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from dotenv import load_dotenv
from rich.console import Console

from ingestion.pdf_enricher import parse_image_refs
from master_tree.master_tree import MasterTreeStore
from retrieval.fetcher import (
    RetrievedChunk,
    collect_expansion_node_refs,
    fetch_multiple_nodes_detailed,
)
from retrieval.navigator import navigate_doc_tree
from retrieval.pageindex_engine import PageIndexEngineResult, run_pageindex_retrieval
from retrieval.planner import QueryPlan, plan_query
from retrieval.router import RouterCapture, route_query, route_query_broadened
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
    get_advanced_retrieval_enabled,
    get_query_planning_enabled,
    get_adaptive_width_enabled,
    get_node_expansion_enabled,
    get_max_docs_cap,
    get_max_nodes_cap,
    is_reasoning_effort_unsupported_error,
    is_stream_options_unsupported_error,
    is_temperature_unsupported_error,
    llm_usage_context,
    managed_async_client,
    model_supports_explicit_temperature,
    normalize_reasoning_effort,
    record_llm_usage,
    render_conversation_context,
)

# TYPE_CHECKING import avoids a hard dependency on the traces package at module
# load time — the feature is opt-in via the trace_service parameter.
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from traces.service import TraceService

load_dotenv()

logger = logging.getLogger(__name__)
console = Console()


# ── Public data structures ────────────────────────────────────────────────────


@dataclass
class AdvancedRetrievalConfig:
    """Policy bundle for the optional advanced retrieval features.

    When ``enabled=False`` (the default), all sub-features are inactive and the
    pipeline behaves identically to the pre-advanced-retrieval baseline.

    When ``enabled=True``, each sub-feature can still be individually toggled.
    The env-var accessors (``get_query_planning_enabled()`` etc.) provide the
    defaults; pass explicit values to override them per-call.
    """

    enabled: bool = False
    enable_planning: bool = True
    enable_adaptive_width: bool = True
    enable_node_expansion: bool = True
    max_docs_cap: int = 6
    max_nodes_cap: int = 6

    @classmethod
    def from_env(cls) -> "AdvancedRetrievalConfig":
        """Construct an instance driven entirely by environment variables."""
        enabled = get_advanced_retrieval_enabled()
        return cls(
            enabled=enabled,
            enable_planning=get_query_planning_enabled(),
            enable_adaptive_width=get_adaptive_width_enabled(),
            enable_node_expansion=get_node_expansion_enabled(),
            max_docs_cap=get_max_docs_cap(),
            max_nodes_cap=get_max_nodes_cap(),
        )


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
    # Advanced retrieval metadata
    advanced_retrieval_enabled: bool = False
    planner_output: QueryPlan | None = None
    effective_max_docs: int = 3
    effective_max_nodes: int = 3
    node_expansion_applied: bool = False
    primary_node_refs: list[str] = field(default_factory=list)
    expanded_node_refs: list[str] = field(default_factory=list)
    # PageIndex agentic mode metrics (populated only when retrieval_mode="pageindex")
    pageindex_tool_calls_made: int = 0
    pageindex_tool_call_budget: int = 0
    pageindex_content_tokens_used: int = 0
    pageindex_content_token_budget: int = 0
    pageindex_explored_docs: list[str] = field(default_factory=list)
    pageindex_tool_budget_exhausted: bool = False
    pageindex_content_budget_exhausted: bool = False


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
    image_refs: list[dict] = field(default_factory=list)
    """IMAGE_REF blocks found in the retrieved context, if any."""


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
    async with managed_async_client(get_async_client()) as client:
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

    async with managed_async_client(get_async_client()) as client:
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
    truncated: bool,
    routing_broadened: bool,
) -> str:
    """Compose the system prompt for the final answer LLM call.

    - Uses the DOMAIN_NAME env var to personalise the assistant introduction.
    - Injects explicit caveats when retrieval quality is degraded so the model
      calibrates confidence rather than asserting false certainty.
    """
    domain_name = get_domain_name()
    image_instruction = (
        " When your answer references an image from the context, insert a "
        "placeholder on its own line in the exact position where the image is "
        "relevant, using this format: [IMAGE:img_id] — where img_id is the id "
        "value from the IMAGE_REF block in the retrieved context. Only emit "
        "[IMAGE:img_id] tags for images that genuinely illustrate the answer at "
        "that point. Do not group them all at the end."
    )
    if domain_name:
        intro = (
            f"You are an expert assistant for {domain_name}. "
            "Answer the user's question using ONLY the retrieved document context "
            "provided. Use inline citations like [doc_id, p.N–M] when drawing on specific sections. End your answer with a **Sources** block. "
            "If the context does not contain sufficient information to answer, "
            f"say so clearly.{image_instruction}"
        )
    else:
        intro = (
            "You are a knowledgeable assistant. Answer the user's question using "
            "ONLY the retrieved document context provided. Use inline citations like [doc_id, p.N–M] when drawing on specific sections. End your answer with a **Sources** block. "
            "If the context does not contain "
            f"sufficient information to answer, say so clearly.{image_instruction}"
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

    return (
        f"{intro}{caveat_block}"
        f"\n\nRetrieved Context:\n{retrieved_context}"
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
    max_nodes: int,
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
        max_nodes=max_nodes,
    )

    if not node_refs:
        master_node = master_tree_store.get_node(doc_id)
        if master_node and master_node.top_sections:
            node_refs = [s.node_ref for s in master_node.top_sections[:max_nodes]]
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
    model: str,
    verbose: bool,
    reasoning_effort: str | None,
    max_nodes: int,
    node_expansion: bool,
    answer_token_callback: Callable[[str], None] | None = None,
    retrieval_only: bool = False,
) -> tuple[
    str,            # answer
    list[str],      # selected_nodes (primary only)
    dict[str, list[str]],  # navigation_map
    list[RetrievedChunk],  # chunks
    int,            # token_budget
    bool,           # fetch_truncated
    str,            # retrieved_context
    bool,           # verification_applied
    list[str],      # primary_node_refs
    list[str],      # expanded_node_refs
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
                max_nodes=max_nodes,
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

    primary_node_refs = [ref for refs in navigation_map.values() for ref in refs]

    if verbose:
        console.print(f"Primary nodes to fetch: {primary_node_refs}")

    # ── Step 3: Node-neighborhood expansion (optional) ────────────────────────
    expanded_node_refs: list[str] = []
    if node_expansion and primary_node_refs:
        loaded_trees: dict[str, dict] = {}
        for doc_id in selected_doc_ids:
            try:
                loaded_trees[doc_id] = storage.load_doc_tree(doc_id)
            except FileNotFoundError:
                pass
        expanded_node_refs = collect_expansion_node_refs(primary_node_refs, loaded_trees)
        if verbose and expanded_node_refs:
            console.print(f"Expansion nodes: {expanded_node_refs}")

    # ── Step 4: Fetch ─────────────────────────────────────────────────────────
    fetch_result = await fetch_multiple_nodes_detailed(
        primary_node_refs,
        storage,
        expansion_refs=expanded_node_refs or None,
    )
    retrieved_context = fetch_result.combined_text
    conversation_block = render_conversation_context(conversation_context)

    # ── Step 5: Answer ────────────────────────────────────────────────────────
    if retrieval_only:
        answer = ""
    else:
        system_prompt = _build_answer_system_prompt(
            retrieved_context=retrieved_context,
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
        primary_node_refs,
        navigation_map,
        fetch_result.chunks,
        fetch_result.token_budget,
        fetch_result.truncated,
        retrieved_context,
        verification_applied,
        primary_node_refs,
        expanded_node_refs,
    )


# ── PageIndex agentic pipeline ────────────────────────────────────────────────


async def _run_pageindex_mode(
    user_query: str,
    conversation_context: ConversationContext,
    selected_doc_ids: list[str],
    master_tree_store: MasterTreeStore,
    storage: DocumentStore,
    model: str,
    verbose: bool,
    reasoning_effort: str | None,
    answer_token_callback: Callable[[str], None] | None = None,
) -> tuple[str, list[str], dict[str, list[str]], str, PageIndexEngineResult]:
    """Execute the PageIndex agentic loop for pre-selected documents."""
    answer, accessed_nodes, combined_context, engine_result = await run_pageindex_retrieval(
        user_query=user_query,
        selected_doc_ids=selected_doc_ids,
        master_tree_store=master_tree_store,
        storage=storage,
        model=model,
        conversation_context=conversation_context,
        reasoning_effort=reasoning_effort,
        answer_token_callback=answer_token_callback,
    )

    if verbose:
        console.print(f"PageIndex agent accessed nodes: {accessed_nodes}")
        console.print(
            f"PageIndex tool calls: {engine_result.tool_calls_made}/{engine_result.tool_call_budget}"
            f"  content tokens: {engine_result.content_tokens_used}/{engine_result.content_token_budget}"
        )
        if engine_result.explored_docs:
            console.print(f"PageIndex explored docs: {engine_result.explored_docs}")
        if engine_result.tool_budget_exhausted:
            console.print("[yellow]PageIndex: tool-call budget exhausted.[/yellow]")
        if engine_result.content_budget_exhausted:
            console.print("[yellow]PageIndex: content-token budget exhausted.[/yellow]")

    navigation_map: dict[str, list[str]] = {doc_id: [] for doc_id in selected_doc_ids}
    for node_ref in accessed_nodes:
        if "::" in node_ref:
            doc_id, _ = node_ref.split("::", 1)
            if doc_id in navigation_map:
                navigation_map[doc_id].append(node_ref)

    return answer, accessed_nodes, navigation_map, combined_context, engine_result


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


# ── Audit trace helpers ───────────────────────────────────────────────────────


def _serialize_conversation(conv: ConversationContext) -> list[dict]:
    """Normalise any ConversationContext shape into a JSON-serialisable list."""
    if conv is None:
        return []
    if isinstance(conv, str):
        return [{"role": "context", "content": conv}]
    if isinstance(conv, list):
        return [dict(turn) for turn in conv if isinstance(turn, dict)]
    return []


def _build_audit_trace(
    *,
    project: str,
    model: str,
    query: str,
    conversation_context: ConversationContext,
    result: "QueryResult",
    router_capture: RouterCapture,
    routing_broadened: bool,
    routing_seconds: float,
    chunks: list[RetrievedChunk],
) -> "AuditTrace":
    """Assemble a complete AuditTrace from the query pipeline outputs."""
    from traces.schema import (
        AuditTrace,
        RouterTrace,
        TraceMetrics,
        TraceSection,
        _context_window,
        _estimate_cost,
    )

    sections_used: list[TraceSection] = [
        TraceSection(
            node_ref=c.node_ref,
            doc_id=c.doc_id,
            node_id=c.node_id,
            title=c.title,
            page_start=c.start_index,
            page_end=c.end_index,
            estimated_tokens=c.estimated_tokens,
            truncated=c.truncated,
            text=c.text,
        )
        for c in chunks
    ]

    context_tokens = (
        sum(c.estimated_tokens for c in chunks)
        if chunks
        else len(result.retrieved_context) // 4  # rough estimate for pageindex mode
    )

    m = result.metrics
    pipeline_seconds = max(0.0, (m.total_time_seconds if m else 0.0) - routing_seconds)

    ctx_window = _context_window(model)
    ctx_util = (
        round(context_tokens / ctx_window, 4) if ctx_window and context_tokens else None
    )
    retr_ratio = (
        round(context_tokens / m.prompt_tokens, 4)
        if m and m.prompt_tokens
        else None
    )

    trace_obj = result.trace
    truncation_occurred = False
    if trace_obj is not None:
        truncation_occurred = trace_obj.truncated

    metrics = TraceMetrics(
        ttft_seconds=m.ttft_seconds if m else 0.0,
        total_seconds=m.total_time_seconds if m else 0.0,
        routing_seconds=routing_seconds,
        pipeline_seconds=pipeline_seconds,
        prompt_tokens=m.prompt_tokens if m else 0,
        completion_tokens=m.completion_tokens if m else 0,
        total_tokens=m.total_tokens if m else 0,
        llm_calls=m.llm_calls if m else 0,
        estimated_token_usage=m.estimated_token_usage if m else False,
        context_tokens=context_tokens,
        docs_routed=len(result.selected_docs),
        sections_retrieved=len(sections_used),
        sections_dropped_by_verifier=0,
        truncation_occurred=truncation_occurred,
        routing_broadened=routing_broadened,
        context_utilization_pct=ctx_util,
        retrieval_token_ratio=retr_ratio,
        estimated_cost_usd=_estimate_cost(
            model,
            m.prompt_tokens if m else 0,
            m.completion_tokens if m else 0,
        ),
    )

    return AuditTrace(
        project=project,
        model=model,
        query=query,
        conversation_snapshot=_serialize_conversation(conversation_context),
        routing=RouterTrace(
            context_snapshot=router_capture.context_snapshot,
            raw_response=router_capture.raw_response,
            broadened=routing_broadened,
            selected_doc_ids=result.selected_docs,
        ),
        navigation_map=result.trace.navigation if result.trace else {},
        sections_used=sections_used,
        answer=result.answer,
        retrieved_context_preview=result.retrieved_context[:500],
        retrieval_mode=result.trace.retrieval_mode if result.trace else "hybrid",
        verification_applied=result.trace.verification_applied if result.trace else False,
        metrics=metrics,
    )


# ── Public entry point ────────────────────────────────────────────────────────


async def query(
    user_query: str,
    master_tree_store: MasterTreeStore,
    storage: DocumentStore,
    model: str | None = None,
    conversation_context: ConversationContext = None,
    max_docs: int = 3,
    verbose: bool = False,
    reasoning_effort: str | None = None,
    answer_token_callback: Callable[[str], None] | None = None,
    trace_service: "TraceService | None" = None,
    project: str = "default",
    advanced_retrieval: AdvancedRetrievalConfig | None = None,
    retrieval_mode: str | None = None,
    retrieval_only: bool = False,
) -> QueryResult:
    """Execute the complete retrieval workflow for one user question.

    Retrieval mode is read from the ``RETRIEVAL_MODE`` env var by default, or
    can be overridden by passing ``retrieval_mode`` explicitly:
    - ``hybrid``    — deterministic navigator + verifier + fetcher pipeline.
    - ``pageindex`` — agentic tool-use loop.

    Both modes share the same router, including the broadened-fallback pass.

    The caller may optionally pass ``conversation_context`` as either:
    - a pre-formatted string summary/block, or
    - a list of prior chat turns for convenience.

    The query engine consumes that context but does not mutate, persist, or
    return conversation state.

    Advanced retrieval
    ------------------
    Pass ``advanced_retrieval=AdvancedRetrievalConfig(enabled=True)`` (or build
    one via ``AdvancedRetrievalConfig.from_env()``) to enable optional features:
    query planning, adaptive width, and node-neighborhood expansion.  When
    ``advanced_retrieval`` is None the env-var defaults apply.

    Retrieval-only mode
    -------------------
    Pass ``retrieval_only=True`` to stop the pipeline after context is fetched
    and skip the final answer-generation LLM call.  The returned ``QueryResult``
    will have ``answer=""`` and all other retrieval fields populated as normal.
    Use this when your own answer engine will consume ``retrieved_context`` and
    ``sources`` directly.  Note: in ``pageindex`` mode the agentic tool loop
    still runs to completion (retrieval and answering are coupled), but the
    answer text is discarded and ``answer=""`` is returned.
    """
    model = model or get_default_model()
    # Explicit retrieval_mode param takes precedence over the env-var default.
    if retrieval_mode is None or retrieval_mode not in ("hybrid", "pageindex"):
        retrieval_mode = get_retrieval_mode()
    query_started_at = time.perf_counter()
    first_token_elapsed: float | None = None

    # Resolve advanced retrieval config.
    if advanced_retrieval is None:
        advanced_retrieval = AdvancedRetrievalConfig.from_env()
    adv = advanced_retrieval

    def _wrapped_answer_token(token: str) -> None:
        """Capture end-to-end TTFT once, then forward streamed text onward."""
        nonlocal first_token_elapsed
        if first_token_elapsed is None:
            first_token_elapsed = max(0.0, time.perf_counter() - query_started_at)
        if answer_token_callback is not None:
            answer_token_callback(token)

    router_capture = RouterCapture()
    routing_started_at = time.perf_counter()

    with llm_usage_context():
        # ── Optional query planning ───────────────────────────────────────────
        plan: QueryPlan | None = None
        if adv.enabled and adv.enable_planning:
            plan = await plan_query(
                query=user_query,
                model=model,
                hard_max_docs=adv.max_docs_cap,
                hard_max_nodes=adv.max_nodes_cap,
            )
            if verbose and plan is not None:
                console.print(
                    f"Planner: type={plan.query_type} broad={plan.is_broad} "
                    f"docs={plan.recommended_max_docs} nodes={plan.recommended_max_nodes} "
                    f"expand={plan.use_node_expansion}"
                )

        # Determine effective width.
        effective_max_docs = max_docs
        effective_max_nodes = 3  # standard default
        if adv.enabled and adv.enable_adaptive_width and plan is not None:
            effective_max_docs = min(plan.recommended_max_docs, adv.max_docs_cap)
            effective_max_nodes = min(plan.recommended_max_nodes, adv.max_nodes_cap)

        # Determine whether node expansion is requested.
        node_expansion = (
            adv.enabled
            and adv.enable_node_expansion
            and (plan is None or plan.use_node_expansion)
        )

        # ── Routing (shared by both modes) ────────────────────────────────────
        selected_doc_ids = await route_query(
            query=user_query,
            master_tree_store=master_tree_store,
            model=model,
            max_docs=effective_max_docs,
            conversation_context=conversation_context,
            reasoning_effort=reasoning_effort,
            capture=router_capture,
        )

        routing_broadened = False
        if not selected_doc_ids:
            # Strict routing found nothing — try the broadened pass before giving up.
            selected_doc_ids = await route_query_broadened(
                query=user_query,
                master_tree_store=master_tree_store,
                model=model,
                max_docs=effective_max_docs,
                conversation_context=conversation_context,
                reasoning_effort=reasoning_effort,
                capture=router_capture,
            )
            if selected_doc_ids:
                routing_broadened = True
                logger.info(
                    "Strict routing found no matches; broadened routing selected: %s",
                    selected_doc_ids,
                )

        routing_elapsed = max(0.0, time.perf_counter() - routing_started_at)

        if verbose:
            console.print(
                f"Routing → {selected_doc_ids} "
                f"[mode={retrieval_mode}, broadened={routing_broadened}, "
                f"advanced={adv.enabled}]"
            )

        # ── Hard no-match path ────────────────────────────────────────────────
        if not selected_doc_ids:
            answer = (
                "I couldn't identify any indexed documents relevant to this query, "
                "even after a broadened search. Please check that the relevant "
                "documents have been ingested."
            )
            _no_match_result = QueryResult(
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
                    advanced_retrieval_enabled=adv.enabled,
                    planner_output=plan,
                    effective_max_docs=effective_max_docs,
                    effective_max_nodes=effective_max_nodes,
                    node_expansion_applied=False,
                ),
                metrics=_build_query_metrics(
                    query_started_at,
                    ttft_seconds=first_token_elapsed,
                ),
            )
            if trace_service is not None:
                _audit = _build_audit_trace(
                    project=project,
                    model=model,
                    query=user_query,
                    conversation_context=conversation_context,
                    result=_no_match_result,
                    router_capture=router_capture,
                    routing_broadened=routing_broadened,
                    routing_seconds=routing_elapsed,
                    chunks=[],
                )
                asyncio.create_task(trace_service.write_trace(_audit))
            return _no_match_result

        # ── PageIndex agentic mode ────────────────────────────────────────────
        if retrieval_mode == "pageindex":
            answer, selected_nodes, navigation_map, retrieved_context, pi_engine = (
                await _run_pageindex_mode(
                    user_query=user_query,
                    conversation_context=conversation_context,
                    selected_doc_ids=selected_doc_ids,
                    master_tree_store=master_tree_store,
                    storage=storage,
                    model=model,
                    verbose=verbose,
                    reasoning_effort=reasoning_effort,
                    answer_token_callback=(
                        _wrapped_answer_token if answer_token_callback is not None else None
                    ),
                )
            )
            _pi_result = QueryResult(
                answer="" if retrieval_only else answer,
                selected_docs=selected_doc_ids,
                selected_nodes=selected_nodes,
                retrieved_context=retrieved_context,
                sources=[],
                image_refs=parse_image_refs(retrieved_context),
                trace=QueryTrace(
                    routed_docs=selected_doc_ids,
                    navigation=navigation_map,
                    fetched_chunks=[],
                    token_budget=0,
                    truncated=False,
                    retrieval_mode="pageindex",
                    routing_broadened=routing_broadened,
                    verification_applied=False,
                    advanced_retrieval_enabled=adv.enabled,
                    planner_output=plan,
                    effective_max_docs=effective_max_docs,
                    effective_max_nodes=effective_max_nodes,
                    node_expansion_applied=False,
                    pageindex_tool_calls_made=pi_engine.tool_calls_made,
                    pageindex_tool_call_budget=pi_engine.tool_call_budget,
                    pageindex_content_tokens_used=pi_engine.content_tokens_used,
                    pageindex_content_token_budget=pi_engine.content_token_budget,
                    pageindex_explored_docs=pi_engine.explored_docs,
                    pageindex_tool_budget_exhausted=pi_engine.tool_budget_exhausted,
                    pageindex_content_budget_exhausted=pi_engine.content_budget_exhausted,
                ),
                metrics=_build_query_metrics(
                    query_started_at,
                    ttft_seconds=first_token_elapsed,
                ),
            )
            if trace_service is not None:
                _audit = _build_audit_trace(
                    project=project,
                    model=model,
                    query=user_query,
                    conversation_context=conversation_context,
                    result=_pi_result,
                    router_capture=router_capture,
                    routing_broadened=routing_broadened,
                    routing_seconds=routing_elapsed,
                    chunks=[],
                )
                asyncio.create_task(trace_service.write_trace(_audit))
            return _pi_result

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
            primary_node_refs,
            expanded_node_refs,
        ) = await _run_hybrid(
            user_query=user_query,
            conversation_context=conversation_context,
            selected_doc_ids=selected_doc_ids,
            routing_broadened=routing_broadened,
            master_tree_store=master_tree_store,
            storage=storage,
            model=model,
            verbose=verbose,
            reasoning_effort=reasoning_effort,
            max_nodes=effective_max_nodes,
            node_expansion=node_expansion,
            answer_token_callback=(
                _wrapped_answer_token if answer_token_callback is not None else None
            ),
            retrieval_only=retrieval_only,
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
        _hybrid_result = QueryResult(
            answer=answer,
            selected_docs=selected_doc_ids,
            selected_nodes=selected_nodes,
            retrieved_context=retrieved_context,
            sources=sources,
            image_refs=parse_image_refs(retrieved_context),
            trace=QueryTrace(
                routed_docs=selected_doc_ids,
                navigation=navigation_map,
                fetched_chunks=chunks,
                token_budget=token_budget,
                truncated=fetch_truncated,
                retrieval_mode="hybrid",
                routing_broadened=routing_broadened,
                verification_applied=verification_applied,
                advanced_retrieval_enabled=adv.enabled,
                planner_output=plan,
                effective_max_docs=effective_max_docs,
                effective_max_nodes=effective_max_nodes,
                node_expansion_applied=node_expansion and bool(expanded_node_refs),
                primary_node_refs=primary_node_refs,
                expanded_node_refs=expanded_node_refs,
            ),
            metrics=_build_query_metrics(
                query_started_at,
                ttft_seconds=first_token_elapsed,
            ),
        )
        if trace_service is not None:
            _audit = _build_audit_trace(
                project=project,
                model=model,
                query=user_query,
                conversation_context=conversation_context,
                result=_hybrid_result,
                router_capture=router_capture,
                routing_broadened=routing_broadened,
                routing_seconds=routing_elapsed,
                chunks=chunks,
            )
            asyncio.create_task(trace_service.write_trace(_audit))
        return _hybrid_result
