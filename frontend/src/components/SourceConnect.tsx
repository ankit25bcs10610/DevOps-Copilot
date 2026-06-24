import { useState } from "react";

import { connectLogs, connectRepo } from "../api";
import { refreshConfig } from "../hooks/useConfig";

const shortPath = (p?: string) => {
  if (!p) return "default";
  const parts = p.split("/").filter(Boolean);
  return parts.length > 2 ? `…/${parts.slice(-2).join("/")}` : p;
};

/** Point the repo or logs/metrics MCP server at a local directory (validated
 *  server-side). Rendered inside the matching sidebar card. */
export function SourceConnect({ kind, path }: { kind: "repo" | "logs"; path?: string }) {
  const [open, setOpen] = useState(false);
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [warn, setWarn] = useState<string | null>(null);

  const apply = async () => {
    setError(null);
    setWarn(null);
    setBusy(true);
    try {
      if (kind === "repo") {
        await connectRepo(value.trim());
      } else {
        const r = await connectLogs(value.trim());
        if (r.missing_files?.length) setWarn(`missing: ${r.missing_files.join(", ")}`);
      }
      await refreshConfig();
      setOpen(false);
      setValue("");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="src">
      <div className="src__row">
        <span className="src__path" title={path}>
          {shortPath(path)}
        </span>
        <button className="gh__link" onClick={() => setOpen((o) => !o)}>
          {open ? "Cancel" : "Change"}
        </button>
      </div>
      {warn && <div className="src__warn">{warn}</div>}
      {open && (
        <div className="gh__form">
          <input
            className="gh__input"
            type="text"
            placeholder={kind === "repo" ? "/path/to/your/repo" : "/path/to/logs-dir"}
            aria-label={kind === "repo" ? "Repository path" : "Logs directory path"}
            value={value}
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && value.trim() && apply()}
          />
          {error && (
            <div className="gh__error" role="alert">
              {error}
            </div>
          )}
          <button
            className="btn btn--approve gh__btn"
            disabled={busy || !value.trim()}
            onClick={apply}
          >
            {busy ? "Setting…" : "Use this path"}
          </button>
        </div>
      )}
    </div>
  );
}
