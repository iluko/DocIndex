import { useEffect, useMemo, useState } from "react";
import { Route, Routes, useLocation } from "react-router-dom";

import { fetchModels, fetchProjects, fetchRuntime, fetchTraces } from "./api/client";
import { AppShell } from "./components/AppShell";
import { HelpHint } from "./components/HelpHint";
import { SelectMenu } from "./components/SelectMenu";
import { useAskWorkspace } from "./pages/AskPage";
import { useIngestWorkspace } from "./pages/IngestPage";
import { useInspectWorkspace } from "./pages/InspectPage";
import type { RuntimeSummary, TraceSummary, WorkspaceRenderModel } from "./types";

type WorkspaceDescriptor = {
  title: string;
  subtitle: string;
  sidebarSections: WorkspaceRenderModel["sidebarSections"];
  content: React.ReactNode;
};

export default function App() {
  const location = useLocation();
  const [projects, setProjects] = useState<string[]>([]);
  const [models, setModels] = useState<string[]>([]);
  const [project, setProject] = useState("default");
  const [model, setModel] = useState("");
  const [runtime, setRuntime] = useState<RuntimeSummary | null>(null);
  const [traces, setTraces] = useState<TraceSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [runtimeError, setRuntimeError] = useState<string | null>(null);

  async function refreshProjects(preferredProject?: string) {
    const nextProjects = await fetchProjects();
    setProjects(nextProjects);
    const nextProject =
      preferredProject && nextProjects.includes(preferredProject)
        ? preferredProject
        : nextProjects[0] ?? "default";
    setProject(nextProject);
    return nextProject;
  }

  async function refreshWorkspaceData(nextProject = project, nextModel = model) {
    if (!nextProject) {
      return;
    }

    setLoading(true);
    setRuntimeError(null);
    try {
      const [nextRuntime, nextTraces] = await Promise.all([
        fetchRuntime(nextProject, nextModel || undefined),
        fetchTraces(nextProject, nextModel || undefined),
      ]);
      setRuntime(nextRuntime);
      setTraces(nextTraces);
    } catch (cause) {
      setRuntime(null);
      setTraces([]);
      setRuntimeError(cause instanceof Error ? cause.message : "Failed to load runtime.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void refreshProjects(project);
    void fetchModels().then((items) => {
      setModels(items);
      if (items.length > 0) {
        setModel((current) => (items.includes(current) ? current : items[0]));
      }
    });
  }, []);

  useEffect(() => {
    void refreshWorkspaceData(project, model);
  }, [project, model]);

  const ask = useAskWorkspace({
    runtime,
    onQueryCompleted: () => {
      void refreshWorkspaceData();
    },
  });
  const ingest = useIngestWorkspace({
    runtime,
    onIngestionCompleted: () => {
      void refreshWorkspaceData();
    },
  });
  const inspectWithActions = useInspectWorkspace({
    runtime,
    traces,
    onProjectDeleted: async (deletedProject) => {
      const nextProject = await refreshProjects(
        project === deletedProject ? undefined : project,
      );
      await refreshWorkspaceData(nextProject, model);
    },
  });

  const workspace = useMemo<WorkspaceDescriptor>(() => {
    if (location.pathname.startsWith("/ingest")) {
      return {
        title: "Ingest workspace",
        subtitle: "Upload, configure, and review ingestion in one scoped workflow.",
        sidebarSections: ingest.sidebarSections,
        content: ingest.content,
      };
    }
    if (location.pathname.startsWith("/inspect")) {
      return {
        title: "Inspect workspace",
        subtitle: "Browse documents and traces without polluting the assistant surface.",
        sidebarSections: inspectWithActions.sidebarSections,
        content: inspectWithActions.content,
      };
    }
    return {
      title: "Ask workspace",
      subtitle: "The assistant is primary. Diagnostics stay in a secondary rail instead of under the composer.",
      sidebarSections: ask.sidebarSections,
      content: ask.content,
    };
  }, [
    ask.content,
    ask.sidebarSections,
    ingest.content,
    ingest.sidebarSections,
    inspectWithActions.content,
    inspectWithActions.sidebarSections,
    location.pathname,
  ]);

  const headerActions = (
    <div className="header-controls">
      <label className="field field-inline">
        <div className="field-heading">
          <span>Project</span>
          <HelpHint
            label="Project"
            text="Projects are separate knowledge bases. Switching projects changes which indexed documents, trees, and traces you are working against."
          />
        </div>
        <SelectMenu
          value={project}
          options={projects.map((item) => ({ value: item, label: item }))}
          onChange={setProject}
        />
      </label>
      <label className="field field-inline">
        <div className="field-heading">
          <span>Model</span>
          <HelpHint
            label="Model"
            text="The model controls LLM behavior for ingestion and querying, but it does not change which documents belong to the current project."
          />
        </div>
        <SelectMenu
          value={model}
          options={models.map((item) => ({ value: item, label: item }))}
          onChange={setModel}
        />
      </label>
      <div className="header-status">
        {loading ? <span className="pill pill-neutral">Loading runtime…</span> : null}
        {runtimeError ? <span className="pill pill-danger">Runtime error</span> : null}
      </div>
    </div>
  );

  return (
    <AppShell
      title={workspace.title}
      subtitle={workspace.subtitle}
      project={runtime?.project ?? project}
      model={runtime?.model ?? model}
      sidebarSections={workspace.sidebarSections}
      headerActions={headerActions}
    >
      {runtimeError ? <div className="notice notice-error">{runtimeError}</div> : null}

      <Routes>
        <Route path="/" element={workspace.content} />
        <Route path="/ingest" element={workspace.content} />
        <Route path="/inspect" element={workspace.content} />
      </Routes>
    </AppShell>
  );
}
