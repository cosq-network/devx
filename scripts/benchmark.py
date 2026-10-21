"""Run deterministic, offline benchmarks for the local developer experience."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

CASES_PATH = ROOT / "benchmarks" / "cases.json"


def _run_case(case: dict[str, Any]) -> dict[str, Any]:
    from devx.harness.changes import ChangeJournal
    from devx.harness.permissions import PermissionError, PermissionPolicy
    from devx.harness.tools import WorkspaceTools
    from devx.token_optim.context import read_context

    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="devx-benchmark-") as directory:
        workspace = Path(directory)
        kind = case["kind"]
        if kind == "bounded_read":
            target = workspace / "large.txt"
            target.write_text("x" * 4096, encoding="utf-8")
            tools = WorkspaceTools(
                PermissionPolicy(workspace, approval_required=False),
                max_bytes=case["max_bytes"],
            )
            output = tools.read(target).output
            assert len(output.encode()) <= case["max_bytes"]
            metrics = {"output_bytes": len(output.encode())}
        elif kind == "command_safety":
            tools = WorkspaceTools(PermissionPolicy(workspace, approval_required=False))
            result = tools.run(["rm", "-rf", "."], approved=True)
            if result.ok:
                raise AssertionError("destructive command was not blocked")
            metrics = {"blocked": True}
        elif kind == "journal_rollback":
            journal = ChangeJournal(workspace / "changes.db")
            target = workspace / "file.txt"
            target.write_text("before", encoding="utf-8")
            tools = WorkspaceTools(
                PermissionPolicy(workspace, approval_required=False),
                journal=journal,
                run_id="benchmark-run",
            )
            tools.write(target, "after", approved=True)
            restored = journal.rollback("benchmark-run")
            assert target in [Path(item) for item in restored]
            assert target.read_text(encoding="utf-8") == "before"
            metrics = {"restored_files": len(restored)}
        elif kind == "context_budget":
            paths = []
            for index in range(4):
                target = workspace / f"file-{index}.txt"
                target.write_text("content " * 256, encoding="utf-8")
                paths.append(target)
            output = read_context(paths, max_file_bytes=4096, max_total_bytes=case["max_bytes"])
            assert len(output.encode()) <= case["max_bytes"]
            metrics = {"context_bytes": len(output.encode())}
        else:  # pragma: no cover - cases are maintained in cases.json
            raise ValueError(f"Unknown benchmark case: {kind}")
    return {
        "name": case["name"],
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
        "metrics": metrics,
    }


def run_benchmarks(selected: set[str] | None = None) -> list[dict[str, Any]]:
    cases = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    if selected:
        cases = [case for case in cases if case["name"] in selected]
        missing = selected - {case["name"] for case in cases}
        if missing:
            raise ValueError("Unknown benchmark case(s): " + ", ".join(sorted(missing)))
    return [_run_case(case) for case in cases]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", action="append", help="Run only this case; repeatable")
    args = parser.parse_args()
    results = run_benchmarks(set(args.case or []))
    print(json.dumps({"results": results}, indent=2))


if __name__ == "__main__":
    main()
