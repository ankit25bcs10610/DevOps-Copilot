import { useEffect, useRef } from "react";

import { Composer } from "./components/Composer";
import { Header } from "./components/Header";
import { Message } from "./components/Message";
import { useCopilot } from "./hooks/useCopilot";

export default function App() {
  const { turns, busy, error, send, respond } = useCopilot();
  const endRef = useRef<HTMLDivElement>(null);

  // Keep the latest message in view as the conversation grows.
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [turns]);

  return (
    <div className="app">
      <Header />

      <main className="chat">
        {turns.length === 0 && (
          <div className="empty">
            <div className="empty__icon">🛠️</div>
            <h2>Investigate a production incident</h2>
            <p>
              Ask a question and the agent will pull logs &amp; metrics, read the
              code, find the root cause, and propose a fix — pausing for your
              approval before any write action.
            </p>
          </div>
        )}

        {turns.map((turn) => (
          <Message key={turn.id} turn={turn} onDecision={respond} />
        ))}

        {error && <div className="error">⚠️ {error}</div>}
        <div ref={endRef} />
      </main>

      <Composer disabled={busy} onSend={send} />
    </div>
  );
}
