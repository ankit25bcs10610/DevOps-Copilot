import { providerLabel, useConfig } from "../hooks/useConfig";
import { Icon } from "./Icon";

const PIPELINE = [
  { icon: "clipboard", label: "Plan", hint: "decompose the request" },
  { icon: "search", label: "Investigate", hint: "call MCP tools" },
  { icon: "pause", label: "Approve", hint: "human-in-the-loop" },
  { icon: "insight", label: "Diagnose", hint: "root cause + fix" },
  { icon: "refresh", label: "Reflect", hint: "done or loop" },
];

// Per-server glyphs (keyed by backend server name).
const SERVER_ICON: Record<string, string> = {
  "logs-metrics": "database",
  repo: "folder",
  github: "branch",
};

export function Sidebar() {
  const { config: cfg, failed } = useConfig();
  const placeholder = failed ? "Backend offline — start the API" : "Connecting…";

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
          <div className={`side-skel${failed ? " side-skel--offline" : ""}`}>
            {placeholder}
          </div>
        )}
      </section>

      <section className="side-section">
        <h3 className="side-title">
          MCP Servers{cfg ? ` · ${cfg.servers.length}` : ""}
        </h3>
        <div className="servers">
          {!cfg && <div className="side-skel">{placeholder}</div>}
          {(cfg?.servers ?? []).map((s) => (
            <div key={s.name} className="server">
              <div className="server__head">
                <Icon
                  name={SERVER_ICON[s.name] ?? "server"}
                  size={15}
                  className="server__icon"
                />
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
              <span className="pipeline__icon">
                <Icon name={p.icon} size={16} />
              </span>
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
