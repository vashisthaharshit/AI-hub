// Turns the flat list of events from the backend into display items.
//
// The backend sends tool_call and tool_result as two separate events.
// For the UI it's nicer to show each tool call as ONE card with its result
// inside, so we pair them up by id here. Pure functions: easy to test.

import type { EventMessage, RunMetrics, RunResult } from "../api/types";

export interface ToolItem {
  type: "tool";
  seq: number;
  id: string | null;
  name: string;
  input: Record<string, unknown>;
  result?: { output: string; isError: boolean };
}

export interface ApprovalItem {
  type: "approval";
  seq: number;
  id: string;
  toolName: string;
  input: Record<string, unknown>;
  decision?: { behavior: "allow" | "deny"; message: string | null };
}

export type Item =
  | { type: "user"; seq: number; text: string; followUp: boolean; model: string | null; effort: string | null }
  | { type: "session"; seq: number; model: string | null }
  | ApprovalItem
  | { type: "checks"; seq: number; metrics: RunMetrics }
  | { type: "text"; seq: number; text: string }
  | ToolItem
  | { type: "result"; seq: number; result: RunResult }
  | { type: "error"; seq: number; message: string }
  | { type: "raw"; seq: number; data: Record<string, unknown> };

export function groupEvents(events: EventMessage[]): Item[] {
  const items: Item[] = [];
  const toolsById = new Map<string, ToolItem>();
  const approvalsById = new Map<string, ApprovalItem>();

  for (const e of events) {
    const d = e.data;
    switch (e.kind) {
      case "user_message":
        items.push({
          type: "user",
          seq: e.seq,
          text: String(d.text ?? ""),
          followUp: Boolean(d.follow_up),
          model: (d.model as string) ?? null,
          effort: (d.effort as string) ?? null,
        });
        break;
      case "approval_request": {
        const approval: ApprovalItem = {
          type: "approval",
          seq: e.seq,
          id: String(d.id),
          toolName: String(d.tool_name ?? "tool"),
          input: (d.input as Record<string, unknown>) ?? {},
        };
        approvalsById.set(approval.id, approval);
        items.push(approval);
        break;
      }
      case "approval_resolved": {
        const approval = approvalsById.get(String(d.id));
        if (approval) {
          approval.decision = {
            behavior: d.behavior === "allow" ? "allow" : "deny",
            message: (d.message as string) ?? null,
          };
        }
        break;
      }
      case "checks":
        items.push({ type: "checks", seq: e.seq, metrics: d as unknown as RunMetrics });
        break;
      case "session_started":
        items.push({ type: "session", seq: e.seq, model: (d.model as string) ?? null });
        break;
      case "text":
        items.push({ type: "text", seq: e.seq, text: String(d.text ?? "") });
        break;
      case "tool_call": {
        const tool: ToolItem = {
          type: "tool",
          seq: e.seq,
          id: (d.id as string) ?? null,
          name: String(d.name ?? "tool"),
          input: (d.input as Record<string, unknown>) ?? {},
        };
        if (tool.id) toolsById.set(tool.id, tool);
        items.push(tool);
        break;
      }
      case "tool_result": {
        const result = { output: String(d.output ?? ""), isError: Boolean(d.is_error) };
        const tool = toolsById.get(String(d.tool_use_id));
        if (tool) {
          tool.result = result;
        } else {
          items.push({ type: "tool", seq: e.seq, id: null, name: "Tool result", input: {}, result });
        }
        break;
      }
      case "result":
        items.push({ type: "result", seq: e.seq, result: d as unknown as RunResult });
        break;
      case "error":
        items.push({ type: "error", seq: e.seq, message: String(d.message ?? "Unknown error") });
        break;
      default:
        items.push({ type: "raw", seq: e.seq, data: d });
    }
  }
  return items;
}

/** Approvals still waiting for the user (no decision yet). */
export function pendingApprovals(items: Item[]): ApprovalItem[] {
  return items.filter((i): i is ApprovalItem => i.type === "approval" && !i.decision);
}

/** One short line describing what a tool call did, e.g. "src/app.py" or "npm test". */
export function summarizeToolInput(input: Record<string, unknown>): string {
  for (const key of ["file_path", "path", "command", "pattern", "url", "description"]) {
    const value = input[key];
    if (typeof value === "string" && value) return value;
  }
  const json = JSON.stringify(input);
  return json === "{}" ? "" : json;
}

// ---------- formatting helpers ----------

export function formatCost(usd: number | null | undefined): string {
  if (usd == null) return "-";
  return usd < 0.01 ? `$${usd.toFixed(4)}` : `$${usd.toFixed(2)}`;
}

export function formatDuration(ms: number | null | undefined): string {
  if (ms == null) return "-";
  const s = ms / 1000;
  return s < 60 ? `${s.toFixed(1)}s` : `${Math.floor(s / 60)}m ${Math.round(s % 60)}s`;
}

export function formatTokens(n: number | null | undefined): string {
  if (n == null) return "-";
  return n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n);
}

export function timeAgo(iso: string, now: Date = new Date()): string {
  const seconds = Math.max(0, (now.getTime() - new Date(iso).getTime()) / 1000);
  if (seconds < 60) return "just now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return `${Math.floor(seconds / 86400)}d ago`;
}
