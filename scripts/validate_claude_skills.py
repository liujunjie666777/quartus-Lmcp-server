#!/usr/bin/env python3
"""Validate project-level Claude skills against the Quartus MCP tool surface."""

from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "quartus_mcp_server.py"
SKILLS_DIR = ROOT / ".claude" / "skills"


def decorated_tool_names() -> set[str]:
    tree = ast.parse(SERVER.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        for decorator in node.decorator_list:
            target = decorator.func if isinstance(decorator, ast.Call) else decorator
            if (
                isinstance(target, ast.Attribute)
                and target.attr == "tool"
                and isinstance(target.value, ast.Name)
                and target.value.id == "mcp"
            ):
                names.add(node.name)
                break
    return names


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---\n"):
        return {}, text
    _, frontmatter, body = text.split("---\n", 2)
    data: dict[str, str] = {}
    for line in frontmatter.splitlines():
        if not line.strip():
            continue
        key, sep, value = line.partition(":")
        if sep:
            data[key.strip()] = value.strip().strip('"')
    return data, body


def required_tools(text: str) -> list[str]:
    lines = text.splitlines()
    in_section = False
    tools: list[str] = []
    for line in lines:
        if line.strip() == "## Required MCP Tools":
            in_section = True
            continue
        if in_section and line.startswith("## "):
            break
        if in_section:
            tools.extend(re.findall(r"`([A-Za-z_][A-Za-z0-9_]*)`", line))
    return tools


def validate() -> dict[str, Any]:
    failures: list[str] = []
    tools = decorated_tool_names()
    skills: dict[str, dict[str, Any]] = {}

    if not SKILLS_DIR.exists():
        failures.append(f"Missing skills directory: {SKILLS_DIR}")
        return {"ok": False, "failures": failures, "skills": skills, "tool_count": len(tools)}

    for skill_dir in sorted(path for path in SKILLS_DIR.iterdir() if path.is_dir()):
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            failures.append(f"{skill_dir.name}: missing SKILL.md")
            continue
        text = skill_md.read_text(encoding="utf-8")
        frontmatter, _ = parse_frontmatter(text)
        used_tools = required_tools(text)
        unknown = sorted(set(used_tools) - tools)
        skills[skill_dir.name] = {
            "required_tool_count": len(used_tools),
            "required_tools": used_tools,
            "unknown_tools": unknown,
        }
        if set(frontmatter) != {"name", "description"}:
            failures.append(f"{skill_dir.name}: frontmatter must contain only name and description")
        if frontmatter.get("name") != skill_dir.name:
            failures.append(f"{skill_dir.name}: frontmatter name mismatch")
        if not frontmatter.get("description") or "TODO" in frontmatter.get("description", ""):
            failures.append(f"{skill_dir.name}: missing real description")
        if "TODO" in text:
            failures.append(f"{skill_dir.name}: contains TODO")
        if not used_tools:
            failures.append(f"{skill_dir.name}: missing Required MCP Tools entries")
        if unknown:
            failures.append(f"{skill_dir.name}: unknown MCP tools: {', '.join(unknown)}")
        if (skill_dir / "README.md").exists():
            failures.append(f"{skill_dir.name}: do not include README.md inside skill directories")

    return {
        "ok": not failures,
        "failures": failures,
        "skill_count": len(skills),
        "tool_count": len(tools),
        "skills": skills,
    }


def main() -> int:
    result = validate()
    print(json.dumps(result, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
