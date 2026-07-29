---
name: quartus-qor-power-review
description: Review Quartus quality-of-results, resource utilization, compile status, DRC, flow summaries, and power reports through the Quartus MCP server. Use when the user asks for area, resource pressure, warnings, power, design health, or a compact report after compilation.
---

# Quartus QoR Power Review

Summarize design health from reports, not from intuition. Separate hard failures, risky warnings, and ordinary informational noise.

## Required MCP Tools

- `get_project_info`
- `get_compilation_status`
- `get_compilation_messages`
- `get_flow_summary`
- `get_resource_usage`
- `get_power_report`
- `run_design_rule_check`
- `read_report_file`

## Workflow

1. Read `get_project_info` and `get_compilation_status`.
2. Pull `get_compilation_messages` and classify errors, critical warnings, and actionable warnings.
3. Read `get_flow_summary` for high-level pass/fail and build metadata.
4. Extract resource pressure with `get_resource_usage`.
5. Run `run_design_rule_check` when DRC status is missing or stale.
6. Run `get_power_report` only after fit/assembly reports exist.
7. Use `read_report_file` for focused report excerpts when a summary is ambiguous.

## Review Heuristics

- Flag resources near exhaustion before timing closure; congested designs often fail timing later.
- Treat inferred latches, multiple drivers, unconstrained paths, missing clocks, and ignored assignments as high-risk.
- State whether power numbers are vectorless estimates or based on user-supplied switching activity if that evidence exists.
- Keep recommendations tied to a report line, resource class, or warning category.

## Output Contract

Return a compact scorecard: build status, errors/critical warnings, resources, DRC, power, top risks, and next skill to use.
