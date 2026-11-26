"""Programs an app needs (Git, Bun, LaTeX...): find them, and install them.

Finding
  We look on PATH, but with a twist for Windows. When an installer adds a
  program to PATH, it writes the new PATH to the registry; programs that are
  already running (AI-Hub, the terminal it started in) keep the old copy.
  That's why guides say "restart your terminal". Instead, we read the fresh
  PATH from the registry ourselves, so a program shows up right after it is
  installed, and we add its folder to our own PATH so agents we start see it.

Installing
  On Windows we use winget, the package manager built into Windows 10/11.
  Only when the user clicks Install, one install at a time, with the output
  shown live. Afterwards we don't trust exit codes (winget uses many, e.g.
  for "already installed"); we simply look for the program again:
    found                      -> installed
    not found, installer ok    -> restart_needed (Windows hasn't caught up)
    not found, installer error -> failed (the log says why)

Testing the flow without uninstalling anything
  Set AIHUB_PRETEND_MISSING=bun,latex before starting aihub: those tools
  are reported as missing until you click Install for them once.
"""

import asyncio
import os
import re
import shutil
import sys
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from aihub.db import now_iso
from aihub.models import AppRequirement, InstallJob

INSTALL_TIMEOUT = 45 * 60  # seconds; LaTeX is big


@dataclass(frozen=True)
class Tool:
    id: str
    label: str
    programs: tuple[str, ...]      # any of these on PATH = installed
    winget_id: str | None          # None = AI-Hub can't install it
    website: str
    note: str | None = None        # shown before installing
    extra_dirs: tuple[str, ...] = ()  # usual install folders, in case PATH isn't updated


TOOLS: dict[str, Tool] = {t.id: t for t in (
    Tool("git", "Git", ("git",), "Git.Git", "https://git-scm.com/downloads",
         "Small download.", (r"%ProgramFiles%\Git\cmd",)),
    Tool("python", "Python", ("uv",), None, "https://docs.astral.sh/uv/",
         "Comes with AI-Hub: uv downloads the right Python version by itself."),
    Tool("bun", "Bun", ("bun",), "Oven-sh.Bun", "https://bun.sh",
         "Small download.", (r"%USERPROFILE%\.bun\bin",)),
    Tool("node", "Node.js", ("node",), "OpenJS.NodeJS.LTS", "https://nodejs.org",
         "Small download.", (r"%ProgramFiles%\nodejs",)),
    Tool("latex", "LaTeX (MiKTeX)", ("lualatex", "xelatex", "pdflatex"), "MiKTeX.MiKTeX",
         "https://miktex.org/download",
         "Large download (a few hundred MB). The first PDF may take longer while MiKTeX "
         "fetches the extra packages it needs.",
         (r"%LOCALAPPDATA%\Programs\MiKTeX\miktex\bin\x64", r"%ProgramFiles%\MiKTeX\miktex\bin\x64")),
    Tool("docker", "Docker Desktop", ("docker",), "Docker.DockerDesktop",
         "https://www.docker.com/products/docker-desktop/",
         "Large download, and Windows needs a restart afterwards. Optional.",
         (r"%ProgramFiles%\Docker\Docker\resources\bin",)),
)}

# Tool ids to report as missing (for testing the install flow).
PRETEND_MISSING: set[str] = {t.strip() for t in os.environ.get("AIHUB_PRETEND_MISSING", "").split(",") if t.strip()}


class InstallError(RuntimeError):
    """A problem the user can act on; shown as-is."""


# ---------- finding programs ----------

