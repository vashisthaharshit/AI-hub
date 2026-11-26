"""The summary model: an LLM that writes short summaries for AI-Hub.

Uses: the handoff when switching agents mid-run (see runner.py), and the
beginner guide of a connected app (apps/guide.py). complete() is the
general call; summarize() is complete() with the handoff instructions.

Where the LLM comes from (the user picks in Settings):
- "agent": one of the user's own agents (e.g. Claude Code with Haiku, or
  Codex with low effort). No key needed; it uses that agent's plan.
- "groq" / "openrouter": the user's OWN API key, read from the OS keychain.
  Both speak the OpenAI "chat completions" format, so one function serves both.

Nobody's key is shared: each user's AI-Hub only uses that user's key.
"""

import asyncio
import json
import os
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from aihub.agents.base import RunOptions
from aihub.agents.registry import AgentRegistry
from aihub.db import Database
from aihub.models import SummarySettings, SummaryTest
from aihub.secrets_store import SecretStore

# name -> (label, default base URL). The URL can be changed with an
# environment variable (AIHUB_GROQ_BASE_URL...), which the tests use.
HTTP_PROVIDERS = {
    "groq": ("Groq", "https://api.groq.com/openai/v1"),
    "openrouter": ("OpenRouter", "https://openrouter.ai/api/v1"),
}

INSTRUCTIONS = (
    "You summarize a coding session for another AI coding agent that will continue the work. "
    "Keep: the goal, decisions made, what was done, which files changed, the state of tests, "
    "problems and anything still to do. Leave out chit-chat. Plain text, at most 250 words. "
    "Do not use any tools; just answer with the summary."
)
HTTP_TIMEOUT_S = 90


class SummaryError(RuntimeError):
    pass


def base_url(provider: str) -> str:
    return os.environ.get(f"AIHUB_{provider.upper()}_BASE_URL", HTTP_PROVIDERS[provider][1]).rstrip("/")


class Summarizer:
    def __init__(self, db: Database, registry: AgentRegistry, secrets: SecretStore):
        self.db = db
        self.registry = registry
        self.secrets = secrets

    # ---------- settings ----------

    def settings(self) -> SummarySettings:
        return SummarySettings(**self.db.get_setting("summary", {}))

    def save_settings(self, settings: SummarySettings) -> None:
        self.db.set_setting("summary", settings.model_dump())

    def describe(self, s: SummarySettings) -> str:
        if s.provider == "off":
            return "off (template only)"
        if s.provider == "agent":
            try:
                name = self.registry.get(s.agent_id or "").name
            except KeyError:
                name = s.agent_id or "agent"
            return f"{name} · {s.model or 'default model'}" + (f" · effort {s.effort}" if s.effort else "")
        return f"{HTTP_PROVIDERS[s.provider][0]} · {s.model}"

    # ---------- summarizing ----------

    async def summarize(self, text: str, settings: SummarySettings | None = None) -> str:
        return await self.complete(INSTRUCTIONS, text, settings)

    async def complete(self, instructions: str, text: str, settings: SummarySettings | None = None,
                       max_tokens: int = 800) -> str:
        """Ask the chosen LLM once: instructions + text in, answer text out."""
        s = settings or self.settings()
        if s.provider == "off":
            raise SummaryError("No summary model is set.")
        if s.provider == "agent":
            return await self._via_agent(s, instructions, text)
        return await self._via_http(s, instructions, text, max_tokens)

    async def test(self, settings: SummarySettings) -> SummaryTest:
        """Summarize a tiny example, to check the settings work before saving them."""
        sample = (
            "Turn 1: Claude Code. Request: add a /health endpoint with a test.\n"
            "Result (finished): added GET /health returning {status: ok} in app/main.py and "
            "tests/test_health.py. Tests (pytest -q): passed."
        )
        start = time.monotonic()
        try:
            summary = await self.summarize(sample, settings)
        except Exception as exc:  # noqa: BLE001 - shown to the user in Settings
            return SummaryTest(ok=False, error=str(exc), used=self.describe(settings))
        return SummaryTest(ok=True, summary=summary, used=self.describe(settings),
                           duration_ms=int((time.monotonic() - start) * 1000))

    async def _via_agent(self, s: SummarySettings, instructions: str, text: str) -> str:
        try:
            adapter = self.registry.get(s.agent_id or "")
        except KeyError:
            raise SummaryError("Pick which agent writes the summaries.") from None
        # An empty temp folder: the agent can't touch any real files.
        with tempfile.TemporaryDirectory() as tmp:
            options = RunOptions(model=s.model, effort=s.effort, max_turns=1)
            events = [e async for e in adapter.run(f"{instructions}\n\n---\n\n{text}", Path(tmp), options)]
        result = next((e for e in events if e.kind == "result"), None)
        if result and result.data.get("success") and result.data.get("summary"):
            return str(result.data["summary"]).strip()
        texts = [str(e.data.get("text", "")) for e in events if e.kind == "text"]
        if result and result.data.get("success") and texts:
            return texts[-1].strip()
        errors = [str(e.data.get("message", "")) for e in events if e.kind == "error"]
        raise SummaryError(errors[0] if errors else f"{adapter.name} didn't return a summary.")

    async def _via_http(self, s: SummarySettings, instructions: str, text: str, max_tokens: int = 800) -> str:
        label = HTTP_PROVIDERS[s.provider][0]
        key = self.secrets.get(s.provider)
        if not key:
            raise SummaryError(f"No {label} API key saved. Add yours in Settings.")
        if not s.model:
            raise SummaryError(f"Pick a {label} model.")
        body = {
            "model": s.model,
            "messages": [{"role": "system", "content": instructions}, {"role": "user", "content": text}],
            "temperature": 0.2,
            "max_tokens": max_tokens,
        }
        data = await asyncio.to_thread(_request, "POST", f"{base_url(s.provider)}/chat/completions", key, body)
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise SummaryError(f"Unexpected reply from {label}: {str(data)[:200]}") from None
        if not content or not str(content).strip():
            raise SummaryError(f"{label} returned an empty summary.")
        return str(content).strip()

    async def list_models(self, provider: str) -> list[str]:
        """Model ids the provider offers to this key (for the Settings dropdown)."""
        if provider not in HTTP_PROVIDERS:
            raise SummaryError(f"Unknown provider '{provider}'")
        key = self.secrets.get(provider)
        if not key:
            raise SummaryError(f"Save your {HTTP_PROVIDERS[provider][0]} API key first.")
        data = await asyncio.to_thread(_request, "GET", f"{base_url(provider)}/models", key, None)
        return sorted(m["id"] for m in data.get("data") or [] if isinstance(m, dict) and m.get("id"))


def _request(method: str, url: str, key: str, body: dict | None) -> dict:
    """One HTTPS call with the standard library (runs in a thread)."""
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "User-Agent": "AI-Hub/0.2",
        # OpenRouter uses these to name the app in its dashboard; others ignore them.
        "X-Title": "AI-Hub",
    }
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_S) as response:
            return json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        try:
            detail = json.loads(detail).get("error", {}).get("message") or detail
        except (ValueError, AttributeError):
            pass
        if exc.code == 401:
            raise SummaryError("The API key was rejected (401). Check it in Settings.") from None
        raise SummaryError(f"HTTP {exc.code}: {detail}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise SummaryError(f"Couldn't reach the provider: {exc}") from None
