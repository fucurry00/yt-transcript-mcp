#!/usr/bin/env python3
# .claude/hooks/typecheck.py
import json
import os
import subprocess
import sys

data = json.load(sys.stdin)
file_path = data.get("tool_input", {}).get("file_path", "")

if not file_path.endswith(".py"):
    sys.exit(0)

project_dir = os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd())

result = subprocess.run(
    ["uv", "run", "mypy", file_path, "--no-error-summary", "--ignore-missing-imports"],
    cwd=project_dir,
    capture_output=True,
    text=True,
)

if result.returncode != 0:
    # Claudeに見せる（修正の参考にさせる）
    print(result.stdout, file=sys.stderr)

sys.exit(0)  # 型エラーでもブロックしない
