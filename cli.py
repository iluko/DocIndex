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
from index_registry import DEFAULT_PROJECT, build_runtime_components, delete_project, list_projects
from retrieval.query_engine import AdvancedRetrievalConfig, query as run_query
from utils import (
    REASONING_EFFORT_OPTIONS,
    ADVANCED_RETRIEVAL_MAX_DOCS_CAP,
    ADVANCED_RETRIEVAL_MAX_NODES_CAP,
    get_default_model,
    get_master_top_sections_target,
    get_related_docs_mode,
    get_retrieval_mode,
    normalize_reasoning_effort,
    RELATED_DOCS_MODE_DEFAULT,
    RETRIEVAL_MODE_OPTIONS,
    validate_doc_id,
)

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
            f"MASTER_TOP_SECTIONS_TARGET={get_master_top_sections_target()}\n"
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
            f"MASTER_TOP_SECTIONS_TARGET={get_master_top_sections_target()}\n"
        )

    env_path.write_text(content, encoding="utf-8")
    console.print(f"[green]Wrote .env to {env_path}[/green]")


@cli.command()
@click.option("--file", "file_path", required=True, type=click.Path(exists=True, path_type=Path))
@click.option("--doc-id", required=True)
@click.option("--title", "doc_title", required=True)
@click.option("--doc-type", required=True)
@click.option("--model", default=DEFAULT_MODEL, show_default=True)
@click.option(
    "--top-sections-target",
    default=get_master_top_sections_target(),
    show_default=True,
    type=click.IntRange(1, 12),
    help="Ingestion-only target for stored master-tree top sections. Prompt range becomes target-1 to target+1. Higher values improve routing detail but increase master-tree prompt size.",
)
@click.option(
    "--relationship-mode",
    "relationship_mode",
    type=click.Choice(["off", "basic", "enhanced"], case_sensitive=False),
    default=None,
    help=(
        "Ingestion-time relationship maintenance mode. "
        "'off' preserves near-current behavior (no reconciliation). "
        "'basic' (default) enforces symmetry, dedup, and bounded length deterministically. "
        "'enhanced' adds a bounded LLM-assisted candidate-neighbor pass before reconciliation. "
        "Defaults to the RELATED_DOCS_MODE env var (currently: basic)."
    ),
)
@click.option("--project", default=None, help="Optional project namespace for index isolation.")
def ingest(
    file_path: Path,
    doc_id: str,
    doc_title: str,
    doc_type: str,
    model: str,
    top_sections_target: int,
    relationship_mode: str | None,
    project: str | None,
) -> None:
    """Ingest one source document into the active project index."""
    try:
        validate_doc_id(doc_id)
    except ValueError as exc:
        raise click.BadParameter(str(exc), param_hint="--doc-id") from exc
    resolved_mode = relationship_mode or get_related_docs_mode()
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
            top_sections_target=top_sections_target,
            relationship_mode=resolved_mode,
        )
    )
    console.print(f"Project: {runtime.index_context.project}  |  Model: {model}  |  Relationship mode: {resolved_mode}")
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
@click.option(
    "--advanced",
    "advanced_retrieval",
    is_flag=True,
    default=False,
    help=(
        "Enable advanced retrieval: query planning, adaptive width, and node-neighborhood "
        "expansion. Better completeness for broad/workflow queries but higher latency and cost."
    ),
)
@click.option(
    "--no-planning",
    is_flag=True,
    default=False,
    help="[Advanced] Disable query-intent planning when --advanced is on.",
)
@click.option(
    "--no-adaptive-width",
    is_flag=True,
    default=False,
    help="[Advanced] Disable adaptive retrieval width when --advanced is on.",
)
@click.option(
    "--no-expansion",
    is_flag=True,
    default=False,
    help="[Advanced] Disable node-neighborhood expansion when --advanced is on.",
)
@click.option(
    "--max-docs-cap",
    default=ADVANCED_RETRIEVAL_MAX_DOCS_CAP,
    show_default=True,
    type=click.IntRange(1, 10),
    help="[Advanced] Hard cap on documents routed (advanced mode only).",
)
@click.option(
    "--max-nodes-cap",
    default=ADVANCED_RETRIEVAL_MAX_NODES_CAP,
    show_default=True,
    type=click.IntRange(1, 10),
    help="[Advanced] Hard cap on nodes selected per document (advanced mode only).",
)
@click.option(
    "--retrieval-mode",
    "retrieval_mode",
    type=click.Choice(list(RETRIEVAL_MODE_OPTIONS), case_sensitive=False),
    default=None,
    help=(
        "Retrieval pipeline to use. "
        "'hybrid' (default) is deterministic and bounded. "
        "'pageindex' is an agentic tool-use loop with higher latency/cost but "
        "potentially better completeness on hard multi-document questions. "
        "Defaults to the RETRIEVAL_MODE env var (currently: hybrid)."
    ),
)
@click.option(
    "--retrieval-only",
    "retrieval_only",
    is_flag=True,
    default=False,
    help=(
        "Stop after context retrieval — skip the final answer LLM call. "
        "Prints the retrieved context and sources so you can pipe them into "
        "your own answer engine. In pageindex mode the agentic loop still runs "
        "(retrieval and answering are coupled), but the answer text is discarded."
    ),
)
def query_command(
    user_query: str,
    max_docs: int,
    model: str,
    reasoning_effort: str,
    verbose: bool,
    use_stream: bool,
    project: str | None,
    advanced_retrieval: bool,
    no_planning: bool,
    no_adaptive_width: bool,
    no_expansion: bool,
    max_docs_cap: int,
    max_nodes_cap: int,
    retrieval_mode: str | None,
    retrieval_only: bool,
) -> None:
    """Run interactive querying with optional follow-up turns."""
    runtime = _build_runtime(model, project=project)
    chat_history: list[dict] = []
    current_query = user_query

    adv_config = AdvancedRetrievalConfig(
        enabled=advanced_retrieval,
        enable_planning=not no_planning,
        enable_adaptive_width=not no_adaptive_width,
        enable_node_expansion=not no_expansion,
        max_docs_cap=max_docs_cap,
        max_nodes_cap=max_nodes_cap,
    )

    resolved_retrieval_mode = retrieval_mode or get_retrieval_mode()

    console.print(f"Project: {runtime.index_context.project}  |  Model: {model}")
    console.print(
        f"Retrieval mode: {resolved_retrieval_mode}"
        f"  |  Reasoning: {normalize_reasoning_effort(reasoning_effort) or 'auto'}"
    )
    if retrieval_only:
        console.print(
            "[yellow]Retrieval-only mode — answer generation is skipped. "
            "Returning context and sources for use in an external answer engine.[/yellow]"
        )
    if resolved_retrieval_mode == "pageindex":
        console.print(
            "[yellow]PageIndex mode — agentic, higher latency/cost. "
            "Better for hard multi-document questions.[/yellow]"
        )
    if advanced_retrieval:
        console.print(
            "[yellow]Advanced retrieval enabled — higher latency/cost, better "
            "completeness for broad questions.[/yellow]"
        )

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
                model=model,
                conversation_context=chat_history,
                max_docs=max_docs,
                verbose=verbose,
                reasoning_effort=normalize_reasoning_effort(reasoning_effort),
                answer_token_callback=cb,
                trace_service=runtime.trace_service,
                project=runtime.index_context.project,
                advanced_retrieval=adv_config,
                retrieval_mode=resolved_retrieval_mode,
                retrieval_only=retrieval_only,
            )
        )

        if retrieval_only:
            console.print(Panel(result.retrieved_context, title="Retrieved Context", expand=False))
            if result.sources:
                console.print("Sources:")
                for src in result.sources:
                    console.print(f"  [{src.doc_id}] {src.section} (p.{src.page_range})")
            console.print(f"Selected docs: {result.selected_docs}")
            console.print(f"Selected nodes: {result.selected_nodes}")
        elif use_stream:
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
            if result.trace and result.trace.retrieval_mode == "pageindex":
                t = result.trace
                console.print(
                    f"  PageIndex tool calls: {t.pageindex_tool_calls_made}/{t.pageindex_tool_call_budget}"
                    f"  content tokens: {t.pageindex_content_tokens_used}/{t.pageindex_content_token_budget}"
                )
                if t.pageindex_explored_docs:
                    console.print(f"  Explored docs: {t.pageindex_explored_docs}")
                if t.pageindex_tool_budget_exhausted:
                    console.print("[yellow]  ⚠ Tool-call budget exhausted[/yellow]")
                if t.pageindex_content_budget_exhausted:
                    console.print("[yellow]  ⚠ Content-token budget exhausted[/yellow]")
            if result.trace and result.trace.advanced_retrieval_enabled:
                plan = result.trace.planner_output
                if plan:
                    console.print(
                        f"  Planner: type={plan.query_type} broad={plan.is_broad} "
                        f"docs={plan.recommended_max_docs} nodes={plan.recommended_max_nodes}"
                    )
                console.print(
                    f"  Effective: max_docs={result.trace.effective_max_docs} "
                    f"max_nodes={result.trace.effective_max_nodes} "
                    f"expansion={result.trace.node_expansion_applied}"
                )
                if result.trace.expanded_node_refs:
                    console.print(f"  Expanded nodes: {result.trace.expanded_node_refs}")
            if result.sources:
                console.print("Sources:")
                for src in result.sources:
                    console.print(f"  [{src.doc_id}] {src.section} (p.{src.page_range})")

        if not retrieval_only:
            chat_history = chat_history + [
                {"role": "user", "content": current_query},
                {"role": "assistant", "content": result.answer},
            ]
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
@click.option(
    "--top-sections-target",
    default=get_master_top_sections_target(),
    show_default=True,
    type=click.IntRange(1, 12),
    help="Ingestion-only target for stored master-tree top sections. Higher values improve routing detail but increase master-tree prompt size.",
)
@click.option(
    "--relationship-mode",
    "relationship_mode",
    type=click.Choice(["off", "basic", "enhanced"], case_sensitive=False),
    default=None,
    help=(
        "Ingestion-time relationship maintenance mode. "
        "Defaults to the RELATED_DOCS_MODE env var (currently: basic)."
    ),
)
@click.option("--yes", "confirmed", is_flag=True, default=False,
              help="Skip the confirmation prompt when a previous version exists.")
