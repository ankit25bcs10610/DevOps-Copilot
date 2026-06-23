import { useEffect, useState } from "react";

import { health } from "../api";

export function Header() {
  const [online, setOnline] = useState<boolean | null>(null);

  useEffect(() => {
    let active = true;
    const ping = async () => {
      const ok = await health();
      if (active) setOnline(ok);
    };
    ping();
    const id = setInterval(ping, 10_000);
    return () => {
      active = false;
      clearInterval(id);
    };
  }, []);

  const label =
    online === null ? "connecting…" : online ? "backend online" : "backend offline";
  const dotClass =
    online === null ? "dot dot--idle" : online ? "dot dot--ok" : "dot dot--bad";

  return (
    <header className="header">
      <div className="brand">
        <span className="brand__logo">🛠️</span>
        <div>
          <h1 className="brand__title">DevOps Copilot</h1>
          <p className="brand__subtitle">LangGraph · MCP · Groq</p>
        </div>
      </div>
      <div className="status">
        <span className={dotClass} />
        <span className="status__label">{label}</span>
      </div>
    </header>
  );
}
