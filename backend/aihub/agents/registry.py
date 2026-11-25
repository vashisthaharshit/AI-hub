"""The list of agents AI-Hub knows about, and what we know about each.

To add an agent later: write its adapter, then add it to default_registry().
The Agents panel, the API and (later) the router all read from here.

The registry also caches two things, because getting them is slow:
- discovery: the models + account info each CLI reports (starts the CLI)
- the result of the last "Test connection" (sends a real prompt)
"Re-check" in the UI refreshes discovery.
"""

import asyncio

from aihub.agents.base import AgentAdapter
from aihub.agents.claude_code import ClaudeCodeAdapter
from aihub.agents.codex import CodexAdapter
from aihub.models import AgentStatus, Discovery


class AgentRegistry:
    def __init__(self, adapters: list[AgentAdapter]):
        self._adapters = {a.id: a for a in adapters}
        self._discovery: dict[str, Discovery] = {}
        self._tested: dict[str, AgentStatus] = {}

    def get(self, agent_id: str) -> AgentAdapter:
        try:
            return self._adapters[agent_id]
        except KeyError:
            raise KeyError(f"Unknown agent '{agent_id}'") from None

    def all(self) -> list[AgentAdapter]:
        return list(self._adapters.values())

    async def scan(self, refresh: bool = False, hidden: dict[str, set[str]] | None = None) -> list[AgentStatus]:
        """Status of every agent. Checks all agents at the same time."""
        hidden = hidden or {}
        return list(await asyncio.gather(*(
            self.status_of(a.id, refresh, hidden.get(a.id, set())) for a in self._adapters.values()
        )))

    async def status_of(self, agent_id: str, refresh: bool = False, hidden: set[str] | None = None) -> AgentStatus:
        adapter = self.get(agent_id)
        status = await adapter.detect()
        if status.state == "not_installed":
            self._discovery.pop(agent_id, None)
            self._tested.pop(agent_id, None)
            return status

        # Keep the answer of the last "Test connection" (detect alone can't know it).
        tested = self._tested.get(agent_id)
        if status.state == "installed" and tested:
            status = status.model_copy(update={"state": tested.state, "help": tested.help})

        discovery = self._discovery.get(agent_id)
        if discovery is None or refresh:
            discovery = await adapter.discover()
            self._discovery[agent_id] = discovery

        hidden = hidden or set()
        return status.model_copy(update={
            "models": [m for m in discovery.models if m.id not in hidden],
            "default_label": discovery.default_label,
            "default_efforts": discovery.default_efforts,
            "default_effort": discovery.default_effort,
            "account": discovery.account,
            "hidden_models": sorted(hidden),
            "discovery_error": discovery.error,
        })

    def remember_test(self, status: AgentStatus) -> None:
        if status.state in ("ready", "not_signed_in"):
            self._tested[status.id] = status
        else:
            self._tested.pop(status.id, None)


def default_registry() -> AgentRegistry:
    return AgentRegistry([
        ClaudeCodeAdapter(),
        CodexAdapter(),
    ])
