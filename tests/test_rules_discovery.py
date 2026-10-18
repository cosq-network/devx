from __future__ import annotations

from pathlib import Path
from devx.rules import discover_project_rules, format_rules_for_prompt


def test_discover_rules_and_skills(tmp_path: Path):
    # Setup .devx/rules
    rules_dir = tmp_path / ".devx" / "rules"
    rules_dir.mkdir(parents=True)
    (rules_dir / "coding-style.md").write_text("# Coding Style\nAlways use type hints.", encoding="utf-8")
    (rules_dir / "security.md").write_text("# Security\nDo not hardcode credentials.", encoding="utf-8")

    # Setup .devx/skills
    skill_dir = tmp_path / ".devx" / "skills" / "deploy"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# Deploy Skill\nRun deploy script.", encoding="utf-8")

    discovered = discover_project_rules(tmp_path)
    assert len(discovered) == 3

    names = {d["name"] for d in discovered}
    assert "coding-style" in names
    assert "security" in names
    assert "deploy" in names

    types = {d["name"]: d["type"] for d in discovered}
    assert types["coding-style"] == "rule"
    assert types["security"] == "rule"
    assert types["deploy"] == "skill"

    # Format for prompt
    prompt_text = format_rules_for_prompt(discovered)
    assert "# Repository Custom Rules & Skills:" in prompt_text
    assert "## [RULE] coding-style" in prompt_text
    assert "Always use type hints." in prompt_text
    assert "## [SKILL] deploy" in prompt_text
    assert "Run deploy script." in prompt_text


def test_discover_rules_empty_when_no_devx(tmp_path: Path):
    discovered = discover_project_rules(tmp_path)
    assert discovered == []
    assert format_rules_for_prompt(discovered) == ""


def test_discover_rules_truncation(tmp_path: Path):
    rules_dir = tmp_path / ".devx" / "rules"
    rules_dir.mkdir(parents=True)
    (rules_dir / "large.md").write_text("A" * 500, encoding="utf-8")

    discovered = discover_project_rules(tmp_path, max_total_bytes=100)
    assert len(discovered) == 1
    assert "truncated" in discovered[0]["content"]
