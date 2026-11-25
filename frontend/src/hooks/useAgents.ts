// Loads the agents list once, and lets the UI re-check or test one agent.
// Lives in App so both the Agents panel and the New task form share it.

import { useCallback, useEffect, useState } from "react";
import { api, errorText } from "../api/client";
import type { AgentStatus } from "../api/types";

export function useAgents() {
  const [agents, setAgents] = useState<AgentStatus[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // Several agents can be tested at the same time, so this is a set of ids.
  const [testingIds, setTestingIds] = useState<Set<string>>(new Set());

  // force=true makes the backend ask each CLI again for its models and account
  // info ("Re-check"); otherwise the cached answer comes back quickly.
  // quiet=true: update in the background (e.g. new usage numbers after a run)
  // without showing "Checking...".
  const refresh = useCallback(async (force = false, quiet = false) => {
    if (!quiet) setLoading(true);
    setError(null);
    try {
      setAgents(await api.agents(force));
    } catch (e) {
      setError(`Can't reach the AI-Hub backend: ${errorText(e)}`);
    } finally {
      if (!quiet) setLoading(false);
    }
  }, []);

  const test = useCallback(async (id: string) => {
    setTestingIds((s) => new Set(s).add(id));
    setError(null);
    try {
      const updated = await api.testAgent(id);
      setAgents((list) => list.map((a) => (a.id === id ? updated : a)));
    } catch (e) {
      setError(errorText(e));
    } finally {
      setTestingIds((s) => {
        const next = new Set(s);
        next.delete(id);
        return next;
      });
    }
  }, []);

  const unhide = useCallback(async (id: string) => {
    try {
      const updated = await api.unhideModels(id);
      setAgents((list) => list.map((a) => (a.id === id ? updated : a)));
    } catch (e) {
      setError(errorText(e));
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return { agents, loading, error, testingIds, refresh, test, unhide };
}

/** Agents a task can be sent to. "installed" isn't verified yet but will usually work. */
export function isUsable(agent: AgentStatus): boolean {
  return agent.state === "ready" || agent.state === "installed";
}
