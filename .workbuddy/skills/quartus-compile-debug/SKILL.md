---
name: quartus-compile-debug
description: Diagnose and fix Quartus compile, map, fit, assembler, DRC, and report failures through the Quartus MCP server. Use when compilation fails, reports contain errors/critical warnings, Quartus cannot find source/top-level entities, fitter fails, or the user asks why a build is broken.
---

# Quartus Compile Debug

Debug from the first failing stage outward. Prefer report evidence over guessing from the final error line.

## Required MCP Tools

- `compile_project`
- `run_analysis_synthesis`
- `run_fitter`
- `run_assembler`
- `get_compilation_status`
- `get_compilation_messages`
- `read_report_file`
- `get_flow_summary`
- `run_design_rule_check`
- `read_qsf`
- `list_project_files`
- `get_project_info`
- `execute_tcl_command`

## Workflow

1. Read `get_project_info`, `list_project_files`, and `read_qsf` to verify project identity and registered inputs.
2. Run the narrowest failing stage first: `run_analysis_synthesis`, `run_fitter`, `run_assembler`, or `compile_project` with the requested flow.
3. Call `get_compilation_messages` immediately after failure.
4. Use `read_report_file` for the failing stage report, then `get_flow_summary` for global context.
5. Run `run_design_rule_check` when the design compiles but Quartus reports structural or assignment issues.
6. Apply one fix class at a time: source registration, top-level mismatch, syntax/elaboration, constraints, fitter/resource issue, or assembler/programming output issue.
7. Re-run the same stage to prove the fix before proceeding to the next stage.

## Diagnosis Hints

- Missing top entity: check `TOP_LEVEL_ENTITY`, file registration, and module/entity spelling.
- File not found: prefer `add_file_to_project` from `$quartus-project-bringup`, not manual QSF edits.
- Fitter errors: inspect device, pins, assignments, and resources before editing RTL.
- Critical warnings are not automatically acceptable; classify their impact.

## Output Contract

Return the first failing stage, root cause, exact report/message evidence, edit made or requested, rerun result, and remaining risks.
