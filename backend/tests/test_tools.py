"""Apps tab, step 2: finding the tools an app needs, and installing them.

A fake installer script stands in for winget: it prints winget-like output
(spinner, a progress bar that redraws itself) and "installs" a fake program
into a temp folder. Nothing real is installed.
"""

import asyncio
import sys
from pathlib import Path

import pytest

from aihub.apps import tools
from aihub.models import AppRequirement

FAKE_WINGET = r'''
import os, sys, time
from pathlib import Path
out = lambda text: (sys.stdout.buffer.write(text.encode("utf-8")), sys.stdout.flush())
mode = os.environ["FAKE_MODE"]
out("  -\r  \\\r  |\r")
out("Found Fake Tool [" + sys.argv[sys.argv.index("--id") + 1] + "]\n")
for bar in ("█▒▒ 1 MB\r", "██▒ 2 MB\r", "███ 3 MB\n"):
    out(bar)
    time.sleep(0.01)
if mode == "ok":
    folder = Path(os.environ["FAKE_DIR"])
    folder.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        (folder / "faketool.cmd").write_text("@echo off\n")
    else:
        prog = folder / "faketool"
        prog.write_text("#!/bin/sh\n")
        prog.chmod(0o755)
    out("Successfully installed\n")
    sys.exit(0)
if mode == "silent":
    out("Successfully installed\n")
    sys.exit(0)
out("Installer failed with exit code: 1603\n")
sys.exit(1)
'''


@pytest.fixture
def fake(tmp_path, monkeypatch):
    """A tool 'fake' whose program lands in tmp/bin, and a fake winget."""
    bin_dir = tmp_path / "bin"
    tool = tools.Tool("fake", "Fake Tool", ("faketool",), "Fake.Tool", "https://example.com",
                      "Small download.", (str(bin_dir),))
    monkeypatch.setitem(tools.TOOLS, "fake", tool)
    monkeypatch.setattr(tools, "PRETEND_MISSING", set())
    # find() adds folders to PATH on purpose; restore PATH after each test.
    monkeypatch.setenv("PATH", tools.os.environ.get("PATH", ""))
    script = tmp_path / "fake_winget.py"
    script.write_text(FAKE_WINGET, encoding="utf-8")
    monkeypatch.setenv("FAKE_DIR", str(bin_dir))
    return tools.Installer(command_prefix=[sys.executable, str(script)]), bin_dir


def install(installer: tools.Installer, mode: str, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", mode)

    async def go():
        job = installer.start("fake")
        assert job.state == "running"
        return await installer.wait(job.id)
    return asyncio.run(go())


class TestInstall:
    def test_success_and_clean_log(self, fake, monkeypatch):
        installer, bin_dir = fake
        assert tools.find(tools.TOOLS["fake"]) is None
        job = install(installer, "ok", monkeypatch)
        assert job.state == "installed" and job.finished_at
        # spinner dropped, the redrawn progress bar kept once (its last state)
        assert job.lines == ["Found Fake Tool [Fake.Tool]", "███ 3 MB", "Successfully installed"]
        assert job.command.startswith("winget install --id Fake.Tool --exact")
        assert tools.find(tools.TOOLS["fake"])          # found now...
        assert str(bin_dir) in tools.os.environ["PATH"]  # ...and agents will find it too

    def test_polling_returns_only_new_lines(self, fake, monkeypatch):
        installer, _ = fake
        job = install(installer, "ok", monkeypatch)
        later = installer.get(job.id, after=0)
        assert later.lines == ["███ 3 MB", "Successfully installed"] and later.line_count == 3

    def test_installer_ok_but_program_not_found(self, fake, monkeypatch):
        job = install(fake[0], "silent", monkeypatch)
        assert job.state == "restart_needed" and "start it again" in job.message

    def test_installer_error(self, fake, monkeypatch):
        job = install(fake[0], "fail", monkeypatch)
        assert job.state == "failed" and "code 1" in job.message
        assert job.lines[-1] == "Installer failed with exit code: 1603"

    def test_one_install_at_a_time(self, fake, monkeypatch):
        installer, _ = fake
        monkeypatch.setenv("FAKE_MODE", "ok")

        async def go():
            first = installer.start("fake")
            with pytest.raises(tools.InstallError, match="still running"):
                installer.start("fake")
            await installer.wait(first.id)
        asyncio.run(go())

    def test_already_installed(self, fake, monkeypatch):
        install(fake[0], "ok", monkeypatch)
        with pytest.raises(tools.InstallError, match="already installed"):
            fake[0].start("fake")

    def test_tool_without_installer(self, fake):
        with pytest.raises(tools.InstallError, match="can't be installed from AI-Hub"):
            fake[0].start("python")

    def test_unknown_tool(self, fake):
        with pytest.raises(KeyError):
            fake[0].start("nope")

    def test_unknown_job(self, fake):
        with pytest.raises(KeyError):
            fake[0].get("nope")


class TestFind:
    def test_pretend_missing_until_an_install_attempt(self, fake, monkeypatch):
        installer, bin_dir = fake
        install(installer, "ok", monkeypatch)
        tools.PRETEND_MISSING.add("fake")
        assert tools.find(tools.TOOLS["fake"]) is None
        tools.PRETEND_MISSING.discard("fake")
        assert tools.find(tools.TOOLS["fake"])

    def test_found_in_usual_install_folder_even_if_not_on_path(self, fake, monkeypatch):
        installer, bin_dir = fake
        install(installer, "ok", monkeypatch)
        monkeypatch.setenv("PATH", "")  # not on PATH at all
        assert tools.find(tools.TOOLS["fake"])

    def test_status_fields(self, fake):
        req = tools.status("fake", "because", optional=True)
        assert (req.installed, req.optional, req.website, req.note) == (False, True, "https://example.com",
                                                                         "Small download.")
        assert req.can_install == (sys.platform == "win32")
        assert not tools.status("python", "x").can_install   # comes with AI-Hub

    def test_recheck_updates_installed(self, fake, monkeypatch):
        before = [tools.status("fake", "because"), AppRequirement(id="other", label="O", why="w", installed=True)]
        assert not before[0].installed
        install(fake[0], "ok", monkeypatch)
        after = tools.recheck(before)
        assert after[0].installed and after[0].why == "because"
        assert after[1] is before[1]   # tools we don't know are left as they were

    def test_fake_which_for_manifest_tests(self):
        assert tools.status("bun", "x", which=lambda p: "/bin/bun").installed
        assert not tools.status("latex", "x", which=lambda p: None).installed
