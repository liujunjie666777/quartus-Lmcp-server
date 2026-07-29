---
name: fpga-board-programming
description: Prepare and execute FPGA board programming through the Quartus MCP server. Use when the user asks to detect JTAG cables/devices, verify pin assignments before programming, generate assembler output, locate SOF files, or program a connected board.
---

# FPGA Board Programming

Programming is hardware-facing. Confirm the target, cable, and generated bitstream before writing to the board.

## Required MCP Tools

- `get_project_info`
- `get_pin_assignments`
- `compile_project`
- `run_assembler`
- `get_compilation_status`
- `get_programmer_cables`
- `detect_jtag_devices`
- `program_device`

## Workflow

1. Read `get_project_info` and `get_pin_assignments`; warn if board pin mapping is incomplete or obviously stale.
2. Ensure a current `.sof` exists. Run `run_assembler` or `compile_project` if needed.
3. Read `get_compilation_status` and confirm assembler artifacts.
4. Detect hardware with `get_programmer_cables` and `detect_jtag_devices`.
5. If no cable/device is found, stop with a hardware checklist instead of retrying blindly.
6. If more than one cable or device is present, ask the user which target to program.
7. Call `program_device` with the selected cable/device index.

## Safety Rules

- Do not program when the selected device is ambiguous.
- Do not promise success if no board is connected; MCP returns structured failures for this case.
- Do not change pins during a programming task unless the user explicitly asks for board constraint fixes.
- Mention that SRAM `.sof` programming is volatile unless the user requested flash programming through a supported flow.

## Output Contract

Return cable/device detection results, SOF path, programming command result, and any physical setup issue.
