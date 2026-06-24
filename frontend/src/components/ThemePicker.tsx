import { useEffect, useRef, useState } from "react";

import { useTheme } from "../hooks/useTheme";
import { THEMES } from "../themes";
import { Icon } from "./Icon";

export function ThemePicker() {
  const { theme, setTheme } = useTheme();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  // Close on outside click or Escape.
  useEffect(() => {
    if (!open) return;
    const onClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", onClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onClick);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const active = THEMES.find((t) => t.id === theme) ?? THEMES[0];

  return (
    <div className="theme" ref={ref}>
      <button
        className="theme-btn"
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="menu"
        aria-expanded={open}
        title="Change theme"
      >
        <span
          className="theme-btn__swatch"
          style={{
            background: `linear-gradient(135deg, ${active.swatch[0]}, ${active.swatch[1]})`,
          }}
        />
        <span className="theme-btn__label">{active.name}</span>
        <Icon name="chevron" size={13} className="theme-btn__caret" />
      </button>

      {open && (
        <div className="theme-menu" role="menu">
          <div className="theme-menu__head">
            <strong>Themes</strong>
            <span>Choose your preferred theme</span>
          </div>
          {THEMES.map((t) => (
            <button
              key={t.id}
              className={`theme-card${t.id === theme ? " theme-card--active" : ""}`}
              role="menuitemradio"
              aria-checked={t.id === theme}
              onClick={() => {
                setTheme(t.id);
                setOpen(false);
              }}
            >
              <span
                className="theme-card__swatch"
                style={{
                  background: `linear-gradient(135deg, ${t.swatch[0]}, ${t.swatch[1]})`,
                }}
              />
              <span className="theme-card__text">
                <span className="theme-card__name">{t.name}</span>
                <span className="theme-card__desc">{t.description}</span>
              </span>
              {t.id === theme && (
                <Icon name="check" size={16} className="theme-card__check" />
              )}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
