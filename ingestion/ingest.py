"""Main ingestion pipeline from raw file to PageIndex tree to master-tree node."""

from __future__ import annotations

import inspect
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from dotenv import load_dotenv
from rich.console import Console

from ingestion.docx_converter import docx_to_markdown
from ingestion.master_node_gen import generate_master_node
from master_tree.master_tree import MasterTreeStore
from master_tree.schema import MasterNode
from storage.store import DocumentStore
from utils import (
    detect_llm_provider,
    emit_progress,
    ensure_pageindex_environment,
    ensure_tiktoken_model_aliases,
    get_default_model,
    INGESTION_REASONING_EFFORT,
    iter_tree_nodes,
    patch_pageindex_llm_helpers,
    patch_pageindex_progress_hooks,
    progress_context,
)

load_dotenv()

page_index_main = None
md_to_tree = None
ConfigLoader = None


console = Console()
VALID_EXTENSIONS = {".pdf", ".md", ".markdown", ".docx"}


@dataclass
class IngestionStep:
    """One human-readable ingestion milestone shown in traces and the UI."""

    name: str
    detail: str
    payload: dict | list | str | None = None


@dataclass
class IngestionTrace:
    """Structured trace describing how one document moved through ingestion."""

    doc_id: str
    file_path: str
    file_type: str
    tree_path: str = ""
    steps: list[IngestionStep] = field(default_factory=list)


@dataclass
class IngestionResult:
    """Return value for traced ingestion: master node, tree, and debug trace."""

    master_node: MasterNode
    per_doc_tree: dict
    trace: IngestionTrace


def _build_pageindex_opt(model: str, pageindex_opts: dict | None = None):
    """Merge our defaults with any user overrides into a PageIndex config object."""
    _load_pageindex_dependencies()
    if ConfigLoader is None:
        raise ImportError(
            "PageIndex is not installed. Clone/install PageIndex before ingestion."
        )

    options = {
        "model": model,
        "if_add_node_id": "yes",
        "if_add_node_summary": "yes",
        "if_add_doc_description": "no",
        "max_page_num_each_node": 10,
        "max_token_num_each_node": 20000,
    }
    if pageindex_opts:
        options.update(pageindex_opts)

    return ConfigLoader().load(options)


def _build_markdown_pageindex_kwargs(opt) -> dict:
    """Translate one config object into the markdown entrypoint's kwargs.

    Junior note:
    The PDF and markdown PageIndex entrypoints do not share the same function
    signature, so we adapt our unified config into the markdown-specific shape here.
    """
    return {
        "if_thinning": "yes" == str(getattr(opt, "if_thinning", "no")).lower(),
        "min_token_threshold": getattr(opt, "max_token_num_each_node", None),
        "if_add_node_summary": getattr(opt, "if_add_node_summary", "yes"),
        "summary_token_threshold": getattr(opt, "summary_token_threshold", 200),
        "model": getattr(opt, "model", None),
        "if_add_doc_description": getattr(opt, "if_add_doc_description", "no"),
        "if_add_node_text": getattr(opt, "if_add_node_text", "no"),
        "if_add_node_id": getattr(opt, "if_add_node_id", "yes"),
    }


def _load_pageindex_dependencies() -> None:
    """Import PageIndex lazily and patch it for this project's runtime behavior.

    We patch the dependency at runtime instead of editing its source so upgrades
    stay straightforward and local compatibility logic remains in our codebase.
    """
    global page_index_main, md_to_tree, ConfigLoader

    if page_index_main is not None and md_to_tree is not None and ConfigLoader is not None:
        return

    project_root = Path(__file__).resolve().parent.parent
    candidate_paths = [
        project_root / "PageIndex",
        project_root.parent / "PageIndex",
    ]
    for candidate in candidate_paths:
        if (candidate / "pageindex").exists():
            candidate_str = str(candidate)
            if candidate_str not in sys.path:
                sys.path.insert(0, candidate_str)
            break

    ensure_tiktoken_model_aliases()

    try:
        import pageindex.page_index as imported_page_index_module
        import pageindex.page_index_md as imported_page_index_md_module
        import pageindex.utils as imported_pageindex_utils
        from pageindex.page_index import page_index_main as imported_page_index_main
        from pageindex.page_index_md import md_to_tree as imported_md_to_tree
        from pageindex.utils import ConfigLoader as imported_config_loader
    except ImportError as exc:  # pragma: no cover - depends on local installation
        raise ImportError(
            "PageIndex is not installed. Clone/install PageIndex before ingestion."
        ) from exc

    patch_pageindex_llm_helpers(
        imported_pageindex_utils,
        imported_page_index_module,
        imported_page_index_md_module,
    )
    patch_pageindex_progress_hooks(
        imported_pageindex_utils,
        imported_page_index_module,
        imported_page_index_md_module,
    )

    page_index_main = imported_page_index_main
    md_to_tree = imported_md_to_tree
    ConfigLoader = imported_config_loader


