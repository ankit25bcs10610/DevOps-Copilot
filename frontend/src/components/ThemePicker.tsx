import { type KeyboardEvent, useEffect, useRef, useState } from "react";

import { useTheme } from "../hooks/useTheme";
import { THEMES } from "../themes";
import { Icon } from "./Icon";

export function ThemePicker() {
  const { theme, setTheme } = useTheme();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const itemRefs = useRef<(HTMLButtonElement | null)[]>([]);

  const activeIndex = Math.max(0, THEMES.findIndex((t) => t.id === theme));

  // Close on outside click.
  useEffect(() => {
    if (!open) return;
    const onClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, [open]);

  // Move focus into the menu (onto the active item) when it opens.
  useEffect(() => {
    if (open) itemRefs.current[activeIndex]?.focus();
  }, [open, activeIndex]);

  const close = (restoreFocus = true) => {
    setOpen(false);
    if (restoreFocus) triggerRef.current?.focus();
  };

  // ARIA menu keyboard pattern: Up/Down (wrapping), Home/End, Escape.
  const onMenuKeyDown = (e: KeyboardEvent) => {
    const last = THEMES.length - 1;
    const cur = itemRefs.current.findIndex((el) => el === document.activeElement);
    const focus = (i: number) => itemRefs.current[i]?.focus();
    if (e.key === "Escape") {
      e.preventDefault();
      close();
    } else if (e.key === "ArrowDown") {
      e.preventDefault();
      focus(cur >= last ? 0 : cur + 1);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      focus(cur <= 0 ? last : cur - 1);
    } else if (e.key === "Home") {
      e.preventDefault();
      focus(0);
    } else if (e.key === "End") {
      e.preventDefault();
      focus(last);
    }
  };

  const active = THEMES.find((t) => t.id === theme) ?? THEMES[0];

  return (
    <div className="theme" ref={ref}>
      <button
        ref={triggerRef}
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
        <div className="theme-menu" role="menu" aria-label="Theme" onKeyDown={onMenuKeyDown}>
          <div className="theme-menu__head">
            <strong>Themes</strong>
            <span>Choose your preferred theme</span>
          </div>
          {THEMES.map((t, i) => (
            <button
              key={t.id}
              ref={(el) => {
                itemRefs.current[i] = el;
              }}
              className={`theme-card${t.id === theme ? " theme-card--active" : ""}`}
              role="menuitemradio"
              aria-checked={t.id === theme}
              tabIndex={-1}
              onClick={() => {
                setTheme(t.id);
                close();
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
