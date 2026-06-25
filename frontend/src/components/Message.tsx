import { useState } from "react";
import ReactMarkdown from "react-markdown";

import type { Turn } from "../types";
import { ActivityTimeline } from "./ActivityTimeline";
import { ApprovalCard } from "./ApprovalCard";
import { Icon } from "./Icon";
import { RcaReportCard } from "./RcaReportCard";

interface Props {
  turn: Turn;
  onDecision: (turnId: string, approved: boolean, reason: string) => void;
  onRetry?: () => void;
  onFeedback?: (rating: "up" | "down") => void;
}

export function Message({ turn, onDecision, onRetry, onFeedback }: Props) {
  const [rated, setRated] = useState<"up" | "down" | null>(null);
  if (turn.role === "user") {
    return (
      <div className="row row--user">
        <div className="bubble bubble--user">{turn.text}</div>
      </div>
    );
  }

  const thinking = turn.status === "thinking";

  return (
    <div className="row row--assistant">
      <div className="avatar">
        <Icon name="bot" size={18} />
      </div>
      <div className="bubble bubble--assistant">
        <ActivityTimeline trace={turn.trace} thinking={thinking} />

        {turn.status === "awaiting_approval" && turn.approval && (
          <ApprovalCard
            request={turn.approval}
            onDecision={(approved, reason) =>
              onDecision(turn.id, approved, reason)
            }
          />
        )}

        {turn.status === "error" && (
          <div className="inline-error" role="alert">
            <Icon name="alert" size={16} className="inline-error__icon" />
            <span>{turn.text}</span>
            {onRetry && (
              <button type="button" className="inline-error__retry" onClick={onRetry}>
                <Icon name="refresh" size={13} />
                <span>Retry</span>
              </button>
            )}
          </div>
        )}

        {turn.text &&
          turn.status !== "awaiting_approval" &&
          turn.status !== "error" && (
            <div className="answer">
              <ReactMarkdown>{turn.text}</ReactMarkdown>
            </div>
          )}

        {turn.status === "completed" && turn.report && (
          <RcaReportCard report={turn.report} />
        )}

        {turn.status === "completed" && (turn.text || turn.report) && (
          <div className="turn-foot">
            {!!turn.tokensUsed && (
              <span className="turn-meta" title="Total LLM tokens spent on this investigation">
                <Icon name="cpu" size={12} />
                <span>{turn.tokensUsed.toLocaleString()} tokens</span>
              </span>
            )}
            {onFeedback && (
              <span className="feedback" role="group" aria-label="Rate this investigation">
                <button
                  type="button"
                  className={`feedback__btn${rated === "up" ? " feedback__btn--on" : ""}`}
                  aria-label="Helpful"
                  aria-pressed={rated === "up"}
                  disabled={rated !== null}
                  onClick={() => {
                    setRated("up");
                    onFeedback("up");
                  }}
                >
                  <Icon name="thumbs-up" size={13} />
                </button>
                <button
                  type="button"
                  className={`feedback__btn${rated === "down" ? " feedback__btn--on" : ""}`}
                  aria-label="Not helpful"
                  aria-pressed={rated === "down"}
                  disabled={rated !== null}
                  onClick={() => {
                    setRated("down");
                    onFeedback("down");
                  }}
                >
                  <Icon name="thumbs-down" size={13} />
                </button>
                {rated && <span className="feedback__thanks">Thanks for the feedback</span>}
              </span>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
