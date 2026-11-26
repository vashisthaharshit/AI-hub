"""Apps: GitHub repos built on top of an AI agent, used through buttons.

Some repos (e.g. ai-job-search) have no program of their own: the agent IS
the program. They ship a CLAUDE.md / AGENTS.md plus slash commands, and the
user is expected to open a terminal and type `/setup`, `/apply ...`.
The Apps tab turns those commands into buttons a beginner can click.

  manifest.py  reads a repo folder: commands, skills, tools it needs (no AI, no tokens)
  connect.py   clones a GitHub link (or uses a local folder) and saves the app
"""
