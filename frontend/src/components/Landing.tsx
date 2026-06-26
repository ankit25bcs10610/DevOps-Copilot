import "./landing.css";

import { useEffect, useState } from "react";

import { getMetrics } from "../api";
import { modelShort, providerLabel, useConfig } from "../hooks/useConfig";
import { Icon } from "./Icon";

const PIPELINE = [
  { icon: "clipboard", name: "Plan", desc: "Decompose the incident into an investigation plan." },
  { icon: "search", name: "Investigate", desc: "Pull logs, metrics, traces, k8s & recent deploys." },
  { icon: "pause", name: "Approve", desc: "Human-in-the-loop gate before any write action." },
  { icon: "insight", name: "Diagnose", desc: "Pinpoint the root cause and propose the fix." },
  { icon: "refresh", name: "Reflect", desc: "Verify completeness, loop or finish." },
  { icon: "download", name: "Report", desc: "Deliver a structured RCA + postmortem." },
];

const PILLARS = [
  {
    icon: "search",
    name: "Autonomous investigation",
    tag: "From alert to root cause — hands-off",
    items: [
      { icon: "insight", title: "Root-cause reports", sub: "Ranked hypotheses + postmortem" },
      { icon: "server", title: "9 MCP tool servers", sub: "Logs · traces · k8s · sentry · deploys" },
      { icon: "send", title: "Triggered → Slack", sub: "PagerDuty auto-investigate + approve" },
      { icon: "refresh", title: "Deterministic evals", sub: "Record/replay golden gate" },
    ],
  },
  {
    icon: "lock",
    name: "Safe by design",
    tag: "Guardrails before any write",
    items: [
      { icon: "pause", title: "Risk-tiered approval", sub: "Human-in-the-loop before writes" },
      { icon: "alert", title: "Injection + PII guardrails", sub: "Untrusted telemetry scrubbed" },
      { icon: "cpu", title: "Token budget", sub: "Per-investigation cost kill-switch" },
      { icon: "check", title: "Tamper-evident audit", sub: "Hash-chained, verifiable trail" },
    ],
  },
  {
    icon: "branch",
    name: "Enterprise SaaS",
    tag: "Multi-tenant from day one",
    items: [
      { icon: "branch", title: "Multi-tenant + RBAC", sub: "Orgs · roles · scoped API keys" },
      { icon: "database", title: "Usage metering & billing", sub: "Plan quotas · Stripe sync" },
      { icon: "tool", title: "SSO login", sub: "Supabase / OIDC JWT" },
      { icon: "sparkles", title: "Datadog APM + LLMObs", sub: "Trace the agent itself" },
    ],
  },
];

