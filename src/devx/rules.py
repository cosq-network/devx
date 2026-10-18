from __future__ import annotations

from pathlib import Path


def discover_project_rules(workspace: Path, *, max_total_bytes: int = 64_000) -> list[dict[str, str]]:
    """Discover repository-level custom instructions from .devx/rules and .devx/skills.

    Discovers:
      - .devx/rules/*.md (coding standards, architectural decisions, testing rules)
      - .devx/skills/*/SKILL.md or .devx/skills/*.md (custom capabilities and domain workflows)
    """
    devx_dir = workspace / ".devx"
    if not devx_dir.is_dir():
        return []

    discovered: list[dict[str, str]] = []
    total_bytes = 0

    # 1. Discover .devx/rules/*.md
    rules_dir = devx_dir / "rules"
    if rules_dir.is_dir():
        for path in sorted(rules_dir.glob("*.md")):
            if not path.is_file() or path.name.startswith("."):
                continue
            try:
                content = path.read_text(encoding="utf-8", errors="replace").strip()
                if not content:
                    continue
                size = len(content.encode("utf-8"))
                if total_bytes + size > max_total_bytes:
                    content = content[: max(0, max_total_bytes - total_bytes)] + "\n... (truncated)"
                discovered.append({
                    "name": path.stem,
                    "path": str(path.relative_to(workspace)),
                    "type": "rule",
                    "content": content,
                })
                total_bytes += size
                if total_bytes >= max_total_bytes:
                    return discovered
            except OSError:
                continue

    # 2. Discover .devx/skills/*/SKILL.md and .devx/skills/*.md
    skills_dir = devx_dir / "skills"
    if skills_dir.is_dir():
        skill_files = list(skills_dir.glob("*/SKILL.md")) + list(skills_dir.glob("*.md"))
        for path in sorted(set(skill_files)):
            if not path.is_file() or path.name.startswith("."):
                continue
            try:
                content = path.read_text(encoding="utf-8", errors="replace").strip()
                if not content:
                    continue
                size = len(content.encode("utf-8"))
                if total_bytes + size > max_total_bytes:
                    content = content[: max(0, max_total_bytes - total_bytes)] + "\n... (truncated)"
                skill_name = path.parent.name if path.name == "SKILL.md" else path.stem
                discovered.append({
                    "name": skill_name,
                    "path": str(path.relative_to(workspace)),
                    "type": "skill",
                    "content": content,
                })
                total_bytes += size
                if total_bytes >= max_total_bytes:
                    return discovered
            except OSError:
                continue

    return discovered


def format_rules_for_prompt(rules: list[dict[str, str]]) -> str:
    """Format discovered rules and skills for inclusion in LLM prompt context."""
    if not rules:
        return ""
    sections: list[str] = ["# Repository Custom Rules & Skills:"]
    for r in rules:
        header = f"## [{r['type'].upper()}] {r['name']} ({r['path']})"
        sections.append(f"{header}\n{r['content']}")
    return "\n\n".join(sections)
