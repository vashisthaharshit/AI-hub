"""Open the computer's own "choose a folder" window and return the path.

Why the backend does this: a web page can't. Browsers hide full file and
folder paths from pages, for privacy. AI-Hub's backend runs on the user's
own computer (127.0.0.1 only), so it can open the normal Windows dialog.

How: tkinter (comes with Python on Windows). If tkinter isn't there, on
Windows we fall back to PowerShell's FolderBrowserDialog.
"""

import asyncio
import subprocess
import sys
from pathlib import Path

_lock = asyncio.Lock()  # one dialog at a time


class PickerError(RuntimeError):
    pass


def _with_tkinter(start: str | None, title: str) -> str | None:
    import tkinter
    from tkinter import filedialog

    root = tkinter.Tk()
    root.withdraw()                      # no empty main window
    root.attributes("-topmost", True)    # show the dialog above the browser
    try:
        chosen = filedialog.askdirectory(initialdir=start or None, title=title, mustexist=False, parent=root)
    finally:
        root.destroy()
    return str(Path(chosen)) if chosen else None


def _with_powershell(start: str | None, title: str) -> str | None:
    script = (
        "Add-Type -AssemblyName System.Windows.Forms;"
        "$d = New-Object System.Windows.Forms.FolderBrowserDialog;"
        f"$d.Description = '{title.replace(chr(39), '')}';"
        "$d.ShowNewFolderButton = $true;"
        + (f"$d.SelectedPath = '{start.replace(chr(39), '')}';" if start else "")
        + "$top = New-Object System.Windows.Forms.Form -Property @{TopMost=$true};"
        "if ($d.ShowDialog($top) -eq 'OK') { Write-Output $d.SelectedPath }"
    )
    out = subprocess.run(
        ["powershell", "-NoProfile", "-STA", "-Command", script],
        capture_output=True, text=True, timeout=600,
    )
    return out.stdout.strip() or None


def _pick(start: str | None, title: str) -> str | None:
    if start and not Path(start).is_dir():
        start = None
    try:
        return _with_tkinter(start, title)
    except ImportError:
        if sys.platform == "win32":
            return _with_powershell(start, title)
        raise PickerError("The folder picker isn't available here. Type the folder path instead.") from None
    except Exception as exc:  # e.g. no screen (a server without a desktop)
        raise PickerError(f"Couldn't open the folder picker ({exc}). Type the folder path instead.") from None


async def pick_folder(start: str | None = None, title: str = "Choose a folder") -> str | None:
    """The chosen folder, or None if the user cancelled. Waits until they decide."""
    if _lock.locked():
        raise PickerError("A folder window is already open. It may be behind your browser.")
    async with _lock:
        return await asyncio.to_thread(_pick, start, title)
