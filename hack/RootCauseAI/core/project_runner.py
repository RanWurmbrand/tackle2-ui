# project_runner.py
import subprocess
import time
from pathlib import Path
import os
ROOT = Path(__file__).resolve().parents[1]
class ProjectRunner:
    def __init__(self, project_path: str, command: str = "npm test"):
        self.project_path = Path(project_path).resolve()
        self.command = command
        self.logs_dir = ROOT / "artifacts" / "rootcause_logs"
        self.logs_dir.mkdir(exist_ok=True)
        
        self.collect_output = os.getenv("COLLECT_OUTPUT_LOGS", "").lower() == "true"
        self.output_log_name = os.getenv("OUTPUT_LOG_NAME", "")

        if not self.project_path.is_dir():
            raise ValueError(f"Invalid project path: {self.project_path}")
    def _find_latest_output_log(self) -> Path | None:
        """Find the most recent log file matching OUTPUT_LOG_NAME."""
        if not self.output_log_name:
            return None
        
        matching_files = []
        for log_file in self.project_path.rglob("*.log"):
            if self.output_log_name in log_file.name:
                matching_files.append(log_file)
        
        if not matching_files:
            return None
        
        return max(matching_files, key=lambda p: p.stat().st_mtime)
    
    def _collect_output_log(self, timestamp: str) -> Path | None:
        """Copy the latest output log into the artifacts directory."""
        latest = self._find_latest_output_log()
        if not latest:
            print(f"[runner] Output log not found: {self.output_log_name}")
            return None
        
        dest = self.output_logs_dir / f"output_{timestamp}.log"
        content = latest.read_text(encoding="utf-8", errors="ignore")
        dest.write_text(content, encoding="utf-8")
        
        print(f"[runner] Collected output log: {latest.name} -> {dest}")
        return dest

    
    def run(self) -> Path:
        timestamp = time.strftime("%Y-%m-%d_%H-%M-%S")
        log_file = self.logs_dir / f"run_{timestamp}.log"

        print(f"[runner] Running: {self.command}")
        print(f"[runner] Project: {self.project_path}")
        print(f"[runner] Log file: {log_file}")

        # Ensure Playwright generates traces for DOM inspection
        env = os.environ.copy()
        env['PLAYWRIGHT_TRACE'] = 'retain-on-failure'

        with open(log_file, "w", encoding="utf-8") as log:
            process = subprocess.Popen(
                self.command,
                cwd=self.project_path,
                shell=True,
                stdout=log,
                stderr=log,
                text=True,
                env=env
            )
            process.wait()

        print(f"[runner] Done. Exit code: {process.returncode}")
        print(f"[runner] Log saved to: {log_file}")

        output_log = None
        if self.collect_output:
            output_log = self._collect_output_log(timestamp)

        return log_file, process.returncode, output_log


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python project_runner.py /path/to/project [command]")
        exit(1)

    project_path = sys.argv[1]
    command = sys.argv[2] if len(sys.argv) > 2 else "npm test"

    runner = ProjectRunner(project_path, command)
    runner.run()
