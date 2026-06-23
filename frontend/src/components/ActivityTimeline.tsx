// Renders the agent's node-by-node trace as a vertical timeline. The backend
// trace strings already carry emoji + a short label (see app/session.py).

interface Props {
  trace: string[];
  thinking?: boolean;
}

export function ActivityTimeline({ trace, thinking }: Props) {
  if (trace.length === 0 && !thinking) return null;

  return (
    <div className="timeline">
      <div className="timeline__head">Agent activity</div>
      <ul className="timeline__list">
        {trace.map((step, i) => (
          <li key={i} className="timeline__item">
            <span className="timeline__bullet" />
            <span className="timeline__text">{step}</span>
          </li>
        ))}
        {thinking && (
          <li className="timeline__item timeline__item--live">
            <span className="timeline__bullet timeline__bullet--live" />
            <span className="timeline__text">
              working<span className="ellipsis" />
            </span>
          </li>
        )}
      </ul>
    </div>
  );
}
