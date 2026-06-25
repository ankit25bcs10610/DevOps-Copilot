// Thin, typed client for the DevOps Copilot backend.

import type { AppConfig, ChatResponse, GithubStatus, MetricsResponse, StreamEvent } from "./types";

const BASE_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8000";
// Optional bearer token (set VITE_API_TOKEN when the backend has COPILOT_API_TOKEN).
const TOKEN = import.meta.env.VITE_API_TOKEN ?? "";

function authHeaders(extra: Record<string, string> = {}): Record<string, string> {
  return TOKEN ? { ...extra, Authorization: `Bearer ${TOKEN}` } : { ...extra };
}

async function post<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(`${res.status} ${res.statusText}: ${detail}`);
  }
  return res.json() as Promise<T>;
}

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, { headers: authHeaders() });
  if (!res.ok) throw new Error(`${path} ${res.status}`);
  return res.json() as Promise<T>;
}

/** POST that consumes a Server-Sent Events stream, invoking `onEvent` per message.
 *  Pass an AbortSignal to cancel mid-stream (the Stop button) — aborting the fetch
 *  disconnects the SSE, which cancels the agent run server-side. */
async function streamPost(
  path: string,
  body: unknown,
  onEvent: (ev: StreamEvent) => void,
  signal?: AbortSignal
): Promise<void> {
  const res = await fetch(`${BASE_URL}${path}`, {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(body),
    signal,
  });
  if (!res.ok || !res.body) {
    const detail = await res.text().catch(() => "");
    throw new Error(`${res.status} ${res.statusText}: ${detail}`);
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const frames = buffer.split("\n\n");
    buffer = frames.pop() ?? ""; // keep the partial frame
    for (const frame of frames) {
      const data = frame
        .split("\n")
        .filter((l) => l.startsWith("data:"))
        .map((l) => l.slice(5).replace(/^ /, ""))
        .join("\n");
      if (!data) continue; // skip pings / comments
      try {
        onEvent(JSON.parse(data) as StreamEvent);
      } catch {
        /* ignore non-JSON keep-alives */
      }
    }
  }
}

/** Start an investigation, streaming trace + final answer events. */
export function chatStream(
  threadId: string,
  message: string,
  onEvent: (ev: StreamEvent) => void,
  signal?: AbortSignal
): Promise<void> {
  return streamPost("/chat/stream", { thread_id: threadId, message }, onEvent, signal);
}

/** Resume a paused investigation, streaming the continuation. */
export function approveStream(
  threadId: string,
  approved: boolean,
  reason: string,
  onEvent: (ev: StreamEvent) => void,
  signal?: AbortSignal
): Promise<void> {
  return streamPost(
    "/approve/stream",
    { thread_id: threadId, approved, reason },
    onEvent,
    signal
  );
}

/** Non-streaming fallbacks (also used by tools/tests). */
export function chat(threadId: string, message: string): Promise<ChatResponse> {
  return post<ChatResponse>("/chat", { thread_id: threadId, message });
}
export function approve(
  threadId: string,
  approved: boolean,
  reason = ""
): Promise<ChatResponse> {
  return post<ChatResponse>("/approve", { thread_id: threadId, approved, reason });
}

/** Real metric series + error summary for the dashboard charts. */
export function getMetrics(): Promise<MetricsResponse> {
  return get<MetricsResponse>("/metrics");
}

/** Submit thumbs up/down on an investigation (feeds the eval/learning loop). */
export function submitFeedback(
  threadId: string,
  rating: "up" | "down",
  comment = "",
  question = ""
): Promise<unknown> {
  return post("/feedback", { thread_id: threadId, rating, comment, question });
}

/** Fetch the running agent's provider, models, and MCP server catalog. */
export function getConfig(): Promise<AppConfig> {
  return get<AppConfig>("/config");
}

/** Switch LLM provider/model/key at runtime. */
export function configureModel(
  provider: string,
  api_key: string,
  model: string,
  fast_model: string
): Promise<{ provider: string; model: string; fast_model: string }> {
  return post("/model/configure", { provider, api_key, model, fast_model });
}

/** Point the repo MCP server at a local directory. */
export function connectRepo(path: string): Promise<{ repo_path: string }> {
  return post("/sources/repo", { path });
}

/** Point the logs/metrics MCP server at a local directory. */
export function connectLogs(
  path: string
): Promise<{ logs_path: string; missing_files: string[] }> {
  return post("/sources/logs", { path });
}

/** Revert all runtime overrides to .env defaults. */
export function resetConfig(): Promise<unknown> {
  return post("/reset", {});
}

/** Current GitHub connection state. */
export function githubStatus(): Promise<GithubStatus> {
  return get<GithubStatus>("/github/status");
}

/** Connect a GitHub repo (validated server-side against the real API). */
export function githubConnect(token: string, repo: string): Promise<GithubStatus> {
  return post<GithubStatus>("/github/connect", { token, repo });
}

/** Disconnect GitHub and revert to offline-demo mode. */
export function githubDisconnect(): Promise<GithubStatus> {
  return post<GithubStatus>("/github/disconnect", {});
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
