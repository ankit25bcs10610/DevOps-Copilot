import { useEffect, useRef } from "react";

import { Composer } from "./components/Composer";
import { Header } from "./components/Header";
import { Message } from "./components/Message";
import { useCopilot } from "./hooks/useCopilot";

export default function App() {
  const { turns, busy, send, respond } = useCopilot();
  const endRef = useRef<HTMLDivElement>(null);

  // Keep the latest message in view as the conversation grows.
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [turns]);

  return (
    <div className="page">
      <div className="shell">
        <Header />

        <main className="chat">
          {turns.length === 0 ? (
            <div className="empty">
              <div className="empty__icon">🛠️</div>
              <h2 className="empty__title">Investigate a production incident</h2>
              <p className="empty__text">
                Ask a question and the agent pulls logs &amp; metrics, reads the
                code, finds the root cause, and proposes a fix — pausing for your
                approval before any write action.
              </p>
              <div className="empty__steps">
                <span>📋 Plan</span>
                <span>🔧 Investigate</span>
                <span>🧠 Diagnose</span>
                <span>⏸ Approve</span>
              </div>
            </div>
          ) : (
            <div className="thread">
              {turns.map((turn) => (
                <Message key={turn.id} turn={turn} onDecision={respond} />
              ))}
              <div ref={endRef} />
            </div>
          )}
        </main>

        <Composer disabled={busy} onSend={send} />
      </div>
    </div>
  );
}
