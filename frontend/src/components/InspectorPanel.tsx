type InspectorPanelProps = {
  title: string;
  description: string;
  children: React.ReactNode;
};

export function InspectorPanel({ title, description, children }: InspectorPanelProps) {
  return (
    <aside className="inspector-panel">
      <header>
        <div className="eyebrow">Inspector</div>
        <h3>{title}</h3>
        <p>{description}</p>
      </header>
      <div className="inspector-body">{children}</div>
    </aside>
  );
}
