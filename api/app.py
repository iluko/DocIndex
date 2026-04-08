"""FastAPI adapter for the existing Hybrid Approach runtime."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware

from api.contracts import (
    CreateProjectRequest,
    IngestionMetadata,
    QueryRequest,
    RegisterModelRequest,
)
from api.runtime import build_runtime, get_model_registry, persist_upload
from index_registry import delete_project, list_projects, sanitize_index_key_part
from ingestion.ingest import ingest_document_with_trace
from retrieval.query_engine import AdvancedRetrievalConfig, query
from utils import get_default_model, validate_doc_id


def _to_jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if is_dataclass(value):
        return {key: _to_jsonable(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {str(key): _to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_to_jsonable(item) for item in value]
    return value


app = FastAPI(
    title="Hybrid Approach API",
    version="0.1.0",
    description="Thin API adapter over the existing ingestion, retrieval, and tracing runtime.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/projects")
def get_projects() -> dict[str, list[str]]:
    return {"projects": list_projects(Path(__file__).resolve().parent.parent / "data")}


@app.post("/api/projects")
def create_project(payload: CreateProjectRequest) -> dict[str, Any]:
    runtime = build_runtime(project=payload.name, model=get_default_model())
    logical_project = sanitize_index_key_part(payload.name)
    return {
        "project": logical_project,
        "index_dir": str(runtime.index_context.index_dir),
    }


@app.delete("/api/projects/{project}")
def remove_project(project: str) -> dict[str, str]:
    delete_project(Path(__file__).resolve().parent.parent / "data", project)
    return {"deleted": project}


@app.get("/api/models")
def get_models() -> dict[str, list[str]]:
    registry = get_model_registry()
    registry.ensure_model(get_default_model())
    return {"models": registry.list_models()}


@app.post("/api/models")
def register_model(payload: RegisterModelRequest) -> dict[str, str]:
    registry = get_model_registry()
    model = registry.add_model(payload.name)
    return {"model": model}


@app.get("/api/runtime")
def get_runtime(
    project: str = Query(...),
    model: str | None = Query(default=None),
) -> dict[str, Any]:
    runtime = build_runtime(project=project, model=model)
    logical_project = sanitize_index_key_part(project)
    docs = runtime.master_tree_store.list_docs()
    return {
        "project": logical_project,
        "provider": runtime.index_context.provider,
        "model": runtime.index_context.model,
        "index_dir": str(runtime.index_context.index_dir),
        "doc_count": len(docs),
        "docs": [
            {
                "doc_id": doc.doc_id,
                "doc_title": doc.doc_title,
                "doc_type": doc.doc_type,
                "related_docs": doc.related_docs,
            }
            for doc in docs
        ],
    }


@app.get("/api/documents")
def get_documents(
    project: str = Query(...),
    model: str | None = Query(default=None),
) -> dict[str, Any]:
    runtime = build_runtime(project=project, model=model)
    docs = runtime.master_tree_store.list_docs()
    return {"documents": [_to_jsonable(doc) for doc in docs]}


@app.get("/api/documents/{doc_id}")
def get_document(
    doc_id: str,
    project: str = Query(...),
    model: str | None = Query(default=None),
) -> dict[str, Any]:
    runtime = build_runtime(project=project, model=model)
    node = runtime.master_tree_store.get_node(doc_id)
    if node is None:
        raise HTTPException(status_code=404, detail=f"Document '{doc_id}' not found.")
    return {"document": _to_jsonable(node)}


@app.get("/api/documents/{doc_id}/tree")
def get_document_tree(
    doc_id: str,
    project: str = Query(...),
    model: str | None = Query(default=None),
) -> dict[str, Any]:
    runtime = build_runtime(project=project, model=model)
    try:
        tree = runtime.storage.load_doc_tree(doc_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"doc_id": doc_id, "tree": tree}


@app.post("/api/query")
async def run_query(payload: QueryRequest) -> Any:
    runtime = build_runtime(project=payload.project, model=payload.model)
    try:
        result = await query(
            user_query=payload.user_query,
            master_tree_store=runtime.master_tree_store,
            storage=runtime.storage,
            model=runtime.index_context.model,
            conversation_context=payload.conversation_context,
            max_docs=payload.max_docs,
            verbose=False,
            reasoning_effort=payload.reasoning_effort,
            trace_service=runtime.trace_service,
            project=runtime.index_context.project,
            advanced_retrieval=AdvancedRetrievalConfig(
                enabled=payload.advanced_retrieval.enabled,
                enable_planning=payload.advanced_retrieval.enable_planning,
                enable_adaptive_width=payload.advanced_retrieval.enable_adaptive_width,
                enable_node_expansion=payload.advanced_retrieval.enable_node_expansion,
                max_docs_cap=payload.advanced_retrieval.max_docs_cap,
                max_nodes_cap=payload.advanced_retrieval.max_nodes_cap,
            ),
            retrieval_mode=payload.retrieval_mode,
            retrieval_only=payload.retrieval_only,
        )
    except Exception as exc:  # pragma: no cover - runtime path
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return jsonable_encoder(_to_jsonable(result))


@app.post("/api/ingest")
async def run_ingestion(
    file: UploadFile = File(...),
    project: str = Form(...),
    model: str | None = Form(default=None),
    doc_id: str = Form(...),
    doc_title: str = Form(...),
    doc_type: str = Form(...),
    top_sections_target: int | None = Form(default=None),
    relationship_mode: str = Form(default="basic"),
) -> Any:
    try:
        validate_doc_id(doc_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    runtime = build_runtime(project=project, model=model)
    saved_path = await persist_upload(file, runtime.index_context.uploads_dir, doc_id)

    try:
        result = await ingest_document_with_trace(
            file_path=str(saved_path),
            doc_id=doc_id,
            doc_title=doc_title,
            doc_type=doc_type,
            master_tree_store=runtime.master_tree_store,
            storage=runtime.storage,
            model=runtime.index_context.model,
            top_sections_target=top_sections_target,
            relationship_mode=relationship_mode,
        )
    except Exception as exc:  # pragma: no cover - runtime path
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    runtime.master_tree_store.load()
    return jsonable_encoder(_to_jsonable(result))


@app.get("/api/traces")
def get_traces(
    project: str = Query(...),
    model: str | None = Query(default=None),
    limit: int = Query(default=25, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    search: str | None = Query(default=None),
) -> dict[str, Any]:
    runtime = build_runtime(project=project, model=model)
    traces = runtime.trace_service.list_traces(limit=limit, offset=offset, search=search)
    return {"traces": [_to_jsonable(trace) for trace in traces]}


@app.get("/api/traces/stats")
def get_trace_stats(
    project: str = Query(...),
    model: str | None = Query(default=None),
) -> Any:
    runtime = build_runtime(project=project, model=model)
    return jsonable_encoder(_to_jsonable(runtime.trace_service.get_stats()))


@app.get("/api/traces/{trace_id}")
def get_trace(
    trace_id: str,
    project: str = Query(...),
    model: str | None = Query(default=None),
) -> Any:
    runtime = build_runtime(project=project, model=model)
    trace = runtime.trace_service.get_trace(trace_id)
    if trace is None:
        raise HTTPException(status_code=404, detail=f"Trace '{trace_id}' not found.")
    return jsonable_encoder(_to_jsonable(trace))


@app.post("/api/traces/{trace_id}/analysis")
async def analyze_trace(
    trace_id: str,
    project: str = Query(...),
    model: str | None = Query(default=None),
) -> Any:
    runtime = build_runtime(project=project, model=model)
    analysis = await runtime.trace_service.run_post_hoc_analysis(
        trace_id=trace_id,
        model=runtime.index_context.model,
    )
    if analysis is None:
        raise HTTPException(status_code=404, detail=f"Trace '{trace_id}' not found.")
    return jsonable_encoder(_to_jsonable(analysis))
