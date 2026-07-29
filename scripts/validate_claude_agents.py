#!/usr/bin/env python3
"""Validate project-level Claude Code agents and their skill bindings."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
AGENTS_DIR = ROOT / ".claude" / "agents"
SKILLS_DIR = ROOT / ".claude" / "skills"

REQUIRED_HEADINGS = {
    "## Primary Responsibilities",
    "## Boundaries",
    "## Handoff Contract",
}


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---\n"):
        return {}, text
    _, frontmatter, body = text.split("---\n", 2)
    data: dict[str, Any] = {}
    current_list_key = ""
    for raw_line in frontmatter.splitlines():
        if not raw_line.strip():
            continue
        if raw_line.startswith("  - ") and current_list_key:
            data.setdefault(current_list_key, []).append(raw_line[4:].strip().strip('"'))
            continue
        key, sep, value = raw_line.partition(":")
        if not sep:
            continue
        key = key.strip()
        value = value.strip()
        current_list_key = ""
        if value:
            data[key] = value.strip('"')
        else:
            data[key] = []
            current_list_key = key
    return data, body


def skill_names() -> set[str]:
    if not SKILLS_DIR.exists():
        return set()
    return {path.name for path in SKILLS_DIR.iterdir() if path.is_dir()}


def validate() -> dict[str, Any]:
    failures: list[str] = []
    skills = skill_names()
    agents: dict[str, dict[str, Any]] = {}
    seen_names: set[str] = set()

    if not AGENTS_DIR.exists():
        failures.append(f"Missing agents directory: {AGENTS_DIR}")
        return {"ok": False, "failures": failures, "agent_count": 0, "skill_count": len(skills)}

    for agent_path in sorted(AGENTS_DIR.glob("*.md")):
        text = agent_path.read_text(encoding="utf-8")
        frontmatter, body = parse_frontmatter(text)
        name = str(frontmatter.get("name", ""))
        description = str(frontmatter.get("description", ""))
        declared_skills = frontmatter.get("skills", [])
        if isinstance(declared_skills, str):
            declared_skills = [item.strip() for item in declared_skills.split(",") if item.strip()]
        missing_skills = sorted(set(declared_skills) - skills)
        missing_headings = sorted(REQUIRED_HEADINGS - set(re.findall(r"^## .+$", body, flags=re.M)))

        agents[agent_path.name] = {
            "name": name,
            "skills": declared_skills,
            "missing_skills": missing_skills,
            "missing_headings": missing_headings,
        }

        if not name:
            failures.append(f"{agent_path.name}: missing name")
        if name in seen_names:
            failures.append(f"{agent_path.name}: duplicate agent name {name}")
        seen_names.add(name)
        if name and agent_path.stem != name:
            failures.append(f"{agent_path.name}: file stem must match frontmatter name")
        if not re.fullmatch(r"[a-z0-9-]{1,64}", name or ""):
            failures.append(f"{agent_path.name}: invalid agent name {name!r}")
        if not description or "TODO" in description:
            failures.append(f"{agent_path.name}: missing real description")
        if not declared_skills:
            failures.append(f"{agent_path.name}: missing skills binding")
        if missing_skills:
            failures.append(f"{agent_path.name}: unknown skills: {', '.join(missing_skills)}")
        if missing_headings:
            failures.append(f"{agent_path.name}: missing headings: {', '.join(missing_headings)}")
        if "TODO" in text:
            failures.append(f"{agent_path.name}: contains TODO")

    return {
        "ok": not failures,
        "failures": failures,
        "agent_count": len(agents),
        "skill_count": len(skills),
        "agents": agents,
    }


def main() -> int:
    result = validate()
    print(json.dumps(result, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
