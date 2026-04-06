"""Agentic PageIndex retrieval adapter for experiment runs."""

from __future__ import annotations

import time
from pathlib import Path

from experiments.retrieval.base import (
    RetrievalAdapter,
    RetrievalExecutionContext,
    RetrievalExecutionResult,
)
from master_tree.master_tree import MasterTreeStore
from retrieval.pageindex_engine import run_pageindex_retrieval
from retrieval.planner import plan_query
from storage.store import DocumentStore
from retrieval.query_engine import AdvancedRetrievalConfig
from retrieval.router import route_query, route_query_broadened


def _find_output_path(build, label: str) -> str:
    for output in build.outputs:
        if output.label == label:
            return output.path
    raise FileNotFoundError(f"Build '{build.build_id}' has no output labeled '{label}'.")


class PageIndexAgenticRetrievalAdapter(RetrievalAdapter):
    """Experiment adapter for the agentic PageIndex exploration loop."""

    mode = "pageindex"
    artifact_family = "pageindex_tree"

    async def retrieve(self, context: RetrievalExecutionContext) -> RetrievalExecutionResult:
        build_index_dir = Path(_find_output_path(context.build, "pageindex_index_dir"))
        master_tree_store = MasterTreeStore(str(build_index_dir / "master_tree.json"))
        storage = DocumentStore(str(build_index_dir))

        profile = context.retrieval_profile
        cfg = profile.config
        advanced = AdvancedRetrievalConfig(
            enabled=profile.advanced_retrieval,
            enable_planning=True,
            enable_adaptive_width=True,
            enable_node_expansion=False,
            max_docs_cap=int(cfg.get("max_docs_cap", 6)),
            max_nodes_cap=int(cfg.get("max_nodes_cap", 6)),
        )
        max_docs = int(cfg.get("max_docs", 3))
        max_tool_calls = int(cfg.get("max_tool_calls", 12))

        retrieval_started_at = time.perf_counter()
        planner_output = None
        if advanced.enabled:
            planner_output = await plan_query(
                query=context.query,
                model=context.model,
                hard_max_docs=advanced.max_docs_cap,
                hard_max_nodes=advanced.max_nodes_cap,
            )

        effective_max_docs = max_docs
        if advanced.enabled and planner_output and advanced.enable_adaptive_width:
            effective_max_docs = min(planner_output.recommended_max_docs, advanced.max_docs_cap)

        selected_doc_ids = await route_query(
            query=context.query,
            master_tree_store=master_tree_store,
            model=context.model,
            max_docs=effective_max_docs,
            conversation_context=context.conversation_context,
            reasoning_effort=profile.reasoning_effort,
        )
        routing_broadened = False
        if not selected_doc_ids:
            selected_doc_ids = await route_query_broadened(
                query=context.query,
                master_tree_store=master_tree_store,
                model=context.model,
                max_docs=effective_max_docs,
                conversation_context=context.conversation_context,
                reasoning_effort=profile.reasoning_effort,
            )
            routing_broadened = bool(selected_doc_ids)

        if not selected_doc_ids:
            return RetrievalExecutionResult(
                selected_docs=[],
                selected_nodes=[],
                retrieved_context="",
                trace={
                    "mode": "pageindex",
                    "advanced_retrieval": advanced.enabled,
                    "planner_output": planner_output.__dict__ if planner_output else None,
                    "routing_broadened": False,
                    "effective_max_docs": effective_max_docs,
                },
                retrieval_metrics={
                    "retrieval_time_seconds": max(
                        0.0, time.perf_counter() - retrieval_started_at
                    ),
                    "retrieved_context_tokens": 0,
                },
            )

        _ignored_answer, accessed_nodes, combined_context, engine_result = await run_pageindex_retrieval(
            user_query=context.query,
            selected_doc_ids=selected_doc_ids,
            master_tree_store=master_tree_store,
            storage=storage,
            model=context.model,
            conversation_context=context.conversation_context,
            reasoning_effort=profile.reasoning_effort,
            max_tool_calls=max_tool_calls,
            answer_immediately=False,
        )

        return RetrievalExecutionResult(
            selected_docs=selected_doc_ids,
            selected_nodes=accessed_nodes,
            retrieved_context=combined_context,
            sources=[{"node_ref": ref} for ref in accessed_nodes],
            trace={
                "mode": "pageindex",
                "advanced_retrieval": advanced.enabled,
                "planner_output": planner_output.__dict__ if planner_output else None,
                "routing_broadened": routing_broadened,
                "effective_max_docs": effective_max_docs,
                "tool_calls_made": engine_result.tool_calls_made,
                "tool_call_budget": engine_result.tool_call_budget,
                "content_tokens_used": engine_result.content_tokens_used,
                "content_token_budget": engine_result.content_token_budget,
                "explored_docs": engine_result.explored_docs,
                "tool_budget_exhausted": engine_result.tool_budget_exhausted,
                "content_budget_exhausted": engine_result.content_budget_exhausted,
            },
            retrieval_metrics={
                "retrieval_time_seconds": max(
                    0.0, time.perf_counter() - retrieval_started_at
                ),
                "retrieved_context_tokens": engine_result.content_tokens_used,
                "tool_calls_made": engine_result.tool_calls_made,
            },
        )
