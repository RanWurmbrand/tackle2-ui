"""Manages Minikube deployment lifecycle and test environment setup."""

import json
import os
import subprocess
import time
from pathlib import Path
from xml.etree import ElementTree


class Deployer:
    """Handles Minikube deployment refresh, port-forwarding, and test discovery."""

    NAMESPACE = "konveyor-tackle"
    PORT_FORWARD_LOCAL = 9000
    PORT_FORWARD_REMOTE = 8080

    def __init__(self, project_path: str):
        self.project_path = Path(project_path).resolve()
        # tackle2-ui root is one level above the cypress/ directory
        self.tackle2_ui_root = self.project_path.parent
        self._port_forward_proc = None

    # ------------------------------------------------------------------
    # 1. Minikube health check
    # ------------------------------------------------------------------

    def check_minikube(self):
        """Verify minikube is running. Raises RuntimeError if not."""
        print("[Deploy] Checking minikube status...")
        try:
            result = subprocess.run(
                ["minikube", "status", "--format", "{{.Host}}"],
                capture_output=True, text=True, timeout=30,
            )
        except FileNotFoundError:
            raise RuntimeError("minikube not found. Install it first.")

        status = result.stdout.strip()
        if status != "Running":
            raise RuntimeError(
                f"Minikube is not running (status: {status}). "
                "Start it with: minikube start"
            )
        print("  Minikube is running")

    # ------------------------------------------------------------------
    # 2. Pull latest test code
    # ------------------------------------------------------------------

    def pull_latest_tests(self):
        """Fetch upstream, checkout main, rebase, and keep the custom commit on top."""
        repo = str(self.tackle2_ui_root)

        print("[Deploy] Pulling latest test code...")

        # Stash any dirty changes so checkout/rebase can proceed
        subprocess.run(
            ["git", "-C", repo, "stash", "--include-untracked"],
            capture_output=True, text=True, timeout=30,
        )

        # Make sure we're on main
        subprocess.run(
            ["git", "-C", repo, "checkout", "main"],
            capture_output=True, text=True, timeout=30, check=True,
        )

        subprocess.run(
            ["git", "-C", repo, "fetch", "upstream"],
            capture_output=True, text=True, timeout=120, check=True,
        )

        # Rebase main onto upstream/main — keeps any local commits on top
        result = subprocess.run(
            ["git", "-C", repo, "rebase", "upstream/main"],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode != 0:
            # Abort failed rebase and fall back to reset + cherry-pick
            subprocess.run(
                ["git", "-C", repo, "rebase", "--abort"],
                capture_output=True, text=True, timeout=10,
            )
            raise RuntimeError(
                f"Rebase failed — resolve manually:\n{result.stderr}"
            )

        print("  Rebased main onto upstream/main")

        print("[Deploy] Installing cypress dependencies...")
        subprocess.run(
            ["npm", "install", "--ignore-scripts"],
            cwd=str(self.project_path),
            timeout=600, check=True,
        )
        print("  Dependencies installed")

    # ------------------------------------------------------------------
    # 3. Refresh Konveyor deployment (pull latest images)
    # ------------------------------------------------------------------

    def refresh_deployment(self):
        """Delete the Tackle CR and re-create it so fresh images are pulled."""
        kubectl = self._get_kubectl()

        print("[Deploy] Refreshing Konveyor deployment...")

        # Delete existing Tackle CR (operator will clean up pods)
        print("  Deleting Tackle CR...")
        subprocess.run(
            kubectl + [
                "delete", "-n", self.NAMESPACE,
                "tackles.tackle.konveyor.io/tackle",
                "--ignore-not-found",
            ],
            capture_output=True, text=True, timeout=120,
        )

        # Wait for tackle pods to terminate
        print("  Waiting for pods to terminate...")
        self._wait_for_pods_terminated()

        # Re-create via setup-operator.sh (idempotent — skips operator
        # install if CRD already exists, then creates the Tackle CR).
        # IMAGE_PULL_POLICY defaults to Always in the script.
        print("  Re-creating Tackle CR (pulling latest images)...")
        setup_script = self.tackle2_ui_root / "hack" / "setup-operator.sh"
        env = os.environ.copy()
        env["IMAGE_PULL_POLICY"] = "Always"
        env["NAMESPACE"] = self.NAMESPACE

        subprocess.run(
            ["bash", str(setup_script)],
            env=env, timeout=900, check=True,
        )
        print("  Deployment refreshed")

    # ------------------------------------------------------------------
    # 4. Port-forwarding
    # ------------------------------------------------------------------

    def start_port_forward(self):
        """Start kubectl port-forward to tackle-ui in the background."""
        kubectl = self._get_kubectl()
        port_spec = f"{self.PORT_FORWARD_LOCAL}:{self.PORT_FORWARD_REMOTE}"

        print(f"[Deploy] Starting port-forward (localhost:{self.PORT_FORWARD_LOCAL} -> tackle-ui:{self.PORT_FORWARD_REMOTE})...")

        # Kill anything already listening on that port
        subprocess.run(
            ["bash", "-c", f"lsof -ti:{self.PORT_FORWARD_LOCAL} | xargs -r kill 2>/dev/null"],
            capture_output=True, timeout=10,
        )
        time.sleep(1)

        self._port_forward_proc = subprocess.Popen(
            kubectl + [
                "port-forward", "-n", self.NAMESPACE,
                "svc/tackle-ui", port_spec,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        # Give it a moment, then check it's alive
        time.sleep(3)
        if self._port_forward_proc.poll() is not None:
            raise RuntimeError("Port-forward process exited immediately")
        print("  Port-forward active")

    def stop_port_forward(self):
        """Terminate the background port-forward process."""
        if self._port_forward_proc:
            self._port_forward_proc.terminate()
            try:
                self._port_forward_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._port_forward_proc.kill()
            self._port_forward_proc = None
            print("[Deploy] Port-forward stopped")

    # ------------------------------------------------------------------
    # 5. Run CI tests
    # ------------------------------------------------------------------

    CI_TAG_GROUPS = [
        "@ci,@tier0,@tier2_A,@tier2_B",
        "@tier2_secretsNeeded,@tier3_secretsNeeded",
        "@tier2_taskManager",
        "@tier3_A,@tier3_B,@tier3_C",
        "@tier3_D,@tier3_E,@tier3_F",
    ]

    def run_ci_tests(self, artifacts_dir: Path) -> list[str]:
        """Run the same tests as the nightly CI and return failing test paths."""
        print("[Deploy] Running CI tests...")

        # Clean previous test results
        run_dir = self.project_path / "run"
        if run_dir.exists():
            import shutil
            shutil.rmtree(run_dir)

        all_tags = ",".join(self.CI_TAG_GROUPS)
        print(f"  Running with tags: {all_tags}")

        env = os.environ.copy()
        env["CYPRESS_INCLUDE_TAGS"] = all_tags
        env["CYPRESS_EXCLUDE_TAGS"] = "cf,downstream"
        env["CYPRESS_FAIL_FAST_ENABLED"] = "false"

        result = subprocess.run(
            ["npm", "run", "e2e:run:local"],
            cwd=str(self.project_path),
            env=env,
            timeout=7200,
        )

        print(f"  CI tests exit code: {result.returncode}")

        if result.returncode == 0:
            print("  All tests passed — nothing to fix")
            return []

        # Parse JUnit XML reports for failing test file paths
        junit_dir = self.project_path / "run" / "report" / "junit"
        failing_tests = self._parse_junit_failures(junit_dir)

        # Persist to failing_tests.json
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        output_file = artifacts_dir / "failing_tests.json"
        output_file.write_text(json.dumps(failing_tests, indent=2))

        print(f"  Found {len(failing_tests)} failing test(s)")
        for t in failing_tests:
            print(f"    - {t}")

        return failing_tests

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _parse_junit_failures(self, junit_dir: Path) -> list[str]:
        """Extract unique test file paths from JUnit XML files that contain failures."""
        failing_files: set[str] = set()

        if not junit_dir.exists():
            print(f"  Warning: JUnit directory not found at {junit_dir}")
            return []

        for xml_file in junit_dir.glob("*.xml"):
            try:
                tree = ElementTree.parse(xml_file)
                root = tree.getroot()

                if root.find(".//failure") is None:
                    continue

                # The JUnit XML produced by cypress has a file= attribute
                # on <testsuite> or <testcase> elements.
                for elem in root.iter():
                    file_attr = elem.get("file")
                    if file_attr:
                        failing_files.add(file_attr)
                        break
            except ElementTree.ParseError:
                continue

        return sorted(failing_files)

    def _wait_for_pods_terminated(self, timeout: int = 300):
        """Block until all tackle pods are gone."""
        kubectl = self._get_kubectl()
        deadline = time.time() + timeout

        while time.time() < deadline:
            result = subprocess.run(
                kubectl + [
                    "get", "pods", "-n", self.NAMESPACE,
                    "-l", "app.kubernetes.io/part-of=tackle",
                    "--no-headers",
                ],
                capture_output=True, text=True, timeout=30,
            )
            if not result.stdout.strip():
                return
            time.sleep(5)

        print("  Warning: timed out waiting for pods to terminate, continuing anyway")

    def _get_kubectl(self) -> list[str]:
        """Return the kubectl command as a list (plain kubectl or minikube wrapper)."""
        result = subprocess.run(
            ["which", "kubectl"], capture_output=True, timeout=5,
        )
        if result.returncode == 0:
            return ["kubectl"]
        return ["minikube", "kubectl", "--"]
