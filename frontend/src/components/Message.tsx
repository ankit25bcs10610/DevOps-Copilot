import ReactMarkdown from "react-markdown";

import type { Turn } from "../types";
import { ActivityTimeline } from "./ActivityTimeline";
import { ApprovalCard } from "./ApprovalCard";

interface Props {
  turn: Turn;
  onDecision: (turnId: string, approved: boolean, reason: string) => void;
}

export function Message({ turn, onDecision }: Props) {
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
      <div className="avatar">🤖</div>
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

        {turn.text && turn.status !== "awaiting_approval" && (
          <div className="answer">
            <ReactMarkdown>{turn.text}</ReactMarkdown>
          </div>
        )}
      </div>
    </div>
  );
}
