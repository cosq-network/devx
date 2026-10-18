"""Static analysis, AST scanning, and diff parsing modules."""
from __future__ import annotations

from devx.analysis.ast_scanner import CodebaseTopology, ModuleInfo, scan_module, scan_workspace
from devx.analysis.diff_analyzer import DiffAnalyzer, DiffHunk, FileDiff, parse_unified_diff

__all__ = [
    "CodebaseTopology",
    "DiffAnalyzer",
    "DiffHunk",
    "FileDiff",
    "ModuleInfo",
    "parse_unified_diff",
    "scan_module",
    "scan_workspace",
]
