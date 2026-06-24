import "./landing.css";

import { modelShort, providerLabel, useConfig } from "../hooks/useConfig";
import { Icon } from "./Icon";

const PIPELINE = [
  { icon: "clipboard", name: "Plan", desc: "Decompose the incident into an investigation plan." },
  { icon: "search", name: "Investigate", desc: "Pull logs & metrics, read code, inspect git history." },
  { icon: "pause", name: "Approve", desc: "Human-in-the-loop gate before any write action." },
  { icon: "insight", name: "Diagnose", desc: "Pinpoint the root cause and propose the fix." },
  { icon: "refresh", name: "Reflect", desc: "Verify completeness, loop or finish." },
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
    <svg className="spark" viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none">
      <polyline points={pts} fill="none" stroke={color} strokeWidth="2" />
    </svg>
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
          <div className="hero__stats">
            <div>
              <strong>{serverCount}</strong>
              <span>MCP servers</span>
            </div>
            <div>
              <strong>5-stage</strong>
              <span>agent pipeline</span>
            </div>
            <div>
              <strong>&lt; 30s</strong>
              <span>to root cause</span>
            </div>
          </div>
        </div>
        <div className="hero__hint">scroll to explore</div>
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
            </div>
            <svg className="topo" viewBox="0 0 240 120">
              <line x1="40" y1="60" x2="120" y2="60" />
              <line x1="120" y1="60" x2="200" y2="30" />
              <line x1="120" y1="60" x2="200" y2="90" />
              <g className="topo__node">
                <circle cx="40" cy="60" r="14" />
                <text x="40" y="92">gateway</text>
              </g>
              <g className="topo__node topo__node--bad">
                <circle cx="120" cy="60" r="16" />
                <text x="120" y="94">checkout</text>
              </g>
              <g className="topo__node">
                <circle cx="200" cy="30" r="12" />
                <text x="200" y="16">inventory</text>
              </g>
              <g className="topo__node">
                <circle cx="200" cy="90" r="12" />
                <text x="200" y="112">payments</text>
              </g>
            </svg>
          </article>
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

      {/* ---- Stack ---- */}
      <section id="stack" className="stack">
        <div className="section-head">
          <span className="section-eyebrow">Powered by</span>
          <h2>Production-grade agent infrastructure</h2>
        </div>
        <div className="stack__chips">
          {["LangGraph", "Model Context Protocol", provider, model, "FastAPI", "React + R3F"].map(
            (s) => (
              <span key={s} className="stack__chip">
                {s}
              </span>
            )
          )}
        </div>
        <div className="stack__cta">
          <button className="btn3d btn3d--primary btn3d--lg" onClick={onLaunch}>
            Launch the console <Icon name="send" size={16} />
          </button>
        </div>
      </section>

      <footer className="lfoot">
        <span>DevOps Copilot</span>
        <span>AI Incident Command Center · LangGraph · MCP · {provider}</span>
      </footer>
    </div>
  );
}
