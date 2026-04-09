#!/usr/bin/env python3
"""
RootCause AI Pipeline

A single script that runs the full bug detection and fixing flow:
  1. Run tests
  2. Analyze failure (trace-analyzer skill)
  3. Generate fix (bug-fixer skill)
  4. Notify via Telegram
  5. Apply fix if approved (fix-applier skill)
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from dotenv import load_dotenv

from core.pipeline import Pipeline

# Setup paths
ROOT = Path(__file__).parent
SKILLS_DIR = ROOT / "skills"

load_dotenv(override=True)


def fetch_nightly_failures():
    """Run get_failing_tests.sh and return list of failing test paths."""
    script = ROOT / "scripts" / "get_failing_tests.sh"
    subprocess.run([str(script)], check=True)

    failing_tests_file = ROOT / "artifacts" / "failing_tests.json"
    if failing_tests_file.exists():
        return json.loads(failing_tests_file.read_text())
    return []


def run_ci_cycle():
    """Run CI tests locally, then run RootCauseAI on every failure."""
    from core.deployer import Deployer

    project_path = os.getenv("PROJECT_PATH")
    if not project_path:
        print("ERROR: PROJECT_PATH not set in .env")
        sys.exit(1)

    artifacts_dir = ROOT / "artifacts"
    deployer = Deployer(project_path)

    failing_tests = deployer.run_ci_tests(artifacts_dir)

    if not failing_tests:
        print("\nNo failures found. Nothing to fix.")
        return 0

    return run_on_failing_tests(failing_tests)


def run_full_cycle():
    """Full cycle: refresh deployment on minikube, pull latest tests, run
    the discovery suite, then run RootCauseAI on every failure."""
    from core.deployer import Deployer

    project_path = os.getenv("PROJECT_PATH")
    if not project_path:
        print("ERROR: PROJECT_PATH not set in .env")
        sys.exit(1)

    artifacts_dir = ROOT / "artifacts"
    deployer = Deployer(project_path)

    try:
        # 1. Verify minikube is up
        deployer.check_minikube()

        # 2. Pull latest test code & install deps
        #deployer.pull_latest_tests()

        # 3. Refresh Konveyor deployment (delete/recreate CR → pulls latest images)
        deployer.refresh_deployment()

        # 4. Port-forward so Cypress can reach the UI
        deployer.start_port_forward()

        # 5. Fetch failing tests from nightly CI report
        failing_tests = fetch_nightly_failures()

        if not failing_tests:
            print("\nNo failures found. Nothing to fix.")
            return 0

        # 6. Iterate over failures — same logic as --nightly
        return run_on_failing_tests(failing_tests)

    except Exception as e:
        print(f"\nFull-cycle error: {e}")
        return 1
    finally:
        deployer.stop_port_forward()


def run_on_failing_tests(failing_tests: list[str]) -> int:
    """Run the fix pipeline on each failing test. Returns exit code."""
    print(f"Found {len(failing_tests)} failing tests")
    for test in failing_tests:
        print(f"  - {test}")

    results = []
    for i, test in enumerate(failing_tests, 1):
        print(f"\n{'='*50}")
        print(f"[{i}/{len(failing_tests)}] Running pipeline for: {test}")
        print("="*50)

        os.environ["EXECUTE_COMMAND"] = f'npm run e2e:run:local -- --headed --spec "{test}"'

        pipeline = Pipeline(ROOT, SKILLS_DIR)
        success = pipeline.run()
        results.append({"test": test, "success": success})

        if not success:
            print(f"\nPipeline failed for {test}, continuing to next...")

    print(f"\n{'='*50}")
    print("Run complete")
    print("="*50)
    passed = sum(1 for r in results if r["success"])
    print(f"Results: {passed}/{len(results)} succeeded")
    for r in results:
        status = "pass" if r["success"] else "FAIL"
        print(f"  {status} {r['test']}")

    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="RootCause AI Pipeline")
    parser.add_argument("--nightly", action="store_true",
                        help="Fetch failing tests from nightly CI and run pipeline on each")
    parser.add_argument("--ft", action="store_true",
                        help="Run pipeline on existing failing_tests.json without fetching")
    parser.add_argument("--full", action="store_true",
                        help="Full cycle: refresh minikube deployment, pull latest tests, "
                             "run e2e suite, then fix every failure")
    parser.add_argument("--ci", action="store_true",
                        help="Run CI tests locally, then fix every failure")
    args = parser.parse_args()

    try:
        if args.ci:
            sys.exit(run_ci_cycle())

        if args.full:
            sys.exit(run_full_cycle())

        if args.ft:
            failing_tests_file = ROOT / "artifacts" / "failing_tests.json"
            if not failing_tests_file.exists():
                print("No failing_tests.json found. Run --nightly first.")
                sys.exit(1)
            failing_tests = json.loads(failing_tests_file.read_text())
        elif args.nightly:
            failing_tests = fetch_nightly_failures()
        else:
            failing_tests = None

        if failing_tests is not None:
            sys.exit(run_on_failing_tests(failing_tests))

        pipeline = Pipeline(ROOT, SKILLS_DIR)
        success = pipeline.run()
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\n\nPipeline interrupted by user")
        sys.exit(1)
