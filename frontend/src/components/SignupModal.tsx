import { useState } from "react";

import * as api from "../api";
import type { SignupResult } from "../api";
import { Icon } from "./Icon";

/** Self-serve onboarding modal: create an organization + owner API key with no
 *  prior credential. Works only when the backend runs multi-tenant mode; in
 *  single-tenant mode the backend returns a clear 400 which we surface inline. */
export function SignupModal({ onClose }: { onClose: () => void }) {
  const [orgName, setOrgName] = useState("");
  const [email, setEmail] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<SignupResult | null>(null);
  const [copied, setCopied] = useState(false);

  const submit = async () => {
    setError("");
    setBusy(true);
    try {
      setResult(await api.signup(orgName.trim(), email.trim()));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const copyKey = async () => {
    if (!result) return;
    try {
      await navigator.clipboard.writeText(result.api_key);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard blocked — the key is visible to copy manually */
    }
  };

  const canSubmit = orgName.trim().length > 0 && email.trim().includes("@") && !busy;

  return (
    <div className="admin__overlay" role="dialog" aria-modal="true" aria-label="Create an account">
      <div className="admin signup">
        <header className="admin__head">
          <h2>Create an account</h2>
          <button className="admin__close" onClick={onClose} aria-label="Close">
            <Icon name="x" size={18} />
          </button>
        </header>

        {!result && (
          <div className="signup__body">
            <p className="admin__note">
              Spin up an organization and get an owner API key — your tenant is provisioned on the
              free plan. No credit card.
            </p>
            <label className="signup__field">
              <span>Organization name</span>
              <input
                value={orgName}
                onChange={(e) => setOrgName(e.target.value)}
                placeholder="Acme Inc."
                maxLength={120}
                autoFocus
              />
            </label>
            <label className="signup__field">
              <span>Work email</span>
              <input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="you@acme.com"
                onKeyDown={(e) => e.key === "Enter" && canSubmit && submit()}
              />
            </label>
            {error && <div className="admin__err" role="alert">{error}</div>}
            <button className="signup__cta" onClick={submit} disabled={!canSubmit}>
              {busy ? "Creating…" : "Create organization"}
              {!busy && <Icon name="send" size={15} />}
            </button>
          </div>
        )}

        {result && (
          <div className="signup__body">
            <p className="admin__note">
              <strong>{result.org_name}</strong> is ready on the <strong>{result.plan}</strong> plan.
              Here is your <strong>owner</strong> API key — copy it now, it&apos;s shown only once.
            </p>
            <div className="signup__key">
              <code>{result.api_key}</code>
              <button onClick={copyKey} aria-label="Copy API key">
                <Icon name={copied ? "check" : "clipboard"} size={14} />
                {copied ? "Copied" : "Copy"}
              </button>
            </div>
            <p className="admin__muted">
              Send it as <code>Authorization: Bearer {result.api_key.slice(0, 12)}…</code> on every
              request, or set <code>VITE_API_TOKEN</code> for this frontend.
            </p>
            <button className="signup__cta" onClick={onClose}>Done</button>
          </div>
        )}
      </div>
    </div>
  );
}
