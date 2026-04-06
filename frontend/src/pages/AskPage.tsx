import { useMemo, useState } from "react";

import { runQuery } from "../api/client";
import { ChatThread } from "../components/ChatThread";
import { HelpHint } from "../components/HelpHint";
import { InspectorPanel } from "../components/InspectorPanel";
import { SelectMenu } from "../components/SelectMenu";
import type {
  QueryRequest,
  QueryResponse,
  RuntimeSummary,
  WorkspaceRenderModel,
} from "../types";

type AskPageProps = {
  runtime: RuntimeSummary | null;
  onQueryCompleted?: () => void;
};

type Turn = {
  role: "user" | "assistant";
  content: string;
};

const REASONING_OPTIONS = ["default", "low", "medium", "high"] as const;

function formatMetric(value: number | null | undefined, suffix = ""): string {
  if (value == null || Number.isNaN(value)) {
    return "n/a";
  }
  return `${value}${suffix}`;
}

export function useAskWorkspace({ runtime, onQueryCompleted }: AskPageProps): WorkspaceRenderModel {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [draft, setDraft] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastResponse, setLastResponse] = useState<QueryResponse | null>(null);
  const [lastRequest, setLastRequest] = useState<QueryRequest | null>(null);
  const [reasoningEffort, setReasoningEffort] = useState<(typeof REASONING_OPTIONS)[number]>("default");
  const [settings, setSettings] = useState<QueryRequest["advanced_retrieval"]>({
    enabled: false,
    enable_planning: true,
    enable_adaptive_width: true,
    enable_node_expansion: true,
    max_docs_cap: 6,
    max_nodes_cap: 6,
  });
  const [maxDocs, setMaxDocs] = useState(3);
  const [retrievalMode, setRetrievalMode] = useState<"hybrid" | "pageindex">("hybrid");

  const sidebarSections = useMemo(
    () => [
      {
        title: "Conversation Scope",
        description: "These controls apply only to the next message. They are snapshotted when you send.",
        children: (
          <>
            <label className="field">
              <div className="field-heading">
                <span>Retrieval mode</span>
                <HelpHint
                  label="Retrieval mode"
                  text="Hybrid uses the staged router and fetch pipeline. PageIndex uses the agentic tree-navigation loop. This choice only affects the next message you send."
                />
              </div>
              <SelectMenu
                value={retrievalMode}
                disabled={pending}
                options={[
                  { value: "hybrid", label: "hybrid", note: "staged pipeline" },
                  { value: "pageindex", label: "pageindex", note: "agentic tree loop" },
                ]}
                onChange={(value) => setRetrievalMode(value as "hybrid" | "pageindex")}
              />
            </label>
            <label className="field">
              <div className="field-heading">
                <span>Max docs</span>
                <HelpHint
                  label="Max docs"
                  text="This is the base routing width for the next query. Higher values can improve recall on broad questions but usually cost more tokens and latency."
                />
              </div>
              <div className="slider-row">
                <input
                  type="range"
                  min={1}
                  max={15}
                  value={maxDocs}
                  disabled={pending}
                  onChange={(event) => setMaxDocs(Number(event.target.value))}
                />
                <span className="slider-value">{maxDocs}</span>
              </div>
            </label>
            <label className="field">
              <div className="field-heading">
                <span>Reasoning effort</span>
                <HelpHint
                  label="Reasoning effort"
                  text="This asks the model to think less or more deeply during the next answer. Higher effort may improve difficult answers but often increases latency."
                />
              </div>
              <SelectMenu
                value={reasoningEffort}
                disabled={pending}
                options={REASONING_OPTIONS.map((option) => ({ value: option, label: option }))}
                onChange={(value) =>
                  setReasoningEffort(value as (typeof REASONING_OPTIONS)[number])
                }
              />
            </label>
          </>
        ),
      },
      {
        title: "Advanced Retrieval",
        description:
          "Advanced retrieval is explicit and bounded. Toggling it during an in-flight request does nothing to that active query.",
        children: (
          <>
            <label className="toggle-row">
              <input
                type="checkbox"
                checked={settings.enabled}
                disabled={pending}
                onChange={(event) =>
                  setSettings((current) => ({ ...current, enabled: event.target.checked }))
                }
              />
              <span className="toggle-copy">
                Enable advanced retrieval
                <HelpHint
                  label="Enable advanced retrieval"
                  text="Turns on the planner-based retrieval controls below. This does not modify a request that is already running; it only changes the next one."
                />
              </span>
            </label>
            <label className="toggle-row">
              <input
                type="checkbox"
                checked={settings.enable_planning}
                disabled={pending || !settings.enabled}
                onChange={(event) =>
                  setSettings((current) => ({ ...current, enable_planning: event.target.checked }))
                }
              />
              <span className="toggle-copy">
                Query planning
                <HelpHint
                  label="Query planning"
                  text="Lets the system classify the query first and recommend retrieval width. It is useful for broad or ambiguous questions, but adds an extra planning step."
                />
              </span>
            </label>
            <label className="toggle-row">
              <input
                type="checkbox"
                checked={settings.enable_adaptive_width}
                disabled={pending || !settings.enabled}
                onChange={(event) =>
                  setSettings((current) => ({
                    ...current,
                    enable_adaptive_width: event.target.checked,
                  }))
                }
              />
              <span className="toggle-copy">
                Adaptive width
                <HelpHint
                  label="Adaptive width"
                  text="Allows the planner to widen or narrow how many documents and nodes get explored. This can improve broad-question recall but may expand cost."
                />
              </span>
            </label>
            <label className="toggle-row">
              <input
                type="checkbox"
                checked={settings.enable_node_expansion}
                disabled={pending || !settings.enabled}
                onChange={(event) =>
                  setSettings((current) => ({
                    ...current,
                    enable_node_expansion: event.target.checked,
                  }))
                }
              />
              <span className="toggle-copy">
                Node expansion
                <HelpHint
                  label="Node expansion"
                  text="Allows the pipeline to pull nearby structural neighbors when one node looks promising. This can help synthesis, but it may retrieve more context than necessary."
                />
              </span>
            </label>
            <label className="field">
              <div className="field-heading">
                <span>Advanced max docs cap</span>
                <HelpHint
                  label="Advanced max docs cap"
                  text="This is the hard ceiling the planner cannot exceed when widening document routing. It protects latency and cost."
                />
              </div>
              <input
                type="number"
                min={1}
                max={15}
                value={settings.max_docs_cap}
                disabled={pending || !settings.enabled}
                onChange={(event) =>
                  setSettings((current) => ({
                    ...current,
                    max_docs_cap: Number(event.target.value),
                  }))
                }
              />
            </label>
            <label className="field">
              <div className="field-heading">
                <span>Advanced max nodes cap</span>
                <HelpHint
                  label="Advanced max nodes cap"
                  text="This caps how many structural nodes the advanced flow may explore. Raising it can improve difficult synthesis, but it can also increase prompt size."
                />
              </div>
              <input
                type="number"
                min={1}
                max={15}
                value={settings.max_nodes_cap}
                disabled={pending || !settings.enabled}
                onChange={(event) =>
                  setSettings((current) => ({
                    ...current,
                    max_nodes_cap: Number(event.target.value),
                  }))
                }
              />
            </label>
          </>
        ),
      },
    ],
    [maxDocs, pending, reasoningEffort, retrievalMode, settings],
  );

  async function handleSend() {
    if (!draft.trim() || !runtime || pending) {
      return;
    }

    const snappedRequest: QueryRequest = {
      project: runtime.project,
      model: runtime.model,
      user_query: draft.trim(),
      conversation_context: turns,
      max_docs: maxDocs,
      retrieval_mode: retrievalMode,
      advanced_retrieval: { ...settings },
      reasoning_effort: reasoningEffort === "default" ? null : reasoningEffort,
    };

    const question = draft.trim();
    setDraft("");
    setError(null);
    setPending(true);
    setLastRequest(snappedRequest);
    setTurns((current) => [...current, { role: "user", content: question }]);

    try {
      const response = await runQuery(snappedRequest);
      setLastResponse(response);
      setTurns((current) => [...current, { role: "assistant", content: response.answer }]);
      onQueryCompleted?.();
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : "Query failed.";
      setError(message);
      setTurns((current) => [
        ...current,
        { role: "assistant", content: `I couldn't complete that request: ${message}` },
      ]);
    } finally {
      setPending(false);
    }
  }

  const metrics = lastResponse?.metrics;

  return {
    sidebarSections,
    content: (
      <div className="workspace-grid workspace-grid-ask">
        <section className="workspace-card workspace-card-chat">
          <div className="chat-header">
            <div>
              <h3>Assistant</h3>
              <p className="muted">
                The conversation stays central. Retrieval diagnostics live in the inspector rail.
              </p>
            </div>
            {lastRequest ? (
              <span className="pill">
                {lastRequest.retrieval_mode} · max {lastRequest.max_docs} docs
              </span>
            ) : null}
          </div>

          <ChatThread turns={turns} pending={pending} />

          <div className="composer">
            <textarea
              value={draft}
              disabled={pending || !runtime}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey) {
                  event.preventDefault();
                  void handleSend();
                }
              }}
              placeholder="Ask about your indexed documents — Enter to send, Shift+Enter for newline"
            />
            <div className="composer-row">
              <span className="muted">
                {runtime
                  ? "Project and model are global shell context. Retrieval controls are per-message."
                  : "Load a runtime first to start asking questions."}
              </span>
              <button
                type="button"
                disabled={pending || !draft.trim() || !runtime}
                onClick={handleSend}
              >
                {pending ? "Answering…" : "Send"}
              </button>
            </div>
          </div>

          {error ? <div className="notice notice-error">{error}</div> : null}
        </section>

        <InspectorPanel
          title="Latest answer context"
          description="This rail tracks the last completed query, its snapped settings, and the evidence it touched."
        >
          {lastResponse ? (
            <>
              <div className="stat-grid">
                <div className="mini-card">
                  <span>Docs</span>
                  <strong>{lastResponse.selected_docs.length}</strong>
                </div>
                <div className="mini-card">
                  <span>Nodes</span>
                  <strong>{lastResponse.selected_nodes.length}</strong>
                </div>
                <div className="mini-card">
                  <span>Sources</span>
                  <strong>{lastResponse.sources.length}</strong>
                </div>
                <div className="mini-card">
                  <span>Total tokens</span>
                  <strong>{formatMetric(metrics?.total_tokens)}</strong>
                </div>
              </div>

              {lastRequest ? (
                <div className="inspector-block">
                  <h4>Snapped settings</h4>
                  <dl className="detail-stack">
                    <div>
                      <dt>Mode</dt>
                      <dd>{lastRequest.retrieval_mode}</dd>
                    </div>
                    <div>
                      <dt>Max docs</dt>
                      <dd>{lastRequest.max_docs}</dd>
                    </div>
                    <div>
                      <dt>Advanced</dt>
                      <dd>{lastRequest.advanced_retrieval.enabled ? "enabled" : "off"}</dd>
                    </div>
                    <div>
                      <dt>Reasoning</dt>
                      <dd>{lastRequest.reasoning_effort ?? "default"}</dd>
                    </div>
                  </dl>
                </div>
              ) : null}

              <div className="inspector-block">
                <h4>Sources</h4>
                {lastResponse.sources.length > 0 ? (
                  <ul className="bullet-list">
                    {lastResponse.sources.map((source) => (
                      <li key={source.node_ref}>
                        <strong>{source.doc_id}</strong>
                        <span>{source.section || source.node_ref}</span>
                        <small>{source.page_range}</small>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="muted">No explicit sources were returned for this answer.</p>
                )}
              </div>

              <div className="inspector-block">
                <h4>Metrics</h4>
                <dl className="detail-stack">
                  <div>
                    <dt>TTFT</dt>
                    <dd>{formatMetric(metrics?.ttft_seconds, "s")}</dd>
                  </div>
                  <div>
                    <dt>Total time</dt>
                    <dd>{formatMetric(metrics?.total_time_seconds, "s")}</dd>
                  </div>
                  <div>
                    <dt>LLM calls</dt>
                    <dd>{formatMetric(metrics?.llm_calls)}</dd>
                  </div>
                  <div>
                    <dt>Prompt tokens</dt>
                    <dd>{formatMetric(metrics?.prompt_tokens)}</dd>
                  </div>
                </dl>
              </div>
            </>
          ) : (
            <p className="muted">
              No answer yet. The first completed query will populate this rail without pushing metadata
              underneath the composer.
            </p>
          )}
        </InspectorPanel>
      </div>
    ),
  };
}
