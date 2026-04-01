"""Streamlit frontend for ingestion, querying, and internal tracing."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from queue import Empty, Queue
from threading import Thread
from uuid import uuid4

import streamlit as st
from dotenv import load_dotenv

from ingestion.ingest import IngestionResult, ingest_document_with_trace
from index_registry import IndexContext, build_runtime_components
from model_registry import ModelRegistry
from retrieval.query_engine import QueryResult, query
from utils import REASONING_EFFORT_OPTIONS, detect_llm_provider, get_default_model

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
UPLOADS_DIR = DATA_DIR / "uploads"
MODEL_REGISTRY_PATH = DATA_DIR / "model_registry.json"

UPLOADS_DIR.mkdir(parents=True, exist_ok=True)


def run_async_task(coro):
    """Run one coroutine in a background thread and return its final result.

    Junior note:
    Streamlit is synchronous from the script's point of view, so we bridge into
    `asyncio` by running the coroutine inside a worker thread.
    """
    result_queue: Queue = Queue()

    def _runner() -> None:
        """Execute the coroutine inside a fresh event loop."""
        import asyncio

        try:
            result_queue.put((True, asyncio.run(coro)))
        except Exception as exc:  # pragma: no cover - UI path
            result_queue.put((False, exc))

    thread = Thread(target=_runner, daemon=True)
    thread.start()
    thread.join()
    succeeded, payload = result_queue.get()
    if succeeded:
        return payload
    raise payload


def run_async_task_with_progress(coro_factory, on_progress=None, poll_interval: float = 0.1):
    """Run an async task while polling progress events for live UI updates."""
    result_queue: Queue = Queue()
    progress_queue: Queue = Queue()

    def _progress_callback(event: dict) -> None:
        """Bridge worker-thread progress events into the main polling queue."""
        progress_queue.put(event)

    def _runner() -> None:
        """Execute the coroutine factory with the progress callback installed."""
        import asyncio

        try:
            result_queue.put((True, asyncio.run(coro_factory(_progress_callback))))
        except Exception as exc:  # pragma: no cover - UI path
            result_queue.put((False, exc))

    thread = Thread(target=_runner, daemon=True)
    thread.start()

    # We poll instead of calling `thread.join()` immediately so Streamlit can keep
    # updating the status area while ingestion is still working in the background.
    while thread.is_alive() or not progress_queue.empty():
        while True:
            try:
                event = progress_queue.get_nowait()
            except Empty:
                break
            if on_progress is not None:
                on_progress(event)
        if not thread.is_alive() and not result_queue.empty():
            break
        time.sleep(poll_interval)

    thread.join()

    while True:
        try:
            event = progress_queue.get_nowait()
        except Empty:
            break
        if on_progress is not None:
            on_progress(event)

    succeeded, payload = result_queue.get()
    if succeeded:
        return payload
    raise payload


def slugify(value: str) -> str:
    """Convert a title or filename into a stable machine-friendly identifier."""
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", value.strip().lower()).strip("_")
    return slug or "document"


def build_runtime(model: str):
    """Construct the model-scoped runtime bundle used by the UI."""
    return build_runtime_components(DATA_DIR, model=model)


def render_styles() -> None:
    """Inject the custom CSS that defines the app's visual design."""
    st.markdown(
        """
        <style>
            .stApp {
                background:
                    radial-gradient(circle at top left, rgba(16, 185, 129, 0.10), transparent 28%),
                    radial-gradient(circle at top right, rgba(14, 165, 233, 0.14), transparent 26%),
                    linear-gradient(180deg, #f4f1e8 0%, #fdfcf7 48%, #ffffff 100%);
                color: #14213d;
            }
            .hero {
                padding: 1.4rem 1.6rem;
                border: 1px solid rgba(20, 33, 61, 0.08);
                border-radius: 22px;
                background: rgba(255, 255, 255, 0.82);
                box-shadow: 0 18px 50px rgba(15, 23, 42, 0.08);
                margin-bottom: 1rem;
            }
            .hero h1 {
                font-family: "Avenir Next", "Segoe UI", sans-serif;
                letter-spacing: 0.02em;
                margin: 0;
                font-size: 2.2rem;
            }
            .hero p {
                margin: 0.45rem 0 0 0;
                color: #31536b;
                max-width: 58rem;
            }
            .metric-card {
                border: 1px solid rgba(20, 33, 61, 0.08);
                border-radius: 18px;
                padding: 0.9rem 1rem;
                background: rgba(255, 255, 255, 0.82);
                margin-bottom: 0.8rem;
            }
            .metric-card h4 {
                margin: 0 0 0.2rem 0;
                font-size: 0.85rem;
                text-transform: uppercase;
                letter-spacing: 0.08em;
                color: #5b7285;
            }
            .metric-card p {
                margin: 0;
                font-size: 1.1rem;
                color: #14213d;
            }
            .chunk-card {
                border-left: 4px solid #0ea5e9;
                background: rgba(255, 255, 255, 0.88);
                padding: 0.9rem 1rem;
                border-radius: 14px;
                margin-bottom: 0.8rem;
            }
            .stFileUploader label,
            .stTextInput label,
            .stSelectbox label,
            .stTextArea label,
            .stNumberInput label,
            .stRadio label,
            .stMarkdown,
            .stCaption,
            .stStatus label {
                color: #1f3559 !important;
            }
            [data-testid="stFileUploaderDropzone"] {
                background: #1e2430;
                border: 1px solid rgba(255, 255, 255, 0.08);
            }
            [data-testid="stFileUploaderDropzone"] * {
                color: #f4f7fb !important;
            }
            .stTextInput input {
                background: #262b36;
                color: #f7fafc;
            }
            .stButton button {
                background: #162032;
                color: #f8fafc;
                border: 1px solid rgba(20, 33, 61, 0.18);
            }
            .stButton button:hover {
                background: #1f2d46;
                color: #ffffff;
            }
            button[data-baseweb="tab"] {
                color: #1f2937 !important;
            }
            button[data-baseweb="tab"] p {
                color: #1f2937 !important;
                font-weight: 600;
            }
            button[data-baseweb="tab"][aria-selected="true"] {
                color: #111827 !important;
            }
            button[data-baseweb="tab"][aria-selected="true"] p {
                color: #111827 !important;
            }
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_metric(label: str, value: str) -> None:
    """Render one small metric card used throughout the UI."""
    st.markdown(
        f"""
        <div class="metric-card">
            <h4>{label}</h4>
            <p>{value}</p>
        </div>
        """,
        unsafe_allow_html=True,
    )


def ensure_session_state() -> None:
    """Initialize all Streamlit session-state keys used by the app."""
    st.session_state.setdefault("chat_history", [])
    st.session_state.setdefault("latest_query", None)
    st.session_state.setdefault("latest_ingestion", None)
    st.session_state.setdefault("selected_doc_id", None)
    st.session_state.setdefault("active_index_key", None)
    st.session_state.setdefault("retrieval_reasoning_effort", "auto")


def render_sidebar(
    master_tree_store,
    index_context: IndexContext,
    model_registry: ModelRegistry,
) -> tuple[str, int, str | None]:
    """Render runtime controls and return the current settings."""
    st.sidebar.markdown("## Runtime")
    current_model = st.session_state.get("model_name", get_default_model())
    model_registry.ensure_model(current_model)
    registered_models = model_registry.list_models()
    selected_index = registered_models.index(current_model) if current_model in registered_models else 0
    model = st.sidebar.selectbox(
        "Registered model",
        options=registered_models,
        index=selected_index,
        help="For Azure, this should be your deployment name.",
    )
    max_docs = st.sidebar.slider("Max docs", min_value=1, max_value=5, value=3)
    reasoning_options = ["auto", *REASONING_EFFORT_OPTIONS]
    current_reasoning = st.session_state.get("retrieval_reasoning_effort", "auto")
    if current_reasoning not in reasoning_options:
        current_reasoning = "auto"
    reasoning_effort = st.sidebar.selectbox(
        "Retrieval reasoning",
        options=reasoning_options,
        index=reasoning_options.index(current_reasoning),
        help="Used for routing, tree navigation, and final answer synthesis. Unsupported models will ignore it automatically.",
    )
    st.session_state["model_name"] = model
    st.session_state["retrieval_reasoning_effort"] = reasoning_effort
    st.sidebar.caption(f"Provider: `{detect_llm_provider()}`")
    st.sidebar.caption(f"Index: `{index_context.index_key}`")
    st.sidebar.caption("Ingestion reasoning: `high`")

    with st.sidebar.expander("Register model"):
        with st.form("register_model_form", clear_on_submit=True):
            new_model_name = st.text_input(
                "New model/deployment",
                key="model_registration_input",
            )
            st.caption("Add a model once, then select it from the dropdown.")
            submitted = st.form_submit_button("Add model", use_container_width=True)

        if submitted:
            try:
                added_model = model_registry.add_model(new_model_name)
            except ValueError as exc:
                st.error(str(exc))
            else:
                st.session_state["model_name"] = added_model
                st.rerun()

    docs = master_tree_store.list_docs()
    st.sidebar.markdown("## Indexed Docs")
    if docs:
        for doc in docs:
            st.sidebar.markdown(f"- `{doc.doc_id}`")
    else:
        st.sidebar.caption("No documents ingested yet.")

    st.sidebar.markdown("## Storage")
    st.sidebar.caption(
        f"This app uses model-scoped local JSON/file storage at `{index_context.index_dir}`."
    )
    return model, max_docs, None if reasoning_effort == "auto" else reasoning_effort


def save_uploaded_file(uploaded_file, doc_id: str) -> Path:
    """Persist an uploaded file into the local uploads folder."""
    suffix = Path(uploaded_file.name).suffix
    destination = UPLOADS_DIR / f"{doc_id}_{uuid4().hex[:8]}{suffix}"
    destination.write_bytes(uploaded_file.getbuffer())
    return destination


def create_ingestion_progress_renderer(status):
    """Build a callback that turns ingestion progress events into live UI updates.

    Junior note:
    This function returns another function (`_handle`). Returning functions is a
    normal Python pattern called a closure: the inner function keeps access to the
    placeholders and local `state` dictionary defined here.
    """
    stage_placeholder = st.empty()
    summary_bar = st.empty()
    summary_caption = st.empty()
    token_caption = st.empty()
    node_costs = st.empty()
    event_log = st.empty()
    state = {
        "recent_messages": [],
        "summary_total": 0,
        "summary_completed": 0,
        "total_input_tokens": 0,
        "total_output_tokens": 0,
        "recent_node_costs": [],
    }

    def _render_recent_events() -> None:
        """Show a rolling window of the latest ingestion events."""
        if not state["recent_messages"]:
            return
        event_log.markdown(
            "**Recent progress**\n" + "\n".join(f"- {message}" for message in state["recent_messages"][-8:])
        )

    def _render_token_metrics() -> None:
        """Show running token totals plus the last few node-summary costs."""
        total_input = state["total_input_tokens"]
        total_output = state["total_output_tokens"]
        token_caption.caption(
            f"Estimated summary tokens so far: input {total_input:,} | output {total_output:,} | total {total_input + total_output:,}"
        )
        if not state["recent_node_costs"]:
            return
        lines = [
            f"- `{item['title']}`: in {item['input_tokens']:,} | out {item['output_tokens']:,} | total {item['total_tokens']:,}{' | skipped LLM' if not item['used_model'] else ''}"
            for item in state["recent_node_costs"][-6:]
        ]
        node_costs.markdown("**Recent node token usage**\n" + "\n".join(lines))

    def _handle(event: dict) -> None:
        """Apply one progress event emitted by the ingestion pipeline."""
        message = str(event.get("message", "Working..."))
        state["recent_messages"].append(message)
        stage_placeholder.markdown(f"**Current activity:** {message}")

        event_type = event.get("event")
        step_name = event.get("step")
        if event_type == "pageindex_summary_batch_started":
            total = int(event.get("total_nodes", 0) or 0)
            state["summary_total"] = total
            state["summary_completed"] = 0
            state["total_input_tokens"] = int(event.get("total_input_tokens", 0) or 0)
            state["total_output_tokens"] = int(event.get("total_output_tokens", 0) or 0)
            state["recent_node_costs"] = []
            summary_bar.progress(
                0.0,
                text=f"Generating node summaries: 0/{total}" if total else "Generating node summaries...",
            )
            summary_caption.caption("PageIndex is generating summaries for the tree nodes.")
            status.update(label="Generating node summaries", state="running")
        elif event_type == "pageindex_summary_node_completed":
            total = int(event.get("total_nodes", state["summary_total"]) or 0)
            completed = int(event.get("completed_nodes", 0) or 0)
            title = str(event.get("title") or "Untitled section")
            state["summary_total"] = total
            state["summary_completed"] = completed
            state["total_input_tokens"] = int(event.get("total_input_tokens", 0) or 0)
            state["total_output_tokens"] = int(event.get("total_output_tokens", 0) or 0)
            state["recent_node_costs"].append(
                {
                    "title": title,
                    "used_model": bool(event.get("used_model", True)),
                    "input_tokens": int(event.get("estimated_input_tokens", 0) or 0),
                    "output_tokens": int(event.get("estimated_output_tokens", 0) or 0),
                    "total_tokens": int(event.get("estimated_total_tokens", 0) or 0),
                }
            )
            ratio = (completed / total) if total else 0.0
            summary_bar.progress(
                min(1.0, ratio),
                text=f"Generating node summaries: {completed}/{total}" if total else "Generating node summaries...",
            )
            summary_caption.caption(f"Latest completed node: {title}")
            status.update(label=f"Generating node summaries ({completed}/{total})", state="running")
        elif event_type == "pageindex_summary_batch_completed":
            total = int(event.get("total_nodes", state["summary_total"]) or 0)
            completed = int(event.get("completed_nodes", total) or total)
            state["total_input_tokens"] = int(event.get("total_input_tokens", 0) or 0)
            state["total_output_tokens"] = int(event.get("total_output_tokens", 0) or 0)
            if total:
                summary_bar.progress(1.0, text=f"Generating node summaries: {completed}/{total}")
            summary_caption.caption("Node-summary generation finished.")
            status.update(label="Finishing PageIndex tree build", state="running")
        elif event_type == "pageindex_started":
            status.update(label="Building PageIndex tree", state="running")
        elif event_type == "master_node_started":
            status.update(label="Generating master node", state="running")
        elif event_type == "ingestion_step" and step_name == "persist_tree":
            status.update(label="Saving tree artifacts", state="running")
        elif event_type == "ingestion_step" and step_name == "build_pageindex_tree":
            status.update(label="PageIndex tree ready", state="running")
        elif event_type == "ingestion_complete":
            status.update(label="Ingestion complete", state="complete")

        _render_token_metrics()
        _render_recent_events()

    return _handle


def render_ingestion_trace(result: IngestionResult | None) -> None:
    """Render the post-ingestion trace once a document has finished processing."""
    if result is None:
        st.info("Upload a document to see the ingestion mapping.")
        return

    st.markdown("### Latest Ingestion")
    render_metric("Document", result.master_node.doc_title)
    render_metric("Doc ID", result.master_node.doc_id)
    render_metric("Tree Path", result.trace.tree_path)

    st.markdown("#### Master Node Snapshot")
    st.json(result.master_node.model_dump(mode="json"))

    st.markdown("#### Step Trace")
    for step in result.trace.steps:
        with st.expander(f"{step.name}: {step.detail}", expanded=False):
            if step.payload is not None:
                st.json(step.payload)


def render_query_trace(result: QueryResult | None) -> None:
    """Render the internal retrieval trace for the latest query."""
    if result is None:
        st.info("Ask a question to inspect routing, navigation, and chunk assembly.")
        return

    st.markdown("### Latest Query Mapping")
    render_metric("Selected Docs", ", ".join(result.selected_docs) or "None")
    render_metric("Selected Nodes", ", ".join(result.selected_nodes) or "None")

    if result.trace is not None:
        with st.expander("Navigation Map", expanded=True):
            st.json(result.trace.navigation)

        with st.expander("Retrieved Chunks", expanded=True):
            if not result.trace.fetched_chunks:
                st.caption("No chunks retrieved.")
            for chunk in result.trace.fetched_chunks:
                st.markdown(
                    f"""
                    <div class="chunk-card">
                        <strong>{chunk.title}</strong><br/>
                        <code>{chunk.node_ref}</code><br/>
                        Tokens: {chunk.estimated_tokens}
                        {" | Truncated" if chunk.truncated else ""}
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
                st.code(chunk.text, language="markdown")

        with st.expander("Final Retrieved Context", expanded=False):
            st.code(result.retrieved_context or "[empty]", language="markdown")


def render_internal_map(master_tree_store, arch_map, index_context: IndexContext) -> None:
    """Show the high-level architecture and currently loaded index artifacts."""
    st.markdown("### Internal Mapping")
    st.markdown(
        """
        1. Ingestion validates the file, runs PageIndex, saves the per-doc tree, generates a master node, and upserts the master tree.
        2. Querying routes across the master tree, navigates each selected doc tree, fetches raw node text, then answers from the combined chunks.
        3. Chunk order matters: chunks are concatenated in navigator-ranked order and truncated only when the token budget is reached.
        """
    )

    st.caption(
        f"Active index scope: provider `{index_context.provider}`, model `{index_context.model}`, key `{index_context.index_key}`."
    )

    col_a, col_b = st.columns(2)
    with col_a:
        st.markdown("#### Master Tree")
        st.json(master_tree_store.tree.model_dump(mode="json"))
    with col_b:
        st.markdown("#### Architecture Map")
        arch_map_context = arch_map.to_llm_context()
        if arch_map_context:
            st.code(arch_map_context, language="json")
        else:
            st.info("No architecture map loaded.")


def _extract_tree_nodes(tree: dict | list | None) -> list[dict]:
    """Normalize different saved PageIndex shapes into a plain node list."""
    if isinstance(tree, list):
        return [node for node in tree if isinstance(node, dict)]

    if not isinstance(tree, dict):
        return []

    if isinstance(tree.get("structure"), list):
        return [node for node in tree["structure"] if isinstance(node, dict)]

    if isinstance(tree.get("nodes"), list):
        return [node for node in tree["nodes"] if isinstance(node, dict)]

    return []


def _count_tree_nodes(nodes: list[dict]) -> int:
    """Count a tree's nodes recursively."""
    total = 0
    for node in nodes:
        total += 1
        total += _count_tree_nodes(_extract_tree_nodes(node.get("nodes", [])))
    return total


def _format_node_metadata(node: dict) -> str:
    """Build the short metadata string shown beside each outline node."""
    metadata: list[str] = []
    if node.get("node_id"):
        metadata.append(str(node["node_id"]))
    if node.get("start_index") is not None and node.get("end_index") is not None:
        metadata.append(f"pages {node['start_index']}-{node['end_index']}")
    elif node.get("line_num") is not None:
        metadata.append(f"line {node['line_num']}")
    return " | ".join(metadata)


def _build_tree_outline_lines(nodes: list[dict], depth: int = 0) -> list[str]:
    """Convert a nested tree into indented outline lines for the Docs tab."""
    lines: list[str] = []
    indent = "  " * depth

    for node in nodes:
        label = node.get("title", "Untitled section")
        metadata = _format_node_metadata(node)
        if metadata:
            lines.append(f"{indent}- {label} [{metadata}]")
        else:
            lines.append(f"{indent}- {label}")

        summary = node.get("summary") or node.get("prefix_summary")
        if summary:
            lines.append(f"{indent}  {summary}")

        child_nodes = _extract_tree_nodes(node.get("nodes", []))
        if child_nodes:
            lines.extend(_build_tree_outline_lines(child_nodes, depth + 1))

    return lines


def render_doc_browser(master_tree_store, storage, index_context: IndexContext) -> None:
    """Show one document's saved master node and PageIndex tree."""
    docs = master_tree_store.list_docs()
    st.markdown("### Document Browser")
    st.caption(f"Showing PageIndex output for the active model-specific index: `{index_context.index_key}`.")

    if not docs:
        st.info("Ingest a document first to inspect its PageIndex output.")
        return

    doc_options = [doc.doc_id for doc in docs]
    default_doc_id = st.session_state.get("selected_doc_id")
    if default_doc_id not in doc_options:
        latest_ingestion = st.session_state.get("latest_ingestion")
        latest_doc_id = (
            latest_ingestion.master_node.doc_id if latest_ingestion is not None else doc_options[0]
        )
        default_doc_id = latest_doc_id if latest_doc_id in doc_options else doc_options[0]

    selected_doc_id = st.selectbox(
        "Choose a document",
        options=doc_options,
        index=doc_options.index(default_doc_id),
    )
    st.session_state["selected_doc_id"] = selected_doc_id

    master_node = master_tree_store.get_node(selected_doc_id)
    if master_node is None:
        st.error(f"Document '{selected_doc_id}' is missing from the master tree.")
        return

    try:
        per_doc_tree = storage.load_doc_tree(selected_doc_id)
    except FileNotFoundError as exc:
        st.error(str(exc))
        return

    root_nodes = _extract_tree_nodes(per_doc_tree)
    node_count = _count_tree_nodes(root_nodes)
    outline_lines = _build_tree_outline_lines(root_nodes)

    overview_col, refs_col = st.columns([1.05, 0.95])
    with overview_col:
        render_metric("Document", master_node.doc_title)
        render_metric("Doc Type", master_node.doc_type)
        render_metric("Total Nodes", str(node_count))
    with refs_col:
        render_metric("Source Path", master_node.file_path)
        render_metric("Tree Path", master_node.tree_path)
        render_metric("Related Docs", ", ".join(master_node.related_docs) or "None")

    overview_tab, outline_tab, raw_tab = st.tabs(["Overview", "Outline", "Raw JSON"])

    with overview_tab:
        st.markdown("#### Master Node")
        st.json(master_node.model_dump(mode="json"))

    with outline_tab:
        st.markdown("#### PageIndex Outline")
        if outline_lines:
            st.code("\n".join(outline_lines), language="text")
        else:
            st.info("This document has no extracted outline nodes.")

    with raw_tab:
        st.markdown("#### PageIndex Tree JSON")
        st.json(per_doc_tree)


def main() -> None:
    """Assemble the full Streamlit application."""
    st.set_page_config(page_title="PageIndex Atlas", layout="wide")
    render_styles()
    ensure_session_state()

    model_registry = ModelRegistry(MODEL_REGISTRY_PATH)
    active_model = st.session_state.get("model_name", get_default_model())
    model_registry.ensure_model(active_model)
    runtime = build_runtime(active_model)
    model, max_docs, reasoning_effort = render_sidebar(
        runtime.master_tree_store,
        runtime.index_context,
        model_registry,
    )

    if model != active_model:
        st.session_state["model_name"] = model
        st.session_state["chat_history"] = []
        st.session_state["latest_query"] = None
        st.session_state["latest_ingestion"] = None
        st.session_state["selected_doc_id"] = None
        st.rerun()

    if st.session_state.get("active_index_key") != runtime.index_context.index_key:
        st.session_state["chat_history"] = []
        st.session_state["latest_query"] = None
        st.session_state["latest_ingestion"] = None
        st.session_state["selected_doc_id"] = None
        st.session_state["active_index_key"] = runtime.index_context.index_key

    st.markdown(
        """
        <div class="hero">
            <h1>PageIndex Atlas</h1>
            <p>Upload documents, inspect the ingestion trace, ask multi-document questions, and see exactly how routing, node selection, and chunk assembly work under the hood. Every index is scoped to a single provider/model.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    ingest_tab, ask_tab, docs_tab, map_tab = st.tabs(["Upload", "Ask", "Docs", "Map"])

    with ingest_tab:
        left, right = st.columns([1.05, 0.95])
        with left:
            st.markdown("### Add Document")
            st.caption("Upload a source file, choose a stable document id, give it a readable title, and set a broad type like `technical_spec` or `research_note`.")
            uploaded_file = st.file_uploader(
                "PDF, Markdown, or DOCX",
                type=["pdf", "md", "markdown", "docx"],
                accept_multiple_files=False,
            )
            default_doc_id = slugify(Path(uploaded_file.name).stem) if uploaded_file else ""
            doc_id = st.text_input("Document ID", value=default_doc_id)
            doc_title = st.text_input("Title")
            doc_type = st.text_input("Document Type", value="technical_spec")

            if st.button("Run Ingestion", use_container_width=True):
                if uploaded_file is None:
                    st.error("Upload a file first.")
                elif not doc_id or not doc_title or not doc_type:
                    st.error("Document ID, title, and type are required.")
                else:
                    saved_path = save_uploaded_file(uploaded_file, doc_id)
                    with st.status("Running ingestion", expanded=True) as status:
                        st.write(f"Saved upload to `{saved_path}`")
                        progress_handler = create_ingestion_progress_renderer(status)
                        result = run_async_task_with_progress(
                            lambda progress_callback: ingest_document_with_trace(
                                file_path=str(saved_path),
                                doc_id=doc_id,
                                doc_title=doc_title,
                                doc_type=doc_type,
                                master_tree_store=runtime.master_tree_store,
                                storage=runtime.storage,
                                model=model,
                                progress_callback=progress_callback,
                            ),
                            on_progress=progress_handler,
                        )
                        st.session_state.latest_ingestion = result
                        runtime.master_tree_store.load()
                        status.update(label="Ingestion complete", state="complete")
                    st.success(f"Ingested `{doc_id}`.")

        with right:
            render_ingestion_trace(st.session_state.latest_ingestion)

    with ask_tab:
        st.markdown("### Ask the Index")
        for turn in st.session_state.chat_history:
            with st.chat_message("user" if turn["role"] == "user" else "assistant"):
                st.markdown(turn["content"])

        prompt = st.chat_input("Ask about your indexed documents")
        if prompt:
            with st.spinner("Running router, navigator, fetcher, and answer synthesis..."):
                result = run_async_task(
                    query(
                        user_query=prompt,
                        master_tree_store=runtime.master_tree_store,
                        storage=runtime.storage,
                        arch_map=runtime.arch_map,
                        model=model,
                        chat_history=st.session_state.chat_history,
                        max_docs=max_docs,
                        verbose=False,
                        reasoning_effort=reasoning_effort,
                    )
                )
                st.session_state.chat_history = result.chat_history_updated
                st.session_state.latest_query = result
            st.rerun()

        latest_query = st.session_state.latest_query
        if latest_query is not None:
            st.markdown("### Answer")
            st.markdown(latest_query.answer)
            render_query_trace(latest_query)

    with docs_tab:
        render_doc_browser(runtime.master_tree_store, runtime.storage, runtime.index_context)

    with map_tab:
        render_internal_map(runtime.master_tree_store, runtime.arch_map, runtime.index_context)


if __name__ == "__main__":
    main()
