export type WorkspaceStatus = "active" | "archived";
export type ConversationStatus = "active" | "archived";
export type RunRisk = "low" | "medium" | "high" | "critical";
export type ApprovalStatus =
  | "pending"
  | "approved"
  | "rejected"
  | "expired"
  | "invalidated"
  | "consumed";
export type RunStatus =
  | "received"
  | "planning"
  | "needs_clarification"
  | "ready"
  | "awaiting_approval"
  | "executing"
  | "verifying"
  | "succeeded"
  | "partially_succeeded"
  | "failed"
  | "denied"
  | "cancelled";

export interface User {
  id: string;
  display_name: string;
  locale: string;
  timezone: string;
  created_at: string;
  updated_at: string;
}

export interface Workspace {
  id: string;
  owner_id: string;
  name: string;
  status: WorkspaceStatus;
  created_at: string;
  updated_at: string;
}

export interface SetupState {
  user: User;
  workspace: Workspace;
  created: boolean;
}

export interface Conversation {
  id: string;
  workspace_id: string;
  title: string | null;
  status: ConversationStatus;
  summary: string | null;
  created_at: string;
  updated_at: string;
}

export interface MessagePart {
  type: "text" | "tool_call" | "tool_result" | "artifact";
  text: string | null;
  data: Record<string, unknown> | null;
}

export interface Message {
  id: string;
  conversation_id: string;
  role: "user" | "assistant" | "tool";
  parts: MessagePart[];
  created_at: string;
}

export interface Run {
  id: string;
  workspace_id: string;
  conversation_id: string;
  request_message_id: string | null;
  trace_id: string;
  status: RunStatus;
  risk: RunRisk;
  autonomy_level: number;
  error_code: string | null;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface PlanStep {
  id: string;
  run_id: string;
  position: number;
  title: string;
  status: "pending" | "in_progress" | "succeeded" | "failed" | "skipped" | "cancelled";
  risk: RunRisk;
  requires_approval: boolean;
  tool_name: string | null;
  operation: string | null;
  tool_input: Record<string, unknown> | null;
  created_at: string;
  updated_at: string;
}

export interface ToolCall {
  id: string;
  run_id: string;
  plan_step_id: string;
  tool_name: string;
  operation: string;
  input: Record<string, unknown>;
  output: Record<string, unknown> | null;
  status: "pending" | "executing" | "succeeded" | "failed" | "cancelled";
  risk: RunRisk;
  idempotency_key: string;
  error_code: string | null;
  attempt_count: number;
  max_attempts: number;
  available_at: string;
  lease_expires_at: string | null;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface Task {
  id: string;
  workspace_id: string;
  title: string;
  description: string | null;
  status: "pending" | "in_progress" | "completed" | "cancelled";
  priority: "low" | "normal" | "high";
  due_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface ApprovalRequest {
  id: string;
  run_id: string;
  operation: string;
  summary: string;
  risk: RunRisk;
  action_fingerprint: string;
  status: ApprovalStatus;
  decision_note: string | null;
  requested_at: string;
  expires_at: string;
  decided_at: string | null;
}
