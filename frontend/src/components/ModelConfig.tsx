import { useState } from "react";

import { configureModel, resetConfig } from "../api";
import { refreshConfig } from "../hooks/useConfig";

const PROVIDERS = [
  { id: "anthropic", label: "Anthropic — Claude" },
  { id: "openai", label: "OpenAI — GPT" },
  { id: "gemini", label: "Google — Gemini" },
  { id: "groq", label: "Groq — Llama / open models" },
  { id: "deepseek", label: "DeepSeek" },
];

// Selectable models per provider. The first entry is the provider's main default.
const MODELS: Record<string, string[]> = {
  anthropic: [
    "claude-opus-4-8",
    "claude-opus-4-7",
    "claude-opus-4-6",
    "claude-sonnet-4-6",
    "claude-haiku-4-5",
    "claude-fable-5",
  ],
  openai: [
    "gpt-4o",
    "gpt-4o-mini",
    "gpt-4.1",
    "gpt-4.1-mini",
    "gpt-4.1-nano",
    "gpt-4-turbo",
    "gpt-3.5-turbo",
    "o3",
    "o3-mini",
    "o1",
    "o1-mini",
  ],
  gemini: [
    "gemini-2.5-pro",
    "gemini-2.5-flash",
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
    "gemini-1.5-pro",
    "gemini-1.5-flash",
    "gemini-1.5-flash-8b",
  ],
  groq: [
    "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant",
    "llama-3.1-70b-versatile",
    "llama3-70b-8192",
    "llama3-8b-8192",
    "mixtral-8x7b-32768",
    "gemma2-9b-it",
    "deepseek-r1-distill-llama-70b",
    "qwen-2.5-32b",
  ],
  deepseek: ["deepseek-chat", "deepseek-reasoner"],
};

/** "Change model" control under the MODEL card — switch provider, pick the exact
 *  main/fast model, and paste an API key without a restart. */
export function ModelConfig() {
  const [open, setOpen] = useState(false);
  const [provider, setProvider] = useState("anthropic");
  const [apiKey, setApiKey] = useState("");
  const [model, setModel] = useState("");
  const [fast, setFast] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const models = MODELS[provider] ?? [];

  // Switching provider resets the model picks (each provider has its own list).
  const changeProvider = (p: string) => {
    setProvider(p);
    setModel("");
    setFast("");
  };

  const apply = async () => {
    setError(null);
    setBusy(true);
    try {
      await configureModel(provider, apiKey.trim(), model.trim(), fast.trim());
      await refreshConfig(); // updates the sidebar MODEL card to the chosen models
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
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
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
        <select value={provider} onChange={(e) => changeProvider(e.target.value)}>
          {PROVIDERS.map((p) => (
            <option key={p.id} value={p.id}>
              {p.label}
            </option>
          ))}
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

      <label className="cfg__field">
        <span>Main model</span>
        <select value={model} onChange={(e) => setModel(e.target.value)}>
          <option value="">Provider default</option>
          {models.map((m) => (
            <option key={m} value={m}>
              {m}
            </option>
          ))}
        </select>
      </label>

      <label className="cfg__field">
        <span>Fast model (plan / reflect)</span>
        <select value={fast} onChange={(e) => setFast(e.target.value)}>
          <option value="">Provider default</option>
          {models.map((m) => (
            <option key={m} value={m}>
              {m}
            </option>
          ))}
        </select>
      </label>

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
