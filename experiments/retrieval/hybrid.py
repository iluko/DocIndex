"""Hybrid PageIndex retrieval adapter for experiment runs."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

from experiments.retrieval.base import (
    RetrievalAdapter,
    RetrievalExecutionContext,
    RetrievalExecutionResult,
)
from master_tree.master_tree import MasterTreeStore
from retrieval.fetcher import (
    RetrievedChunk,
    collect_expansion_node_refs,
    fetch_multiple_nodes_detailed,
)
from retrieval.navigator import navigate_doc_tree
from retrieval.planner import QueryPlan, plan_query
from retrieval.router import route_query, route_query_broadened
from retrieval.verifier import verify_navigation_batch
from storage.store import DocumentStore
from retrieval.query_engine import AdvancedRetrievalConfig
from utils import get_navigator_verification_enabled


def _find_output_path(build, label: str) -> str:
    for output in build.outputs:
        if output.label == label:
            return output.path
    raise FileNotFoundError(f"Build '{build.build_id}' has no output labeled '{label}'.")


class HybridRetrievalAdapter(RetrievalAdapter):
    """Experiment adapter for the deterministic hybrid retrieval pipeline."""

    mode = "hybrid"
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
            enable_node_expansion=True,
            max_docs_cap=int(cfg.get("max_docs_cap", 6)),
            max_nodes_cap=int(cfg.get("max_nodes_cap", 6)),
        )
        max_docs = int(cfg.get("max_docs", 3))

        retrieval_started_at = time.perf_counter()
        planner_output: QueryPlan | None = None
        if advanced.enabled:
            planner_output = await plan_query(
                query=context.query,
                model=context.model,
                hard_max_docs=advanced.max_docs_cap,
                hard_max_nodes=advanced.max_nodes_cap,
            )

        effective_max_docs = max_docs
        effective_max_nodes = 3
        if advanced.enabled and planner_output and advanced.enable_adaptive_width:
            effective_max_docs = min(planner_output.recommended_max_docs, advanced.max_docs_cap)
            effective_max_nodes = min(
                planner_output.recommended_max_nodes,
                advanced.max_nodes_cap,
            )

        node_expansion = (
            advanced.enabled
            and advanced.enable_node_expansion
            and (planner_output is None or planner_output.use_node_expansion)
        )

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
                    "mode": "hybrid",
                    "advanced_retrieval": advanced.enabled,
                    "planner_output": planner_output.__dict__ if planner_output else None,
                    "routing_broadened": False,
                    "effective_max_docs": effective_max_docs,
                    "effective_max_nodes": effective_max_nodes,
                    "verification_applied": False,
                    "expanded_node_refs": [],
                    "primary_node_refs": [],
                },
                retrieval_metrics={
                    "retrieval_time_seconds": max(
                        0.0, time.perf_counter() - retrieval_started_at
                    ),
                    "retrieved_context_tokens": 0,
                },
            )

        nav_results = await asyncio.gather(
            *[
                self._navigate_with_fallback(
                    query=context.query,
                    doc_id=doc_id,
                    master_tree_store=master_tree_store,
                    storage=storage,
                    model=context.model,
                    conversation_context=context.conversation_context,
                    reasoning_effort=profile.reasoning_effort,
                    max_nodes=effective_max_nodes,
                )
                for doc_id in selected_doc_ids
            ]
        )
        navigation_map = {
            doc_id: nav_results[i] for i, doc_id in enumerate(selected_doc_ids)
        }

        verification_applied = False
        if get_navigator_verification_enabled():
            navigation_map = await verify_navigation_batch(
                query=context.query,
                navigation_map=navigation_map,
                storage=storage,
                model=context.model,
                reasoning_effort=profile.reasoning_effort,
            )
            verification_applied = True

        primary_node_refs = [ref for refs in navigation_map.values() for ref in refs]
        per_doc_trees = {
            doc_id: storage.load_doc_tree(doc_id) for doc_id in selected_doc_ids
        }
        expanded_node_refs = (
            collect_expansion_node_refs(primary_node_refs, per_doc_trees)
            if node_expansion
            else []
        )
        fetch_result = await fetch_multiple_nodes_detailed(
            primary_node_refs,
            storage,
            expansion_refs=expanded_node_refs,
        )

        sources = [
            {
                "node_ref": chunk.node_ref,
                "doc_id": chunk.doc_id,
                "section": chunk.title,
                "page_range": f"{chunk.start_index}–{chunk.end_index}",
                "is_expansion": chunk.is_expansion,
            }
            for chunk in fetch_result.chunks
        ]

        return RetrievalExecutionResult(
            selected_docs=selected_doc_ids,
            selected_nodes=primary_node_refs + [
                ref for ref in expanded_node_refs if ref not in primary_node_refs
            ],
            retrieved_context=fetch_result.combined_text,
            sources=sources,
            trace={
                "mode": "hybrid",
                "advanced_retrieval": advanced.enabled,
                "planner_output": planner_output.__dict__ if planner_output else None,
                "routing_broadened": routing_broadened,
                "effective_max_docs": effective_max_docs,
                "effective_max_nodes": effective_max_nodes,
                "verification_applied": verification_applied,
                "primary_node_refs": primary_node_refs,
                "expanded_node_refs": expanded_node_refs,
                "navigation_map": navigation_map,
                "truncated": fetch_result.truncated,
                "token_budget": fetch_result.token_budget,
            },
            retrieval_metrics={
                "retrieval_time_seconds": max(
                    0.0, time.perf_counter() - retrieval_started_at
                ),
                "retrieved_context_tokens": sum(
                    chunk.estimated_tokens for chunk in fetch_result.chunks
                ),
                "fetched_chunk_count": len(fetch_result.chunks),
            },
        )

    async def _navigate_with_fallback(
        self,
        *,
        query: str,
        doc_id: str,
        master_tree_store: MasterTreeStore,
        storage: DocumentStore,
        model: str,
        conversation_context: str | None,
        reasoning_effort: str | None,
        max_nodes: int,
    ) -> list[str]:
        tree = storage.load_doc_tree(doc_id)
        node_refs = await navigate_doc_tree(
            query=query,
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
                node_refs = [section.node_ref for section in master_node.top_sections[:2]]
        return node_refs
