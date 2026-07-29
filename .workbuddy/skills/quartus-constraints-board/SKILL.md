---
name: quartus-constraints-board
description: Manage Quartus QSF/SDC constraints, board pins, I/O standards, top-level settings, and clock constraints through the Quartus MCP server. Use when the user asks to bind signals to board pins, add clocks, inspect QSF assignments, resolve unconstrained clocks, or prepare a design for board programming.
---

# Quartus Constraints Board

Treat constraints as versioned design inputs. Read current QSF/SDC state before writing new assignments.

## Required MCP Tools

- `get_project_info`
- `get_pin_assignments`
- `set_pin_assignment`
- `remove_pin_assignment`
- `get_global_assignments`
- `set_global_assignment`
- `add_file_to_project`
- `list_project_files`
- `read_qsf`
- `run_timing_analysis`
- `get_clock_summary`
- `get_timing_summary`
- `execute_tcl_command`

## Workflow

1. Read `get_project_info`, `read_qsf`, and `get_global_assignments`.
2. For pin work, inspect `get_pin_assignments` before changing anything.
3. Apply pins with `set_pin_assignment`, including I/O standard when the board requirement is known.
4. Remove stale bindings with `remove_pin_assignment` before reassigning a signal to a different physical pin.
5. For clocks, create or update an SDC file in the project workspace, register it with `add_file_to_project`, then run `run_timing_analysis`.
6. Confirm clock recognition with `get_clock_summary`; read `get_timing_summary` if clocks are missing or timing remains unconstrained.
7. Use `execute_tcl_command` only for advanced Quartus queries that are not covered by safer MCP tools.

## Constraint Policy

- Never invent board pin names; ask for board documentation or reuse an existing board map supplied by the user.
- Do not silently overwrite working pin assignments. Report before/after state.
- Keep QSF global assignments and SDC timing constraints separate.
- If clock ports are renamed, update both HDL references and SDC constraints in the same task.

## Output Contract

Report pins changed, constraints added, files registered, clock summary, and any remaining unconstrained or board-documentation-dependent items.
