#!/usr/bin/env python3
"""Call every Quartus MCP tool once and verify JSON-shaped responses.

This is an integration smoke test. It creates a tiny Verilog project in a
temporary directory, exercises the compile/report/file/Tcl tools, and calls the
programmer tools without requiring hardware to be attached.
"""

from __future__ import annotations

import argparse
import ast
import json
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import quartus_mcp_server as qms  # noqa: E402


PROJECT_NAME = "mcp_smoke"
FAMILY = "Cyclone IV E"
DEVICE = "EP4CE10F17C8"


def qms_tool_names() -> list[str]:
    tree = ast.parse((ROOT / "quartus_mcp_server.py").read_text(encoding="utf-8"))
    names: list[str] = []
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
                names.append(node.name)
                break
    return sorted(names)


def parse_response(raw: str) -> dict[str, Any]:
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("tool returned JSON, but not an object")
    return data


def call(records: list[dict[str, Any]], name: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
    func: Callable[..., str] = getattr(qms, name)
    record: dict[str, Any] = {"tool": name, "called": True, "json": False, "exception": ""}
    try:
        raw = func(*args, **kwargs)
        data = parse_response(raw)
        record["json"] = True
        record["keys"] = sorted(data.keys())
        if "success" in data:
            record["success"] = data["success"]
        if "error" in data:
            record["error"] = str(data["error"])[:200]
        records.append(record)
        return data
    except Exception as exc:  # pragma: no cover - this script reports failures itself
        record["exception"] = repr(exc)
        records.append(record)
        return {"error": repr(exc)}


def require(checks: list[str], condition: bool, message: str) -> None:
    if not condition:
        checks.append(message)


def write_demo_source(project_dir: Path) -> Path:
    source = project_dir / f"{PROJECT_NAME}.v"
    source.write_text(
        "\n".join(
            [
                f"module {PROJECT_NAME}(",
                "    input wire clk,",
                "    input wire reset_n,",
                "    output reg led",
                ");",
                "    reg [23:0] counter;",
                "    always @(posedge clk or negedge reset_n) begin",
                "        if (!reset_n) begin",
                "            counter <= 24'd0;",
                "            led <= 1'b0;",
                "        end else begin",
                "            counter <= counter + 24'd1;",
                "            led <= counter[23];",
                "        end",
                "    end",
                "endmodule",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return source


def write_demo_sdc(project_dir: Path) -> Path:
    sdc = project_dir / f"{PROJECT_NAME}.sdc"
    sdc.write_text('create_clock -name clk -period 20.000 [get_ports {clk}]\n', encoding="utf-8")
    return sdc


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--keep-project", action="store_true", help="Do not delete the temporary Quartus project.")
    parser.add_argument("--skip-flows", action="store_true", help="Skip the heavier compile/fitter/assembler tools.")
    parser.add_argument(
        "--project-dir",
        help="Exact project directory to create. It must not already exist.",
    )
    args = parser.parse_args()

    records: list[dict[str, Any]] = []
    failures: list[str] = []
    if args.project_dir:
        project_dir = Path(args.project_dir).resolve()
        temp_root = project_dir.parent
        if project_dir.exists():
            print(f"Project directory already exists: {project_dir}", file=sys.stderr)
            return 2
        temp_root.mkdir(parents=True, exist_ok=True)
    else:
        temp_root = Path(tempfile.mkdtemp(prefix="quartus_mcp_smoke_"))
        project_dir = temp_root / PROJECT_NAME
    script_path = temp_root / "hello.tcl"

    try:
        install = call(records, "get_quartus_installation")
        require(failures, bool(install.get("available")), "Quartus installation was not detected")
        require(failures, Path(str(install.get("quartus_sh", ""))).exists(), "quartus_sh.exe path does not exist")
        modelsim = install.get("modelsim", {})
        require(failures, bool(modelsim.get("available")), "ModelSim installation was not detected")
        require(failures, Path(str(modelsim.get("tools", {}).get("vsim", {}).get("path", ""))).exists(), "ModelSim vsim.exe path does not exist")

        families = call(records, "get_device_families")
        require(failures, families.get("count", 0) > 0, "No Quartus device families returned")

        devices = call(records, "get_devices", FAMILY)
        require(failures, DEVICE in devices.get("devices", []), f"{DEVICE} was not listed for {FAMILY}")

        created = call(records, "create_project", PROJECT_NAME, str(project_dir), FAMILY, DEVICE)
        require(failures, bool(created.get("created")), "Project was not created")

        opened = call(records, "open_project", str(project_dir))
        require(failures, bool(opened.get("opened")), "Project could not be opened")

        call(records, "get_project_info", str(project_dir))
        call(records, "list_projects", str(temp_root))
        call(records, "close_project")
        call(records, "set_global_assignment", str(project_dir), "TOP_LEVEL_ENTITY", PROJECT_NAME)
        call(records, "get_global_assignments", str(project_dir))

        call(records, "set_pin_assignment", str(project_dir), "led", "PIN_A1", "3.3-V LVTTL")
        call(records, "get_pin_assignments", str(project_dir))
        call(records, "remove_pin_assignment", str(project_dir), "led")

        source = write_demo_source(project_dir)
        added = call(records, "add_file_to_project", str(project_dir), str(source), "VERILOG_FILE")
        require(failures, bool(added.get("added")), "Verilog source file was not added")
        call(records, "remove_file_from_project", str(project_dir), str(source))
        added = call(records, "add_file_to_project", str(project_dir), str(source), "VERILOG_FILE")
        require(failures, bool(added.get("added")), "Verilog source file was not re-added")

        sdc = write_demo_sdc(project_dir)
        sdc_added = call(records, "add_file_to_project", str(project_dir), str(sdc), "SDC_FILE")
        require(failures, bool(sdc_added.get("added")), "SDC file was not added")

        call(records, "list_project_files", str(project_dir))
        call(records, "read_qsf", str(project_dir))

        testbench = call(records, "create_testbench", str(project_dir), "", "", "verilog", 1000, True)
        require(failures, bool(testbench.get("created")), "create_testbench did not create a testbench")
        tb_path = testbench.get("testbench", "")
        sim_added = call(records, "add_simulation_file", str(project_dir), tb_path, "verilog", "testbench")
        require(failures, bool(sim_added.get("added")), "add_simulation_file did not register the testbench")
        sim_result = call(records, "run_simulation", str(project_dir), testbench.get("top_module", ""), "all")
        require(failures, bool(sim_result.get("success")), "run_simulation failed")
        sim_log = call(records, "read_simulation_log", str(project_dir))
        require(failures, bool(sim_log.get("pass")), "read_simulation_log did not find MCP_SIMULATION_PASS")
        sim_artifacts = call(records, "list_simulation_artifacts", str(project_dir))
        require(failures, len(sim_artifacts.get("artifacts", [])) > 0, "list_simulation_artifacts returned no artifacts")

        if not args.skip_flows:
            compiled = call(records, "compile_project", str(project_dir), "map")
            require(failures, bool(compiled.get("success")), "compile_project(map) failed")

            analysis = call(records, "run_analysis_synthesis", str(project_dir))
            require(failures, bool(analysis.get("success")), "run_analysis_synthesis failed")

            fitter = call(records, "run_fitter", str(project_dir))
            require(failures, bool(fitter.get("success")), "run_fitter failed")

            assembler = call(records, "run_assembler", str(project_dir))
            require(failures, bool(assembler.get("success")), "run_assembler failed")

            timing = call(records, "run_timing_analysis", str(project_dir))
            require(failures, bool(timing.get("success")), "run_timing_analysis failed")
        else:
            call(records, "compile_project", str(project_dir), "not_a_real_flow")
            call(records, "run_analysis_synthesis", str(project_dir / "missing"))
            call(records, "run_fitter", str(project_dir / "missing"))
            call(records, "run_assembler", str(project_dir / "missing"))
            call(records, "run_timing_analysis", str(project_dir / "missing"))

        call(records, "get_compilation_status", str(project_dir))
        messages = call(records, "get_compilation_messages", str(project_dir))
        require(failures, messages.get("error_count") == 0, "Compilation reports contain errors")
        call(records, "get_timing_summary", str(project_dir))
        clocks = call(records, "get_clock_summary", str(project_dir))
        require(failures, clocks.get("count", 0) > 0, "get_clock_summary did not find any clocks")
        timing_paths = call(records, "get_timing_paths", str(project_dir), "*", "*")
        require(failures, bool(timing_paths.get("success")), "get_timing_paths did not run successfully")
        require(failures, len(timing_paths.get("paths", [])) > 0, "get_timing_paths did not return timing paths")

        call(records, "get_flow_summary", str(project_dir))
        resources = call(records, "get_resource_usage", str(project_dir))
        require(failures, bool(resources.get("resources")), "get_resource_usage returned no resources")
        report = call(records, "read_report_file", str(project_dir), "map")
        require(failures, bool(report.get("content")), "read_report_file returned no content")
        call(records, "get_power_report", str(project_dir))
        call(records, "run_design_rule_check", str(project_dir))
        archive = call(records, "archive_project", str(project_dir), str(temp_root / f"{PROJECT_NAME}.qar"))
        require(failures, bool(archive.get("archived")), "archive_project did not create a .qar")

        script_path.write_text('puts "SMOKE_TCL_SCRIPT_OK"\n', encoding="utf-8")
        script = call(records, "run_tcl_script", str(script_path), str(project_dir))
        require(failures, "SMOKE_TCL_SCRIPT_OK" in script.get("stdout", ""), "run_tcl_script did not execute")

        inline = call(records, "execute_tcl_command", 'puts "SMOKE_INLINE_TCL_OK"', str(project_dir))
        require(failures, "SMOKE_INLINE_TCL_OK" in inline.get("stdout", ""), "execute_tcl_command did not execute")

        call(records, "detect_jtag_devices")
        call(records, "get_programmer_cables")
        call(records, "program_device", str(project_dir))

        # Incremental compilation, RTL analysis, SDC
        call(records, "enable_incremental_compilation", str(project_dir), True)
        call(records, "create_design_partition", str(project_dir), PROJECT_NAME, "default")
        call(records, "get_design_partitions", str(project_dir))
        call(records, "analyze_rtl_structure", str(project_dir))
        call(records, "check_coding_style", str(project_dir))
        call(records, "generate_sdc_constraints", str(project_dir), 50.0)
        call(records, "set_clock_constraint", str(project_dir), "clk", 20.0, 50.0)
        call(records, "set_false_path_constraint", str(project_dir), "", "get_registers {sync_ff*}")

        # SignalTap II & IP Core & Conversion tools
        call(records, "get_signaltap_context", str(project_dir))
        call(records, "create_signaltap_file", str(project_dir), "clk")
        call(records, "list_available_ip", FAMILY)
        call(records, "convert_programming_file", str(project_dir), "", "hex")

        malformed = [r for r in records if not r["json"] or r["exception"]]
        for record in malformed:
            failures.append(f"{record['tool']} did not return valid JSON: {record['exception']}")

        unique_tools = sorted({record["tool"] for record in records})
        missing_tools = sorted(set(qms_tool_names()) - set(unique_tools))
        for tool in missing_tools:
            failures.append(f"{tool} was not called")

        print(json.dumps(
            {
                "ok": not failures,
                "total_calls": len(records),
                "unique_tools_called": len(unique_tools),
                "project_dir": str(project_dir),
                "failures": failures,
                "records": records,
            },
            indent=2,
        ))
        return 0 if not failures and len(unique_tools) == 60 else 1
    finally:
        if args.keep_project:
            print(f"Kept smoke project: {project_dir}", file=sys.stderr)
        else:
            shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
