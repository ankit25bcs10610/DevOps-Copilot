import { useEffect, useState } from "react";

import * as api from "../api";
import type { AdminOrg, ApiKeyInfo, AuditEvent, UsageSummary } from "../types";
import { Icon } from "./Icon";

/** Tenant admin console (multi-tenant mode): org summary, usage vs quota, API-key
 *  management, and a tamper-evident audit viewer. Degrades to a clear notice when
 *  the backend is single-tenant. */
export function AdminPanel({ onClose }: { onClose: () => void }) {
  const [usage, setUsage] = useState<UsageSummary | null>(null);
  const [org, setOrg] = useState<AdminOrg | null>(null);
  const [keys, setKeys] = useState<ApiKeyInfo[]>([]);
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [chain, setChain] = useState<string>("");
  const [newKey, setNewKey] = useState<string>("");
  const [keyName, setKeyName] = useState("");
  const [keyRole, setKeyRole] = useState("responder");
  const [enabled, setEnabled] = useState<boolean | null>(null);
  const [error, setError] = useState("");

  const refresh = async () => {
    try {
      const u = await api.getUsage();
      setUsage(u);
      if (u.multi_tenant === false) {
        setEnabled(false);
        return;
      }
      setEnabled(true);
      const [o, k, a] = await Promise.all([
        api.adminOrg().catch(() => null),
        api.adminListKeys().catch(() => ({ api_keys: [] })),
        api.getAudit(25).catch(() => ({ events: [] })),
      ]);
      setOrg(o);
      setKeys(k.api_keys);
      setEvents(a.events);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  useEffect(() => {
    void refresh();
  }, []);

  const createKey = async () => {
    setError("");
    try {
      const res = await api.adminCreateKey(keyName, keyRole);
      setNewKey(res.api_key);
      setKeyName("");
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const revoke = async (id: string) => {
    await api.adminRevokeKey(id).catch((e) => setError(String(e)));
    await refresh();
  };

  const verify = async () => {
    const r = await api.verifyAudit();
    setChain(r.valid ? `✓ intact (${r.count} events)` : `✗ broken at #${r.broken_at}`);
  };

  const quotaPct =
    usage && usage.investigations_quota && usage.investigations_quota > 0
      ? Math.min(100, Math.round(((usage.investigations_used ?? 0) / usage.investigations_quota) * 100))
      : 0;

  return (
    <div className="admin__overlay" role="dialog" aria-modal="true" aria-label="Admin console">
      <div className="admin">
        <header className="admin__head">
          <h2>Admin console</h2>
          <button className="admin__close" onClick={onClose} aria-label="Close admin console">
            <Icon name="x" size={18} />
          </button>
        </header>

        {error && <div className="admin__err" role="alert">{error}</div>}

        {enabled === false && (
          <p className="admin__note">
            Multi-tenant mode is off — admin features are available when the backend runs with
            <code> COPILOT_MULTI_TENANT=true</code>. See <code>docs/COMMERCIALIZATION.md</code>.
          </p>
        )}

        {enabled && (
          <div className="admin__body">
            <section className="admin__card">
              <h3>Organization</h3>
              {org ? (
                <div className="admin__org">
                  <span><strong>{org.name}</strong> · plan <strong>{org.plan}</strong></span>
                  <span>{org.members} members · {org.active_api_keys} keys · {org.integrations} integrations</span>
                </div>
              ) : <p className="admin__muted">Your role can't view org details.</p>}
            </section>

            {usage && (
              <section className="admin__card">
                <h3>Usage this {usage.period ?? "period"}</h3>
                <div className="admin__usage">
                  <span>
                    {usage.investigations_used ?? 0}
                    {usage.investigations_quota && usage.investigations_quota > 0
                      ? ` / ${usage.investigations_quota}`
                      : " (unlimited)"} investigations
                  </span>
                  {usage.investigations_quota && usage.investigations_quota > 0 && (
                    <div className="admin__bar"><span style={{ width: `${quotaPct}%` }} /></div>
                  )}
                  {usage.warning && <span className="admin__warn">Approaching your quota — consider upgrading.</span>}
                </div>
              </section>
            )}

            <section className="admin__card">
              <h3>API keys</h3>
              <div className="admin__keyform">
                <input placeholder="key name" value={keyName} onChange={(e) => setKeyName(e.target.value)} />
                <select value={keyRole} onChange={(e) => setKeyRole(e.target.value)}>
                  <option value="viewer">viewer</option>
                  <option value="responder">responder</option>
                  <option value="admin">admin</option>
                  <option value="owner">owner</option>
                </select>
                <button onClick={createKey}>Issue key</button>
              </div>
              {newKey && (
                <div className="admin__newkey">
                  <Icon name="lock" size={13} /> <code>{newKey}</code> — copy now, shown once.
                </div>
              )}
              <ul className="admin__keys">
                {keys.map((k) => (
                  <li key={k.id} className={k.active ? "" : "admin__key--revoked"}>
                    <code>{k.prefix}…</code>
                    <span>{k.name || "—"}</span>
                    <span className="admin__role">{k.role}</span>
                    {k.active ? (
                      <button className="admin__revoke" onClick={() => revoke(k.id)}>Revoke</button>
                    ) : <span className="admin__muted">revoked</span>}
                  </li>
                ))}
              </ul>
            </section>

            <section className="admin__card">
              <h3>
                Audit trail
                <button className="admin__verify" onClick={verify}>Verify chain</button>
                {chain && <span className="admin__chain">{chain}</span>}
              </h3>
              <ul className="admin__audit">
                {events.map((e, i) => (
                  <li key={i}>
                    <span className="admin__ts">{e.ts}</span>
                    <code>{e.event}</code>
                    <span className="admin__actor">{e.actor}</span>
                  </li>
                ))}
              </ul>
            </section>
          </div>
        )}
      </div>
    </div>
  );
}
