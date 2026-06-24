// Thin, typed client for the DevOps Copilot backend.

import type { AppConfig, ChatResponse, GithubStatus } from "./types";

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

/** Fetch the running agent's provider, models, and MCP server catalog. */
export async function getConfig(): Promise<AppConfig> {
  const res = await fetch(`${BASE_URL}/config`);
  if (!res.ok) throw new Error(`config ${res.status}`);
  return res.json() as Promise<AppConfig>;
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
export async function githubStatus(): Promise<GithubStatus> {
  const res = await fetch(`${BASE_URL}/github/status`);
  if (!res.ok) throw new Error(`github status ${res.status}`);
  return res.json() as Promise<GithubStatus>;
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
