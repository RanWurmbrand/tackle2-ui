"""Pipeline orchestrator for the RootCause AI fix flow."""

import json
import os
import random
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from core.run_report import RunReport
from core.fix_history import FixHistory, extract_error_summary
from core.skill_runner import SkillRunner

class Pipeline:
    """Orchestrates the full bug detection and fixing flow."""

    MAX_FIX_ATTEMPTS = 15
    MAX_CLEANER_ATTEMPTS = 15

    def __init__(self, root_dir: Path, skills_dir: Path):
        """Initialize the pipeline.

        Args:
            root_dir: Root directory of the pipeline
            skills_dir: Directory containing skill files
        """
        self._root_dir = root_dir
        self._skills_dir = skills_dir
        self._artifacts_dir = root_dir / "artifacts"

        # These are set during run()
        self.report: RunReport = None
        self.fix_history: FixHistory = None
        self.skill_runner: SkillRunner = None
        self._base_commit: str = ""  # Commit to restore to on give-up
        self._original_branch: str = ""  # Branch we were on before creating fix branch
        self._fix_branch: str = ""  # The fix branch we created

    def run(self) -> bool:
        """Run the full pipeline with loop support."""
        print("=" * 50)
        print("RootCause AI Pipeline")
        print("=" * 50)

        # Archive previous run artifacts before starting
        self._archive_previous_artifacts()

        # Create a new branch for this fix attempt
        print("\nCreating fix branch...")
        branch_name, base_commit = self._create_fix_branch()
        if not branch_name:
            print("Pipeline stopped: Could not create fix branch")
            return False
        self._base_commit = base_commit

        self.report = RunReport(self._artifacts_dir)
        print(f"Run report: {self.report.path}")

        self.fix_history = FixHistory(self._artifacts_dir, self.report.session_name)
        print(f"Fix history: {self.fix_history.path}")

        self.skill_runner = SkillRunner(self._root_dir, self._skills_dir)

        # Commentator diary (isolated from other skills)
        self._commentator_diary_dir = self._artifacts_dir / "commentator_diary"
        self._commentator_diary_dir.mkdir(parents=True, exist_ok=True)
        self._commentator_diary_path = self._commentator_diary_dir / "diary.json"

        # Cleaner tracking file
        self._cleaner_tracking_dir = self._artifacts_dir / "cleaner"
        self._cleaner_tracking_dir.mkdir(parents=True, exist_ok=True)
        self._cleaner_tracking_path = self._cleaner_tracking_dir / "tracking.json"

        # skip_tests avoids re-running tests we just saw fail.  It's set
        # to True in two places: (1) after a post-fix test run still fails
        # (so the next loop iteration jumps straight to analysis), and
        # (2) after a verification run proves the fix is flaky.
        skip_tests = False
        fix_attempted = False

        while True:
            # Step 1: Run tests (skipped if we just ran them in fix_and_rerun)
            if not skip_tests:
                success, all_passed = self._step_run_tests()
                if not success:
                    print("\nPipeline stopped: Test execution failed")
                    return False

                # If all tests passed on first run, no fixes needed
                if all_passed:
                    print("\n✓ All tests passed!")
                    return True
            skip_tests = False

            # Commentator observes after DOM capture
            self._step_commentator()

            # Step 3: Analyze
            if not self._step_analyze():
                print("\nPipeline stopped: Analysis failed")
                return False

            # Check if analyzer determined no fix is possible
            hint = self._read_latest_hint()
            analyzer_says_no_fix = hint.get("no_fix_possible", False) if hint else False

            # Step 4: Generate fix
            if not self._step_generate_fix():
                print("\nPipeline stopped: Fix generation failed")
                return False

            # Check if bug-fixer also determined no fix is possible
            fix_record = self._read_latest_fix()
            fixer_says_no_fix = fix_record.get("no_fix_possible", False) if fix_record else False

            # If both analyzer and fixer agree no fix is possible, give up early
            if analyzer_says_no_fix and fixer_says_no_fix:
                analyzer_reason = hint.get("no_fix_reason", hint.get("cause", "Unknown"))
                fixer_reason = fix_record.get("no_fix_reason", fix_record.get("reason", "Unknown"))
                print(f"\n✗ No fix possible - both analyzer and fixer agree")
                print(f"  Analyzer: {analyzer_reason}")
                print(f"  Fixer: {fixer_reason}")
                self._give_up_no_fix(analyzer_reason)
                return False

            # Step 5: Apply fix and rerun
            fix_attempted = True  # Mark that a fix has been attempted

            # Run tests after fix (bug-fixer already applied the fix)
            print("\nFix applied. Rerunning tests...")
            success, all_passed = self._step_run_tests()

            if not success:
                self.fix_history.record_attempt("failed", "Test execution failed")
                print("\nPipeline stopped: Test execution failed")
                return False

            if all_passed:
                # Verification run: run tests a second time to catch flaky
                # fixes that pass once due to timing but fail on retry.
                print("\n✓ Tests passed. Running verification...")
                success, verified = self._step_run_tests()
                if not success:
                    self.fix_history.record_attempt("failed", "Verification run failed")
                    print("\nPipeline stopped: Verification test execution failed")
                    return False

                if not verified:
                    # Verification failed - continue normal pipeline
                    print("\n⚠ Verification failed - fix may be flaky")
                    logs_dir = self._artifacts_dir / "rootcause_logs"
                    log_files = sorted(logs_dir.glob("run_*.log"), key=lambda f: f.stat().st_mtime)
                    error_summary = extract_error_summary(log_files[-1]) if log_files else ""
                    self.fix_history.record_attempt("failed", f"Verification failed: {error_summary}")
                    skip_tests = True
                    continue

                self.fix_history.record_attempt("passed")
                print("\n✓ All tests passed after fix (verified)!")

                # Run cleaner loop
                if not self._run_cleaner_loop():
                    print("\nPipeline stopped: Cleaner loop failed")
                    return False

                # Analyze impact - find all tests that might be affected
                self._step_impact_analysis()

                # Senior review loop - refactor if needed
                self._run_senior_review_loop()

                self._step_commit_fixes()
                return True

            # Tests still failing — record attempt with error summary
            logs_dir = self._artifacts_dir / "rootcause_logs"
            log_files = sorted(logs_dir.glob("run_*.log"), key=lambda f: f.stat().st_mtime)
            error_summary = extract_error_summary(log_files[-1]) if log_files else ""
            self.fix_history.record_attempt("failed", error_summary)

            # Check if we've exceeded max attempts
            attempt_count = len(self.fix_history.read().get("attempts", []))
            if attempt_count >= self.MAX_FIX_ATTEMPTS:
                print(f"\n✗ Giving up after {attempt_count} failed attempts")
                self._give_up_and_restore()
                return False

            # Continue pipeline — skip tests since we just ran them
            skip_tests = True
            continue

    # -------------------------------------------------------------------------
    # Pipeline Steps
    # -------------------------------------------------------------------------

    def _step_run_tests(self) -> tuple[bool, bool]:
        """Run the test suite. Returns (success, all_passed)."""
        print("\n[1/4] Running tests...")

        # Clean DOM snapshots before each test run
        self._clean_dom_snapshots()

        # Import here to avoid circular issues
        sys.path.insert(0, str(self._root_dir))
        from core.project_runner import ProjectRunner

        project_path = os.getenv("PROJECT_PATH")
        command = os.getenv("EXECUTE_COMMAND", "npm test")

        if not project_path:
            print("  ERROR: PROJECT_PATH not set in .env")
            return False, False

        try:
            if self.report:
                self.report.log("pipeline", "step", f"Running tests: {command}")
            runner = ProjectRunner(project_path, command)
            log_file, exit_code, _ = runner.run()
            print(f"  Log: {log_file}")
            print(f"  Exit code: {exit_code}")
            if self.report:
                self.report.log("pipeline", "step", f"Tests finished. Exit code: {exit_code}. Log: {log_file}")
            all_passed = exit_code == 0

            # Copy screenshots and DOM snapshots after every test run
            self._copy_screenshot()
            self._copy_dom_snapshot()

            return True, all_passed
        except Exception as e:
            print(f"  ERROR: {e}")
            if self.report:
                self.report.log("pipeline", "error", f"Test execution error: {e}")
            return False, False

    def _copy_screenshot(self):
        """Copy Cypress screenshot to artifacts before DOM capture."""
        project_path = os.getenv("PROJECT_PATH")
        if not project_path:
            return
        script = self._root_dir / "scripts" / "copy_cypress_screenshot.sh"
        try:
            subprocess.run(
                [str(script), project_path, str(self._artifacts_dir)],
                capture_output=True, text=True, timeout=30
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

    def _copy_dom_snapshot(self):
        """Copy DOM snapshot to artifacts."""
        project_path = os.getenv("PROJECT_PATH")
        if not project_path:
            return
        script = self._root_dir / "scripts" / "copy_dom_snapshot.sh"
        try:
            result = subprocess.run(
                [str(script), project_path, str(self._artifacts_dir)],
                capture_output=True, text=True, timeout=30
            )
            if result.stdout.strip():
                print(f"  {result.stdout.strip()}")
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

    def _step_analyze(self) -> bool:
        """Analyze test failure using trace-analyzer skill."""
        print("\n[2/4] Analyzing failure...")
        return self.skill_runner.run("trace-analyzer", timeout=3600, report=self.report, fix_history=self.fix_history)

    def _step_generate_fix(self) -> bool:
        """Generate and apply fix using bug-fixer skill."""
        print("\n[3/4] Generating and applying fix...")
        return self.skill_runner.run("bug-fixer", timeout=3600, report=self.report, fix_history=self.fix_history)

    def _step_commit_fixes(self) -> bool:
        """Commit all fixes using fix-committer skill."""
        print("\nCommitting fixes...")
        return self.skill_runner.run("fix-committer", timeout=600, report=self.report)

    def _step_commentator(self) -> bool:
        """Run commentator to observe and document in its private diary."""
        print("\n[Commentator] Observing...")
        return self.skill_runner.run(
            "commentator",
            timeout=300,
            report=self.report,
            fix_history=self.fix_history,
            diary_path=self._commentator_diary_path
        )

    def _step_cleaner(self) -> bool:
        """Run cleaner to remove unnecessary code from failed attempts."""
        print("\n[Cleaner] Cleaning...")
        return self.skill_runner.run(
            "cleaner",
            timeout=600,
            report=self.report,
            fix_history=self.fix_history,
            diary_path=self._commentator_diary_path,
            tracking_path=self._cleaner_tracking_path
        )

    def _run_cleaner_loop(self) -> bool:
        """Run cleaner loop: clean, test, restore if needed, repeat until pass."""
        print("\n[Cleaner] Starting cleanup loop...")

        cleaner_attempts = 0
        while True:
            cleaner_attempts += 1

            # Check if we've exceeded max attempts
            if cleaner_attempts > self.MAX_CLEANER_ATTEMPTS:
                print(f"\n✗ Cleaner giving up after {self.MAX_CLEANER_ATTEMPTS} failed attempts")
                self._give_up_and_restore()
                return False

            # Run cleaner
            if not self._step_cleaner():
                print("  Cleaner failed")
                return False

            # Run tests
            success, all_passed = self._step_run_tests()
            if not success:
                print("  Test execution failed during cleanup")
                return False

            if all_passed:
                print("  ✓ Tests still pass after cleanup")
                self._step_commit_cleanup()
                return True

            # Tests failed - cleaner will restore on next iteration
            print(f"  Tests failed after cleanup (attempt {cleaner_attempts}/{self.MAX_CLEANER_ATTEMPTS}), cleaner will restore...")

    def _step_commit_cleanup(self) -> bool:
        """Commit cleanup changes."""
        print("\n[Cleaner] Committing cleanup...")
        return self.skill_runner.run("fix-committer", timeout=600, report=self.report)

    def _give_up_and_restore(self):
        """Restore target repo to base commit and cleanup fix branch."""
        project_path = os.getenv("PROJECT_PATH")
        if project_path and self._base_commit:
            self._reset_to_commit(project_path, self._base_commit)
            print(f"  Restored repo to base commit {self._base_commit[:8]}")
        self._cleanup_fix_branch()

    def _step_impact_analysis(self) -> bool:
        """Analyze which tests might be affected by the changes."""
        print("\n[Impact] Analyzing affected tests...")
        return self.skill_runner.run(
            "impact-analyzer",
            timeout=600,
            report=self.report,
            fix_history=self.fix_history
        )

    def _step_senior_review(self) -> bool:
        """Run senior reviewer to check fix quality."""
        print("\n[Senior] Reviewing fix...")
        return self.skill_runner.run(
            "senior-reviewer",
            timeout=900,
            report=self.report
        )

    def _run_senior_review_loop(self):
        """Run senior review loop: review, refactor if needed, test, repeat."""
        print("\n[Senior] Starting review loop...")

        project_path = os.getenv("PROJECT_PATH")
        if not project_path:
            print("  ERROR: PROJECT_PATH not set")
            return

        # Save safe commit to revert to if refactoring breaks tests
        safe_commit = self._get_head_commit(project_path)
        max_iterations = 3

        for iteration in range(max_iterations):
            print(f"\n[Senior] Review iteration {iteration + 1}/{max_iterations}")

            # Run senior reviewer
            if not self._step_senior_review():
                print("  Senior review failed")
                return

            # Read decision from artifact
            review_file = self._artifacts_dir / "senior_review.json"
            if not review_file.exists():
                print("  No review file produced, assuming approved")
                return

            review = json.loads(review_file.read_text())
            decision = review.get("decision", "approved")

            if decision == "approved":
                print("  ✓ Fix approved by senior reviewer")
                return

            if decision == "rejected":
                reason = review.get("reason", "Fix weakens test instead of fixing bug")
                print(f"  ✗ Fix rejected: {reason}")
                # Revert to base commit - the fix is illegitimate
                base_commit = self.fix_history.read().get("base_commit", "")
                if base_commit:
                    self._reset_to_commit(project_path, base_commit)
                    print("  Reverted to base commit")
                return

            if decision == "stopped":
                print("  Senior reviewer stopped: " + review.get("reason", ""))
                # Reset to safe commit if we made any changes
                if iteration > 0:
                    self._reset_to_commit(project_path, safe_commit)
                return

            if decision == "refactoring":
                print("  Refactoring applied, running tests...")

                # Run tests
                success, all_passed = self._step_run_tests()
                if not success:
                    print("  Test execution failed, reverting...")
                    self._reset_to_commit(project_path, safe_commit)
                    return

                if all_passed:
                    print("  ✓ Tests pass after refactoring")
                    self._step_commit_fixes()
                    safe_commit = self._get_head_commit(project_path)
                    # Continue loop for more review
                else:
                    print("  Tests failed after refactoring, reverting...")
                    self._reset_to_commit(project_path, safe_commit)
                    # Continue loop - reviewer will see failure and decide

        print(f"  Max iterations ({max_iterations}) reached")

    def _get_head_commit(self, project_path: str) -> str:
        """Get current HEAD commit hash."""
        try:
            result = subprocess.run(
                ["git", "-C", project_path, "rev-parse", "HEAD"],
                capture_output=True, text=True, timeout=10
            )
            return result.stdout.strip() if result.returncode == 0 else ""
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return ""

    def _reset_to_commit(self, project_path: str, commit: str):
        """Reset project to a specific commit."""
        if not commit:
            return
        try:
            subprocess.run(
                ["git", "-C", project_path, "reset", "--hard", commit],
                capture_output=True, text=True, timeout=30
            )
            print(f"  Reset to commit {commit[:8]}")
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

    # -------------------------------------------------------------------------
    # Helper Methods
    # -------------------------------------------------------------------------

    def _read_latest_hint(self) -> dict | None:
        """Read the latest hint file from artifacts/hints/."""
        hints_dir = self._artifacts_dir / "hints"
        if not hints_dir.exists():
            return None
        hint_files = sorted(hints_dir.glob("hint_*.json"), key=lambda f: f.stat().st_mtime)
        if not hint_files:
            return None
        try:
            return json.loads(hint_files[-1].read_text())
        except (json.JSONDecodeError, OSError):
            return None

    def _read_latest_fix(self) -> dict | None:
        """Read the latest fix file from artifacts/bug_fixes/."""
        fixes_dir = self._artifacts_dir / "bug_fixes"
        if not fixes_dir.exists():
            return None
        fix_files = sorted(fixes_dir.glob("fix_*.json"), key=lambda f: f.stat().st_mtime)
        if not fix_files:
            return None
        try:
            return json.loads(fix_files[-1].read_text())
        except (json.JSONDecodeError, OSError):
            return None

    def _give_up_no_fix(self, reason: str):
        """Restore target repo to base commit when no fix is possible."""
        project_path = os.getenv("PROJECT_PATH")
        if project_path and self._base_commit:
            self._reset_to_commit(project_path, self._base_commit)
            print(f"  Restored repo to base commit {self._base_commit[:8]}")
        self._cleanup_fix_branch()

    def _create_fix_branch(self) -> tuple[str, str]:
        """Create a new branch for this fix attempt. Returns (branch_name, base_commit) or ("", "") on failure."""
        project_path = os.getenv("PROJECT_PATH")
        if not project_path:
            print("  ERROR: PROJECT_PATH not set")
            return "", ""

        # Save original branch name before creating fix branch
        try:
            result = subprocess.run(
                ["git", "-C", project_path, "rev-parse", "--abbrev-ref", "HEAD"],
                capture_output=True, text=True, timeout=10
            )
            self._original_branch = result.stdout.strip() if result.returncode == 0 else ""
        except (subprocess.TimeoutExpired, FileNotFoundError):
            self._original_branch = ""

        # Save base commit before creating branch
        base_commit = self._get_head_commit(project_path)

        # Extract test name from command
        execute_command = os.getenv("EXECUTE_COMMAND", "")
        test_name = self._extract_test_name_from_command(execute_command)
        test_name = self._sanitize_branch_name(test_name)

        # Generate branch name with random 8 digits
        random_suffix = str(random.randint(10000000, 99999999))
        branch_name = f"{test_name}-fix-{random_suffix}"

        try:
            # Create and checkout new branch
            result = subprocess.run(
                ["git", "-C", project_path, "checkout", "-b", branch_name],
                capture_output=True, text=True, timeout=30
            )
            if result.returncode != 0:
                print(f"  ERROR: Failed to create branch: {result.stderr}")
                return "", ""

            self._fix_branch = branch_name
            print(f"  Created branch: {branch_name}")
            return branch_name, base_commit

        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            print(f"  ERROR: {e}")
            return "", ""

    def _cleanup_fix_branch(self):
        """Checkout original branch and delete the fix branch."""
        project_path = os.getenv("PROJECT_PATH")
        if not project_path or not self._original_branch or not self._fix_branch:
            return

        try:
            subprocess.run(
                ["git", "-C", project_path, "checkout", self._original_branch],
                capture_output=True, text=True, timeout=30
            )
            subprocess.run(
                ["git", "-C", project_path, "branch", "-D", self._fix_branch],
                capture_output=True, text=True, timeout=30
            )
            print(f"  Deleted fix branch: {self._fix_branch}")
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

    def _extract_test_name_from_command(self, command: str) -> str:
        """Extract test name from EXECUTE_COMMAND for branch naming."""
        # Try to extract spec file from Cypress command (--spec "path/to/test.cy.ts")
        spec_match = re.search(r'--spec\s+["\']?([^"\']+)["\']?', command)
        if spec_match:
            spec_path = spec_match.group(1)
            # Get filename without extension (e.g., "my-test" from "cypress/e2e/my-test.cy.ts")
            filename = Path(spec_path).stem
            # Remove .cy suffix if present
            if filename.endswith('.cy'):
                filename = filename[:-3]
            return filename

        # Fallback: use last non-flag argument
        parts = command.split()
        for part in reversed(parts):
            if not part.startswith('-') and '/' not in part and part not in ('npx', 'npm', 'yarn', 'run', 'test', 'cypress'):
                return part

        return "test"

    def _sanitize_branch_name(self, name: str) -> str:
        """Sanitize a string to be a valid git branch name."""
        # Replace spaces and invalid chars with hyphens
        sanitized = re.sub(r'[^a-zA-Z0-9_-]', '-', name)
        # Remove consecutive hyphens
        sanitized = re.sub(r'-+', '-', sanitized)
        # Remove leading/trailing hyphens
        sanitized = sanitized.strip('-')
        return sanitized.lower() or "test"

    def _archive_previous_artifacts(self):
        """Archive artifacts from a previous run before starting a new one."""
        # Items that indicate a previous run exists
        items_to_archive = [
            "run_reports",
            "hints",
            "bug_fixes",
            "rootcause_logs",
            "suggestions",
            "fix_history.json",
            "dom_snapshots",
            "commentator_diary",
            "cleaner",
            "cleaner_report.json",
            "impact_analysis.json",
            "senior_review.json",
        ]

        # Check if there's anything to archive
        has_artifacts = any((self._artifacts_dir / item).exists() for item in items_to_archive)
        if not has_artifacts:
            return

        # Try to get session name from existing fix_history.json
        fix_history_path = self._artifacts_dir / "fix_history.json"
        session_name = None
        if fix_history_path.exists():
            try:
                history = json.loads(fix_history_path.read_text())
                session_name = history.get("session")
            except (json.JSONDecodeError, OSError):
                pass

        # Fallback to timestamp if no session name found
        if not session_name:
            session_name = f"run_{time.strftime('%Y-%m-%d_%H-%M-%S')}_archived"

        archive_base = self._root_dir / "previous_artifacts"
        archive_base.mkdir(parents=True, exist_ok=True)

        run_archive = archive_base / session_name
        run_archive.mkdir(exist_ok=True)

        for item in items_to_archive:
            src = self._artifacts_dir / item
            if src.exists():
                dst = run_archive / item
                if src.is_file():
                    shutil.move(str(src), str(dst))
                else:
                    shutil.copytree(src, dst, dirs_exist_ok=True)
                    shutil.rmtree(src)

        print(f"Archived previous artifacts to: {run_archive}")

    def _clean_dom_snapshots(self):
        """Remove DOM snapshots folder before running tests."""
        dom_snapshots_dir = self._artifacts_dir / "dom_snapshots"
        if dom_snapshots_dir.exists():
            shutil.rmtree(dom_snapshots_dir)
            print("  Cleaned DOM snapshots folder")
