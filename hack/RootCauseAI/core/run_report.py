"""Run report management for pipeline progress tracking."""

import json
import threading
import time
from pathlib import Path


class RunReport:
    """Manages a JSONL run report file for tracking pipeline progress."""

    def __init__(self, artifacts_dir: Path):
        """Create a new JSONL run report file."""
        reports_dir = artifacts_dir / "run_reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        timestamp = time.strftime("%Y-%m-%d_%H-%M-%S")
        self._path = reports_dir / f"run_{timestamp}.jsonl"
        self._path.touch()

    @property
    def path(self) -> Path:
        """Get the report file path."""
        return self._path

    @property
    def session_name(self) -> str:
        """Get the session name (filename without extension)."""
        return self._path.stem

    def log(self, skill: str, event: str, message: str, **extra):
        """Append a JSONL entry to the run report."""
        entry = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "skill": skill,
            "event": event,
            "message": message,
            **extra,
        }
        with open(self._path, "a") as f:
            f.write(json.dumps(entry) + "\n")

    def tail(self, stop_event: threading.Event):
        """Tail the report file and print new entries in real-time."""
        with open(self._path, "r") as f:
            while not stop_event.is_set():
                line = f.readline()
                if line.strip():
                    try:
                        entry = json.loads(line.strip())
                        event = entry.get("event", "?")
                        message = entry.get("message", "")
                        skill = entry.get("skill", "?")
                        agent = entry.get("agent", "")
                        agent_str = f" ({agent})" if agent else ""
                        print(f"  [{skill}] {event}{agent_str}: {message}", flush=True)
                    except json.JSONDecodeError:
                        print(f"  {line.strip()}", flush=True)
                else:
                    stop_event.wait(0.5)
