---
name: quartus-flow-signoff
description: Run an end-to-end Quartus and ModelSim signoff workflow through the Quartus MCP server. Use when the user asks for a complete FPGA project check, release gate, final verification, archive, or confirmation that compile, simulation, timing, DRC, resource, power, and reports are all clean.
---

# Quartus Flow Signoff

This is the high-level gate. It should orchestrate specialized skills when failures appear, not hide failures behind a single pass/fail line.

## Required MCP Tools

- `get_quartus_installation`
- `open_project`
- `get_project_info`
- `list_project_files`
- `compile_project`
- `run_analysis_synthesis`
- `run_fitter`
- `run_assembler`
- `run_timing_analysis`
- `get_timing_summary`
- `get_compilation_messages`
- `run_design_rule_check`
- `get_flow_summary`
- `get_resource_usage`
- `get_power_report`
- `create_testbench`
- `run_simulation`
- `read_simulation_log`
- `list_simulation_artifacts`
- `archive_project`

## Workflow

1. Confirm Quartus and ModelSim discovery with `get_quartus_installation`.
2. Open and identify the project with `open_project`, `get_project_info`, and `list_project_files`.
3. Run synthesis, fitter, assembler, and STA in sequence with the narrow stage tools. Use `compile_project` for a full rebuild when requested.
4. Read `get_compilation_messages` after each failing stage.
5. Run `run_design_rule_check`, `get_flow_summary`, `get_resource_usage`, and `get_power_report`.
6. Run simulation using an existing registered testbench, or create a minimal one with `create_testbench` when the user accepts a smoke test.
7. Read `read_simulation_log` and `list_simulation_artifacts`.
8. Archive the project with `archive_project` only after the requested gates pass or the user asks for a failure snapshot.

## Signoff Gates

- Tool discovery passes.
- Project opens and source list is coherent.
- Analysis/synthesis, fitter, assembler, and STA complete as required.
- Compilation messages contain no errors and no unexplained critical warnings.
- ModelSim simulation has explicit pass evidence.
- DRC, resource, power, and timing summaries are reported.
- Archive exists if this is a release handoff.

## Output Contract

Return a gate table with pass/fail/blocked, evidence paths, failing stage root cause, and the next specialized skill for unresolved issues.
