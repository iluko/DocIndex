type HelpHintProps = {
  label: string;
  text: string;
};

export function HelpHint({ label, text }: HelpHintProps) {
  return (
    <span className="help-hint" tabIndex={0} aria-label={`${label}: ${text}`}>
      <span className="help-hint-trigger">?</span>
      <span className="help-hint-bubble" role="tooltip">
        <strong>{label}</strong>
        <span>{text}</span>
      </span>
    </span>
  );
}
