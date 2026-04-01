"""Command-line entrypoints for ingestion, querying, and inspection."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import click
from dotenv import load_dotenv
from rich.console import Console
from rich.json import JSON
from rich.panel import Panel
from rich.table import Table

from ingestion.ingest import ingest_document
from index_registry import build_runtime_components
from retrieval.query_engine import query as run_query
from utils import REASONING_EFFORT_OPTIONS, get_default_model, normalize_reasoning_effort

# ── Helpers ───────────────────────────────────────────────────────────────────


def _delete_document(runtime, doc_id: str) -> None:
    """Remove all artifacts for one document from the active index.

    Touches three locations in order:
      1. Master tree JSON  — routing metadata
      2. Doc tree JSON     — per-document PageIndex tree
      3. Source registry   — file-path record
      4. Derived markdown  — only present for DOCX-sourced documents

    Each step is attempted independently so a partial prior deletion does not
    block a clean-up run.
    """
    removed_node = runtime.master_tree_store.remove_node(doc_id)
    if removed_node:
        runtime.master_tree_store.save()

    removed_tree = runtime.storage.delete_doc_tree(doc_id)
    removed_source = runtime.storage.deregister_doc_source(doc_id)
    removed_md = runtime.storage.delete_derived_markdown(doc_id)

    console.print(f"  master tree entry : {'removed' if removed_node   else 'not found'}")
    console.print(f"  doc tree file     : {'removed' if removed_tree   else 'not found'}")
    console.print(f"  source registry   : {'removed' if removed_source else 'not found'}")
    console.print(f"  derived markdown  : {'removed' if removed_md     else 'not found / N/A'}")

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DEFAULT_MODEL = get_default_model()

console = Console()


def _build_runtime(model: str):
    """Build the model-scoped runtime bundle used by CLI commands."""
    return build_runtime_components(DATA_DIR, model=model)


@click.group()
def cli() -> None:
    """Multi-document PageIndex CLI."""


@cli.command()
@click.option("--file", "file_path", required=True, type=click.Path(exists=True, path_type=Path))
@click.option("--doc-id", required=True)
@click.option("--title", "doc_title", required=True)
@click.option("--doc-type", required=True)
@click.option("--model", default=DEFAULT_MODEL, show_default=True)
def ingest(file_path: Path, doc_id: str, doc_title: str, doc_type: str, model: str) -> None:
    """Ingest one source document into the active model-specific index."""
    runtime = _build_runtime(model)
    master_node = asyncio.run(
        ingest_document(
            file_path=str(file_path),
            doc_id=doc_id,
            doc_title=doc_title,
            doc_type=doc_type,
            master_tree_store=runtime.master_tree_store,
            storage=runtime.storage,
            model=model,
        )
    )
    console.print(f"Using index: {runtime.index_context.index_key}")
    console.print(JSON.from_data(master_node.model_dump(mode="json")))


@cli.command("query")
@click.argument("user_query")
@click.option("--max-docs", default=3, show_default=True, type=int)
@click.option("--model", default=DEFAULT_MODEL, show_default=True)
@click.option(
    "--reasoning-effort",
    default="auto",
    show_default=True,
    type=click.Choice(["auto", *REASONING_EFFORT_OPTIONS], case_sensitive=False),
)
@click.option("--verbose", is_flag=True)
def query_command(
    user_query: str,
    max_docs: int,
    model: str,
    reasoning_effort: str,
    verbose: bool,
) -> None:
    """Run interactive querying with optional follow-up turns."""
    runtime = _build_runtime(model)
    chat_history: list[dict] = []
    current_query = user_query
    console.print(f"Using index: {runtime.index_context.index_key}")
    console.print(f"Retrieval reasoning: {normalize_reasoning_effort(reasoning_effort) or 'auto'}")

    while True:
        result = asyncio.run(
            run_query(
                user_query=current_query,
                master_tree_store=runtime.master_tree_store,
                storage=runtime.storage,
                arch_map=runtime.arch_map,
                model=model,
                chat_history=chat_history,
                max_docs=max_docs,
                verbose=verbose,
                reasoning_effort=normalize_reasoning_effort(reasoning_effort),
            )
        )
        console.print(Panel(result.answer, title="Answer", expand=False))
        if verbose:
            console.print(f"Selected docs: {result.selected_docs}")
            console.print(f"Selected nodes: {result.selected_nodes}")

        chat_history = result.chat_history_updated
        follow_up = console.input("Follow-up (or 'exit'): ").strip()
        if follow_up.lower() == "exit":
            break
        current_query = follow_up


@cli.command("list-docs")
@click.option("--model", default=DEFAULT_MODEL, show_default=True)
def list_docs_command(model: str) -> None:
    """Print the documents stored in the active model-specific index."""
    runtime = _build_runtime(model)
    docs = runtime.master_tree_store.list_docs()
    if not docs:
        console.print(f"No documents ingested yet for index: {runtime.index_context.index_key}")
        return

    table = Table(title=f"Indexed Documents ({runtime.index_context.index_key})")
    table.add_column("doc_id")
    table.add_column("doc_title")
    table.add_column("doc_type")
    table.add_column("ingested_at")

    for doc in docs:
        table.add_row(doc.doc_id, doc.doc_title, doc.doc_type, doc.ingested_at)

    console.print(table)


@cli.command("show-master-tree")
@click.option("--model", default=DEFAULT_MODEL, show_default=True)
@click.option("--doc-id", default=None)
def show_master_tree(model: str, doc_id: str | None) -> None:
    """Print either the whole master tree or one selected master node."""
    runtime = _build_runtime(model)

    if doc_id:
        node = runtime.master_tree_store.get_node(doc_id)
        if node is None:
            raise click.ClickException(f"Document '{doc_id}' was not found in the master tree.")
        console.print(JSON(json.dumps(node.model_dump(mode="json"), indent=2)))
        return

    console.print(JSON(json.dumps(runtime.master_tree_store.tree.model_dump(mode="json"), indent=2)))


@cli.command("delete-doc")
@click.argument("doc_id")
@click.option("--model", default=DEFAULT_MODEL, show_default=True)
@click.option("--yes", "confirmed", is_flag=True, default=False,
              help="Skip the confirmation prompt.")
def delete_doc_command(doc_id: str, model: str, confirmed: bool) -> None:
    """Permanently remove DOC_ID from the active index.

    Deletes the master tree entry, the per-document PageIndex tree, the
    source-path registry record, and any derived Markdown (DOCX sources).
    This cannot be undone — reingest the document to restore it.
    """
    runtime = _build_runtime(model)

    if runtime.master_tree_store.get_node(doc_id) is None:
        raise click.ClickException(
            f"Document '{doc_id}' not found in index '{runtime.index_context.index_key}'."
        )

    if not confirmed:
        click.confirm(
            f"Permanently delete '{doc_id}' from index "
            f"'{runtime.index_context.index_key}'?",
            abort=True,
        )

    console.print(f"Deleting '{doc_id}' from index: {runtime.index_context.index_key}")
    _delete_document(runtime, doc_id)
    console.print(f"[green]Deleted '{doc_id}' successfully.[/green]")


@cli.command("reingest")
@click.option("--file", "file_path", required=True, type=click.Path(exists=True, path_type=Path))
@click.option("--doc-id", required=True)
@click.option("--title", "doc_title", required=True)
@click.option("--doc-type", required=True)
@click.option("--model", default=DEFAULT_MODEL, show_default=True)
@click.option("--yes", "confirmed", is_flag=True, default=False,
              help="Skip the confirmation prompt when a previous version exists.")
def reingest_command(
    file_path: Path,
    doc_id: str,
    doc_title: str,
    doc_type: str,
    model: str,
    confirmed: bool,
) -> None:
    """Replace an existing document with a fresh ingestion run.

    If DOC_ID already exists in the index you will be asked to confirm before
    the old version is removed. Equivalent to delete-doc followed by ingest,
    but atomic from the CLI's perspective.
    """
    runtime = _build_runtime(model)
    existing = runtime.master_tree_store.get_node(doc_id)

    if existing is not None and not confirmed:
        click.confirm(
            f"'{doc_id}' already exists in index "
            f"'{runtime.index_context.index_key}'. "
            "Delete the existing version and reingest?",
            abort=True,
        )

    if existing is not None:
        console.print(f"Removing previous version of '{doc_id}'…")
        _delete_document(runtime, doc_id)

    console.print(f"Ingesting '{doc_id}' into index: {runtime.index_context.index_key}")
    master_node = asyncio.run(
        ingest_document(
            file_path=str(file_path),
            doc_id=doc_id,
            doc_title=doc_title,
            doc_type=doc_type,
            master_tree_store=runtime.master_tree_store,
            storage=runtime.storage,
            model=model,
        )
    )
    console.print(f"[green]Reingest complete:[/green] {doc_id}")
    console.print(JSON.from_data(master_node.model_dump(mode="json")))


if __name__ == "__main__":
    cli()
