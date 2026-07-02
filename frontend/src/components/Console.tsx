import { useEffect, useRef, useState } from "react";

import { getMetrics, health } from "../api";
import { useCopilot } from "../hooks/useCopilot";
import { modelShort, providerLabel, useConfig } from "../hooks/useConfig";
import { useMe } from "../hooks/useMe";
import { AdminPanel } from "./AdminPanel";
import { SignupModal } from "./SignupModal";
import { Composer } from "./Composer";
import { Icon } from "./Icon";
import { Message } from "./Message";
import { Sidebar } from "./Sidebar";
import { ThemePicker } from "./ThemePicker";

const PIPELINE = [
  { n: 1, icon: "clipboard", name: "Plan", sub: "Scope + prior incidents" },
  { n: 2, icon: "search", name: "Investigate", sub: "Logs · traces · k8s · deploys" },
  { n: 3, icon: "check", name: "Approve", sub: "Risk-tiered review" },
  { n: 4, icon: "insight", name: "Diagnose", sub: "Find root cause" },
  { n: 5, icon: "refresh", name: "Reflect", sub: "Close the gaps" },
  { n: 6, icon: "download", name: "Report", sub: "RCA + postmortem" },
];

const SUGGESTIONS = [
  "Why is the checkout API throwing 500 errors?",
  "Are any checkout-svc pods crashing in Kubernetes?",
  "What recent deploy most likely caused the checkout failures?",
  "Have we seen this checkout incident before?",
];

const FEATURES = [
  { icon: "insight", title: "Root-cause reports", sub: "Ranked hypotheses + postmortem" },
  { icon: "server", title: "9 MCP tool servers", sub: "Logs · traces · k8s · sentry · deploys" },
  { icon: "lock", title: "Risk-tiered approval", sub: "Human-in-the-loop before writes" },
  { icon: "alert", title: "Injection + PII guardrails", sub: "Untrusted telemetry scrubbed" },
  { icon: "cpu", title: "Token budget", sub: "Per-investigation cost kill-switch" },
  { icon: "branch", title: "Multi-tenant + RBAC", sub: "Orgs · roles · scoped API keys" },
  { icon: "database", title: "Usage metering & billing", sub: "Plan quotas · Stripe sync" },
  { icon: "check", title: "Tamper-evident audit", sub: "Hash-chained, verifiable trail" },
  { icon: "tool", title: "SSO login", sub: "Supabase / OIDC JWT" },
  { icon: "refresh", title: "Deterministic evals", sub: "Record/replay golden gate" },
  { icon: "send", title: "Triggered → Slack", sub: "PagerDuty auto-investigate + approve" },
  { icon: "sparkles", title: "Datadog APM + LLMObs", sub: "Trace the agent itself" },
];

const STATS = [
  { icon: "server", value: "9", label: "MCP tool servers" },
  { icon: "cpu", value: "47", label: "agent tools" },
  { icon: "sparkles", value: "5", label: "LLM providers" },
  { icon: "check", value: "188", label: "tests passing" },
];

function greeting(): string {
  const h = new Date().getHours();
  if (h < 12) return "Good morning";
  if (h < 18) return "Good afternoon";
  return "Good evening";
}

/** Compact sparkline of the sample environment's checkout 5xx spike — the very
 *  incident the suggested questions ask about. Honest demo signal, not live. */
function IncidentSignal() {
  // Real checkout 5xx series from /metrics (fractions → %); demo curve as fallback.
  const [data, setData] = useState<number[]>([2, 1, 3, 2, 4, 3, 6, 11, 22, 40, 60, 71]);
  useEffect(() => {
    let active = true;
    getMetrics()
      .then((m) => {
        const s = m.services?.["checkout-svc"]?.error_rate_5xx;
        if (active && s && s.length) setData(s.map((p) => Math.round((p.value ?? 0) * 100)));
      })
      .catch(() => {});
    return () => {
      active = false;
    };
  }, []);
  const W = 132;
  const H = 46;
  const n = data.length;
  const max = 75;
  const px = (i: number) => (i / (n - 1)) * W;
  const py = (v: number) => H - (v / max) * H;
  const line = data.map((v, i) => `${px(i).toFixed(1)},${py(v).toFixed(1)}`).join(" ");
  const area = `0,${H} ${line} ${W},${H}`;
  const last = Math.round(data[data.length - 1] ?? 0);
  return (
    <div
      className="signal"
      role="img"
      aria-label={`Sample incident signal: checkout-svc 5xx error rate trending up to about ${last}%`}
    >
      <div className="signal__head">
        <span className="signal__dot" />
        <span className="signal__svc">checkout-svc</span>
        <span className="signal__tag">5xx ↑71%</span>
      </div>
      <svg className="signal__spark" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none">
        <defs>
          <linearGradient id="sig-fill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#ff6b6b" stopOpacity="0.4" />
            <stop offset="100%" stopColor="#ff6b6b" stopOpacity="0" />
          </linearGradient>
        </defs>
        <polygon points={area} fill="url(#sig-fill)" />
        <polyline points={line} className="signal__line" />
      </svg>
      <div className="signal__foot">sample incident signal</div>
    </div>
  );
}

