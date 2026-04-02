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
from index_registry import build_runtime_components, delete_project, list_projects
from retrieval.query_engine import query as run_query
from utils import REASONING_EFFORT_OPTIONS, get_default_model, normalize_reasoning_effort, validate_doc_id

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


def _build_runtime(model: str, project: str | None = None):
    """Build the model-scoped runtime bundle used by CLI commands."""
    return build_runtime_components(DATA_DIR, model=model, project=project)


@click.group()
def cli() -> None:
    """Multi-document PageIndex CLI."""


@cli.command("init")
def init_command() -> None:
    """Interactive setup wizard — writes .env with your LLM credentials."""
    provider = click.prompt(
        "Provider",
        type=click.Choice(["openai", "azure"], case_sensitive=False),
        default="openai",
    ).lower()

    api_key = click.prompt("API key", hide_input=True)

    endpoint = ""
    if provider == "azure":
        endpoint = click.prompt("Azure endpoint (e.g. https://YOUR_RESOURCE.openai.azure.com)").strip().rstrip("/")

    if provider == "azure":
        model_label = "Deployment name"
    else:
        model_label = "Model name"
    model = click.prompt(model_label, default="gpt-4o" if provider == "openai" else "").strip()

    domain = click.prompt("Domain name (optional, press Enter to skip)", default="").strip()

    env_path = BASE_DIR / ".env"
    if env_path.exists():
        existing = env_path.read_text(encoding="utf-8")
        has_key = any(
            line.strip() and not line.startswith("#") and "API_KEY=" in line and line.split("=", 1)[1].strip()
            for line in existing.splitlines()
        )
        if has_key:
            click.confirm(".env already exists with API key values. Overwrite?", abort=True)

    if provider == "openai":
        content = (
            f"LLM_PROVIDER=openai\n"
            f"LLM_MODEL={model}\n"
            f"\n"
            f"DOMAIN_NAME={domain}\n"
            f"\n"
            f"RETRIEVAL_MODE=hybrid\n"
            f"NAVIGATOR_VERIFICATION=false\n"
            f"\n"
            f"OPENAI_API_KEY={api_key}\n"
            f"OPENAI_MODEL={model}\n"
            f"\n"
            f"# Azure OpenAI (not configured)\n"
            f"AZURE_OPENAI_API_KEY=\n"
            f"AZURE_OPENAI_ENDPOINT=\n"
            f"AZURE_OPENAI_BASE_URL=\n"
            f"AZURE_OPENAI_CHAT_DEPLOYMENT=\n"
            f"\n"
            f"CHATGPT_API_KEY={api_key}\n"
        )
    else:
        content = (
            f"LLM_PROVIDER=azure\n"
            f"LLM_MODEL={model}\n"
            f"\n"
            f"DOMAIN_NAME={domain}\n"
            f"\n"
            f"RETRIEVAL_MODE=hybrid\n"
            f"NAVIGATOR_VERIFICATION=false\n"
            f"\n"
            f"# OpenAI (not configured)\n"
            f"OPENAI_API_KEY=\n"
            f"OPENAI_MODEL=gpt-4o-2024-11-20\n"
            f"\n"
            f"AZURE_OPENAI_API_KEY={api_key}\n"
            f"AZURE_OPENAI_ENDPOINT={endpoint}\n"
            f"AZURE_OPENAI_BASE_URL={endpoint}/openai/v1/\n"
            f"AZURE_OPENAI_CHAT_DEPLOYMENT={model}\n"
            f"\n"
            f"CHATGPT_API_KEY={api_key}\n"
        )

    env_path.write_text(content, encoding="utf-8")
    console.print(f"[green]Wrote .env to {env_path}[/green]")


@cli.command()
@click.option("--file", "file_path", required=True, type=click.Path(exists=True, path_type=Path))
@click.option("--doc-id", required=True)
@click.option("--title", "doc_title", required=True)
@click.option("--doc-type", required=True)
@click.option("--model", default=DEFAULT_MODEL, show_default=True)
@click.option("--project", default=None, help="Optional project namespace for index isolation.")
def ingest(file_path: Path, doc_id: str, doc_title: str, doc_type: str, model: str, project: str | None) -> None:
    """Ingest one source document into the active project index."""
    try:
        validate_doc_id(doc_id)
    except ValueError as exc:
        raise click.BadParameter(str(exc), param_hint="--doc-id") from exc
    runtime = _build_runtime(model, project=project)
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
    console.print(f"Project: {runtime.index_context.project}  |  Model: {model}")
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
@click.option("--stream", "use_stream", is_flag=True, default=False, help="Stream the answer token by token.")
@click.option("--project", default=None, help="Optional project namespace for index isolation.")
def query_command(
    user_query: str,
    max_docs: int,
    model: str,
    reasoning_effort: str,
    verbose: bool,
    use_stream: bool,
    project: str | None,
) -> None:
    """Run interactive querying with optional follow-up turns."""
    runtime = _build_runtime(model, project=project)
    chat_history: list[dict] = []
    current_query = user_query
    console.print(f"Project: {runtime.index_context.project}  |  Model: {model}")
    console.print(f"Retrieval reasoning: {normalize_reasoning_effort(reasoning_effort) or 'auto'}")

    while True:
        if use_stream:
            def on_token(token: str) -> None:
                import sys
                sys.stdout.write(token)
                sys.stdout.flush()
            cb = on_token
        else:
            cb = None

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
                answer_token_callback=cb,
            )
        )

        if use_stream:
            console.print()  # newline after streamed answer
        else:
            console.print(Panel(result.answer, title="Answer", expand=False))
        if result.metrics is not None:
            token_note = " (includes estimates)" if result.metrics.estimated_token_usage else ""
            console.print(
                "TTFT: "
                f"{result.metrics.ttft_seconds:.2f}s"
                "  |  Total: "
                f"{result.metrics.total_time_seconds:.2f}s"
                "  |  Tokens: "
                f"{result.metrics.total_tokens:,}"
                f"{token_note}"
            )
            console.print(
                "  prompt="
                f"{result.metrics.prompt_tokens:,}"
                "  completion="
                f"{result.metrics.completion_tokens:,}"
                "  llm_calls="
                f"{result.metrics.llm_calls}"
            )

        if verbose:
            console.print(f"Selected docs: {result.selected_docs}")
            console.print(f"Selected nodes: {result.selected_nodes}")
            if result.sources:
                console.print("Sources:")
                for src in result.sources:
                    console.print(f"  [{src.doc_id}] {src.section} (p.{src.page_range})")

        chat_history = result.chat_history_updated
        follow_up = console.input("Follow-up (or 'exit'): ").strip()
        if follow_up.lower() == "exit":
            break
        current_query = follow_up


