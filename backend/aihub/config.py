"""Where AI-Hub keeps its data, and how it is reached.

Everything lives in one folder (default: ~/.aihub) so it never mixes with
the user's own repos. Set the AIHUB_DATA_DIR environment variable to move it.
"""

import os
from pathlib import Path

DATA_DIR = Path(os.environ.get("AIHUB_DATA_DIR", Path.home() / ".aihub"))
DB_PATH = DATA_DIR / "aihub.db"
WORKTREES_DIR = DATA_DIR / "worktrees"

# The server only listens on this machine. It can run agents that edit files,
# so it must never be reachable from the network.
HOST = "127.0.0.1"
PORT = int(os.environ.get("AIHUB_PORT", "8000"))

# The built React app (`npm run build` in frontend/). The backend serves it,
# so users only need one command and one address. backend/aihub/config.py
# -> parents[2] is the repo root.
FRONTEND_DIST = Path(os.environ.get(
    "AIHUB_FRONTEND_DIST", Path(__file__).resolve().parents[2] / "frontend" / "dist"
))

# The React dev server (Vite) runs here during development; it may call the API.
FRONTEND_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]


def base_url() -> str:
    return f"http://{HOST}:{PORT}"


def ensure_dirs() -> None:
    WORKTREES_DIR.mkdir(parents=True, exist_ok=True)
