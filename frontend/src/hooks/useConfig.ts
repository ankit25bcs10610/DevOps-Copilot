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

/** Force a refetch and notify all subscribers (call after a config change).
 *  Re-throws on failure so the caller can surface it instead of silently
 *  keeping the stale config. */
export async function refreshConfig(): Promise<AppConfig | null> {
  try {
    _config = await getConfig();
    _failed = false;
    emit();
    return _config;
  } catch (e) {
    _failed = true;
    emit();
    throw e;
  }
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
      // Show "offline" after a few quick tries, but keep retrying forever
      // (slower) so a backend that starts late auto-recovers — no reload needed.
      if (tries >= 3) {
        _failed = true;
        emit();
      }
      setTimeout(attempt, tries <= 10 ? 3000 : 10000);
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

const PROVIDER_LABELS: Record<string, string> = {
  anthropic: "Anthropic",
  openai: "OpenAI",
  gemini: "Google",
  groq: "Groq",
  deepseek: "DeepSeek",
};
export const providerLabel = (p?: string) => (p ? PROVIDER_LABELS[p] ?? p : "—");

/** Pretty short names: "claude-opus-4-8" -> "Opus 4.8", "gpt-4o" -> "GPT-4o",
 *  "gemini-1.5-pro" -> "Gemini 1.5 Pro", "deepseek-chat" -> "DeepSeek Chat". */
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
  if (id.startsWith("gpt-")) return `GPT-${id.slice(4)}`;
  if (id.startsWith("gemini-")) {
    const m = id.match(/gemini-([\d.]+)-(\w+)/);
    if (m) return `Gemini ${m[1]} ${cap(m[2])}`;
  }
  if (id.startsWith("deepseek-")) return `DeepSeek ${cap(id.slice(9))}`;
  return id;
}

const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);
