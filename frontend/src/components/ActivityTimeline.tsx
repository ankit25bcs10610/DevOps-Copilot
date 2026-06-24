// Renders the agent's node-by-node trace as a vertical timeline. The backend
// trace strings carry a leading emoji + a short label (see app/session.py); we
// map that emoji to an SVG icon and strip it from the text (no emoji-as-icon).

import { Icon } from "./Icon";

const EMOJI_ICON: Record<string, string> = {
  "📋": "clipboard",
  "🤖": "bot",
  "🔧": "search",
  "⏸️": "pause",
  "⏸": "pause",
  "🔁": "refresh",
};

function splitStep(step: string): { icon: string; text: string } {
  for (const [emoji, icon] of Object.entries(EMOJI_ICON)) {
    if (step.startsWith(emoji)) {
      return { icon, text: step.slice(emoji.length).trim() };
    }
  }
  return { icon: "", text: step.replace(/^•\s*/, "") };
}

interface Props {
  trace: string[];
  thinking?: boolean;
}

export function ActivityTimeline({ trace, thinking }: Props) {
  if (trace.length === 0 && !thinking) return null;

  return (
    <div className="timeline" aria-live="polite" aria-busy={thinking}>
      <div className="timeline__head">Agent activity</div>
      <ul className="timeline__list">
        {trace.map((step, i) => {
          const { icon, text } = splitStep(step);
          return (
            <li key={i} className="timeline__item">
              <span className="timeline__icon">
                {icon ? <Icon name={icon} size={13} /> : <span className="timeline__dot" />}
              </span>
              <span className="timeline__text">{text}</span>
            </li>
          );
        })}
        {thinking && (
          <li className="timeline__item timeline__item--live">
            <span className="timeline__icon">
              <span className="timeline__dot timeline__dot--live" />
            </span>
            <span className="timeline__text">
              working<span className="ellipsis" />
            </span>
          </li>
        )}
      </ul>
    </div>
  );
}
