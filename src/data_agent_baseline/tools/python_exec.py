from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


def execute_python_code(context_root: Path, code: str, *, timeout_seconds: int = 30) -> dict[str, Any]:
    resolved_context_root = context_root.resolve()

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False, encoding="utf-8"
    ) as script_file:
        script_file.write(code)
        script_path = script_file.name

    try:
        completed = subprocess.run(
            [sys.executable, script_path],
            cwd=str(resolved_context_root),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            errors="replace",
        )
        stdout_str = completed.stdout
        if len(stdout_str) > 8000:
            stdout_str = stdout_str[:4000] + "\n...[STDOUT TRUNCATED]...\n" + stdout_str[-4000:]
            
        stderr_str = completed.stderr
        if len(stderr_str) > 4000:
            stderr_str = stderr_str[:2000] + "\n...[STDERR TRUNCATED]...\n" + stderr_str[-2000:]
            
        return {
            "success": completed.returncode == 0,
            "output": stdout_str,
            "stderr": stderr_str,
            "error": stderr_str.strip() if completed.returncode != 0 else "",
        }
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "output": "",
            "stderr": "",
            "error": f"Python execution timed out after {timeout_seconds} seconds.",
        }
    except Exception as exc:
        return {
            "success": False,
            "output": "",
            "stderr": "",
            "error": str(exc),
        }
    finally:
        Path(script_path).unlink(missing_ok=True)
