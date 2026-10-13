"""Lightweight secret scanning for the workspace.

devx takes a "don't silently ship secrets to the model" posture: anything the
agent reads that looks like a private key, API token, or credential assignment
is flagged (and optionally redacted or blocked) before it can reach the LLM.
The same scanner powers the CI-friendly ``devx scan-secrets`` command.

The matchers here are deliberately heuristic: they trade a little recall for a
much lower false-positive rate than dumping entropy scores on the whole
workspace. They are a tripwire, not a vault.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

# Scan modes understood by Settings.secret_scan_mode / WorkspaceTools.
SECRET_SCAN_MODES = ("off", "warn", "redact", "block")
SECRET_SCAN_MODE_DEFAULT = "warn"

# Operation modes of secret scanning
SCAN_MODE_OFF = "off"
SCAN_MODE_WARN = "warn"
SCAN_MODE_REDACT = "redact"
SCAN_MODE_BLOCK = "block"


@dataclass(frozen=True)
class SecretHit:
    """A single potential secret found on one line of a file."""

    kind: str
    line: int
    snippet: str


# (kind, pattern) pairs, tried in order on each line.
_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("private-key", re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")),
    ("aws-access-key-id", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("aws-secret-access-key", re.compile(r"(?i)\baws[._-]?secret[._-]?access[._-]?key\b\s*[:=]\s*[\"']?[A-Za-z0-9/+=]{40}")),
    ("github-token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("github-fine-grained-pat", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{22,}\b")),
    ("openai-key", re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}\b")),
    ("google-api-key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("slack-token", re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}\b")),
    ("stripe-live-key", re.compile(r"\b(?:sk|rk)_live_[0-9A-Za-z]{24,}\b")),
    ("uri-credentials", re.compile(r"\b[a-z][a-z0-9+.-]*://[^/\s:@]+:[^/\s:@]+@")),
    (
        "generic-assignment",
        re.compile(
            r"(?i)\b(?:api[._-]?key|secret[._-]?key|client[._-]?secret|access[._-]?token|auth[._-]?token|password|passwd)\b"
            r"\s*[:=]\s*[\"']?[A-Za-z0-9_\-+/]{16,}"
        ),
    ),
]

# Directories excluded from whole-tree scans; ``backups`` holds pre-write copies
# of files the agent already read/wrote, so re-flagging them is noise.
_SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".tox",
    ".nox",
    "dist",
    "build",
    "backups",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".devx",
}

# Binary-ish extensions never scanned.
_SKIP_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".svg",
    ".woff", ".woff2", ".ttf", ".otf", ".eot",
    ".pyc", ".pyo", ".so", ".dll", ".exe", ".dylib", ".zip", ".gz", ".bz2", ".xz",
}

_MAX_SCAN_FILE_BYTES = 1_000_000


def mask_secret(value: str, visible: int = 4) -> str:
    """Mask a matched secret for display/log output.

    Only the first and last ``visible`` characters are kept, joined by ``...``
    (plain ASCII so the result is safe on cp1252 consoles and in CI logs). The
    raw value is never returned; callers that need the actual secret for
    redaction use :func:`_spans` against the original text instead.
    """
    if len(value) <= visible * 2:
        return value
    return f"{value[:visible]}...{value[-visible:]}"


def scan_secrets(data: str) -> list[SecretHit]:
    """Return every detected secret match in ``data``, with line numbers.

    ``SecretHit.snippet`` is pre-masked so nothing downstream (CLI output, tool
    warnings, logs) can echo a live credential.
    """
    hits: list[SecretHit] = []
    for line_no, line in enumerate(data.splitlines(), start=1):
        for kind, pattern in _PATTERNS:
            for match in pattern.finditer(line):
                hits.append(SecretHit(kind=kind, line=line_no, snippet=mask_secret(match.group(0))))
    return hits


def scan_bytes(data: bytes) -> list[SecretHit]:
    """Scan raw bytes, decoding lossily first."""
    return scan_secrets(data.decode("utf-8", errors="replace"))


def scan_file(path: str | Path) -> list[SecretHit]:
    """Scan one file and return its hits. OSErrors and oversized files yield []."""
    candidate = Path(path)
    try:
        if candidate.stat().st_size > _MAX_SCAN_FILE_BYTES:
            return []
        return scan_bytes(candidate.read_bytes())
    except OSError:
        return []


def redact_secrets(data: str) -> str:
    """Return ``data`` with every detected secret replaced by ``[REDACTED]``."""
    merged: list[tuple[int, int]] = []
    for start, end in sorted((span for _, span, _ in _spans(data)), key=lambda item: item[0]):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    if not merged:
        return data
    parts: list[str] = []
    cursor = 0
    for start, end in merged:
        parts.append(data[cursor:start])
        parts.append("[REDACTED]")
        cursor = end
    parts.append(data[cursor:])
    return "".join(parts)


def _spans(data: str) -> list[tuple[str, tuple[int, int], str]]:
    """Return (kind, (start, end), snippet) for every match across the text."""
    found = []
    for kind, pattern in _PATTERNS:
        for match in pattern.finditer(data):
            found.append((kind, match.span(), match.group(0)))
    return found


def scan_tree(root: str | Path) -> list[tuple[str, SecretHit]]:
    """Scan a directory tree, returning ``(relative_path, hit)`` pairs.

    Well-known dependency/build directories and binary files are skipped, and
    files larger than 1 MB are ignored so the scan stays fast on real repos.
    """
    root_dir = Path(root).expanduser()
    found: list[tuple[str, SecretHit]] = []
    if not root_dir.is_dir():
        return found
    for path in sorted(root_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root_dir)
        parts = rel.parts
        if any(part in _SKIP_DIRS for part in parts):
            continue
        if path.suffix.lower() in _SKIP_SUFFIXES:
            continue
        for hit in scan_file(path):
            found.append((rel.as_posix(), hit))
    return found