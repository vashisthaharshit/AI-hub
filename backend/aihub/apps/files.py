"""Files the user drops on a command's card (a CV, a template...).

They are saved inside the app's folder, where the repo expects them
(e.g. documents/cv/), so the agent can read them. Rules:
  - only inside the repo folder: no "..", no absolute paths
  - the file name is cleaned (no folders, no odd characters)
  - an existing file is never overwritten: "CV.pdf" becomes "CV (2).pdf"
  - at most MAX_BYTES per file
"""

import re
from pathlib import Path

MAX_BYTES = 25 * 1024 * 1024


class UploadError(RuntimeError):
    pass


def clean_name(name: str) -> str:
    # Drop any folder part. Not Path(name).name: on Windows "a:b.txt" would be
    # read as drive "a:" + "b.txt" and lose part of the name.
    name = re.split(r"[\\/]", name)[-1]
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    if not name:
        raise UploadError("The file needs a name.")
    return name[:120]


def target_folder(repo: Path, folder: str | None) -> Path:
    text = (folder or "uploads").strip().replace("\\", "/")
    parts = [p for p in text.split("/") if p not in ("", ".")]
    if text.startswith("/") or re.match(r"^[A-Za-z]:", text) or ".." in parts or not parts:
        raise UploadError("Files can only be saved inside the app's folder.")
    target = (repo / Path(*parts)).resolve()
    if not target.is_relative_to(repo.resolve()):
        raise UploadError("Files can only be saved inside the app's folder.")
    return target


def save_upload(repo: Path, folder: str | None, name: str, data: bytes) -> str:
    """Save the file and return its path relative to the repo, with / slashes."""
    if not data:
        raise UploadError("The file is empty.")
    if len(data) > MAX_BYTES:
        raise UploadError(f"The file is too big (over {MAX_BYTES // (1024 * 1024)} MB).")
    target = target_folder(repo, folder)
    target.mkdir(parents=True, exist_ok=True)
    name = clean_name(name)
    stem, suffix = Path(name).stem, Path(name).suffix
    path, n = target / name, 2
    while path.exists():
        path = target / f"{stem} ({n}){suffix}"
        n += 1
    path.write_bytes(data)
    return path.relative_to(repo.resolve()).as_posix()
