"""Separate Streamlit UI for the experiments harness."""

from __future__ import annotations

from collections import Counter
from html import escape
import json
from pathlib import Path
import tempfile
from typing import Any, Iterable

import altair as alt
import pandas as pd
import streamlit as st

from experiments.builds.registry import ArtifactBuildRunner
from experiments.corpora import CorpusStore
from experiments.evals.registry import EvaluationRunner
from experiments.evals.suites import (
    EvaluationSuiteStore,
    suite_csv_template,
    suite_json_template,
)
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

        /* ── st.metric — force ink color so numbers aren't white ─────── */
        [data-testid="stMetricValue"],
        [data-testid="stMetricValue"] > div {
          color: var(--ink) !important;
          font-weight: 700 !important;
        }
        [data-testid="stMetricLabel"],
        [data-testid="stMetricLabel"] > div {
          color: var(--muted) !important;
          font-size: 0.78rem !important;
          text-transform: uppercase !important;
          letter-spacing: 0.08em !important;
        }
        [data-testid="stMetricDelta"] {
          color: var(--muted) !important;
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


def _render_progress_rows(
    placeholder,
    *,
    title: str,
    rows: list[dict[str, Any]],
    empty_message: str,
) -> None:
    with placeholder.container():
        if rows:
            st.markdown(f"**{title}**")
            _render_table(pd.DataFrame(rows))
        else:
            st.caption(empty_message)


def _ensure_frame_columns(
    df: pd.DataFrame,
    defaults: dict[str, Any],
) -> pd.DataFrame:
    frame = df.copy()
    for column, default in defaults.items():
        if column not in frame.columns:
            frame[column] = default
    return frame


def _sort_frame(
    df: pd.DataFrame,
    *,
    by: str | list[str],
    ascending: bool | list[bool] = True,
) -> pd.DataFrame:
    columns = [by] if isinstance(by, str) else list(by)
    if any(column not in df.columns for column in columns):
        return df
    return df.sort_values(by=by, ascending=ascending)


def _configured_chart(chart):
    """Apply a consistent light theme to any Altair chart before rendering."""
    return (
        chart
        .configure(
            background="transparent",
            font="'Avenir Next', 'Segoe UI', sans-serif",
            padding={"top": 5, "bottom": 72, "left": 5, "right": 5},
        )
        .configure_axis(
            labelColor="#173042",
            titleColor="#5f6f79",
            gridColor="rgba(215, 203, 185, 0.45)",
            domainColor="rgba(215, 203, 185, 0.8)",
            tickColor="rgba(215, 203, 185, 0.8)",
            labelFontSize=11,
            titleFontSize=12,
        )
        .configure_legend(
            labelColor="#173042",
            titleColor="#5f6f79",
            labelFontSize=11,
            titleFontSize=11,
            fillColor="rgba(252, 250, 245, 0.9)",
            strokeColor="rgba(215, 203, 185, 0.7)",
            padding=8,
            cornerRadius=8,
        )
        .configure_title(
            color="#173042",
            fontSize=13,
            fontWeight=700,
            anchor="start",
        )
        .configure_view(stroke="transparent")
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
            x=alt.X(
                f"{x}:N",
                sort=sort,
                title=None,
                axis=alt.Axis(labelAngle=-45, labelLimit=140, labelPadding=6),
            ),
            y=alt.Y(f"{y}:Q", title=y.replace("_", " ").title()),
            color=(
                alt.Color(f"{color}:N", title=color.replace("_", " ").title())
                if color and color in df.columns
                else alt.value("#145d74")
            ),
            tooltip=list(df.columns),
        )
        .properties(height=300, title=title)
    )
    st.altair_chart(_configured_chart(chart), use_container_width=True)


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
        .properties(height=300, title=title)
    )
    st.altair_chart(_configured_chart(chart), use_container_width=True)


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
        text=alt.Text(f"{color}:Q", format=".1f"),
        color=alt.value("#173042"),
    )
    st.altair_chart(
        _configured_chart((heatmap + text).properties(height=300, title=title)),
        use_container_width=True,
    )


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


def _run_label(run: ComparisonRunManifest) -> str:
    short_id = run.run_id[:8]
    suffixes = [value for value in [run.case_id, run.suite_id] if value]
    if not suffixes:
        return f"{short_id}… · {run.title}"
    return f"{short_id}… · {run.title} · {' / '.join(suffixes)}"


def _attached_suite_id_for_runs(
    runs: list[ComparisonRunManifest],
    suite_map: dict[str, object],
) -> str | None:
    attached_suite_ids = {
        run.suite_id
        for run in runs
        if run.suite_id and run.suite_id in suite_map
    }
    if len(attached_suite_ids) == 1:
        return next(iter(attached_suite_ids))
    return None


