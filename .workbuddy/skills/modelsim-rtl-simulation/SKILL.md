---
name: modelsim-rtl-simulation
description: Create testbenches, register simulation-only files, run ModelSim batch simulations, and inspect simulation logs through the Quartus MCP server. Use when the user asks to simulate RTL, verify behavior before synthesis, generate a simple Verilog testbench, read ModelSim output, or list simulation artifacts.
---

# ModelSim RTL Simulation

Use the MCP ModelSim flow as the default simulation path. It is wired to the configured `QUARTUS_MCP_MODELSIM_BIN` and the Quartus simulation library discovery logic.

## Required MCP Tools

- `get_quartus_installation`
- `get_project_info`
- `list_project_files`
- `create_testbench`
- `add_simulation_file`
- `run_simulation`
- `read_simulation_log`
- `list_simulation_artifacts`

## Workflow

1. Confirm ModelSim availability through `get_quartus_installation`.
2. Read `get_project_info` and `list_project_files` so the simulated top matches the compiled project.
3. If no testbench exists, create one with `create_testbench`.
4. Register user-provided benches, helper modules, or `.do`/Tcl assets with `add_simulation_file`.
5. Run `run_simulation` with `run_time="all"` unless the user asks for a finite duration.
6. Inspect `read_simulation_log`; require explicit pass/fail markers or clear final conditions.
7. Use `list_simulation_artifacts` to surface VCDs, transcripts, manifests, and logs.

## Simulation Rules

- Do not use `quartus_sim`; this MCP flow is ModelSim-only.
- Do not treat a clean compile as a passing test. Look for `$finish`, `MCP_SIMULATION_PASS`, assertions, or user-specified checks.
- Prefer deterministic testbenches with reset, clocks, bounded runtime, and `$display` pass/fail markers.
- If the design uses Intel IP/LPM/megafunctions, hand off to `$intel-ip-cosimulation`.

## Output Contract

Return the testbench top, compiled files, ModelSim status, pass/fail evidence, warnings/errors, and artifact paths.
