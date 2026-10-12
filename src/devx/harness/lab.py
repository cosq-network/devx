"""Disposable remote workspace for demos and onboarding (``devx lab``).

``devx lab up`` boots a throwaway ``linuxserver/openssh-server`` container,
initializes an empty workspace root inside it, and prints ready-to-paste
``--remote`` / ``--container`` URIs. ``devx lab down <name>`` tears it down.

Uses the same image and options as the opt-in SSH end-to-end test suite, so a
demo literally runs against the same byte path the CI job exercises.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import time
import uuid

LAB_IMAGE = os.environ.get("DEVX_E2E_IMAGE", "lscr.io/linuxserver/openssh-server:latest")
LAB_USER = "devx"
LAB_PASSWORD = "devxpass"
LAB_ROOT = "/config/repo"
LAB_HOST = "127.0.0.1"

_WAIT_TIMEOUT = 120


def docker_available() -> bool:
    """Return whether a usable Docker daemon is present."""
    if not shutil.which("docker"):
        return False
    try:
        subprocess.run(["docker", "info"], capture_output=True, timeout=30, check=True)
        return True
    except Exception:
        return False


def _wait_for_port(host: str, port: int, timeout: float = _WAIT_TIMEOUT) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.settimeout(1.0)
            if probe.connect_ex((host, port)) == 0:
                return
        time.sleep(0.5)
    raise TimeoutError(f"sshd did not become ready on {host}:{port} within {timeout}s")


def start_lab() -> dict[str, str]:
    """Boot a disposable remote workspace and return its connection details.

    Raises ``RuntimeError`` when Docker is unavailable. Returns a dict with
    ``name``, ``port``, ``ssh_uri``, ``container_uri`` and ``remote_path``.
    """
    if not docker_available():
        raise RuntimeError("Docker is required for 'devx lab up' but was not found or not running")

    name = f"devx-lab-{uuid.uuid4().hex[:8]}"
    subprocess.run(
        [
            "docker", "run", "-d", "--rm", "--name", name, "-p", "127.0.0.1::2222",
            "-e", "PUID=1000", "-e", "PGID=1000", "-e", "TZ=UTC",
            "-e", "PASSWORD_ACCESS=true",
            "-e", f"USER_PASSWORD={LAB_PASSWORD}",
            "-e", f"USER_NAME={LAB_USER}",
            LAB_IMAGE,
        ],
        check=True, capture_output=True, timeout=300,
    )
    try:
        mapping = subprocess.run(
            ["docker", "port", name, "2222"], check=True, capture_output=True, timeout=60
        ).stdout.decode().strip()
        port = int(mapping.rsplit(":", 1)[-1])
        _wait_for_port(LAB_HOST, port)
        subprocess.run(
            ["docker", "exec", "-u", "root", name, "sh", "-c",
             f"mkdir -p {LAB_ROOT} && chown -R 1000:1000 {LAB_ROOT}"],
            check=True, capture_output=True, timeout=60,
        )
    except Exception:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=60)
        raise

    ssh_uri = f"ssh://{LAB_USER}@{LAB_HOST}:{port}{LAB_ROOT}"
    return {
        "name": name,
        "port": str(port),
        "ssh_uri": ssh_uri,
        "container_uri": f"container://{name}{LAB_ROOT}",
        "remote_path": LAB_ROOT,
    }


def stop_lab(name: str) -> None:
    """Force-remove a lab container (idempotent)."""
    subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=60)