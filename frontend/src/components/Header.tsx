import { useEffect, useState } from "react";

import { health } from "../api";
import { modelShort, providerLabel, useConfig } from "../hooks/useConfig";
import { Icon } from "./Icon";
import { ThemePicker } from "./ThemePicker";

export function Header({ onHome }: { onHome?: () => void }) {
  const { config: cfg } = useConfig();
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
    online === null ? "connecting" : online ? "online" : "offline";
  const dotClass =
    online === null ? "dot dot--idle" : online ? "dot dot--ok" : "dot dot--bad";

  return (
    <header className="header">
      <div className="brand">
        {onHome && (
          <button className="brand__home" onClick={onHome} aria-label="Back to home" title="Home">
            <Icon name="chevron" size={16} className="brand__home-icon" />
          </button>
        )}
        <span className="brand__logo">
          <Icon name="tool" size={20} />
        </span>
        <div>
          <h1 className="brand__title">DevOps Copilot</h1>
          <p className="brand__subtitle">Autonomous incident investigation</p>
        </div>
      </div>

      <div className="header__meta">
        {cfg && (
          <span className="model-badge" title={cfg.model}>
            <span className="model-badge__provider">{providerLabel(cfg.provider)}</span>
            <span className="model-badge__model">{modelShort(cfg.model)}</span>
          </span>
        )}
        <span className="status">
          <span className={dotClass} />
          <span className="status__label">{label}</span>
        </span>
        <ThemePicker />
      </div>
    </header>
  );
}