@cli.command("list-docs")
@click.option("--model", default=DEFAULT_MODEL, show_default=True)
@click.option("--project", default=None, help="Optional project namespace for index isolation.")
def list_docs_command(model: str, project: str | None) -> None:
    """Print the documents stored in the active project index."""
    runtime = _build_runtime(model, project=project)
    docs = runtime.master_tree_store.list_docs()
    if not docs:
        console.print(f"No documents ingested yet in project: {runtime.index_context.project}")
        return

    table = Table(title=f"Indexed Documents — project: {runtime.index_context.project}")
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
@click.option("--project", default=None, help="Optional project namespace for index isolation.")
def show_master_tree(model: str, doc_id: str | None, project: str | None) -> None:
    """Print either the whole master tree or one selected master node."""
    runtime = _build_runtime(model, project=project)

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
@click.option("--project", default=None, help="Optional project namespace for index isolation.")
def delete_doc_command(doc_id: str, model: str, confirmed: bool, project: str | None) -> None:
    """Permanently remove DOC_ID from the active index.

    Deletes the master tree entry, the per-document PageIndex tree, the
    source-path registry record, and any derived Markdown (DOCX sources).
    This cannot be undone — reingest the document to restore it.
    """
    runtime = _build_runtime(model, project=project)

    if runtime.master_tree_store.get_node(doc_id) is None:
        raise click.ClickException(
            f"Document '{doc_id}' not found in project '{runtime.index_context.project}'."
        )

    if not confirmed:
        click.confirm(
            f"Permanently delete '{doc_id}' from project "
            f"'{runtime.index_context.project}'?",
            abort=True,
        )

    console.print(f"Deleting '{doc_id}' from project: {runtime.index_context.project}")
    _delete_document(runtime, doc_id)
    console.print(f"[green]Deleted '{doc_id}' successfully.[/green]")


@cli.command("delete-project")
@click.argument("project_name")
@click.option("--yes", "confirmed", is_flag=True, default=False,
              help="Skip the confirmation prompt.")
def delete_project_command(project_name: str, confirmed: bool) -> None:
    """Permanently delete PROJECT_NAME and all its indexed documents.

    Removes every artifact stored for the project — master tree, doc trees,
    source registry, and derived markdown.  This cannot be undone; documents
    must be re-ingested to restore them.
    """
    existing = list_projects(DATA_DIR)
    if project_name not in existing:
        raise click.ClickException(
            f"Project '{project_name}' does not exist. "
            f"Known projects: {existing}"
        )

    if not confirmed:
        click.confirm(
            f"Permanently delete project '{project_name}' and ALL its documents?",
            abort=True,
        )

    console.print(f"Deleting project: [bold]{project_name}[/bold]")
    delete_project(DATA_DIR, project_name)
    console.print(f"[green]Project '{project_name}' deleted.[/green]")


@cli.command("reingest")
@click.option("--file", "file_path", required=True, type=click.Path(exists=True, path_type=Path))
@click.option("--doc-id", required=True)
@click.option("--title", "doc_title", required=True)
@click.option("--doc-type", required=True)
@click.option("--model", default=DEFAULT_MODEL, show_default=True)
@click.option("--yes", "confirmed", is_flag=True, default=False,
              help="Skip the confirmation prompt when a previous version exists.")
@click.option("--project", default=None, help="Optional project namespace for index isolation.")
def reingest_command(
    file_path: Path,
    doc_id: str,
    doc_title: str,
    doc_type: str,
    model: str,
    confirmed: bool,
    project: str | None,
) -> None:
    """Replace an existing document with a fresh ingestion run.

    If DOC_ID already exists in the index you will be asked to confirm before
    the old version is removed. Equivalent to delete-doc followed by ingest,
    but atomic from the CLI's perspective.
    """
    try:
        validate_doc_id(doc_id)
    except ValueError as exc:
        raise click.BadParameter(str(exc), param_hint="--doc-id") from exc
    runtime = _build_runtime(model, project=project)
    existing = runtime.master_tree_store.get_node(doc_id)

    if existing is not None and not confirmed:
        click.confirm(
            f"'{doc_id}' already exists in project "
            f"'{runtime.index_context.project}'. "
            "Delete the existing version and reingest?",
            abort=True,
        )

    if existing is not None:
        console.print(f"Removing previous version of '{doc_id}'…")
        _delete_document(runtime, doc_id)

    console.print(f"Ingesting '{doc_id}' into project: {runtime.index_context.project}  |  model: {model}")
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
