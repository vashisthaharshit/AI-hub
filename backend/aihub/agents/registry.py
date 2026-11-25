"""The list of agents AI-Hub knows about.

To add an agent later: write its adapter, then add it to default_registry().
The Agents panel, the API and (later) the router all read from here.
"""

import asyncio

from aihub.agents.base import AgentAdapter
from aihub.agents.claude_code import ClaudeCodeAdapter
from aihub.agents.codex import CodexAdapter
from aihub.models import AgentStatus


class AgentRegistry:
    def __init__(self, adapters: list[AgentAdapter]):
        self._adapters = {a.id: a for a in adapters}

    def get(self, agent_id: str) -> AgentAdapter:
        try:
            return self._adapters[agent_id]
        except KeyError:
            raise KeyError(f"Unknown agent '{agent_id}'") from None

    def all(self) -> list[AgentAdapter]:
        return list(self._adapters.values())

    async def scan(self) -> list[AgentStatus]:
        # Check every agent at the same time instead of one after another.
        return list(await asyncio.gather(*(a.detect() for a in self._adapters.values())))


def default_registry() -> AgentRegistry:
    return AgentRegistry([
        ClaudeCodeAdapter(),
        CodexAdapter(),
    ])