function Sparkline({ data, color }: { data: number[]; color: string }) {
  const w = 120;
  const h = 36;
  const max = Math.max(...data);
  const min = Math.min(...data);
  const pts = data
    .map((v, i) => {
      const x = (i / (data.length - 1)) * w;
      const y = h - ((v - min) / (max - min || 1)) * h;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
  return (
    <svg className="spark" viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" aria-hidden="true">
      <polyline points={pts} fill="none" stroke={color} strokeWidth="2" />
    </svg>
  );
}

// 30 points of 5xx error rate (%) over the last 30 min — flat, then the incident spike.
const ERR_SERIES = [
  1, 2, 1, 2, 3, 2, 1, 2, 4, 3, 2, 4, 5, 4, 7, 11, 17, 26, 36, 47, 57, 65, 70, 71, 70, 68, 66,
  64, 63, 62,
];

/** Hand-built SVG area chart — error rate over time (Trend Over Time → Area Chart). */
function ErrorChart() {
  // Real checkout 5xx series from /metrics (fractions → %); demo curve as fallback.
  const [data, setData] = useState<number[]>(ERR_SERIES);
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
  const W = 600;
  const H = 200;
  const n = data.length;
  const incident = Math.min(14, Math.max(0, n - 2)); // marker, clamped to series length
  const px = (i: number) => (n > 1 ? (i / (n - 1)) * W : 0);
  const py = (v: number) => H - (v / 100) * H;
  const line = data.map((v, i) => `${px(i).toFixed(1)},${py(v).toFixed(1)}`).join(" ");
  const area = `0,${H} ${line} ${W},${H}`;
  return (
    <article className="card card--chart">
      <div className="card__head">
        <Icon name="insight" size={16} />
        <span>5xx error rate · last 30 min</span>
        <span className="badge badge--bad">71% ↑</span>
      </div>
      <div className="chart">
        <div className="chart__yax" aria-hidden="true">
          <span>75%</span>
          <span>50%</span>
          <span>25%</span>
          <span>0%</span>
        </div>
        <div className="chart__plot">
          <svg
            className="chart__svg"
            viewBox={`0 0 ${W} ${H}`}
            preserveAspectRatio="none"
            role="img"
            aria-label="Area chart of checkout-svc 5xx error rate over the last 30 minutes, spiking to about 71% when the incident was detected."
          >
            <defs>
              <linearGradient id="err-fill" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="#ff6b6b" stopOpacity="0.45" />
                <stop offset="100%" stopColor="#ff6b6b" stopOpacity="0" />
              </linearGradient>
            </defs>
            <line className="chart__grid" x1="0" y1={H * 0.25} x2={W} y2={H * 0.25} />
            <line className="chart__grid" x1="0" y1={H * 0.5} x2={W} y2={H * 0.5} />
            <line className="chart__grid" x1="0" y1={H * 0.75} x2={W} y2={H * 0.75} />
            <line className="chart__marker" x1={px(incident)} y1="0" x2={px(incident)} y2={H} />
            <polygon className="chart__area" points={area} fill="url(#err-fill)" />
            <polyline className="chart__line" points={line} />
          </svg>
          <span className="chart__flag" style={{ left: `${(incident / (n - 1)) * 100}%` }}>
            incident detected
          </span>
        </div>
        <div className="chart__xax" aria-hidden="true">
          <span>-30m</span>
          <span>-20m</span>
          <span>-10m</span>
          <span>now</span>
        </div>
      </div>
    </article>
  );
}

export function Landing({ onLaunch }: { onLaunch: () => void }) {
  const { config } = useConfig();
  const serverCount = config?.servers.length ?? 3;
  const model = config ? modelShort(config.model) : "Opus 4.8";
  const provider = config ? providerLabel(config.provider) : "Anthropic";

  return (
    <div className="landing">
      {/* ---- Nav ---- */}
      <nav className="lnav">
        <div className="lnav__brand">
          <span className="lnav__logo">
            <Icon name="tool" size={18} />
          </span>
          <span>DevOps Copilot</span>
        </div>
        <div className="lnav__links">
          <a href="#features">Platform</a>
          <a href="#capabilities">Capabilities</a>
          <a href="#how">How it works</a>
          <a href="#stack">Stack</a>
        </div>
        <button className="btn3d btn3d--primary lnav__cta" onClick={onLaunch}>
          Launch Console
        </button>
      </nav>

      {/* ---- Hero ---- */}
      <section className="hero">
        <div className="hero__overlay">
          <span className="hero__eyebrow">
            <span className="pulse-dot" /> Autonomous AI Operations · LangGraph · MCP
          </span>
          <h1 className="hero__title">
            AI INCIDENT
            <br />
            <span className="grad-text">COMMAND CENTER</span>
          </h1>
          <p className="hero__subtitle">
            Autonomous root cause analysis powered by advanced AI agents.
          </p>
          <div className="hero__cta">
            <button className="btn3d btn3d--primary" onClick={onLaunch}>
              Launch Console <Icon name="send" size={15} />
            </button>
            <a className="btn3d btn3d--ghost" href="#features">
              Explore the platform
            </a>
          </div>
          <div className="hero-metrics">
            <div className="hmetric">
              <span className="hmetric__viz">
                <svg className="hmini-mesh" viewBox="0 0 64 22" aria-hidden="true">
                  <line x1="11" y1="11" x2="32" y2="11" />
                  <line x1="32" y1="11" x2="53" y2="11" />
                  <circle cx="11" cy="11" r="4" />
                  <circle cx="32" cy="11" r="4" />
                  <circle cx="53" cy="11" r="4" />
                </svg>
              </span>
              <strong className="hmetric__val">{serverCount}</strong>
              <span className="hmetric__label">MCP servers</span>
            </div>
            <div className="hmetric">
              <span className="hmetric__viz">
                <span className="hmini-steps" aria-hidden="true">
                  <i className="on" />
                  <i className="on" />
                  <i className="on" />
                  <i className="on" />
                  <i className="on" />
                  <i className="live" />
                </span>
              </span>
              <strong className="hmetric__val">6-stage</strong>
              <span className="hmetric__label">agent pipeline</span>
            </div>
            <div className="hmetric">
              <span className="hmetric__viz">
                <svg className="hmini-spark" viewBox="0 0 64 22" preserveAspectRatio="none" aria-hidden="true">
                  <polyline points="0,4 12,7 22,5 32,11 42,13 54,18 64,19" />
                </svg>
              </span>
              <strong className="hmetric__val">&lt; 30s</strong>
              <span className="hmetric__label">to root cause</span>
            </div>
          </div>
        </div>
        <div className="hero__hint">scroll to explore</div>
      </section>

      {/* ---- Stats band ---- */}
      <section className="lstats" aria-label="Platform at a glance">
        {[
          { value: "9", label: "MCP tool servers" },
          { value: "47", label: "agent tools" },
          { value: "5", label: "LLM providers" },
          { value: "188", label: "tests passing" },
        ].map((s) => (
          <div key={s.label} className="lstat">
            <span className="lstat__num grad-text">{s.value}</span>
            <span className="lstat__label">{s.label}</span>
          </div>
        ))}
      </section>

      {/* ---- Bento dashboard ---- */}
      <section id="features" className="bento-wrap">
        <div className="section-head">
          <span className="section-eyebrow">Live operations surface</span>
          <h2>One pane of glass for every incident</h2>
        </div>

        <div className="bento">
          {/* System health */}
          <article className="card card--health">
            <div className="card__head">
              <Icon name="cpu" size={16} />
              <span>System Health</span>
              <span className="badge badge--ok">98.6%</span>
            </div>
            <div className="health-rows">
              {[
                ["gateway", 99, "ok"],
                ["checkout-svc", 71, "bad"],
                ["inventory-svc", 100, "ok"],
                ["payments-svc", 96, "warn"],
              ].map(([name, val, st]) => (
                <div key={name as string} className="health-row">
                  <span className={`sdot sdot--${st}`} />
                  <span className="health-row__name">{name}</span>
                  <span className="bar">
                    <span
                      className={`bar__fill bar__fill--${st}`}
                      style={{ width: `${val}%` }}
                    />
                  </span>
                  <span className="health-row__val">{val}%</span>
                </div>
              ))}
            </div>
          </article>

          {/* Active incident */}
          <article className="card card--incident">
            <div className="card__head">
              <Icon name="alert" size={16} />
              <span>Active Incident</span>
              <span className="badge badge--bad">P1 · live</span>
            </div>
            <div className="incident">
              <div className="incident__svc">checkout-svc</div>
              <div className="incident__msg">
                <code>TypeError: cannot read 'total' of undefined</code>
              </div>
              <div className="incident__meta">
                <span>500 errors ↑ 71%</span>
                <span>·</span>
                <span>applyDiscount() · checkout.js:11</span>
              </div>
            </div>
          </article>

          {/* AI timeline */}
          <article className="card card--timeline">
            <div className="card__head">
              <Icon name="bot" size={16} />
              <span>AI Investigation</span>
              <span className="badge badge--accent">running</span>
            </div>
            <ul className="aitimeline">
              {PIPELINE.map((p, i) => (
                <li key={p.name} className={i < 3 ? "done" : i === 3 ? "active" : ""}>
                  <span className="aitimeline__icon">
                    <Icon name={i < 3 ? "check" : p.icon} size={12} />
                  </span>
                  {p.name}
                </li>
              ))}
            </ul>
          </article>

          {/* Metrics */}
          <article className="card card--metric">
            <div className="card__head">
              <Icon name="database" size={16} />
              <span>Error rate · 5xx</span>
            </div>
            <div className="metric__big grad-text">0.71</div>
            <Sparkline data={[0.0, 0.01, 0.0, 0.02, 0.05, 0.3, 0.55, 0.71]} color="#00D4FF" />
            <div className="metric__foot">p95 142ms · 118 rpm</div>
          </article>

          {/* Repo analysis */}
          <article className="card card--repo">
            <div className="card__head">
              <Icon name="folder" size={16} />
              <span>Repository Analysis</span>
            </div>
            <pre className="diff">
              <code>
                <span className="diff__file">checkout.js</span>
                {"\n"}
                <span className="diff__del">- const pct = coupon.total;</span>
                {"\n"}
                <span className="diff__add">+ const pct = coupon?.total ?? 0;</span>
              </code>
            </pre>
            <div className="repo__foot">
              <Icon name="branch" size={13} /> fix/apply-discount-null-guard
            </div>
          </article>

          {/* Topology */}
          <article className="card card--topo">
            <div className="card__head">
              <Icon name="branch" size={16} />
              <span>Service Topology</span>
              <span className="topo__legend">
                <span>
                  <span className="sdot sdot--ok" /> healthy
                </span>
                <span>
                  <span className="sdot sdot--bad" /> degraded
                </span>
              </span>
            </div>
            <svg
              className="topo"
              viewBox="0 0 680 170"
              preserveAspectRatio="xMidYMid meet"
              role="img"
              aria-label="Service topology: the API gateway routes to checkout-svc (currently degraded), which depends on inventory, payments, and the database."
            >
              {/* base edges */}
              <line x1="90" y1="85" x2="260" y2="85" />
              <line x1="260" y1="85" x2="450" y2="45" />
              <line x1="260" y1="85" x2="450" y2="125" />
              <line x1="450" y1="45" x2="610" y2="85" />
              <line x1="450" y1="125" x2="610" y2="85" />
              {/* animated traffic on the healthy edges */}
              <line className="topo__flow" x1="90" y1="85" x2="260" y2="85" />
              <line className="topo__flow" x1="450" y1="45" x2="610" y2="85" />
              <line className="topo__flow" x1="450" y1="125" x2="610" y2="85" />
              {/* nodes */}
              <g className="topo__node">
                <circle cx="90" cy="85" r="16" />
                <text x="90" y="123">gateway</text>
              </g>
              <g className="topo__node topo__node--bad">
                <circle cx="260" cy="85" r="19" />
                <text x="260" y="126">checkout</text>
              </g>
              <g className="topo__node">
                <circle cx="450" cy="45" r="14" />
                <text x="450" y="23">inventory</text>
              </g>
              <g className="topo__node">
                <circle cx="450" cy="125" r="14" />
                <text x="450" y="159">payments</text>
              </g>
              <g className="topo__node">
                <circle cx="610" cy="85" r="15" />
                <text x="610" y="122">database</text>
              </g>
            </svg>
          </article>

          <ErrorChart />
        </div>
      </section>

      {/* ---- How it works ---- */}
      <section id="how" className="how">
        <div className="section-head">
          <span className="section-eyebrow">The agent loop</span>
          <h2>How autonomous investigation works</h2>
          <p className="section-sub">
            A LangGraph state machine drives Claude across MCP tool servers — pausing for your
            approval before it ever writes.
          </p>
        </div>
        <div className="how__flow">
          {PIPELINE.map((p, i) => (
            <div key={p.name} className="how__step">
              <span className="how__num">{i + 1}</span>
              <span className="how__icon">
                <Icon name={p.icon} size={20} />
              </span>
              <h3>{p.name}</h3>
              <p>{p.desc}</p>
            </div>
          ))}
        </div>
      </section>

      {/* ---- Capabilities ---- */}
      <section id="capabilities" className="caps">
        <div className="section-head">
          <span className="section-eyebrow">Enterprise-ready</span>
          <h2>Everything you need to run it in production</h2>
          <p className="section-sub">
            From single-tenant demo to multi-tenant SaaS — approval, guardrails, audit, metering,
            SSO and observability are built in, not bolted on.
          </p>
        </div>
        <div className="pillars">
          {PILLARS.map((p) => (
            <article key={p.name} className="pillar">
              <header className="pillar__head">
                <span className="pillar__icon">
                  <Icon name={p.icon} size={20} />
                </span>
                <div className="pillar__heading">
                  <h3>{p.name}</h3>
                  <p>{p.tag}</p>
                </div>
              </header>
              <ul className="pillar__list">
                {p.items.map((f) => (
                  <li key={f.title}>
                    <span className="pillar__bullet">
                      <Icon name={f.icon} size={15} />
                    </span>
                    <span className="pillar__txt">
                      <strong>{f.title}</strong>
                      <span>{f.sub}</span>
                    </span>
                  </li>
                ))}
              </ul>
            </article>
          ))}
        </div>
      </section>

      {/* ---- Stack ---- */}
      <section id="stack" className="stack">
        <div className="section-head">
          <span className="section-eyebrow">Powered by</span>
          <h2>Production-grade agent infrastructure</h2>
        </div>
        <div className="stack__grid">
          {[
            { icon: "branch", label: "LangGraph", sub: "Agent orchestration" },
            { icon: "server", label: "Model Context Protocol", sub: "Tool server protocol" },
            { icon: "sparkles", label: provider, sub: "LLM provider" },
            { icon: "bot", label: model, sub: "Reasoning model" },
            { icon: "send", label: "FastAPI", sub: "API & streaming" },
            { icon: "cpu", label: "React + R3F", sub: "3D console UI" },
          ].map((t) => (
            <div key={t.label} className="tech">
              <span className="tech__icon">
                <Icon name={t.icon} size={18} />
              </span>
              <span className="tech__txt">
                <strong>{t.label}</strong>
                <span>{t.sub}</span>
              </span>
            </div>
          ))}
        </div>
        <div className="stack__cta">
          <button className="btn3d btn3d--primary btn3d--lg" onClick={onLaunch}>
            Launch the console <Icon name="send" size={16} />
          </button>
        </div>
      </section>

      <footer className="lfoot">
        <span>
          DevOps Copilot · built by{" "}
          <a href="https://github.com/ankit25bcs10610" target="_blank" rel="noreferrer">
            Ankit Pandey
          </a>
        </span>
        <span>AI Incident Command Center · LangGraph · MCP · {provider}</span>
      </footer>
    </div>
  );
}
