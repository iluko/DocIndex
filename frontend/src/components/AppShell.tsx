import { NavLink } from "react-router-dom";

import type { WorkspaceSidebarSection } from "../types";

type AppShellProps = {
  title: string;
  subtitle: string;
  project: string;
  model: string;
  children: React.ReactNode;
  sidebarSections: WorkspaceSidebarSection[];
  headerActions?: React.ReactNode;
};

export function AppShell(props: AppShellProps) {
  const { title, subtitle, project, model, children, sidebarSections, headerActions } = props;

  return (
    <div className="app-shell">
      <aside className="app-sidebar">
        <div className="app-brand">
          <div className="eyebrow">Hybrid Approach</div>
          <h1>Hybrid UI</h1>
          <p>React frontend migration over the existing Python runtime.</p>
        </div>

        <nav className="workspace-nav" aria-label="Primary">
          <NavLink to="/" end className="workspace-link">
            Ask
          </NavLink>
          <NavLink to="/ingest" className="workspace-link">
            Ingest
          </NavLink>
          <NavLink to="/inspect" className="workspace-link">
            Inspect
          </NavLink>
        </nav>

        <div className="runtime-chip-stack">
          <div className="runtime-chip">
            <span>Project</span>
            <strong>{project}</strong>
          </div>
          <div className="runtime-chip">
            <span>Model</span>
            <strong>{model}</strong>
          </div>
        </div>

        <div className="context-panel">
          {sidebarSections.map((section) => (
            <section className="context-section" key={section.title}>
              <header>
                <h3>{section.title}</h3>
                {section.description ? <p>{section.description}</p> : null}
              </header>
              <div className="context-content">{section.children}</div>
            </section>
          ))}
        </div>
      </aside>

      <main className="app-main">
        <header className="page-header">
          <div>
            <div className="eyebrow">Workspace</div>
            <h2>{title}</h2>
            <p>{subtitle}</p>
          </div>
          <div className="header-actions">{headerActions}</div>
        </header>
        <section className="page-body">{children}</section>
      </main>
    </div>
  );
}
