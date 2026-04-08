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
from index_registry import DEFAULT_PROJECT, IndexContext, build_runtime_components, delete_project, list_projects
from model_registry import ModelRegistry
from retrieval.query_engine import AdvancedRetrievalConfig, QueryResult, SourceReference, query
from traces.schema import AuditTrace, PostHocAnalysis
from utils import (
    REASONING_EFFORT_OPTIONS,
    RETRIEVAL_MODE_OPTIONS,
    detect_llm_provider,
    get_default_model,
    get_master_top_sections_range,
    get_master_top_sections_target,
    get_related_docs_mode,
    get_retrieval_mode,
    validate_doc_id,
)

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


def build_runtime(model: str, project: str | None = None):
    """Construct the model-scoped runtime bundle used by the UI."""
    return build_runtime_components(DATA_DIR, model=model, project=project)


def render_styles() -> None:
    """Inject the custom CSS that defines the app's visual design."""
    st.markdown(
        """
        <style>
            /* ════════════════════════════════════════════════════════════════
               Palette
               Main bg       #fafafa   (zinc-50)
               Surface       #ffffff
               Border        #e4e4e7   (zinc-200)  — hairline, not a drawing
               Text primary  #18181b   (zinc-900)
               Text muted    #71717a   (zinc-500)
               Sidebar bg    #18181b   (zinc-900)
               Sidebar surf  #27272a   (zinc-800)
               Sidebar bord  #3f3f46   (zinc-700)
               Sidebar text  #d4d4d8   (zinc-300)
            ════════════════════════════════════════════════════════════════ */

            /* ── App base ────────────────────────────────────────────────── */
            .stApp { background: #fafafa !important; color: #18181b; }

            /* ── Hero ────────────────────────────────────────────────────── */
            .hero {
                padding: 1rem 1.4rem;
                background: #ffffff;
                border: 1px solid #e4e4e7;
                border-radius: 10px;
                margin-bottom: 1rem;
            }
            .hero h1 { margin: 0; font-size: 1.5rem; font-weight: 700; color: #18181b; }
            .hero p  { margin: 0.2rem 0 0; color: #71717a; font-size: 0.875rem; }

            /* ── Metric / chunk cards ────────────────────────────────────── */
            .metric-card {
                background: #ffffff; border: 1px solid #e4e4e7;
                border-radius: 8px; padding: 0.65rem 0.85rem; margin-bottom: 0.5rem;
            }
            .metric-card h4 {
                margin: 0 0 0.1rem; font-size: 0.7rem; font-weight: 600;
                text-transform: uppercase; letter-spacing: 0.06em; color: #71717a;
            }
            .metric-card p {
                margin: 0; font-size: 0.9rem; font-weight: 600;
                color: #18181b; word-break: break-all;
            }
            .chunk-card {
                background: #ffffff; border-left: 2px solid #a1a1aa;
                border-radius: 6px; padding: 0.65rem 0.85rem;
                margin-bottom: 0.5rem; color: #18181b; font-size: 0.875rem;
            }

            /* ── Main-area labels ────────────────────────────────────────── */
            .stMain .stTextInput label,  .stMain .stNumberInput label,
            .stMain .stTextArea label,   .stMain .stSelectbox label,
            .stMain .stFileUploader label, .stMain .stSlider label,
            .stMain .stCheckbox label,   .stMain .stRadio label,
            .stMain .stMarkdown p,       .stMain .stCaption p,
            .stMain .stAlert p {
                color: #3f3f46 !important;
                font-size: 0.875rem !important;
            }

            /* ── Text inputs (main) ──────────────────────────────────────── */
            .stMain .stTextInput input,
            .stMain .stNumberInput input,
            .stMain .stTextArea textarea {
                background: #ffffff !important;
                color: #18181b !important;
                border: 1px solid #e4e4e7 !important;
                border-radius: 6px !important;
                box-shadow: none !important;
            }
            .stMain .stTextInput input:focus,
            .stMain .stNumberInput input:focus,
            .stMain .stTextArea textarea:focus {
                border-color: #a1a1aa !important;
                box-shadow: none !important;
                outline: none !important;
            }

            /* ── Selectbox (main) ────────────────────────────────────────── */
            .stMain [data-baseweb="select"] > div {
                background: #ffffff !important;
                border: 1px solid #e4e4e7 !important;
                border-radius: 6px !important;
                color: #18181b !important;
            }

            /* ── Expanders — main content only (light) ───────────────────── */
            .stMain [data-testid="stExpander"] {
                background: #ffffff !important;
                border: 1px solid #e4e4e7 !important;
                border-radius: 8px !important;
                box-shadow: none !important;
            }
            .stMain [data-testid="stExpander"] summary {
                background: #ffffff !important;
                color: #18181b !important;
            }
            .stMain [data-testid="stExpander"] summary p,
            .stMain [data-testid="stExpander"] summary span {
                color: #18181b !important;
            }
            .stMain [data-testid="stExpander"] > div {
                background: #ffffff !important;
                color: #18181b !important;
            }

            /* ── File uploader ───────────────────────────────────────────── */
            [data-testid="stFileUploaderDropzone"] {
                background: #ffffff !important;
                border: 1.5px dashed #d4d4d8 !important;
                border-radius: 8px !important;
            }
            [data-testid="stFileUploaderDropzone"] span,
            [data-testid="stFileUploaderDropzone"] p {
                color: #71717a !important;
            }

            /* ── Buttons (main) — neutral, no colour on hover ────────────── */
            .stMain .stButton > button {
                background: #ffffff !important;
                color: #3f3f46 !important;
                border: 1px solid #e4e4e7 !important;
                border-radius: 6px !important;
                font-weight: 500 !important;
                padding: 0.35rem 1rem !important;
                box-shadow: none !important;
                transition: background 100ms ease !important;
            }
            .stMain .stButton > button:hover {
                background: #f4f4f5 !important;
                border-color: #d4d4d8 !important;
                color: #18181b !important;
            }
            .stMain .stButton > button:active { background: #e4e4e7 !important; }
            .stMain .stButton > button:disabled {
                background: #fafafa !important;
                border-color: #f4f4f5 !important;
                color: #a1a1aa !important;
            }

            /* Number input steppers ─────────────────────────────────────── */
            [data-testid="stNumberInput"] button {
                background: #f4f4f5 !important;
                color: #52525b !important;
                border: 1px solid #e4e4e7 !important;
                box-shadow: none !important;
            }
            [data-testid="stNumberInput"] button:hover {
                background: #e4e4e7 !important;
                color: #18181b !important;
            }

            /* ── Chat messages (main) ───────────────────────────────────── */
            .stMain [data-testid="stChatMessage"] {
                background: transparent !important;
                color: #18181b !important;
            }
            .stMain [data-testid="stChatMessage"] p,
            .stMain [data-testid="stChatMessage"] li,
            .stMain [data-testid="stChatMessage"] span,
            .stMain [data-testid="stChatMessage"] code,
            .stMain [data-testid="stChatMessage"] .stMarkdown {
                color: #18181b !important;
            }

            /* ── Checkbox (main) ─────────────────────────────────────────── */
            .stMain .stCheckbox label,
            .stMain .stCheckbox label p,
            .stMain .stCheckbox label span {
                color: #3f3f46 !important;
            }
            /* Disabled checkbox keeps label readable */
            .stMain .stCheckbox [aria-disabled="true"] label,
            .stMain .stCheckbox [aria-disabled="true"] label p,
            .stMain .stCheckbox [aria-disabled="true"] label span {
                color: #a1a1aa !important;
            }

            /* ── Multiselect (main) ──────────────────────────────────────── */
            .stMain [data-testid="stMultiSelect"] [data-baseweb="select"] > div {
                background: #ffffff !important;
                border: 1px solid #e4e4e7 !important;
                color: #18181b !important;
            }
            .stMain [data-testid="stMultiSelect"] span,
            .stMain [data-testid="stMultiSelect"] [data-testid="stMultiSelectTag"] {
                color: #18181b !important;
                background: #f4f4f5 !important;
            }

            /* ── Select/multiselect dropdown popover ─────────────────────── */
            [data-baseweb="popover"] li,
            [data-baseweb="popover"] [role="option"],
            [data-baseweb="menu"] li {
                color: #18181b !important;
                background: #ffffff !important;
            }
            [data-baseweb="popover"] li:hover,
            [data-baseweb="menu"] li:hover {
                background: #f4f4f5 !important;
            }

            /* ── Alert / info / warning / success boxes ──────────────────── */
            .stMain [data-testid="stAlert"] p,
            .stMain [data-testid="stAlert"] span,
            .stMain [data-testid="stNotification"] p,
            .stMain [data-testid="stNotification"] span {
                color: #18181b !important;
            }

            /* ── Tabs ────────────────────────────────────────────────────── */
            button[data-baseweb="tab"] {
                color: #71717a !important;
                font-weight: 500 !important;
                background: transparent !important;
            }
            button[data-baseweb="tab"][aria-selected="true"] {
                color: #18181b !important;
                font-weight: 600 !important;
            }

            /* ════════════════════════════════════════════════════════════════
               SIDEBAR — everything here is dark zinc
            ════════════════════════════════════════════════════════════════ */
            [data-testid="stSidebar"] {
                background: #18181b !important;
            }

            /* Section labels */
            [data-testid="stSidebar"] h3 {
                color: #52525b !important;
                font-size: 0.65rem !important;
                font-weight: 700 !important;
                text-transform: uppercase !important;
                letter-spacing: 0.12em !important;
                margin: 1.4rem 0 0.3rem !important;
                padding-bottom: 0.3rem !important;
                border-bottom: 1px solid #27272a !important;
            }

            /* All sidebar text labels */
            [data-testid="stSidebar"] label {
                color: #a1a1aa !important;
                font-size: 0.82rem !important;
            }
            [data-testid="stSidebar"] .stMarkdown p,
            [data-testid="stSidebar"] .stCaption p,
            [data-testid="stSidebar"] li {
                color: #71717a !important;
                font-size: 0.82rem !important;
            }

            /* Sidebar selects */
            [data-testid="stSidebar"] [data-baseweb="select"] > div {
                background: #27272a !important;
                border: 1px solid #3f3f46 !important;
                border-radius: 6px !important;
                color: #d4d4d8 !important;
            }
            [data-testid="stSidebar"] [data-baseweb="select"] span,
            [data-testid="stSidebar"] [data-baseweb="select"] div {
                color: #d4d4d8 !important;
            }

            /* Sidebar text inputs */
            [data-testid="stSidebar"] .stTextInput input,
            [data-testid="stSidebar"] .stNumberInput input {
                background: #27272a !important;
                color: #d4d4d8 !important;
                border: 1px solid #3f3f46 !important;
                border-radius: 6px !important;
            }
            [data-testid="stSidebar"] .stTextInput input::placeholder {
                color: #52525b !important;
            }

            /* Sidebar expanders — dark, NOT white */
            [data-testid="stSidebar"] [data-testid="stExpander"] {
                background: #27272a !important;
                border: 1px solid #3f3f46 !important;
                border-radius: 8px !important;
            }
            [data-testid="stSidebar"] [data-testid="stExpander"] summary {
                background: #27272a !important;
            }
            [data-testid="stSidebar"] [data-testid="stExpander"] summary p,
            [data-testid="stSidebar"] [data-testid="stExpander"] summary span,
            [data-testid="stSidebar"] [data-testid="stExpander"] > div,
            [data-testid="stSidebar"] [data-testid="stExpander"] > div * {
                background: #27272a !important;
                color: #d4d4d8 !important;
            }
            [data-testid="stSidebar"] [data-testid="stExpander"] label {
                color: #a1a1aa !important;
            }

            /* Sidebar buttons */
            [data-testid="stSidebar"] .stButton > button {
                background: #27272a !important;
                color: #d4d4d8 !important;
                border: 1px solid #3f3f46 !important;
                border-radius: 6px !important;
                font-weight: 500 !important;
                box-shadow: none !important;
            }
            [data-testid="stSidebar"] .stButton > button:hover {
                background: #3f3f46 !important;
                border-color: #52525b !important;
                color: #f4f4f5 !important;
            }

            /* Sidebar code spans */
            [data-testid="stSidebar"] code {
                background: #27272a !important;
                color: #a1a1aa !important;
                border-radius: 4px;
                padding: 1px 5px;
                font-size: 0.8rem;
            }

            /* Sidebar slider track label */
            [data-testid="stSidebar"] .stSlider label,
            [data-testid="stSidebar"] .stSlider [data-testid="stTickBarMin"],
            [data-testid="stSidebar"] .stSlider [data-testid="stTickBarMax"] {
                color: #71717a !important;
            }

            /* ── st.code blocks (main area) — force light theme ─────────────
               Streamlit ships code blocks with a dark background regardless of
               the app theme.  Override the pre/code wrapper and the syntax
               highlight tokens so they stay readable on the light canvas.    */
            .stMain [data-testid="stCode"],
            .stMain [data-testid="stCode"] pre,
            .stMain [data-testid="stCode"] code,
            .stMain .stCode pre,
            .stMain .stCode code {
                background: #f4f4f5 !important;
                color: #18181b !important;
                border: 1px solid #e4e4e7 !important;
                border-radius: 6px !important;
            }
            /* Syntax highlight token colours — keep them readable on light bg */
            .stMain [data-testid="stCode"] .token.string,
            .stMain [data-testid="stCode"] .token.attr-value   { color: #166534 !important; }
            .stMain [data-testid="stCode"] .token.keyword,
            .stMain [data-testid="stCode"] .token.operator     { color: #7c3aed !important; }
            .stMain [data-testid="stCode"] .token.number,
            .stMain [data-testid="stCode"] .token.boolean      { color: #b45309 !important; }
            .stMain [data-testid="stCode"] .token.comment      { color: #6b7280 !important; }
            .stMain [data-testid="stCode"] .token.property,
            .stMain [data-testid="stCode"] .token.attr-name    { color: #1d4ed8 !important; }
            /* Copy button on code blocks */
            .stMain [data-testid="stCode"] button {
                background: #e4e4e7 !important;
                color: #18181b !important;
                border: 1px solid #d4d4d8 !important;
            }
            .stMain [data-testid="stCode"] button:hover {
                background: #d4d4d8 !important;
            }
            /* Inline code spans in markdown */
            .stMain .stMarkdown code,
            .stMain .stText code {
                background: #f4f4f5 !important;
                color: #18181b !important;
                border: 1px solid #e4e4e7 !important;
                border-radius: 3px !important;
                padding: 1px 5px !important;
                font-size: 0.875em !important;
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
    st.session_state.setdefault("project_name", "")


def render_sidebar(
    master_tree_store,
    index_context: IndexContext,
    model_registry: ModelRegistry,
) -> tuple[str, int, str | None, str, AdvancedRetrievalConfig]:
    """Render runtime controls and return (model, max_docs, reasoning_effort, project, adv_config)."""
    st.sidebar.markdown("### Project")

    # ── Project selector (primary) ────────────────────────────────────────────
    existing_projects = list_projects(DATA_DIR)
    current_project = st.session_state.get("project_name", DEFAULT_PROJECT) or DEFAULT_PROJECT
    # Ensure the active project appears even if it's brand new (not yet on disk).
    if current_project not in existing_projects:
        existing_projects = [current_project] + existing_projects

    project = st.sidebar.selectbox(
        "Active project",
        options=existing_projects,
        index=existing_projects.index(current_project),
        help="Each project is an independent document collection with its own index.",
    )
    st.session_state["project_name"] = project

    with st.sidebar.expander("Create project"):
        with st.form("create_project_form", clear_on_submit=True):
            new_project_name = st.text_input(
                "New project name",
                key="new_project_input",
                help="Lowercase letters, numbers, and hyphens recommended.",
            )
            create_submitted = st.form_submit_button("Create", use_container_width=True)
        if create_submitted and new_project_name.strip():
            st.session_state["project_name"] = new_project_name.strip()
            st.rerun()

    with st.sidebar.expander("Delete project"):
        st.caption(
            f"This will permanently remove **{project}** and all its indexed "
            "documents. This cannot be undone."
        )
        with st.form("delete_project_form", clear_on_submit=True):
            confirmed = st.checkbox(
                f'Yes, permanently delete "{project}"',
                key="delete_project_confirm",
            )
            delete_submitted = st.form_submit_button(
                "Delete project", use_container_width=True, type="primary"
            )
        if delete_submitted:
            if not confirmed:
                st.sidebar.error("Check the confirmation box first.")
            else:
                delete_project(DATA_DIR, project)
                st.session_state["project_name"] = DEFAULT_PROJECT
                st.rerun()

    st.sidebar.markdown("### Model")

    # ── Model selector (secondary — LLM calls only, not storage) ─────────────
    current_model = st.session_state.get("model_name", get_default_model())
    model_registry.ensure_model(current_model)
    registered_models = model_registry.list_models()
    selected_index = registered_models.index(current_model) if current_model in registered_models else 0
    model = st.sidebar.selectbox(
        "LLM model",
        options=registered_models,
        index=selected_index,
        help="Switching models does not change which documents are visible — only the project does that.",
    )
    st.session_state["model_name"] = model

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

    st.sidebar.markdown("### Query")
    max_docs = st.sidebar.slider("Max docs", min_value=1, max_value=15, value=3)
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
    st.session_state["retrieval_reasoning_effort"] = reasoning_effort

    retrieval_mode_options = list(RETRIEVAL_MODE_OPTIONS)
    default_ret_mode = get_retrieval_mode()
    default_ret_idx = retrieval_mode_options.index(default_ret_mode) if default_ret_mode in retrieval_mode_options else 0
    retrieval_mode_ui = st.sidebar.selectbox(
        "Retrieval mode",
        options=retrieval_mode_options,
        index=default_ret_idx,
        help=(
            "**hybrid** (default): deterministic, bounded, fast. Best for most questions.\n\n"
            "**pageindex**: agentic tool-use loop. Higher latency and cost, but can explore "
            "documents more deeply. Better for hard multi-document questions."
        ),
    )
    if retrieval_mode_ui == "pageindex":
        st.sidebar.caption(
            ":warning: PageIndex mode uses more LLM calls and costs more. "
            "Best for complex multi-document questions."
        )

    # ── Advanced retrieval controls ───────────────────────────────────────────
    st.sidebar.markdown("### Advanced Retrieval")
    adv_enabled = st.sidebar.toggle(
        "Enable advanced retrieval",
        value=False,
        help=(
            "Enables query-intent planning, adaptive retrieval width, and node-neighborhood "
            "expansion. Improves completeness for broad/workflow questions at the cost of "
            "higher latency and token usage."
        ),
    )
    adv_config = AdvancedRetrievalConfig(enabled=adv_enabled)
    if adv_enabled:
        st.sidebar.caption(
            ":warning: Higher latency and token cost. Best for broad, workflow, or "
            "multi-document questions."
        )
        with st.sidebar.expander("Advanced options"):
            adv_config.enable_planning = st.checkbox("Query planning", value=True, help="Classify query intent and recommend retrieval width.")
            adv_config.enable_adaptive_width = st.checkbox("Adaptive width", value=True, help="Use planner output to adjust max docs/nodes.")
            adv_config.enable_node_expansion = st.checkbox("Node expansion", value=True, help="Add bounded neighboring context around selected nodes.")
            adv_config.max_docs_cap = st.slider("Max docs cap", min_value=1, max_value=10, value=6, help="Hard cap on docs routed in advanced mode.")
            adv_config.max_nodes_cap = st.slider("Max nodes cap", min_value=1, max_value=10, value=6, help="Hard cap on nodes per doc in advanced mode.")

    st.sidebar.caption(f"Provider: `{detect_llm_provider()}`")
    st.sidebar.caption(f"Project: `{index_context.project}`")
    st.sidebar.caption(f"Model: `{model}`")
    st.sidebar.caption("Ingestion reasoning: `high`")

    docs = master_tree_store.list_docs()
    st.sidebar.markdown("### Indexed Docs")
    if docs:
        for doc in docs:
            st.sidebar.markdown(f"- `{doc.doc_id}`")
    else:
        st.sidebar.caption("No documents ingested yet.")

    st.sidebar.markdown("### Storage")
    st.sidebar.caption(f"Project index at `{index_context.index_dir}`.")

    return model, max_docs, None if reasoning_effort == "auto" else reasoning_effort, project, adv_config, retrieval_mode_ui


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
            status.update(label="Generating node summaries", state="running", expanded=True)
        elif event_type == "pageindex_summary_node_started":
            total = int(event.get("total_nodes", state["summary_total"]) or 0)
            started = int(event.get("started_nodes", 0) or 0)
            title = str(event.get("title") or "Untitled section")
            state["summary_total"] = max(state["summary_total"], total)
            summary_caption.caption(f"Currently summarizing node {started}/{total}: {title}" if total else f"Currently summarizing: {title}")
        elif event_type == "pageindex_summary_node_completed":
            total = int(event.get("total_nodes", state["summary_total"]) or 0)
            completed = int(event.get("completed_nodes", 0) or 0)
            title = str(event.get("title") or "Untitled section")
            display_total = max(state["summary_total"], total, completed)
            state["summary_total"] = display_total
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
            ratio = (completed / display_total) if display_total else 0.0
            summary_bar.progress(
                min(1.0, ratio),
                text=f"Generating node summaries: {completed}/{display_total}" if display_total else "Generating node summaries...",
            )
            summary_caption.caption(f"Latest completed node: {title}")
        elif event_type == "pageindex_summary_batch_completed":
            total = int(event.get("total_nodes", state["summary_total"]) or 0)
            completed = int(event.get("completed_nodes", total) or total)
            display_total = max(state["summary_total"], total, completed)
            state["total_input_tokens"] = int(event.get("total_input_tokens", 0) or 0)
            state["total_output_tokens"] = int(event.get("total_output_tokens", 0) or 0)
            if display_total:
                summary_bar.progress(1.0, text=f"Generating node summaries: {completed}/{display_total}")
            summary_caption.caption("Node-summary generation finished.")
            status.update(label="Finishing PageIndex tree build", state="running", expanded=True)
        elif event_type == "pageindex_started":
            status.update(label="Building PageIndex tree", state="running", expanded=True)
        elif event_type == "master_node_started":
            status.update(label="Generating master node", state="running", expanded=True)
        elif event_type == "ingestion_step" and step_name == "persist_tree":
            status.update(label="Saving tree artifacts", state="running", expanded=True)
        elif event_type == "ingestion_step" and step_name == "build_pageindex_tree":
            status.update(label="PageIndex tree ready", state="running", expanded=True)
        elif event_type == "ingestion_complete":
            status.update(label="Ingestion complete", state="complete", expanded=True)

        _render_token_metrics()
        _render_recent_events()

    return _handle


def render_answer_with_images(answer: str, image_refs: list[dict]) -> None:
    """Render an assistant answer with images interleaved at [IMAGE:img_id] tags.

    The LLM emits [IMAGE:img_id] on its own line where an image is relevant.
    This function splits on those tags and renders text segments and images
    in order, so step-by-step content appears with its screenshot inline.
    """
    import re
    from pathlib import Path as _Path

    # Build lookup: img_id → stored path
    img_lookup: dict[str, str] = {
        ref["id"]: ref.get("stored", "")
        for ref in image_refs
        if ref.get("id")
    }

    # Split answer on [IMAGE:some_id] tags, keeping the tag as a capture group.
    parts = re.split(r"(\[IMAGE:[^\]]+\])", answer)

    for part in parts:
        m = re.match(r"\[IMAGE:([^\]]+)\]", part)
        if m:
            img_id = m.group(1).strip()
            stored = img_lookup.get(img_id, "")
            if stored:
                try:
                    img_bytes = _Path(stored).read_bytes()
                    # Find matching ref for caption metadata
                    ref = next((r for r in image_refs if r.get("id") == img_id), {})
                    caption = f"{ref.get('type', 'image')} · page {ref.get('page', '?')}"
                    st.image(img_bytes, caption=caption, use_container_width=False)
                except Exception:
                    pass
        elif part.strip():
            st.markdown(part)


def render_ingestion_trace(result: IngestionResult | None) -> None:
    """Render the post-ingestion trace once a document has finished processing."""
    if result is None:
        st.info("Upload a document to see the ingestion mapping.")
        return

    st.markdown("### Latest Ingestion")
    render_metric("Document", result.master_node.doc_title)
    render_metric("Doc ID", result.master_node.doc_id)
    render_metric("Tree Path", result.trace.tree_path)
    render_metric("Relationship mode", result.trace.relationship_mode)

    if result.trace.relationship_reconciliation is not None:
        recon = result.trace.relationship_reconciliation
        if recon.ran:
            affected = ", ".join(recon.affected_doc_ids) or "none"
            st.caption(
                f"Reconciliation: mode=`{recon.mode}`, "
                f"affected=`{affected}`"
                + (", enhanced LLM ran" if recon.enhanced_ran else "")
                + (", **fallback used**" if recon.fallback_used else "")
            )

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

    if result.metrics is not None:
        st.markdown("### Answer Metrics")
        metric_a, metric_b, metric_c = st.columns(3)
        with metric_a:
            render_metric("TTFT", f"{result.metrics.ttft_seconds:.2f}s")
        with metric_b:
            render_metric("Total Time", f"{result.metrics.total_time_seconds:.2f}s")
        with metric_c:
            render_metric("Total Tokens", f"{result.metrics.total_tokens:,}")
        st.caption(
            "Prompt tokens: "
            f"{result.metrics.prompt_tokens:,} | Completion tokens: "
            f"{result.metrics.completion_tokens:,} | LLM calls: {result.metrics.llm_calls}"
            + (
                " | Token usage includes estimates for one or more calls."
                if result.metrics.estimated_token_usage
                else ""
            )
        )

    st.markdown("### Latest Query Mapping")
    render_metric("Selected Docs", ", ".join(result.selected_docs) or "None")
    render_metric("Selected Nodes", ", ".join(result.selected_nodes) or "None")

    if result.sources:
        with st.expander("Sources", expanded=True):
            for src in result.sources:
                st.markdown(f"- **{src.doc_id}** — {src.section} *(p. {src.page_range})*")

    if result.trace is not None:
        # Retrieval mode badge
        mode_label = result.trace.retrieval_mode
        if result.trace.routing_broadened:
            mode_label += " (broadened)"
        st.caption(f"Retrieval mode: `{mode_label}`")

        # PageIndex agentic mode trace
        if result.trace.retrieval_mode == "pageindex":
            t = result.trace
            pi_parts = [
                f"**PageIndex mode**",
                f"tool calls: {t.pageindex_tool_calls_made}/{t.pageindex_tool_call_budget}",
                f"content tokens: {t.pageindex_content_tokens_used:,}/{t.pageindex_content_token_budget:,}",
            ]
            if t.pageindex_tool_budget_exhausted:
                pi_parts.append("⚠ tool budget exhausted")
            if t.pageindex_content_budget_exhausted:
                pi_parts.append("⚠ content budget exhausted")
            st.warning(" · ".join(pi_parts))
            if t.pageindex_explored_docs:
                st.caption(f"Explored docs: {', '.join(f'`{d}`' for d in t.pageindex_explored_docs)}")

        # Advanced retrieval trace info
        if result.trace.advanced_retrieval_enabled:
            plan = result.trace.planner_output
            adv_parts = [
                f"**Advanced retrieval**: on",
                f"max_docs={result.trace.effective_max_docs}",
                f"max_nodes={result.trace.effective_max_nodes}",
                f"expansion={'yes' if result.trace.node_expansion_applied else 'no'}",
            ]
            if plan:
                adv_parts.append(f"query_type=`{plan.query_type}`")
                adv_parts.append(f"broad={'yes' if plan.is_broad else 'no'}")
            st.info(" · ".join(adv_parts))
            if result.trace.expanded_node_refs:
                st.caption(f"Expanded nodes: {', '.join(f'`{r}`' for r in result.trace.expanded_node_refs)}")

        with st.expander("Navigation Map", expanded=True):
            st.json(result.trace.navigation)

        with st.expander("Retrieved Chunks", expanded=True):
            if not result.trace.fetched_chunks:
                st.caption("No chunks retrieved.")
            for chunk in result.trace.fetched_chunks:
                expansion_label = " · [expanded]" if chunk.is_expansion else ""
                st.markdown(
                    f"""
                    <div class="chunk-card">
                        <strong>{chunk.title}</strong><br/>
                        <code>{chunk.node_ref}</code><br/>
                        Tokens: {chunk.estimated_tokens}
                        {" | Truncated" if chunk.truncated else ""}
                        {expansion_label}
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
                st.code(chunk.text, language="markdown")

        with st.expander("Final Retrieved Context", expanded=False):
            st.code(result.retrieved_context or "[empty]", language="markdown")


def render_internal_map(master_tree_store, index_context: IndexContext) -> None:
    """Show the master tree and currently loaded index artifacts."""
    st.markdown("### Internal Mapping")
    st.markdown(
        """
        1. Ingestion validates the file, runs PageIndex, saves the per-doc tree, generates a master node, and upserts the master tree.
        2. Querying routes across the master tree, navigates each selected doc tree, fetches raw node text, then answers from the combined chunks.
        3. Chunk order matters: chunks are concatenated in navigator-ranked order and truncated only when the token budget is reached.
        """
    )

    st.caption(
        f"Project: `{index_context.project}` — provider: `{index_context.provider}` — model: `{index_context.model}`"
    )

    st.markdown("#### Master Tree")
    st.json(master_tree_store.tree.model_dump(mode="json"))


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
    st.caption(f"Project: `{index_context.project}` — documents are shared across all models.")

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


def render_audit_log(runtime) -> None:
    """Render the Audit Log tab — query history, metrics, and post-hoc analysis."""
    service = runtime.trace_service

    st.markdown("### Query Audit Log")
    st.caption(
        "Every query is recorded automatically. Use the Analyze button to run a "
        "post-hoc explanation of routing decisions and section relevance — this "
        "replays the exact context the system saw, not the current index state."
    )

    # ── Stats overview ────────────────────────────────────────────────────────
    stats = service.get_stats()
    if stats.total_queries == 0:
        st.info("No queries recorded yet for this project. Ask a question in the Ask tab first.")
        return

    s_col1, s_col2, s_col3, s_col4, s_col5 = st.columns(5)
    with s_col1:
        render_metric("Total Queries", str(stats.total_queries))
    with s_col2:
        render_metric("Avg Latency", f"{stats.avg_latency_seconds:.2f}s")
    with s_col3:
        render_metric("Avg TTFT", f"{stats.avg_ttft_seconds:.2f}s")
    with s_col4:
        render_metric("Avg Tokens", f"{stats.avg_tokens_total:,.0f}")
    with s_col5:
        cost_str = f"${stats.total_estimated_cost_usd:.4f}" if stats.total_estimated_cost_usd is not None else "—"
        render_metric("Total Est. Cost", cost_str)

    broadened_pct = stats.broadened_routing_rate * 100
    st.caption(
        f"Broadened routing rate: **{broadened_pct:.1f}%** of queries needed fallback routing  |  "
        f"Avg docs routed: **{stats.avg_docs_routed:.1f}**  |  "
        f"Avg sections retrieved: **{stats.avg_sections_retrieved:.1f}**"
    )

    if stats.most_accessed_docs:
        with st.expander("Most accessed documents", expanded=False):
            for doc_id, count in stats.most_accessed_docs:
                st.markdown(f"- `{doc_id}` — {count} quer{'y' if count == 1 else 'ies'}")

    st.divider()

    # ── Search + list ─────────────────────────────────────────────────────────
    col_search, col_limit = st.columns([3, 1])
    with col_search:
        search_term = st.text_input("Search queries", placeholder="Filter by keyword…", label_visibility="collapsed")
    with col_limit:
        list_limit = st.selectbox("Show", [10, 25, 50, 100], index=0, label_visibility="collapsed")

    summaries = service.list_traces(limit=list_limit, search=search_term or None)

    if not summaries:
        st.info("No traces match your search.")
        return

    # ── Per-trace rows ────────────────────────────────────────────────────────
    for s in summaries:
        broadened_badge = " 🔀" if s.routing_broadened else ""
        cost_str = f" · ${s.estimated_cost_usd:.4f}" if s.estimated_cost_usd is not None else ""
        label = (
            f"`{s.timestamp.strftime('%Y-%m-%d %H:%M:%S')}` · "
            f"**{s.retrieval_mode}{broadened_badge}** · "
            f"TTFT {s.ttft_seconds:.2f}s · Total {s.total_seconds:.2f}s · "
            f"{s.total_tokens:,} tok{cost_str} · "
            f"{s.query_preview[:80]}{'…' if len(s.query_preview) >= 80 else ''}"
        )
        with st.expander(label, expanded=False):
            trace = service.get_trace(s.trace_id)
            if trace is None:
                st.error("Trace data not found.")
                continue

            # Metrics row
            mc1, mc2, mc3, mc4, mc5 = st.columns(5)
            with mc1:
                render_metric("TTFT", f"{trace.metrics.ttft_seconds:.2f}s")
            with mc2:
                render_metric("Total Time", f"{trace.metrics.total_seconds:.2f}s")
            with mc3:
                render_metric("Routing Time", f"{trace.metrics.routing_seconds:.2f}s")
            with mc4:
                render_metric("Pipeline Time", f"{trace.metrics.pipeline_seconds:.2f}s")
            with mc5:
                cost_val = (
                    f"${trace.metrics.estimated_cost_usd:.6f}"
                    if trace.metrics.estimated_cost_usd is not None
                    else "—"
                )
                render_metric("Est. Cost", cost_val)

            token_parts = [
                f"Prompt: {trace.metrics.prompt_tokens:,}",
                f"Completion: {trace.metrics.completion_tokens:,}",
                f"Total: {trace.metrics.total_tokens:,}",
                f"LLM calls: {trace.metrics.llm_calls}",
            ]
            if trace.metrics.context_utilization_pct is not None:
                token_parts.append(f"Context utilization: {trace.metrics.context_utilization_pct * 100:.1f}%")
            if trace.metrics.retrieval_token_ratio is not None:
                token_parts.append(f"Retrieval ratio: {trace.metrics.retrieval_token_ratio * 100:.1f}%")
            if trace.metrics.estimated_token_usage:
                token_parts.append("⚠ includes token estimates")
            st.caption("  ·  ".join(token_parts))

            # Routing
            routing_mode = f"{trace.retrieval_mode}" + (" (broadened)" if trace.routing.broadened else "")
            st.markdown(
                f"**Model:** `{trace.model}` · **Mode:** `{routing_mode}` · "
                f"**Verification:** {'yes' if trace.verification_applied else 'no'}"
            )
            st.markdown(f"**Routed docs:** {', '.join(f'`{d}`' for d in trace.routing.selected_doc_ids) or 'none'}")

            if trace.conversation_snapshot:
                with st.expander("Conversation context at query time", expanded=False):
                    st.json(trace.conversation_snapshot)

            # Sections used
            if trace.sections_used:
                with st.expander(f"Sections retrieved ({len(trace.sections_used)})", expanded=False):
                    for sec in trace.sections_used:
                        st.markdown(
                            f"""<div class="chunk-card">
                            <strong>{sec.title}</strong> &nbsp;
                            <code>{sec.node_ref}</code> &nbsp;
                            p.{sec.page_start}–{sec.page_end} &nbsp;
                            ~{sec.estimated_tokens} tokens
                            {" &nbsp; <em>truncated</em>" if sec.truncated else ""}
                            </div>""",
                            unsafe_allow_html=True,
                        )
                        st.code(sec.text, language="markdown")
            else:
                st.caption("No section detail available (PageIndex mode).")

            with st.expander("Navigation map", expanded=False):
                st.json(trace.navigation_map)

            with st.expander("Router context snapshot (what the router saw)", expanded=False):
                st.caption("This is the exact master tree + arch map context that was passed to the routing LLM.")
                st.code(trace.routing.context_snapshot, language="json")
                st.caption("Router raw response:")
                st.code(trace.routing.raw_response)

            with st.expander("Answer", expanded=False):
                st.markdown(trace.answer)

            # Raw JSON download
            st.download_button(
                "Download raw JSON",
                data=trace.model_dump_json(indent=2),
                file_name=f"trace_{trace.trace_id[:8]}.json",
                mime="application/json",
                key=f"dl_{trace.trace_id}",
            )

            # ── Post-hoc analysis ─────────────────────────────────────────────
            st.divider()
            st.markdown("**Post-hoc Analysis**")
            st.caption(
                "Runs one LLM call to explain routing decisions and score each section's "
                "relevance. Uses the exact context stored in this trace — not the current index."
            )

            analysis_key = f"analysis_{trace.trace_id}"
            if analysis_key not in st.session_state:
                st.session_state[analysis_key] = None

            if st.button("Analyze this trace", key=f"btn_{trace.trace_id}"):
                with st.spinner("Running post-hoc analysis…"):
                    analysis = run_async_task(
                        service.run_post_hoc_analysis(
                            trace_id=trace.trace_id,
                            model=runtime.index_context.model,
                        )
                    )
                st.session_state[analysis_key] = analysis

            analysis: PostHocAnalysis | None = st.session_state[analysis_key]
            if analysis is not None:
                st.markdown("**Routing explanation**")
                st.info(analysis.routing_explanation)
                if analysis.section_scores:
                    st.markdown("**Section relevance scores**")
                    for score in analysis.section_scores:
                        bar_fill = "█" * score.score + "░" * (10 - score.score)
                        st.markdown(
                            f"`{score.node_ref}` — **{score.title}**  \n"
                            f"Score: **{score.score}/10** `{bar_fill}`  \n"
                            f"{score.explanation}"
                        )
                else:
                    st.caption("No section scores available (PageIndex mode or no sections retrieved).")


def main() -> None:
    """Assemble the full Streamlit application."""
    st.set_page_config(page_title="PageIndex Atlas", layout="wide")
    render_styles()
    ensure_session_state()

    model_registry = ModelRegistry(MODEL_REGISTRY_PATH)
    active_model = st.session_state.get("model_name", get_default_model())
    model_registry.ensure_model(active_model)
    active_project = st.session_state.get("project_name") or DEFAULT_PROJECT
    runtime = build_runtime(active_model, project=active_project)
    model, max_docs, reasoning_effort, project, adv_config, retrieval_mode_ui = render_sidebar(
        runtime.master_tree_store,
        runtime.index_context,
        model_registry,
    )

    # When the user switches project or model, rebuild the runtime on the next run.
    if model != active_model or project != runtime.index_context.project:
        st.session_state["model_name"] = model
        st.session_state["project_name"] = project
        st.session_state["chat_history"] = []
        st.session_state["latest_query"] = None
        st.session_state["latest_ingestion"] = None
        st.session_state["selected_doc_id"] = None
        st.rerun()

    st.markdown(
        f"""
        <div class="hero">
            <h1>PageIndex Atlas</h1>
            <p>Upload &rarr; ingest &rarr; ask. Project: <strong>{runtime.index_context.project}</strong>
            &mdash; Model: <strong>{runtime.index_context.model}</strong></p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    ingest_tab, ask_tab, docs_tab, map_tab, audit_tab = st.tabs(["Upload", "Ask", "Docs", "Map", "Audit"])

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
            default_top_sections_target = get_master_top_sections_target()
            with st.expander("Advanced ingestion options", expanded=False):
                top_sections_target = st.number_input(
                    "Master top-sections target",
                    min_value=1,
                    max_value=12,
                    value=default_top_sections_target,
                    step=1,
                    help="Ingestion-only. Stored master-node top sections will be generated with a target-centered range.",
                )
                min_sections, max_sections = get_master_top_sections_range(
                    int(top_sections_target)
                )
                st.caption(
                    f"Current prompt range: `{min_sections}-{max_sections}` sections. "
                    "Higher values improve routing detail but increase master-tree prompt size."
                )
                relationship_mode_options = ["off", "basic", "enhanced"]
                default_rel_mode = get_related_docs_mode()
                relationship_mode_ui = st.selectbox(
                    "Relationship maintenance",
                    options=relationship_mode_options,
                    index=relationship_mode_options.index(default_rel_mode),
                    help=(
                        "Controls how related_docs links are maintained after ingestion. "
                        "'off': no reconciliation (near-current behavior). "
                        "'basic': deterministic symmetry and cleanup — no extra LLM calls. "
                        "'enhanced': adds a bounded LLM pass to score candidate neighbors; "
                        "falls back to basic on failure."
                    ),
                )
                _is_pdf = uploaded_file is not None and uploaded_file.name.lower().endswith(".pdf")
                contains_images = st.checkbox(
                    "Document contains meaningful images",
                    value=False,
                    disabled=not _is_pdf,
                    help=(
                        "PDF only. When enabled, images are extracted, analysed by GPT-5.4 "
                        "(vision), and woven into the document text before the tree is built. "
                        "This improves retrieval of diagram and chart content but adds latency."
                    ),
                )

            if st.button("Run Ingestion"):
                if uploaded_file is None:
                    st.error("Upload a file first.")
                elif not doc_id or not doc_title or not doc_type:
                    st.error("Document ID, title, and type are required.")
                else:
                    try:
                        validate_doc_id(doc_id)
                    except ValueError as exc:
                        st.error(str(exc))
                    else:
                        saved_path = save_uploaded_file(uploaded_file, doc_id)
                        _rel_mode = relationship_mode_ui
                        with st.status("Running ingestion", expanded=True) as status:
                            st.write(f"Saved upload to `{saved_path}`")
                            progress_handler = create_ingestion_progress_renderer(status)
                            _contains_images = contains_images
                            result = run_async_task_with_progress(
                                lambda progress_callback: ingest_document_with_trace(
                                    file_path=str(saved_path),
                                    doc_id=doc_id,
                                    doc_title=doc_title,
                                    doc_type=doc_type,
                                    master_tree_store=runtime.master_tree_store,
                                    storage=runtime.storage,
                                    model=model,
                                    top_sections_target=int(top_sections_target),
                                    progress_callback=progress_callback,
                                    relationship_mode=_rel_mode,
                                    contains_images=_contains_images,
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
                if turn["role"] == "assistant":
                    render_answer_with_images(
                        turn["content"], turn.get("image_refs", [])
                    )
                else:
                    st.markdown(turn["content"])

        prompt = st.chat_input("Ask about your indexed documents")
        if prompt:
            import asyncio
            from queue import Queue
            from threading import Thread

            with st.chat_message("user"):
                st.markdown(prompt)

            # Capture session state on the main thread before spawning the worker.
            # Streamlit session state cannot be accessed from background threads.
            _current_history = list(st.session_state.chat_history)

            token_queue: Queue = Queue()
            result_holder: Queue = Queue()
            _STREAM_DONE = object()

            def _on_token(token: str) -> None:
                token_queue.put(token)

            def _run_query() -> None:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    res = loop.run_until_complete(
                        query(
                            user_query=prompt,
                            master_tree_store=runtime.master_tree_store,
                            storage=runtime.storage,
                            model=model,
                            conversation_context=_current_history,
                            max_docs=max_docs,
                            verbose=False,
                            reasoning_effort=reasoning_effort,
                            answer_token_callback=_on_token,
                            trace_service=runtime.trace_service,
                            project=runtime.index_context.project,
                            advanced_retrieval=adv_config,
                            retrieval_mode=retrieval_mode_ui,
                        )
                    )
                    token_queue.put(_STREAM_DONE)
                    result_holder.put((True, res))
                except Exception as exc:
                    token_queue.put(_STREAM_DONE)
                    result_holder.put((False, exc))
                finally:
                    # Allow pending cleanup tasks (e.g. httpx connection close) to
                    # finish before closing the loop, suppressing the harmless
                    # "Event loop is closed" warning from async HTTP clients.
                    try:
                        pending = asyncio.all_tasks(loop)
                        if pending:
                            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                    except Exception:
                        pass
                    loop.close()

            _thread = Thread(target=_run_query, daemon=True)
            _thread.start()

            def _token_generator():
                while True:
                    item = token_queue.get()
                    if item is _STREAM_DONE:
                        break
                    yield item

            with st.chat_message("assistant"):
                st.write_stream(_token_generator())

            _thread.join()
            _ok, _payload = result_holder.get()
            if not _ok:
                raise _payload
            result = _payload
            st.session_state.chat_history = _current_history + [
                {"role": "user", "content": prompt},
                {
                    "role": "assistant",
                    "content": result.answer,
                    "image_refs": getattr(result, "image_refs", []),
                },
            ]
            st.session_state.latest_query = result
            st.rerun()

        latest_query = st.session_state.latest_query
        if latest_query is not None:
            render_query_trace(latest_query)

    with docs_tab:
        render_doc_browser(runtime.master_tree_store, runtime.storage, runtime.index_context)

    with map_tab:
        render_internal_map(runtime.master_tree_store, runtime.index_context)

    with audit_tab:
        render_audit_log(runtime)


if __name__ == "__main__":
    main()
