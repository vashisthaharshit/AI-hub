import { describe, expect, it } from "vitest";
import type { EventMessage } from "../api/types";
import { formatCost, formatDuration, groupEvents, pendingApprovals, summarizeToolInput } from "./events";

const ev = (seq: number, kind: EventMessage["kind"], data: Record<string, unknown>): EventMessage => ({
  type: "event",
  seq,
  kind,
  data,
});

describe("groupEvents", () => {
  it("attaches a tool result to its tool call", () => {
    const items = groupEvents([
      ev(0, "text", { text: "Let me read it" }),
      ev(1, "tool_call", { id: "t1", name: "Read", input: { file_path: "a.py" } }),
      ev(2, "tool_result", { tool_use_id: "t1", output: "print(1)", is_error: false }),
    ]);
    expect(items.map((i) => i.type)).toEqual(["text", "tool"]);
    const tool = items[1];
    expect(tool.type === "tool" && tool.result?.output).toBe("print(1)");
  });

  it("keeps an orphan tool result as its own item", () => {
    const items = groupEvents([ev(0, "tool_result", { tool_use_id: "nope", output: "x" })]);
    expect(items).toHaveLength(1);
    expect(items[0].type).toBe("tool");
  });

  it("maps result, error and unknown events", () => {
    const items = groupEvents([
      ev(0, "result", { success: true, cost_usd: 0.02 }),
      ev(1, "error", { message: "boom" }),
      ev(2, "raw", { type: "new_thing" }),
    ]);
    expect(items.map((i) => i.type)).toEqual(["result", "error", "raw"]);
  });
});

describe("new event kinds", () => {
  it("shows user messages, including follow-ups", () => {
    const items = groupEvents([
      ev(0, "user_message", { text: "Add a test", follow_up: false }),
      ev(5, "user_message", { text: "Now fix lint", follow_up: true }),
    ]);
    expect(items.map((i) => i.type === "user" && i.followUp)).toEqual([false, true]);
  });

  it("attaches the decision to its approval request", () => {
    const items = groupEvents([
      ev(0, "approval_request", { id: "a1", tool_name: "Bash", input: { command: "npm i" } }),
      ev(1, "approval_request", { id: "a2", tool_name: "Write", input: {} }),
      ev(2, "approval_resolved", { id: "a1", behavior: "deny", message: "no installs" }),
    ]);
    expect(items).toHaveLength(2);
    const first = items[0];
    expect(first.type === "approval" && first.decision?.behavior).toBe("deny");
    expect(pendingApprovals(items).map((a) => a.id)).toEqual(["a2"]);
  });

  it("maps checks", () => {
    const [item] = groupEvents([ev(0, "checks", { files_changed: 2, tests_passed: true })]);
    expect(item.type === "checks" && item.metrics.files_changed).toBe(2);
  });
});

describe("helpers", () => {
  it("summarizes tool input with the most useful field", () => {
    expect(summarizeToolInput({ file_path: "src/app.py", content: "..." })).toBe("src/app.py");
    expect(summarizeToolInput({ command: "npm test" })).toBe("npm test");
    expect(summarizeToolInput({})).toBe("");
  });

  it("formats numbers for people", () => {
    expect(formatCost(0.004)).toBe("$0.0040");
    expect(formatCost(1.5)).toBe("$1.50");
    expect(formatDuration(1500)).toBe("1.5s");
    expect(formatDuration(125000)).toBe("2m 5s");
  });
});
