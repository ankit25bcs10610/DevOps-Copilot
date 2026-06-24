import { useState } from "react";

import { githubConnect, githubDisconnect } from "../api";
import { refreshConfig, useConfig } from "../hooks/useConfig";

/** Connect-GitHub control inside the sidebar's GitHub card. The shared config
 *  store (cfg.github) is the single source of truth, so a Reset elsewhere or a
 *  refetch keeps this card in sync. */
export function GithubConnect() {
  const { config } = useConfig();
  const gh = config?.github;
  const connected = !!gh?.connected;

  const [open, setOpen] = useState(false);
  const [token, setToken] = useState("");
  const [repo, setRepo] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [owner, name] = repo.trim().split("/");
  const repoOk = !!owner && !!name;
  const canSubmit = token.trim().length > 0 && repoOk && !busy;

  const connect = async () => {
    setError(null);
    setBusy(true);
    try {
      await githubConnect(token.trim(), repo.trim());
      await refreshConfig(); // store drives the pill + this card
      setOpen(false);
      setToken("");
      setRepo("");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const disconnect = async () => {
    setError(null);
    setBusy(true);
    try {
      await githubDisconnect();
      await refreshConfig();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="gh">
      <div className="gh__status">
        <span className={`gh__dot ${connected ? "gh__dot--live" : "gh__dot--off"}`} />
        <span className="gh__label" title={gh?.repo ?? undefined}>
          {connected ? gh?.repo : "Offline demo"}
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
      {error && !open && (
        <div className="gh__error" role="alert">
          {error}
        </div>
      )}
    </div>
  );
}
