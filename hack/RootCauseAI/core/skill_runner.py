"""Skill runner for executing Claude skills."""

import json
import os
import subprocess
import threading
from pathlib import Path

from core.run_report import RunReport
from core.fix_history import FixHistory


class SkillRunner:
    """Runs Claude skills as subprocesses."""

    # Each skill receives a different subset of context via its prompt
    # and --add-dir flags.  The tuples below control what each skill sees:
    #
    #   PROJECT_SKILLS     → get --add-dir <project_path> and the path in the prompt
    #   FIX_HISTORY_SKILLS → get the fix_history.json path so they can avoid
    #                         repeating failed approaches
    #   DIARY_SKILLS       → get the commentator diary path (for reading or writing)
    #   FULL_ACCESS_SKILLS → get both project and diary access via --add-dir
    #
    # A skill can appear in multiple tuples (e.g. "cleaner" is in all four).

    PROJECT_SKILLS = ("dom-capturer", "trace-analyzer", "bug-fixer", "fix-applier", "fix-committer", "cleaner", "impact-analyzer", "senior-reviewer")
    FIX_HISTORY_SKILLS = ("trace-analyzer", "bug-fixer", "cleaner")
    DIARY_SKILLS = ("commentator", "cleaner")
    FULL_ACCESS_SKILLS = ("commentator", "cleaner")

    def __init__(self, root_dir: Path, skills_dir: Path):
        """Initialize the skill runner.

        Args:
            root_dir: Root directory of the pipeline
            skills_dir: Directory containing skill files
        """
        self._root_dir = root_dir
        self._skills_dir = skills_dir

    def run(self, skill_name: str, timeout: int = 7200, cwd: str = None,
            report: RunReport = None, fix_history: FixHistory = None,
            diary_path: Path = None, tracking_path: Path = None) -> bool:
        """Run a Claude skill and return success/failure."""
        skill_path = self._skills_dir / f"{skill_name}.skill.md"

        if not skill_path.exists():
            print(f"  ERROR: Skill not found: {skill_path}")
            return False

        skill_content = skill_path.read_text()
        work_dir = cwd or str(self._root_dir)

        prompt = self._build_prompt(skill_name, report, fix_history, diary_path, tracking_path)
        cmd = self._build_command(skill_name, skill_content, prompt)

        # Log skill start (skip commentator to keep it isolated)
        if report and skill_name != "commentator":
            report.log(skill_name, "started", f"Pipeline launched {skill_name}")

        # Start tailing the report file
        stop_event = None
        tail_thread = None
        if report:
            stop_event = threading.Event()
            tail_thread = threading.Thread(target=report.tail, args=(stop_event,), daemon=True)
            tail_thread.start()

        try:
            result = subprocess.run(
                cmd,
                cwd=work_dir,
                timeout=timeout,
                stdin=subprocess.DEVNULL
            )
            success = result.returncode == 0
            if report and skill_name != "commentator":
                status = "success" if success else "failed"
                report.log(skill_name, "finished", f"{skill_name} {status} (exit code {result.returncode})")
            return success

        except subprocess.TimeoutExpired:
            print(f"  ERROR: Skill timed out after {timeout}s")
            if report:
                report.log(skill_name, "error", f"Timed out after {timeout}s")
            return False
        except FileNotFoundError:
            print("  ERROR: Claude CLI not found")
            if report:
                report.log(skill_name, "error", "Claude CLI not found")
            return False
        finally:
            if stop_event:
                stop_event.set()
            if tail_thread:
                tail_thread.join(timeout=2)

    def _build_prompt(self, skill_name: str, report: RunReport = None,
                      fix_history: FixHistory = None, diary_path: Path = None,
                      tracking_path: Path = None) -> str:
        """Build the prompt for a skill."""
        prompt = "Execute the task defined in the system prompt."

        # Add target project dir for skills that need project access
        project_path = os.getenv("PROJECT_PATH")
        if skill_name in self.PROJECT_SKILLS and project_path:
            prompt += f" The target project is at: {project_path}"

        # Pass test command to dom-capturer
        if skill_name == "dom-capturer":
            execute_command = os.getenv("EXECUTE_COMMAND", "npm test")
            prompt += f" Execute command: {execute_command}"

        # Add fix history path
        if skill_name in self.FIX_HISTORY_SKILLS and fix_history:
            prompt += f" Fix history file: {fix_history.path}"

            # After 10 failed attempts the trace-analyzer may be stuck
            # in a loop, re-diagnosing the same cause.  Inject the last
            # 5 commentator observations so it can see the pattern and
            # change strategy.
            if skill_name == "trace-analyzer":
                try:
                    history = fix_history.read()
                    attempts = len(history.get("attempts", []))
                    if attempts >= 10:
                        diary_file = self._root_dir / "artifacts" / "commentator_diary" / "diary.json"
                        if diary_file.exists():
                            diary = json.loads(diary_file.read_text())
                            entries = diary.get("entries", [])[-5:]
                            comments = [e.get("comment", "") for e in entries if e.get("comment")]
                            if comments:
                                prompt += f" IMPORTANT - After {attempts} failed attempts, here are observations from the commentator (last 5 runs): {'; '.join(comments)}"
                except (json.JSONDecodeError, OSError):
                    pass

        # Diary skills get fix history + diary path
        if skill_name in self.DIARY_SKILLS:
            if fix_history:
                prompt += f" Fix history file: {fix_history.path}"
            if diary_path:
                prompt += f" Diary path: {diary_path}"

        # Cleaner gets tracking path
        if skill_name == "cleaner" and tracking_path:
            prompt += f" Tracking file: {tracking_path}"

        # Add report path
        if report:
            prompt += f" Run report file: {report.path}"

        return prompt

    def _build_command(self, skill_name: str, skill_content: str, prompt: str) -> list:
        """Build the command to run a skill."""
        cmd = [
            "claude",
            "--print",
            "--dangerously-skip-permissions",
            "--model", "claude-opus-4-5",
        ]

        # Add root dir access
        cmd.extend(["--add-dir", str(self._root_dir)])

        # Add project access for relevant skills
        project_path = os.getenv("PROJECT_PATH")
        if skill_name in self.PROJECT_SKILLS and project_path:
            cmd.extend(["--add-dir", project_path])

        # Full access skills get everything
        if skill_name in self.FULL_ACCESS_SKILLS and project_path:
            cmd.extend(["--add-dir", project_path])

        cmd.extend(["--system-prompt", skill_content, prompt])

        return cmd
