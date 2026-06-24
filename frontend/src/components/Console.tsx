import { useEffect, useRef, useState } from "react";

import { health } from "../api";
import { useCopilot } from "../hooks/useCopilot";
import { modelShort, providerLabel, useConfig } from "../hooks/useConfig";
import { Composer } from "./Composer";
import { Icon } from "./Icon";
import { Message } from "./Message";
import { Sidebar } from "./Sidebar";
import { ThemePicker } from "./ThemePicker";

const PIPELINE = [
  { n: 1, icon: "clipboard", name: "Plan", sub: "Understand scope" },
  { n: 2, icon: "search", name: "Investigate", sub: "Collect & analyze" },
  { n: 3, icon: "check", name: "Approve", sub: "Review & confirm" },
  { n: 4, icon: "insight", name: "Diagnose", sub: "Find root cause" },
  { n: 5, icon: "refresh", name: "Reflect", sub: "Summarize & learn" },
];

const SUGGESTIONS = [
  "Why is the checkout API throwing 500 errors?",
  "Is the checkout service error rate going up or down?",
  "Which services are emitting logs right now?",
  "Show me the top error logs in the last 15 minutes",
];

const FEATURES = [
  { icon: "sparkles", title: "Context aware", sub: "Understands your stack" },
  { icon: "check", title: "Source linked", sub: "Logs, code & metrics" },
  { icon: "lock", title: "Safe by design", sub: "Approval before writes" },
  { icon: "cpu", title: "Extensible", sub: "MCP-powered tools" },
];

function greeting(): string {
  const h = new Date().getHours();
  if (h < 12) return "Good morning";
  if (h < 18) return "Good afternoon";
  return "Good evening";
}

function TopBar({ onHome }: { onHome: () => void }) {
  const { config: cfg } = useConfig();
  const [online, setOnline] = useState<boolean | null>(null);

  useEffect(() => {
    let active = true;
    const ping = async () => {
      const ok = await health();
      if (active) setOnline(ok);
    };
    ping();
    const id = setInterval(ping, 10_000);
    return () => {
      active = false;
      clearInterval(id);
    };
  }, []);

  const dot = online === null ? "dot dot--idle" : online ? "dot dot--ok" : "dot dot--bad";
  const label = online === null ? "connecting" : online ? "Online" : "Offline";

  return (
    <header className="cns-top">
      <button className="cns-brand" onClick={onHome} title="Back to home">
        <span className="cns-brand__logo">
          <Icon name="tool" size={20} />
        </span>
        <span className="cns-brand__txt">
          <strong>DevOps Copilot</strong>
          <span>Autonomous incident investigation</span>
        </span>
      </button>

      <div className="cns-top__right">
        {cfg && (
          <span className="model-badge" title={cfg.model}>
            <span className="model-badge__provider">{providerLabel(cfg.provider)}</span>
            <span className="model-badge__model">{modelShort(cfg.model)}</span>
          </span>
        )}
        <span className="status">
          <span className={dot} />
          <span className="status__label">{label}</span>
        </span>
        <ThemePicker />
        <span className="cns-avatar">A</span>
      </div>
    </header>
  );
}

export function Console({ onHome }: { onHome: () => void }) {
  const { turns, busy, awaitingApproval, send, respond } = useCopilot();
  const endRef = useRef<HTMLDivElement>(null);
  const disabled = busy || awaitingApproval;

  useEffect(() => {
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    endRef.current?.scrollIntoView({ behavior: reduce ? "auto" : "smooth" });
  }, [turns]);

  const empty = turns.length === 0;

  return (
    <div className="cns">
      <TopBar onHome={onHome} />
      <div className="cns-body">
        <Sidebar />
        <main className="cns-main">
          {empty ? (
            <div className="cns-scroll">
              <div className="welcome">
                <div className="welcome__text">
                  <p className="welcome__greet">{greeting()}, Alex! 👋</p>
                  <h1 className="welcome__title">Investigate a production incident</h1>
                  <p className="welcome__sub">
                    Ask a question and DevOps Copilot will pull logs &amp; metrics, read the code,
                    find the root cause, and propose a fix — pausing for your approval before any
                    write action.
                  </p>
                </div>
                <div className="welcome__art" aria-hidden="true">
                  <Icon name="tool" size={48} />
                </div>
              </div>

              <div className="pipe-card">
                {PIPELINE.map((p, i) => (
                  <div key={p.name} className="pipe-step">
                    <div className="pipe-step__top">
                      <span className="pipe-step__num">{p.n}</span>
                      <span className="pipe-step__name">{p.name}</span>
                      {i < PIPELINE.length - 1 && <span className="pipe-step__line" />}
                    </div>
                    <div className="pipe-step__body">
                      <span className="pipe-step__icon">
                        <Icon name={p.icon} size={16} />
                      </span>
                      <span className="pipe-step__sub">{p.sub}</span>
                    </div>
                  </div>
                ))}
              </div>

              <div className="suggest-card">
                <div className="suggest-card__head">
                  <span>Suggested questions</span>
                </div>
                <div className="suggest-grid">
                  {SUGGESTIONS.map((q) => (
                    <button
                      key={q}
                      className="suggest-chip"
                      disabled={disabled}
                      onClick={() => send(q)}
                    >
                      {q}
                    </button>
                  ))}
                </div>
              </div>

              <Composer disabled={disabled} onSend={send} />

              <div className="feature-row">
                {FEATURES.map((f) => (
                  <div key={f.title} className="feature">
                    <span className="feature__icon">
                      <Icon name={f.icon} size={16} />
                    </span>
                    <span className="feature__txt">
                      <strong>{f.title}</strong>
                      <span>{f.sub}</span>
                    </span>
                  </div>
                ))}
              </div>
            </div>
          ) : (
            <>
              <div className="cns-scroll">
                <div className="thread">
                  {turns.map((turn) => (
                    <Message key={turn.id} turn={turn} onDecision={respond} />
                  ))}
                  <div ref={endRef} />
                </div>
              </div>
              <div className="cns-dock">
                <Composer disabled={disabled} onSend={send} />
              </div>
            </>
          )}
        </main>
      </div>
    </div>
  );
}
