// Model + effort pickers for one agent, filled from what the agent's own CLI
// reports for this account (see the backend's discover()). Used by the New
// task form and next to the follow-up box.
//
// "" means "default": no --model / --effort flag is passed, so the agent
// uses its own default (shown as the first option's label).

import type { AgentStatus, ModelOption } from "../api/types";

interface Props {
  agent: AgentStatus;
  model: string;
  effort: string;
  onChange: (model: string, effort: string) => void;
  compact?: boolean; // the small version next to the follow-up box
}

export function ModelPicker({ agent, model, effort, onChange, compact = false }: Props) {
  if (agent.models.length === 0) {
    return <p className="text-xs text-zinc-500">{agent.name} uses its default model.</p>;
  }
  const selected: ModelOption | undefined = agent.models.find((m) => m.id === model);
  // "Default" is a real model too (e.g. Opus 5.5), so it has effort levels.
  const efforts = selected ? selected.efforts : agent.default_efforts;
  const defaultEffort = selected ? selected.default_effort : agent.default_effort;
  const field = `rounded-md border border-zinc-700 bg-zinc-900 px-3 text-sm ${compact ? "py-1.5" : "py-2"}`;

  return (
    <div className="flex flex-col gap-1.5">
      <div className={`flex gap-2 ${compact ? "flex-col" : ""}`}>
        <select
          aria-label="Model"
          value={model}
          // A new model starts on its own default effort.
          onChange={(e) => onChange(e.target.value, "")}
          title="Model for this message and the ones after it"
          className={`${field} min-w-0 flex-1`}
        >
          <option value="">{agent.default_label ?? "Default model"}</option>
          {agent.models.map((m) => (
            <option key={m.id} value={m.id}>
              {m.label}
              {m.note ? " (retiring)" : ""}
            </option>
          ))}
        </select>
        {efforts.length > 0 && (
          <select
            aria-label="Effort"
            value={effort}
            onChange={(e) => onChange(model, e.target.value)}
            title="How much the model thinks: higher is better on hard tasks, but slower and uses more"
            className={`${field} ${compact ? "" : "w-44"}`}
          >
            <option value="">
              {defaultEffort ? `Effort: default (${defaultEffort})` : "Effort: default"}
            </option>
            {efforts.map((level) => (
              <option key={level} value={level}>
                Effort: {level}
              </option>
            ))}
          </select>
        )}
      </div>
      {!compact && selected?.description && <p className="text-xs text-zinc-500">{selected.description}</p>}
      {!compact && selected?.note && <p className="text-xs text-amber-300">{selected.note}</p>}
    </div>
  );
}
