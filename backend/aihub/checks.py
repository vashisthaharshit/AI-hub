"""AI-Hub's own measurements after each turn of a run.

Agents report their own numbers (tokens, cost), but those differ between
agents and can't be checked. These are measured by AI-Hub the same way for
every agent, which is what makes runs comparable later (Phase 2 evaluator,
Phase 4 router):

- files and lines changed (from git)
- whether the project's tests pass (by running the user's test command)
"""

import asyncio
import time
from pathlib import Path

from aihub import worktree
from aihub.models import RunMetrics

TEST_TIMEOUT_S = 10 * 60
TEST_OUTPUT_CHARS = 4000  # keep the end of the output: that's where test summaries are


async def measure(worktree_path: Path, base_commit: str, test_command: str | None) -> RunMetrics:
    files, added, removed = await worktree.diff_stats(worktree_path, base_commit)
    metrics = RunMetrics(files_changed=len(files), lines_added=added, lines_removed=removed, files=files)
    if test_command:
        code, output, duration_ms = await run_tests(test_command, worktree_path)
        metrics.test_command = test_command
        metrics.test_exit_code = code
        metrics.tests_passed = code == 0
        metrics.test_duration_ms = duration_ms
        metrics.test_output = output[-TEST_OUTPUT_CHARS:]
    return metrics


async def run_tests(command: str, cwd: Path) -> tuple[int, str, int]:
    """Run the user's test command through the shell. Returns (exit code, output, ms).

    The shell is used on purpose: test commands are things like
    "pytest -q" or "npm test && npm run lint" that the user typed themselves.
    """
    start = time.monotonic()
    proc = await asyncio.create_subprocess_shell(
        command, cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,  # one combined log
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), TEST_TIMEOUT_S)
        code = proc.returncode
        output = out.decode("utf-8", errors="replace")
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        code, output = -1, f"Tests timed out after {TEST_TIMEOUT_S // 60} minutes."
    return code, output, int((time.monotonic() - start) * 1000)
