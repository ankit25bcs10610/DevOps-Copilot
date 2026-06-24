import { useEffect, useState } from "react";

import { githubConnect, githubDisconnect, githubStatus } from "../api";
import { refreshConfig } from "../hooks/useConfig";
import type { GithubStatus } from "../types";

/** Connect-GitHub control rendered inside the sidebar's GitHub server card.
 *  Lets the user point the GitHub MCP server at a real repo (validated by the
 *  backend) instead of the offline demo fixtures. */
export function GithubConnect() {
  const [status, setStatus] = useState<GithubStatus | null>(null);
  const [open, setOpen] = useState(false);
  const [token, setToken] = useState("");
  const [repo, setRepo] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    githubStatus()
      .then((s) => active && setStatus(s))
      .catch(() => {});
    return () => {
      active = false;
    };
  }, []);

  const connected = !!status?.connected;
  const canSubmit = token.trim().length > 0 && repo.includes("/") && !busy;

  const connect = async () => {
    setError(null);
    setBusy(true);
    try {
      const s = await githubConnect(token.trim(), repo.trim());
      setStatus(s);
      setOpen(false);
      setToken("");
      refreshConfig(); // clear the "offline demo" pill across the sidebar
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const disconnect = async () => {
    setBusy(true);
    try {
      setStatus(await githubDisconnect());
      refreshConfig();
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="gh">
      <div className="gh__status">
        <span className={`gh__dot ${connected ? "gh__dot--live" : "gh__dot--off"}`} />
        <span className="gh__label" title={status?.full_name ?? undefined}>
          {connected ? status?.full_name ?? status?.repo : "Offline demo"}
        </span>
        {connected ? (
          <button className="gh__link" onClick={disconnect} disabled={busy}>
            Disconnect
          </button>
        ) : (
          <button className="gh__link" onClick={() => setOpen((o) => !o)}>
            {open ? "Cancel" : "Connect"}
          </button>
        )}
      </div>

      {open && !connected && (
        <div className="gh__form">
          <input
            className="gh__input"
            type="password"
            placeholder="GitHub token (repo scope)"
            aria-label="GitHub access token"
            value={token}
            onChange={(e) => setToken(e.target.value)}
          />
          <input
            className="gh__input"
            type="text"
            placeholder="owner/repo"
            aria-label="GitHub repository"
            value={repo}
            onChange={(e) => setRepo(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && canSubmit && connect()}
          />
          {error && (
            <div className="gh__error" role="alert">
              {error}
            </div>
          )}
          <button className="btn btn--approve gh__btn" disabled={!canSubmit} onClick={connect}>
            {busy ? "Connecting…" : "Connect repo"}
          </button>
          <p className="gh__note">Validated against GitHub · stored in memory only.</p>
        </div>
      )}
    </div>
  );
}
