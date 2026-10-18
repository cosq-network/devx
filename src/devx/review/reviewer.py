"""Automated multi-perspective code review engine."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from devx.analysis.diff_analyzer import DiffAnalyzer
from devx.config import Settings
from devx.llm import create_fallback_llm, create_llm, invoke_with_retry
from devx.rules import discover_project_rules, format_rules_for_prompt


@dataclass
class ReviewComment:
    file: str
    line: int | None
    severity: str  # "BLOCKER", "WARNING", "SUGGESTION", "NIT"
    critic: str  # "Security", "Performance", "Correctness", "Maintainability"
    message: str
    suggested_patch: str | None = None


@dataclass
class ReviewReport:
    verdict: str  # "APPROVED", "CHANGES_REQUESTED", "COMMENTED"
    summary: str
    comments: list[ReviewComment] = field(default_factory=list)
    risk_factors: list[str] = field(default_factory=list)
    linter_findings: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)

    def to_markdown(self) -> str:
        lines = [
            f"# Code Review Report — {self.verdict}",
            f"*Generated at: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(self.created_at))}*",
            "",
            "## Summary",
            self.summary,
            "",
        ]

        if self.risk_factors:
            lines.extend(["## ⚠️ Risk Factors", ""])
            for rf in self.risk_factors:
                lines.append(f"- {rf}")
            lines.append("")

        if self.linter_findings:
            lines.extend(["## 🔍 Static Analysis Findings", ""])
            for lf in self.linter_findings:
                lines.append(f"- `{lf}`")
            lines.append("")

        if self.comments:
            lines.extend(["## 💬 Review Comments", ""])
            for c in self.comments:
                loc = f"`{c.file}`" + (f":{c.line}" if c.line else "")
                lines.append(f"### [{c.severity}] {loc} ({c.critic})")
                lines.append(c.message)
                if c.suggested_patch:
                    lines.extend(["```diff", c.suggested_patch, "```"])
                lines.append("")
        else:
            lines.append("✅ No critical issues found.")

        return "\n".join(lines)


class CodeReviewer:
    """Executes multi-perspective code reviews with local static analysis pre-checks."""

    def __init__(self, settings: Settings, workspace: Path | None = None):
        self.settings = settings
        self.workspace = workspace or settings.workspace
        self.diff_analyzer = DiffAnalyzer(self.workspace)

    def run_local_linters(self, modified_files: list[str]) -> list[str]:
        """Run project linters locally on modified files to gather factual diagnostics."""
        findings: list[str] = []
        if not modified_files or not self.workspace:
            return findings

        # Run ruff check if python files are modified
        py_files = [f for f in modified_files if f.endswith(".py")]
        if py_files and shutil.which("ruff"):
            try:
                res = subprocess.run(
                    ["ruff", "check", "--quiet", *py_files[:20]],
                    cwd=str(self.workspace),
                    capture_output=True,
                    text=True,
                    timeout=15,
                    check=False,
                )
                if res.stdout:
                    for line in res.stdout.splitlines()[:10]:
                        if line.strip():
                            findings.append(line.strip())
            except Exception:
                pass

        return findings

    def review_diff(self, diff_text: str, focus: str = "all") -> ReviewReport:
        """Run an intelligent multi-perspective review on a unified git diff."""
        if not diff_text.strip():
            return ReviewReport(
                verdict="APPROVED",
                summary="Diff is empty. No changes to review.",
            )

        # 1. Deterministic diff analysis
        analysis = self.diff_analyzer.analyze(diff_text)
        modified_paths = [f.new_path or f.old_path for f in analysis.files]

        # 2. Local static analysis pre-checks
        linter_findings = self.run_local_linters(modified_paths)

        # 3. Discovered project rules
        rules = discover_project_rules(self.workspace)
        rules_prompt = format_rules_for_prompt(rules)

        # 4. Prompt construction for 4-critic review
        prompt = (
            "You are an elite, thorough code reviewer inspecting a pull request / git diff.\n"
            "Analyze the changes through 4 distinct critic lenses:\n"
            "1. **Security Critic**: Authentication, input validation, SQL/command injection, secrets, path traversal.\n"
            "2. **Performance Critic**: Big-O algorithmic complexity, resource leaks, N+1 queries, unclosed handles.\n"
            "3. **Correctness Critic**: Boundary/null conditions, off-by-one errors, exception handling, race conditions.\n"
            "4. **Maintainability Critic**: Clean code, readability, adherence to project conventions.\n\n"
        )

        if rules_prompt:
            prompt += f"Project Rules to enforce:\n{rules_prompt}\n\n"

        if linter_findings:
            prompt += "Local Static Analysis (Linter) Confirmed Issues:\n"
            prompt += "\n".join(f"- {f}" for f in linter_findings) + "\n\n"

        if analysis.risk_factors:
            prompt += "Detected Diff Risk Factors:\n"
            prompt += "\n".join(f"- {rf}" for rf in analysis.risk_factors) + "\n\n"

        # Chunk diff if necessary
        chunks = self.diff_analyzer.chunk_diff(diff_text, max_chunk_chars=12_000)
        review_diff_snippet = chunks[0] if chunks else diff_text[:12_000]

        prompt += (
            f"Git Diff:\n```diff\n{review_diff_snippet}\n```\n\n"
            "Respond in JSON format with this structure:\n"
            "{\n"
            '  "verdict": "APPROVED" | "CHANGES_REQUESTED" | "COMMENTED",\n'
            '  "summary": "Overall review summary explaining key findings",\n'
            '  "comments": [\n'
            "    {\n"
            '      "file": "path/to/file",\n'
            '      "line": 42,\n'
            '      "severity": "BLOCKER" | "WARNING" | "SUGGESTION" | "NIT",\n'
            '      "critic": "Security" | "Performance" | "Correctness" | "Maintainability",\n'
            '      "message": "Detailed actionable explanation",\n'
            '      "suggested_patch": "optional diff snippet"\n'
            "    }\n"
            "  ]\n"
            "}"
        )

        # 5. LLM execution with fallback
        try:
            llm = create_llm(self.settings)
            fb_llm = create_fallback_llm(self.settings)
            response = invoke_with_retry(
                llm,
                prompt,
                provider=self.settings.provider,
                fallback_model=fb_llm,
                fallback_provider=getattr(self.settings, "fallback_provider", ""),
            )
            raw_text = str(getattr(response, "content", response))
            return self._parse_review_response(raw_text, analysis.risk_factors, linter_findings)
        except Exception as exc:
            # Fallback deterministic report if model call fails
            return ReviewReport(
                verdict="CHANGES_REQUESTED" if analysis.risk_factors else "COMMENTED",
                summary=f"Model review failed ({exc}); local analysis completed.",
                risk_factors=analysis.risk_factors,
                linter_findings=linter_findings,
            )

    def _parse_review_response(
        self,
        raw_text: str,
        risk_factors: list[str],
        linter_findings: list[str],
    ) -> ReviewReport:
        # Extract json block
        json_match = re.search(r"\{.*\}", raw_text, re.DOTALL)
        if json_match:
            try:
                data = json.loads(json_match.group(0))
                comments: list[ReviewComment] = []
                for c in data.get("comments", []):
                    comments.append(
                        ReviewComment(
                            file=c.get("file", ""),
                            line=c.get("line"),
                            severity=c.get("severity", "WARNING").upper(),
                            critic=c.get("critic", "Correctness"),
                            message=c.get("message", ""),
                            suggested_patch=c.get("suggested_patch"),
                        )
                    )
                return ReviewReport(
                    verdict=data.get("verdict", "COMMENTED").upper(),
                    summary=data.get("summary", "Review complete."),
                    comments=comments,
                    risk_factors=risk_factors,
                    linter_findings=linter_findings,
                )
            except Exception:
                pass

        # Text fallback if json parsing fails
        return ReviewReport(
            verdict="COMMENTED",
            summary=raw_text[:2000],
            risk_factors=risk_factors,
            linter_findings=linter_findings,
        )

    def save_report(self, report: ReviewReport, name: str = "") -> Path:
        """Save review report to .devx/reviews/."""
        reviews_dir = self.workspace / ".devx" / "reviews"
        reviews_dir.mkdir(parents=True, exist_ok=True)
        filename = f"review_{int(report.created_at)}" + (f"_{name}" if name else "") + ".md"
        target = reviews_dir / filename
        target.write_text(report.to_markdown(), encoding="utf-8")
        return target
