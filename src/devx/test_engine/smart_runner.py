"""Smart test runner with Test-Impact Analysis (TIA), flakiness prober, and integration preflight."""
from __future__ import annotations

import os
import shutil
import socket
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from devx.analysis.ast_scanner import scan_workspace
from devx.config import Settings
from devx.harness.testing import detect_test_command


@dataclass
class FlakinessReport:
    test_target: str
    total_runs: int
    passes: int
    failures: int
    is_flaky: bool
    durations: list[float] = field(default_factory=list)
    failure_outputs: list[str] = field(default_factory=list)

    @property
    def pass_rate(self) -> float:
        return (self.passes / self.total_runs) * 100 if self.total_runs else 0.0


@dataclass
class PreflightReport:
    ok: bool
    checks: list[dict[str, Any]] = field(default_factory=list)  # [{"name": str, "ok": bool, "detail": str}]


class SmartTestRunner:
    """Manages selective test execution, flakiness probing, and integration preflight checks."""

    def __init__(self, settings: Settings, workspace: Path | None = None):
        self.settings = settings
        self.workspace = workspace or settings.workspace

    def get_impacted_tests(self, modified_files: list[str] | None = None) -> list[str]:
        """Find test files impacted by modified source files using Test-Impact Analysis (TIA)."""
        if modified_files is None:
            modified_files = self._get_git_modified_files()

        if not modified_files:
            return []

        topology = scan_workspace(self.workspace)
        topology.build_internal_graph()

        impacted: list[str] = []
        for file in modified_files:
            if file.startswith("tests/") or file.startswith("test/"):
                if file not in impacted:
                    impacted.append(file)
            else:
                matches = topology.map_source_to_test(file)
                for m in matches:
                    if m not in impacted:
                        impacted.append(m)

        return impacted

    def _get_git_modified_files(self) -> list[str]:
        """Get modified and untracked files from git status."""
        try:
            res = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=str(self.workspace),
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            files = []
            for line in res.stdout.splitlines():
                parts = line.strip().split()
                if len(parts) >= 2:
                    files.append(parts[-1])
            return files
        except Exception:
            return []

    def build_targeted_test_command(self, test_files: list[str]) -> str | None:
        """Construct a targeted test command to run only the specified test files."""
        if not test_files:
            return None

        # Check Python pytest
        if (self.workspace / "pyproject.toml").exists() or (self.workspace / "tests").is_dir():
            files_arg = " ".join(test_files)
            return f"python -m pytest {files_arg} -q"

        # Check package.json / npm
        if (self.workspace / "package.json").is_file():
            files_arg = " ".join(test_files)
            return f"npm test -- {files_arg}"

        # Fallback to default test command
        return detect_test_command(self.workspace)

    def check_flakiness(self, test_target: str, runs: int = 3, timeout_per_run: float = 30) -> FlakinessReport:
        """Probe a test target multiple times in isolated processes to verify determinism and stability."""
        passes = 0
        failures = 0
        durations: list[float] = []
        failure_outputs: list[str] = []

        base_cmd = ["python", "-m", "pytest", test_target, "-q"]

        for i in range(runs):
            t0 = time.time()
            try:
                env = os.environ.copy()
                env["PYTHONHASHSEED"] = str(i * 42)  # vary hash seed for ordering robustness
                res = subprocess.run(
                    base_cmd,
                    cwd=str(self.workspace),
                    capture_output=True,
                    text=True,
                    timeout=timeout_per_run,
                    env=env,
                    check=False,
                )
                duration = time.time() - t0
                durations.append(round(duration, 3))

                if res.returncode == 0:
                    passes += 1
                else:
                    failures += 1
                    failure_outputs.append(res.stdout + res.stderr)
            except subprocess.TimeoutExpired:
                durations.append(timeout_per_run)
                failures += 1
                failure_outputs.append(f"Timeout expired after {timeout_per_run}s")

        is_flaky = passes > 0 and failures > 0

        return FlakinessReport(
            test_target=test_target,
            total_runs=runs,
            passes=passes,
            failures=failures,
            is_flaky=is_flaky,
            durations=durations,
            failure_outputs=failure_outputs[:2],
        )

    def run_preflight(
        self,
        required_ports: list[int] | None = None,
        required_envs: list[str] | None = None,
        check_docker: bool = False,
    ) -> PreflightReport:
        """Verify environment dependencies before running integration tests."""
        checks: list[dict[str, Any]] = []
        overall_ok = True

        # 1. Environment variables
        for var in required_envs or []:
            val = os.environ.get(var)
            is_set = bool(val and val.strip())
            checks.append({
                "name": f"env:{var}",
                "ok": is_set,
                "detail": "Set and non-empty" if is_set else "Missing or empty",
            })
            if not is_set:
                overall_ok = False

        # 2. Port connectivity
        for port in required_ports or []:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1.0)
            res = sock.connect_ex(("127.0.0.1", port))
            sock.close()
            is_open = (res == 0)
            checks.append({
                "name": f"port:{port}",
                "ok": is_open,
                "detail": f"Listening on 127.0.0.1:{port}" if is_open else f"Port {port} is closed",
            })
            if not is_open:
                overall_ok = False

        # 3. Docker connectivity
        if check_docker:
            docker_ok = False
            detail = "Docker not found in PATH"
            if shutil.which("docker"):
                try:
                    res = subprocess.run(
                        ["docker", "info"],
                        capture_output=True,
                        timeout=5,
                        check=False,
                    )
                    if res.returncode == 0:
                        docker_ok = True
                        detail = "Docker daemon responsive"
                    else:
                        detail = "Docker daemon is not running"
                except Exception as exc:
                    detail = str(exc)

            checks.append({
                "name": "service:docker",
                "ok": docker_ok,
                "detail": detail,
            })
            if not docker_ok:
                overall_ok = False

        return PreflightReport(ok=overall_ok, checks=checks)
