"""The Brain: describe a task, AI-Hub plans it and runs it across agents.

Built in steps (see docs in each module):
  1  triage.py   is this task big enough to plan?
     digest.py   a short description of the repo, made by code (free)
     planner.py  clarifying questions + a plan of 2-5 steps, checked by code
     tasks.py    puts it together and saves Brain tasks
  2+ router, running steps, parallel worktrees, merging, project memory
"""
