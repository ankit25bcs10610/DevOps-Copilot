import { useCallback, useEffect, useState } from "react";

import { DEFAULT_THEME } from "../themes";

const KEY = "copilot-theme";

function read(): string {
  try {
    return localStorage.getItem(KEY) || DEFAULT_THEME;
  } catch {
    return DEFAULT_THEME;
  }
}

/** Applies the selected theme to <html data-theme="..."> and persists it. */
export function useTheme() {
  const [theme, setThemeState] = useState<string>(read);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);

  const setTheme = useCallback((id: string) => {
    setThemeState(id);
    try {
      localStorage.setItem(KEY, id);
    } catch {
      /* ignore storage failures (private mode, etc.) */
    }
  }, []);

  return { theme, setTheme };
}
