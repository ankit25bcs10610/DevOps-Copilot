import { useState } from "react";

import type { ApprovalRequest } from "../types";

interface Props {
  request: ApprovalRequest;
  disabled?: boolean;
  onDecision: (approved: boolean, reason: string) => void;
}

/** Human-in-the-loop gate: shows the write action the agent wants to perform
 *  and lets the reviewer approve or reject before it executes. */
export function ApprovalCard({ request, disabled, onDecision }: Props) {
  const [reason, setReason] = useState("");

  return (
    <div className="approval">
      <div className="approval__head">
        <span className="approval__badge">⏸ Approval required</span>
        <span className="approval__msg">{request.message}</span>
      </div>

      {request.actions.map((action, i) => (
        <div key={i} className="approval__action">
          <div className="approval__tool">
            {action.write !== false && <span className="approval__write">WRITE</span>}
            <code>{action.tool}</code>
          </div>
          <dl className="approval__args">
            {Object.entries(action.args).map(([k, v]) => (
              <div key={k} className="approval__arg">
                <dt>{k}</dt>
                <dd>{typeof v === "string" ? v : JSON.stringify(v)}</dd>
              </div>
            ))}
          </dl>
        </div>
      ))}

      <input
        className="approval__reason"
        placeholder="Optional reason (sent to the agent if you reject)…"
        value={reason}
        onChange={(e) => setReason(e.target.value)}
        disabled={disabled}
      />

      <div className="approval__buttons">
        <button
          className="btn btn--reject"
          disabled={disabled}
          onClick={() => onDecision(false, reason)}
        >
          Reject
        </button>
        <button
          className="btn btn--approve"
          disabled={disabled}
          onClick={() => onDecision(true, reason)}
        >
          Approve & run
        </button>
      </div>
    </div>
  );
}
