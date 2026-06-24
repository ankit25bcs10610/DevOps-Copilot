import { useState } from "react";

import { configureModel, resetConfig } from "../api";
import { refreshConfig } from "../hooks/useConfig";

/** "Change model" control under the MODEL card — switch provider/model and
 *  paste an API key (e.g. Anthropic, to unlock Opus 4.8) without a restart. */
export function ModelConfig() {
  const [open, setOpen] = useState(false);
  const [provider, setProvider] = useState("anthropic");
  const [apiKey, setApiKey] = useState("");
  const [model, setModel] = useState("");
  const [fast, setFast] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const apply = async () => {
    setError(null);
    setBusy(true);
    try {
      await configureModel(provider, apiKey.trim(), model.trim(), fast.trim());
      await refreshConfig();
      setOpen(false);
      setApiKey("");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const reset = async () => {
    setBusy(true);
    setError(null);
    try {
      await resetConfig();
      await refreshConfig();
      setOpen(false);
    } finally {
      setBusy(false);
    }
  };

  if (!open) {
    return (
      <button className="link-btn" onClick={() => setOpen(true)}>
        Change model
      </button>
    );
  }

  return (
    <div className="cfg">
      <label className="cfg__field">
        <span>Provider</span>
        <select value={provider} onChange={(e) => setProvider(e.target.value)}>
          <option value="anthropic">Anthropic — Claude Opus 4.8</option>
          <option value="groq">Groq — Llama 3.3</option>
        </select>
      </label>
      <input
        className="gh__input"
        type="password"
        placeholder={`${provider} API key`}
        aria-label="API key"
        value={apiKey}
        onChange={(e) => setApiKey(e.target.value)}
      />
      <input
        className="gh__input"
        type="text"
        placeholder="main model (optional)"
        aria-label="Main model override"
        value={model}
        onChange={(e) => setModel(e.target.value)}
      />
      <input
        className="gh__input"
        type="text"
        placeholder="fast model (optional)"
        aria-label="Fast model override"
        value={fast}
        onChange={(e) => setFast(e.target.value)}
      />
      {error && (
        <div className="gh__error" role="alert">
          {error}
        </div>
      )}
      <div className="cfg__actions">
        <button className="link-btn" onClick={() => setOpen(false)} disabled={busy}>
          Cancel
        </button>
        <button className="link-btn cfg__reset" onClick={reset} disabled={busy}>
          Reset
        </button>
        <button className="btn btn--approve cfg__apply" onClick={apply} disabled={busy}>
          {busy ? "Applying…" : "Apply"}
        </button>
      </div>
      <p className="gh__note">Key is validated on first use · stored in memory only.</p>
    </div>
  );
}
