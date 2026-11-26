// Colors that carry meaning: each workspace has its own, each agent too.

import type { CSSProperties } from "react";
import type { WorkspaceColor } from "../api/types";

export const WORKSPACE_COLORS: Record<WorkspaceColor, string> = {
  sky: "#8e9bff",
  violet: "#b48cff",
  emerald: "#4ade9b",
  amber: "#f2b25a",
  rose: "#ff7a9a",
  cyan: "#4fd6e8",
  fuchsia: "#e879f9",
  lime: "#a3e635",
};

export const AGENT_COLORS: Record<string, string> = {
  "claude-code": "#f2a65a",
  codex: "#58e0c6",
};

export const agentColor = (id: string | null | undefined) => AGENT_COLORS[id ?? ""] ?? "#8e9bff";
export const wsColor = (c: WorkspaceColor | string | null | undefined) =>
  WORKSPACE_COLORS[(c ?? "sky") as WorkspaceColor] ?? WORKSPACE_COLORS.sky;

/** style={tint(color)} sets --tint for .choice, .ws-card, .swatch, .plan-node */
export const tint = (color: string): CSSProperties => ({ ["--tint" as string]: color }) as CSSProperties;

export function timeAgoShort(iso: string | null | undefined): string {
  if (!iso) return "never";
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  const d = Math.floor(s / 86400);
  return d === 1 ? "yesterday" : `${d} days ago`;
}