def _render_run_detail(run: ComparisonRunManifest, *, view_id: str = "detail") -> None:
    suite_note = (
        f'<div class="small-note">Golden dataset: {run.suite_id}</div>'
        if run.suite_id
        else ""
    )
    case_note = (
        f'<div class="small-note">Case: {run.case_id}</div>'
        if run.case_id
        else ""
    )
    st.markdown(
        f"""
        <div class="section-card">
          <div class="hero-kicker">Comparison Run</div>
          <h3 style="margin-bottom:0.3rem;">{run.title}</h3>
          <div class="small-note">Query: {run.query}</div>
          <div class="small-note">Question type: {run.question_type}</div>
          <div class="small-note">Model: {run.model}</div>
          {suite_note}
          {case_note}
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
    model = st.text_input("Model / deployment", value=get_default_model())
    planned_entries = create_run_entries(
        builds=selected_builds,
        selected_profile_ids=selected_profile_ids,
        retrieval_reasoning_effort=_reasoning_value(retrieval_reasoning),
        answer_reasoning_effort=_reasoning_value(answer_reasoning),
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

    question_source = st.radio(
        "Question source",
        options=["Single question", "Golden dataset batch"],
        horizontal=True,
        help=(
            "Single question preserves the original one-off comparison flow. "
            "Golden dataset batch executes the next N suite cases as individual runs."
        ),
    )

    if question_source == "Single question":
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
        question_type = st.selectbox(
            "Question type",
            options=list(QUESTION_TYPES),
            index=0,
            help="Tagging questions now makes later aggregate comparisons much more useful.",
        )
        title = st.text_input("Run title", placeholder="Optional comparison title")

        if st.button("Run Comparison", type="primary", use_container_width=True):
            if not query.strip():
                st.error("Enter a comparison query.")
            elif not planned_entries:
                st.error("No compatible run entries were created from the current selection.")
            else:
                progress_status = st.empty()
                progress_detail = st.empty()
                activity_placeholder = st.empty()
                activity_rows: list[dict[str, Any]] = []

                def _comparison_progress(event: dict[str, Any]) -> None:
                    event_name = event.get("event", "")
                    if event_name == "comparison_started":
                        progress_status.info(
                            f"Running `{event['run_id']}` with {event['total_entries']} entry(s)."
                        )
                        progress_detail.caption("Preparing retrieval and answer generation.")
                    elif event_name == "comparison_entry_started":
                        progress_status.info(
                            f"Running entry {event['entry_index']}/{event['total_entries']} "
                            f"for `{event['run_id']}`."
                        )
                        progress_detail.caption(f"Active entry: `{event['label']}`")
                    elif event_name == "comparison_entry_completed":
                        activity_rows.insert(
                            0,
                            {
                                "entry": event["label"],
                                "status": event["status"],
                                "run_id": event["run_id"],
                            },
                        )
                        _render_progress_rows(
                            activity_placeholder,
                            title="Entry Activity",
                            rows=activity_rows[:10],
                            empty_message="Entry updates will appear here as each retrieval path finishes.",
                        )
                        progress_detail.caption(
                            f"Finished entry {event['entry_index']}/{event['total_entries']}: "
                            f"`{event['label']}` ({event['status']})."
                        )
                    elif event_name == "comparison_completed":
                        progress_status.success(
                            f"Run `{event['run_id']}` finished with status `{event['status']}`."
                        )
                        progress_detail.caption(
                            f"Completed {event['completed_entries']} of {event['total_entries']} entry(s)."
                        )

                manifest = run_runner.run_comparison(
                    query=query,
                    entries=planned_entries,
                    model=model,
                    conversation_context=conversation_context or None,
                    title=title or None,
                    question_type=question_type,
                    max_concurrency=max_concurrency,
                    resume_existing=resume_existing,
                    progress_callback=_comparison_progress,
                )
                st.session_state["experiments_last_run_id"] = manifest.run_id
                st.success(f"Completed run `{manifest.run_id}`.")
    else:
        suite_store = EvaluationSuiteStore(run_runner.paths.root)
        available_suites = list(reversed(suite_store.list_suites()))
        if not available_suites:
            st.info("Import a golden dataset in the Evaluation tab before running suite batches.")
        else:
            suite_map = {suite.suite_id: suite for suite in available_suites}
            preferred_suite_id = st.session_state.get("experiments_last_eval_suite_id")
            selected_suite_id = st.selectbox(
                "Golden dataset",
                options=[suite.suite_id for suite in available_suites],
                index=(
                    [suite.suite_id for suite in available_suites].index(preferred_suite_id)
                    if preferred_suite_id in suite_map
                    else 0
                ),
                format_func=lambda suite_id: f"{suite_id} · {suite_map[suite_id].name}",
                help="Each suite case becomes its own comparison run, linked back to the golden dataset.",
            )
            suite = suite_map[selected_suite_id]
            latest_case_runs = run_runner.latest_runs_for_suite(suite.suite_id)
            completed_case_ids = run_runner.completed_case_ids_for_suite(suite.suite_id)
            remaining_cases = [
                case for case in suite.cases if case.case_id not in completed_case_ids
            ]

            metric_left, metric_mid, metric_right = st.columns(3)
            with metric_left:
                _render_metric_card("Suite Cases", str(len(suite.cases)), "Questions in this golden dataset")
            with metric_mid:
                _render_metric_card("Completed Cases", str(len(completed_case_ids)), "Cases with at least one completed run")
            with metric_right:
                _render_metric_card("Remaining Cases", str(len(remaining_cases)), "Cases that still need a completed run")

            conversation_context = st.text_area(
                "Optional prior conversation context for every suite question",
                placeholder="Usually leave blank for clean per-case evaluation runs.",
                height=100,
            )
            title_prefix = st.text_input(
                "Run title prefix",
                value=suite.name,
                help="Each generated run title appends the suite case ID.",
            )
            skip_completed_cases = st.checkbox(
                "Skip cases that already have a completed suite-linked run",
                value=True,
            )
            case_pool = remaining_cases if skip_completed_cases else list(suite.cases)
            queued_case_count = len(case_pool)
            if queued_case_count > 1:
                max_batch_size = min(10, queued_case_count)
                batch_size = st.slider(
                    "Cases per batch",
                    min_value=1,
                    max_value=max_batch_size,
                    value=min(5, max_batch_size),
                    help="Runs are created per question. Batch size only controls how many cases you launch at once.",
                )
            elif queued_case_count == 1:
                batch_size = 1
                st.text_input(
                    "Cases per batch",
                    value="1",
                    disabled=True,
                    help="Only one suite case is currently queued with the current filters.",
                )
            else:
                batch_size = 0
                st.text_input(
                    "Cases per batch",
                    value="0",
                    disabled=True,
                    help="No suite cases are currently queued with the current filters.",
                )

            preview_cases = case_pool[:batch_size]
            preview_rows = [
                {
                    "case_id": case.case_id,
                    "question_type": case.question_type,
                    "question": case.question,
                    "has_completed_run": "yes" if case.case_id in completed_case_ids else "no",
                    "latest_run": latest_case_runs[case.case_id].run_id if case.case_id in latest_case_runs else "",
                    "latest_status": latest_case_runs[case.case_id].status if case.case_id in latest_case_runs else "",
                }
                for case in preview_cases
            ]
            if preview_rows:
                st.markdown("**Next batch preview**")
                _render_table(pd.DataFrame(preview_rows))
            else:
                st.info("No suite cases are currently queued for the next batch with the current filters.")

            if st.button("Run Next Batch", type="primary", use_container_width=True):
                if not planned_entries:
                    st.error("No compatible run entries were created from the current selection.")
                elif not case_pool:
                    st.error("No suite cases are available for the next batch.")
                else:
                    batch_status = st.empty()
                    batch_detail = st.empty()
                    batch_progress = st.progress(0)
                    completed_cases_placeholder = st.empty()
                    entry_updates_placeholder = st.empty()
                    completed_batch_rows: list[dict[str, Any]] = []
                    entry_update_rows: list[dict[str, Any]] = []
                    total_batch_cases = max(1, len(preview_cases))

                    def _suite_batch_progress(event: dict[str, Any]) -> None:
                        nonlocal total_batch_cases
                        event_name = event.get("event", "")
                        if event_name == "suite_batch_started":
                            total_batch_cases = max(1, int(event.get("total_cases", 0) or 1))
                            batch_progress.progress(0)
                            batch_status.info(
                                f"Running {event['total_cases']} suite case(s) for `{event['suite_id']}`."
                            )
                            batch_detail.caption("Preparing the first suite case.")
                        elif event_name == "suite_case_started":
                            batch_status.info(
                                f"Case {event['case_index']}/{event['total_cases']} "
                                f"· `{event['case_id']}`"
                            )
                            batch_detail.caption(
                                f"Question type: `{event['question_type']}`. "
                                f"Question: {event['question'][:140]}"
                            )
                        elif event_name == "comparison_started":
                            batch_detail.caption(
                                f"Run `{event['run_id']}` started for case `{event.get('case_id') or 'ad_hoc'}` "
                                f"with {event['total_entries']} entry(s)."
                            )
                        elif event_name == "comparison_entry_started":
                            batch_detail.caption(
                                f"Case `{event.get('case_id') or 'ad_hoc'}` · "
                                f"entry {event['entry_index']}/{event['total_entries']}: `{event['label']}`"
                            )
                        elif event_name == "comparison_entry_completed":
                            entry_update_rows.insert(
                                0,
                                {
                                    "case_id": event.get("case_id") or "",
                                    "entry": event["label"],
                                    "status": event["status"],
                                    "run_id": event["run_id"],
                                },
                            )
                            _render_progress_rows(
                                entry_updates_placeholder,
                                title="Latest Entry Updates",
                                rows=entry_update_rows[:12],
                                empty_message="Entry updates will appear here while retrieval paths finish.",
                            )
                            batch_detail.caption(
                                f"Case `{event.get('case_id') or 'ad_hoc'}` · "
                                f"finished entry {event['entry_index']}/{event['total_entries']}: "
                                f"`{event['label']}` ({event['status']})."
                            )
                        elif event_name == "suite_case_completed":
                            completed_batch_rows.append(
                                {
                                    "case_id": event["case_id"],
                                    "run_id": event["run_id"],
                                    "status": event["status"],
                                    "title": event["title"],
                                }
                            )
                            batch_progress.progress(
                                int((int(event["case_index"]) / total_batch_cases) * 100)
                            )
                            batch_status.info(
                                f"Completed case {event['case_index']}/{event['total_cases']} "
                                f"· `{event['case_id']}` ({event['status']})."
                            )
                            _render_progress_rows(
                                completed_cases_placeholder,
                                title="Completed in This Batch",
                                rows=completed_batch_rows,
                                empty_message="Completed suite cases will appear here.",
                            )
                        elif event_name == "suite_batch_completed":
                            batch_progress.progress(100)
                            batch_status.success(
                                f"Finished {event['completed_cases']} suite case(s) for `{event['suite_id']}`."
                            )
                            batch_detail.caption("The requested suite batch has finished.")

                    manifests = run_runner.run_suite_batch(
                        suite=suite,
                        entries=planned_entries,
                        model=model,
                        conversation_context=conversation_context or None,
                        title_prefix=title_prefix or suite.name,
                        batch_size=batch_size,
                        max_concurrency=max_concurrency,
                        resume_existing=resume_existing,
                        skip_completed_cases=skip_completed_cases,
                        progress_callback=_suite_batch_progress,
                    )
                    if not manifests:
                        st.info("No suite runs were created for this batch.")
                    else:
                        batch_run_ids = [manifest.run_id for manifest in manifests]
                        st.session_state["experiments_last_run_id"] = manifests[-1].run_id
                        st.session_state["experiments_last_suite_batch_run_ids"] = batch_run_ids
                        st.session_state["experiments_last_eval_suite_id"] = suite.suite_id
                        st.session_state["experiments_eval_selected_run_ids"] = batch_run_ids
                        st.session_state["experiments_eval_suite_mode"] = suite.suite_id
                        st.success(
                            f"Completed {len(manifests)} suite-linked run(s) for `{suite.suite_id}`."
                        )

            batch_run_ids = st.session_state.get("experiments_last_suite_batch_run_ids", [])
            recent_batch_runs = []
            for run_id in batch_run_ids:
                try:
                    recent_batch_runs.append(run_runner.load_run(run_id))
                except FileNotFoundError:
                    continue
            if recent_batch_runs:
                st.write("")
                st.subheader("Latest Suite Batch")
                _render_table(
                    pd.DataFrame(
                        [
                            {
                                "run_id": run.run_id,
                                "case_id": run.case_id or "",
                                "question_type": run.question_type,
                                "status": run.status,
                                "title": run.title,
                            }
                            for run in recent_batch_runs
                        ]
                    )
                )
                selected_batch_run_id = st.selectbox(
                    "Inspect suite batch run",
                    options=[run.run_id for run in recent_batch_runs],
                    format_func=lambda run_id: _run_label(
                        next(run for run in recent_batch_runs if run.run_id == run_id)
                    ),
                )
                _render_run_detail(
                    next(run for run in recent_batch_runs if run.run_id == selected_batch_run_id),
                    view_id="compare_suite_batch",
                )

    last_run_id = st.session_state.get("experiments_last_run_id")
    if question_source == "Single question" and last_run_id:
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
        format_func=lambda run_id: _run_label(run_runner.load_run(run_id)),
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
        q = (entry.question or "").strip()
        rows.append(
            {
                "question": q,
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
                "completeness_score": judge.completeness_score if judge else None,
                "directness_score": judge.directness_score if judge else None,
                "actionability_score": judge.actionability_score if judge else None,
                "abstention_quality_score": judge.abstention_quality_score if judge else None,
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


def _compute_profile_recommendation(profile_df: pd.DataFrame) -> dict | None:
    """Return speed / quality / cost recommended profile IDs based on pareto logic."""
    if profile_df.empty:
        return None
    df = profile_df.copy()
    has_judge = df["avg_judge_score"].fillna(0).sum() > 0
    quality_col = "avg_judge_score" if has_judge else "avg_technical_score"
    max_quality = df[quality_col].max()
    # Candidates within 15 % of the best quality score
    threshold = max_quality * 0.85 if max_quality > 0 else 0
    candidates = df[df[quality_col] >= threshold] if threshold > 0 else df
    speed_best = candidates.loc[
        candidates["avg_total_time_seconds"].idxmin(), "retrieval_profile_id"
    ] if not candidates.empty else None
    cost_best = candidates.loc[
        candidates["avg_total_tokens"].idxmin(), "retrieval_profile_id"
    ] if not candidates.empty else None
    quality_best = df.loc[df[quality_col].idxmax(), "retrieval_profile_id"]
    # Balanced: best score-per-token ratio (avoid div-by-zero)
    df["_score_per_1k_tokens"] = df[quality_col] / (df["avg_total_tokens"] / 1000.0 + 1.0)
    balanced_best = df.loc[df["_score_per_1k_tokens"].idxmax(), "retrieval_profile_id"]
    return {
        "speed": speed_best,
        "quality": quality_best,
        "cost": cost_best,
        "balanced": balanced_best,
        "quality_col": quality_col,
    }


def _aggregate_failure_modes(evaluation) -> tuple[list[tuple[str, int]], list[tuple[str, int]]]:
    """Return top missing-fact strings and top risk strings across all judged entries."""
    missing_counter: dict[str, int] = {}
    risk_counter: dict[str, int] = {}
    for entry in evaluation.entries:
        judge = entry.judge_scorecard
        if not judge:
            continue
        for fact in judge.missing_required_facts or []:
            fact = fact.strip()
            if fact:
                missing_counter[fact] = missing_counter.get(fact, 0) + 1
        for risk in judge.risks or []:
            risk = risk.strip()
            if risk:
                risk_counter[risk] = risk_counter.get(risk, 0) + 1
    top_missing = sorted(missing_counter.items(), key=lambda x: x[1], reverse=True)[:10]
    top_risks = sorted(risk_counter.items(), key=lambda x: x[1], reverse=True)[:10]
    return top_missing, top_risks


def _judge_subscores_by_profile(entries_df: pd.DataFrame) -> pd.DataFrame:
    """Aggregate judge sub-scores from the entry-level frame grouped by retrieval profile."""
    sub_cols = [
        "groundedness_score",
        "completeness_score",
        "directness_score",
        "actionability_score",
        "abstention_quality_score",
    ]
    available = [c for c in sub_cols if c in entries_df.columns]
    if not available or "retrieval_profile_id" not in entries_df.columns:
        return pd.DataFrame()
    judged = entries_df[entries_df[available].notna().any(axis=1)]
    if judged.empty:
        return pd.DataFrame()
    agg = judged.groupby("retrieval_profile_id")[available].mean().round(1).reset_index()
    agg.columns = ["retrieval_profile_id"] + [
        c.replace("_score", "").replace("_", " ").title() for c in available
    ]
    return agg


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


def _routing_strategy_from_entries(entries_df: pd.DataFrame) -> pd.DataFrame:
    """For each question type, identify the best retrieval profile and score gap."""
    has_judge = entries_df["judge_score"].notna().any() if "judge_score" in entries_df.columns else False
    score_col = "judge_score" if has_judge else "technical_score"
    if score_col not in entries_df.columns or "retrieval_profile_id" not in entries_df.columns:
        return pd.DataFrame()
    completed = entries_df[entries_df["status"] == "completed"] if "status" in entries_df.columns else entries_df
    if completed.empty:
        return pd.DataFrame()
    grouped = (
        completed.dropna(subset=[score_col])
        .groupby(["question_type", "retrieval_profile_id"])[score_col]
        .agg(["mean", "count"])
        .reset_index()
    )
    grouped.columns = ["question_type", "retrieval_profile_id", "avg_score", "n_entries"]
    rows = []
    for qtype in sorted(grouped["question_type"].unique()):
        subset = grouped[grouped["question_type"] == qtype].sort_values("avg_score", ascending=False)
        if subset.empty:
            continue
        best = subset.iloc[0]
        score_gap = round(best["avg_score"] - subset.iloc[1]["avg_score"], 1) if len(subset) > 1 else None
        rows.append({
            "question_type": qtype,
            "recommended_profile": best["retrieval_profile_id"],
            f"avg_{score_col}": round(best["avg_score"], 1),
            "n_entries": int(best["n_entries"]),
            "score_gap_vs_2nd": score_gap if score_gap is not None else "—",
            "confidence": "high" if best["n_entries"] >= 5 else "medium" if best["n_entries"] >= 3 else "low",
        })
    return pd.DataFrame(rows)


def _routing_config_json(routing_df: pd.DataFrame) -> str:
    config: dict[str, str] = {}
    for _, row in routing_df.iterrows():
        config[str(row["question_type"])] = str(row["recommended_profile"])
    return json.dumps(
        {
            "routing_strategy": config,
            "_note": (
                "Generated from evaluation data. "
                "Low-confidence entries have fewer than 3 samples — run more comparisons before trusting them."
            ),
        },
        indent=2,
        ensure_ascii=False,
    )


def _snapshot_comparison_frame(eval_a, eval_b) -> pd.DataFrame:
    """Return a per-profile delta table comparing two evaluation snapshots."""
    profiles_a = pd.DataFrame(eval_a.aggregate_payload.get("profiles", []))
    profiles_b = pd.DataFrame(eval_b.aggregate_payload.get("profiles", []))
    if profiles_a.empty or profiles_b.empty:
        return pd.DataFrame()
    score_cols = [
        "avg_technical_score",
        "avg_judge_score",
        "avg_business_score",
        "avg_groundedness_score",
        "avg_gold_alignment_score",
        "avg_total_time_seconds",
        "avg_total_tokens",
        "missing_required_fact_rate",
        "forbidden_claim_violation_rate",
    ]
    cols_a = [c for c in score_cols if c in profiles_a.columns]
    cols_b = [c for c in score_cols if c in profiles_b.columns]
    shared_cols = [c for c in cols_a if c in cols_b]
    if not shared_cols:
        return pd.DataFrame()
    merged = profiles_a[["retrieval_profile_id"] + shared_cols].merge(
        profiles_b[["retrieval_profile_id"] + shared_cols],
        on="retrieval_profile_id",
        suffixes=("_before", "_after"),
        how="outer",
    )
    rows = []
    for _, row in merged.iterrows():
        profile = row["retrieval_profile_id"]
        for col in shared_cols:
            before = row.get(f"{col}_before")
            after = row.get(f"{col}_after")
            if pd.isna(before) and pd.isna(after):
                continue
            delta = round(after - before, 2) if pd.notna(before) and pd.notna(after) else None
            rows.append({
                "profile": profile,
                "metric": col.replace("avg_", "").replace("_", " "),
                "before": round(before, 2) if pd.notna(before) else "—",
                "after": round(after, 2) if pd.notna(after) else "—",
                "delta": delta if delta is not None else "—",
                "direction": (
                    "▲" if delta is not None and delta > 0 else
                    "▼" if delta is not None and delta < 0 else
                    "—"
                ),
            })
    return pd.DataFrame(rows)


def _answers_for_case(evaluation, case_id: str) -> list:
    """Return all completed entries for a given case_id, one per retrieval profile."""
    return [
        entry for entry in evaluation.entries
        if entry.case_id == case_id and entry.status == "completed"
    ]


def _export_low_score_entries_csv(evaluation, threshold: float, score_col: str = "judge_score") -> str | None:
    """Generate a CSV string of entries scoring below threshold, with full judge rationale."""
    rows = []
    for entry in evaluation.entries:
        judge = entry.judge_scorecard
        scorecard = entry.scorecard
        score: float | None = None
        if score_col == "judge_score":
            score = judge.overall_quality_score if judge else None
        else:
            score = scorecard.technical_score if scorecard else None
        if score is None or score > threshold:
            continue
        rows.append({
            "question": entry.question,
            "retrieval_profile_id": entry.retrieval_profile_id,
            "question_type": entry.question_type,
            "answer_preview": entry.answer_preview,
            "technical_score": scorecard.technical_score if scorecard else "",
            "efficiency_score": scorecard.efficiency_score if scorecard else "",
            "judge_score": judge.overall_quality_score if judge else "",
            "business_score": judge.business_quality_score if judge else "",
            "groundedness": judge.groundedness_score if judge else "",
            "completeness": judge.completeness_score if judge else "",
            "rationale": judge.rationale if judge else "",
            "strengths": " | ".join(judge.strengths or []) if judge else "",
            "risks": " | ".join(judge.risks or []) if judge else "",
            "missing_required_facts": " | ".join(judge.missing_required_facts or []) if judge else "",
            "source_run_id": entry.source_run_id,
            "case_id": entry.case_id,
        })
    if not rows:
        return None
    return pd.DataFrame(rows).to_csv(index=False)


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
        run_options = [run.run_id for run in eligible_runs]
        persisted_selected_run_ids = [
            run_id
            for run_id in st.session_state.get("experiments_eval_selected_run_ids", [])
            if run_id in run_options
        ]
        if not persisted_selected_run_ids:
            last_suite_batch_run_ids = [
                run_id
                for run_id in st.session_state.get("experiments_last_suite_batch_run_ids", [])
                if run_id in run_options
            ]
            if last_suite_batch_run_ids:
                persisted_selected_run_ids = last_suite_batch_run_ids
            else:
                persisted_selected_run_ids = run_options[: min(8, len(run_options))]
        # Only set the initial value if the widget key hasn't been registered yet.
        # Writing to a widget-bound key after the widget is instantiated raises
        # a StreamlitAPIException, so we use setdefault-style logic here.
        if "experiments_eval_selected_run_ids" not in st.session_state:
            st.session_state["experiments_eval_selected_run_ids"] = persisted_selected_run_ids

        selected_run_ids = st.multiselect(
            "Source runs",
            options=run_options,
            key="experiments_eval_selected_run_ids",
            format_func=lambda run_id: _run_label(run_runner.load_run(run_id)),
        )
        selected_runs = [
            run
            for run in eligible_runs
            if run.run_id in set(selected_run_ids)
        ]
        suite_options = ["Ad hoc from selected runs", *[suite.suite_id for suite in available_suites]]
        auto_suite_id = _attached_suite_id_for_runs(selected_runs, suite_map)
        preferred_suite_id = st.session_state.get("experiments_last_eval_suite_id")
        current_suite_mode = st.session_state.get("experiments_eval_suite_mode")
        if auto_suite_id and current_suite_mode in {None, "Ad hoc from selected runs"}:
            st.session_state["experiments_eval_suite_mode"] = auto_suite_id
        elif current_suite_mode not in suite_options:
            st.session_state["experiments_eval_suite_mode"] = (
                preferred_suite_id
                if preferred_suite_id in suite_options
                else "Ad hoc from selected runs"
            )
        if auto_suite_id:
            st.caption(
                f"Attached golden dataset detected from the selected runs: `{auto_suite_id}`. "
                "It was preselected automatically."
            )
        selected_suite_mode = st.selectbox(
            "Evaluation suite",
            options=suite_options,
            key="experiments_eval_suite_mode",
            format_func=lambda value: (
                "Ad hoc from selected runs"
                if value == "Ad hoc from selected runs"
                else f"{value} · {suite_map[value].name}"
            ),
            help="Choose a persisted golden dataset or build an ad hoc suite from the selected runs.",
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
        selected_entry_count = sum(len(run.entries) for run in selected_runs)
        judgeable_entry_count = sum(
            1
            for run in selected_runs
            for entry in run.entries
            if entry.status == "completed"
        )
        st.caption(
            f"Selected runs: `{len(selected_runs)}` · "
            f"entries to score deterministically: `{selected_entry_count}` · "
            f"judge calls: `{judgeable_entry_count if judge_enabled else 0}`"
        )
        submitted = st.form_submit_button("Generate Evaluation Snapshot", use_container_width=True)

    if submitted:
        progress_status = st.empty()
        progress_detail = st.empty()
        progress_bar = st.progress(0)
        live_results_placeholder = st.empty()
        live_rows: list[dict[str, Any]] = []
        deterministic_total = max(0, selected_entry_count)
        judge_total = max(0, judgeable_entry_count if judge_enabled else 0)
        overall_total = max(1, deterministic_total + judge_total)
        deterministic_done = 0
        judge_done = 0

        def _upsert_live_row(
            *,
            source_run_id: str | None,
            case_id: str | None,
            label: str | None,
            status: str | None,
            technical_score: float | None = None,
            judge_score: float | None = None,
            judge_error: str | None = None,
        ) -> None:
            key = f"{source_run_id or ''}::{case_id or ''}::{label or ''}"
            for row in live_rows:
                if row["_key"] == key:
                    if status is not None:
                        row["status"] = status
                    if technical_score is not None:
                        row["technical_score"] = technical_score
                    if judge_score is not None:
                        row["judge_score"] = judge_score
                    if judge_error is not None:
                        row["judge_error"] = judge_error
                    break
            else:
                live_rows.insert(
                    0,
                    {
                        "_key": key,
                        "source_run_id": source_run_id or "",
                        "case_id": case_id or "",
                        "label": label or "",
                        "status": status or "",
                        "technical_score": technical_score,
                        "judge_score": judge_score,
                        "judge_error": judge_error or "",
                    },
                )
            _render_progress_rows(
                live_results_placeholder,
                title="Completed Analysis So Far",
                rows=[
                    {key: value for key, value in row.items() if key != "_key"}
                    for row in live_rows[:20]
                ],
                empty_message="Completed evaluation rows will appear here as they finish.",
            )

        def _render_eval_progress(event: dict[str, Any]) -> None:
            nonlocal deterministic_done, judge_done, overall_total, deterministic_total, judge_total
            event_name = event.get("event", "")
            if event_name == "evaluation_started":
                progress_status.info(
                    f"Running evaluation `{event['eval_run_id']}` across "
                    f"{event['total_runs']} run(s) and {event['total_entries']} entry(s)."
                )
                progress_detail.caption(
                    "Preparing deterministic scoring and evaluation suite matching."
                )
            elif event_name == "deterministic_started":
                deterministic_total = max(0, int(event.get("total_entries", 0) or 0))
                overall_total = max(1, deterministic_total + judge_total)
                progress_detail.caption(
                    f"Deterministic scoring started for {event['total_entries']} entry(s)."
                )
            elif event_name == "deterministic_entry_completed":
                deterministic_done = int(event.get("processed_entries", 0) or 0)
                progress_bar.progress(int(((deterministic_done + judge_done) / overall_total) * 100))
                progress_status.info(
                    f"Deterministic scoring {deterministic_done}/{event['total_entries']}."
                )
                progress_detail.caption(
                    f"Finished deterministic scoring for case `{event['case_id']}` · "
                    f"`{event['label']}`."
                )
                _upsert_live_row(
                    source_run_id=event.get("source_run_id"),
                    case_id=event.get("case_id"),
                    label=event.get("label"),
                    status=event.get("status"),
                    technical_score=event.get("technical_score"),
                )
            elif event_name == "deterministic_completed":
                progress_detail.caption("Deterministic scoring finished.")
            elif event_name == "judge_started":
                judge_total = max(0, int(event.get("total_entries", 0) or 0))
                overall_total = max(1, deterministic_total + judge_total)
                progress_status.info(
                    f"Judge scoring started for {event['total_entries']} completed entry(s)."
                )
                progress_detail.caption(
                    f"Using judge model `{event['judge_model']}`."
                )
            elif event_name == "judge_entry_started":
                progress_status.info(
                    f"Judge scoring {judge_done + 1}/{event['total_entries']}."
                )
                progress_detail.caption(
                    f"Judging case `{event['case_id']}` · `{event['label']}`."
                )
            elif event_name == "judge_entry_completed":
                judge_done = int(event.get("processed_entries", 0) or 0)
                progress_bar.progress(int(((deterministic_total + judge_done) / overall_total) * 100))
                progress_status.info(
                    f"Judge scoring {judge_done}/{event['total_entries']}."
                )
                progress_detail.caption(
                    f"Finished judge scoring for case `{event['case_id']}` · "
                    f"`{event['label']}`."
                )
                _upsert_live_row(
                    source_run_id=event.get("source_run_id"),
                    case_id=event.get("case_id"),
                    label=event.get("label"),
                    status=event.get("status"),
                    judge_score=event.get("judge_score"),
                    judge_error=event.get("judge_error"),
                )
            elif event_name == "judge_completed":
                progress_detail.caption(
                    f"Judge scoring finished. Successful judge results: {event.get('judged_entries', 0)}."
                )
            elif event_name == "evaluation_completed":
                progress_bar.progress(100)
                progress_status.success(
                    f"Evaluation `{event['eval_run_id']}` finished with status `{event['status']}`."
                )
                progress_detail.caption("The full evaluation snapshot is ready below.")
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
                progress_callback=_render_eval_progress,
            )
        except ValueError as exc:
            st.error(str(exc))
        else:
            st.session_state["experiments_last_eval_id"] = manifest.eval_run_id
            # experiments_eval_selected_run_ids is widget-bound (st.multiselect key)
            # so it already holds selected_run_ids — writing to it again would raise.
            if manifest.suite.suite_id in suite_map:
                st.session_state["experiments_last_eval_suite_id"] = manifest.suite.suite_id
                st.session_state["experiments_eval_suite_mode"] = manifest.suite.suite_id
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

    # ── 3C: Snapshot Comparison ────────────────────────────────────────────
    if len(evaluations) >= 2:
        with st.expander("Compare with another snapshot", expanded=False):
            other_eval_ids = [eid for eid in eval_ids if eid != selected_eval_id]
            compare_eval_id = st.selectbox(
                "Baseline snapshot",
                options=other_eval_ids,
                format_func=lambda eid: f"{eid[:8]}… · {eval_runner.load_evaluation(eid).title}",
                key="phase3_compare_snapshot",
            )
            if compare_eval_id:
                baseline_eval = eval_runner.load_evaluation(compare_eval_id)
                st.caption(
                    f"Comparing **{evaluation.title}** (after) vs **{baseline_eval.title}** (before). "
                    "Positive delta = improvement for quality metrics; negative = regression."
                )
                cmp_df = _snapshot_comparison_frame(baseline_eval, evaluation)
                if cmp_df.empty:
                    st.info("No overlapping profile metrics found between the two snapshots.")
                else:
                    # Render with colour coding via HTML
                    headers = "".join(f"<th>{escape(str(c))}</th>" for c in cmp_df.columns)
                    rows_html: list[str] = []
                    # Metrics where bigger delta = better (quality) vs worse (cost)
                    cost_metrics = {"total time seconds", "total tokens", "missing required fact rate", "forbidden claim violation rate"}
                    for record in cmp_df.to_dict(orient="records"):
                        delta_val = record.get("delta")
                        metric_name = str(record.get("metric", "")).lower()
                        is_cost = metric_name in cost_metrics
                        delta_color = ""
                        if isinstance(delta_val, (int, float)):
                            positive_is_good = not is_cost
                            if (delta_val > 0 and positive_is_good) or (delta_val < 0 and not positive_is_good):
                                delta_color = "color:#3b6653;font-weight:700"
                            elif delta_val != 0:
                                delta_color = "color:#8f5457;font-weight:700"
                        cells = ""
                        for col in cmp_df.columns:
                            val = escape(str(record.get(col, "")))
                            style = f' style="{delta_color}"' if col == "delta" and delta_color else ""
                            cells += f"<td{style}>{val}</td>"
                        rows_html.append(f"<tr>{cells}</tr>")
                    st.markdown(
                        f"""<div class="table-shell"><div class="table-scroll">
                        <table><thead><tr>{headers}</tr></thead>
                        <tbody>{''.join(rows_html)}</tbody></table>
                        </div></div>""",
                        unsafe_allow_html=True,
                    )

    if evaluation.status == "running":
        st.info(
            "This evaluation snapshot is still running. Partial entry-level results may be available "
            "below, but aggregate leaderboards and charts can remain incomplete until the run finishes."
        )
    elif evaluation.status == "failed":
        st.warning(
            "This evaluation snapshot is marked failed. Any partial results below were persisted before "
            "the failure."
        )
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

    profile_df = _ensure_frame_columns(
        pd.DataFrame(evaluation.aggregate_payload.get("profiles", [])),
        {
            "retrieval_profile_id": "",
            "artifact_families": "",
            "avg_technical_score": 0.0,
            "avg_judge_score": 0.0,
            "avg_business_score": 0.0,
            "avg_total_time_seconds": 0.0,
            "avg_total_tokens": 0.0,
            "completion_rate": 0.0,
            "operator_win_rate": 0.0,
            "avg_tool_calls_made": 0.0,
            "broadened_routing_rate": 0.0,
            "tool_budget_exhaustion_rate": 0.0,
            "content_budget_exhaustion_rate": 0.0,
            "avg_gold_alignment_score": 0.0,
            "avg_groundedness_score": 0.0,
            "avg_source_match_score": 0.0,
            "missing_required_fact_rate": 0.0,
            "forbidden_claim_violation_rate": 0.0,
            "judged_entries": 0.0,
            "gold_backed_entries": 0.0,
        },
    )
    qtype_df = _ensure_frame_columns(
        pd.DataFrame(evaluation.aggregate_payload.get("question_types", [])),
        {
            "question_type": "",
            "retrieval_profile_id": "",
            "avg_technical_score": 0.0,
            "avg_business_score": 0.0,
            "avg_judge_score": 0.0,
        },
    )
    build_df = _ensure_frame_columns(
        pd.DataFrame(evaluation.aggregate_payload.get("builds", [])),
        {
            "build_label": "",
            "retrieval_profile_id": "",
            "avg_technical_score": 0.0,
        },
    )
    corpus_df = _ensure_frame_columns(
        pd.DataFrame(evaluation.aggregate_payload.get("corpora", [])),
        {
            "corpus_id": "",
            "retrieval_profile_id": "",
            "avg_technical_score": 0.0,
        },
    )
    entries_df = _evaluation_entries_frame(evaluation)
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

    # ── Profile Recommendation ─────────────────────────────────────────────
    _rec = _compute_profile_recommendation(profile_df)
    if _rec:
        st.subheader("Profile Recommendation")
        st.caption(
            "Derived from the evaluation data. Candidates for speed and cost must stay within 15 % "
            "of the top quality score."
        )
        rec1, rec2, rec3, rec4 = st.columns(4)
        with rec1:
            _render_metric_card(
                "Speed-First",
                _rec["speed"] or "n/a",
                "Fastest profile within 15 % of peak quality",
            )
        with rec2:
            _render_metric_card(
                "Quality-First",
                _rec["quality"] or "n/a",
                "Highest overall judge / technical score",
            )
        with rec3:
            _render_metric_card(
                "Cost-First",
                _rec["cost"] or "n/a",
                "Fewest tokens within 15 % of peak quality",
            )
        with rec4:
            _render_metric_card(
                "Balanced",
                _rec["balanced"] or "n/a",
                "Best quality-per-1k-token ratio",
            )

    st.subheader("Technical Leaderboard")
    if not profile_df.empty:
        leaderboard_df = profile_df.copy()
        if "artifact_families" in leaderboard_df.columns:
            leaderboard_df["artifact_families"] = leaderboard_df["artifact_families"].apply(
                lambda value: ", ".join(value) if isinstance(value, list) else value
            )
        with st.expander("Show leaderboard table", expanded=True):
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
                ].pipe(lambda frame: _sort_frame(frame, by="avg_technical_score", ascending=False))
            )
    else:
        st.info("No profile aggregates are available yet.")

    chart_left, chart_right = st.columns(2)
    with chart_left:
        _render_bar_chart(
            _sort_frame(profile_df, by="avg_technical_score", ascending=False),
            x="retrieval_profile_id",
            y="avg_technical_score",
            title="Average Technical Score By Profile",
        )
    with chart_right:
        _render_bar_chart(
            _sort_frame(profile_df, by="avg_total_time_seconds"),
            x="retrieval_profile_id",
            y="avg_total_time_seconds",
            title="Average Total Time By Profile",
        )
    st.caption("Higher technical score = better retrieval quality. Lower total time = faster end-to-end response per query.")

    chart_left, chart_right = st.columns(2)
    with chart_left:
        _render_bar_chart(
            _sort_frame(profile_df, by="avg_total_tokens"),
            x="retrieval_profile_id",
            y="avg_total_tokens",
            title="Average Total Tokens By Profile",
        )
    with chart_right:
        if not profile_df.empty and profile_df["operator_win_rate"].fillna(0).sum() > 0:
            _render_bar_chart(
                _sort_frame(profile_df, by="operator_win_rate", ascending=False),
                x="retrieval_profile_id",
                y="operator_win_rate",
                title="Operator Win Rate By Profile",
            )
        else:
            st.caption("Operator Win Rate — no wins recorded yet. Select an operator winner in the Runs tab to populate this chart.")
    st.caption("Fewer tokens = lower cost proxy. Operator win rate = share of questions where a human reviewer preferred this profile's answer.")

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
    st.caption("Cost vs Latency: bottom-left is ideal (fast and cheap). Latency vs Technical Score: top-left is ideal (high quality at low latency).")

    # ── Judge Quality Breakdown ────────────────────────────────────────────
    _subscores_df = _judge_subscores_by_profile(entries_df)
    if not _subscores_df.empty:
        st.subheader("Judge Quality Breakdown")
        st.caption(
            "Five LLM-judge sub-dimensions averaged per profile. "
            "Scores are 0–100. "
            "Groundedness: did the answer stick to retrieved facts? "
            "Completeness: did it cover everything the question required? "
            "Directness: was it concise and on-point? "
            "Actionability: could a user act on it? "
            "Abstention Quality: did it correctly refuse unanswerable questions?"
        )
        with st.expander("Show sub-score table", expanded=True):
            _render_table(_subscores_df)

        # Groundedness vs Completeness scatter — key risk signal
        if "Groundedness" in _subscores_df.columns and "Completeness" in _subscores_df.columns:
            _gc_df = _subscores_df.rename(columns={"retrieval_profile_id": "retrieval_profile_id"})
            _render_scatter_chart(
                _gc_df.rename(columns={
                    "Groundedness": "groundedness",
                    "Completeness": "completeness",
                }),
                x="groundedness",
                y="completeness",
                title="Groundedness vs Completeness (risk posture per profile)",
                color="retrieval_profile_id",
            )
            st.caption("Top-right = ideal (grounded and complete). Top-left = over-cautious (safe but thin answers). Bottom-right = hallucination risk (complete but not grounded in retrieved facts).")

    if not profile_df.empty and profile_df["avg_judge_score"].fillna(0).sum() > 0:
        st.subheader("Business And Gold Diagnostics")
        business_left, business_right = st.columns(2)
        with business_left:
            _render_bar_chart(
                _sort_frame(profile_df, by="avg_business_score", ascending=False),
                x="retrieval_profile_id",
                y="avg_business_score",
                title="Average Business Score By Profile",
            )
        with business_right:
            _render_bar_chart(
                _sort_frame(profile_df, by="avg_gold_alignment_score", ascending=False),
                x="retrieval_profile_id",
                y="avg_gold_alignment_score",
                title="Average Gold Alignment By Profile",
            )
        st.caption("Business score measures practical usefulness of the answer. Gold alignment measures how closely the answer matched the reference (ground truth) answer — only meaningful when a golden dataset is attached.")

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
        st.caption("Cost vs Business: top-left is ideal (high business value at low token cost). Technical vs Business: top-right is ideal — high scores on both independent dimensions.")

    # ── Risk & Compliance ──────────────────────────────────────────────────
    _has_risk_data = not profile_df.empty and (
        profile_df["missing_required_fact_rate"].fillna(0).sum() > 0
        or profile_df["forbidden_claim_violation_rate"].fillna(0).sum() > 0
    )
    if _has_risk_data:
        st.subheader("Risk & Compliance")
        st.caption(
            "Rates are computed over judged entries only. "
            "Missing fact rate signals corpus or retrieval gaps. "
            "Forbidden claim rate is a production safety signal — any non-zero value warrants inspection."
        )
        risk_left, risk_right = st.columns(2)
        with risk_left:
            _render_bar_chart(
                _sort_frame(profile_df, by="missing_required_fact_rate", ascending=False),
                x="retrieval_profile_id",
                y="missing_required_fact_rate",
                title="Missing Required Fact Rate By Profile",
            )
        with risk_right:
            _render_bar_chart(
                _sort_frame(profile_df, by="forbidden_claim_violation_rate", ascending=False),
                x="retrieval_profile_id",
                y="forbidden_claim_violation_rate",
                title="Forbidden Claim Violation Rate By Profile",
            )
        st.caption("Lower is always better on both charts. Missing fact rate = share of judged entries where a required fact was absent from the answer (corpus or retrieval gap). Forbidden claim rate = share of entries where the answer made a claim it was explicitly not supposed to (production safety risk).")

    st.subheader("Question Type Diagnostics")
    st.caption("Each cell shows the average score for that profile × question type combination. Darker = higher score. Look for a profile that dominates your most common question type — or use the Routing Strategy section below to get an automatic recommendation.")
    if not qtype_df.empty:
        qtype_left, qtype_right = st.columns(2)
        with qtype_left:
            _render_heatmap(
                qtype_df,
                x="retrieval_profile_id",
                y="question_type",
                color="avg_technical_score",
                title="Technical Score by Question Type × Profile",
            )
        with qtype_right:
            if "avg_judge_score" in qtype_df.columns and qtype_df["avg_judge_score"].fillna(0).sum() > 0:
                _render_heatmap(
                    qtype_df,
                    x="retrieval_profile_id",
                    y="question_type",
                    color="avg_judge_score",
                    title="Judge Score by Question Type × Profile",
                )
            elif "avg_business_score" in qtype_df.columns and qtype_df["avg_business_score"].fillna(0).sum() > 0:
                _render_heatmap(
                    qtype_df,
                    x="retrieval_profile_id",
                    y="question_type",
                    color="avg_business_score",
                    title="Business Score by Question Type × Profile",
                )
        if "avg_business_score" in qtype_df.columns and qtype_df["avg_business_score"].fillna(0).sum() > 0 \
                and "avg_judge_score" in qtype_df.columns and qtype_df["avg_judge_score"].fillna(0).sum() > 0:
            _render_heatmap(
                qtype_df,
                x="retrieval_profile_id",
                y="question_type",
                color="avg_business_score",
                title="Business Score by Question Type × Profile",
            )
    else:
        st.info("No question-type aggregates are available for this snapshot.")

    # ── Failure Mode Taxonomy ──────────────────────────────────────────────
    _top_missing, _top_risks = _aggregate_failure_modes(evaluation)
    if _top_missing or _top_risks:
        st.subheader("Failure Mode Taxonomy")
        st.caption(
            "Aggregated across all judged entries. Recurring missing facts indicate corpus or "
            "retrieval gaps — not just answer quality issues. Recurring risks may signal "
            "prompt, retrieval, or knowledge-base problems worth addressing."
        )
        fail_left, fail_right = st.columns(2)
        with fail_left:
            if _top_missing:
                st.markdown("**Top Missing Required Facts**")
                missing_rows = [
                    {"count": count, "missing fact": fact}
                    for fact, count in _top_missing
                ]
                _render_table(pd.DataFrame(missing_rows))
            else:
                st.caption("No missing required facts recorded across judged entries.")
        with fail_right:
            if _top_risks:
                st.markdown("**Top Recurring Risks**")
                risk_rows = [
                    {"count": count, "risk": risk}
                    for risk, count in _top_risks
                ]
                _render_table(pd.DataFrame(risk_rows))
            else:
                st.caption("No risks recorded across judged entries.")

    # ── 3B: Routing Strategy Builder ──────────────────────────────────────
    _routing_df = _routing_strategy_from_entries(entries_df)
    if not _routing_df.empty:
        st.subheader("Routing Strategy")
        st.caption(
            "Best retrieval profile per question type, ranked by average judge score "
            "(falls back to technical score when judge scoring is disabled). "
            "Confidence = high (≥5 samples) / medium (≥3) / low (<3). "
            "Score gap shows how much better the top profile is vs the runner-up."
        )
        with st.expander("Show routing table", expanded=True):
            _render_table(_routing_df)
            dl_col, _ = st.columns([1, 3])
            with dl_col:
                st.download_button(
                    "Download Routing Config JSON",
                    data=_routing_config_json(_routing_df),
                    file_name="routing_strategy.json",
                    mime="application/json",
                    key="phase3_routing_download",
                    use_container_width=True,
                )

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
        if diag_df["rate"].fillna(0).sum() > 0:
            diag_chart = (
                alt.Chart(diag_df)
                .mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4)
                .encode(
                    x=alt.X("retrieval_profile_id:N", title="Retrieval Profile"),
                    y=alt.Y("rate:Q", title="Rate"),
                    color=alt.Color("diagnostic:N", title="Diagnostic"),
                    tooltip=list(diag_df.columns),
                )
                .properties(height=300, title="Budget And Routing Diagnostics")
            )
            st.altair_chart(_configured_chart(diag_chart), use_container_width=True)
        else:
            st.caption("Budget and Routing Diagnostics — no budget exhaustion or broadened routing events recorded for this snapshot.")

    if not build_df.empty:
        with st.expander("Build × Profile Rollup", expanded=False):
            st.caption("Average technical score broken down by which build preset was used alongside each retrieval profile. Useful for spotting if a particular artifact build (e.g. pageindex_related_enhanced) consistently outperforms others.")
            _render_table(_sort_frame(build_df, by="avg_technical_score", ascending=False))

    if not corpus_df.empty:
        with st.expander("Corpus × Profile Rollup", expanded=False):
            st.caption("Average technical score per corpus × profile combination. Use this to check if score differences are driven by the corpus (document set) rather than the retrieval profile.")
            _render_table(_sort_frame(corpus_df, by="avg_technical_score", ascending=False))

    st.subheader("Entry Deep Dive")
    if entries_df.empty:
        st.info("No entry-level evaluation data is available.")
    else:
        f_col1, f_col2, f_col3 = st.columns(3)
        with f_col1:
            profile_filter = st.selectbox(
                "Filter by retrieval profile",
                options=["All", *sorted(entries_df["retrieval_profile_id"].dropna().unique().tolist())],
                index=0,
            )
        with f_col2:
            qtype_filter = st.selectbox(
                "Filter by question type",
                options=["All", *sorted(entries_df["question_type"].dropna().unique().tolist())],
                index=0,
            )
        with f_col3:
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

        # Build a focused display frame — truncate question, drop uninformative columns
        display_df = filtered_entries.copy()
        if "question" in display_df.columns:
            display_df["question"] = display_df["question"].apply(
                lambda q: (q[:68] + "…") if q and len(q) > 68 else (q or "")
            )
        display_cols = [c for c in [
            "question",
            "retrieval_profile_id",
            "question_type",
            "technical_score",
            "judge_score",
            "business_score",
            "groundedness_score",
            "total_time_seconds",
            "total_tokens",
            "status",
        ] if c in display_df.columns]
        with st.expander(f"Show entry table ({len(filtered_entries)} entries)", expanded=True):
            st.caption("Sorted by judge score descending, then technical score. Click 'Inspect evaluated entry' below to drill into any specific entry's full scorecard and rationale.")
            _render_table(
                _sort_frame(
                    display_df[display_cols],
                    by=["judge_score", "technical_score", "total_time_seconds"],
                    ascending=[False, False, True],
                )
            )

        def _entry_label(entry) -> str:
            q = entry.question.strip()
            q_short = q if len(q) <= 72 else q[:69] + "…"
            judge = entry.judge_scorecard
            score_tag = f" [{judge.overall_quality_score:.0f}]" if judge and judge.overall_quality_score is not None else ""
            return f"{q_short}{score_tag}  —  {entry.retrieval_profile_id} · {entry.question_type}"

        # ── Entry inspector filters ──────────────────────────────────────────
        st.markdown("**Inspect evaluated entry**")
        st.caption("Use the filters below to narrow the list, then pick an entry to drill in.")
        _all_entries = evaluation.entries
        _qtypes_all = sorted({e.question_type for e in _all_entries if e.question_type})
        _runs_all = sorted({e.source_run_title for e in _all_entries if e.source_run_title})
        _profiles_all = sorted({e.retrieval_profile_id for e in _all_entries if e.retrieval_profile_id})

        _fcol1, _fcol2, _fcol3, _fcol4 = st.columns([2, 2, 2, 1])
        with _fcol1:
            _f_qtype = st.selectbox(
                "Question type",
                options=["All"] + _qtypes_all,
                key="inspector_f_qtype",
            )
        with _fcol2:
            _f_run = st.selectbox(
                "Build / run",
                options=["All"] + _runs_all,
                key="inspector_f_run",
            )
        with _fcol3:
            _f_profile = st.selectbox(
                "Retrieval profile",
                options=["All"] + _profiles_all,
                key="inspector_f_profile",
            )
        with _fcol4:
            _f_min_score = st.number_input(
                "Min judge score",
                min_value=0,
                max_value=100,
                value=0,
                step=5,
                key="inspector_f_minscore",
            )

        def _entry_passes_filters(e) -> bool:
            if _f_qtype != "All" and e.question_type != _f_qtype:
                return False
            if _f_run != "All" and e.source_run_title != _f_run:
                return False
            if _f_profile != "All" and e.retrieval_profile_id != _f_profile:
                return False
            if _f_min_score > 0:
                judge_s = e.judge_scorecard
                score = judge_s.overall_quality_score if judge_s else None
                if score is None or score < _f_min_score:
                    return False
            return True

        entry_options = [i for i, e in enumerate(_all_entries) if _entry_passes_filters(e)]
        if not entry_options:
            st.warning("No entries match the current filters.")
            entry_options = list(range(len(_all_entries)))

        st.caption(f"{len(entry_options)} of {len(_all_entries)} entries match filters")
        selected_entry_idx = st.selectbox(
            "Select entry",
            options=entry_options,
            format_func=lambda i: _entry_label(evaluation.entries[i]),
            index=0,
            key="inspector_entry_select",
        )
        selected_entry = evaluation.entries[selected_entry_idx]

        # ── Entry context header ─────────────────────────────────────────────
        st.markdown("---")
        _meta1, _meta2, _meta3, _meta4 = st.columns([3, 1, 1, 1])
        with _meta1:
            _full_q = selected_entry.question.strip()
            st.markdown(f"**{_full_q}**")
            st.caption(f"Case: `{selected_entry.case_id}`")
        with _meta2:
            st.markdown("**Profile**")
            st.markdown(f"`{selected_entry.retrieval_profile_id}`")
        with _meta3:
            st.markdown("**Question type**")
            st.markdown(f"`{selected_entry.question_type}`")
        with _meta4:
            st.markdown("**Run**")
            _rtitle = selected_entry.source_run_title or selected_entry.source_run_id[:8]
            st.markdown(f"`{_rtitle}`")

        st.markdown("---")

        def _score_tile(col, label, value, decimals=0):
            with col:
                if value is None:
                    st.metric(label, "—")
                else:
                    color = "🟢" if value >= 70 else "🟡" if value >= 40 else "🔴"
                    fmt = f"{value:.{decimals}f}"
                    st.metric(label, f"{color} {fmt}")

        # ── Technical Scorecard ──────────────────────────────────────────────
        _sc = selected_entry.scorecard
        st.markdown("##### Technical Scorecard")
        st.caption("Deterministic — computed from runtime trace data, no LLM involved.")
        if _sc:
            sc1, sc2, sc3, sc4 = st.columns(4)
            _score_tile(sc1, "Technical", _sc.technical_score, 1)
            _score_tile(sc2, "Efficiency", _sc.efficiency_score, 1)
            _score_tile(sc3, "Reliability", _sc.reliability_score, 1)
            _score_tile(sc4, "Retrieval Discipline", _sc.retrieval_discipline_score, 1)
            if _sc.notes:
                with st.expander("Scoring notes", expanded=False):
                    for _note in _sc.notes:
                        st.markdown(f"- {_note}")
        else:
            st.caption("No technical scorecard available for this entry.")

        st.markdown("")

        # ── Judge Scorecard ──────────────────────────────────────────────────
        judge = selected_entry.judge_scorecard
        st.markdown("##### Judge Scorecard")
        st.caption("LLM-evaluated — scored at temperature 0 against the retrieved context and case constraints.")
        if judge:
            # Composite scores
            j1, j2, j3, j4 = st.columns(4)
            _score_tile(j1, "Overall", judge.overall_quality_score)
            _score_tile(j2, "Technical Quality", judge.technical_quality_score)
            _score_tile(j3, "Business Quality", judge.business_quality_score)
            _score_tile(j4, "Gold Alignment", judge.gold_alignment_score)

            st.markdown("")

            # Sub-dimension scores
            st.markdown("**Sub-dimensions**")
            d1, d2, d3, d4, d5 = st.columns(5)
            _score_tile(d1, "Groundedness", judge.groundedness_score)
            _score_tile(d2, "Completeness", judge.completeness_score)
            _score_tile(d3, "Directness", judge.directness_score)
            _score_tile(d4, "Actionability", judge.actionability_score)
            _score_tile(d5, "Abstention Q.", judge.abstention_quality_score)

            st.markdown("")

            # Rationale
            if judge.rationale:
                st.markdown("**Rationale**")
                st.info(judge.rationale)

            # Strengths + Risks side by side
            _has_strengths = bool(judge.strengths)
            _has_risks = bool(judge.risks)
            if _has_strengths or _has_risks:
                str_col, risk_col = st.columns(2)
                with str_col:
                    if _has_strengths:
                        st.markdown("**Strengths**")
                        for s in judge.strengths:
                            st.markdown(f"- ✅ {s}")
                with risk_col:
                    if _has_risks:
                        st.markdown("**Risks**")
                        for r in judge.risks:
                            st.markdown(f"- ⚠️ {r}")

            # Missing facts + Violations side by side
            _has_missing = bool(judge.missing_required_facts)
            _has_violations = bool(judge.forbidden_claim_violations)
            if _has_missing or _has_violations:
                mf_col, fv_col = st.columns(2)
                with mf_col:
                    if _has_missing:
                        st.markdown("**Missing Required Facts**")
                        for f in judge.missing_required_facts:
                            st.markdown(f"- ❌ {f}")
                with fv_col:
                    if _has_violations:
                        st.markdown("**Forbidden Claim Violations**")
                        for v in judge.forbidden_claim_violations:
                            st.markdown(f"- 🚫 {v}")

            if judge.matched_gold_sources:
                with st.expander(f"Matched gold sources ({len(judge.matched_gold_sources)})", expanded=False):
                    for src in judge.matched_gold_sources:
                        st.markdown(f"- `{src}`")
        else:
            st.caption("No judge scorecard — enable judge scoring when generating the evaluation.")

        # ── Developer details (collapsed by default) ─────────────────────────
        st.markdown("")
        with st.expander("Developer details", expanded=False):
            st.markdown("**Raw Metrics**")
            st.json(selected_entry.metrics)
            st.markdown("**Diagnostics**")
            st.json(selected_entry.diagnostics)
            matched_case = evaluation.suite.case_lookup().get(selected_entry.case_id)
            if matched_case is not None:
                st.markdown("**Golden Dataset Case**")
                st.json(matched_case.model_dump(mode="json"))
            if judge:
                st.markdown("**Raw Judge Scorecard**")
                st.json(judge.model_dump(mode="json"))
        if selected_entry.trace_path:
            with st.expander("Trace payload", expanded=False):
                st.json(_safe_read_json(selected_entry.trace_path))

        # ── 3A: Answer Comparison View ─────────────────────────────────────
        st.write("")
        with st.expander("Compare answers across profiles for a case", expanded=False):
            st.caption(
                "Select a case (question) to see every profile's answer and scores side by side. "
                "Uses the answer preview stored in the evaluation manifest."
            )
            # Build unique cases from entries that have at least 2 profiles
            _case_counts: dict[str, int] = {}
            for _e in evaluation.entries:
                if _e.status == "completed":
                    _case_counts[_e.case_id] = _case_counts.get(_e.case_id, 0) + 1
            _comparable_cases = [cid for cid, cnt in _case_counts.items() if cnt >= 2]
            if not _comparable_cases:
                st.info("No case has been answered by 2 or more profiles yet.")
            else:
                _case_lookup = evaluation.suite.case_lookup()
                _compare_case_id = st.selectbox(
                    "Select case to compare",
                    options=_comparable_cases,
                    format_func=lambda cid: (
                        (_case_lookup[cid].question[:90] + "…")
                        if cid in _case_lookup and len(_case_lookup[cid].question) > 90
                        else (_case_lookup[cid].question if cid in _case_lookup else cid)
                    ),
                    key="phase3_compare_case",
                )
                _case_entries = _answers_for_case(evaluation, _compare_case_id)
                if _case_entries:
                    _case_obj = _case_lookup.get(_compare_case_id)
                    if _case_obj:
                        st.markdown(f"**Question:** {_case_obj.question}")
                        if _case_obj.ground_truth_answer:
                            with st.expander("Ground truth answer", expanded=False):
                                st.markdown(_case_obj.ground_truth_answer)
                    n_profiles = len(_case_entries)
                    _ans_cols = st.columns(min(n_profiles, 3))
                    for _col_idx, _ce in enumerate(_case_entries):
                        _col = _ans_cols[_col_idx % len(_ans_cols)]
                        with _col:
                            _ce_judge = _ce.judge_scorecard
                            _ce_sc = _ce.scorecard
                            j_score = f"{_ce_judge.overall_quality_score:.0f}" if _ce_judge and _ce_judge.overall_quality_score is not None else "—"
                            t_score = f"{_ce_sc.technical_score:.0f}" if _ce_sc else "—"
                            st.markdown(
                                f"**{_ce.retrieval_profile_id}**  \n"
                                f"<span style='color:var(--muted);font-size:0.85rem'>"
                                f"judge {j_score} · tech {t_score}"
                                f"</span>",
                                unsafe_allow_html=True,
                            )
                            st.markdown(
                                _ce.answer_preview if _ce.answer_preview else "_No preview available._"
                            )
                            if _ce_judge and _ce_judge.rationale:
                                with st.expander("Rationale", expanded=False):
                                    st.caption(_ce_judge.rationale)

        # ── 3D: Low-Score Entry Export ─────────────────────────────────────
        with st.expander("Export low-scoring entries for improvement", expanded=False):
            st.caption(
                "Download a CSV of entries below a score threshold, including judge rationale, "
                "strengths, risks, and missing facts. Use this to feed a focused improvement cycle."
            )
            _has_judge_scores = entries_df["judge_score"].notna().any() if "judge_score" in entries_df.columns else False
            _export_score_col = st.radio(
                "Score to filter on",
                options=["judge_score", "technical_score"] if _has_judge_scores else ["technical_score"],
                format_func=lambda c: "Judge Score (overall quality)" if c == "judge_score" else "Technical Score (deterministic)",
                horizontal=True,
                key="phase3_export_score_col",
            )
            _export_threshold = st.slider(
                "Export entries with score below",
                min_value=0,
                max_value=100,
                value=65,
                step=5,
                key="phase3_export_threshold",
            )
            _below_count = sum(
                1 for _e in evaluation.entries
                if (
                    (_export_score_col == "judge_score" and _e.judge_scorecard and
                     _e.judge_scorecard.overall_quality_score is not None and
                     _e.judge_scorecard.overall_quality_score <= _export_threshold)
                    or
                    (_export_score_col == "technical_score" and _e.scorecard and
                     _e.scorecard.technical_score <= _export_threshold)
                )
            )
            st.caption(f"Entries matching filter: **{_below_count}** of {len(evaluation.entries)} total.")
            if _below_count > 0:
                _csv_data = _export_low_score_entries_csv(evaluation, _export_threshold, _export_score_col)
                if _csv_data:
                    st.download_button(
                        f"Download {_below_count} entries as CSV",
                        data=_csv_data,
                        file_name=f"low_score_entries_{evaluation.eval_run_id[:8]}_threshold{_export_threshold}.csv",
                        mime="text/csv",
                        key="phase3_export_csv",
                        use_container_width=False,
                    )
            else:
                st.info("No entries fall below the selected threshold.")

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
