"""System design, architectural diagramming, ADR synthesis, and deep research engine."""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from devx.analysis.ast_scanner import CodebaseTopology, scan_workspace
from devx.config import Settings


@dataclass
class ArchitecturalDecisionRecord:
    number: int
    title: str
    status: str  # "Proposed", "Accepted", "Superseded", "Rejected"
    context: str
    decision: str
    consequences: str
    alternatives: str = ""
    date: str = field(default_factory=lambda: time.strftime("%Y-%m-%d"))

    def to_markdown(self) -> str:
        lines = [
            f"# ADR-{self.number:04d}: {self.title}",
            "",
            f"**Date**: {self.date}  ",
            f"**Status**: {self.status}",
            "",
            "## Context",
            self.context,
            "",
            "## Decision",
            self.decision,
            "",
            "## Consequences",
            self.consequences,
        ]
        if self.alternatives:
            lines.extend(["", "## Alternatives Considered", self.alternatives])
        return "\n".join(lines)


class ArchitectureEngine:
    """Provides client-side system architecture topology, diagram synthesis, ADRs, and research."""

    def __init__(self, settings: Settings, workspace: Path | None = None):
        self.settings = settings
        self.workspace = workspace or settings.workspace
        self._topology: CodebaseTopology | None = None

    @property
    def topology(self) -> CodebaseTopology:
        if self._topology is None:
            self._topology = scan_workspace(self.workspace)
        return self._topology

    def generate_diagram(self, kind: str = "flowchart", focus: str = "all") -> str:
        """Synthesize a clean Mermaid diagram representing codebase architecture."""
        top = self.topology
        top.build_internal_graph()

        if kind == "class":
            return self._generate_class_diagram(top, focus)
        elif kind == "sequence":
            return self._generate_sequence_diagram(top, focus)
        else:
            return self._generate_flowchart_diagram(top, focus)

    def _generate_flowchart_diagram(self, top: CodebaseTopology, focus: str) -> str:
        lines = ["flowchart TD"]
        rendered_edges = 0
        seen_nodes: set[str] = set()

        for src, targets in top._dep_graph.items():
            if focus != "all" and focus.lower() not in src.lower():
                continue
            src_clean = Path(src).stem.replace("-", "_")
            if src_clean not in seen_nodes:
                lines.append(f'    {src_clean}["{src_clean}"]')
                seen_nodes.add(src_clean)

            for tgt in sorted(targets):
                tgt_clean = Path(tgt).stem.replace("-", "_")
                if tgt_clean not in seen_nodes:
                    lines.append(f'    {tgt_clean}["{tgt_clean}"]')
                    seen_nodes.add(tgt_clean)
                lines.append(f"    {src_clean} --> {tgt_clean}")
                rendered_edges += 1
                if rendered_edges > 35:
                    break
            if rendered_edges > 35:
                break

        if rendered_edges == 0:
            lines.append('    Empty["No cross-module connections found"]')

        return "\n".join(lines)

    def _generate_class_diagram(self, top: CodebaseTopology, focus: str) -> str:
        lines = ["classDiagram"]
        class_count = 0

        for rel_path, mod in top.modules.items():
            if focus != "all" and focus.lower() not in rel_path.lower():
                continue
            for sym in mod.symbols:
                if sym.kind == "class":
                    clean_name = sym.name.replace("-", "_")
                    lines.append(f"    class {clean_name}")
                    for base_name, _ in sym.params:
                        clean_base = base_name.replace("-", "_")
                        lines.append(f"    {clean_base} <|-- {clean_name}")
                    class_count += 1
                    if class_count >= 20:
                        return "\n".join(lines)

        if class_count == 0:
            lines.append("    class Architecture")
            lines.append("    Architecture : +explore()")

        return "\n".join(lines)

    def _generate_sequence_diagram(self, top: CodebaseTopology, focus: str) -> str:
        lines = ["sequenceDiagram", "    autonumber"]
        seq_count = 0

        for rel_path, mod in top.modules.items():
            if focus != "all" and focus.lower() not in rel_path.lower():
                continue
            caller = Path(rel_path).stem.replace("-", "_")
            for sym in mod.symbols:
                if sym.kind in ("function", "async_function") and sym.calls:
                    for called in sym.calls[:2]:
                        target = called.split(".")[0].replace("-", "_")
                        if target != caller:
                            lines.append(f"    {caller}->>+{target}: {sym.name}()")
                            lines.append(f"    {target}-->>-{caller}: return")
                            seq_count += 1
                            if seq_count >= 12:
                                return "\n".join(lines)

        if seq_count == 0:
            lines.append("    User->>+DevX: command")
            lines.append("    DevX-->>-User: response")

        return "\n".join(lines)

    def create_adr(
        self,
        title: str,
        context: str,
        decision: str,
        consequences: str,
        alternatives: str = "",
        status: str = "Proposed",
    ) -> tuple[ArchitecturalDecisionRecord, Path]:
        """Create and persist a new Architectural Decision Record in .devx/design/."""
        design_dir = self.workspace / ".devx" / "design"
        design_dir.mkdir(parents=True, exist_ok=True)

        # Determine next ADR number
        existing_adrs = list(design_dir.glob("ADR-*.md"))
        next_num = 1
        for adr_file in existing_adrs:
            m = re.match(r"^ADR-(\d+)", adr_file.name)
            if m:
                next_num = max(next_num, int(m.group(1)) + 1)

        record = ArchitecturalDecisionRecord(
            number=next_num,
            title=title,
            status=status,
            context=context,
            decision=decision,
            consequences=consequences,
            alternatives=alternatives,
        )

        slug = re.sub(r"[^a-zA-Z0-9_\-]+", "-", title.lower()).strip("-")
        filename = f"ADR-{next_num:04d}-{slug}.md"
        target_path = design_dir / filename
        target_path.write_text(record.to_markdown(), encoding="utf-8")

        return record, target_path

    def deep_research_trace(self, symbol_name: str) -> dict[str, Any]:
        """Perform recursive multi-hop symbol tracing across the codebase."""
        top = self.topology
        results: dict[str, Any] = {
            "symbol": symbol_name,
            "definitions": [],
            "callers": [],
            "dependencies": [],
        }

        # 1. Locate definitions
        for rel_path, mod in top.modules.items():
            for sym in mod.symbols:
                if sym.name == symbol_name or sym.name.endswith(f".{symbol_name}"):
                    results["definitions"].append({
                        "file": rel_path,
                        "line": sym.line,
                        "end_line": sym.end_line,
                        "kind": sym.kind,
                        "params": sym.params,
                        "exceptions": sym.exceptions_raised,
                    })

        # 2. Locate callers / references
        for rel_path, mod in top.modules.items():
            for sym in mod.symbols:
                if any(symbol_name in call for call in sym.calls):
                    results["callers"].append({
                        "file": rel_path,
                        "caller_symbol": sym.name,
                        "line": sym.line,
                    })

        # 3. Module dependencies
        for d in results["definitions"]:
            results["dependencies"].extend(top.get_dependencies(d["file"]))

        results["dependencies"] = sorted(set(results["dependencies"]))
        return results
