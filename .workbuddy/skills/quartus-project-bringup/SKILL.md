---
name: quartus-project-bringup
description: Create, import, and sanity-check Quartus projects through the Quartus MCP server. Use when the user asks to make a new FPGA project, register HDL/SDC files, choose a device/family, set the top-level entity, verify QSF state, or run the first compile pass before deeper simulation/timing work.
---

# Quartus Project Bring-up

Use the MCP tools as the source of truth. Prefer creating a minimal reproducible Quartus project over explaining manual GUI steps.

## Required MCP Tools

- `get_quartus_installation`
- `get_device_families`
- `get_devices`
- `create_project`
- `open_project`
- `set_global_assignment`
- `get_global_assignments`
- `add_file_to_project`
- `list_project_files`
- `get_project_info`
- `read_qsf`
- `compile_project`
- `get_compilation_messages`

## Workflow

1. Confirm toolchain discovery with `get_quartus_installation`; stop if Quartus is unavailable.
2. If family/device are unspecified, call `get_device_families` and `get_devices` before choosing. Prefer explicit user choices over inferred defaults.
3. Create or open the project with `create_project` or `open_project`.
4. Set `TOP_LEVEL_ENTITY` through `set_global_assignment`.
5. Register HDL and SDC files through `add_file_to_project`; verify with `list_project_files`.
6. Read `get_project_info`, `get_global_assignments`, and `read_qsf` to catch wrong revision, missing family/device, missing top entity, or duplicate assignments.
7. Run a first `compile_project` pass using `map` unless the user asked for a full build.
8. Inspect `get_compilation_messages`; fix deterministic project setup errors before moving to simulation or timing.

## Bring-up Rules

- Do not assume the GUI project state is current; read the `.qpf`/`.qsf` through MCP.
- Do not add generated build output files as source files.
- Keep HDL file type assignments explicit: Verilog/SystemVerilog/VHDL/SDC.
- When a compile fails, hand off to `$quartus-compile-debug` rather than making unrelated project edits.

## Output Contract

Return the project path, family/device, revision, top-level entity, registered files, compile status, and the next recommended skill: ModelSim simulation, timing closure, or compile debug.
