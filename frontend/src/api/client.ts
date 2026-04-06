import { API_BASE_URL } from "../config";
import type {
  DocumentTreeResponse,
  IngestionRequest,
  IngestionResponse,
  PostHocAnalysis,
  QueryRequest,
  QueryResponse,
  RuntimeSummary,
  TraceDetail,
  TraceSummary,
} from "../types";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers ?? {});
  const body = init?.body;

  if (!(body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers,
  });

  if (!response.ok) {
    const text = await response.text();
    let detail = text;
    try {
      const parsed = JSON.parse(text) as { detail?: string };
      detail = parsed.detail ?? text;
    } catch {
      // Response was not JSON; keep raw text.
    }
    throw new Error(detail || `Request failed with status ${response.status}`);
  }

  return (await response.json()) as T;
}

function withRuntimeParams(project: string, model?: string | null): string {
  const params = new URLSearchParams({ project });
  if (model) {
    params.set("model", model);
  }
  return params.toString();
}

export async function fetchProjects(): Promise<string[]> {
  const data = await request<{ projects: string[] }>("/api/projects");
  return data.projects;
}

export async function deleteProject(project: string): Promise<void> {
  await request<{ deleted: string }>(`/api/projects/${encodeURIComponent(project)}`, {
    method: "DELETE",
  });
}

export async function fetchModels(): Promise<string[]> {
  const data = await request<{ models: string[] }>("/api/models");
  return data.models;
}

export async function fetchRuntime(project: string, model?: string | null): Promise<RuntimeSummary> {
  return request<RuntimeSummary>(`/api/runtime?${withRuntimeParams(project, model)}`);
}

export async function runQuery(payload: QueryRequest): Promise<QueryResponse> {
  return request<QueryResponse>("/api/query", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function runIngestion(payload: IngestionRequest): Promise<IngestionResponse> {
  const form = new FormData();
  form.append("file", payload.file);
  form.append("project", payload.project);
  form.append("doc_id", payload.doc_id);
  form.append("doc_title", payload.doc_title);
  form.append("doc_type", payload.doc_type);
  form.append("relationship_mode", payload.relationship_mode);
  if (payload.model) {
    form.append("model", payload.model);
  }
  if (payload.top_sections_target != null) {
    form.append("top_sections_target", String(payload.top_sections_target));
  }

  return request<IngestionResponse>("/api/ingest", {
    method: "POST",
    body: form,
  });
}

export async function fetchTraces(project: string, model?: string | null): Promise<TraceSummary[]> {
  const data = await request<{ traces: TraceSummary[] }>(
    `/api/traces?${withRuntimeParams(project, model)}`,
  );
  return data.traces;
}

export async function fetchDocumentTree(
  project: string,
  model: string | null | undefined,
  docId: string,
): Promise<DocumentTreeResponse> {
  return request<DocumentTreeResponse>(
    `/api/documents/${encodeURIComponent(docId)}/tree?${withRuntimeParams(project, model)}`,
  );
}

export async function fetchTrace(
  project: string,
  model: string | null | undefined,
  traceId: string,
): Promise<TraceDetail> {
  return request<TraceDetail>(
    `/api/traces/${encodeURIComponent(traceId)}?${withRuntimeParams(project, model)}`,
  );
}

export async function analyzeTrace(
  project: string,
  model: string | null | undefined,
  traceId: string,
): Promise<PostHocAnalysis> {
  return request<PostHocAnalysis>(
    `/api/traces/${encodeURIComponent(traceId)}/analysis?${withRuntimeParams(project, model)}`,
    {
      method: "POST",
    },
  );
}
