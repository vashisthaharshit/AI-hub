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

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setAgents(await api.agents());
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

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return { agents, loading, error, testingId, refresh, test };
}

/** Agents a task can be sent to. "installed" isn't verified yet but will usually work. */
export function isUsable(agent: AgentStatus): boolean {
  return agent.state === "ready" || agent.state === "installed";
}
