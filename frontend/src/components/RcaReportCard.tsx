import { useState } from "react";

import type { Confidence, RcaReport, Severity, Verdict, VerifyVerdict } from "../types";
import { Icon } from "./Icon";

const VERIFY_ICON: Record<VerifyVerdict, string> = {
  verified: "check",
  unverified: "x",
  inconclusive: "help",
  no_fix_proposed: "help",
};

const VERIFY_LABEL: Record<VerifyVerdict, string> = {
  verified: "Fix verified",
  unverified: "Fix does not resolve the cause",
  inconclusive: "Fix unverified — inconclusive",
  no_fix_proposed: "No fix proposed",
};

const SEV_LABEL: Record<Severity, string> = {
  SEV1: "SEV1 · Critical",
  SEV2: "SEV2 · High",
  SEV3: "SEV3 · Moderate",
  SEV4: "SEV4 · Low",
  INFO: "Info",
};

const VERDICT_ICON: Record<Verdict, string> = {
  validated: "check",
  invalidated: "x",
  inconclusive: "help",
};

function download(filename: string, text: string) {
  const blob = new Blob([text], { type: "text/markdown" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

/** Renders the structured RCA deliverable: severity/confidence header, root
 *  cause, ranked hypotheses with verdicts + cited evidence, and recommended
 *  actions — plus a one-click postmortem download. This is the agent's verifiable
 *  output, so an SRE can drop from the top hypothesis to the evidence behind it. */
export function RcaReportCard({ report }: { report: RcaReport }) {
  const [open, setOpen] = useState(true);
  const sev = report.severity ?? "SEV3";
  const conf: Confidence = report.calibrated_confidence ?? report.confidence ?? "low";

  return (
    <section className={`rca rca--${sev.toLowerCase()}`} aria-label="Root cause analysis report">
      <button
        className="rca__head"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        <span className={`rca__sev rca__sev--${sev.toLowerCase()}`}>{SEV_LABEL[sev]}</span>
        <span className="rca__title">Root cause analysis</span>
        <span className={`rca__conf rca__conf--${conf}`} title="Confidence in the root cause">
          {conf} confidence
        </span>
        <Icon name={open ? "chevron-up" : "chevron-down"} size={16} className="rca__chev" />
      </button>

      {open && (
        <div className="rca__body">
          {report.abstained && (
            <div className="rca__abstain" role="note">
              <Icon name="help" size={15} className="rca__abstain-icon" />
              <div>
                <strong>Insufficient evidence</strong> — provisional read, not a confirmed root cause.
                {report.needs && report.needs.length > 0 && (
                  <ul className="rca__needs">
                    {report.needs.map((n, i) => (
                      <li key={i}>{n}</li>
                    ))}
                  </ul>
                )}
              </div>
            </div>
          )}

          <p className="rca__summary">{report.summary}</p>

          {report.root_cause && (
            <div className="rca__rc">
              <span className="rca__rc-label">Root cause</span>
              <span className="rca__rc-text">{report.root_cause}</span>
            </div>
          )}

          {report.affected_services.length > 0 && (
            <div className="rca__services">
              {report.affected_services.map((s) => (
                <span key={s} className="rca__chip">{s}</span>
              ))}
            </div>
          )}

          {report.hypotheses.length > 0 && (
            <div className="rca__section">
              <h4 className="rca__h">Hypotheses</h4>
              <ul className="rca__hyps">
                {report.hypotheses.map((h, i) => (
                  <li key={i} className={`rca__hyp rca__hyp--${h.verdict}`}>
                    <div className="rca__hyp-top">
                      <Icon name={VERDICT_ICON[h.verdict]} size={14} className="rca__hyp-icon" />
                      <span className="rca__hyp-cause">{h.cause}</span>
                      <span className="rca__hyp-verdict">{h.verdict}</span>
                    </div>
                    {h.evidence.length > 0 && (
                      <ul className="rca__ev">
                        {h.evidence.map((e, j) => (
                          <li key={j}><code>{e}</code></li>
                        ))}
                      </ul>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {report.evidence.length > 0 && (
            <div className="rca__section">
              <h4 className="rca__h">Evidence</h4>
              <ul className="rca__ev rca__ev--flat">
                {report.evidence.map((e, i) => (
                  <li key={i}><code>{e}</code></li>
                ))}
              </ul>
            </div>
          )}

          {report.recommended_actions.length > 0 && (
            <div className="rca__section">
              <h4 className="rca__h">Recommended actions</h4>
              <ol className="rca__actions">
                {report.recommended_actions.map((a, i) => (
                  <li key={i}>{a}</li>
                ))}
              </ol>
            </div>
          )}

          {report.verification && report.verification.verdict !== "no_fix_proposed" && (
            <div className="rca__section">
              <h4 className="rca__h">Fix verification</h4>
              <div className={`rca__verify rca__verify--${report.verification.verdict}`}>
                <div className="rca__verify-top">
                  <Icon name={VERIFY_ICON[report.verification.verdict]} size={14} className="rca__hyp-icon" />
                  <span className="rca__verify-verdict">{VERIFY_LABEL[report.verification.verdict]}</span>
                  <span className={`rca__conf rca__conf--${report.verification.confidence}`}>
                    {report.verification.confidence} confidence
                  </span>
                </div>
                {report.verification.rationale && (
                  <p className="rca__verify-rationale">{report.verification.rationale}</p>
                )}
                {report.verification.sandbox &&
                  report.verification.sandbox.verdict !== "no_patch" && (
                    <div className={`rca__sandbox rca__sandbox--${report.verification.sandbox.verdict}`}>
                      <Icon
                        name={report.verification.sandbox.verdict === "resolved" ? "check" : "help"}
                        size={13}
                        className="rca__hyp-icon"
                      />
                      <span>
                        <strong>Sandbox counterfactual: {report.verification.sandbox.verdict}</strong>
                        {report.verification.sandbox.detail ? ` — ${report.verification.sandbox.detail}` : ""}
                      </span>
                    </div>
                  )}
                {report.verification.resolution_criteria.length > 0 && (
                  <>
                    <div className="rca__verify-label">Resolution criteria</div>
                    <ul className="rca__ev rca__ev--flat">
                      {report.verification.resolution_criteria.map((c, i) => (
                        <li key={i}><code>{c}</code></li>
                      ))}
                    </ul>
                  </>
                )}
                {report.verification.residual_risks.length > 0 && (
                  <>
                    <div className="rca__verify-label">Residual risks</div>
                    <ul className="rca__needs">
                      {report.verification.residual_risks.map((r, i) => (
                        <li key={i}>{r}</li>
                      ))}
                    </ul>
                  </>
                )}
              </div>
            </div>
          )}

          {report.postmortem && (
            <div className="rca__foot">
              <button
                type="button"
                className="rca__dl"
                onClick={() => download("postmortem.md", report.postmortem)}
              >
                <Icon name="download" size={14} />
                <span>Download postmortem</span>
              </button>
            </div>
          )}
        </div>
      )}
    </section>
  );
}