async def _run_pageindex(
    file_path: str,
    model: str | None,
    pageindex_opts: dict | None = None,
) -> dict:
    """Run the correct PageIndex entrypoint for the given source file."""
    model = model or get_default_model()
    ensure_pageindex_environment()
    _load_pageindex_dependencies()
    path = Path(file_path)
    opt = _build_pageindex_opt(model=model, pageindex_opts=pageindex_opts)

    if path.suffix.lower() == ".pdf":
        if page_index_main is None:
            raise ImportError("PageIndex PDF ingestion support is unavailable.")
        result = page_index_main(str(path), opt)
        # Some upstream versions return a plain dict immediately, while others
        # may return an awaitable. This check lets us support both safely.
        return await result if inspect.isawaitable(result) else result

    if md_to_tree is None:
        raise ImportError("PageIndex markdown ingestion support is unavailable.")
    result = md_to_tree(str(path), **_build_markdown_pageindex_kwargs(opt))
    return await result if inspect.isawaitable(result) else result


def _prepare_ingestion_source(
    original_path: Path,
    doc_id: str,
    storage: DocumentStore,
    trace: IngestionTrace,
) -> tuple[Path, str]:
    """Preprocess source files like DOCX before they enter PageIndex."""
    if original_path.suffix.lower() != ".docx":
        return original_path, str(original_path)

    console.print(f"[cyan]Converting DOCX to Markdown for[/cyan] {doc_id}")
    markdown = docx_to_markdown(str(original_path))
    derived_markdown_path = storage.save_derived_markdown(doc_id, markdown)

    _append_trace(
        trace,
        "convert_docx",
        "Converted the DOCX source into Markdown so PageIndex can build a hierarchical tree.",
        {
            "source_path": str(original_path),
            "derived_markdown_path": derived_markdown_path,
        },
    )

    return Path(derived_markdown_path), derived_markdown_path


def _append_trace(
    trace: IngestionTrace,
    name: str,
    detail: str,
    payload: dict | list | str | None = None,
) -> None:
    """Append a trace step and mirror it to the live progress channel."""
    trace.steps.append(IngestionStep(name=name, detail=detail, payload=payload))
    emit_progress(
        "ingestion_step",
        detail,
        step=name,
        payload=payload,
    )


def _tree_overview(tree: dict) -> dict:
    """Build a compact summary of the generated tree for the ingestion trace."""
    top_level_nodes = tree.get("nodes", [])
    return {
        "total_nodes": len(list(iter_tree_nodes(tree))),
        "top_level_nodes": [
            {
                "node_id": node.get("node_id"),
                "title": node.get("title"),
                "start_index": node.get("start_index"),
                "end_index": node.get("end_index"),
            }
            for node in top_level_nodes[:8]
        ],
    }


