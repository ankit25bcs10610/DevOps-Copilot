// Mirrors the FastAPI response contract (app/api/main.py).

export type TurnStatus = "completed" | "awaiting_approval" | "error";

export type RiskTier = "low" | "medium" | "high";
export type ActionDecision = "allow" | "notify" | "approve";

export interface ProposedAction {
  tool: string;
  args: Record<string, unknown>;
  write?: boolean;
  decision?: ActionDecision;
  risk?: RiskTier;
  why?: string;
  preview?: string;
}

export interface ApprovalRequest {
  type: string;
  message: string;
  risk?: RiskTier;
  evidence_count?: number;
  confidence?: Confidence;
  // Confidence gate: a programmatic approver would be refused this write (thin
  // evidence for its risk). A human can still approve, but should scrutinize.
  auto_approve_blocked?: boolean;
  gate_reason?: string;
  actions: ProposedAction[];
}

// Structured RCA deliverable produced when an investigation completes.
export type Severity = "SEV1" | "SEV2" | "SEV3" | "SEV4" | "INFO";
export type Confidence = "high" | "medium" | "low";
export type Verdict = "validated" | "invalidated" | "inconclusive";

export interface Hypothesis {
  cause: string;
  verdict: Verdict;
  confidence: Confidence;
  evidence: string[];
}

export type VerifyVerdict = "verified" | "unverified" | "inconclusive" | "no_fix_proposed";

export type SandboxVerdict =
  | "resolved"
  | "not_resolved"
  | "no_repro"
  | "patch_failed"
  | "no_patch"
  | "error";

// Sandbox counterfactual result: the proposed patch applied to a throwaway repo
// copy and a reproducer run before/after (set server-side by the verify node).
export interface Sandbox {
  verdict: SandboxVerdict;
  detail: string;
  applied?: boolean;
  baseline_failed?: boolean | null;
  patched_passed?: boolean | null;
}

// Fix-verification result (set server-side by the verify node): does the proposed
// remediation address the root cause, and what signal confirms resolution.
export interface Verification {
  verdict: VerifyVerdict;
  addresses_cause: boolean;
  confidence: Confidence;
  resolution_criteria: string[];
  residual_risks: string[];
  rationale: string;
  sandbox?: Sandbox;
}

export type CritiqueVerdict = "upheld" | "weakened" | "refuted";

// Adversarial RCA critique (prosecutor/defender panel, set server-side).
export interface Critique {
  verdict: CritiqueVerdict;
  standing_objections?: { claim: string; severity: Confidence }[];
}

export interface RcaReport {
  summary: string;
  severity: Severity;
  confidence: Confidence;
  root_cause: string | null;
  affected_services: string[];
  hypotheses: Hypothesis[];
  evidence: string[];
  recommended_actions: string[];
  postmortem: string; // rendered Markdown
  // Deterministic calibration (set server-side by the report node).
  calibrated_confidence?: Confidence;
  abstained?: boolean;
  needs?: string[];
  // Fix verification (set server-side by the verify node).
  verification?: Verification;
  // Adversarial critique (set server-side by the report node).
  critique?: Critique;
}

export interface ChatResponse {
  thread_id: string;
  status: TurnStatus;
  answer: string;
  approval_request: ApprovalRequest | null;
  trace: string[];
  report?: RcaReport | null;
}

// One Server-Sent event from /chat/stream or /approve/stream.
export interface StreamEvent {
  type: "trace" | "approval" | "done" | "error";
  thread_id?: string;
  line?: string; // trace line
  status?: TurnStatus;
  answer?: string; // done / error
  approval_request?: ApprovalRequest | null;
  trace?: string[];
  report?: RcaReport | null;
  tokens_used?: number;
}

// /metrics response (real series from the logs/metrics source).
export interface MetricPoint {
  ts: string;
  value: number;
}
export interface MetricsResponse {
  services: Record<string, Record<string, MetricPoint[]>>;
  error_summary: { total_errors: number; breakdown: { message: string; count: number }[] };
}

export interface McpServer {
  name: string;
  label: string;
  custom?: boolean;
  tools: string[];
}

export interface GithubStatus {
  connected: boolean;
  repo: string | null;
  mode: "live" | "offline";
  full_name?: string;
  private?: boolean;
}

export interface AppConfig {
  provider: string;
  model: string;
  fast_model: string;
  offline_mode: boolean;
  servers: McpServer[];
  github: GithubStatus;
  sources: { repo_path: string; logs_path: string };
  has_key: boolean;
}

// --- Admin / multi-tenant ---

export interface UsageSummary {
  multi_tenant?: boolean;
  plan?: string;
  period?: string;
  investigations_used?: number;
  investigations_quota?: number;
  investigations_remaining?: number;
  tokens_used?: number;
  warning?: boolean;
}

export interface AdminOrg {
  id: string;
  name: string;
  plan: string;
  members: number;
  active_api_keys: number;
  integrations: number;
}

export interface ApiKeyInfo {
  id: string;
  prefix: string;
  name: string;
  role: string;
  created_at: string;
  last_used_at: string;
  active: boolean;
}

export interface AuditEvent {
  ts: string;
  event: string;
  org_id: string;
  actor: string;
  request_id: string;
  [k: string]: unknown;
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
  report?: RcaReport | null;
  tokensUsed?: number;
}
