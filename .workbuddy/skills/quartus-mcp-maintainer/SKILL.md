---
name: quartus-mcp-maintainer
description: Maintain the Quartus MCP server, README, tests, smoke scripts, and project-level skills. Use when adding/removing MCP tools, changing tool names or arguments, updating ModelSim/Quartus discovery, auditing README tool count drift, validating skill references, or preparing the repository for release.
---

# Quartus MCP Maintainer

Keep the MCP surface, docs, tests, smoke checks, and project skills in lockstep. Tool drift is a release blocker.

## Required MCP Tools

- `get_quartus_installation`

## Repository Checks

Run these after changing MCP tools or skills:

```powershell
python scripts\validate_claude_skills.py
python scripts\validate_claude_agents.py
python -m py_compile quartus_mcp_server.py scripts\smoke_test_tools.py tests\test_quartus_mcp_server.py scripts\validate_claude_skills.py scripts\validate_claude_agents.py
python -m unittest discover -s tests
```

Run the full smoke test when tool behavior or discovery changes:

```powershell
python scripts\smoke_test_tools.py
```

## Workflow

1. Parse `@mcp.tool()` definitions in `quartus_mcp_server.py`; never rely on README counts by hand.
2. Keep `README.md` `Available Tools (N)` and the tool table synchronized with source.
3. Keep `scripts/smoke_test_tools.py` calling every MCP tool at least once.
4. Keep `tests/test_quartus_mcp_server.py` checking source/README/tool count and project skill/agent references.
5. For ModelSim changes, verify `QUARTUS_MCP_MODELSIM_BIN`, `MODELSIM_BIN`, and actual `vsim` discovery.
6. For skills, ensure every tool named in `## Required MCP Tools` exists in the server.
7. For agents, ensure every skill listed in frontmatter exists and each agent has a clear responsibility, boundary, and handoff contract.
8. Use a real Quartus project and, when possible, the existing Intel IP/LPM co-simulation project to verify behavior.

## Drift Rules

- Adding a tool requires README, tests, smoke script, and any affected skills.
- Removing or renaming a tool requires updating skill `Required MCP Tools` sections first.
- Removing or renaming a skill requires updating `.claude/agents/*.md` `skills` bindings first.
- Do not reintroduce generic simulator/Questa configuration names; this server exposes a ModelSim-specific flow.
- Hardware-dependent JTAG failures are acceptable only when returned as structured JSON and documented as no-board/no-cable conditions.

## Output Contract

Return changed tool count, README count, skill validation result, tests run, smoke result, and any hardware- or license-dependent gaps.
