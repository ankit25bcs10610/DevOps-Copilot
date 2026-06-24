import { useCallback, useRef, useState } from "react";

import * as api from "../api";
import type { ChatResponse, Turn } from "../types";

const newId = () =>
  typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : Math.random().toString(36).slice(2);

/**
 * Owns the whole conversation: a stable thread_id (so the backend checkpointer
 * can pause/resume across requests), the list of turns, and the approval flow.
 */
export function useCopilot() {
  const threadId = useRef(`web-${newId()}`).current;
  const [turns, setTurns] = useState<Turn[]>([]);
  const [busy, setBusy] = useState(false);

  const patch = useCallback((id: string, update: Partial<Turn>) => {
    setTurns((prev) => prev.map((t) => (t.id === id ? { ...t, ...update } : t)));
  }, []);

  const applyResponse = useCallback(
    (assistantId: string, res: ChatResponse) => {
      patch(assistantId, {
        text:
          res.status === "awaiting_approval"
            ? res.approval_request?.message ?? "Awaiting approval…"
            : res.answer,
        trace: res.trace,
        status: res.status,
        approval: res.approval_request,
      });
    },
    [patch]
  );

  /** Send a new question. */
  const send = useCallback(
    async (message: string) => {
      const userTurn: Turn = {
        id: newId(),
        role: "user",
        text: message,
        trace: [],
        status: "completed",
        approval: null,
      };
      const assistantTurn: Turn = {
        id: newId(),
        role: "assistant",
        text: "",
        trace: [],
        status: "thinking",
        approval: null,
      };
      setTurns((prev) => [...prev, userTurn, assistantTurn]);
      setBusy(true);
      try {
        const res = await api.chat(threadId, message);
        applyResponse(assistantTurn.id, res);
      } catch (e) {
        // Surface transport failures (e.g. backend down) as a visible error turn.
        patch(assistantTurn.id, {
          status: "error",
          text: e instanceof Error ? e.message : String(e),
        });
      } finally {
        setBusy(false);
      }
    },
    [threadId, applyResponse, patch]
  );

  /** Approve or reject the pending write action on a given turn. */
  const respond = useCallback(
    async (turnId: string, approved: boolean, reason = "") => {
      patch(turnId, { status: "thinking", approval: null });
      setBusy(true);
      try {
        const res = await api.approve(threadId, approved, reason);
        applyResponse(turnId, res);
      } catch (e) {
        // Don't leave the turn stuck on the spinner — show the error.
        patch(turnId, {
          status: "error",
          text: e instanceof Error ? e.message : String(e),
        });
      } finally {
        setBusy(false);
      }
    },
    [threadId, applyResponse, patch]
  );

  // Block new input while a turn is paused for approval (the thread is mid-graph).
  const awaitingApproval = turns.some((t) => t.status === "awaiting_approval");

  return { turns, busy, awaitingApproval, send, respond };
}
