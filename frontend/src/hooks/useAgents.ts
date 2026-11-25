// Loads the agents list once, and lets the UI re-check or test one agent.
// Lives in App so both the Agents panel and the New task form share it.

import { useCallback, useEffect, useState } from "react";
import { api, errorText } from "../api/client";
import type { AgentStatus } from "../api/types";

export function useAgents() {
  const [agents, setAgents] = useState<AgentStatus[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [testingId, setTestingId] = useState<string | null>(null);

  // force=true makes the backend ask each CLI again for its models and account
  // info ("Re-check"); otherwise the cached answer comes back quickly.
  const refresh = useCallback(async (force = false) => {
    setLoading(true);
    setError(null);
    try {
      setAgents(await api.agents(force));
    } catch (e) {
      setError(`Can't reach the AI-Hub backend: ${errorText(e)}`);
    } finally {
      setLoading(false);
    }
  }, []);

  const test = useCallback(async (id: string) => {
    setTestingId(id);
    setError(null);
    try {
      const updated = await api.testAgent(id);
      setAgents((list) => list.map((a) => (a.id === id ? updated : a)));
    } catch (e) {
      setError(errorText(e));
    } finally {
      setTestingId(null);
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

  return { agents, loading, error, testingId, refresh, test, unhide };
}

/** Agents a task can be sent to. "installed" isn't verified yet but will usually work. */
export function isUsable(agent: AgentStatus): boolean {
  return agent.state === "ready" || agent.state === "installed";
}