def _registry_path() -> list[str]:
    """PATH as Windows has it saved now (machine + user), not our old copy."""
    if sys.platform != "win32":
        return []
    import winreg

    parts: list[str] = []
    for root, key in (
        (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
        (winreg.HKEY_CURRENT_USER, "Environment"),
    ):
        try:
            with winreg.OpenKey(root, key) as handle:
                value, _ = winreg.QueryValueEx(handle, "Path")
                parts += os.path.expandvars(value).split(os.pathsep)
        except OSError:
            pass
    return parts


def search_path(tool: Tool | None = None) -> str:
    parts = os.environ.get("PATH", "").split(os.pathsep) + _registry_path()
    if tool:
        parts += [os.path.expandvars(d) for d in tool.extra_dirs]
    seen, out = set(), []
    for part in parts:
        key = os.path.normcase(part.strip())
        if part.strip() and key not in seen:
            seen.add(key)
            out.append(part.strip())
    return os.pathsep.join(out)


def _learn(path: str) -> None:
    """Add a program's folder to our own PATH, so agents we start find it too."""
    folder = str(Path(path).parent)
    current = os.environ.get("PATH", "").split(os.pathsep)
    if os.path.normcase(folder) not in {os.path.normcase(p) for p in current}:
        os.environ["PATH"] = os.pathsep.join([*current, folder])


def _uv() -> str | None:
    """uv is a dependency of AI-Hub, so it's usually inside AI-Hub's own venv."""
    try:
        from uv import find_uv_bin
        return find_uv_bin()
    except Exception:  # not installed as a package, or binary missing
        return None


def find(tool: Tool) -> str | None:
    """Full path of the tool's program, or None."""
    if tool.id in PRETEND_MISSING:
        return None
    if tool.id == "python" and (found := _uv()):
        return found
    path = search_path(tool)
    for program in tool.programs:
        found = shutil.which(program, path=path)
        if found:
            _learn(found)
            return found
    return None


def status(tool_id: str, why: str, optional: bool = False, auto: bool = False,
           which: Callable[[str], str | None] | None = None) -> AppRequirement:
    """What the UI shows for one needed tool. `which` replaces the search (tests)."""
    tool = TOOLS[tool_id]
    if which is not None:
        installed = any(which(p) for p in tool.programs)
    else:
        installed = find(tool) is not None
    return AppRequirement(
        id=tool.id, label=tool.label, why=why, installed=installed, optional=optional, auto=auto,
        website=tool.website, note=tool.note,
        can_install=bool(tool.winget_id) and sys.platform == "win32",
    )


def recheck(requirements: list[AppRequirement]) -> list[AppRequirement]:
    """Look again for every tool (e.g. after the user installed one by hand)."""
    return [status(r.id, r.why, r.optional, r.auto) if r.id in TOOLS else r for r in requirements]


# ---------- installing ----------

_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
_SPINNER = re.compile(r"^[-\\|/\s]*$")
_PROGRESS = re.compile(r"[█▒]")


class Installer:
    """Runs one install at a time and keeps its output for the UI to poll."""

    def __init__(self, command_prefix: list[str] | None = None):
        # command_prefix replaces "winget" (tests use a fake installer script).
        self.command_prefix = command_prefix
        self.jobs: dict[str, InstallJob] = {}
        self._lines: dict[str, list[str]] = {}
        self._tasks: dict[str, asyncio.Task] = {}

    def _winget(self) -> list[str] | None:
        if self.command_prefix:
            return self.command_prefix
        if sys.platform != "win32":
            return None
        found = shutil.which("winget", path=search_path())
        return [found] if found else None

    def start(self, tool_id: str) -> InstallJob:
        tool = TOOLS.get(tool_id)
        if not tool:
            raise KeyError(f"Unknown tool '{tool_id}'")
        if not tool.winget_id:
            raise InstallError(f"{tool.label} can't be installed from AI-Hub. Get it here: {tool.website}")
        if any(j.state == "running" for j in self.jobs.values()):
            raise InstallError("Another install is still running. Wait for it to finish.")
        if find(tool):
            raise InstallError(f"{tool.label} is already installed.")
        winget = self._winget()
        if not winget:
            raise InstallError(f"winget (Windows' app installer) isn't available here. "
                               f"Install {tool.label} from {tool.website}, then click Check again.")
        command = [*winget, "install", "--id", tool.winget_id, "--exact", "--source", "winget",
                   "--accept-package-agreements", "--accept-source-agreements", "--disable-interactivity"]
        job = InstallJob(id=uuid.uuid4().hex[:12], tool_id=tool.id, label=tool.label, state="running",
                         command="winget " + " ".join(command[len(winget):]), started_at=now_iso())
        self.jobs[job.id] = job
        self._lines[job.id] = []
        self._tasks[job.id] = asyncio.create_task(self._run(job, command, tool))
        return job

    def _add(self, job_id: str, raw: str) -> None:
        line = _ANSI.sub("", raw).strip()
        if _SPINNER.match(line):
            return
        lines = self._lines[job_id]
        # Progress bars redraw themselves; keep only the latest one.
        if lines and _PROGRESS.search(line) and _PROGRESS.search(lines[-1]):
            lines[-1] = line
        else:
            lines.append(line)

    async def _read(self, job: InstallJob, proc) -> None:
        buffer = ""
        while chunk := await proc.stdout.read(1024):
            buffer += chunk.decode("utf-8", errors="replace")
            *complete, buffer = re.split(r"\r\n|\r|\n", buffer)
            for line in complete:
                self._add(job.id, line)
        if buffer:
            self._add(job.id, buffer)

    async def _run(self, job: InstallJob, command: list[str], tool: Tool) -> None:
        code = None
        try:
            proc = await asyncio.create_subprocess_exec(
                *command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                stdin=asyncio.subprocess.DEVNULL,
            )
            try:
                await asyncio.wait_for(self._read(job, proc), INSTALL_TIMEOUT)
                code = await proc.wait()
            except asyncio.TimeoutError:
                proc.kill()
                self._finish(job, "failed", f"The install took longer than {INSTALL_TIMEOUT // 60} minutes "
                                            f"and was stopped. You can install it from {tool.website}")
                return
        except OSError as exc:
            self._finish(job, "failed", f"Couldn't start the installer: {exc}")
            return

        PRETEND_MISSING.discard(tool.id)  # a real install attempt happened; look for real now
        if find(tool):
            self._finish(job, "installed", f"{tool.label} is installed.")
        elif code == 0:
            self._finish(job, "restart_needed", f"{tool.label} was installed, but Windows hasn't made it "
                                                "visible yet. Close AI-Hub and start it again (or restart "
                                                "your computer), then click Check again.")
        else:
            self._finish(job, "failed", f"The installer stopped with an error (code {code}). See the log "
                                        f"above, or install it from {tool.website}")

    def _finish(self, job: InstallJob, state: str, message: str) -> None:
        job.state = state
        job.message = message
        job.finished_at = now_iso()

    def get(self, job_id: str, after: int = -1) -> InstallJob:
        """The job, with the log lines after line number `after` (for polling)."""
        job = self.jobs.get(job_id)
        if not job:
            raise KeyError(f"Install '{job_id}' not found")
        lines = self._lines[job_id]
        return job.model_copy(update={"lines": lines[after + 1:], "line_count": len(lines)})

    async def wait(self, job_id: str) -> InstallJob:
        await self._tasks[job_id]
        return self.get(job_id)
