// Mirrors the FastAPI response contract (app/api/main.py).

export type TurnStatus = "completed" | "awaiting_approval" | "error";

export interface ProposedAction {
  tool: string;
  args: Record<string, unknown>;
  write?: boolean;
}

export interface ApprovalRequest {
  type: string;
  message: string;
  actions: ProposedAction[];
}

export interface ChatResponse {
  thread_id: string;
  status: TurnStatus;
  answer: string;
  approval_request: ApprovalRequest | null;
  trace: string[];
}

export interface McpServer {
  name: string;
  label: string;
  custom?: boolean;
  tools: string[];
}

export interface AppConfig {
  provider: string;
  model: string;
  fast_model: string;
  offline_mode: boolean;
  servers: McpServer[];
}

// --- UI-side model ---

export type Role = "user" | "assistant";

export interface Turn {
  id: string;
  role: Role;
  text: string;
  trace: string[];
  // "thinking" is a transient UI state while a request is in flight.
  status: TurnStatus | "thinking";
  approval: ApprovalRequest | null;
}