async def _ingest_document_impl(
    file_path: str,
    doc_id: str,
    doc_title: str,
    doc_type: str,
    master_tree_store: MasterTreeStore,
    storage: DocumentStore,
    model: str | None = None,
    pageindex_opts: dict | None = None,
    progress_callback: Callable[[dict], None] | None = None,
) -> IngestionResult:
    """Shared implementation behind both simple and traced ingestion APIs."""
    with progress_context(progress_callback):
        # The progress context lets nested helpers and PageIndex runtime patches
        # emit live updates without threading `progress_callback` through every
        # helper function in the stack.
        model = model or get_default_model()
        path = Path(file_path).expanduser().resolve()
        trace = IngestionTrace(
            doc_id=doc_id,
            file_path=str(path),
            file_type=path.suffix.lower(),
        )

        _append_trace(
            trace,
            "validate_input",
            "Validated the source file path and supported extension before ingestion.",
            {
                "doc_title": doc_title,
                "doc_type": doc_type,
                "model": model,
                "provider": detect_llm_provider(),
                "ingestion_reasoning_effort": INGESTION_REASONING_EFFORT,
                "pageindex_overrides": pageindex_opts or {},
            },
        )

        if not path.exists():
            raise FileNotFoundError(f"Document not found: {path}")

        if path.suffix.lower() not in VALID_EXTENSIONS:
            raise ValueError(
                f"Unsupported file type '{path.suffix}'. Use PDF, MD, MARKDOWN, or DOCX files."
            )

        processing_path, retrieval_path = _prepare_ingestion_source(
            original_path=path,
            doc_id=doc_id,
            storage=storage,
            trace=trace,
        )

        console.print(f"[cyan]Building PageIndex tree for[/cyan] {doc_id}")
        emit_progress(
            "pageindex_started",
            f"Building the PageIndex tree for {doc_id}.",
            doc_id=doc_id,
            file_path=str(processing_path),
        )
        try:
            per_doc_tree = await _run_pageindex(
                file_path=str(processing_path),
                model=model,
                pageindex_opts=pageindex_opts,
            )
        except Exception as exc:
            raise RuntimeError(f"Failed to build PageIndex tree for {doc_id}: {exc}") from exc

        _append_trace(
            trace,
            "build_pageindex_tree",
            "Generated the per-document PageIndex hierarchy used for section-level navigation.",
            _tree_overview(per_doc_tree),
        )

        console.print(f"[cyan]Saving per-document tree for[/cyan] {doc_id}")
        tree_path = storage.save_doc_tree(doc_id, per_doc_tree)
        storage.register_doc_source(doc_id, str(path), retrieval_path=retrieval_path)
        trace.tree_path = tree_path

        _append_trace(
            trace,
            "persist_tree",
            "Saved the per-document tree and registered the original source path.",
            {
                "tree_path": tree_path,
                "source_path": str(path),
                "retrieval_path": retrieval_path,
            },
        )

        console.print(f"[cyan]Generating master node for[/cyan] {doc_id}")
        emit_progress(
            "master_node_started",
            f"Generating the master-tree metadata for {doc_id}.",
            doc_id=doc_id,
        )
        existing_master_tree = master_tree_store.to_llm_context()
        master_node = await generate_master_node(
            doc_id=doc_id,
            doc_title=doc_title,
            doc_type=doc_type,
            file_path=str(path),
            tree_path=tree_path,
            per_doc_tree=per_doc_tree,
            existing_master_tree=existing_master_tree,
            model=model,
        )

        _append_trace(
            trace,
            "generate_master_node",
            "Created the cross-document routing metadata record for this document.",
            {
                "doc_summary": master_node.doc_summary,
                "key_topics": master_node.key_topics,
                "top_sections": [section.model_dump(mode="json") for section in master_node.top_sections],
                "related_docs": master_node.related_docs,
            },
        )

        console.print(f"[cyan]Updating master tree for[/cyan] {doc_id}")
        master_tree_store.add_node(master_node)
        master_tree_store.save(master_tree_store.tree)
        console.print(f"[green]Ingestion complete:[/green] {doc_id}")

        _append_trace(
            trace,
            "update_master_tree",
            "Upserted the document into the master tree used by the router.",
            {
                "master_tree_path": str(master_tree_store.master_tree_path),
                "doc_count": len(master_tree_store.list_docs()),
            },
        )

        emit_progress(
            "ingestion_complete",
            f"Ingestion complete for {doc_id}.",
            doc_id=doc_id,
            tree_path=tree_path,
        )

        return IngestionResult(
            master_node=master_node,
            per_doc_tree=per_doc_tree,
            trace=trace,
        )


async def ingest_document(
    file_path: str,
    doc_id: str,
    doc_title: str,
    doc_type: str,
    master_tree_store: MasterTreeStore,
    storage: DocumentStore,
    model: str | None = None,
    pageindex_opts: dict | None = None,
    progress_callback: Callable[[dict], None] | None = None,
) -> MasterNode:
    """Ingest one document and return only the master-tree node."""
    result = await _ingest_document_impl(
        file_path=file_path,
        doc_id=doc_id,
        doc_title=doc_title,
        doc_type=doc_type,
        master_tree_store=master_tree_store,
        storage=storage,
        model=model,
        pageindex_opts=pageindex_opts,
        progress_callback=progress_callback,
    )
    return result.master_node


async def ingest_document_with_trace(
    file_path: str,
    doc_id: str,
    doc_title: str,
    doc_type: str,
    master_tree_store: MasterTreeStore,
    storage: DocumentStore,
    model: str | None = None,
    pageindex_opts: dict | None = None,
    progress_callback: Callable[[dict], None] | None = None,
) -> IngestionResult:
    """Ingest one document and return the tree plus a detailed trace."""
    return await _ingest_document_impl(
        file_path=file_path,
        doc_id=doc_id,
        doc_title=doc_title,
        doc_type=doc_type,
        master_tree_store=master_tree_store,
        storage=storage,
        model=model,
        pageindex_opts=pageindex_opts,
        progress_callback=progress_callback,
    )
