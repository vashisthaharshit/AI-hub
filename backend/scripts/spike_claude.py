"""Learning script: watch Claude Code's raw event stream.

No AI-Hub code, no dependencies. Run it to SEE what the adapter has to parse:

    python scripts/spike_claude.py "List the files in this folder"

It prints the type of every JSON line Claude Code emits, then the final
cost and token numbers. Read this output once before reading
aihub/agents/claude_code.py; the adapter will make much more sense.
"""

import json
import subprocess
import sys

prompt = sys.argv[1] if len(sys.argv) > 1 else "Reply with just the word OK."

proc = subprocess.Popen(
    ["claude", "-p", "--output-format", "stream-json", "--verbose", "--max-turns", "3"],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    text=True, encoding="utf-8", shell=(sys.platform == "win32"),  # Windows: find claude.cmd
)
proc.stdin.write(prompt)
proc.stdin.close()

for line in proc.stdout:  # one JSON object per line, as it happens
    line = line.strip()
    if not line:
        continue
    msg = json.loads(line)
    kind = msg.get("type"), msg.get("subtype")
    print(f"--- {kind}")
    if msg.get("type") == "assistant":
        for block in msg["message"]["content"]:
            if block["type"] == "text":
                print("  text:", block["text"][:200])
            elif block["type"] == "tool_use":
                print("  tool:", block["name"], json.dumps(block["input"])[:200])
    elif msg.get("type") == "result":
        print("  cost $", msg.get("total_cost_usd"), "| time ms", msg.get("duration_ms"),
              "| turns", msg.get("num_turns"), "| usage", msg.get("usage"))

proc.wait()
if proc.returncode:
    print("stderr:", proc.stderr.read())
