"""The contract every agent adapter follows.

The rest of AI-Hub only talks to this interface. To support a new agent
(OpenCode, an ACP agent...), write one new subclass. Nothing else in the
app has to change.
"""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

from aihub.models import AgentEvent, AgentStatus, Discovery, ModelOption


@dataclass
class RunOptions:
    """Extra settings for one turn of a run."""

    resume_session_id: str | None = None  # continue this agent session (follow-ups)
    model: str | None = None              # None = the agent's default model
    effort: str | None = None             # None = the model's default effort
    max_turns: int | None = None
    # Supervised mode: set by RunManager. Contains what the approval bridge
    # needs to reach AI-Hub (URL, run id, secret token). None = full access.
    approval_env: dict[str, str] | None = None
    # App runs (Apps tab):
    extra_env: dict[str, str] | None = None   # the app's settings + keys, for the agent's process
    plugin_dirs: list[str] | None = None      # Claude Code plugins to load for this run only
    accept_edits: bool = False                # with approvals: file edits don't ask, other actions do


class AgentAdapter(ABC):
    id: str    # stable machine name, e.g. "claude-code"
    name: str  # shown in the UI, e.g. "Claude Code"
    supports_supervised: bool = False
    supports_follow_up: bool = False
    # Built-in fallback list, used only if discover() can't ask the CLI.
    models: list[ModelOption] = []

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

    async def discover(self) -> Discovery:
        """Ask the CLI which models (and account details) this user has.

        Slower than detect() (it starts the CLI), so the registry caches it.
        Never costs tokens. The default just returns the built-in list.
        """
        return Discovery(models=self.models)

    def status(self, state, **fields) -> AgentStatus:
        """Helper so every adapter fills in the same common fields."""
        return AgentStatus(
            id=self.id, name=self.name, state=state,
            supports_supervised=self.supports_supervised,
            supports_follow_up=self.supports_follow_up,
            models=self.models,
            **fields,
        )
