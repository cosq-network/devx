"""Semantic git diff analyzer and hunk chunking engine."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class DiffHunk:
    old_start: int
    old_lines: int
    new_start: int
    new_lines: int
    header: str
    lines: list[str] = field(default_factory=list)

    @property
    def added_lines(self) -> list[str]:
        return [line[1:] for line in self.lines if line.startswith("+") and not line.startswith("+++")]

    @property
    def deleted_lines(self) -> list[str]:
        return [line[1:] for line in self.lines if line.startswith("-") and not line.startswith("---")]


@dataclass
class FileDiff:
    old_path: str
    new_path: str
    is_new: bool = False
    is_deleted: bool = False
    is_rename: bool = False
    hunks: list[DiffHunk] = field(default_factory=list)

    @property
    def additions_count(self) -> int:
        return sum(len(h.added_lines) for h in self.hunks)

    @property
    def deletions_count(self) -> int:
        return sum(len(h.deleted_lines) for h in self.hunks)


def parse_unified_diff(diff_text: str) -> list[FileDiff]:
    """Parse a unified git diff text into structured FileDiff and DiffHunk objects."""
    files: list[FileDiff] = []
    current_file: FileDiff | None = None
    current_hunk: DiffHunk | None = None

    file_header_re = re.compile(r"^diff --git a/(.*?) b/(.*)$")
    hunk_header_re = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$")

    for line in diff_text.splitlines():
        file_match = file_header_re.match(line)
        if file_match:
            if current_hunk and current_file:
                current_file.hunks.append(current_hunk)
                current_hunk = None
            if current_file:
                files.append(current_file)
            old_p, new_p = file_match.group(1), file_match.group(2)
            current_file = FileDiff(old_path=old_p, new_path=new_p)
            continue

        if current_file is None:
            continue

        if line.startswith("new file mode"):
            current_file.is_new = True
            continue
        if line.startswith("deleted file mode"):
            current_file.is_deleted = True
            continue
        if line.startswith("similarity index") or line.startswith("rename from"):
            current_file.is_rename = True
            continue

        hunk_match = hunk_header_re.match(line)
        if hunk_match:
            if current_hunk:
                current_file.hunks.append(current_hunk)
            old_start = int(hunk_match.group(1))
            old_lines = int(hunk_match.group(2) or 1)
            new_start = int(hunk_match.group(3))
            new_lines = int(hunk_match.group(4) or 1)
            header = hunk_match.group(5).strip()
            current_hunk = DiffHunk(
                old_start=old_start,
                old_lines=old_lines,
                new_start=new_start,
                new_lines=new_lines,
                header=header,
            )
            continue

        if current_hunk is not None:
            current_hunk.lines.append(line)

    if current_hunk and current_file:
        current_file.hunks.append(current_hunk)
    if current_file:
        files.append(current_file)

    return files


@dataclass
class DiffAnalysisReport:
    total_files: int
    total_additions: int
    total_deletions: int
    risk_factors: list[str]
    modified_symbols: dict[str, list[str]]  # file -> list of function/class names
    files: list[FileDiff]


class DiffAnalyzer:
    """Performs risk assessment, symbol mapping, and chunking on code diffs."""

    def __init__(self, workspace: Path | None = None):
        self.workspace = workspace

    def analyze(self, diff_text: str) -> DiffAnalysisReport:
        files = parse_unified_diff(diff_text)
        total_add = sum(f.additions_count for f in files)
        total_del = sum(f.deletions_count for f in files)
        risk_factors: list[str] = []
        modified_symbols: dict[str, list[str]] = {}

        for f in files:
            path_str = f.new_path or f.old_path
            # Check risk factors
            if f.is_deleted and ("test" in path_str.lower() or "spec" in path_str.lower()):
                risk_factors.append(f"Deleted test file: {path_str}")

            if "test" not in path_str.lower() and ("auth" in path_str.lower() or "secret" in path_str.lower() or "perm" in path_str.lower()):
                risk_factors.append(f"Modified security-sensitive file: {path_str}")

            symbols = []
            for h in f.hunks:
                if h.header:
                    symbols.append(h.header)
                # Check for suspicious added strings
                for added in h.added_lines:
                    if re.search(r"""(password|secret|api_key|token)[a-zA-Z0-9_]*\s*=\s*['"][A-Za-z0-9_\-]{8,}['"]""", added, re.I):
                        risk_factors.append(f"Possible hardcoded credential added in {path_str}")
                        break

            if symbols:
                modified_symbols[path_str] = list(dict.fromkeys(symbols))

        return DiffAnalysisReport(
            total_files=len(files),
            total_additions=total_add,
            total_deletions=total_del,
            risk_factors=list(dict.fromkeys(risk_factors)),
            modified_symbols=modified_symbols,
            files=files,
        )

    def chunk_diff(self, diff_text: str, max_chunk_chars: int = 15_000) -> list[str]:
        """Split a large unified diff into bounded chunks without cutting through hunks."""
        files = parse_unified_diff(diff_text)
        if not files:
            return [diff_text[:max_chunk_chars]] if diff_text else []

        chunks: list[str] = []
        current_chunk: list[str] = []
        current_len = 0

        for f in files:
            file_header = f"diff --git a/{f.old_path} b/{f.new_path}\n"
            if f.is_new:
                file_header += "new file mode\n"
            elif f.is_deleted:
                file_header += "deleted file mode\n"

            for h in f.hunks:
                hunk_text = f"@@ -{h.old_start},{h.old_lines} +{h.new_start},{h.new_lines} @@ {h.header}\n"
                hunk_text += "\n".join(h.lines) + "\n"

                block = file_header + hunk_text if not current_chunk else hunk_text
                if current_len + len(block) > max_chunk_chars and current_chunk:
                    chunks.append("".join(current_chunk))
                    current_chunk = [file_header + hunk_text]
                    current_len = len(current_chunk[0])
                else:
                    current_chunk.append(block)
                    current_len += len(block)

        if current_chunk:
            chunks.append("".join(current_chunk))

        return chunks
