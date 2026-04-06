import { useDeferredValue, useEffect, useMemo, useState } from "react";

import { analyzeTrace, deleteProject, fetchDocumentTree, fetchTrace } from "../api/client";
import { HelpHint } from "../components/HelpHint";
import { InspectorPanel } from "../components/InspectorPanel";
import { SelectMenu } from "../components/SelectMenu";
import type {
  DocumentTreeResponse,
  PostHocAnalysis,
  RuntimeSummary,
  TraceDetail,
  TraceSummary,
  WorkspaceRenderModel,
} from "../types";

type InspectMode = "documents" | "traces";

type InspectPageProps = {
  runtime: RuntimeSummary | null;
  traces: TraceSummary[];
  onProjectDeleted?: (project: string) => Promise<void> | void;
};

function formatJson(value: unknown): string {
  return JSON.stringify(value, null, 2);
}

export function useInspectWorkspace({
  runtime,
  traces,
  onProjectDeleted,
}: InspectPageProps): WorkspaceRenderModel {
  const [inspectMode, setInspectMode] = useState<InspectMode>("traces");
  const [search, setSearch] = useState("");
  const deferredSearch = useDeferredValue(search.trim().toLowerCase());
  const [selectedDocId, setSelectedDocId] = useState<string | null>(null);
  const [selectedTraceId, setSelectedTraceId] = useState<string | null>(null);
  const [docTree, setDocTree] = useState<DocumentTreeResponse | null>(null);
  const [traceDetail, setTraceDetail] = useState<TraceDetail | null>(null);
  const [analysis, setAnalysis] = useState<PostHocAnalysis | null>(null);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [analysisPending, setAnalysisPending] = useState(false);
  const [deletePending, setDeletePending] = useState(false);
  const [deleteConfirmValue, setDeleteConfirmValue] = useState("");
  const [error, setError] = useState<string | null>(null);

  const filteredDocs = useMemo(() => {
    const docs = runtime?.docs ?? [];
    if (!deferredSearch) {
      return docs;
    }
    return docs.filter((doc) =>
      [doc.doc_id, doc.doc_title, doc.doc_type].some((value) =>
        value.toLowerCase().includes(deferredSearch),
      ),
    );
  }, [deferredSearch, runtime?.docs]);

  const filteredTraces = useMemo(() => {
    if (!deferredSearch) {
      return traces;
    }
    return traces.filter((trace) =>
      [trace.trace_id, trace.query_preview, trace.retrieval_mode, trace.model].some((value) =>
        value.toLowerCase().includes(deferredSearch),
      ),
    );
  }, [deferredSearch, traces]);

  useEffect(() => {
    if (inspectMode !== "documents" || !runtime) {
      return;
    }
    if (!selectedDocId && filteredDocs.length > 0) {
      setSelectedDocId(filteredDocs[0].doc_id);
    }
  }, [filteredDocs, inspectMode, runtime, selectedDocId]);

  useEffect(() => {
    if (inspectMode !== "traces") {
      return;
    }
    if (!selectedTraceId && filteredTraces.length > 0) {
      setSelectedTraceId(filteredTraces[0].trace_id);
    }
  }, [filteredTraces, inspectMode, selectedTraceId]);

  useEffect(() => {
    if (!runtime || inspectMode !== "documents" || !selectedDocId) {
      return;
    }

    let cancelled = false;
    setLoadingDetail(true);
    setError(null);
    void fetchDocumentTree(runtime.project, runtime.model, selectedDocId)
      .then((tree) => {
        if (!cancelled) {
          setDocTree(tree);
        }
      })
      .catch((cause) => {
        if (!cancelled) {
          setError(cause instanceof Error ? cause.message : "Failed to load document tree.");
        }
      })
      .finally(() => {
        if (!cancelled) {
          setLoadingDetail(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [inspectMode, runtime, selectedDocId]);

  useEffect(() => {
    if (!runtime || inspectMode !== "traces" || !selectedTraceId) {
      return;
    }

    let cancelled = false;
    setLoadingDetail(true);
    setError(null);
    setAnalysis(null);
    void fetchTrace(runtime.project, runtime.model, selectedTraceId)
      .then((trace) => {
        if (!cancelled) {
          setTraceDetail(trace);
        }
      })
      .catch((cause) => {
        if (!cancelled) {
          setError(cause instanceof Error ? cause.message : "Failed to load trace.");
        }
      })
      .finally(() => {
        if (!cancelled) {
          setLoadingDetail(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [inspectMode, runtime, selectedTraceId]);

  async function handleAnalyzeTrace() {
    if (!runtime || !selectedTraceId || analysisPending) {
      return;
    }

    setAnalysisPending(true);
    setError(null);
    try {
      const nextAnalysis = await analyzeTrace(runtime.project, runtime.model, selectedTraceId);
      setAnalysis(nextAnalysis);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Trace analysis failed.");
    } finally {
      setAnalysisPending(false);
    }
  }

  async function handleDeleteProject() {
    if (!runtime || deletePending || deleteConfirmValue.trim() !== runtime.project) {
      return;
    }

    const confirmed = window.confirm(
      `Delete project "${runtime.project}" and all of its indexed artifacts? This cannot be undone.`,
    );
    if (!confirmed) {
      return;
    }

    setDeletePending(true);
    setError(null);
    try {
      await deleteProject(runtime.project);
      setDeleteConfirmValue("");
      setDocTree(null);
      setTraceDetail(null);
      setAnalysis(null);
      setSelectedDocId(null);
      setSelectedTraceId(null);
      await onProjectDeleted?.(runtime.project);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Project deletion failed.");
    } finally {
      setDeletePending(false);
    }
  }

  const sidebarSections = useMemo(
    () => {
      const sections = [
        {
          title: "Inspect Filters",
          description:
            "This workspace owns browsing, drill-down, and post-hoc analysis. It does not interfere with assistant state.",
          children: (
            <>
              <label className="field">
                <div className="field-heading">
                  <span>Search</span>
                  <HelpHint
                    label="Search"
                    text="Filters whichever inspect mode is active. In Documents mode it filters doc metadata; in Traces mode it filters trace summaries."
                  />
                </div>
                <input
                  type="text"
                  placeholder="document id, title, query, model"
                  value={search}
                  onChange={(event) => setSearch(event.target.value)}
                />
              </label>
              <label className="field">
                <div className="field-heading">
                  <span>Inspect mode</span>
                  <HelpHint
                    label="Inspect mode"
                    text="Documents shows indexed corpus structure. Traces shows past query executions and their diagnostics."
                  />
                </div>
                <SelectMenu
                  value={inspectMode}
                  options={[
                    { value: "documents", label: "Documents", note: "browse indexed corpus" },
                    { value: "traces", label: "Traces", note: "browse query runs" },
                  ]}
                  onChange={(value) => setInspectMode(value as InspectMode)}
                />
              </label>
            </>
          ),
        },
      ];

      if (runtime) {
        sections.push({
          title: "Project Danger Zone",
          description:
            "Project deletion is scoped here because it removes indexed artifacts and traces for the active project.",
          children: (
            <>
              <div className="danger-card">
                <strong>{runtime.project}</strong>
                <p className="muted">
                  Deletes the project index, document trees, and project-scoped traces. Model registry
                  entries are not affected.
                </p>
              </div>
              <label className="field">
                <div className="field-heading">
                  <span>Type project name to confirm</span>
                  <HelpHint
                    label="Delete project confirmation"
                    text="This forces an explicit confirmation string before the delete button unlocks."
                  />
                </div>
                <input
                  type="text"
                  placeholder={runtime.project}
                  value={deleteConfirmValue}
                  disabled={deletePending}
                  onChange={(event) => setDeleteConfirmValue(event.target.value)}
                />
              </label>
              <button
                type="button"
                className="danger-button"
                disabled={deletePending || deleteConfirmValue.trim() !== runtime.project}
                onClick={handleDeleteProject}
              >
                {deletePending ? "Deleting project…" : "Delete project"}
              </button>
            </>
          ),
        });
      }

      return sections;
    },
    [deleteConfirmValue, deletePending, handleDeleteProject, inspectMode, runtime, search],
  );

  return {
    sidebarSections,
    content: (
      <div className="workspace-grid workspace-grid-inspect">
        <section className="workspace-card">
          <div className="card-header">
            <div>
              <h3>{inspectMode === "documents" ? "Indexed documents" : "Recent traces"}</h3>
              <p className="muted">
                Switch modes without carrying that state into the assistant workspace.
              </p>
            </div>
            <span className="pill">
              {inspectMode === "documents" ? filteredDocs.length : filteredTraces.length} visible
            </span>
          </div>

          <div className="list-grid">
            {inspectMode === "documents"
              ? filteredDocs.map((doc) => (
                  <button
                    type="button"
                    className={`list-card list-card-button ${
                      selectedDocId === doc.doc_id ? "list-card-selected" : ""
                    }`}
                    key={doc.doc_id}
                    onClick={() => setSelectedDocId(doc.doc_id)}
                  >
                    <h4>{doc.doc_title}</h4>
                    <p>{doc.doc_id}</p>
                    <span>{doc.doc_type}</span>
                  </button>
                ))
              : filteredTraces.map((trace) => (
                  <button
                    type="button"
                    className={`list-card list-card-button ${
                      selectedTraceId === trace.trace_id ? "list-card-selected" : ""
                    }`}
                    key={trace.trace_id}
                    onClick={() => setSelectedTraceId(trace.trace_id)}
                  >
                    <h4>{trace.query_preview}</h4>
                    <p>
                      {trace.retrieval_mode} · {trace.total_seconds.toFixed(2)}s · {trace.total_tokens} tok
                    </p>
                    <span>{trace.trace_id}</span>
                  </button>
                ))}
            {(inspectMode === "documents" ? filteredDocs.length : filteredTraces.length) === 0 ? (
              <p className="muted">Nothing matches the current filter.</p>
            ) : null}
          </div>
        </section>

        <InspectorPanel
          title={inspectMode === "documents" ? "Document detail" : "Trace detail"}
          description="This panel shows raw system artifacts without pushing them into the chat flow."
        >
          {loadingDetail ? <p className="muted">Loading detail…</p> : null}
          {error ? <div className="notice notice-error">{error}</div> : null}

          {inspectMode === "documents" ? (
            docTree ? (
              <>
                <div className="inspector-block">
                  <h4>Selected document</h4>
                  <p>{docTree.doc_id}</p>
                </div>
                <div className="inspector-block">
                  <h4>Tree JSON</h4>
                  <pre className="json-block">{formatJson(docTree.tree)}</pre>
                </div>
              </>
            ) : (
              <p className="muted">Select a document to inspect its tree.</p>
            )
          ) : traceDetail ? (
            <>
              <div className="stat-grid">
                <div className="mini-card">
                  <span>Docs routed</span>
                  <strong>{traceDetail.metrics.docs_routed}</strong>
                </div>
                <div className="mini-card">
                  <span>Sections</span>
                  <strong>{traceDetail.metrics.sections_retrieved}</strong>
                </div>
                <div className="mini-card">
                  <span>Total time</span>
                  <strong>{traceDetail.metrics.total_seconds.toFixed(2)}s</strong>
                </div>
                <div className="mini-card">
                  <span>Tokens</span>
                  <strong>{traceDetail.metrics.total_tokens}</strong>
                </div>
              </div>

              <div className="inspector-block">
                <div className="inspector-row">
                  <h4>Answer</h4>
                  <button type="button" disabled={analysisPending} onClick={handleAnalyzeTrace}>
                    {analysisPending ? "Analyzing…" : "Run post-hoc analysis"}
                  </button>
                </div>
                <div className="answer-card">{traceDetail.answer}</div>
              </div>

              <div className="inspector-block">
                <h4>Routing</h4>
                <pre className="json-block">{formatJson(traceDetail.routing)}</pre>
              </div>

              <div className="inspector-block">
                <h4>Retrieved sections</h4>
                <pre className="json-block">{formatJson(traceDetail.sections_used)}</pre>
              </div>

              {analysis ? (
                <div className="inspector-block">
                  <h4>Post-hoc analysis</h4>
                  <pre className="json-block">{formatJson(analysis)}</pre>
                </div>
              ) : null}
            </>
          ) : (
            <p className="muted">Select a trace to inspect it in detail.</p>
          )}
        </InspectorPanel>
      </div>
    ),
  };
}
