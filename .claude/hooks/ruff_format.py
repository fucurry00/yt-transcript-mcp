#!/usr/bin/env python3
import json
import os
import subprocess
import sys

data = json.load(sys.stdin)
file_path = data.get("tool_input", {}).get("file_path", "")

if not file_path.endswith(".py"):
    sys.exit(0)

project_dir = os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd())

# format
subprocess.run(
    ["uv", "run", "ruff", "format", file_path],
    cwd=project_dir,
)

# lint + autofix（ブロックはしない）
result = subprocess.run(
    ["uv", "run", "ruff", "check", "--fix", file_path],
    cwd=project_dir,
    capture_output=True,
    text=True,
)

if result.stdout:
    print(result.stdout, file=sys.stderr)

sys.exit(0)
