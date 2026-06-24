import { useEffect, useState } from "react";

import { getConfig } from "../api";
import type { AppConfig } from "../types";

// Module-level cache so Header + Sidebar share a single /config request.
let cache: Promise<AppConfig> | null = null;

export function useConfig(): AppConfig | null {
  const [cfg, setCfg] = useState<AppConfig | null>(null);

  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout>;

    const attempt = (retriesLeft: number) => {
      if (!cache) cache = getConfig();
      cache
        .then((c) => active && setCfg(c))
        .catch(() => {
          cache = null; // drop the rejected promise so the next attempt refetches
          if (active && retriesLeft > 0) {
            timer = setTimeout(() => attempt(retriesLeft - 1), 3000);
          }
        });
    };

    attempt(10); // recover if the backend wasn't up yet (≈30s of retries)
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, []);

  return cfg;
}

export const providerLabel = (p?: string) =>
  p === "anthropic" ? "Anthropic" : p === "groq" ? "Groq" : p ?? "—";

/** "claude-opus-4-8" -> "Opus 4.8"; "llama-3.3-70b-versatile" -> "Llama 3.3 70B". */
export function modelShort(id?: string): string {
  if (!id) return "";
  if (id.startsWith("claude-")) {
    const m = id.match(/claude-([a-z]+)-(\d+)-(\d+)/);
    if (m) return `${cap(m[1])} ${m[2]}.${m[3]}`;
  }
  if (id.startsWith("llama-")) {
    const m = id.match(/llama-([\d.]+)-(\d+b)/);
    if (m) return `Llama ${m[1]} ${m[2].toUpperCase()}`;
  }
  return id;
}

const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);
