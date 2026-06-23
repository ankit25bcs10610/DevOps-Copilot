// Thin, typed client for the DevOps Copilot backend.

import type { ChatResponse } from "./types";

const BASE_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

async function post<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(`${res.status} ${res.statusText}: ${detail}`);
  }
  return res.json() as Promise<T>;
}

/** Start (or continue) an investigation on a thread. */
export function chat(threadId: string, message: string): Promise<ChatResponse> {
  return post<ChatResponse>("/chat", { thread_id: threadId, message });
}

/** Resume a paused investigation with the human's decision. */
export function approve(
  threadId: string,
  approved: boolean,
  reason = ""
): Promise<ChatResponse> {
  return post<ChatResponse>("/approve", {
    thread_id: threadId,
    approved,
    reason,
  });
}

/** Liveness check used by the header status dot. */
export async function health(): Promise<boolean> {
  try {
    const res = await fetch(`${BASE_URL}/healthz`);
    return res.ok;
  } catch {
    return false;
  }
}
