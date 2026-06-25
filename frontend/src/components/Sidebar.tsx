import { providerLabel, useConfig } from "../hooks/useConfig";
import { GithubConnect } from "./GithubConnect";
import { Icon } from "./Icon";
import { ModelConfig } from "./ModelConfig";
import { SourceConnect } from "./SourceConnect";

// Per-server glyphs (keyed by backend server name).
const SERVER_ICON: Record<string, string> = {
  "logs-metrics": "database",
  repo: "folder",
  github: "branch",
};

export function Sidebar() {
  const { config: cfg, failed } = useConfig();
  const placeholder = failed ? "Backend offline — start the API" : "Connecting…";

  const sourceFor = (name: string) =>
    name === "logs-metrics" ? (
      <SourceConnect kind="logs" path={cfg?.sources.logs_path} />
    ) : name === "repo" ? (
      <SourceConnect kind="repo" path={cfg?.sources.repo_path} />
    ) : name === "github" ? (
      <GithubConnect />
    ) : null;

  return (
    <aside className="sidebar">
      <section className="side-section">
        <h3 className="side-title">Model</h3>
        {cfg ? (
          <div className="model-card">
            <div className="model-card__top">
              <span className="provider-pill">{providerLabel(cfg.provider)}</span>
              <span className="online-pill">online</span>
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
          <div className={`side-skel${failed ? " side-skel--offline" : ""}`}>{placeholder}</div>
        )}
        <ModelConfig />
      </section>

      <section className="side-section">
        <h3 className="side-title">MCP Servers{cfg ? ` · ${cfg.servers.length}` : ""}</h3>
        <div className="servers">
          {!cfg && <div className="side-skel">{placeholder}</div>}
          {(cfg?.servers ?? []).map((s) => (
            <div key={s.name} className="server">
              <div className="server__head">
                <Icon name={SERVER_ICON[s.name] ?? "server"} size={15} className="server__icon" />
                <span className="server__name">{s.label}</span>
                <span className="server__badge">connected</span>
              </div>
              <div className="server__tools">
                {s.tools.map((t) => (
                  <span key={t} className="tool-chip">
                    {t}
                  </span>
                ))}
              </div>
              {sourceFor(s.name)}
            </div>
          ))}
        </div>
      </section>

      <a
        className="side-help"
        href="https://github.com/ankit25bcs10610/DevOps-Copilot#readme"
        target="_blank"
        rel="noopener noreferrer"
      >
        <div className="side-help__txt">
          <strong>Need help getting started?</strong>
          <span>Check out our documentation</span>
        </div>
        <Icon name="send" size={15} />
      </a>
    </aside>
  );
}
