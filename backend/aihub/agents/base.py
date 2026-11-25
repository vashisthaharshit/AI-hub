"""The contract every agent adapter follows.

The rest of AI-Hub only talks to this interface. To support a new agent
(OpenCode, an ACP agent...), write one new subclass. Nothing else in the
app has to change.
"""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

from aihub.models import AgentEvent, AgentStatus


@dataclass
class RunOptions:
    """Extra settings for one turn of a run."""

    resume_session_id: str | None = None  # continue this agent session (follow-ups)
    max_turns: int | None = None
    # Supervised mode: set by RunManager. Contains what the approval bridge
    # needs to reach AI-Hub (URL, run id, secret token). None = full access.
    approval_env: dict[str, str] | None = None


class AgentAdapter(ABC):
    id: str    # stable machine name, e.g. "claude-code"
    name: str  # shown in the UI, e.g. "Claude Code"
    supports_supervised: bool = False
    supports_follow_up: bool = False

    @abstractmethod
    async def detect(self) -> AgentStatus:
        """Cheap check: is it installed, which version? Never costs tokens."""

    @abstractmethod
    async def test_connection(self) -> AgentStatus:
        """Expensive check: send a tiny prompt to confirm it is signed in.
        Only run when the user asks for it."""

    @abstractmethod
    def run(
        self, prompt: str, workdir: Path, options: RunOptions | None = None
    ) -> AsyncIterator[AgentEvent]:
        """Run the agent on `prompt` inside `workdir`, yielding events as they happen.

        Cancelling the task that consumes this iterator must stop the agent
        (implementations kill their process in a `finally` block).
        """

    def status(self, state, **fields) -> AgentStatus:
        """Helper so every adapter fills in the same common fields."""
        return AgentStatus(
            id=self.id, name=self.name, state=state,
            supports_supervised=self.supports_supervised,
            supports_follow_up=self.supports_follow_up,
            **fields,
        )
