"""Separate Streamlit UI for the experiments harness."""

from __future__ import annotations

from collections import Counter
from html import escape
import json
from pathlib import Path
import tempfile
from typing import Iterable

import altair as alt
import pandas as pd
import streamlit as st

from experiments.builds.registry import ArtifactBuildRunner
from experiments.corpora import CorpusStore
from experiments.evals.registry import EvaluationRunner
from experiments.evals.suites import suite_csv_template, suite_json_template
from experiments.models import (
    BuildManifest,
    ComparisonRunManifest,
    CorpusDocumentInput,
    QUESTION_TYPES,
)
from experiments.reports import write_aggregate_reports
from experiments.runs.registry import ExperimentRunRunner
from experiments.workflows import (
    build_preset_catalog,
    compatible_profile_ids_for_artifact_family,
    create_run_entries,
    retrieval_profile_catalog,
)
from utils import get_default_model


def _inject_styles() -> None:
    st.markdown(
        """
        <style>
        :root {
          --bg: #f5f0e7;
          --surface: #fcfaf5;
          --surface-strong: #efe5d7;
          --surface-code: #f7f2e9;
          --surface-code-strong: #ede3d5;
          --ink: #173042;
          --ink-soft: #24455b;
          --muted: #5f6f79;
          --line: #d7cbb9;
          --accent: #145d74;
          --accent-strong: #0f4b5d;
          --accent-soft: rgba(20, 93, 116, 0.12);
          --amber: #9a6d25;
          --amber-soft: rgba(154, 109, 37, 0.14);
          --rose: #8f5457;
          --rose-soft: rgba(143, 84, 87, 0.12);
          --success: #3b6653;
          --success-soft: rgba(59, 102, 83, 0.12);
          --shadow-soft: 0 16px 36px rgba(23, 48, 66, 0.05);
          --shadow-strong: 0 18px 50px rgba(23, 48, 66, 0.07);
        }

        .stApp {
          background:
            radial-gradient(circle at top right, rgba(20, 93, 116, 0.08), transparent 25%),
            radial-gradient(circle at bottom left, rgba(154, 109, 37, 0.08), transparent 22%),
            var(--bg);
          color: var(--ink);
        }

        [data-testid="stAppViewContainer"] {
          background: transparent;
        }

        [data-testid="stHeader"] {
          background: rgba(245, 240, 231, 0.82);
          backdrop-filter: blur(10px);
        }

        [data-testid="stSidebar"] {
          background: linear-gradient(180deg, #faf6ef 0%, #f0e7da 100%);
          border-right: 1px solid var(--line);
        }

        h1, h2, h3 {
          color: var(--ink);
          font-family: "Avenir Next", "Segoe UI", sans-serif;
          letter-spacing: -0.02em;
        }

        .stMarkdown,
        .stMarkdown p,
        .stMarkdown li,
        .stCaption,
        .stAlert,
        .stText,
        [data-testid="stSidebar"] .stMarkdown,
        [data-testid="stSidebar"] .stCaption {
          color: var(--ink);
        }

        .st-emotion-cache-10trblm,
        .st-emotion-cache-16idsys,
        .st-emotion-cache-pkbazv {
          color: var(--ink);
        }

        .hero {
          background: linear-gradient(135deg, rgba(255,255,255,0.85), rgba(241,234,220,0.95));
          border: 1px solid rgba(217, 207, 191, 0.9);
          border-radius: 24px;
          padding: 1.4rem 1.5rem;
          box-shadow: var(--shadow-strong);
          margin-bottom: 1rem;
        }

        .hero-kicker {
          font-size: 0.82rem;
          font-weight: 700;
          letter-spacing: 0.11em;
          text-transform: uppercase;
          color: var(--accent);
          margin-bottom: 0.45rem;
        }

        .hero-copy {
          color: var(--muted);
          max-width: 58rem;
          font-size: 0.98rem;
          line-height: 1.6;
        }

        .metric-card {
          background: rgba(251, 248, 242, 0.92);
          border: 1px solid rgba(217, 207, 191, 0.95);
          border-radius: 18px;
          padding: 1rem 1.05rem;
          box-shadow: var(--shadow-soft);
          min-height: 112px;
        }

        .metric-label {
          font-size: 0.78rem;
          text-transform: uppercase;
          letter-spacing: 0.1em;
          color: var(--muted);
          margin-bottom: 0.45rem;
        }

        .metric-value {
          font-size: 2rem;
          line-height: 1;
          font-weight: 700;
          color: var(--ink);
          margin-bottom: 0.35rem;
        }

        .metric-note {
          font-size: 0.9rem;
          color: var(--muted);
        }

        .section-card {
          background: rgba(251, 248, 242, 0.92);
          border: 1px solid rgba(217, 207, 191, 0.95);
          border-radius: 20px;
          padding: 1rem 1.05rem;
          box-shadow: var(--shadow-soft);
        }

        .pill {
          display: inline-block;
          padding: 0.22rem 0.62rem;
          border-radius: 999px;
          font-size: 0.78rem;
          font-weight: 600;
          margin-right: 0.35rem;
          margin-bottom: 0.35rem;
        }

        .pill-info { background: var(--accent-soft); color: var(--accent); }
        .pill-warn { background: var(--amber-soft); color: var(--amber); }
        .pill-ok { background: var(--success-soft); color: var(--success); }
        .pill-bad { background: var(--rose-soft); color: var(--rose); }

        .entry-card {
          background: linear-gradient(180deg, rgba(255,255,255,0.7), rgba(241,234,220,0.92));
          border: 1px solid rgba(217, 207, 191, 0.92);
          border-radius: 18px;
          padding: 1rem 1.05rem;
          box-shadow: var(--shadow-soft);
        }

        .entry-title {
          font-size: 1rem;
          font-weight: 700;
          color: var(--ink);
          margin-bottom: 0.55rem;
        }

        .entry-meta {
          color: var(--muted);
          font-size: 0.9rem;
          line-height: 1.5;
        }

        .warning-copy {
          border-left: 4px solid var(--amber);
          background: rgba(178, 123, 42, 0.08);
          color: var(--ink);
          padding: 0.8rem 0.9rem;
          border-radius: 12px;
          margin: 0.75rem 0 0.25rem;
        }

        [data-testid="stDataFrame"] {
          border: 1px solid rgba(217, 207, 191, 0.92);
          border-radius: 18px;
          overflow: hidden;
          box-shadow: 0 16px 36px rgba(23, 48, 66, 0.04);
        }

        .small-note {
          color: var(--muted);
          font-size: 0.9rem;
        }

        .table-shell {
          background: rgba(252, 250, 245, 0.96);
          border: 1px solid rgba(215, 203, 185, 0.95);
          border-radius: 18px;
          overflow: hidden;
          box-shadow: var(--shadow-soft);
          margin-bottom: 0.35rem;
        }

        .table-scroll {
          overflow-x: auto;
        }

        .table-shell table {
          width: 100%;
          border-collapse: separate;
          border-spacing: 0;
          color: var(--ink);
          font-size: 0.94rem;
        }

        .table-shell thead th {
          background: linear-gradient(180deg, #f0e7da, #ebe0d1);
          color: var(--ink-soft);
          text-align: left;
          font-size: 0.78rem;
          font-weight: 800;
          letter-spacing: 0.08em;
          text-transform: uppercase;
          padding: 0.82rem 0.9rem;
          border-bottom: 1px solid rgba(215, 203, 185, 0.95);
          white-space: nowrap;
        }

        .table-shell tbody td {
          background: rgba(252, 250, 245, 0.98);
          color: var(--ink);
          padding: 0.8rem 0.9rem;
          border-bottom: 1px solid rgba(231, 223, 211, 0.9);
          vertical-align: top;
        }

        .table-shell tbody tr:nth-child(even) td {
          background: rgba(247, 242, 233, 0.96);
        }

        .table-shell tbody tr:last-child td {
          border-bottom: 0;
        }

        .file-card {
          background: linear-gradient(180deg, var(--surface-code), var(--surface-code-strong));
          border: 1px solid rgba(215, 203, 185, 0.95);
          border-radius: 18px;
          padding: 0.95rem 1rem;
          box-shadow: var(--shadow-soft);
          margin-bottom: 0.55rem;
        }

        .file-label {
          font-size: 0.8rem;
          font-weight: 700;
          text-transform: uppercase;
          letter-spacing: 0.09em;
          color: var(--accent-strong);
          margin-bottom: 0.35rem;
        }

        .file-path {
          font-family: "SFMono-Regular", "Menlo", "Consolas", monospace;
          font-size: 0.96rem;
          line-height: 1.45;
          color: var(--ink);
          word-break: break-all;
        }

        code:not(pre code) {
          background: rgba(20, 93, 116, 0.08);
          color: var(--ink);
          border: 1px solid rgba(20, 93, 116, 0.12);
          border-radius: 8px;
          padding: 0.14rem 0.38rem;
        }

        pre,
        [data-testid="stCodeBlock"],
        [data-testid="stCode"] {
          background: var(--surface-code) !important;
          border: 1px solid rgba(215, 203, 185, 0.95) !important;
          border-radius: 16px !important;
          box-shadow: var(--shadow-soft);
        }

        pre code,
        [data-testid="stCodeBlock"] code,
        [data-testid="stCode"] code {
          color: var(--ink) !important;
          background: transparent !important;
          border: 0 !important;
        }

        div.stButton > button,
        div.stDownloadButton > button,
        button[kind="secondary"],
        button[kind="secondaryFormSubmit"],
        button[kind="primary"],
        button[type="submit"],
        [data-testid="stBaseButton-secondary"],
        [data-testid="stBaseButton-primary"] {
          background: linear-gradient(180deg, var(--surface), var(--surface-strong));
          color: var(--ink) !important;
          border: 1px solid rgba(215, 203, 185, 0.95);
          border-radius: 14px;
          box-shadow: 0 10px 24px rgba(23, 48, 66, 0.08);
          font-weight: 700;
          transition: transform 120ms ease, box-shadow 120ms ease, border-color 120ms ease;
        }

        div.stButton > button:hover,
        div.stDownloadButton > button:hover,
        [data-testid="stBaseButton-secondary"]:hover,
        [data-testid="stBaseButton-primary"]:hover {
          border-color: rgba(20, 93, 116, 0.45);
          box-shadow: 0 14px 28px rgba(23, 48, 66, 0.1);
          transform: translateY(-1px);
        }

        div.stButton > button[kind="primary"],
        button[kind="primary"],
        [data-testid="stBaseButton-primary"] {
          background: linear-gradient(180deg, var(--accent), var(--accent-strong));
          color: #f7fbfd !important;
          border-color: rgba(15, 75, 93, 0.95);
        }

        div.stButton > button[kind="primary"]:hover {
          border-color: rgba(15, 75, 93, 1);
        }

        div.stButton > button:disabled,
        div.stDownloadButton > button:disabled,
        [data-testid="stBaseButton-primary"]:disabled,
        [data-testid="stBaseButton-secondary"]:disabled {
          background: linear-gradient(180deg, #e9e1d4, #dfd4c4) !important;
          color: rgba(36, 69, 91, 0.7) !important;
          border-color: rgba(215, 203, 185, 0.95) !important;
          opacity: 1 !important;
          box-shadow: none !important;
          transform: none !important;
        }

        div.stButton > button p,
        div.stDownloadButton > button p,
        div.stButton > button span,
        div.stDownloadButton > button span,
        [data-testid="stBaseButton-primary"] p,
        [data-testid="stBaseButton-primary"] span,
        [data-testid="stBaseButton-secondary"] p,
        [data-testid="stBaseButton-secondary"] span {
          color: inherit !important;
        }

        .stTextInput label,
        .stTextArea label,
        .stSelectbox label,
        .stMultiSelect label,
        .stFileUploader label,
        .stCheckbox label,
        .stSlider label {
          color: var(--ink) !important;
          font-weight: 600;
        }

        .stTextInput input,
        .stTextArea textarea,
        div[data-baseweb="select"] > div,
        .stMultiSelect [data-baseweb="select"] > div {
          background: rgba(252, 250, 245, 0.96) !important;
          color: var(--ink) !important;
          border: 1px solid rgba(215, 203, 185, 0.95) !important;
          border-radius: 14px !important;
          box-shadow: inset 0 1px 0 rgba(255,255,255,0.7);
        }

        .stTextInput input::placeholder,
        .stTextArea textarea::placeholder {
          color: var(--muted) !important;
          opacity: 0.9;
        }

        [data-baseweb="tag"] {
          background: rgba(20, 93, 116, 0.12) !important;
          border-radius: 999px !important;
        }

        [data-baseweb="tag"] span,
        [data-baseweb="tag"] div {
          color: var(--ink) !important;
        }

        [data-testid="stCheckbox"] label,
        [data-testid="stCheckbox"] label p,
        [data-testid="stCheckbox"] span,
        [data-testid="stCheckbox"] div {
          color: var(--ink) !important;
        }

        [data-testid="stCheckbox"] input {
          accent-color: var(--accent);
        }

        [data-testid="stSlider"] label,
        [data-testid="stSlider"] p,
        [data-testid="stSlider"] span,
        [data-testid="stSlider"] div {
          color: var(--ink) !important;
        }

        [data-baseweb="slider"] [role="slider"] {
          background: var(--accent) !important;
          box-shadow: 0 0 0 3px rgba(20, 93, 116, 0.15);
        }

        [data-baseweb="slider"] > div > div {
          background: rgba(20, 93, 116, 0.16);
        }

        [data-baseweb="tab-list"] {
          gap: 0.4rem;
        }

        [data-baseweb="tab"] {
          background: rgba(252, 250, 245, 0.75);
          border: 1px solid rgba(215, 203, 185, 0.9);
          border-radius: 999px;
          color: var(--ink-soft) !important;
          font-weight: 700;
          padding: 0.5rem 0.9rem;
        }

        [aria-selected="true"][data-baseweb="tab"] {
          background: linear-gradient(180deg, rgba(20, 93, 116, 0.12), rgba(20, 93, 116, 0.18));
          border-color: rgba(20, 93, 116, 0.35);
          color: var(--accent-strong) !important;
        }

        [data-testid="stExpander"] {
          border: 1px solid rgba(215, 203, 185, 0.92);
          border-radius: 16px;
          background: rgba(252, 250, 245, 0.9);
          box-shadow: var(--shadow-soft);
        }

        [data-testid="stExpander"] summary,
        [data-testid="stExpander"] summary p,
        [data-testid="stExpander"] summary span {
          color: var(--ink) !important;
          font-weight: 700;
        }

        [data-testid="stAlertContainer"] * {
          color: var(--ink) !important;
        }

        .stSelectbox [data-baseweb="select"] svg,
        .stMultiSelect [data-baseweb="select"] svg {
          color: var(--ink-soft) !important;
          fill: var(--ink-soft) !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _render_metric_card(label: str, value: str, note: str) -> None:
    st.markdown(
        f"""
        <div class="metric-card">
          <div class="metric-label">{label}</div>
          <div class="metric-value">{value}</div>
          <div class="metric-note">{note}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _status_pill(status: str) -> str:
    tone = {
        "completed": "ok",
        "running": "info",
        "pending": "info",
        "failed": "bad",
        "skipped": "warn",
    }.get(status, "info")
    return f'<span class="pill pill-{tone}">{status}</span>'


def _titleize_label(label: str) -> str:
    return label.replace("_", " ").strip().title()


def _render_export_file(
    *,
    label: str,
    path: str,
    contents: str,
    key: str,
    mime: str,
) -> None:
    st.markdown(
        f"""
        <div class="file-card">
          <div class="file-label">{_titleize_label(label)}</div>
          <div class="file-path">{path}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    if contents:
        st.download_button(
            label=f"Download {_titleize_label(label)}",
            data=contents,
            file_name=Path(path).name,
            mime=mime,
            key=key,
            use_container_width=False,
        )


def _format_table_value(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and pd.isna(value):
        return ""
    if isinstance(value, (dict, list, tuple, set)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _render_table(df: pd.DataFrame) -> None:
    if df.empty:
        return

    headers = "".join(f"<th>{escape(str(column))}</th>" for column in df.columns)
    rows: list[str] = []
    for record in df.to_dict(orient="records"):
        cells = "".join(
            f"<td>{escape(_format_table_value(record.get(column)))}</td>"
            for column in df.columns
        )
        rows.append(f"<tr>{cells}</tr>")

    st.markdown(
        f"""
        <div class="table-shell">
          <div class="table-scroll">
            <table>
              <thead><tr>{headers}</tr></thead>
              <tbody>{''.join(rows)}</tbody>
            </table>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_bar_chart(
    df: pd.DataFrame,
    *,
    x: str,
    y: str,
    title: str,
    color: str | None = None,
    sort: str = "-y",
) -> None:
    if df.empty or x not in df.columns or y not in df.columns:
        return
    chart = (
        alt.Chart(df)
        .mark_bar(cornerRadiusTopLeft=6, cornerRadiusTopRight=6)
        .encode(
            x=alt.X(f"{x}:N", sort=sort, title=x.replace("_", " ").title()),
            y=alt.Y(f"{y}:Q", title=y.replace("_", " ").title()),
            color=(
                alt.Color(f"{color}:N", title=color.replace("_", " ").title())
                if color and color in df.columns
                else alt.value("#145d74")
            ),
            tooltip=list(df.columns),
        )
        .properties(height=320, title=title)
    )
    st.altair_chart(chart, use_container_width=True)


def _render_scatter_chart(
    df: pd.DataFrame,
    *,
    x: str,
    y: str,
    title: str,
    color: str | None = None,
) -> None:
    if df.empty or x not in df.columns or y not in df.columns:
        return
    chart = (
        alt.Chart(df)
        .mark_circle(size=110, opacity=0.82)
        .encode(
            x=alt.X(f"{x}:Q", title=x.replace("_", " ").title()),
            y=alt.Y(f"{y}:Q", title=y.replace("_", " ").title()),
            color=(
                alt.Color(f"{color}:N", title=color.replace("_", " ").title())
                if color and color in df.columns
                else alt.value("#9a6d25")
            ),
            tooltip=list(df.columns),
        )
        .properties(height=320, title=title)
    )
    st.altair_chart(chart, use_container_width=True)


def _render_heatmap(
    df: pd.DataFrame,
    *,
    x: str,
    y: str,
    color: str,
    title: str,
) -> None:
    if df.empty or x not in df.columns or y not in df.columns or color not in df.columns:
        return
    base = alt.Chart(df).encode(
        x=alt.X(f"{x}:N", title=x.replace("_", " ").title()),
        y=alt.Y(f"{y}:N", title=y.replace("_", " ").title()),
    )
    heatmap = base.mark_rect().encode(
        color=alt.Color(f"{color}:Q", title=color.replace("_", " ").title(), scale=alt.Scale(scheme="tealblues")),
        tooltip=list(df.columns),
    )
    text = base.mark_text(baseline="middle").encode(
        text=alt.Text(f"{color}:Q", format=".2f"),
        color=alt.value("#173042"),
    )
    st.altair_chart((heatmap + text).properties(height=320, title=title), use_container_width=True)


def _safe_read_json(path: str | None) -> dict:
    if not path:
        return {}
    trace_path = Path(path)
    if not trace_path.exists():
        return {}
    return json.loads(trace_path.read_text(encoding="utf-8"))


def _safe_read_text(path: str | None) -> str:
    if not path:
        return ""
    file_path = Path(path)
    if not file_path.exists():
        return ""
    return file_path.read_text(encoding="utf-8")


def _builds_for_corpus(builds: Iterable[BuildManifest], corpus_id: str | None) -> list[BuildManifest]:
    if not corpus_id:
        return list(builds)
    return [build for build in builds if build.corpus_id == corpus_id]


def _build_label(build: BuildManifest) -> str:
    return f"{build.build_label} · {build.artifact_family} · {build.status}"


def _reasoning_value(selection: str) -> str | None:
    return None if selection == "default" else selection


def _format_config_summary(config: dict[str, object], *, skip: set[str] | None = None) -> str:
    skip = skip or set()
    parts: list[str] = []
    for key, value in config.items():
        if key in skip or value in (None, "", [], {}, ()):
            continue
        parts.append(f"{key}={value}")
    return ", ".join(parts)


def _find_build_output_path(build: BuildManifest, label: str) -> str | None:
    for output in build.outputs:
        if output.label == label:
            return output.path
    return None


def _load_json_payload(path: str | None) -> object | None:
    if not path:
        return None
    file_path = Path(path)
    if not file_path.exists():
        return None
    return json.loads(file_path.read_text(encoding="utf-8"))


def _render_json_payload(payload: object) -> None:
    st.code(json.dumps(payload, indent=2, ensure_ascii=False), language="json")


def _compact_tree_node(node: dict) -> dict:
    compact = {
        key: node.get(key)
        for key in ("title", "node_id", "line_num", "summary", "prefix_summary")
        if node.get(key)
    }
    children = node.get("nodes") or []
    if children:
        compact["nodes"] = [
            {
                key: child.get(key)
                for key in ("title", "node_id", "line_num", "summary", "prefix_summary")
                if child.get(key)
            }
            for child in children[:2]
        ]
    return compact


def _build_preset_rows() -> list[dict[str, object]]:
    preset_notes = {
        "rag_standard": "Flat lexical chunk baseline. No embeddings, no fusion, no reranker.",
        "rag_vector": "Flat chunk baseline plus local embeddings, optional lexical corpus, and local ANN.",
        "pageindex_base": "Master-tree and per-document PageIndex trees with related-doc enrichment disabled.",
        "pageindex_related_basic": "Same PageIndex artifacts plus deterministic related-doc maintenance.",
        "pageindex_related_enhanced": "Same PageIndex artifacts plus heavier relationship reconciliation.",
    }
    rows: list[dict[str, object]] = []
    for preset_name, factory in build_preset_catalog().items():
        config = factory()
        compatible_profiles = compatible_profile_ids_for_artifact_family(config.artifact_family)
        rows.append(
            {
                "build_preset": preset_name,
                "build_kind": config.kind,
                "artifact_family": config.artifact_family,
                "main_difference": preset_notes[preset_name],
                "key_knobs": _format_config_summary(
                    config.model_dump(),
                    skip={"kind", "label", "artifact_family"},
                ),
                "compatible_profiles": ", ".join(compatible_profiles),
            }
        )
    return rows


def _retrieval_profile_rows() -> list[dict[str, object]]:
    profile_notes = {
        "rag_standard": "Lexical overlap and density over flat chunks.",
        "rag_vector": "Dense-only local semantic retrieval.",
        "rag_vector_rrf": "Dense plus lexical retrieval fused with reciprocal-rank fusion.",
        "rag_vector_rerank": "Dense plus lexical fusion, then local cross-encoder reranking.",
        "hybrid": "Deterministic routed PageIndex retrieval: route, navigate, verify, fetch.",
        "hybrid_advanced": "Hybrid plus planning, adaptive width, and node expansion.",
        "pageindex": "Agentic PageIndex loop with tool-calls over selected docs.",
        "pageindex_advanced": "Agentic PageIndex plus planning and adaptive query-time caps.",
    }
    rows: list[dict[str, object]] = []
    for profile_id, profile in retrieval_profile_catalog().items():
        rows.append(
            {
                "retrieval_profile": profile_id,
                "artifact_family": profile.artifact_family,
                "mode": profile.mode,
                "advanced": "yes" if profile.advanced_retrieval else "no",
                "what_it_does": profile_notes[profile_id],
                "config": _format_config_summary(profile.model_dump()["config"]),
            }
        )
    return rows


def _compatibility_rows() -> tuple[list[dict[str, object]], int]:
    build_configs = [factory() for factory in build_preset_catalog().values()]
    build_counts = Counter(config.artifact_family for config in build_configs)
    profile_counts = Counter(
        profile.artifact_family for profile in retrieval_profile_catalog().values()
    )
    rows: list[dict[str, object]] = []
    total = 0
    for artifact_family in sorted(build_counts):
        combinations = build_counts[artifact_family] * profile_counts[artifact_family]
        total += combinations
        rows.append(
            {
                "artifact_family": artifact_family,
                "build_presets": build_counts[artifact_family],
                "retrieval_profiles": profile_counts[artifact_family],
                "default_comparisons_per_corpus": combinations,
            }
        )
    return rows, total


def _render_what_is_it_tab(
    *,
    corpora: list,
    builds: list[BuildManifest],
    runs: list[ComparisonRunManifest],
) -> None:
    st.markdown(
        """
        <div class="hero">
          <div class="hero-kicker">What Is This</div>
          <h1>The experiments lab is a controlled comparison harness, not another product UI</h1>
          <div class="hero-copy">
            It exists to hold one stable corpus, build multiple artifact families from that same source,
            attach different retrieval policies on top, and compare outputs with shared answer prompts,
            metrics, traces, and exported reports. The point is to isolate where quality differences
            actually come from.
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    left, right = st.columns([1.05, 0.95], gap="large")
    with left:
        st.subheader("Mental Model")
        st.markdown(
            """
1. `Corpus`: one copied, normalized document set stored under `experiments/artifacts/corpora/`.
2. `Build`: one artifact family generated from that corpus, such as flat chunks, local vectors, or PageIndex trees.
3. `Retrieval profile`: the query-time policy that knows how to operate on one artifact family.
4. `Answer profile`: the answer prompt family layered on top of retrieved context. Right now that is intentionally shared.
5. `Run`: one question executed across multiple compatible build/profile combinations so results are comparable.
6. `Reports`: persisted JSON, CSV, Markdown, and HTML summaries for one run plus aggregate rollups by question type.
            """
        )
        st.markdown("**Flow**")
        st.code(
            "\n".join(
                [
                    "source docs",
                    "  -> corpus manifest",
                    "  -> build preset",
                    "  -> artifact family",
                    "  -> retrieval profile",
                    "  -> shared answer profile",
                    "  -> comparison run",
                    "  -> per-entry traces + reports",
                ]
            ),
            language="text",
        )

    with right:
        st.subheader("What Is Actually Variable")
        st.markdown(
            """
- **Build-time variation** changes the artifacts on disk.
  PageIndex builds create master trees and per-document trees.
  RAG builds create chunk artifacts, and vector RAG also stores embeddings plus lexical metadata.
- **Retrieval-time variation** changes how the question is answered against those artifacts.
  The same PageIndex tree can be queried through `hybrid`, `hybrid_advanced`, `pageindex`, or `pageindex_advanced`.
- **Answer-time variation** is mostly fixed on purpose right now.
  That keeps comparisons focused on retrieval quality and cost rather than prompt drift.
            """
        )
        st.markdown("**Current Snapshot**")
        st.markdown(f"- Registered corpora: `{len(corpora)}`")
        st.markdown(f"- Stored builds: `{len(builds)}`")
        st.markdown(f"- Recorded runs: `{len(runs)}`")
        st.markdown("- Shared answer profile: `aligned_default`")

    st.subheader("Current Build Presets")
    _render_table(pd.DataFrame(_build_preset_rows()))

    st.subheader("Current Retrieval Profiles")
    _render_table(pd.DataFrame(_retrieval_profile_rows()))

    compatibility_rows, total_combinations = _compatibility_rows()
    st.subheader("Compatibility And Combinatorics")
    st.caption(
        "Default comparison surface per corpus, before changing reasoning effort or adding more answer profiles."
    )
    _render_table(pd.DataFrame(compatibility_rows))
    st.markdown(
        f"With the current default catalog, one corpus can produce **{total_combinations}** distinct comparison entries."
    )

    st.subheader("Artifacts On Disk")
    st.code(
        "\n".join(
            [
                "experiments/artifacts/",
                "  corpora/<corpus_id>/manifest.json",
                "  builds/<build_id>/manifest.json",
                "  builds/<build_id>/pageindex_index/master_tree.json",
                "  builds/<build_id>/pageindex_index/doc_trees/<doc_id>_tree.json",
                "  builds/<build_id>/rag_vector_chunks.json",
                "  builds/<build_id>/rag_vector_embeddings.npy",
                "  runs/<run_id>/manifest.json",
                "  runs/<run_id>/<entry_label>_trace.json",
                "  reports/<run_id>/summary.json",
            ]
        ),
        language="text",
    )

    st.subheader("Live Schema Examples")
    sample_corpus = corpora[0] if corpora else None
    sample_pageindex_build = next(
        (build for build in builds if build.status == "completed" and build.artifact_family == "pageindex_tree"),
        None,
    )
    sample_vector_build = next(
        (build for build in builds if build.status == "completed" and build.artifact_family == "rag_vector"),
        None,
    )
    sample_run = next(
        (run for run in reversed(runs) if run.status == "completed" and run.entries),
        None,
    )

    if not any([sample_corpus, sample_pageindex_build, sample_vector_build, sample_run]):
        st.info("Create at least one corpus, build, or run to populate the live examples.")
        return

    example_col1, example_col2 = st.columns(2, gap="large")

    with example_col1:
        if sample_corpus:
            with st.expander("Corpus manifest example", expanded=True):
                corpus_payload = {
                    "version": sample_corpus.version,
                    "corpus_id": sample_corpus.corpus_id,
                    "name": sample_corpus.name,
                    "description": sample_corpus.description,
                    "document_count": len(sample_corpus.documents),
                    "documents": [
                        sample_corpus.documents[0].model_dump()
                    ] if sample_corpus.documents else [],
                }
                _render_json_payload(corpus_payload)

        if sample_pageindex_build:
            with st.expander("PageIndex build manifest example", expanded=False):
                build_payload = {
                    "build_id": sample_pageindex_build.build_id,
                    "corpus_id": sample_pageindex_build.corpus_id,
                    "build_label": sample_pageindex_build.build_label,
                    "artifact_family": sample_pageindex_build.artifact_family,
                    "config": sample_pageindex_build.config,
                    "outputs": [output.model_dump() for output in sample_pageindex_build.outputs[:3]],
                    "metrics": sample_pageindex_build.metrics,
                }
                _render_json_payload(build_payload)

            master_tree_payload = _load_json_payload(
                _find_build_output_path(sample_pageindex_build, "master_tree")
            )
            if isinstance(master_tree_payload, dict) and master_tree_payload.get("docs"):
                with st.expander("PageIndex master-tree example", expanded=False):
                    doc_payload = master_tree_payload["docs"][0]
                    _render_json_payload(
                        {
                            "doc_id": doc_payload.get("doc_id"),
                            "doc_summary": doc_payload.get("doc_summary"),
                            "top_sections": doc_payload.get("top_sections", [])[:2],
                            "relevance_hints": doc_payload.get("relevance_hints", {}),
                        }
                    )

            if sample_pageindex_build.documents:
                tree_payload = _load_json_payload(sample_pageindex_build.documents[0].artifact_path)
                if isinstance(tree_payload, dict) and tree_payload.get("structure"):
                    content_nodes = [
                        node
                        for node in tree_payload["structure"]
                        if node.get("title") != "Table of Contents"
                    ]
                    if content_nodes:
                        with st.expander("Per-document PageIndex node example", expanded=False):
                            _render_json_payload(
                                {
                                    "doc_name": tree_payload.get("doc_name"),
                                    "line_count": tree_payload.get("line_count"),
                                    "node": _compact_tree_node(content_nodes[0]),
                                }
                            )

    with example_col2:
        if sample_vector_build:
            with st.expander("Vector-RAG artifact metadata example", expanded=True):
                vector_meta_payload = _load_json_payload(
                    _find_build_output_path(sample_vector_build, "rag_vector_meta")
                )
                if vector_meta_payload:
                    _render_json_payload(vector_meta_payload)

        if sample_run:
            with st.expander("Comparison run manifest example", expanded=False):
                run_payload = {
                    "run_id": sample_run.run_id,
                    "title": sample_run.title,
                    "query": sample_run.query,
                    "question_type": sample_run.question_type,
                    "spec_hash": sample_run.spec_hash,
                    "requested_entries": [
                        entry.model_dump() for entry in sample_run.requested_entries[:2]
                    ],
                    "summary": sample_run.summary.model_dump() if sample_run.summary else None,
                }
                _render_json_payload(run_payload)

            trace_entry = next((entry for entry in sample_run.entries if entry.trace_path), None)
            trace_payload = _load_json_payload(trace_entry.trace_path if trace_entry else None)
            if isinstance(trace_payload, dict):
                with st.expander("Per-entry retrieval trace example", expanded=False):
                    _render_json_payload(
                        {
                            "label": trace_payload.get("label"),
                            "build_id": trace_payload.get("build_id"),
                            "selected_docs": trace_payload.get("selected_docs"),
                            "selected_nodes": trace_payload.get("selected_nodes"),
                            "retrieval_trace": trace_payload.get("retrieval_trace"),
                            "metrics": trace_payload.get("metrics"),
                        }
                    )

    st.subheader("How To Read The Main Variants")
    st.markdown(
        """
- `rag_standard` is the simplest baseline: flat chunks plus lexical scoring.
- `rag_vector` keeps the same flat chunk idea but adds local embeddings, optional lexical fusion, and optional reranking.
- `pageindex_*` build families all produce `pageindex_tree` artifacts. Their build-time difference is how related-document metadata is maintained.
- `hybrid` is the deterministic PageIndex retrieval path.
- `pageindex` is the more agentic PageIndex path with iterative tool-use over the selected docs.
- The `advanced` profiles do not create new artifacts. They change query-time behavior through planning, adaptive width, and related caps.
        """
    )


def _render_overview(
    *,
    corpora: list,
    builds: list[BuildManifest],
    runs: list[ComparisonRunManifest],
) -> None:
    st.markdown(
        """
        <div class="hero">
          <div class="hero-kicker">Experiments Lab</div>
          <h1>Evaluate pipeline variants without disturbing the main app</h1>
          <div class="hero-copy">
            Register one corpus, build multiple artifact families, and compare retrieval modes
            side by side with matched prompts, answer metrics, and trace visibility. This app
            is the controlled environment for finding out which combinations are actually useful.
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        _render_metric_card("Corpora", str(len(corpora)), "Reusable document sets copied once")
    with col2:
        _render_metric_card(
            "Builds",
            str(len(builds)),
            f"{sum(1 for build in builds if build.status == 'completed')} completed artifacts",
        )
    with col3:
        _render_metric_card(
            "Runs",
            str(len(runs)),
            f"{sum(1 for run in runs if run.status == 'completed')} completed comparisons",
        )
    with col4:
        _render_metric_card(
            "Modes",
            str(len(retrieval_profile_catalog())),
            "Lexical RAG, vector RAG, hybrid, and PageIndex variants",
        )

    st.markdown(
        """
        <div class="warning-copy">
          Advanced retrieval modes usually improve completeness on broader questions, but they
          also increase latency, token usage, and the chance of noisier context. Keep the default
          hybrid path for speed comparisons and enable the heavier modes intentionally. The local
          vector-RAG baseline also requires optional experiments dependencies on first use.
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_corpora_tab(store: CorpusStore) -> None:
    corpora = store.list_corpora()
    left, right = st.columns([1.15, 0.85], gap="large")

    with left:
        st.subheader("Registered Corpora")
        if corpora:
            data = [
                {
                    "corpus_id": corpus.corpus_id,
                    "name": corpus.name,
                    "documents": len(corpus.documents),
                    "created_at": corpus.created_at,
                }
                for corpus in corpora
            ]
            _render_table(pd.DataFrame(data))
        else:
            st.info("No corpora registered yet.")

    with right:
        st.subheader("Create Corpus")
        st.caption("Upload once here. Downstream builds stay isolated from the source corpus.")
        with st.form("create_corpus_form", clear_on_submit=True):
            name = st.text_input("Corpus name", placeholder="Enterprise Auth Corpus")
            description = st.text_area(
                "Description",
                placeholder="Optional notes about the document set and intended questions.",
                height=100,
            )
            uploaded_files = st.file_uploader(
                "Documents",
                type=["pdf", "md", "markdown", "docx"],
                accept_multiple_files=True,
            )
            overwrite = st.checkbox(
                "Overwrite existing corpus with the same normalized id",
                value=False,
            )
            submitted = st.form_submit_button("Create Corpus", use_container_width=True)

        if submitted:
            if not name.strip():
                st.error("Enter a corpus name.")
            elif not uploaded_files:
                st.error("Upload at least one document.")
            else:
                with st.spinner("Registering corpus and copying source documents..."):
                    with tempfile.TemporaryDirectory() as temp_dir:
                        inputs = []
                        for uploaded in uploaded_files:
                            temp_path = Path(temp_dir) / uploaded.name
                            temp_path.write_bytes(uploaded.getbuffer())
                            inputs.append(CorpusDocumentInput(source_path=str(temp_path)))
                        manifest = store.create_corpus(
                            name=name,
                            description=description or None,
                            documents=inputs,
                            overwrite=overwrite,
                        )
                st.success(f"Created corpus `{manifest.corpus_id}` with {len(manifest.documents)} documents.")


def _render_builds_tab(store: CorpusStore, build_runner: ArtifactBuildRunner) -> None:
    corpora = store.list_corpora()
    builds = build_runner.list_builds()
    left, right = st.columns([1.1, 0.9], gap="large")

    with left:
        st.subheader("Artifact Builds")
        if builds:
            build_rows = [
                {
                    "build_id": build.build_id,
                    "corpus_id": build.corpus_id,
                    "label": build.build_label,
                    "family": build.artifact_family,
                    "status": build.status,
                    "created_at": build.created_at,
                    "completed_at": build.completed_at or "",
                }
                for build in builds
            ]
            _render_table(pd.DataFrame(build_rows))
        else:
            st.info("No builds exist yet.")

    with right:
        st.subheader("Run Build Presets")
        if not corpora:
            st.info("Create a corpus first.")
            return

        preset_catalog = build_preset_catalog()
        with st.form("run_builds_form"):
            corpus_id = st.selectbox(
                "Corpus",
                options=[corpus.corpus_id for corpus in corpora],
            )
            selected_presets = st.multiselect(
                "Build presets",
                options=list(preset_catalog.keys()),
                default=["rag_standard", "rag_vector", "pageindex_base", "pageindex_related_basic"],
            )
            embedding_model = st.text_input(
                "Local embedding model for vector RAG",
                value="sentence-transformers/all-MiniLM-L6-v2",
                help="Used only by the rag_vector preset. This downloads locally on first use.",
            )
            vector_backend = st.selectbox(
                "Vector backend",
                options=["auto", "brute_force", "hnsw"],
                index=0,
                help="HNSW is optional acceleration. Brute force still stays fully local.",
            )
            top_sections_target = st.slider(
                "Master top sections target",
                min_value=3,
                max_value=12,
                value=4,
                help="Higher values make routing more descriptive but increase master-tree context size.",
            )
            force = st.checkbox("Force rebuild even if a matching manifest already exists")
            submitted = st.form_submit_button("Run Builds", use_container_width=True)

        if submitted:
            if not selected_presets:
                st.error("Select at least one build preset.")
            else:
                created: list[str] = []
                with st.spinner("Running selected experiment builds..."):
                    for preset_name in selected_presets:
                        factory = preset_catalog[preset_name]
                        if preset_name == "rag_standard":
                            config = factory()
                        elif preset_name == "rag_vector":
                            config = factory(
                                embedding_model=embedding_model,
                                vector_backend=vector_backend,
                            )
                        else:
                            config = factory(top_sections_target=top_sections_target)
                        manifest = build_runner.run_build(corpus_id, config, force=force)
                        created.append(f"{manifest.build_label} ({manifest.status})")
                st.success("Completed builds: " + ", ".join(created))
                if "rag_vector" in selected_presets:
                    st.info(
                        "Vector RAG builds store local embeddings and can use local semantic search. "
                        "If sentence-transformers is not installed yet, install the optional experiments dependencies first."
                    )


def _render_run_detail(run: ComparisonRunManifest, *, view_id: str = "detail") -> None:
    st.markdown(
        f"""
        <div class="section-card">
          <div class="hero-kicker">Comparison Run</div>
          <h3 style="margin-bottom:0.3rem;">{run.title}</h3>
          <div class="small-note">Query: {run.query}</div>
          <div class="small-note">Question type: {run.question_type}</div>
          <div class="small-note">Model: {run.model}</div>
          <div class="small-note">Spec hash: {run.spec_hash or "n/a"}</div>
          <div style="margin-top:0.75rem;">{_status_pill(run.status)}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.write("")

    if run.summary:
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            _render_metric_card("Completed", str(run.summary.completed_entries), "Entries that returned answers")
        with col2:
            _render_metric_card("Failed", str(run.summary.failed_entries), "Entries that failed outright")
        with col3:
            _render_metric_card("Fastest", run.summary.fastest_entry_label or "n/a", "Lowest total runtime")
        with col4:
            _render_metric_card("Lowest Tokens", run.summary.lowest_tokens_entry_label or "n/a", "Most token-efficient entry")
    if run.operator_winner_label or run.notes:
        st.markdown("**Review Annotations**")
        if run.operator_winner_label:
            st.write(f"Winner: `{run.operator_winner_label}`")
        if run.notes:
            st.write(run.notes)

    rows = []
    for entry in run.entries:
        metrics = entry.metrics
        rows.append(
            {
                "label": entry.label,
                "status": entry.status,
                "build_id": entry.build_id,
                "retrieval_profile": entry.retrieval_profile_id,
                "total_time_s": round(metrics.total_time_seconds, 3) if metrics else None,
                "ttft_s": round(metrics.ttft_seconds, 3) if metrics else None,
                "total_tokens": metrics.total_tokens if metrics else None,
                "llm_calls": metrics.llm_calls if metrics else None,
                "retrieved_context_tokens": metrics.retrieved_context_tokens if metrics else None,
                "selected_docs": len(entry.selected_docs),
                "selected_nodes": len(entry.selected_nodes),
            }
        )
    if rows:
        st.subheader("Entry Metrics")
        _render_table(pd.DataFrame(rows))

    st.subheader("Entries")
    for entry in run.entries:
        metrics = entry.metrics
        st.markdown(
            f"""
            <div class="entry-card">
              <div class="entry-title">{entry.label}</div>
              <div>{_status_pill(entry.status)}</div>
              <div class="entry-meta">
                Build: <code>{entry.build_id}</code><br/>
                Retrieval: <code>{entry.retrieval_profile_id}</code><br/>
                Nodes: {len(entry.selected_nodes)} | Docs: {len(entry.selected_docs)}<br/>
                Total time: {f"{metrics.total_time_seconds:.2f}s" if metrics else "n/a"} |
                Tokens: {metrics.total_tokens if metrics else "n/a"}
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        if entry.answer:
            st.markdown("**Answer**")
            st.write(entry.answer)
        if entry.sources:
            st.markdown("**Sources**")
            _render_table(pd.DataFrame(entry.sources))
        if entry.trace_path:
            with st.expander("Trace", expanded=False):
                st.json(_safe_read_json(entry.trace_path))
        if entry.error:
            st.error(entry.error)
        st.write("")

    if run.report_paths:
        st.subheader("Exported Reports")
        for label, path in run.report_paths.items():
            contents = _safe_read_text(path)
            mime = "text/html" if path.endswith(".html") else "text/plain"
            _render_export_file(
                label=label,
                path=path,
                contents=contents,
                key=f"download_{view_id}_{run.run_id}_{label}",
                mime=mime,
            )


def _render_compare_tab(build_runner: ArtifactBuildRunner, run_runner: ExperimentRunRunner) -> None:
    builds = build_runner.list_builds()
    if not builds:
        st.info("Create at least one build before running comparisons.")
        return

    corpora = sorted({build.corpus_id for build in builds})
    selected_corpus = st.selectbox(
        "Corpus filter",
        options=["All corpora", *corpora],
        index=0,
    )
    filtered_builds = _builds_for_corpus(
        builds,
        None if selected_corpus == "All corpora" else selected_corpus,
    )
    selected_build_ids = st.multiselect(
        "Builds to compare",
        options=[build.build_id for build in filtered_builds],
        default=[build.build_id for build in filtered_builds[:3]],
        format_func=lambda build_id: _build_label(build_runner.load_build(build_id)),
    )
    selected_builds = [build_runner.load_build(build_id) for build_id in selected_build_ids]

    compatible_profiles = sorted(
        {
            profile_id
            for build in selected_builds
            for profile_id in compatible_profile_ids_for_artifact_family(build.artifact_family)
        }
    )

    col1, col2 = st.columns(2)
    with col1:
        retrieval_reasoning = st.selectbox(
            "Retrieval reasoning effort",
            options=["default", "low", "medium", "high"],
            index=0,
        )
    with col2:
        answer_reasoning = st.selectbox(
            "Answer reasoning effort",
            options=["default", "low", "medium", "high"],
            index=0,
        )

    selected_profile_ids = st.multiselect(
        "Retrieval profiles",
        options=compatible_profiles,
        default=compatible_profiles,
    )
    query = st.text_area(
        "Comparison query",
        placeholder="Ask one question and compare how the different pipelines answer it.",
        height=130,
    )
    conversation_context = st.text_area(
        "Optional prior conversation context",
        placeholder="Leave blank for clean single-turn comparisons.",
        height=100,
    )
    model = st.text_input("Model / deployment", value=get_default_model())
    planned_entries = create_run_entries(
        builds=selected_builds,
        selected_profile_ids=selected_profile_ids,
        retrieval_reasoning_effort=_reasoning_value(retrieval_reasoning),
        answer_reasoning_effort=_reasoning_value(answer_reasoning),
    )
    question_type = st.selectbox(
        "Question type",
        options=list(QUESTION_TYPES),
        index=0,
        help="Tagging questions now makes later aggregate comparisons much more useful.",
    )
    entry_count = len(planned_entries)
    if entry_count > 1:
        max_concurrency = st.slider(
            "Max concurrent entries",
            min_value=1,
            max_value=entry_count,
            value=min(3, entry_count),
            help="Run more entries in parallel for speed. Lower this if the provider or local machine becomes noisy.",
        )
    else:
        max_concurrency = 1
        st.text_input(
            "Max concurrent entries",
            value="1",
            disabled=True,
            help=(
                "Only one compatible entry is available right now."
                if entry_count == 1
                else "Concurrency becomes adjustable once compatible entries exist."
            ),
        )
    resume_existing = st.checkbox(
        "Resume matching incomplete run if one already exists",
        value=True,
    )
    title = st.text_input("Run title", placeholder="Optional comparison title")
    if planned_entries:
        preview_rows = [
            {
                "label": entry.label,
                "build_id": entry.build_id,
                "artifact_family": entry.retrieval_profile.artifact_family,
                "retrieval_profile": entry.retrieval_profile.profile_id,
                "answer_profile": entry.answer_profile.profile_id,
            }
            for entry in planned_entries
        ]
        st.markdown("**Planned entries**")
        _render_table(pd.DataFrame(preview_rows))
    else:
        st.info("Select compatible builds and retrieval profiles to create run entries.")

    if st.button("Run Comparison", type="primary", use_container_width=True):
        if not query.strip():
            st.error("Enter a comparison query.")
        elif not planned_entries:
            st.error("No compatible run entries were created from the current selection.")
        else:
            with st.spinner("Running side-by-side comparison..."):
                manifest = run_runner.run_comparison(
                    query=query,
                    entries=planned_entries,
                    model=model,
                    conversation_context=conversation_context or None,
                    title=title or None,
                    question_type=question_type,
                    max_concurrency=max_concurrency,
                    resume_existing=resume_existing,
                )
            st.session_state["experiments_last_run_id"] = manifest.run_id
            st.success(f"Completed run `{manifest.run_id}`.")

    last_run_id = st.session_state.get("experiments_last_run_id")
    if last_run_id:
        st.write("")
        st.subheader("Latest Run")
        _render_run_detail(run_runner.load_run(last_run_id), view_id="compare_latest")


def _render_runs_tab(run_runner: ExperimentRunRunner) -> None:
    runs = list(reversed(run_runner.list_runs()))
    if not runs:
        st.info("No comparison runs recorded yet.")
        return

    selected_run_id = st.selectbox(
        "Select run",
        options=[run.run_id for run in runs],
        format_func=lambda run_id: f"{run_id} · {run_runner.load_run(run_id).title}",
    )
    run = run_runner.load_run(selected_run_id)
    has_failed_or_skipped = any(entry.status in {"failed", "skipped"} for entry in run.entries)
    has_pending = any(entry.status == "pending" for entry in run.entries)

    action_col1, action_col2 = st.columns(2)
    with action_col1:
        if st.button(
            "Resume Pending Run",
            use_container_width=True,
            key=f"resume_{run.run_id}",
            disabled=not has_pending,
        ):
            refreshed = run_runner.resume_existing_run(run.run_id, max_concurrency=run.max_concurrency)
            st.session_state["experiments_last_run_id"] = refreshed.run_id
            st.success(f"Resumed run `{refreshed.run_id}`.")
            run = refreshed
    with action_col2:
        if st.button(
            "Rerun Failed/Skipped Entries",
            use_container_width=True,
            key=f"rerun_{run.run_id}",
            disabled=not has_failed_or_skipped,
        ):
            try:
                rerun = run_runner.rerun_entries(
                    run.run_id,
                    failed_only=True,
                    max_concurrency=run.max_concurrency,
                )
            except ValueError as exc:
                st.error(str(exc))
            else:
                st.session_state["experiments_last_run_id"] = rerun.run_id
                st.success(f"Started rerun `{rerun.run_id}`.")

    winner_options = ["No selection", *[entry.label for entry in run.entries if entry.status == "completed"]]
    selected_winner = st.selectbox(
        "Operator winner",
        options=winner_options,
        index=winner_options.index(run.operator_winner_label) if run.operator_winner_label in winner_options else 0,
        key=f"winner_{run.run_id}",
    )
    notes = st.text_area(
        "Reviewer notes",
        value=run.notes or "",
        height=100,
        key=f"notes_{run.run_id}",
    )
    if st.button("Save Run Review", use_container_width=True, key=f"annotate_{run.run_id}"):
        run = run_runner.annotate_run(
            run.run_id,
            operator_winner_label=None if selected_winner == "No selection" else selected_winner,
            notes=notes or None,
        )
        st.success("Saved run annotations.")

    _render_run_detail(run, view_id="runs_tab")


def _render_reports_tab(run_runner: ExperimentRunRunner) -> None:
    runs = run_runner.list_runs()
    report_paths = write_aggregate_reports(runs=runs, reports_dir=run_runner.paths.reports_dir)
    st.subheader("Aggregate Reports")
    st.caption("These reports roll up tagged runs by question type and track human-selected winners separately from simple efficiency metrics.")

    summary_json = _safe_read_json(report_paths["question_type_json"])
    question_rows = []
    for question_type, payload in summary_json.get("question_types", {}).items():
        question_rows.append(
            {
                "question_type": question_type,
                "runs": payload.get("runs", 0),
                "scored_runs": payload.get("scored_runs", 0),
                "operator_wins": payload.get("operator_wins", {}),
                "fastest_wins": payload.get("fastest_wins", {}),
                "lowest_token_wins": payload.get("lowest_token_wins", {}),
            }
        )
    if question_rows:
        _render_table(pd.DataFrame(question_rows))
    else:
        st.info("No completed runs have been aggregated yet.")

    for label, path in report_paths.items():
        contents = _safe_read_text(path)
        mime = "text/html" if path.endswith(".html") else "text/plain"
        _render_export_file(
            label=label,
            path=path,
            contents=contents,
            key=f"aggregate_{label}",
            mime=mime,
        )


def _evaluation_entries_frame(evaluation) -> pd.DataFrame:
    rows = []
    for entry in evaluation.entries:
        scorecard = entry.scorecard
        judge = entry.judge_scorecard
        rows.append(
            {
                "source_run_id": entry.source_run_id,
                "case_id": entry.case_id,
                "question_type": entry.question_type,
                "business_scenario": entry.business_scenario or "unknown",
                "label": entry.label,
                "build_label": entry.build_label or "unknown",
                "corpus_id": entry.corpus_id or "unknown",
                "retrieval_profile_id": entry.retrieval_profile_id,
                "status": entry.status,
                "operator_winner": "yes" if entry.operator_winner else "no",
                "ground_truth_available": "yes" if entry.ground_truth_available else "no",
                "gold_sources_available": "yes" if entry.gold_sources_available else "no",
                "technical_score": scorecard.technical_score if scorecard else None,
                "judge_score": judge.overall_quality_score if judge else None,
                "business_score": judge.business_quality_score if judge else None,
                "groundedness_score": judge.groundedness_score if judge else None,
                "gold_alignment_score": judge.gold_alignment_score if judge else None,
                "source_match_score": judge.source_match_score if judge else None,
                "efficiency_score": scorecard.efficiency_score if scorecard else None,
                "reliability_score": scorecard.reliability_score if scorecard else None,
                "retrieval_discipline_score": (
                    scorecard.retrieval_discipline_score if scorecard else None
                ),
                "total_time_seconds": entry.metrics.get("total_time_seconds"),
                "total_tokens": entry.metrics.get("total_tokens"),
                "llm_calls": entry.metrics.get("llm_calls"),
                "retrieved_context_tokens": entry.metrics.get("retrieved_context_tokens"),
                "selected_docs_count": entry.metrics.get("selected_docs_count"),
                "selected_nodes_count": entry.metrics.get("selected_nodes_count"),
                "mode": entry.diagnostics.get("mode"),
                "judge_error": entry.judge_error or "",
            }
        )
    return pd.DataFrame(rows)


def _evaluation_suite_rows_frame(suites) -> pd.DataFrame:
    rows = []
    for suite in suites:
        rows.append(
            {
                "suite_id": suite.suite_id,
                "name": suite.name,
                "cases": len(suite.cases),
                "gold_cases": suite.gold_case_count,
                "description": suite.description or "",
                "created_at": suite.created_at,
            }
        )
    return pd.DataFrame(rows)


def _golden_dataset_markdown() -> str:
    return """
### Golden Dataset Setup

A golden dataset is a reusable evaluation suite that pairs questions with optional reference data.

Recommended workflow:

1. Start from the CSV or JSON template below.
2. Add one row per question.
3. At minimum, provide `question`. For gold-backed scoring, also provide `ground_truth_answer`.
4. Add `must_include_facts`, `must_not_claim`, and `gold_sources` when you want stricter evaluation.
5. Upload the suite here, persist it, then use it when generating an evaluation snapshot.

Column notes:
- `question`: required
- `ground_truth_answer`: optional but strongly recommended for gold-backed scoring
- `question_type`: should match the experiments taxonomy when you know it
- `must_include_facts`: pipe-delimited facts the answer must contain
- `must_not_claim`: pipe-delimited claims the answer must avoid
- `gold_sources`: pipe-delimited doc IDs or exact node refs expected in retrieval

This UI supports `CSV` and `JSON` for golden datasets. Excel is intentionally not supported in this phase.
"""


def _render_evaluation_tab(
    eval_runner: EvaluationRunner,
    run_runner: ExperimentRunRunner,
) -> None:
    suite_store = eval_runner.suites
    eligible_runs = [
        run
        for run in reversed(run_runner.list_runs())
        if run.status in {"completed", "failed"}
    ]
    available_suites = list(reversed(suite_store.list_suites()))
    suite_map = {suite.suite_id: suite for suite in available_suites}
    st.markdown(
        """
        <div class="hero">
          <div class="hero-kicker">Phase 2 Evaluation</div>
          <div class="hero-copy">
            This dashboard evaluates existing comparison runs from stored manifests and trace JSON.
            It can score deterministic technical behavior, add LLM-judge business and groundedness scoring,
            and optionally compare against a persisted golden dataset with reference answers and gold sources.
            Evaluation snapshots are persisted under <code>experiments/artifacts/evals/</code>.
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    with st.expander("Golden Dataset Setup", expanded=False):
        st.markdown(_golden_dataset_markdown())
        template_left, template_right = st.columns(2)
        with template_left:
            st.download_button(
                "Download CSV Template",
                data=suite_csv_template(),
                file_name="golden_dataset_template.csv",
                mime="text/csv",
                key="eval_template_csv",
                use_container_width=True,
            )
        with template_right:
            st.download_button(
                "Download JSON Template",
                data=suite_json_template(),
                file_name="golden_dataset_template.json",
                mime="application/json",
                key="eval_template_json",
                use_container_width=True,
            )

        with st.form("import_evaluation_suite_form"):
            uploaded_suite = st.file_uploader(
                "Upload evaluation suite",
                type=["csv", "json"],
                help="Upload a CSV or JSON suite. CSV is the recommended path for golden datasets.",
            )
            suite_name = st.text_input(
                "Suite name",
                placeholder="Optional display name for the imported suite",
            )
            suite_id = st.text_input(
                "Suite ID",
                placeholder="Optional stable identifier. Leave blank to derive from the name.",
            )
            suite_description = st.text_area(
                "Suite description",
                placeholder="Optional description or notes about this golden dataset.",
                height=90,
            )
            import_suite_submitted = st.form_submit_button(
                "Import Evaluation Suite",
                use_container_width=True,
            )

        if import_suite_submitted:
            if uploaded_suite is None:
                st.error("Choose a CSV or JSON suite file before importing.")
            else:
                uploaded_text = uploaded_suite.getvalue().decode("utf-8")
                try:
                    if uploaded_suite.name.lower().endswith(".csv"):
                        imported_suite = suite_store.import_suite_from_csv_text(
                            uploaded_text,
                            suite_id=suite_id or None,
                            name=suite_name or None,
                            description=suite_description or None,
                            persist=True,
                        )
                    else:
                        imported_suite = suite_store.import_suite_from_json_text(
                            uploaded_text,
                            suite_id=suite_id or None,
                            name=suite_name or None,
                            description=suite_description or None,
                            persist=True,
                        )
                except Exception as exc:
                    st.error(f"Could not import evaluation suite: {exc}")
                else:
                    st.session_state["experiments_last_eval_suite_id"] = imported_suite.suite_id
                    st.success(
                        f"Imported suite `{imported_suite.suite_id}` with {len(imported_suite.cases)} cases."
                    )
                    available_suites = list(reversed(suite_store.list_suites()))
                    suite_map = {suite.suite_id: suite for suite in available_suites}

        suite_frame = _evaluation_suite_rows_frame(available_suites)
        if not suite_frame.empty:
            st.markdown("**Persisted evaluation suites**")
            _render_table(suite_frame)
        else:
            st.info("No persisted evaluation suites yet. Use the templates above to import one.")

    if not eligible_runs:
        st.info("No completed or failed comparison runs are available for evaluation yet.")
        return

    with st.form("generate_evaluation_snapshot_form"):
        suite_options = ["Ad hoc from selected runs", *[suite.suite_id for suite in available_suites]]
        preferred_suite_id = st.session_state.get("experiments_last_eval_suite_id")
        selected_suite_mode = st.selectbox(
            "Evaluation suite",
            options=suite_options,
            index=(
                suite_options.index(preferred_suite_id)
                if preferred_suite_id in suite_options
                else 0
            ),
            format_func=lambda value: (
                "Ad hoc from selected runs"
                if value == "Ad hoc from selected runs"
                else f"{value} · {suite_map[value].name}"
            ),
            help="Choose a persisted golden dataset or build an ad hoc suite from the selected runs.",
        )
        selected_run_ids = st.multiselect(
            "Source runs",
            options=[run.run_id for run in eligible_runs],
            default=[run.run_id for run in eligible_runs[: min(8, len(eligible_runs))]],
            format_func=lambda run_id: f"{run_id} · {run_runner.load_run(run_id).title}",
        )
        title = st.text_input(
            "Evaluation snapshot title",
            placeholder="Optional deterministic evaluation title",
        )
        persist_suite = st.checkbox(
            "Persist the derived ad hoc suite snapshot",
            value=True,
            help="Useful if you want a durable suite record that mirrors the selected source runs.",
        )
        judge_enabled = st.checkbox(
            "Enable judge scoring",
            value=False,
            help="Adds LLM-judge groundedness, business, and gold-aware scoring on top of deterministic metrics.",
        )
        judge_model = st.text_input(
            "Judge model",
            value=get_default_model(),
            disabled=not judge_enabled,
            help="Defaults to the active chat model. This is only used when judge scoring is enabled.",
        )
        judge_reasoning_effort = st.selectbox(
            "Judge reasoning effort",
            options=["default", "low", "medium", "high"],
            index=0,
            disabled=not judge_enabled,
        )
        submitted = st.form_submit_button("Generate Evaluation Snapshot", use_container_width=True)

    if submitted:
        try:
            manifest = eval_runner.run_evaluation(
                source_run_ids=selected_run_ids or None,
                title=title or None,
                suite_id=(
                    None
                    if selected_suite_mode == "Ad hoc from selected runs"
                    else selected_suite_mode
                ),
                persist_suite=persist_suite,
                judge_enabled=judge_enabled,
                judge_model=judge_model or None,
                judge_reasoning_effort=(
                    None if judge_reasoning_effort == "default" else judge_reasoning_effort
                ),
            )
        except ValueError as exc:
            st.error(str(exc))
        else:
            st.session_state["experiments_last_eval_id"] = manifest.eval_run_id
            if selected_suite_mode != "Ad hoc from selected runs":
                st.session_state["experiments_last_eval_suite_id"] = selected_suite_mode
            st.success(f"Created evaluation snapshot `{manifest.eval_run_id}`.")

    evaluations = list(reversed(eval_runner.list_evaluations()))
    if not evaluations:
        st.info("Generate an evaluation snapshot to populate the dashboard.")
        return

    eval_ids = [evaluation.eval_run_id for evaluation in evaluations]
    preferred_eval_id = st.session_state.get("experiments_last_eval_id")
    selected_eval_id = st.selectbox(
        "Evaluation snapshot",
        options=eval_ids,
        index=eval_ids.index(preferred_eval_id) if preferred_eval_id in eval_ids else 0,
        format_func=lambda eval_id: f"{eval_id} · {eval_runner.load_evaluation(eval_id).title}",
    )
    evaluation = eval_runner.load_evaluation(selected_eval_id)
    st.session_state["experiments_last_eval_id"] = evaluation.eval_run_id
    if evaluation.judge_error:
        st.warning(f"Judge scoring note: {evaluation.judge_error}")

    summary = evaluation.summary
    if summary:
        card1, card2, card3, card4, card5, card6 = st.columns(6)
        with card1:
            _render_metric_card("Source Runs", str(summary.source_runs_scanned), "Comparison runs scanned")
        with card2:
            _render_metric_card("Entries", str(summary.entries_scored), "Evaluated entries in this snapshot")
        with card3:
            _render_metric_card("Avg Tech Score", f"{summary.average_technical_score:.1f}", "Deterministic technical score")
        with card4:
            _render_metric_card("Avg Judge Score", f"{summary.average_judge_score:.1f}", "LLM-judge overall quality")
        with card5:
            _render_metric_card("Avg Business", f"{summary.average_business_score:.1f}", "Business usefulness score")
        with card6:
            _render_metric_card("Gold Cases", str(summary.gold_backed_entries), "Entries with ground truth or gold sources")

        card7, card8, card9 = st.columns(3)
        with card7:
            _render_metric_card("Best Tech Profile", summary.best_profile_by_technical_score or "n/a", "Highest average technical score")
        with card8:
            _render_metric_card("Best Business Profile", summary.best_profile_by_business_score or "n/a", "Highest average business score")
        with card9:
            _render_metric_card("Operator Favorite", summary.operator_favorite_profile or "n/a", "Most human winner selections")

    profile_df = pd.DataFrame(evaluation.aggregate_payload.get("profiles", []))
    qtype_df = pd.DataFrame(evaluation.aggregate_payload.get("question_types", []))
    build_df = pd.DataFrame(evaluation.aggregate_payload.get("builds", []))
    corpus_df = pd.DataFrame(evaluation.aggregate_payload.get("corpora", []))
    entries_df = _evaluation_entries_frame(evaluation)
    for column in [
        "avg_judge_score",
        "avg_business_score",
        "avg_gold_alignment_score",
        "avg_groundedness_score",
        "avg_source_match_score",
        "missing_required_fact_rate",
        "forbidden_claim_violation_rate",
        "judged_entries",
        "gold_backed_entries",
    ]:
        if column not in profile_df.columns:
            profile_df[column] = 0.0
    for column in ["avg_judge_score", "avg_business_score"]:
        if column not in qtype_df.columns:
            qtype_df[column] = 0.0
    suite_case_frame = pd.DataFrame(
        [
            {
                "case_id": case.case_id,
                "question_type": case.question_type,
                "question": case.question,
                "ground_truth": "yes" if case.has_ground_truth else "no",
                "gold_sources": "yes" if case.has_gold_sources else "no",
                "business_scenario": case.business_scenario or "",
            }
            for case in evaluation.suite.cases
        ]
    )

    with st.expander("Active Evaluation Suite", expanded=False):
        st.markdown(
            f"**{evaluation.suite.name}** · `{evaluation.suite.suite_id}` · {len(evaluation.suite.cases)} cases"
        )
        if evaluation.suite.description:
            st.caption(evaluation.suite.description)
        if not suite_case_frame.empty:
            _render_table(suite_case_frame)

    st.subheader("Technical Leaderboard")
    if not profile_df.empty:
        leaderboard_df = profile_df.copy()
        if "artifact_families" in leaderboard_df.columns:
            leaderboard_df["artifact_families"] = leaderboard_df["artifact_families"].apply(
                lambda value: ", ".join(value) if isinstance(value, list) else value
            )
        _render_table(
            leaderboard_df[
                [
                    "retrieval_profile_id",
                    "avg_technical_score",
                    "avg_judge_score",
                    "avg_business_score",
                    "avg_total_time_seconds",
                    "avg_total_tokens",
                    "completion_rate",
                    "operator_win_rate",
                    "avg_tool_calls_made",
                ]
            ].sort_values(by="avg_technical_score", ascending=False)
        )
    else:
        st.info("No profile aggregates are available yet.")

    chart_left, chart_right = st.columns(2)
    with chart_left:
        _render_bar_chart(
            profile_df.sort_values(by="avg_technical_score", ascending=False),
            x="retrieval_profile_id",
            y="avg_technical_score",
            title="Average Technical Score By Profile",
        )
    with chart_right:
        _render_bar_chart(
            profile_df.sort_values(by="avg_total_time_seconds"),
            x="retrieval_profile_id",
            y="avg_total_time_seconds",
            title="Average Total Time By Profile",
        )

    chart_left, chart_right = st.columns(2)
    with chart_left:
        _render_bar_chart(
            profile_df.sort_values(by="avg_total_tokens"),
            x="retrieval_profile_id",
            y="avg_total_tokens",
            title="Average Total Tokens By Profile",
        )
    with chart_right:
        _render_bar_chart(
            profile_df.sort_values(by="operator_win_rate", ascending=False),
            x="retrieval_profile_id",
            y="operator_win_rate",
            title="Operator Win Rate By Profile",
        )

    scatter_left, scatter_right = st.columns(2)
    with scatter_left:
        _render_scatter_chart(
            profile_df,
            x="avg_total_tokens",
            y="avg_total_time_seconds",
            title="Cost vs Latency",
            color="retrieval_profile_id",
        )
    with scatter_right:
        _render_scatter_chart(
            profile_df,
            x="avg_total_time_seconds",
            y="avg_technical_score",
            title="Latency vs Technical Score",
            color="retrieval_profile_id",
        )

    if not profile_df.empty and profile_df["avg_judge_score"].fillna(0).sum() > 0:
        st.subheader("Business And Gold Diagnostics")
        business_left, business_right = st.columns(2)
        with business_left:
            _render_bar_chart(
                profile_df.sort_values(by="avg_business_score", ascending=False),
                x="retrieval_profile_id",
                y="avg_business_score",
                title="Average Business Score By Profile",
            )
        with business_right:
            _render_bar_chart(
                profile_df.sort_values(by="avg_gold_alignment_score", ascending=False),
                x="retrieval_profile_id",
                y="avg_gold_alignment_score",
                title="Average Gold Alignment By Profile",
            )

        business_left, business_right = st.columns(2)
        with business_left:
            _render_scatter_chart(
                profile_df,
                x="avg_total_tokens",
                y="avg_business_score",
                title="Cost vs Business Score",
                color="retrieval_profile_id",
            )
        with business_right:
            _render_scatter_chart(
                profile_df,
                x="avg_technical_score",
                y="avg_business_score",
                title="Technical vs Business Score",
                color="retrieval_profile_id",
            )

    st.subheader("Question Type Diagnostics")
    if not qtype_df.empty:
        qtype_left, qtype_right = st.columns(2)
        with qtype_left:
            _render_heatmap(
                qtype_df,
                x="retrieval_profile_id",
                y="question_type",
                color="avg_technical_score",
                title="Average Technical Score By Question Type",
            )
        with qtype_right:
            if "avg_business_score" in qtype_df.columns and qtype_df["avg_business_score"].fillna(0).sum() > 0:
                _render_heatmap(
                    qtype_df,
                    x="retrieval_profile_id",
                    y="question_type",
                    color="avg_business_score",
                    title="Average Business Score By Question Type",
                )
    else:
        st.info("No question-type aggregates are available for this snapshot.")

    st.subheader("Operational Diagnostics")
    if not profile_df.empty:
        diag_df = profile_df[
            [
                "retrieval_profile_id",
                "broadened_routing_rate",
                "tool_budget_exhaustion_rate",
                "content_budget_exhaustion_rate",
            ]
        ].melt(
            id_vars="retrieval_profile_id",
            var_name="diagnostic",
            value_name="rate",
        )
        diag_chart = (
            alt.Chart(diag_df)
            .mark_bar()
            .encode(
                x=alt.X("retrieval_profile_id:N", title="Retrieval Profile"),
                y=alt.Y("rate:Q", title="Rate"),
                color=alt.Color("diagnostic:N", title="Diagnostic"),
                tooltip=list(diag_df.columns),
            )
            .properties(height=320, title="Budget And Routing Diagnostics")
        )
        st.altair_chart(diag_chart, use_container_width=True)

    if not build_df.empty:
        st.markdown("**Build × Profile Rollup**")
        _render_table(build_df.sort_values(by="avg_technical_score", ascending=False))

    if not corpus_df.empty:
        st.markdown("**Corpus × Profile Rollup**")
        _render_table(corpus_df.sort_values(by="avg_technical_score", ascending=False))

    st.subheader("Entry Deep Dive")
    if entries_df.empty:
        st.info("No entry-level evaluation data is available.")
    else:
        profile_filter = st.selectbox(
            "Filter by retrieval profile",
            options=["All", *sorted(entries_df["retrieval_profile_id"].dropna().unique().tolist())],
            index=0,
        )
        qtype_filter = st.selectbox(
            "Filter by question type",
            options=["All", *sorted(entries_df["question_type"].dropna().unique().tolist())],
            index=0,
        )
        status_filter = st.selectbox(
            "Filter by status",
            options=["All", *sorted(entries_df["status"].dropna().unique().tolist())],
            index=0,
        )
        filtered_entries = entries_df.copy()
        if profile_filter != "All":
            filtered_entries = filtered_entries[
                filtered_entries["retrieval_profile_id"] == profile_filter
            ]
        if qtype_filter != "All":
            filtered_entries = filtered_entries[filtered_entries["question_type"] == qtype_filter]
        if status_filter != "All":
            filtered_entries = filtered_entries[filtered_entries["status"] == status_filter]

        _render_table(
            filtered_entries.sort_values(
                by=["judge_score", "technical_score", "total_time_seconds"],
                ascending=[False, False, True],
            )
        )

        entry_options = [
            f"{entry.source_run_id} :: {entry.label}"
            for entry in evaluation.entries
        ]
        selected_entry_key = st.selectbox(
            "Inspect evaluated entry",
            options=entry_options,
            index=0,
        )
        selected_entry = next(
            entry
            for entry in evaluation.entries
            if f"{entry.source_run_id} :: {entry.label}" == selected_entry_key
        )
        st.markdown("**Entry Scorecard**")
        st.json(selected_entry.scorecard.model_dump(mode="json") if selected_entry.scorecard else {})
        st.markdown("**Judge Scorecard**")
        st.json(selected_entry.judge_scorecard.model_dump(mode="json") if selected_entry.judge_scorecard else {})
        st.markdown("**Raw Metrics**")
        st.json(selected_entry.metrics)
        st.markdown("**Diagnostics**")
        st.json(selected_entry.diagnostics)
        matched_case = evaluation.suite.case_lookup().get(selected_entry.case_id)
        if matched_case is not None:
            st.markdown("**Golden Dataset Case**")
            st.json(matched_case.model_dump(mode="json"))
        if selected_entry.trace_path:
            with st.expander("Trace Payload", expanded=False):
                st.json(_safe_read_json(selected_entry.trace_path))

    if evaluation.report_paths:
        st.subheader("Evaluation Exports")
        for label, path in evaluation.report_paths.items():
            contents = _safe_read_text(path)
            mime = "text/html" if path.endswith(".html") else "text/plain"
            _render_export_file(
                label=label,
                path=path,
                contents=contents,
                key=f"eval_{evaluation.eval_run_id}_{label}",
                mime=mime,
            )


def main() -> None:
    st.set_page_config(
        page_title="Experiments Lab",
        page_icon="🧪",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    _inject_styles()

    store = CorpusStore()
    build_runner = ArtifactBuildRunner()
    run_runner = ExperimentRunRunner()
    eval_runner = EvaluationRunner()

    corpora = store.list_corpora()
    builds = build_runner.list_builds()
    runs = run_runner.list_runs()
    evaluations = eval_runner.list_evaluations()

    with st.sidebar:
        st.markdown("### Lab Snapshot")
        st.markdown(f"- Default model: `{get_default_model()}`")
        st.markdown(f"- Corpora: `{len(corpora)}`")
        st.markdown(f"- Builds: `{len(builds)}`")
        st.markdown(f"- Runs: `{len(runs)}`")
        st.markdown(f"- Eval snapshots: `{len(evaluations)}`")
        st.markdown(
            """
            <div class="small-note">
            This interface is separate from the main app. It exists to compare build and retrieval
            variants without changing the production/demo orchestration. Phase 1 evaluation snapshots
            are deterministic and read from stored run manifests and traces.
            </div>
            """,
            unsafe_allow_html=True,
        )

    overview_tab, what_is_it_tab, corpora_tab, builds_tab, compare_tab, runs_tab, evaluation_tab, reports_tab = st.tabs(
        ["Overview", "What Is It", "Corpora", "Builds", "Compare", "Runs", "Evaluation", "Reports"]
    )

    with overview_tab:
        _render_overview(corpora=corpora, builds=builds, runs=runs)
    with what_is_it_tab:
        _render_what_is_it_tab(corpora=corpora, builds=builds, runs=runs)
    with corpora_tab:
        _render_corpora_tab(store)
    with builds_tab:
        _render_builds_tab(store, build_runner)
    with compare_tab:
        _render_compare_tab(build_runner, run_runner)
    with runs_tab:
        _render_runs_tab(run_runner)
    with evaluation_tab:
        _render_evaluation_tab(eval_runner, run_runner)
    with reports_tab:
        _render_reports_tab(run_runner)


if __name__ == "__main__":
    main()
