import { useEffect, useState } from "react";

import { getConfig } from "../api";
import type { AppConfig } from "../types";

const PIPELINE = [
  { icon: "📋", label: "Plan", hint: "decompose the request" },
  { icon: "🔧", label: "Investigate", hint: "call MCP tools" },
  { icon: "⏸", label: "Approve", hint: "human-in-the-loop" },
  { icon: "🧠", label: "Diagnose", hint: "root cause + fix" },
  { icon: "🔁", label: "Reflect", hint: "done or loop" },
];

const providerLabel = (p: string) =>
  p === "anthropic" ? "Anthropic" : p === "groq" ? "Groq" : p;

export function Sidebar() {
  const [cfg, setCfg] = useState<AppConfig | null>(null);

  useEffect(() => {
    let active = true;
    getConfig()
      .then((c) => active && setCfg(c))
      .catch(() => active && setCfg(null));
    return () => {
      active = false;
    };
  }, []);

  return (
    <aside className="sidebar">
      <section className="side-section">
        <h3 className="side-title">Model</h3>
        {cfg ? (
          <div className="model-card">
            <div className="model-card__top">
              <span className="provider-pill">{providerLabel(cfg.provider)}</span>
              {cfg.offline_mode && <span className="offline-pill">offline demo</span>}
            </div>
            <div className="model-row">
              <span className="model-row__k">main</span>
              <code className="model-row__v">{cfg.model}</code>
            </div>
            <div className="model-row">
              <span className="model-row__k">fast</span>
              <code className="model-row__v">{cfg.fast_model}</code>
            </div>
          </div>
        ) : (
          <div className="side-skel">loading…</div>
        )}
      </section>

      <section className="side-section">
        <h3 className="side-title">
          MCP Servers{cfg ? ` · ${cfg.servers.length}` : ""}
        </h3>
        <div className="servers">
          {(cfg?.servers ?? []).map((s) => (
            <div key={s.name} className="server">
              <div className="server__head">
                <span className="server__name">{s.label}</span>
                {s.custom && <span className="server__badge">custom</span>}
              </div>
              <div className="server__tools">
                {s.tools.map((t) => (
                  <span key={t} className="tool-chip">
                    {t}
                  </span>
                ))}
              </div>
            </div>
          ))}
        </div>
      </section>

      <section className="side-section">
        <h3 className="side-title">Agent Pipeline</h3>
        <ol className="pipeline">
          {PIPELINE.map((p) => (
            <li key={p.label} className="pipeline__step">
              <span className="pipeline__icon">{p.icon}</span>
              <span className="pipeline__label">{p.label}</span>
              <span className="pipeline__hint">{p.hint}</span>
            </li>
          ))}
        </ol>
      </section>

      <div className="side-foot">
        LangGraph · MCP · {cfg ? providerLabel(cfg.provider) : "—"}
      </div>
    </aside>
  );
}
