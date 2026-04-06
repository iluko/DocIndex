"""PageIndex/master-tree artifact builder for experiments."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from experiments.builds.base import ArtifactBuildAdapter, BuildAdapterResult, BuildExecutionContext
from experiments.models import ArtifactOutput, BuiltDocumentArtifact, PageIndexBuildConfig
from ingestion.ingest import ingest_document_with_trace
from master_tree.master_tree import MasterTreeStore
from storage.store import DocumentStore
from utils import atomic_write_text, iter_tree_nodes


class PageIndexBuildAdapter(ArtifactBuildAdapter):
    """Build isolated PageIndex artifacts for one experiment corpus."""

    kind = "pageindex"
    artifact_family = "pageindex_tree"

    async def build(self, context: BuildExecutionContext) -> BuildAdapterResult:
        config = context.config
        if not isinstance(config, PageIndexBuildConfig):
            raise TypeError("PageIndexBuildAdapter requires PageIndexBuildConfig.")

        index_dir = context.build_dir / "pageindex_index"
        traces_dir = context.build_dir / "ingestion_traces"
        traces_dir.mkdir(parents=True, exist_ok=True)

        master_tree_store = MasterTreeStore(str(index_dir / "master_tree.json"))
        storage = DocumentStore(str(index_dir))

        built_docs: list[BuiltDocumentArtifact] = []
        total_nodes = 0

        for document in context.corpus.documents:
            result = await ingest_document_with_trace(
                file_path=document.normalized_path,
                doc_id=document.doc_id,
                doc_title=document.doc_title,
                doc_type=document.doc_type,
                master_tree_store=master_tree_store,
                storage=storage,
                model=config.model,
                pageindex_opts=config.pageindex_opts or None,
                top_sections_target=config.top_sections_target,
                relationship_mode=config.relationship_mode,
            )
            total_nodes += len(list(iter_tree_nodes(result.per_doc_tree)))
            trace_path = traces_dir / f"{document.doc_id}_ingestion_trace.json"
            atomic_write_text(
                trace_path,
                json.dumps(asdict(result.trace), indent=2, ensure_ascii=False),
            )
            built_docs.append(
                BuiltDocumentArtifact(
                    doc_id=document.doc_id,
                    artifact_path=result.trace.tree_path,
                    trace_path=str(trace_path),
                    metadata={
                        "relationship_mode": result.trace.relationship_mode,
                        "tree_path": result.trace.tree_path,
                    },
                )
            )

        return BuildAdapterResult(
            outputs=[
                ArtifactOutput(
                    label="pageindex_index_dir",
                    path=str(index_dir),
                    description="Directory containing the master tree and per-document PageIndex trees.",
                ),
                ArtifactOutput(
                    label="master_tree",
                    path=str(master_tree_store.master_tree_path),
                    description="Master-tree JSON generated for this build.",
                    metadata={"doc_count": len(master_tree_store.list_docs())},
                ),
                ArtifactOutput(
                    label="ingestion_traces",
                    path=str(traces_dir),
                    description="Per-document ingestion traces captured during the build.",
                ),
            ],
            documents=built_docs,
            metrics={
                "docs_processed": len(context.corpus.documents),
                "total_nodes": total_nodes,
                "relationship_mode": config.relationship_mode,
                "top_sections_target": config.top_sections_target,
            },
        )