function TopBar({
  onHome,
  onNew,
  onAdmin,
  onSignup,
}: {
  onHome: () => void;
  onNew: () => void;
  onAdmin: () => void;
  onSignup: () => void;
}) {
  const { config: cfg } = useConfig();
  const me = useMe();
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
        <button
          className="cns-newbtn"
          onClick={onNew}
          title="Start a new conversation"
          aria-label="Start a new conversation"
        >
          <Icon name="refresh" size={15} />
          <span>New</span>
        </button>
        <button
          className="cns-newbtn"
          onClick={onSignup}
          title="Create an organization (self-serve)"
          aria-label="Create an account"
        >
          <Icon name="sparkles" size={15} />
          <span>Sign up</span>
        </button>
        <button
          className="cns-newbtn"
          onClick={onAdmin}
          title="Admin console (multi-tenant)"
          aria-label="Open admin console"
        >
          <Icon name="lock" size={15} />
          <span>Admin</span>
        </button>
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
        <span className="cns-avatar" role="img" aria-label={`Account: ${me.label}`}>
          {me.label.charAt(0).toUpperCase()}
        </span>
      </div>
    </header>
  );
}

export function Console({ onHome }: { onHome: () => void }) {
  const { turns, busy, awaitingApproval, send, respond, stop, newConversation, sendFeedback } =
    useCopilot();
  const endRef = useRef<HTMLDivElement>(null);
  const me = useMe();
  const [adminOpen, setAdminOpen] = useState(false);
  const [signupOpen, setSignupOpen] = useState(false);
  const disabled = busy || awaitingApproval;

  useEffect(() => {
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    endRef.current?.scrollIntoView({ behavior: reduce ? "auto" : "smooth" });
  }, [turns]);

  const empty = turns.length === 0;

  // Concise screen-reader status — the visible activity trace isn't announced live.
  const liveStatus = awaitingApproval
    ? "The agent is waiting for your approval."
    : busy
      ? "Investigating, please wait…"
      : "";

  return (
    <div className="cns">
      <a className="skip-link" href="#cns-main">Skip to main content</a>
      <TopBar
        onHome={onHome}
        onNew={newConversation}
        onAdmin={() => setAdminOpen(true)}
        onSignup={() => setSignupOpen(true)}
      />
      {adminOpen && <AdminPanel onClose={() => setAdminOpen(false)} />}
      {signupOpen && <SignupModal onClose={() => setSignupOpen(false)} />}
      <div className="sr-only" role="status" aria-live="polite">
        {liveStatus}
      </div>
      <div className="cns-body">
        <Sidebar />
        <main className="cns-main" id="cns-main">
          {empty ? (
            <div className="cns-scroll">
              <div className="welcome">
                <div className="welcome__text">
                  <p className="welcome__greet">{greeting()}, {me.label}!</p>
                  <h1 className="welcome__title">Investigate a production incident</h1>
                  <p className="welcome__sub">
                    Ask a question and DevOps Copilot will pull logs, metrics, traces, Kubernetes
                    and recent deploys, read the code, correlate the change, and deliver a
                    structured root-cause report — pausing for your approval before any write.
                  </p>
                </div>
                <IncidentSignal />
              </div>

              <div className="trust-bar">
                <span className="trust-pill"><Icon name="lock" size={13} /> Human-in-the-loop approval</span>
                <span className="trust-pill"><Icon name="branch" size={13} /> Multi-tenant + RBAC</span>
                <span className="trust-pill"><Icon name="check" size={13} /> Tamper-evident audit</span>
                <span className="trust-pill"><Icon name="server" size={13} /> Self-hostable</span>
              </div>

              <div className="stat-strip">
                {STATS.map((s) => (
                  <div key={s.label} className="stat">
                    <span className="stat__icon">
                      <Icon name={s.icon} size={16} />
                    </span>
                    <span className="stat__num">{s.value}</span>
                    <span className="stat__label">{s.label}</span>
                  </div>
                ))}
              </div>

              <h2 className="feature-head">How it works</h2>
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

              <h2 className="feature-head">Built-in capabilities</h2>
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

              <div className="suggest-card">
                <h2 className="suggest-card__head">Suggested questions</h2>
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

              <Composer disabled={disabled} onSend={send} busy={busy} onStop={stop} />
            </div>
          ) : (
            <>
              <div className="cns-scroll">
                <div className="thread">
                  {turns.map((turn, i) => (
                    <Message
                      key={turn.id}
                      turn={turn}
                      onDecision={respond}
                      onRetry={
                        turn.status === "error" && !disabled && turns[i - 1]?.role === "user"
                          ? () => send(turns[i - 1].text)
                          : undefined
                      }
                      onFeedback={
                        turn.role === "assistant"
                          ? (rating) => sendFeedback(rating, turns[i - 1]?.text ?? "")
                          : undefined
                      }
                    />
                  ))}
                  <div ref={endRef} />
                </div>
              </div>
              <div className="cns-dock">
                <Composer disabled={disabled} onSend={send} busy={busy} onStop={stop} />
              </div>
            </>
          )}
        </main>
      </div>
    </div>
  );
}
