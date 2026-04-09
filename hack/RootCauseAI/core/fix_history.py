"""Fix history management for tracking fix attempts."""

import json
import os
import subprocess
from pathlib import Path


class FixHistory:
    """Manages fix_history.json for tracking fix attempts across pipeline runs."""

    def __init__(self, artifacts_dir: Path, session_name: str):
        """Create fix_history.json at pipeline start."""
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        self._path = artifacts_dir / "fix_history.json"
        self._artifacts_dir = artifacts_dir

        history = {
            "session": session_name,
            "base_commit": self._get_project_head_commit(),
            "attempts": [],
        }
        self._path.write_text(json.dumps(history, indent=2))

    @property
    def path(self) -> Path:
        """Get the fix history file path."""
        return self._path

    def _get_project_head_commit(self) -> str:
        """Get the current HEAD commit hash of the target project."""
        project_path = os.getenv("PROJECT_PATH")
        if not project_path:
            return ""
        try:
            result = subprocess.run(
                ["git", "-C", project_path, "rev-parse", "HEAD"],
                capture_output=True, text=True, timeout=10
            )
            return result.stdout.strip() if result.returncode == 0 else ""
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return ""

    def record_attempt(self, result: str, error_after: str = None):
        """Record a fix attempt in fix_history.json."""
        history = json.loads(self._path.read_text())
        attempt_num = len(history["attempts"]) + 1

        # Read latest hint file for cause
        hints_dir = self._artifacts_dir / "hints"
        hint_files = sorted(hints_dir.glob("hint_*.json"), key=lambda f: f.stat().st_mtime)
        cause = ""
        if hint_files:
            try:
                hint = json.loads(hint_files[-1].read_text())
                cause = hint.get("cause", "")
            except (json.JSONDecodeError, OSError):
                pass

        # Read latest fix file for patch info
        fixes_dir = self._artifacts_dir / "bug_fixes"
        fix_files = sorted(fixes_dir.glob("fix_*.json"), key=lambda f: f.stat().st_mtime)
        fix_applied = ""
        files_changed = []
        if fix_files:
            try:
                fix = json.loads(fix_files[-1].read_text())
                fix_applied = fix.get("patch_suggestion", "")
                files_changed = fix.get("functions_to_edit", [])
            except (json.JSONDecodeError, OSError):
                pass

        attempt = {
            "attempt": attempt_num,
            "cause": cause,
            "fix_applied": fix_applied,
            "files_changed": files_changed,
            "commit_hash": self._get_project_head_commit(),
            "result": result,
        }
        if error_after:
            attempt["error_after"] = error_after

        history["attempts"].append(attempt)
        self._path.write_text(json.dumps(history, indent=2))

    def read(self) -> dict:
        """Read and return the current fix history."""
        return json.loads(self._path.read_text())


def extract_error_summary(log_path: Path, max_chars: int = 200) -> str:
    """Extract the actual test assertion error from a log file."""
    try:
        content = log_path.read_text(encoding="utf-8", errors="ignore")
        lines = content.strip().split("\n")
        # Look for the actual test error (AssertionError, TypeError, etc.)
        # Search top-down — first assertion/test error is the real one
        for line in lines:
            stripped = line.strip()
            if any(kw in stripped for kw in ["AssertionError:", "AssertionError:", "TypeError:", "Error:", "TimeoutError:"]):
                # Skip npm wrapper errors and generic lines
                if "npm error" in stripped or "command failed" in stripped or "exit code" in stripped:
                    continue
                return stripped[:max_chars]
        # Fallback: last non-empty line that isn't npm noise
        for line in reversed(lines):
            stripped = line.strip()
            if stripped and "npm error" not in stripped:
                return stripped[:max_chars]
    except OSError:
        pass
    return ""
