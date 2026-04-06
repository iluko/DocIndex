type ChatTurn = {
  role: "user" | "assistant";
  content: string;
};

type ChatThreadProps = {
  turns: ChatTurn[];
  pending?: boolean;
};

export function ChatThread({ turns, pending = false }: ChatThreadProps) {
  return (
    <div className="chat-thread">
      {turns.length === 0 ? (
        <div className="empty-state">
          <h3>Start with a real question</h3>
          <p>
            The new Ask workspace keeps the conversation central and pushes routing diagnostics into a
            secondary inspector instead of dumping them below the composer.
          </p>
        </div>
      ) : null}

      {turns.map((turn, index) => (
        <article className={`chat-bubble chat-bubble-${turn.role}`} key={`${turn.role}-${index}`}>
          <div className="chat-role">{turn.role === "user" ? "You" : "Assistant"}</div>
          <div className="chat-content">{turn.content}</div>
        </article>
      ))}

      {pending ? <div className="chat-pending">Answering with snapped settings…</div> : null}
    </div>
  );
}
