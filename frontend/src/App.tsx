import { useEffect, useRef } from "react";

import { Composer } from "./components/Composer";
import { Header } from "./components/Header";
import { Icon } from "./components/Icon";
import { Message } from "./components/Message";
import { Sidebar } from "./components/Sidebar";
import { useCopilot } from "./hooks/useCopilot";

export default function App() {
  const { turns, busy, awaitingApproval, send, respond } = useCopilot();
  const endRef = useRef<HTMLDivElement>(null);

  // Keep the latest message in view as the conversation grows. JS smooth-scroll
  // ignores the CSS reduced-motion override, so gate it on the media query.
  useEffect(() => {
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    endRef.current?.scrollIntoView({ behavior: reduce ? "auto" : "smooth" });
  }, [turns]);

  return (
    <div className="page">
      <div className="layout">
        <Sidebar />
        <div className="shell">
        <Header />

        <main className="chat">
          {turns.length === 0 ? (
            <div className="empty">
              <div className="empty__icon">
                <Icon name="tool" size={28} />
              </div>
              <h2 className="empty__title">Investigate a production incident</h2>
              <p className="empty__text">
                Ask a question and the agent pulls logs &amp; metrics, reads the
                code, finds the root cause, and proposes a fix — pausing for your
                approval before any write action.
              </p>
              <ol className="stepper">
                {["Plan", "Investigate", "Approve", "Diagnose", "Reflect"].map(
                  (s, i) => (
                    <li key={s} className="stepper__item">
                      <span className="stepper__dot">{i + 1}</span>
                      <span className="stepper__label">{s}</span>
                    </li>
                  )
                )}
              </ol>
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

        <Composer disabled={busy || awaitingApproval} onSend={send} />
        </div>
      </div>
    </div>
  );
}
