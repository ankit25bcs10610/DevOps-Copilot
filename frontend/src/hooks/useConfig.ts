import { useEffect, useReducer } from "react";

import { getConfig } from "../api";
import type { AppConfig } from "../types";

// Shared, subscribable config store so every consumer (header badge, model
// card, source cards) stays in sync — and any mutation can refresh them all.
let _config: AppConfig | null = null;
let _failed = false;
let _started = false;
const subs = new Set<() => void>();

const emit = () => subs.forEach((f) => f());

/** Force a refetch and notify all subscribers (call after a config change). */
export async function refreshConfig(): Promise<AppConfig | null> {
  try {
    _config = await getConfig();
    _failed = false;
  } catch {
    _failed = true;
  }
  emit();
  return _config;
}

function ensureStarted() {
  if (_started) return;
  _started = true;
  let tries = 0;
  const attempt = async () => {
    try {
      _config = await getConfig();
      _failed = false;
      emit();
    } catch {
      tries += 1;
      if (tries <= 10) {
        setTimeout(attempt, 3000); // recover if the backend wasn't up yet
      } else {
        _failed = true;
        emit();
      }
    }
  };
  attempt();
}

export interface ConfigState {
  config: AppConfig | null;
  failed: boolean;
  refresh: () => Promise<AppConfig | null>;
}

export function useConfig(): ConfigState {
  const [, force] = useReducer((c: number) => c + 1, 0);
  useEffect(() => {
    subs.add(force);
    ensureStarted();
    return () => {
      subs.delete(force);
    };
  }, []);
  return { config: _config, failed: _failed, refresh: refreshConfig };
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