@click.option("--project", default=None, help="Optional project namespace for index isolation.")
def reingest_command(
    file_path: Path,
    doc_id: str,
    doc_title: str,
    doc_type: str,
    model: str,
    top_sections_target: int,
    relationship_mode: str | None,
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
    resolved_mode = relationship_mode or get_related_docs_mode()
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

    console.print(f"Ingesting '{doc_id}' into project: {runtime.index_context.project}  |  model: {model}  |  relationship mode: {resolved_mode}")
    master_node = asyncio.run(
        ingest_document(
            file_path=str(file_path),
            doc_id=doc_id,
            doc_title=doc_title,
            doc_type=doc_type,
            master_tree_store=runtime.master_tree_store,
            storage=runtime.storage,
            model=model,
            top_sections_target=top_sections_target,
            relationship_mode=resolved_mode,
        )
    )
    console.print(f"[green]Reingest complete:[/green] {doc_id}")
    console.print(JSON.from_data(master_node.model_dump(mode="json")))


# ── Traces ────────────────────────────────────────────────────────────────────


@cli.group("traces")
def traces_group() -> None:
    """Inspect and export query audit traces."""


@traces_group.command("list")
@click.option("--project", default=DEFAULT_PROJECT, show_default=True)
@click.option("--limit", default=20, show_default=True, type=int)
@click.option("--search", default=None, help="Filter traces by query substring.")
def traces_list_command(project: str, limit: int, search: str | None) -> None:
    """List recent query audit traces for a project."""
    runtime = _build_runtime(DEFAULT_MODEL, project=project)
    summaries = runtime.trace_service.list_traces(limit=limit, search=search)

    if not summaries:
        console.print(f"No traces found for project: {project}")
        return

    table = Table(title=f"Audit Traces — project: {project}", show_lines=False)
    table.add_column("timestamp", style="dim", no_wrap=True)
    table.add_column("trace_id", style="dim", no_wrap=True)
    table.add_column("mode", no_wrap=True)
    table.add_column("docs", justify="right")
    table.add_column("sections", justify="right")
    table.add_column("TTFT", justify="right")
    table.add_column("total", justify="right")
    table.add_column("routing", justify="right")
    table.add_column("tokens", justify="right")
    table.add_column("cost", justify="right")
    table.add_column("query", max_width=55)

    for s in summaries:
        broadened_marker = " [B]" if s.routing_broadened else ""
        cost_str = f"${s.estimated_cost_usd:.4f}" if s.estimated_cost_usd is not None else "—"
        table.add_row(
            s.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            s.trace_id[:8],
            f"{s.retrieval_mode}{broadened_marker}",
            str(s.docs_routed),
            str(s.sections_retrieved),
            f"{s.ttft_seconds:.2f}s",
            f"{s.total_seconds:.2f}s",
            f"{s.routing_seconds:.2f}s",
            f"{s.total_tokens:,}",
            cost_str,
            s.query_preview,
        )

    console.print(table)
    console.print("[dim][B] = broadened routing was used[/dim]")


@traces_group.command("show")
@click.argument("trace_id")
@click.option("--project", default=DEFAULT_PROJECT, show_default=True)
@click.option("--raw", is_flag=True, default=False, help="Print raw JSON.")
def traces_show_command(trace_id: str, project: str, raw: bool) -> None:
    """Show the full audit trace for TRACE_ID."""
    runtime = _build_runtime(DEFAULT_MODEL, project=project)
    trace = runtime.trace_service.get_trace(trace_id)

    if trace is None:
        raise click.ClickException(
            f"Trace '{trace_id}' not found in project '{project}'."
        )

    if raw:
        console.print(JSON(json.dumps(trace.model_dump(mode="json"), indent=2)))
        return

    console.print(Panel(f"[bold]Query:[/bold] {trace.query}", title=f"Trace {trace.trace_id[:8]}"))

    # Metrics
    m = trace.metrics
    console.print(
        f"  TTFT: [cyan]{m.ttft_seconds:.2f}s[/cyan]"
        f"  |  Total: [cyan]{m.total_seconds:.2f}s[/cyan]"
        f"  |  Routing: [cyan]{m.routing_seconds:.2f}s[/cyan]"
        f"  |  Pipeline: [cyan]{m.pipeline_seconds:.2f}s[/cyan]"
    )
    console.print(
        f"  Tokens: [cyan]{m.total_tokens:,}[/cyan]"
        f"  (prompt={m.prompt_tokens:,}  completion={m.completion_tokens:,}"
        f"  llm_calls={m.llm_calls})"
    )
    if m.estimated_cost_usd is not None:
        console.print(f"  Estimated cost: [cyan]${m.estimated_cost_usd:.6f}[/cyan]")
    if m.context_utilization_pct is not None:
        console.print(f"  Context utilization: [cyan]{m.context_utilization_pct * 100:.1f}%[/cyan]")

    # Routing
    console.print(
        f"\n  Mode: [yellow]{trace.retrieval_mode}[/yellow]"
        f"  |  Routing broadened: {'yes' if trace.routing.broadened else 'no'}"
        f"  |  Verification: {'yes' if trace.verification_applied else 'no'}"
    )
    console.print(f"  Routed docs: {trace.routing.selected_doc_ids}")

    # Sections
    if trace.sections_used:
        console.print(f"\n  Sections retrieved: {len(trace.sections_used)}")
        for sec in trace.sections_used:
            console.print(
                f"    [{sec.doc_id}] {sec.title}  "
                f"(p.{sec.page_start}–{sec.page_end}  ~{sec.estimated_tokens} tokens"
                f"{'  truncated' if sec.truncated else ''})"
            )

    # Answer preview
    console.print(
        Panel(
            trace.answer[:600] + ("…" if len(trace.answer) > 600 else ""),
            title="Answer (preview)",
            expand=False,
        )
    )


@traces_group.command("stats")
@click.option("--project", default=DEFAULT_PROJECT, show_default=True)
def traces_stats_command(project: str) -> None:
    """Show aggregated statistics for a project's audit traces."""
    runtime = _build_runtime(DEFAULT_MODEL, project=project)
    stats = runtime.trace_service.get_stats()

    if stats.total_queries == 0:
        console.print(f"No traces found for project: {project}")
        return

    table = Table(title=f"Trace Stats — project: {project}", show_header=False)
    table.add_column("metric", style="bold")
    table.add_column("value")
    table.add_row("Total queries", str(stats.total_queries))
    table.add_row("Avg latency", f"{stats.avg_latency_seconds:.2f}s")
    table.add_row("Avg TTFT", f"{stats.avg_ttft_seconds:.2f}s")
    table.add_row("Avg tokens", f"{stats.avg_tokens_total:,.0f}")
    table.add_row("Avg docs routed", f"{stats.avg_docs_routed:.1f}")
    table.add_row("Avg sections retrieved", f"{stats.avg_sections_retrieved:.1f}")
    table.add_row("Broadened routing rate", f"{stats.broadened_routing_rate * 100:.1f}%")
    if stats.total_estimated_cost_usd is not None:
        table.add_row("Total estimated cost", f"${stats.total_estimated_cost_usd:.4f}")
    console.print(table)

    if stats.most_accessed_docs:
        console.print("\nMost accessed docs:")
        for doc_id, count in stats.most_accessed_docs:
            console.print(f"  {doc_id}: {count} quer{'y' if count == 1 else 'ies'}")


@traces_group.command("export")
@click.option("--project", default=DEFAULT_PROJECT, show_default=True)
@click.option(
    "--format", "fmt",
    type=click.Choice(["json", "csv"], case_sensitive=False),
    default="json",
    show_default=True,
)
@click.option("--limit", default=None, type=int, help="Max number of traces to export.")
def traces_export_command(project: str, fmt: str, limit: int | None) -> None:
    """Export all audit traces for a project as JSON or CSV (stdout)."""
    runtime = _build_runtime(DEFAULT_MODEL, project=project)
    if fmt == "json":
        data = runtime.trace_service.export_json(limit=limit)
        click.echo(json.dumps(data, indent=2, default=str))
    else:
        csv_str = runtime.trace_service.export_csv(limit=limit)
        click.echo(csv_str, nl=False)


if __name__ == "__main__":
    cli()
