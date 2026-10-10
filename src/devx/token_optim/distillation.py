from __future__ import annotations

import re


def distill_test_output(output: str, max_chars: int = 4000) -> str:
    """Extract actionable failure diagnostics from test or linter output.

    Strips passed test listings, banners, environment summaries, and progress dots,
    preserving only the failing assertions, traceback frames, and line diagnostics.
    Falls back to a bounded raw representation if no specific format is matched.
    """
    if not output or not output.strip():
        return ""

    text = output.strip()

    # 1. Pytest failures
    pytest_failures = _extract_pytest_failures(text)
    if pytest_failures:
        return _bounded(pytest_failures, max_chars)

    # 2. Python traceback
    python_tb = _extract_python_traceback(text)
    if python_tb:
        return _bounded(python_tb, max_chars)

    # 3. JS/TS (Vitest / Jest)
    jest_failures = _extract_jest_failures(text)
    if jest_failures:
        return _bounded(jest_failures, max_chars)

    # 4. Linter output (Ruff / Flake8 / ESLint)
    linter_errors = _extract_linter_errors(text)
    if linter_errors:
        return _bounded(linter_errors, max_chars)

    # 5. Generic fallback: isolate lines with error markers or tail
    return _bounded(_fallback_distillation(text), max_chars)


def _bounded(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    # Keep the head and tail of the distilled diagnostics
    half = max(100, (limit - 20) // 2)
    return text[:half].rstrip() + "\n... [truncated diagnostic] ...\n" + text[-half:].lstrip()


def _extract_pytest_failures(text: str) -> str | None:
    # Look for pytest FAILURES or ERRORS delimiter
    failures_match = re.search(r"=+\s+(?:FAILURES|ERRORS)\s+=+(.*?)(?:=+\s+short test summary info\s+=+|=+\s+\d+ failed|\Z)", text, re.DOTALL)
    if failures_match:
        failures_section = failures_match.group(1).strip()
        # Also grab the short test summary info if present
        summary_match = re.search(r"(=+\s+short test summary info\s+=+.*?)(?:=+\s+\d+ failed|\Z)", text, re.DOTALL)
        summary = summary_match.group(1).strip() if summary_match else ""
        if summary:
            return f"{failures_section}\n\n{summary}"
        return failures_section

    # Short format with FAILED test_name
    failed_lines = [line for line in text.splitlines() if "FAILED" in line or line.startswith("E   ") or line.startswith("E  ")]
    if failed_lines and any("FAILED" in line for line in failed_lines):
        return "\n".join(failed_lines)

    return None


def _extract_python_traceback(text: str) -> str | None:
    if "Traceback (most recent call last):" not in text:
        return None
    tb_matches = list(re.finditer(r"Traceback \(most recent call last\):.*?(?:\n[A-Za-z_][A-Za-z0-9_]*Error:.*|\n[A-Za-z_][A-Za-z0-9_]*Exception:.*|\Z)", text, re.DOTALL))
    if tb_matches:
        # Return all traceback blocks concatenated
        return "\n\n".join(m.group(0).strip() for m in tb_matches)
    return None


def _extract_jest_failures(text: str) -> str | None:
    # Jest/Vitest style: FAIL ... followed by ● test title
    if "FAIL " in text or "● " in text:
        lines = text.splitlines()
        extracted: list[str] = []
        capturing = False
        for line in lines:
            if line.startswith("FAIL ") or "● " in line or "AssertionError" in line or "Expected:" in line or "Received:" in line:
                capturing = True
            if capturing:
                extracted.append(line)
            if "Test Suites:" in line:
                extracted.append(line)
                break
        if extracted:
            return "\n".join(extracted)
    return None


def _extract_linter_errors(text: str) -> str | None:
    # Ruff/Flake8/ESLint file:line:col: error_code message
    error_pattern = re.compile(r"^.+:\d+:\d+:?\s+[A-Za-z0-9_-]+.*$")
    lines = text.splitlines()
    matched = [line for line in lines if error_pattern.match(line.strip())]
    if matched:
        return "\n".join(matched)
    return None


def _fallback_distillation(text: str) -> str:
    lines = text.splitlines()
    error_indicators = ("error", "fail", "failed", "exception", "exit code", "fatal", "denied")
    relevant = [line for line in lines if any(ind in line.lower() for ind in error_indicators)]
    if relevant:
        return "\n".join(relevant)
    # If no keywords matched, return the tail lines
    return "\n".join(lines[-20:])
