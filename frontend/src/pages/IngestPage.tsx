import { useMemo, useState } from "react";

import { runIngestion } from "../api/client";
import { HelpHint } from "../components/HelpHint";
import { SelectMenu } from "../components/SelectMenu";
import type {
  IngestionRelationshipMode,
  IngestionResponse,
  RuntimeSummary,
  WorkspaceRenderModel,
} from "../types";

type IngestPageProps = {
  runtime: RuntimeSummary | null;
  onIngestionCompleted?: () => void;
};

function slugify(value: string): string {
  return value
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "");
}

function summarizeValue(value: unknown): string {
  if (value == null) {
    return "n/a";
  }
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  return JSON.stringify(value, null, 2);
}

export function useIngestWorkspace({
  runtime,
  onIngestionCompleted,
}: IngestPageProps): WorkspaceRenderModel {
  const [file, setFile] = useState<File | null>(null);
  const [docId, setDocId] = useState("");
  const [docTitle, setDocTitle] = useState("");
  const [docType, setDocType] = useState("technical_spec");
  const [topSectionsTarget, setTopSectionsTarget] = useState(4);
  const [relationshipMode, setRelationshipMode] =
    useState<IngestionRelationshipMode>("basic");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<IngestionResponse | null>(null);

  const sidebarSections = useMemo(
    () => [
      {
        title: "Ingestion Scope",
        description: "The center workspace owns the actual controls. The sidebar explains what those choices affect.",
        children: (
          <>
            <div className="support-card">
              <strong>Query settings are separate.</strong>
              <p className="muted">
                Nothing on this page affects asking questions until you actually ingest the document.
              </p>
            </div>
            <div className="mini-card">
              <span>Current project</span>
              <strong>{runtime?.project ?? "No project selected"}</strong>
            </div>
            <div className="mini-card">
              <span>Current model</span>
              <strong>{runtime?.model ?? "No model selected"}</strong>
            </div>
          </>
        ),
      },
      {
        title: "What Happens On Run",
        description:
          "The run button executes the existing ingestion pipeline. The React UI is only changing how you drive it.",
        children: (
          <>
            <ul className="bullet-list">
              <li>
                <strong>1. Preprocess source</strong>
                <span>DOCX files are converted before PageIndex ingestion.</span>
              </li>
              <li>
                <strong>2. Build per-document tree</strong>
                <span>The document is indexed into a PageIndex-style tree.</span>
              </li>
              <li>
                <strong>3. Generate master node</strong>
                <span>The doc contributes a routing summary to the project master tree.</span>
              </li>
              <li>
                <strong>4. Reconcile relationships</strong>
                <span>The selected relationship mode controls whether cross-document links are maintained.</span>
              </li>
            </ul>
          </>
        ),
      },
    ],
    [pending, runtime],
  );

  async function handleIngest() {
    if (!runtime || !file || !docId.trim() || !docTitle.trim() || pending) {
      return;
    }

    setPending(true);
    setError(null);

    try {
      const response = await runIngestion({
        project: runtime.project,
        model: runtime.model,
        file,
        doc_id: docId.trim(),
        doc_title: docTitle.trim(),
        doc_type: docType.trim(),
        top_sections_target: topSectionsTarget,
        relationship_mode: relationshipMode,
      });
      setResult(response);
      onIngestionCompleted?.();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Ingestion failed.");
    } finally {
      setPending(false);
    }
  }

  function handleFileChange(nextFile: File | null) {
    setFile(nextFile);
    if (!nextFile) {
      return;
    }

    if (!docTitle.trim()) {
      setDocTitle(nextFile.name.replace(/\.[^.]+$/, ""));
    }
    if (!docId.trim()) {
      setDocId(slugify(nextFile.name.replace(/\.[^.]+$/, "")));
    }
  }

  return {
    sidebarSections,
    content: (
      <div className="workspace-grid workspace-grid-ingest">
        <section className="workspace-card">
          <div className="card-header">
            <div>
              <h3>Ingestion setup</h3>
              <p className="muted">
                All editable ingestion settings live here together. If you can see a value in this panel, you can change it here.
              </p>
            </div>
            {runtime ? <span className="pill">{runtime.project}</span> : null}
          </div>

          <label className="upload-dropzone">
            <input
              type="file"
              disabled={pending}
              accept=".pdf,.md,.markdown,.docx"
              onChange={(event) => handleFileChange(event.target.files?.[0] ?? null)}
            />
            {file ? (
              <>
                <span className="upload-dropzone-icon">📄</span>
                <span className="upload-dropzone-label">{file.name}</span>
                <span className="upload-dropzone-sub">Click to replace</span>
              </>
            ) : (
              <>
                <span className="upload-dropzone-icon">⬆</span>
                <span className="upload-dropzone-label">Click or drop a file here</span>
                <span className="upload-dropzone-sub">PDF, Markdown, or DOCX</span>
              </>
            )}
          </label>

          <div className="form-grid">
            <label className="field">
              <div className="field-heading">
                <span>Document ID</span>
                <HelpHint
                  label="Document ID"
                  text="A stable machine-safe identifier inside the current project. Reusing it means you are intentionally targeting the same logical document."
                />
              </div>
              <input
                type="text"
                placeholder="stable_doc_id"
                value={docId}
                disabled={pending}
                onChange={(event) => setDocId(event.target.value)}
              />
            </label>

            <label className="field">
              <div className="field-heading">
                <span>Title</span>
                <HelpHint
                  label="Title"
                  text="Human-readable display name used in the UI and traces. It does not control storage paths."
                />
              </div>
              <input
                type="text"
                placeholder="Readable title"
                value={docTitle}
                disabled={pending}
                onChange={(event) => setDocTitle(event.target.value)}
              />
            </label>

            <label className="field">
              <div className="field-heading">
                <span>Document type</span>
                <HelpHint
                  label="Document type"
                  text="A descriptive category such as technical_spec or policy. It helps humans and future filtering, but does not change file parsing."
                />
              </div>
              <input
                type="text"
                value={docType}
                disabled={pending}
                onChange={(event) => setDocType(event.target.value)}
              />
            </label>

            <label className="field">
              <div className="field-heading">
                <span>Master top-sections target</span>
                <HelpHint
                  label="Master top-sections target"
                  text="Controls how many top sections feed the master-node summary. Higher values improve routing detail but grow the master-tree prompt."
                />
              </div>
              <input
                type="number"
                min={1}
                max={12}
                value={topSectionsTarget}
                disabled={pending}
                onChange={(event) => setTopSectionsTarget(Number(event.target.value))}
              />
            </label>

            <label className="field">
              <div className="field-heading">
                <span>Relationship maintenance</span>
                <HelpHint
                  label="Relationship maintenance"
                  text="Off builds plain trees only. Basic adds deterministic relationship reconciliation. Enhanced adds richer cross-document maintenance but costs more time."
                />
              </div>
              <SelectMenu
                value={relationshipMode}
                disabled={pending}
                options={[
                  { value: "off", label: "off", note: "no reconciliation" },
                  { value: "basic", label: "basic", note: "deterministic links" },
                  { value: "enhanced", label: "enhanced", note: "richer maintenance" },
                ]}
                onChange={(value) =>
                  setRelationshipMode(value as IngestionRelationshipMode)
                }
              />
            </label>
          </div>

          <div className="inline-meta-grid">
            <div className="mini-card">
              <span>Selected file</span>
              <strong>{file?.name ?? "none"}</strong>
            </div>
            <div className="mini-card">
              <span>Relationship mode</span>
              <strong>{relationshipMode}</strong>
            </div>
            <div className="mini-card">
              <span>Top sections target</span>
              <strong>{topSectionsTarget}</strong>
            </div>
          </div>

          <div className="card-actions">
            <button
              type="button"
              disabled={pending || !runtime || !file || !docId.trim() || !docTitle.trim()}
              onClick={handleIngest}
            >
              {pending ? "Running ingestion…" : "Run ingestion"}
            </button>
            <span className="muted">
              Phase 1 uses a standard request/response. Progress streaming can layer on later without
              changing the ingestion engine.
            </span>
          </div>

          {error ? <div className="notice notice-error">{error}</div> : null}
        </section>

        <section className="workspace-card">
          <h3>Current runtime</h3>
          {runtime ? (
            <dl className="detail-list">
              <div>
                <dt>Project</dt>
                <dd>{runtime.project}</dd>
              </div>
              <div>
                <dt>Model</dt>
                <dd>{runtime.model}</dd>
              </div>
              <div>
                <dt>Indexed docs</dt>
                <dd>{runtime.doc_count}</dd>
              </div>
            </dl>
          ) : (
            <p className="muted">Runtime not loaded.</p>
          )}
        </section>

        <section className="workspace-card workspace-card-wide">
          <h3>Latest ingestion result</h3>
          {result ? (
            <>
              <div className="inline-meta-grid">
                <div className="mini-card">
                  <span>Document</span>
                  <strong>{result.trace.doc_id}</strong>
                </div>
                <div className="mini-card">
                  <span>File type</span>
                  <strong>{result.trace.file_type}</strong>
                </div>
                <div className="mini-card">
                  <span>Steps</span>
                  <strong>{result.trace.steps.length}</strong>
                </div>
              </div>

              <div className="step-list">
                {result.trace.steps.map((step, index) => (
                  <article className="step-card" key={`${step.name}-${index}`}>
                    <h4>{step.name}</h4>
                    <p>{step.detail}</p>
                    {step.payload != null ? (
                      <pre className="json-block">{summarizeValue(step.payload)}</pre>
                    ) : null}
                  </article>
                ))}
              </div>
            </>
          ) : (
            <p className="muted">
              No ingestion has been run in this new shell yet. The first successful run will show its
              step trace here.
            </p>
          )}
        </section>
      </div>
    ),
  };
}
