---
name: quartus-timing-closure
description: Perform Quartus STA-driven timing closure using the Quartus MCP server. Use when setup/hold timing fails, clocks are missing, timing paths need investigation, the user asks about WNS/TNS/Fmax, or design/constraint changes must be evaluated with fitter and TimeQuest reports.
---

# Quartus Timing Closure

Close timing with evidence from TimeQuest and the fitter. Do not edit RTL until the failing path class is known.

## Required MCP Tools

- `run_fitter`
- `run_timing_analysis`
- `get_timing_summary`
- `get_clock_summary`
- `get_timing_paths`
- `read_report_file`
- `get_compilation_messages`
- `get_flow_summary`
- `set_global_assignment`
- `add_file_to_project`
- `compile_project`

## Workflow

1. Ensure a recent fit exists. Run `run_fitter` or `compile_project` only when reports are stale or absent.
2. Run `run_timing_analysis`.
3. Read `get_clock_summary`; fix missing or duplicate clocks before looking at path optimizations.
4. Read `get_timing_summary` and relevant `read_report_file` output.
5. Use `get_timing_paths` for the worst path classes; group by clock domain and endpoint type.
6. Classify the issue: missing constraints, impossible clock, long combinational logic, high fanout/reset/control, RAM/DSP boundary, I/O timing, or CDC.
7. Apply one change class at a time: SDC fix, pipeline/register balancing, resource inference/IP settings, fitter assignment, or clocking correction.
8. Re-run fitter/STA and compare before/after WNS/TNS/path evidence.

## Closure Rules

- A green compile with missing clocks is not timing closure.
- Do not mark false paths or multicycle paths without a design reason from the user or the RTL protocol.
- Prefer architectural fixes over aggressive assignments when RTL structure is clearly the bottleneck.
- Keep each iteration measurable: report old value, change, new value, and residual risk.

## Output Contract

Return clock coverage, worst failing paths, root-cause class, exact constraints or RTL edits needed, rerun results, and whether signoff is blocked.
