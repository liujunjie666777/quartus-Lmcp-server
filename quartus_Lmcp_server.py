#!/usr/bin/env python3
"""
Quartus MCP Server — v3
Exposes a local Intel/Altera Quartus command-line installation to Claude Code
via the Model Context Protocol.

Design:
- FastMCP API (mcp >= 1.0)
- Stateless: every call opens -> acts -> closes (avoids .qpf.lck conflicts)
- Read operations parse QSF/RPT files directly in Python (no Tcl needed)
- Tcl / quartus executables used only where genuinely required
- ALL logging goes to stderr; stdout is reserved for the MCP JSON-RPC wire
"""

import asyncio
import glob
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path
from typing import Any, Optional

from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Logging — stderr ONLY
# ---------------------------------------------------------------------------
logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(asctime)s [quartus-Lmcp] %(levelname)s %(message)s",
)
log = logging.getLogger("quartus-Lmcp")

# ---------------------------------------------------------------------------
# Configuration / Quartus discovery
# ---------------------------------------------------------------------------

_EXE_SUFFIX = ".exe"

EXECUTABLE_NAMES = {
    "quartus_sh": "quartus_sh.exe",
    "quartus_map": "quartus_map.exe",
    "quartus_fit": "quartus_fit.exe",
    "quartus_asm": "quartus_asm.exe",
    "quartus_sta": "quartus_sta.exe",
    "quartus_pgm": "quartus_pgm.exe",
    "quartus_pow": "quartus_pow.exe",
    "quartus_drc": "quartus_drc.exe",
    "quartus_stp": "quartus_stp.exe",
    "quartus_cpf": "quartus_cpf.exe",
    "quartus_dse": "quartus_dse.exe",
    "quartus_eda": "quartus_eda.exe",
    "quartus_jli": "quartus_jli.exe",
    "quartus_cvp": "quartus_cvp.exe",
    "quartus_cdb": "quartus_cdb.exe",
    "jtagconfig": "jtagconfig.exe",
}

MODELSIM_EXECUTABLE_NAMES = {
    "vlib": "vlib.exe",
    "vmap": "vmap.exe",
    "vlog": "vlog.exe",
    "vcom": "vcom.exe",
    "vsim": "vsim.exe",
}


def _version_key(path: Path) -> tuple[int, ...]:
    numbers = [int(part) for part in re.findall(r"\d+", path.name)]
    return tuple(numbers) if numbers else (0,)


def _candidate_roots() -> list[Path]:
    roots: list[Path] = []
    for env_name in (
        "QUARTUS_MCP_ROOT",
        "QUARTUS_ROOTDIR",
        "QUARTUS_ROOTDIR_OVERRIDE",
        "QUARTUS_BIN",
    ):
        value = os.environ.get(env_name)
        if value:
            roots.append(Path(value))

    for base in _discovery_search_roots():
        roots.append(base)
        if base.exists():
            try:
                children = [child for child in base.iterdir() if child.is_dir()]
            except OSError:
                children = []
            roots.extend(sorted(children, key=_version_key, reverse=True))
    return roots


def _discovery_search_roots() -> list[Path]:
    """Return all candidate base directories for Quartus installation discovery."""
    bases: list[Path] = []
    # Scan common drives on Windows
    for drive in ("C:/", "D:/", "E:/", "F:/"):
        for folder in ("intelFPGA_lite", "intelFPGA", "altera"):
            p = Path(f"{drive}{folder}")
            bases.append(p)
            if p.exists():
                try:
                    children = sorted(
                        [c for c in p.iterdir() if c.is_dir()],
                        key=_version_key, reverse=True,
                    )
                    bases.extend(children)
                except OSError:
                    pass
    # WSL paths
    for base in (
        Path("/mnt/c/intelFPGA_lite"),
        Path("/mnt/c/intelFPGA"),
        Path("/mnt/c/altera"),
        Path("/mnt/d/intelFPGA_lite"),
        Path("/mnt/d/intelFPGA"),
        Path("/mnt/e/intelFPGA_lite"),
    ):
        bases.append(base)
        if base.exists():
            try:
                children = sorted(
                    [c for c in base.iterdir() if c.is_dir()],
                    key=_version_key, reverse=True,
                )
                bases.extend(children)
            except OSError:
                pass
    return bases


def _candidate_bin_dirs(root: Path) -> list[Path]:
    if root.name.lower() == EXECUTABLE_NAMES["quartus_sh"]:
        return [root.parent]
    return [
        root,
        root / "bin64",
        root / "bin",
        root / "quartus" / "bin64",
        root / "quartus" / "bin",
    ]


def _build_install_info(bin_dir: Path, root: Path) -> dict:
    """Build installation info dict from a discovered Quartus bin directory."""
    quartus_sh = bin_dir / EXECUTABLE_NAMES["quartus_sh"]
    quartus_root = bin_dir.parent
    install_root = quartus_root.parent if quartus_root.name.lower() == "quartus" else root
    tools = {
        tool_name: str(bin_dir / exe_name)
        for tool_name, exe_name in EXECUTABLE_NAMES.items()
    }
    # Extract version from the directory path (e.g. "18.1" from "E:/intelFPGA_lite/18.1/quartus/bin64")
    path_ver = ""
    for part in (install_root.name, quartus_root.name):
        m = re.match(r"(\d+\.\d+)", part)
        if m:
            path_ver = m.group(1)
            break
    return {
        "available": True,
        "install_root": str(install_root),
        "quartus_root": str(quartus_root),
        "bin_dir": str(bin_dir),
        "quartus_sh": str(quartus_sh),
        "tools": tools,
        "path_version": path_ver,
    }


def discover_all_quartus() -> list[dict]:
    """Discover ALL Quartus installations on this host, sorted newest first."""
    seen: set[str] = set()
    all_installs: list[dict] = []
    for root in _candidate_roots():
        for bin_dir in _candidate_bin_dirs(root):
            key = str(bin_dir).lower()
            if key in seen:
                continue
            seen.add(key)
            quartus_sh = bin_dir / EXECUTABLE_NAMES["quartus_sh"]
            if not quartus_sh.exists():
                continue
            all_installs.append(_build_install_info(bin_dir, root))
    # Sort newest first by path version
    all_installs.sort(key=lambda x: _version_key(Path(x["install_root"])), reverse=True)
    return all_installs


def discover_quartus() -> dict:
    """Find the best (newest) usable Quartus command-line installation on this host."""
    all_installs = discover_all_quartus()
    if all_installs:
        info = all_installs[0]
        info["checked"] = [i["bin_dir"] for i in all_installs]
        return info
    # Compute checked list for error reporting
    checked: list[str] = []
    seen: set[str] = set()
    for root in _candidate_roots():
        for bin_dir in _candidate_bin_dirs(root):
            key = str(bin_dir).lower()
            if key in seen:
                continue
            seen.add(key)
            checked.append(str(bin_dir))
    return {
        "available": False,
        "error": "No usable quartus_sh.exe was found. Set QUARTUS_MCP_ROOT, QUARTUS_ROOTDIR, or QUARTUS_BIN.",
        "checked": checked,
        "tools": {},
    }


def _candidate_modelsim_bins() -> list[Path]:
    bins: list[Path] = []
    for env_name in ("QUARTUS_MCP_MODELSIM_BIN", "MODELSIM_BIN"):
        value = os.environ.get(env_name)
        if value:
            bins.append(Path(value))

    # Scan modeltech* directories across common drives
    for drive in ("C:/", "D:/", "E:/", "F:/"):
        for root in sorted(Path(drive).glob("modeltech*"), key=_version_key, reverse=True):
            bins.append(root / "win64")
            bins.append(root / "win32")
            bins.append(root / "win32aloem")

    # Scan bundled modelsim_ase inside Intel installations on all drives
    for drive in ("C:/", "D:/", "E:/", "F:/"):
        for base in (Path(f"{drive}intelFPGA_lite"), Path(f"{drive}intelFPGA")):
            if not base.exists():
                continue
            try:
                versions = sorted([child for child in base.iterdir() if child.is_dir()], key=_version_key, reverse=True)
            except OSError:
                versions = []
            for version_root in versions:
                bins.extend([
                    version_root / "modelsim_ase" / "win32aloem",
                    version_root / "modelsim_ase" / "win32",
                ])
    return bins


def discover_modelsim() -> dict:
    """Find a usable ModelSim command-line installation."""
    checked: list[str] = []
    seen: set[str] = set()
    for bin_dir in _candidate_modelsim_bins():
        key = str(bin_dir).lower()
        if key in seen:
            continue
        seen.add(key)
        checked.append(str(bin_dir))
        if not bin_dir.exists():
            continue
        tools = {
            tool_name: str(bin_dir / exe_name)
            for tool_name, exe_name in MODELSIM_EXECUTABLE_NAMES.items()
        }
        required = ("vlib", "vmap", "vlog", "vsim")
        if all(Path(tools[name]).exists() for name in required):
            return {
                "available": True,
                "kind": "modelsim",
                "bin_dir": str(bin_dir),
                "tools": tools,
                "checked": checked,
            }
    return {
        "available": False,
        "error": "No usable ModelSim command-line installation was found. Set QUARTUS_MCP_MODELSIM_BIN or MODELSIM_BIN.",
        "checked": checked,
        "tools": {},
    }


QUARTUS = discover_quartus()
MODELSIM = discover_modelsim()
QUARTUS_BIN = QUARTUS.get("bin_dir", os.environ.get("QUARTUS_BIN", ""))
QUARTUS_SH = QUARTUS.get("tools", {}).get("quartus_sh", str(Path(QUARTUS_BIN) / f"quartus_sh{_EXE_SUFFIX}"))
QUARTUS_MAP = QUARTUS.get("tools", {}).get("quartus_map", str(Path(QUARTUS_BIN) / f"quartus_map{_EXE_SUFFIX}"))
QUARTUS_FIT = QUARTUS.get("tools", {}).get("quartus_fit", str(Path(QUARTUS_BIN) / f"quartus_fit{_EXE_SUFFIX}"))
QUARTUS_ASM = QUARTUS.get("tools", {}).get("quartus_asm", str(Path(QUARTUS_BIN) / f"quartus_asm{_EXE_SUFFIX}"))
QUARTUS_STA = QUARTUS.get("tools", {}).get("quartus_sta", str(Path(QUARTUS_BIN) / f"quartus_sta{_EXE_SUFFIX}"))
QUARTUS_PGM = QUARTUS.get("tools", {}).get("quartus_pgm", str(Path(QUARTUS_BIN) / f"quartus_pgm{_EXE_SUFFIX}"))
QUARTUS_POW = QUARTUS.get("tools", {}).get("quartus_pow", str(Path(QUARTUS_BIN) / f"quartus_pow{_EXE_SUFFIX}"))
QUARTUS_DRC = QUARTUS.get("tools", {}).get("quartus_drc", str(Path(QUARTUS_BIN) / f"quartus_drc{_EXE_SUFFIX}"))
QUARTUS_STP = QUARTUS.get("tools", {}).get("quartus_stp", str(Path(QUARTUS_BIN) / f"quartus_stp{_EXE_SUFFIX}"))
QUARTUS_CPF = QUARTUS.get("tools", {}).get("quartus_cpf", str(Path(QUARTUS_BIN) / f"quartus_cpf{_EXE_SUFFIX}"))
QUARTUS_DSE = QUARTUS.get("tools", {}).get("quartus_dse", str(Path(QUARTUS_BIN) / f"quartus_dse{_EXE_SUFFIX}"))
QUARTUS_EDA = QUARTUS.get("tools", {}).get("quartus_eda", str(Path(QUARTUS_BIN) / f"quartus_eda{_EXE_SUFFIX}"))
QUARTUS_JLI = QUARTUS.get("tools", {}).get("quartus_jli", str(Path(QUARTUS_BIN) / f"quartus_jli{_EXE_SUFFIX}"))
QUARTUS_CVP = QUARTUS.get("tools", {}).get("quartus_cvp", str(Path(QUARTUS_BIN) / f"quartus_cvp{_EXE_SUFFIX}"))
QUARTUS_CDB = QUARTUS.get("tools", {}).get("quartus_cdb", str(Path(QUARTUS_BIN) / f"quartus_cdb{_EXE_SUFFIX}"))

# Qsys / Platform Designer — try multiple path patterns across Quartus versions
def _find_qsys_tools(quartus_bin: str) -> tuple[str, str]:
    """Return (qsys_script, qsys_generate) paths, trying multiple layouts."""
    bin_path = Path(quartus_bin)
    quartus_root = bin_path.parent  # e.g. .../quartus or .../18.1/quartus
    install_root = quartus_root.parent if quartus_root.name.lower() == "quartus" else quartus_root

    candidate_dirs = [
        quartus_root / "sopc_builder" / "bin",
        install_root / "quartus" / "sopc_builder" / "bin",
        bin_path,                                         # 20.x+ may put qsys tools in main bin
        install_root / "quartus" / "bin",
        install_root / "bin",
    ]
    script_name = f"qsys-script{_EXE_SUFFIX}"
    generate_name = f"qsys-generate{_EXE_SUFFIX}"
    for d in candidate_dirs:
        s = d / script_name
        g = d / generate_name
        if s.exists() or g.exists():
            return str(s), str(g)
    # Fallback: first candidate (preserves old behavior)
    return str(candidate_dirs[0] / script_name), str(candidate_dirs[0] / generate_name)

QSYS_SCRIPT, QSYS_GENERATE = _find_qsys_tools(QUARTUS_BIN)

SIM_VLIB = MODELSIM.get("tools", {}).get("vlib", "")
SIM_VMAP = MODELSIM.get("tools", {}).get("vmap", "")
SIM_VLOG = MODELSIM.get("tools", {}).get("vlog", "")
SIM_VCOM = MODELSIM.get("tools", {}).get("vcom", "")
SIM_VSIM = MODELSIM.get("tools", {}).get("vsim", "")

DEFAULT_PROJECT_DIR = os.environ.get(
    "QUARTUS_MCP_PROJECT_DIR",
    str(Path.home() / "Documents" / "quartus_mcp_projects"),
)

QUARTUS_ENV: dict = {**os.environ}
if QUARTUS.get("quartus_root"):
    QUARTUS_ENV["QUARTUS_ROOTDIR"] = str(QUARTUS["quartus_root"])
    QUARTUS_ENV["QUARTUS_ROOTDIR_OVERRIDE"] = str(QUARTUS["quartus_root"])
if QUARTUS_BIN:
    QUARTUS_ENV["PATH"] = str(QUARTUS_BIN) + os.pathsep + QUARTUS_ENV.get("PATH", "")
if MODELSIM.get("bin_dir"):
    QUARTUS_ENV["PATH"] = str(MODELSIM["bin_dir"]) + os.pathsep + QUARTUS_ENV.get("PATH", "")

# ---------------------------------------------------------------------------
# MCP server instance
# ---------------------------------------------------------------------------
mcp = FastMCP("Quartus L MCP")

# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------

def run_quartus(cmd: list[str], cwd: Optional[str] = None, timeout: int = 300) -> dict:
    """Run a Quartus executable and return {success, stdout, stderr, returncode}."""
    if not cmd or not cmd[0]:
        return {
            "success": False,
            "stdout": "",
            "stderr": "Executable not configured",
            "returncode": -1,
            "command": cmd,
        }
    log.info("run_quartus: %s (cwd=%s, timeout=%ds)", Path(cmd[0]).name, cwd, timeout)
    if not Path(cmd[0]).exists():
        return {
            "success": False,
            "stdout": "",
            "stderr": f"Executable not found: {cmd[0]}",
            "returncode": -1,
            "command": cmd,
        }
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
            cwd=cwd,
            env=QUARTUS_ENV,
        )
        log.info("returncode=%d", result.returncode)
        return {
            "success": result.returncode == 0,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "returncode": result.returncode,
            "command": cmd,
        }
    except subprocess.TimeoutExpired:
        log.warning("Timed out after %ds: %s", timeout, cmd[0])
        return {"success": False, "stdout": "",
                "stderr": f"Timed out after {timeout}s", "returncode": -1}
    except FileNotFoundError as e:
        log.error("Executable not found: %s", e)
        return {"success": False, "stdout": "",
                "stderr": f"Executable not found: {e}", "returncode": -1}
    except Exception as e:
        log.error("run_quartus error: %s", e)
        return {"success": False, "stdout": "", "stderr": str(e), "returncode": -1}


def run_tcl_with(executable: str, tcl_code: str, cwd: Optional[str] = None, timeout: int = 300) -> dict:
    """Write tcl_code to a temp file and run it through a Quartus Tcl executable."""
    work_dir = cwd or DEFAULT_PROJECT_DIR
    os.makedirs(work_dir, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".tcl", delete=False, dir=work_dir
    ) as tf:
        tf.write(tcl_code)
        tcl_path = tf.name
    log.info("run_tcl: temp=%s", tcl_path)
    try:
        return run_quartus([executable, "-t", tcl_path], cwd=work_dir, timeout=timeout)
    finally:
        try:
            os.unlink(tcl_path)
        except OSError:
            pass


def run_tcl(tcl_code: str, cwd: Optional[str] = None, timeout: int = 300) -> dict:
    """Write tcl_code to a temp file and run it through quartus_sh -t."""
    return run_tcl_with(QUARTUS_SH, tcl_code, cwd=cwd, timeout=timeout)


def resolve_project(project_path: str) -> tuple:
    """Return (qpf_path, project_dir, revision) from a .qpf path or directory."""
    p = Path(project_path)
    if p.suffix.lower() == ".qpf":
        if not p.exists():
            raise ValueError(f".qpf file not found: {p}")
        qpf = p
    elif p.is_dir():
        qpf_files = list(p.glob("*.qpf"))
        if not qpf_files:
            raise ValueError(f"No .qpf file found in directory: {p}")
        qpf = sorted(qpf_files)[0]
    else:
        raise ValueError(f"project_path must be a .qpf file or directory: {project_path}")
    revision = _read_revision(str(qpf))
    return str(qpf), str(qpf.parent), revision


def _read_revision(qpf_path: str) -> str:
    """Extract the revision name from a .qpf file."""
    text = Path(qpf_path).read_text(errors="replace")
    m = re.search(r'^PROJECT_REVISION\s*=\s*"?(\S+?)"?\s*$', text, re.MULTILINE)
    return m.group(1).rstrip('"') if m else Path(qpf_path).stem


def find_qsf(proj_dir: str, revision: str) -> Optional[str]:
    """Return path to the project's .qsf file, or None if not found."""
    qsf = Path(proj_dir) / f"{revision}.qsf"
    if qsf.exists():
        return str(qsf)
    qsfs = sorted(Path(proj_dir).glob("*.qsf"))
    return str(qsfs[0]) if qsfs else None


def parse_qsf(qsf_path: str) -> list:
    """
    Parse a QSF file and return a list of dicts: {name, value}.
    Handles both quoted and unquoted values.
    """
    assignments = []
    try:
        for line in Path(qsf_path).read_text(errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            m = re.match(r'set_global_assignment\s+-name\s+(\S+)\s+(.*)', line)
            if m:
                name = m.group(1)
                val  = m.group(2).strip().strip('"')
                assignments.append({"name": name, "value": val})
    except OSError:
        pass
    return assignments


def parse_qsf_pins(qsf_path: str) -> list:
    """Extract set_location_assignment lines from a QSF file."""
    pins = []
    try:
        for line in Path(qsf_path).read_text(errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            m = re.match(r'set_location_assignment\s+(PIN_\S+)\s+-to\s+(\S+)', line)
            if m:
                pins.append({"location": m.group(1), "signal": m.group(2)})
    except OSError:
        pass
    return pins


SIMULATION_MANIFEST = "simulation_files.json"
HDL_FILE_ASSIGNMENTS = {
    "VERILOG_FILE",
    "SYSTEMVERILOG_FILE",
    "VHDL_FILE",
    "AHDL_FILE",
}


def simulation_dir(project_dir: str) -> Path:
    return Path(project_dir) / "simulation"


def simulation_manifest_path(project_dir: str) -> Path:
    return simulation_dir(project_dir) / SIMULATION_MANIFEST


def _read_simulation_manifest(project_dir: str) -> dict:
    path = simulation_manifest_path(project_dir)
    if not path.exists():
        return {"files": [], "top_module": "", "testbench": ""}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"files": [], "top_module": "", "testbench": ""}
    if not isinstance(data, dict):
        return {"files": [], "top_module": "", "testbench": ""}
    data.setdefault("files", [])
    data.setdefault("top_module", "")
    data.setdefault("testbench", "")
    return data


def _write_simulation_manifest(project_dir: str, manifest: dict) -> Path:
    sim_dir = simulation_dir(project_dir)
    sim_dir.mkdir(parents=True, exist_ok=True)
    path = simulation_manifest_path(project_dir)
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return path


def _as_project_relative(project_dir: str, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(Path(project_dir).resolve()))
    except ValueError:
        return str(path.resolve())


def _resolve_project_file(project_dir: str, file_path: str) -> Path:
    path = Path(file_path)
    if not path.is_absolute():
        path = Path(project_dir) / path
    return path


def _infer_hdl_kind(path: Path, file_type: str = "auto") -> str:
    if file_type and file_type.lower() != "auto":
        normalized = file_type.upper()
        if normalized in {"VERILOG", "VERILOG_FILE"}:
            return "verilog"
        if normalized in {"SYSTEMVERILOG", "SYSTEMVERILOG_FILE", "SV"}:
            return "systemverilog"
        if normalized in {"VHDL", "VHDL_FILE"}:
            return "vhdl"
        return file_type.lower()
    suffix = path.suffix.lower()
    if suffix == ".sv":
        return "systemverilog"
    if suffix in {".vhd", ".vhdl"}:
        return "vhdl"
    return "verilog"


def _add_simulation_manifest_file(project_dir: str, file_path: Path, role: str, file_type: str) -> dict:
    manifest = _read_simulation_manifest(project_dir)
    relative = _as_project_relative(project_dir, file_path)
    entries = manifest.setdefault("files", [])
    entries[:] = [entry for entry in entries if entry.get("path") != relative]
    entries.append({"path": relative, "role": role, "type": file_type})
    if role == "testbench":
        manifest["testbench"] = relative
        manifest["top_module"] = file_path.stem
    _write_simulation_manifest(project_dir, manifest)
    return manifest


def _top_entity_from_project(proj_dir: str, revision: str) -> str:
    qsf_path = find_qsf(proj_dir, revision)
    if qsf_path:
        for assignment in parse_qsf(qsf_path):
            if assignment["name"] == "TOP_LEVEL_ENTITY":
                return assignment["value"]
    return revision


def _project_hdl_files(proj_dir: str, revision: str) -> list[dict]:
    qsf_path = find_qsf(proj_dir, revision)
    files: list[dict] = []
    if not qsf_path:
        return files
    for assignment in parse_qsf(qsf_path):
        if assignment["name"] not in HDL_FILE_ASSIGNMENTS:
            continue
        path = _resolve_project_file(proj_dir, assignment["value"])
        if path.exists():
            files.append({
                "path": path,
                "type": _infer_hdl_kind(path, assignment["name"]),
                "role": "design",
            })
    return files


def _verilog_ports_for_top(proj_dir: str, top_entity: str) -> list[dict]:
    for candidate in Path(proj_dir).rglob("*.v"):
        text = candidate.read_text(errors="replace")
        match = re.search(rf"\bmodule\s+{re.escape(top_entity)}\s*\((.*?)\)\s*;", text, re.S)
        if not match:
            continue
        ports: list[dict] = []
        for raw in match.group(1).split(","):
            declaration = " ".join(raw.strip().split())
            if not declaration:
                continue
            direction = "inout"
            if declaration.startswith("input "):
                direction = "input"
                declaration = declaration[len("input "):].strip()
            elif declaration.startswith("output "):
                direction = "output"
                declaration = declaration[len("output "):].strip()
            elif declaration.startswith("inout "):
                direction = "inout"
                declaration = declaration[len("inout "):].strip()
            declaration = re.sub(r"\b(wire|reg|logic|signed)\b", "", declaration).strip()
            width = ""
            width_match = re.match(r"(\[[^\]]+\])\s+(.+)", declaration)
            if width_match:
                width = width_match.group(1)
                declaration = width_match.group(2)
            name = declaration.split()[-1].strip()
            if re.match(r"^[A-Za-z_][A-Za-z0-9_$]*$", name):
                ports.append({"name": name, "direction": direction, "width": width})
        if ports:
            return ports
    return [
        {"name": "clk", "direction": "input", "width": ""},
        {"name": "reset_n", "direction": "input", "width": ""},
    ]


def _create_verilog_testbench(top_entity: str, testbench_name: str, ports: list[dict], duration_ns: int) -> str:
    declarations: list[str] = []
    connections: list[str] = []
    initial_assignments: list[str] = []
    has_clock = False
    has_reset_n = False
    for port in ports:
        name = port["name"]
        width = f"{port['width']} " if port.get("width") else ""
        if port["direction"] == "input":
            declarations.append(f"    reg {width}{name} = 1'b0;")
            if name.lower() in {"clk", "clock"}:
                has_clock = True
            elif name.lower() in {"reset_n", "rst_n", "resetn"}:
                has_reset_n = True
            elif name.lower() in {"reset", "rst"}:
                initial_assignments.append(f"        {name} = 1'b1;")
                initial_assignments.append("        #40;")
                initial_assignments.append(f"        {name} = 1'b0;")
            else:
                initial_assignments.append(f"        {name} = 1'b0;")
        elif port["direction"] == "output":
            declarations.append(f"    wire {width}{name};")
        else:
            declarations.append(f"    wire {width}{name};")
        connections.append(f"        .{name}({name})")

    clock_block = "    always #10 clk = ~clk;\n\n" if has_clock else ""
    reset_block = ""
    if has_reset_n:
        reset_block = "        reset_n = 1'b0;\n        #40;\n        reset_n = 1'b1;\n"
    assignment_block = "\n".join(initial_assignments)
    if assignment_block:
        assignment_block += "\n"

    return "\n".join([
        "`timescale 1ns/1ps",
        f"module {testbench_name};",
        *declarations,
        "",
        f"    {top_entity} dut (",
        ",\n".join(connections),
        "    );",
        "",
        clock_block.rstrip(),
        "    initial begin",
        f"        $dumpfile(\"{testbench_name}.vcd\");",
        f"        $dumpvars(0, {testbench_name});",
        reset_block.rstrip(),
        assignment_block.rstrip(),
        f"        #{duration_ns};",
        "        $display(\"MCP_SIMULATION_PASS\");",
        "        $finish;",
        "    end",
        "endmodule",
        "",
    ])


def _modelsim_compile_command(file_info: dict) -> list[str]:
    path = str(file_info["path"])
    kind = file_info.get("type", _infer_hdl_kind(file_info["path"]))
    if kind == "vhdl":
        return [SIM_VCOM, path]
    if kind == "systemverilog":
        return [SIM_VLOG, "-sv", path]
    return [SIM_VLOG, path]


def _simulation_artifacts(proj_dir: str) -> list[dict]:
    sim_dir = simulation_dir(proj_dir)
    if not sim_dir.exists():
        return []
    artifacts = []
    for path in sorted(sim_dir.rglob("*")):
        if path.is_dir():
            continue
        if any(part.lower() == "work" for part in path.relative_to(sim_dir).parts):
            continue
        artifacts.append({
            "path": str(path),
            "relative_path": str(path.relative_to(sim_dir)),
            "size": path.stat().st_size,
        })
    return artifacts


FAMILY_SIM_LIB_FILES = {
    "cyclone iv e": ("cycloneive", "cycloneive_atoms.v"),
    "cyclone iv gx": ("cycloneiv", "cycloneiv_atoms.v"),
    "cyclone iv": ("cycloneiv", "cycloneiv_atoms.v"),
    "cyclone v": ("cyclonev", "cyclonev_atoms.v"),
    "cyclone 10 lp": ("cyclone10lp", "cyclone10lp_atoms.v"),
    "max 10": ("fiftyfivenm", "fiftyfivenm_atoms.v"),
    "max v": ("maxv", "maxv_atoms.v"),
    "max ii": ("maxii", "maxii_atoms.v"),
}


def _project_family(proj_dir: str, revision: str) -> str:
    qsf_path = find_qsf(proj_dir, revision)
    if not qsf_path:
        return ""
    for assignment in parse_qsf(qsf_path):
        if assignment["name"] == "FAMILY":
            return assignment["value"]
    return ""


def _intel_sim_libraries_for_family(family: str) -> list[tuple[str, Path, str]]:
    sim_lib = Path(QUARTUS.get("quartus_root", "")) / "eda" / "sim_lib"
    libs: list[tuple[str, Path, str]] = [
        ("lpm", sim_lib / "220model.v", "verilog"),
        ("sgate", sim_lib / "sgate.v", "verilog"),
        ("altera_mf", sim_lib / "altera_mf.v", "verilog"),
        ("altera_lnsim", sim_lib / "altera_lnsim.sv", "systemverilog"),
        ("altera_primitives", sim_lib / "altera_primitives.v", "verilog"),
    ]
    family_key = family.strip().lower()
    if family_key in FAMILY_SIM_LIB_FILES:
        lib_name, file_name = FAMILY_SIM_LIB_FILES[family_key]
        libs.append((lib_name, sim_lib / file_name, "verilog"))
    return [(name, path, kind) for name, path, kind in libs if path.exists()]


def _compile_intel_sim_libraries(sim_dir: Path, family: str, log_parts: list[str]) -> list[str]:
    library_names: list[str] = []
    for lib_name, source_path, kind in _intel_sim_libraries_for_family(family):
        lib_dir = sim_dir / lib_name
        r = run_quartus([SIM_VLIB, str(lib_dir)], cwd=str(sim_dir), timeout=120)
        log_parts.append(f"\n$ vlib {lib_name}\n{r['stdout']}{r['stderr']}")
        if not r["success"] and "already exists" not in (r["stdout"] + r["stderr"]).lower():
            continue
        r = run_quartus([SIM_VMAP, lib_name, str(lib_dir)], cwd=str(sim_dir), timeout=120)
        log_parts.append(f"\n$ vmap {lib_name} {lib_dir}\n{r['stdout']}{r['stderr']}")
        compile_cmd = [SIM_VLOG]
        if kind == "systemverilog":
            compile_cmd.append("-sv")
        compile_cmd.extend(["-work", lib_name, str(source_path)])
        r = run_quartus(compile_cmd, cwd=str(sim_dir), timeout=240)
        log_parts.append(f"\n$ {' '.join(compile_cmd)}\n{r['stdout']}{r['stderr']}")
        if r["success"]:
            library_names.append(lib_name)
    return library_names


def _discover_ip_setup_scripts(proj_dir: str) -> list[Path]:
    scripts: list[Path] = []
    for pattern in ("**/mentor/msim_setup.tcl", "**/msim_setup.tcl"):
        scripts.extend(Path(proj_dir).glob(pattern))
    unique: list[Path] = []
    seen: set[str] = set()
    for script in scripts:
        key = str(script.resolve()).lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(script)
    return unique


def _run_ip_setup_scripts(proj_dir: str, sim_dir: Path, log_parts: list[str]) -> list[str]:
    library_names: list[str] = []
    for index, script in enumerate(_discover_ip_setup_scripts(proj_dir), start=1):
        do_script = sim_dir / f"run_ip_msim_setup_{index}.do"
        do_script.write_text(
            "\n".join([
                f"cd {{{script.parent}}}",
                f"set QUARTUS_INSTALL_DIR {{{QUARTUS.get('quartus_root', '')}}}",
                f"do {{{script}}}",
                "if {[llength [info procs dev_com]]} { dev_com }",
                "if {[llength [info procs com]]} { com }",
                "quit -f",
                "",
            ]),
            encoding="utf-8",
        )
        r = run_quartus([SIM_VSIM, "-c", "-do", str(do_script)], cwd=str(sim_dir), timeout=300)
        log_parts.append(f"\n$ vsim -c -do {do_script}\n{r['stdout']}{r['stderr']}")
        if r["success"]:
            library_names.extend(["altera_ver", "lpm_ver", "sgate_ver", "altera_mf_ver", "altera_lnsim_ver"])
    return list(dict.fromkeys(library_names))


def _generate_eda_simulation_files(proj_dir: str, revision: str, log_parts: list[str]) -> bool:
    quartus_eda = str(Path(QUARTUS_BIN) / f"quartus_eda{_EXE_SUFFIX}")
    r = run_quartus(
        [quartus_eda, "--simulation", "--tool=questasim", "--format=verilog", revision],
        cwd=proj_dir,
        timeout=300,
    )
    log_parts.append(f"\n$ quartus_eda --simulation --tool=questasim --format=verilog {revision}\n{r['stdout']}{r['stderr']}")
    return r["success"]


def j(data: Any) -> str:
    """JSON-encode data to a string."""
    return json.dumps(data, indent=2)


def truncate(text: str, limit: int = 4000) -> str:
    if len(text) <= limit:
        return text
    half = limit // 2
    return text[:half] + f"\n... [{len(text) - limit} chars omitted] ...\n" + text[-half:]


def _flow_report_successful(text: str) -> bool:
    return (
        "Full Compilation was successful" in text
        or re.search(r"Flow Status\s*;\s*Successful\b", text, re.IGNORECASE) is not None
    )


_QUARTUS_VERSION_STR: Optional[str] = None
_QUARTUS_VERSION_TUPLE: Optional[tuple[int, int]] = None


def _quartus_version() -> str:
    """Cache and return the Quartus version string (e.g. 'Version 18.1.0 Build 625')."""
    global _QUARTUS_VERSION_STR
    if _QUARTUS_VERSION_STR is not None:
        return _QUARTUS_VERSION_STR
    if not Path(QUARTUS_SH).exists():
        _QUARTUS_VERSION_STR = ""
        return ""
    r = run_quartus([QUARTUS_SH, "--version"], timeout=30)
    for line in (r["stdout"] + "\n" + r["stderr"]).splitlines():
        if line.strip().startswith("Version "):
            _QUARTUS_VERSION_STR = line.strip()
            return _QUARTUS_VERSION_STR
    _QUARTUS_VERSION_STR = (r["stdout"] or r["stderr"]).strip().splitlines()[0] if (r["stdout"] or r["stderr"]).strip() else ""
    return _QUARTUS_VERSION_STR


def _parse_quartus_version() -> tuple[int, int]:
    """Return (major, minor) tuple for the detected Quartus version. (0, 0) if unknown."""
    global _QUARTUS_VERSION_TUPLE
    if _QUARTUS_VERSION_TUPLE is not None:
        return _QUARTUS_VERSION_TUPLE
    ver_str = _quartus_version()
    # Match patterns like "Version 18.1.0 Build 625" or "Quartus II 64-Bit Version 13.0.1"
    m = re.search(r"Version\s+(\d+)\.(\d+)", ver_str)
    if m:
        _QUARTUS_VERSION_TUPLE = (int(m.group(1)), int(m.group(2)))
    else:
        _QUARTUS_VERSION_TUPLE = (0, 0)
    return _QUARTUS_VERSION_TUPLE


def _ip_version_string() -> str:
    """Return the Quartus version as an IP version string (e.g. '18.1') for Qsys add_instance."""
    major, minor = _parse_quartus_version()
    if major > 0:
        return f"{major}.{minor}"
    return "18.1"  # sensible default for unknown versions


@mcp.tool()
def get_quartus_installation() -> str:
    """Return the detected Quartus installation, version, and command paths."""
    tool_status = {
        name: {"path": path, "exists": Path(path).exists()}
        for name, path in QUARTUS.get("tools", {}).items()
    }
    modelsim_status = {
        name: {"path": path, "exists": Path(path).exists()}
        for name, path in MODELSIM.get("tools", {}).items()
    }
    return j({
        "available": QUARTUS.get("available", False),
        "install_root": QUARTUS.get("install_root"),
        "quartus_root": QUARTUS.get("quartus_root"),
        "bin_dir": QUARTUS.get("bin_dir"),
        "quartus_sh": QUARTUS_SH,
        "version": _quartus_version(),
        "tools": tool_status,
        "modelsim": {
            "available": MODELSIM.get("available", False),
            "kind": MODELSIM.get("kind"),
            "bin_dir": MODELSIM.get("bin_dir"),
            "tools": modelsim_status,
            "checked": MODELSIM.get("checked", []),
            "error": MODELSIM.get("error", ""),
        },
        "default_project_dir": DEFAULT_PROJECT_DIR,
        "checked": QUARTUS.get("checked", []),
        "error": QUARTUS.get("error", ""),
        "all_installations_count": len(_ALL_INSTALLATIONS) if "_ALL_INSTALLATIONS" in dir() else 0,
        "current_install_index": _CURRENT_INSTALL_INDEX if "_CURRENT_INSTALL_INDEX" in dir() else 0,
    })


# ---------------------------------------------------------------------------
# Multi-version runtime switching
# ---------------------------------------------------------------------------

_ALL_INSTALLATIONS: list[dict] = []
_CURRENT_INSTALL_INDEX: int = 0


def _apply_installation(index: int) -> dict:
    """Apply an installation at the given index, regenerating all globals."""
    global QUARTUS, MODELSIM, QUARTUS_BIN, QUARTUS_SH, QUARTUS_MAP, QUARTUS_FIT
    global QUARTUS_ASM, QUARTUS_STA, QUARTUS_PGM, QUARTUS_POW, QUARTUS_DRC, QUARTUS_STP
    global QUARTUS_CPF, QUARTUS_DSE, QUARTUS_EDA, QUARTUS_JLI, QUARTUS_CVP, QUARTUS_CDB
    global SIM_VLIB, SIM_VMAP, SIM_VLOG, SIM_VCOM, SIM_VSIM
    global QSYS_SCRIPT, QSYS_GENERATE, QUARTUS_ENV
    global _QUARTUS_VERSION_STR, _QUARTUS_VERSION_TUPLE, _CURRENT_INSTALL_INDEX

    if not _ALL_INSTALLATIONS:
        return {"success": False, "error": "No installations discovered"}
    if index < 0 or index >= len(_ALL_INSTALLATIONS):
        return {"success": False, "error": f"Invalid index {index}, valid range: 0-{len(_ALL_INSTALLATIONS)-1}"}

    install = _ALL_INSTALLATIONS[index]
    QUARTUS = install
    QUARTUS_BIN = install.get("bin_dir", os.environ.get("QUARTUS_BIN", ""))
    QUARTUS_SH = install.get("tools", {}).get("quartus_sh", str(Path(QUARTUS_BIN) / "quartus_sh.exe"))
    QUARTUS_MAP = install.get("tools", {}).get("quartus_map", str(Path(QUARTUS_BIN) / "quartus_map.exe"))
    QUARTUS_FIT = install.get("tools", {}).get("quartus_fit", str(Path(QUARTUS_BIN) / "quartus_fit.exe"))
    QUARTUS_ASM = install.get("tools", {}).get("quartus_asm", str(Path(QUARTUS_BIN) / "quartus_asm.exe"))
    QUARTUS_STA = install.get("tools", {}).get("quartus_sta", str(Path(QUARTUS_BIN) / "quartus_sta.exe"))
    QUARTUS_PGM = install.get("tools", {}).get("quartus_pgm", str(Path(QUARTUS_BIN) / "quartus_pgm.exe"))
    QUARTUS_POW = install.get("tools", {}).get("quartus_pow", str(Path(QUARTUS_BIN) / "quartus_pow.exe"))
    QUARTUS_DRC = install.get("tools", {}).get("quartus_drc", str(Path(QUARTUS_BIN) / "quartus_drc.exe"))
    QUARTUS_STP = install.get("tools", {}).get("quartus_stp", str(Path(QUARTUS_BIN) / "quartus_stp.exe"))
    QUARTUS_CPF = install.get("tools", {}).get("quartus_cpf", str(Path(QUARTUS_BIN) / "quartus_cpf.exe"))
    QUARTUS_DSE = install.get("tools", {}).get("quartus_dse", str(Path(QUARTUS_BIN) / "quartus_dse.exe"))
    QUARTUS_EDA = install.get("tools", {}).get("quartus_eda", str(Path(QUARTUS_BIN) / "quartus_eda.exe"))
    QUARTUS_JLI = install.get("tools", {}).get("quartus_jli", str(Path(QUARTUS_BIN) / "quartus_jli.exe"))
    QUARTUS_CVP = install.get("tools", {}).get("quartus_cvp", str(Path(QUARTUS_BIN) / "quartus_cvp.exe"))
    QUARTUS_CDB = install.get("tools", {}).get("quartus_cdb", str(Path(QUARTUS_BIN) / "quartus_cdb.exe"))

    # Re-discover ModelSim (might differ per Quartus version)
    MODELSIM = discover_modelsim()
    SIM_VLIB = MODELSIM.get("tools", {}).get("vlib", "")
    SIM_VMAP = MODELSIM.get("tools", {}).get("vmap", "")
    SIM_VLOG = MODELSIM.get("tools", {}).get("vlog", "")
    SIM_VCOM = MODELSIM.get("tools", {}).get("vcom", "")
    SIM_VSIM = MODELSIM.get("tools", {}).get("vsim", "")

    # Re-discover Qsys tools for this version
    QSYS_SCRIPT, QSYS_GENERATE = _find_qsys_tools(QUARTUS_BIN)

    # Rebuild environment
    QUARTUS_ENV = {**os.environ}
    if install.get("quartus_root"):
        QUARTUS_ENV["QUARTUS_ROOTDIR"] = str(install["quartus_root"])
        QUARTUS_ENV["QUARTUS_ROOTDIR_OVERRIDE"] = str(install["quartus_root"])
    if QUARTUS_BIN:
        QUARTUS_ENV["PATH"] = str(QUARTUS_BIN) + os.pathsep + QUARTUS_ENV.get("PATH", "")
    if MODELSIM.get("bin_dir"):
        QUARTUS_ENV["PATH"] = str(MODELSIM["bin_dir"]) + os.pathsep + QUARTUS_ENV.get("PATH", "")

    # Clear version cache so it's re-detected
    _QUARTUS_VERSION_STR = None
    _QUARTUS_VERSION_TUPLE = None
    _CURRENT_INSTALL_INDEX = index

    log.info("Switched to Quartus installation [%d]: %s", index, install.get("bin_dir"))
    return {"success": True, "index": index, "bin_dir": QUARTUS_BIN}


@mcp.tool()
def list_quartus_installations() -> str:
    """List all discovered Quartus installations on this machine.

    Returns each installation with its index, path, and quick version info.
    Use the index with switch_quartus_installation to select a different version.
    """
    global _ALL_INSTALLATIONS
    _ALL_INSTALLATIONS = discover_all_quartus()
    if not _ALL_INSTALLATIONS:
        return j({"error": "No Quartus installations found", "count": 0})

    installs = []
    for i, inst in enumerate(_ALL_INSTALLATIONS):
        installs.append({
            "index": i,
            "active": (i == _CURRENT_INSTALL_INDEX),
            "bin_dir": inst["bin_dir"],
            "install_root": inst["install_root"],
            "quartus_root": inst["quartus_root"],
            "path_version": inst.get("path_version", "?"),
        })
    return j({"count": len(installs), "current_index": _CURRENT_INSTALL_INDEX, "installations": installs})


@mcp.tool()
def switch_quartus_installation(index: int) -> str:
    """Switch to a different Quartus version by index.

    First call list_quartus_installations to see available versions and their
    indices, then pass the desired index here. All subsequent compilation,
    analysis, and programming commands will use the newly selected version.

    Args:
        index: Installation index from list_quartus_installations
    """
    global _ALL_INSTALLATIONS
    if not _ALL_INSTALLATIONS:
        _ALL_INSTALLATIONS = discover_all_quartus()
    result = _apply_installation(index)
    if result["success"]:
        return j({
            "success": True,
            "switched_to": index,
            "bin_dir": QUARTUS_BIN,
            "version": _quartus_version(),
            "all_versions": [i.get("path_version", "?") for i in _ALL_INSTALLATIONS],
        })
    return j(result)


# ---------------------------------------------------------------------------
# 1. Project Management
# ---------------------------------------------------------------------------

@mcp.tool()
def create_project(name: str, directory: str, family: str, device: str) -> str:
    """Create a new Quartus II project with the given device family and part number.

    Args:
        name: Project name (also used as top-level entity name)
        directory: Directory where the project will be created
        family: Device family e.g. 'Cyclone IV E'
        device: Device part number e.g. 'EP4CE115F29C7'
    """
    os.makedirs(directory, exist_ok=True)
    tcl = textwrap.dedent(f"""\
        package require ::quartus::project
        cd {{{directory}}}
        if {{[project_exists {{{name}}}]}} {{
            project_open -revision {{{name}}} {{{name}}}
        }} else {{
            project_new -revision {{{name}}} {{{name}}}
        }}
        set_global_assignment -name FAMILY {{{family}}}
        set_global_assignment -name DEVICE {{{device}}}
        set_global_assignment -name TOP_LEVEL_ENTITY {{{name}}}
        export_assignments
        project_close
        puts "PROJECT_CREATED:{name}"
    """)
    r = run_tcl(tcl, cwd=directory)
    if "PROJECT_CREATED" in r["stdout"]:
        return j({"created": True, "project": name, "directory": directory,
                  "family": family, "device": device})
    return j({"error": r["stderr"][-800:] or r["stdout"][-400:] or "Unknown error"})


@mcp.tool()
def open_project(project_path: str) -> str:
    """Open and verify an existing Quartus project, then close it.

    Args:
        project_path: Path to .qpf file or project directory
    """
    try:
        qpf, proj_dir, revision = resolve_project(project_path)
    except ValueError as e:
        return j({"error": str(e)})

    tcl = textwrap.dedent(f"""\
        package require ::quartus::project
        project_open -revision {{{revision}}} {{{qpf}}}
        set family [get_global_assignment -name FAMILY]
        set device [get_global_assignment -name DEVICE]
        set top [get_global_assignment -name TOP_LEVEL_ENTITY]
        project_close
        puts "PROJECT_OPENED:{revision}"
        puts "FAMILY:$family"
        puts "DEVICE:$device"
        puts "TOP:$top"
    """)
    r = run_tcl(tcl, cwd=proj_dir)
    info = {
        "opened": "PROJECT_OPENED:" in r["stdout"] and r["success"],
        "qpf": qpf,
        "directory": proj_dir,
        "revision": revision,
        "stdout": r["stdout"][-1000:],
        "stderr": r["stderr"][-1000:],
    }
    for line in r["stdout"].splitlines():
        if line.startswith("FAMILY:"):
            info["family"] = line.split(":", 1)[1]
        elif line.startswith("DEVICE:"):
            info["device"] = line.split(":", 1)[1]
        elif line.startswith("TOP:"):
            info["top_level_entity"] = line.split(":", 1)[1]
    return j(info)


@mcp.tool()
def get_project_info(project_path: str) -> str:
    """Get metadata about a Quartus II project: family, device, top-level entity, file count.

    Args:
        project_path: Path to .qpf file or project directory
    """
    try:
        qpf, proj_dir, revision = resolve_project(project_path)
    except ValueError as e:
        return j({"error": str(e)})
    info: dict = {"revision": revision, "qpf": qpf, "directory": proj_dir}
    qsf_path = find_qsf(proj_dir, revision)
    if qsf_path:
        info["qsf"] = qsf_path
        assignments = parse_qsf(qsf_path)
        for a in assignments:
            if a["name"] in ("FAMILY", "DEVICE", "TOP_LEVEL_ENTITY"):
                info[a["name"].lower()] = a["value"]
        info["source_file_count"] = sum(1 for a in assignments if a["name"].endswith("_FILE"))
    return j(info)


@mcp.tool()
def list_projects(directory: str) -> str:
    """List all Quartus II projects (.qpf files) recursively in a directory.

    Args:
        directory: Root directory to search
    """
    p = Path(directory)
    if not p.is_dir():
        return j({"error": f"Directory not found: {directory}"})
    projects = [
        {"name": qpf.stem, "qpf": str(qpf), "directory": str(qpf.parent)}
        for qpf in sorted(p.rglob("*.qpf"))
    ]
    return j({"directory": directory, "projects": projects, "count": len(projects)})


@mcp.tool()
def close_project() -> str:
    """Close the current project. (Informational — server is stateless; each tool call manages its own session.)"""
    return j({"note": "Server is stateless. Each tool call opens and closes its own project session."})


@mcp.tool()
def archive_project(project_path: str, output_path: str) -> str:
    """Archive a Quartus II project to a .qar file.

    Args:
        project_path: Path to .qpf file or project directory
        output_path: Destination .qar archive file path
    """
    try:
        qpf, proj_dir, revision = resolve_project(project_path)
    except ValueError as e:
        return j({"error": str(e)})
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    r = run_quartus(
        [QUARTUS_SH, "--archive", "-output", str(output), revision],
        cwd=proj_dir,
        timeout=180,
    )
    return j({
        "archived": r["success"] and output.exists(),
        "output_path": str(output),
        "returncode": r["returncode"],
        "stdout": truncate(r["stdout"], 2000),
        "stderr": truncate(r["stderr"], 2000),
    })


# ---------------------------------------------------------------------------
# 2. Compilation & Synthesis
# ---------------------------------------------------------------------------

@mcp.tool()
def compile_project(project_path: str, flow: str = "full") -> str:
    """Compile a Quartus II project. Runs the specified flow stage.

    Args:
        project_path: Path to .qpf file or project directory
        flow: Stage to run — 'full' (all stages), 'map' (analysis+synthesis),
              'fit' (place and route), 'asm' (assembler / generate .sof),
              'sta' (static timing analysis)
    """
    try:
        qpf, proj_dir, revision = resolve_project(project_path)
    except ValueError as e:
        return j({"error": str(e)})

    flow = flow.lower().strip()
    if flow == "full":
        cmd = [QUARTUS_SH, "--flow", "compile", revision]
        timeout = 900
    elif flow == "map":
        cmd = [QUARTUS_MAP, revision, "--read_settings_files=on", "--write_settings_files=off"]
        timeout = 300
    elif flow == "fit":
        cmd = [QUARTUS_FIT, revision, "--read_settings_files=on", "--write_settings_files=off"]
        timeout = 300
    elif flow == "asm":
        cmd = [QUARTUS_ASM, revision, "--read_settings_files=on", "--write_settings_files=off"]
        timeout = 180
    elif flow == "sta":
        cmd = [QUARTUS_STA, revision, "--do_report_timing"]
        timeout = 180
    else:
        return j({"error": f"Unknown flow '{flow}'. Valid: full, map, fit, asm, sta"})

    r = run_quartus(cmd, cwd=proj_dir, timeout=timeout)
    return j({
        "flow": flow, "success": r["success"], "returncode": r["returncode"],
        "stdout": truncate(r["stdout"], 5000),
        "stderr": truncate(r["stderr"], 2000),
    })


@mcp.tool()
def run_analysis_synthesis(project_path: str) -> str:
    """Run Analysis and Synthesis only (quartus_map) on a project.

    Args:
        project_path: Path to .qpf file or project directory
    """
    try:
        qpf, proj_dir, revision = resolve_project(project_path)
    except ValueError as e:
        return j({"error": str(e)})
    r = run_quartus(
        [QUARTUS_MAP, revision, "--read_settings_files=on", "--write_settings_files=off"],
        cwd=proj_dir, timeout=300,
    )
    return j({"success": r["success"], "stdout": truncate(r["stdout"], 4000),
              "stderr": r["stderr"][-1000:]})


@mcp.tool()
def run_fitter(project_path: str) -> str:
    """Run the Fitter / Place & Route (quartus_fit) on a project.

    Args:
        project_path: Path to .qpf file or project directory
    """
    try:
        qpf, proj_dir, revision = resolve_project(project_path)
    except ValueError as e:
        return j({"error": str(e)})
    r = run_quartus(
        [QUARTUS_FIT, revision, "--read_settings_files=on", "--write_settings_files=off"],
        cwd=proj_dir, timeout=300,
    )
    return j({"success": r["success"], "stdout": truncate(r["stdout"], 4000),
              "stderr": r["stderr"][-1000:]})


@mcp.tool()
def run_assembler(project_path: str) -> str:
    """Run the Assembler (quartus_asm) to generate .sof and .pof programming files.

    Args:
        project_path: Path to .qpf file or project directory
    """
    try:
        qpf, proj_dir, revision = resolve_project(project_path)
    except ValueError as e:
        return j({"error": str(e)})
    r = run_quartus(
        [QUARTUS_ASM, revision, "--read_settings_files=on", "--write_settings_files=off"],
        cwd=proj_dir, timeout=180,
    )
    sof_files = [str(f) for f in Path(proj_dir).glob("**/*.sof")]
    pof_files = [str(f) for f in Path(proj_dir).glob("**/*.pof")]
    return j({"success": r["success"], "sof_files": sof_files, "pof_files": pof_files,
              "stdout": r["stdout"][-2000:]})


@mcp.tool()
def get_compilation_status(project_path: str) -> str:
    """Check the last compilation status and list generated artifact files.

    Args:
        project_path: Path to .qpf file or project directory
    """
    try:
        _, proj_dir, _ = resolve_project(project_path)
    except ValueError as e:
        return j({"error": str(e)})
    p = Path(proj_dir)
    status: dict = {"directory": proj_dir, "artifacts": {}}
    for pattern, key in [
        ("*.flow.rpt", "flow_report"), ("*.sta.summary", "sta_summary"),
        ("*.map.rpt",  "map_report"),  ("*.fit.rpt",    "fit_report"),
        ("*.asm.rpt",  "asm_report"),  ("*.sof",        "sof"),
        ("*.pof",      "pof"),
    ]:
        files = sorted(p.glob(pattern), key=lambda f: f.stat().st_mtime, reverse=True)
        if files:
            status["artifacts"][key] = str(files[0])

    flow_rpt = status["artifacts"].get("flow_report")
    if flow_rpt:
        text = Path(flow_rpt).read_text(errors="replace")
        status["flow_passed"] = _flow_report_successful(text)
        keywords = ("Flow Status", "Quartus II Version", "Revision Name", "Top-level Entity",
                    "Family", "Device", "Total logic", "Total registers",
                    "Total pins", "Total memory", "Logic utilization")
        status["summary_lines"] = [
            line.strip() for line in text.splitlines()
            if any(kw in line for kw in keywords)
        ][:25]
    return j(status)


@mcp.tool()
def get_compilation_messages(project_path: str) -> str:
    """Extract errors, warnings, and critical warnings from all compilation report files.

    Args:
        project_path: Path to .qpf file or project directory
    """
    try:
        _, proj_dir, _ = resolve_project(project_path)
    except ValueError as e:
        return j({"error": str(e)})
    messages: dict = {"errors": [], "warnings": [], "criticals": []}
    for rpt_file in sorted(Path(proj_dir).glob("*.rpt")):
        try:
            for line in rpt_file.read_text(errors="replace").splitlines():
                ls = line.strip()
                if re.match(r'Error\s*[\(:]', ls):
                    messages["errors"].append(ls)
                elif re.match(r'Critical Warning', ls):
                    messages["criticals"].append(ls)
                elif re.match(r'Warning\s*[\(:]', ls):
                    messages["warnings"].append(ls)
        except OSError:
            pass
    for key in messages:
        messages[key] = list(dict.fromkeys(messages[key]))[:100]
    return j({**messages,
              "error_count": len(messages["errors"]),
              "warning_count": len(messages["warnings"]),
              "critical_count": len(messages["criticals"])})


# ---------------------------------------------------------------------------
# 3. Incremental Compilation
# ---------------------------------------------------------------------------

@mcp.tool()
def enable_incremental_compilation(project_path: str, enable: bool = True) -> str:
    """Enable or disable incremental compilation (partition-based) for faster rebuilds.

    Args:
        project_path: Path to .qpf file or project directory
        enable: True to turn on incremental compilation, False to disable
    """
    try:
        _, proj_dir, revision = resolve_project(project_path)
    except ValueError as e:
        return j({"error": str(e)})

    tcl = textwrap.dedent(f"""\
        package require ::quartus::project
        project_open -revision {{{revision}}} {{{proj_dir}/{revision}.qpf}}
        set_global_assignment -name INCREMENTAL_COMPILATION {'ON' if enable else 'OFF'}
        export_assignments
        puts "INCREMENTAL_COMPILATION={'ENABLED' if enable else 'DISABLED'}"
        project_close
    """)
    r = run_tcl(tcl, cwd=proj_dir, timeout=60)
    return j({
        "enabled": enable,
        "success": r["success"],
        "stdout": r["stdout"][-500:],
        "stderr": r["stderr"][-500:],
    })


@mcp.tool()
def create_design_partition(
    project_path: str,
    entity_name: str,
    partition_type: str = "default",
) -> str:
    """Create a design partition for incremental or team-based compilation.

    Args:
        project_path: Path to .qpf file or project directory
        entity_name: Entity/module name to partition (e.g. 'cpu_core')
        partition_type: 'default', 'reconfigurable', 'preserved', 'empty'
    """
    try:
        _, proj_dir, revision = resolve_project(project_path)
    except ValueError as e:
        return j({"error": str(e)})

    partition_map = {
        "default": "1",
        "reconfigurable": "2",
        "preserved": "4",
        "empty": "8",
    }
    part_type_val = partition_map.get(partition_type, "1")

    tcl = textwrap.dedent(f"""\
        package require ::quartus::project
        project_open -revision {{{revision}}} {{{proj_dir}/{revision}.qpf}}
        set_global_assignment -name PARTITION {entity_name}
        set_global_assignment -name PARTITION_TYPE {part_type_val}
        set_global_assignment -name PARTITION_COLOR [lindex {{"#FF0000" "#00FF00" "#0000FF" "#FFA500" "#800080" "#00FFFF" "#FF00FF" "#FFFF00"}} [expr int(rand()*8)]]
        export_assignments
        puts "PARTITION_CREATED:{entity_name}"
        project_close
    """)
    r = run_tcl(tcl, cwd=proj_dir, timeout=60)
    return j({
        "created": "PARTITION_CREATED" in r["stdout"],
        "entity": entity_name,
        "partition_type": partition_type,
        "stdout": r["stdout"][-500:],
        "stderr": r["stderr"][-500:],
    })


@mcp.tool()
def get_design_partitions(project_path: str) -> str:
    """List all design partitions and netlist types for a project.

    Args:
        project_path: Path to .qpf file or project directory
    """
    try:
        qpf, proj_dir, revision = resolve_project(project_path)
    except ValueError as e:
        return j({"error": str(e)})

    qsf_path = find_qsf(proj_dir, revision)
    partitions = []
    if qsf_path:
        for a in parse_qsf(qsf_path):
            if a["name"] == "PARTITION":
                partitions.append(a["value"])

    # Also read partition from .cmp.rpt if exists
    rpt_path = Path(proj_dir) / f"{revision}.cmp.rpt"
    partition_status = "compiled" if rpt_path.exists() else "not_compiled"

    return j({
        "partitions": partitions,
        "count": len(partitions),
        "status": partition_status,
        "revision": revision,
    })


@mcp.tool()
def run_incremental_compile(project_path: str) -> str:
    """Run an incremental compile — recomplies only changed partitions.

    Requires INCREMENTAL_COMPILATION enabled and design partitions created.

    Args:
        project_path: Path to .qpf file or project directory
    """
    try:
        _, proj_dir, revision = resolve_project(project_path)
    except ValueError as e:
        return j({"error": str(e)})

    r = run_quartus(
        [QUARTUS_SH, "--flow", "compile", revision, "--incremental"],
        cwd=proj_dir, timeout=1200,
    )
    return j({
        "success": r["success"],
        "mode": "incremental",
        "returncode": r["returncode"],
        "stdout": truncate(r["stdout"], 4000),
        "stderr": truncate(r["stderr"], 2000),
    })


# ---------------------------------------------------------------------------
# 4. RTL Static Analysis
# ---------------------------------------------------------------------------

@mcp.tool()
def analyze_rtl_structure(project_path: str) -> str:
    """Analyze RTL source files for module hierarchy, port connectivity, and design patterns.

    Parse all registered Verilog/SystemVerilog files and extract:
    - Module names, ports, parameters
    - Instantiation hierarchy
    - Clock/reset naming conventions
    - Unconnected ports and potential issues

    Args:
        project_path: Path to .qpf file or project directory
    """
    try:
        qpf, proj_dir, revision = resolve_project(project_path)
    except ValueError as e:
        return j({"error": str(e)})

    # Collect source files from QSF
    qsf_path = find_qsf(proj_dir, revision)
    source_files = []
    if qsf_path:
        for a in parse_qsf(qsf_path):
            name = a["name"]
            if "FILE" in name and "_LIBRARY" not in name and "TB" not in name:
                # Resolve relative to project dir
                fpath = str(Path(proj_dir) / a["value"])
                if Path(fpath).exists() and Path(fpath).suffix in (".v", ".sv", ".vhd", ".vhdl"):
                    source_files.append(fpath)

    modules = []
    instances = []
    all_ports = {}
    module_bodies = {}  # module_name -> raw text

    for sf in source_files:
        try:
            text = Path(sf).read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        # Naive but effective regex-based parsing (not a full parser, but catches 95%+ of cases)
        # Extract modules
        for m in re.finditer(
            r'^\s*module\s+(\w+)\s*(?:#\s*\([^)]*\))?\s*\(([^;]*)\)\s*;',
            text, re.MULTILINE | re.IGNORECASE,
        ):
            mod_name = m.group(1)
            port_text = m.group(2)
            # Parse ports
            ports = []
            for p in re.finditer(r'(?:input|output|inout)\s*(?:wire|reg|logic)?\s*(?:\[[\w\-:\s]+\]\s*)?(\w+)',
                                  port_text, re.IGNORECASE):
                direction = re.search(r'(input|output|inout)', port_text[p.start():], re.IGNORECASE).group(1).lower()
                ports.append({"name": p.group(1), "direction": direction})
            modules.append({"name": mod_name, "ports": ports, "file": sf})
            all_ports[mod_name] = ports
            module_bodies[mod_name] = text[m.start():]

        # Extract instantiations
        for m in re.finditer(
            r'(\w+)\s+(?:#\s*\([^)]*\)\s*)?(\w+)\s*\(([^)]*)\)\s*;',
            text, re.MULTILINE,
        ):
            type_name = m.group(1)
            inst_name = m.group(2)
            port_map = m.group(3).strip()
            # Skip common non-module keywords
            if type_name.lower() in ("module", "endmodule", "input", "output", "inout",
                                       "wire", "reg", "assign", "always", "initial",
                                       "begin", "end", "function", "task", "parameter",
                                       "localparam", "if", "else", "for", "while", "case",
                                       "endcase", "generate", "endgenerate"):
                continue
            # Count connected ports
            connections = [c.strip() for c in port_map.split(",") if c.strip()]
            instances.append({
                "module": type_name,
                "instance": inst_name,
                "file": sf,
                "connections": len(connections),
            })

    # Identify top-level
    instantiated_modules = {i["module"] for i in instances}
    top_candidates = [m["name"] for m in modules if m["name"] not in instantiated_modules]

    # Clock/reset detection
    clock_ports = []
    reset_ports = []
    for mod_name, ports in all_ports.items():
        for p in ports:
            lower = p["name"].lower()
            if any(kw in lower for kw in ("clk", "clock", "clkin", "clk_in")):
                clock_ports.append({"module": mod_name, "port": p["name"], "direction": p["direction"]})
            if any(kw in lower for kw in ("rst", "reset", "rst_n", "reset_n", "nrst")):
                reset_ports.append({"module": mod_name, "port": p["name"], "direction": p["direction"]})

    # FSM detection (simple heuristics)
    fsm_modules = []
    for mod_name, body in module_bodies.items():
        # Look for state register patterns
        if re.search(r'(?:state|ST|c_state|n_state|current_state|next_state)\s*(?:<=|=)', body):
            num_states = len(re.findall(r'\b\d+\'b\d+', body))
            fsm_modules.append({
                "module": mod_name,
                "approx_states": max(num_states, 2),
            })

    # Unconnected port detection (module has more ports than its instantiations connect)
    unconnected_warnings = []
    for mod_name, ports in all_ports.items():
        mod_instances = [i for i in instances if i["module"] == mod_name]
        for inst in mod_instances:
            if inst["connections"] < len(ports):
                unconnected_warnings.append({
                    "module": mod_name,
                    "instance": inst["instance"],
                    "expected_ports": len(ports),
                    "connected": inst["connections"],
                    "unconnected": len(ports) - inst["connections"],
                })

    return j({
        "modules": modules,
        "module_count": len(modules),
        "instances": instances,
        "instance_count": len(instances),
        "top_candidates": top_candidates,
        "clocks": clock_ports,
        "reset_signals": reset_ports,
        "fsm_modules": fsm_modules,
        "unconnected_warnings": unconnected_warnings,
        "source_file_count": len(source_files),
    })


@mcp.tool()
def check_coding_style(project_path: str) -> str:
    """Check RTL coding style for common issues: missing resets, latches, combinational loops.

    Args:
        project_path: Path to .qpf file or project directory
    """
    try:
        qpf, proj_dir, revision = resolve_project(project_path)
    except ValueError as e:
        return j({"error": str(e)})

    qsf_path = find_qsf(proj_dir, revision)
    source_files = []
    if qsf_path:
        for a in parse_qsf(qsf_path):
            if "FILE" in a["name"] and "_LIBRARY" not in a["name"] and "TB" not in a["name"]:
                fpath = str(Path(proj_dir) / a["value"])
                if Path(fpath).exists():
                    source_files.append(fpath)

    issues = []
    total_always = 0
    total_assign = 0

    for sf in source_files:
        try:
            text = Path(sf).read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue

        lines = text.split("\n")

        # Check 1: always @(clk) without reset = potential power-on unknown
        for m in re.finditer(r'always\s*@\s*\(\s*(?:posedge|negedge)\s+(\w+)\s*\)(?!\s*or)', text, re.IGNORECASE):
            # This is a single-edge always block — check if there's a reset in the block
            block_start = m.end()
            block_snippet = text[block_start:block_start + 2000]
            if not re.search(r'if\s*\(\s*!?(?:rst|reset)', block_snippet, re.IGNORECASE):
                line_no = text[:m.start()].count("\n") + 1
                issues.append({
                    "file": sf,
                    "line": line_no,
                    "type": "no_reset_always",
                    "message": f"Sequential always block for clock '{m.group(1)}' has no reset condition — may start in unknown state",
                })

        # Check 2: always @(*) with incomplete sensitivity — warn on blocking assignment in combinational
        for m in re.finditer(
            r'always\s*@\s*\(\s*\*\s*\)|always_comb', text, re.IGNORECASE,
        ):
            block_start = m.end()
            block_snippet = text[block_start:block_start + 3000]
            # Find if there are non-blocking assigns in combinational blocks (definitely wrong!)
            nonblocking = re.findall(r'(\w+)\s*<=\s*[^=]', block_snippet)
            if nonblocking:
                line_no = text[:m.start()].count("\n") + 1
                issues.append({
                    "file": sf,
                    "line": line_no,
                    "type": "nonblocking_in_comb",
                    "message": f"Non-blocking assignment (<=) used in combinational always block — may infer unintended latches: {', '.join(set(nonblocking[:5]))}",
                })

        # Check 3: incomplete case/default in combinational always — latch risk
        case_blocks = re.finditer(r'always\s*@\s*\(\s*\*\s*\)|always_comb', text, re.IGNORECASE)
        for cb in case_blocks:
            block_end = text.find("end\n", cb.end()) if cb.end() < len(text) else -1
            if block_end == -1:
                block_end = text.find("endmodule", cb.end())
            if block_end == -1:
                block_end = len(text)
            snippet = text[cb.end():block_end]
            # Count case/default balance
            case_count = len(re.findall(r'\bcase\b', snippet, re.IGNORECASE))
            default_count = len(re.findall(r'\bdefault\b', snippet, re.IGNORECASE))
            endcase_count = len(re.findall(r'\bendcase\b', snippet, re.IGNORECASE))
            if case_count > 0 and case_count != endcase_count:
                line_no = text[:cb.start()].count("\n") + 1
                issues.append({
                    "file": sf,
                    "line": line_no,
                    "type": "case_endcase_mismatch",
                    "message": f"Mismatched case ({case_count}) / endcase ({endcase_count}) in combinational block — potential latch",
                })
            if case_count > 0 and default_count < case_count:
                line_no = text[:cb.start()].count("\n") + 1
                issues.append({
                    "file": sf,
                    "line": line_no,
                    "type": "missing_default_case",
                    "message": f"case without default in combinational block — may infer latch ({case_count} case(s), {default_count} default(s))",
                })

        total_always += len(re.findall(r'\balways\b', text, re.IGNORECASE))
        total_assign += len(re.findall(r'\bassign\b', text, re.IGNORECASE))

    return j({
        "issues": issues,
        "issue_count": len(issues),
        "sources_checked": len(source_files),
        "total_always_blocks": total_always,
        "total_continuous_assigns": total_assign,
        "severity": {
            "no_reset_always": len([i for i in issues if i["type"] == "no_reset_always"]),
            "nonblocking_in_comb": len([i for i in issues if i["type"] == "nonblocking_in_comb"]),
            "case_endcase_mismatch": len([i for i in issues if i["type"] == "case_endcase_mismatch"]),
            "missing_default_case": len([i for i in issues if i["type"] == "missing_default_case"]),
        },
    })


# ---------------------------------------------------------------------------
# 5. Timing Analysis
# ---------------------------------------------------------------------------

@mcp.tool()
def run_timing_analysis(project_path: str) -> str:
    """Run Static Timing Analysis (quartus_sta) on a compiled project.

    Args:
        project_path: Path to .qpf file or project directory
    """
    try:
        qpf, proj_dir, revision = resolve_project(project_path)
    except ValueError as e:
        return j({"error": str(e)})
    r = run_quartus([QUARTUS_STA, revision, "--do_report_timing"],
                    cwd=proj_dir, timeout=180)
    return j({"success": r["success"],
              "stdout": truncate(r["stdout"], 4000),
              "stderr": r["stderr"][-1000:]})


@mcp.tool()
def get_timing_summary(project_path: str) -> str:
    """Read and return the timing analysis summary (.sta.summary file).

    Args:
        project_path: Path to .qpf file or project directory
    """
    try:
        _, proj_dir, _ = resolve_project(project_path)
    except ValueError as e:
        return j({"error": str(e)})
    summaries = sorted(Path(proj_dir).glob("*.sta.summary"),
                       key=lambda f: f.stat().st_mtime, reverse=True)
    if not summaries:
        return j({"error": "No .sta.summary found. Run run_timing_analysis first."})
    text = summaries[0].read_text(errors="replace")
    return j({"file": str(summaries[0]), "content": text[:8000]})


@mcp.tool()
def get_clock_summary(project_path: str) -> str:
    """Get clock definitions and their frequency/period from the timing analysis summary.

    Args:
        project_path: Path to .qpf file or project directory
    """
    try:
        _, proj_dir, _ = resolve_project(project_path)
    except ValueError as e:
        return j({"error": str(e)})
    summaries = sorted(Path(proj_dir).glob("*.sta.summary"),
                       key=lambda f: f.stat().st_mtime, reverse=True)
    reports = sorted(Path(proj_dir).glob("*.sta.rpt"),
                     key=lambda f: f.stat().st_mtime, reverse=True)
    clocks = []
    source_file = None
    for report in reports + summaries:
        text = report.read_text(errors="replace")
        # Quartus Prime 23.1 clock table:
        # ; clk ; Base ; 20.000 ; 50.0 MHz ; 0.000 ; 10.000 ; ...
        for line in text.splitlines():
            m = re.search(
                r';\s*([^;]+?)\s*;\s*(Base|Virtual|Generated)\s*;\s*([\d.]+)\s*;\s*([\d.]+)\s*MHz',
                line,
            )
            if m:
                clocks.append({
                    "clock": m.group(1).strip(),
                    "type": m.group(2),
                    "period_ns": m.group(3),
                    "frequency_mhz": m.group(4),
                })
        # Original 13.1-style summary fallback:
        # ; clk_name ; 1 ; 50.0 MHz ; 20.0 ns ;
        for line in text.splitlines():
            m = re.search(
                r';\s*(\S+)\s*;\s*\d+\s*;\s*([\d.]+)\s*MHz\s*;\s*([\d.]+)\s*ns', line
            )
            if m:
                clocks.append({"clock": m.group(1),
                                "frequency_mhz": m.group(2),
                                "period_ns": m.group(3)})
        if clocks:
            source_file = report
            break
    unique_clocks = []
    seen = set()
    for clock in clocks:
        key = (clock.get("clock"), clock.get("period_ns"), clock.get("frequency_mhz"))
        if key not in seen:
            seen.add(key)
            unique_clocks.append(clock)
    return j({
        "clocks": unique_clocks,
        "count": len(unique_clocks),
        "source_file": str(source_file) if source_file else None,
        "sta_summary_file": str(summaries[0]) if summaries else None,
    })


@mcp.tool()
def get_timing_paths(project_path: str, from_node: str, to_node: str) -> str:
    """Get timing path information between two nodes via quartus_sta Tcl API.

    Args:
        project_path: Path to .qpf file or project directory
        from_node: Source node name (wildcards * supported)
        to_node: Destination node name (wildcards * supported)
    """
    try:
        qpf, proj_dir, revision = resolve_project(project_path)
    except ValueError as e:
        return j({"error": str(e)})
    path_filters = ""
    if from_node and from_node != "*":
        path_filters += f" -from {{{from_node}}}"
    if to_node and to_node != "*":
        path_filters += f" -to {{{to_node}}}"
    tcl = textwrap.dedent(f"""\
        project_open -revision {{{revision}}} {{{qpf}}}
        create_timing_netlist
        read_sdc
        update_timing_netlist
        report_timing{path_filters} -setup -npaths 5 -detail summary -stdout
        delete_timing_netlist
        project_close
    """)
    r = run_tcl_with(QUARTUS_STA, tcl, cwd=proj_dir, timeout=120)
    paths = []
    current_path: dict[str, Any] | None = None
    for line in r["stdout"].splitlines():
        path_match = re.search(r"Path #(\d+):.*slack is\s+(-?[\d.]+)", line)
        if path_match:
            current_path = {
                "path": int(path_match.group(1)),
                "slack_ns": path_match.group(2),
            }
            paths.append(current_path)
            continue
        field_match = re.search(
            r"(Slack|From Node|To Node|Launch Clock|Latch Clock|Relationship|Clock Skew|Data Delay)\s*:\s*(.+?)\s*$",
            line,
        )
        if field_match and current_path is not None:
            key = field_match.group(1).lower().replace(" ", "_")
            value = field_match.group(2).strip()
            if key in {"slack", "relationship", "clock_skew", "data_delay"}:
                key = f"{key}_ns"
            current_path[key] = value
    return j({
        "success": r["success"],
        "returncode": r["returncode"],
        "from": from_node,
        "to": to_node,
        "paths": paths,
        "raw_output": truncate(r["stdout"], 4000),
        "stderr": truncate(r["stderr"], 2000),
    })


# ---------------------------------------------------------------------------
# 6. Pin Assignment & Device
# ---------------------------------------------------------------------------

@mcp.tool()
def get_device_families() -> str:
    """List all FPGA/CPLD device families supported by this Quartus installation."""
    tcl = textwrap.dedent("""\
        package require ::quartus::device
        foreach f [get_family_list] {
            puts "FAMILY:$f"
        }
    """)
    r = run_tcl(tcl, cwd=DEFAULT_PROJECT_DIR)
    families = [line.split(":", 1)[1] for line in r["stdout"].splitlines()
                if line.startswith("FAMILY:")]
    if not families:
        return j({"error": "Could not retrieve families", "stderr": r["stderr"][-500:]})
    return j({"families": families, "count": len(families)})


@mcp.tool()
def get_devices(family: str) -> str:
    """List all device part numbers for a given device family.

    Args:
        family: Device family name e.g. 'Cyclone IV E'
    """
    tcl = textwrap.dedent(f"""\
        package require ::quartus::device
        foreach p [get_part_list -family {{{family}}}] {{
            puts "DEVICE:$p"
        }}
    """)
    r = run_tcl(tcl, cwd=DEFAULT_PROJECT_DIR)
    devices = [line.split(":", 1)[1] for line in r["stdout"].splitlines()
               if line.startswith("DEVICE:")]
    return j({"family": family, "devices": devices, "count": len(devices)})


@mcp.tool()
def get_pin_assignments(project_path: str) -> str:
    """Get all pin location assignments for a project, parsed from the .qsf file.

    Args:
        project_path: Path to .qpf file or project directory
    """
    try:
        qpf, proj_dir, revision = resolve_project(project_path)
    except ValueError as e:
        return j({"error": str(e)})
    qsf_path = find_qsf(proj_dir, revision)
    if not qsf_path:
        return j({"error": f"No .qsf file found in {proj_dir}"})
    pins = parse_qsf_pins(qsf_path)
    return j({"pins": pins, "count": len(pins), "qsf": qsf_path})


@mcp.tool()
def set_pin_assignment(project_path: str, pin_name: str,
                       pin_location: str, io_standard: str = "") -> str:
    """Set a pin location assignment for a signal in the project.

    Args:
        project_path: Path to .qpf file or project directory
        pin_name: Signal/port name to assign
        pin_location: Pin location e.g. PIN_A1
        io_standard: I/O standard e.g. '3.3-V LVTTL' (optional)
    """
    try:
        qpf, proj_dir, revision = resolve_project(project_path)
    except ValueError as e:
        return j({"error": str(e)})
    io_line = (
        f'set_instance_assignment -name IO_STANDARD "{io_standard}" -to {{{pin_name}}}'
        if io_standard else ""
    )
    tcl = textwrap.dedent(f"""\
        package require ::quartus::project
        project_open -revision {{{revision}}} {{{qpf}}}
        set_location_assignment {pin_location} -to {{{pin_name}}}
        {io_line}
        export_assignments
        project_close
        puts "PIN_SET"
    """)
    r = run_tcl(tcl, cwd=proj_dir)
    return j({"set": "PIN_SET" in r["stdout"], "pin": pin_name,
              "location": pin_location, "io_standard": io_standard,
              "stdout": r["stdout"][-300:]})


@mcp.tool()
def remove_pin_assignment(project_path: str, pin_name: str) -> str:
    """Remove pin location and IO_STANDARD assignments for a signal (edits .qsf directly).

    Args:
        project_path: Path to .qpf file or project directory
        pin_name: Signal/port name whose pin assignment should be removed
    """
    try:
        qpf, proj_dir, revision = resolve_project(project_path)
    except ValueError as e:
        return j({"error": str(e)})
    qsf_path = find_qsf(proj_dir, revision)
    if not qsf_path:
        return j({"error": f"No .qsf file found in {proj_dir}"})
    text = Path(qsf_path).read_text(errors="replace")
    new_lines, removed = [], 0
    escaped = re.escape(pin_name)
    for line in text.splitlines():
        if re.search(rf'-to\s+{escaped}\s*$', line.strip()):
            removed += 1
        else:
            new_lines.append(line)
    if removed:
        Path(qsf_path).write_text("\n".join(new_lines) + "\n")
    return j({"removed": removed > 0, "lines_removed": removed, "pin": pin_name})


@mcp.tool()
def get_global_assignments(project_path: str) -> str:
    """Get all global assignments from a project's .qsf settings file.

    Args:
        project_path: Path to .qpf file or project directory
    """
    try:
        qpf, proj_dir, revision = resolve_project(project_path)
    except ValueError as e:
        return j({"error": str(e)})
    qsf_path = find_qsf(proj_dir, revision)
    if not qsf_path:
        return j({"error": f"No .qsf file found in {proj_dir}"})
    assignments = parse_qsf(qsf_path)
    # Group multi-value assignments (e.g. multiple source files under same key)
    by_name: dict = {}
    for a in assignments:
        nm = a["name"]
        by_name.setdefault(nm, []).append(a["value"])
    # Flatten single-value ones for readability
    simplified = {k: (v[0] if len(v) == 1 else v) for k, v in by_name.items()}
    return j({"assignments": simplified, "total": len(assignments), "qsf": qsf_path})


@mcp.tool()
def set_global_assignment(project_path: str, name: str, value: str) -> str:
    """Set a global assignment in a project (e.g. DEVICE, TOP_LEVEL_ENTITY, SDC_FILE).

    Args:
        project_path: Path to .qpf file or project directory
        name: Assignment name e.g. DEVICE, SDC_FILE
        value: Assignment value
    """
    try:
        qpf, proj_dir, revision = resolve_project(project_path)
    except ValueError as e:
        return j({"error": str(e)})
    tcl = textwrap.dedent(f"""\
        package require ::quartus::project
        project_open -revision {{{revision}}} {{{qpf}}}
        set_global_assignment -name {{{name}}} {{{value}}}
        export_assignments
        project_close
        puts "GLOBAL_SET"
    """)
    r = run_tcl(tcl, cwd=proj_dir)
    return j({"set": "GLOBAL_SET" in r["stdout"], "name": name, "value": value,
              "stdout": r["stdout"][-300:]})


# ---------------------------------------------------------------------------
# 7. SDC Constraint Generation

@mcp.tool()
def generate_sdc_constraints(project_path: str, clock_freq_mhz: float = 50.0) -> str:
    """Auto-generate an SDC timing constraints file based on QSF clock pin assignments.

    Scans the project's QSF for clock pin assignments and generates proper
    create_clock / derive_pll_clocks / derive_clock_uncertainty constraints.

    Args:
        project_path: Path to .qpf file or project directory
        clock_freq_mhz: Default clock frequency in MHz (used when no user clock specified)
    """
    try:
        _, proj_dir, revision = resolve_project(project_path)
    except ValueError as e:
        return j({"error": str(e)})

    qsf_path = find_qsf(proj_dir, revision)
    sdc_path = Path(proj_dir) / f"{revision}.sdc"

    if not qsf_path:
        return j({"error": "No QSF found for project"})

    pars = parse_qsf(qsf_path)

    # Find project device/family
    top_entity = revision
    family = ""
    for a in pars:
        if a["name"] == "TOP_LEVEL_ENTITY":
            top_entity = a["value"]
        if a["name"] == "FAMILY":
            family = a["value"]

    # Find clock pins from assignments
    clock_pins = {}
    for a in pars:
        if a["name"] == "set_location_assignment":
            port = a["to"]
            pin = a["value"]
            port_lower = port.lower()
            if any(kw in port_lower for kw in ("clk", "clock", "clkin", "clk_in", "osc")):
                clock_pins[port] = {"pin": pin, "freq_mhz": clock_freq_mhz}

    if not clock_pins:
        # Fallback: if no clock pins assigned, generate default single-clock
        clock_pins["clk"] = {"pin": "PIN_E1", "freq_mhz": clock_freq_mhz}

    period_ns = 1000.0 / clock_freq_mhz

    # Generate SDC content
    sdc_lines = [
        "# SDC Timing Constraints — Auto-generated by Quartus MCP",
        f"# Target: {top_entity}  |  Device: {family}",
        "",
    ]

    for clk_name, clk_info in clock_pins.items():
        period = 1000.0 / clk_info["freq_mhz"]
        sdc_lines.append(f"# Clock: {clk_name} @ {clk_info['freq_mhz']} MHz on {clk_info['pin']}")
        sdc_lines.append(
            f"create_clock -name {clk_name} -period {period:.3f} "
            f"[get_ports {{{clk_name}}}]"
        )
        sdc_lines.append("")

    # Derive PLL-generated clocks if any QIP files exist
    has_pll = any(Path(proj_dir).glob("**/*pll*.qip"))
    if has_pll:
        sdc_lines.append("# Derive PLL-generated clocks automatically")
        sdc_lines.append("derive_pll_clocks")
        sdc_lines.append("")

    # Clock uncertainty (estimated from frequency)
    if clock_freq_mhz <= 50:
        uncertainty_ns = 0.050
    elif clock_freq_mhz <= 100:
        uncertainty_ns = 0.030
    elif clock_freq_mhz <= 200:
        uncertainty_ns = 0.020
    else:
        uncertainty_ns = 0.010

    sdc_lines.append(f"# Clock uncertainty: {uncertainty_ns * 1000:.0f} ps")
    sdc_lines.append(f"derive_clock_uncertainty -overwrite")
    sdc_lines.append(f"set_clock_uncertainty {uncertainty_ns:.3f} [all_clocks]")
    sdc_lines.append("")

    # Input/output delay estimates
    io_delay_ns = 0.4 * period_ns
    sdc_lines.append(f"# I/O timing constraints (estimated)")
    sdc_lines.append(f"set_input_delay -clock [all_clocks] {io_delay_ns:.3f} [all_inputs]")
    sdc_lines.append(f"set_output_delay -clock [all_clocks] {io_delay_ns:.3f} [all_outputs]")
    sdc_lines.append("")

    # False paths on reset
    has_reset = any(
        a["to"].lower() in ("rst", "rst_n", "reset", "reset_n", "nrst")
        for a in pars if a["name"] == "set_location_assignment"
    )
    if has_reset:
        sdc_lines.append("# Disable timing on asynchronous reset")
        sdc_lines.append("set_false_path -to [get_ports {rst*}]")
        sdc_lines.append("")

    sdc_lines.append(f"# SDC file generated for {revision} — review before use")
    sdc_content = "\n".join(sdc_lines) + "\n"

    sdc_path.write_text(sdc_content, encoding="utf-8")

    return j({
        "success": True,
        "sdc_path": str(sdc_path),
        "clocks": clock_pins,
        "clock_count": len(clock_pins),
        "sdc_lines": len(sdc_lines),
        "content": sdc_content,
    })


@mcp.tool()
def set_clock_constraint(
    project_path: str,
    clock_name: str,
    period_ns: float,
    duty_pct: float = 50.0,
) -> str:
    """Set or update a single create_clock constraint in the SDC file.

    Args:
        project_path: Path to .qpf file or project directory
        clock_name: Clock signal name (e.g. 'clk')
        period_ns: Clock period in nanoseconds
        duty_pct: Duty cycle percentage (default 50%)
    """
    try:
        _, proj_dir, revision = resolve_project(project_path)
    except ValueError as e:
        return j({"error": str(e)})

    sdc_path = Path(proj_dir) / f"{revision}.sdc"
    wave_min = 0.0
    wave_max = period_ns * duty_pct / 100.0

    new_line = (
        f"create_clock -name {clock_name} -period {period_ns:.3f} "
        f"-waveform {{{wave_min:.3f} {wave_max:.3f}}} [get_ports {{{clock_name}}}]"
    )

    if sdc_path.exists():
        content = sdc_path.read_text(encoding="utf-8")
        # Replace existing clock line for this name, or append
        pattern = rf"create_clock\s+-name\s+{clock_name}\b.*"
        if re.search(pattern, content):
            content = re.sub(pattern, new_line, content)
        else:
            content = content.rstrip() + "\n\n" + new_line + "\n"
        sdc_path.write_text(content, encoding="utf-8")
    else:
        sdc_path.write_text(
            f"# SDC for {revision}\n{new_line}\n", encoding="utf-8",
        )

    return j({
        "set": True,
        "sdc_path": str(sdc_path),
        "clock_name": clock_name,
        "period_ns": period_ns,
        "duty_pct": duty_pct,
    })


@mcp.tool()
def set_false_path_constraint(
    project_path: str,
    from_nodes: str = "",
    to_nodes: str = "",
) -> str:
    """Add a set_false_path timing constraint.

    Args:
        project_path: Path to .qpf file or project directory
        from_nodes: Source node pattern (e.g. 'get_ports {rst}') or empty
        to_nodes: Destination node pattern (e.g. 'get_registers {metastable*}') or empty
    """
    try:
        _, proj_dir, revision = resolve_project(project_path)
    except ValueError as e:
        return j({"error": str(e)})

    sdc_path = Path(proj_dir) / f"{revision}.sdc"
    parts = []
    if from_nodes:
        parts.append(f"-from [{from_nodes}]" if "[" not in from_nodes else f"-from {from_nodes}")
    if to_nodes:
        parts.append(f"-to [{to_nodes}]" if "[" not in to_nodes else f"-to {to_nodes}")

    false_path_line = "set_false_path " + " ".join(parts) if parts else "set_false_path -from [all_inputs] -to [all_outputs]"

    if sdc_path.exists():
        content = sdc_path.read_text(encoding="utf-8")
        content = content.rstrip() + "\n" + false_path_line + "\n"
        sdc_path.write_text(content, encoding="utf-8")
    else:
        sdc_path.write_text(
            f"# SDC for {revision}\n{false_path_line}\n", encoding="utf-8",
        )

    return j({
        "set": True,
        "sdc_path": str(sdc_path),
        "constraint": false_path_line,
    })


# ---------------------------------------------------------------------------
# 8. Programmer / JTAG
# ---------------------------------------------------------------------------

@mcp.tool()
def detect_jtag_devices() -> str:
    """Detect JTAG-connected devices using jtagconfig or quartus_pgm."""
    jtagconfig = str(Path(QUARTUS_BIN) / f"jtagconfig{_EXE_SUFFIX}")
    if Path(jtagconfig).exists():
        r = run_quartus([jtagconfig], timeout=30)
    else:
        r = run_quartus([QUARTUS_PGM, "--auto", "--list"], timeout=30)
    return j({"output": r["stdout"], "stderr": r["stderr"], "success": r["success"]})


@mcp.tool()
def get_programmer_cables() -> str:
    """List available programming cables (USB-Blaster, etc.)."""
    r = run_quartus([QUARTUS_PGM, "--list"], timeout=30)
    lines = [l.strip() for l in r["stdout"].splitlines() if l.strip()]
    return j({"cables": lines, "raw_output": r["stdout"]})


@mcp.tool()
def program_device(project_path: str, cable: str = "USB-Blaster",
                   device_index: int = 1) -> str:
    """Program an FPGA via JTAG using the most recently compiled .sof file.

    Args:
        project_path: Path to .qpf file or project directory
        cable: Cable name from get_programmer_cables (default: USB-Blaster)
        device_index: JTAG chain device index, 1-based (default: 1)
    """
    try:
        _, proj_dir, _ = resolve_project(project_path)
    except ValueError as e:
        return j({"error": str(e)})
    sof_files = sorted(Path(proj_dir).glob("**/*.sof"),
                       key=lambda f: f.stat().st_mtime, reverse=True)
    if not sof_files:
        return j({"error": "No .sof file found. Run compile_project first."})
    sof_path = str(sof_files[0])
    r = run_quartus(
        [QUARTUS_PGM, "-c", cable, "-m", "JTAG", "-o", f"p;{sof_path}@{device_index}"],
        cwd=proj_dir, timeout=120,
    )
    return j({"success": r["success"], "sof": sof_path, "cable": cable,
              "device_index": device_index,
              "stdout": r["stdout"], "stderr": r["stderr"]})


# ---------------------------------------------------------------------------
# 9. SignalTap II Logic Analyzer
# ---------------------------------------------------------------------------

@mcp.tool()
def create_signaltap_file(
    project_path: str,
    clock_pin: str,
    sample_depth: str = "128",
    signals: str = "",
    trigger_levels: int = 1,
    output_path: str = "",
) -> str:
    """Create a SignalTap II .stp file for the project.

    Args:
        project_path: Path to .qpf file or project directory
        clock_pin: Sample clock signal name (e.g. 'clk')
        sample_depth: Sample depth — '128', '256', '512', '1K', '2K', '4K', '8K', etc.
        signals: Comma-separated signal names to capture (e.g. 'led,counter[7:0],state')
        trigger_levels: Number of trigger conditions (1-10)
        output_path: Optional full path for the .stp file; defaults to <project_dir>/<revision>.stp
    """
    try:
        _, proj_dir, revision = resolve_project(project_path)
    except ValueError as e:
        return j({"error": str(e)})

    stp_path = Path(output_path) if output_path else Path(proj_dir) / f"{revision}.stp"
    stp_path.parent.mkdir(parents=True, exist_ok=True)

    # Parse signal list
    signal_list = [s.strip() for s in signals.split(",") if s.strip()] if signals else []
    if not signal_list:
        # Default: auto-discover from top-level ports
        top_entity = _top_entity_from_project(proj_dir, revision)
        ports = _verilog_ports_for_top(proj_dir, top_entity)
        signal_list = [p["name"] for p in ports if p["direction"] != "inout"]

    signal_entries = "\n".join(
        f'set_global_assignment -name STAP_SIGNAL_GROUP \'{sig}_group\''
        for sig in signal_list
    )

    tcl = textwrap.dedent(f"""\
        package require ::quartus::project
        project_open -revision {{{revision}}} {{{proj_dir}/{revision}.qpf}}

        # SignalTap II setup
        set_global_assignment -name ENABLE_SIGNALTAP ON
        set_global_assignment -name USE_SIGNALTAP_FILE {{{stp_path.name}}}
        set_global_assignment -name SIGNALTAP_FILE {{{stp_path.name}}}

        # Create SignalTap II file
        if {{[catch {{create_stp_file -overwrite -name {{{stp_path.name}}} -clock {{{clock_pin}}} -depth {{{sample_depth}}} -trigger_levels {trigger_levels}}} msg]}} {{
            puts "STP_CREATE_ERROR: $msg"
        }} else {{
            puts "STP_FILE_CREATED: {stp_path.name}"
        }}

        export_assignments
        project_close
    """)
    r = run_tcl(tcl, cwd=proj_dir, timeout=120)

    return j({
        "created": "STP_FILE_CREATED" in r["stdout"],
        "stp_path": str(stp_path),
        "clock_pin": clock_pin,
        "sample_depth": sample_depth,
        "signal_count": len(signal_list),
        "signals": signal_list,
        "stdout": r["stdout"][-1000:],
        "stderr": r["stderr"][-500:],
    })


@mcp.tool()
def get_signaltap_context(project_path: str) -> str:
    """Check whether SignalTap II is enabled for this project and list .stp files.

    Args:
        project_path: Path to .qpf file or project directory
    """
    try:
        _, proj_dir, revision = resolve_project(project_path)
    except ValueError as e:
        return j({"error": str(e)})

    stp_files = [str(f) for f in Path(proj_dir).glob("*.stp")]
    qsf_path = find_qsf(proj_dir, revision)
    signaltap_enabled = False
    stp_in_qsf = ""
    if qsf_path:
        for a in parse_qsf(qsf_path):
            if a["name"] in ("ENABLE_SIGNALTAP", "USE_SIGNALTAP_FILE", "SIGNALTAP_FILE"):
                signaltap_enabled = True
                if a["name"] in ("USE_SIGNALTAP_FILE", "SIGNALTAP_FILE"):
                    stp_in_qsf = a["value"]

    return j({
        "signaltap_enabled": signaltap_enabled,
        "stp_files": stp_files,
        "stp_in_qsf": stp_in_qsf,
        "revision": revision,
    })


@mcp.tool()
def program_with_signaltap(
    project_path: str,
    cable: str = "USB-Blaster",
    device_index: int = 1,
) -> str:
    """Program FPGA via JTAG including SignalTap II logic analyzer instance.

    This compiles the design with SignalTap enabled and then programs the resulting .sof.

    Args:
        project_path: Path to .qpf file or project directory
        cable: Cable name (default: USB-Blaster)
        device_index: JTAG chain device index, 1-based (default: 1)
    """
    try:
        _, proj_dir, revision = resolve_project(project_path)
    except ValueError as e:
        return j({"error": str(e)})

    # Recompile with SignalTap
    r_compile = run_quartus(
        [QUARTUS_SH, "--flow", "compile", revision],
        cwd=proj_dir, timeout=900,
    )

    # Find .sof
    sof_files = sorted(
        Path(proj_dir).glob("**/*.sof"),
        key=lambda f: f.stat().st_mtime, reverse=True,
    )
    if not sof_files:
        return j({"error": "No .sof file found after compilation", "compile_ok": r_compile["success"]})

    sof_path = str(sof_files[0])
    r_prog = run_quartus(
        [QUARTUS_PGM, "-c", cable, "-m", "JTAG",
         "-o", f"p;{sof_path}@{device_index}"],
        cwd=proj_dir, timeout=120,
    )

    return j({
        "success": r_prog["success"],
        "sof": sof_path,
        "cable": cable,
        "compile_success": r_compile["success"],
        "program_success": r_prog["success"],
        "stdout": r_prog["stdout"],
        "stderr": r_prog["stderr"],
    })


# ---------------------------------------------------------------------------
# 10. IP Core Generation
# ---------------------------------------------------------------------------

@mcp.tool()
def list_available_ip(device_family: str = "") -> str:
    """List MegaWizard / IP cores available for a device family.

    Args:
        device_family: Device family filter (e.g. 'Cyclone IV E'). Empty = show all families.
    """
    tcl = textwrap.dedent(f"""\
        package require ::quartus::device
        if {{[catch {{set ip_list [get_ip_list]}} err]}} {{
            puts "IP_LIST_ERROR: $err"
        }} else {{
            foreach ip $ip_list {{
                set name [lindex $ip 0]
                set version [lindex $ip 1]
                set family [lindex $ip 2]
                if {{ "{device_family}" == "" || [string match -nocase "*{device_family}*" $family] }} {{
                    puts "IP:$name:$version:$family"
                }}
            }}
        }}
    """)
    r = run_tcl(tcl, cwd=DEFAULT_PROJECT_DIR)
    ip_list = []
    for line in r["stdout"].splitlines():
        if line.startswith("IP:"):
            parts = line.split(":", 3)
            if len(parts) >= 4:
                ip_list.append({"name": parts[1], "version": parts[2], "family": parts[3]})
    return j({
        "device_family": device_family or "all",
        "ip_cores": ip_list,
        "count": len(ip_list),
        "stderr": r["stderr"][-500:],
    })


@mcp.tool()
def create_pll_ip(
    project_path: str,
    name: str,
    input_freq_mhz: float,
    output_freqs_mhz: str,
    output_phases: str = "",
    output_duty_cycles: str = "",
) -> str:
    """Generate an ALTPLL IP core using MegaWizard Plug-In Manager.

    Args:
        project_path: Path to .qpf file or project directory
        name: PLL instance name (e.g. 'pll_main')
        input_freq_mhz: Input clock frequency in MHz (e.g. 50.0)
        output_freqs_mhz: Comma-separated output frequencies in MHz (e.g. '100,50,25')
        output_phases: Comma-separated phase shifts in ps (e.g. '0,0,0')
        output_duty_cycles: Comma-separated duty cycles in % (e.g. '50,50,50')
    """
    try:
        _, proj_dir, revision = resolve_project(project_path)
    except ValueError as e:
        return j({"error": str(e)})

    freq_list = [float(x.strip()) for x in output_freqs_mhz.split(",") if x.strip()]
    phase_list = [int(x.strip()) for x in output_phases.split(",") if x.strip()] if output_phases else [0] * len(freq_list)
    duty_list = [int(x.strip()) for x in output_duty_cycles.split(",") if x.strip()] if output_duty_cycles else [50] * len(freq_list)

    num_outputs = len(freq_list)
    if num_outputs == 0:
        return j({"error": "At least one output frequency is required"})

    # Compute PLL multiply/divide
    multiply_factor = int(max(freq_list) / input_freq_mhz) if input_freq_mhz > 0 else 1
    if multiply_factor < 1:
        multiply_factor = 1

    tcl = textwrap.dedent(f"""\
        package require ::quartus::project
        project_open -revision {{{revision}}} {{{proj_dir}/{revision}.qpf}}

        set pll_path {{{proj_dir}/{name}}}
        set pll_qip [file join $pll_path {name}.qip]

        if {{[catch {{
            set megawiz [lindex [get_megawizard_plugins] 0]
            create_megawizard -name {name} -format VERILOG \\
                -type ALTPLL \\
                -params {{
                    operation_mode "normal"
                    input_frequency {input_freq_mhz}
                    multiply_by {multiply_factor}
                    output_clock_frequency {{"{' '.join(str(f) for f in freq_list)}"}}
                    phase_shift {{"{' '.join(str(p) for p in phase_list)}"}}
                    duty_cycle {{"{' '.join(str(d) for d in duty_list)}"}}
                }} \\
                -directory {{{proj_dir}/{name}}}

            set_global_assignment -name QIP_FILE {{{proj_dir}/{name}/{name}.qip}}
            export_assignments
            puts "PLL_CREATED:{name}"
        }} err]}} {{
            puts "PLL_ERROR: $err"
        }}

        project_close
    """)
    r = run_tcl(tcl, cwd=proj_dir, timeout=120)

    return j({
        "created": "PLL_CREATED" in r["stdout"],
        "name": name,
        "input_freq_mhz": input_freq_mhz,
        "output_freqs_mhz": freq_list,
        "directory": str(Path(proj_dir) / name),
        "stdout": r["stdout"][-1000:],
        "stderr": r["stderr"][-500:],
    })


@mcp.tool()
def create_ram_ip(
    project_path: str,
    name: str,
    width: int = 8,
    depth: int = 256,
    ram_type: str = "single_port",
) -> str:
    """Generate an on-chip RAM/ROM IP core (ALTSYNCRAM/altsyncram).

    Args:
        project_path: Path to .qpf file or project directory
        name: RAM instance name (e.g. 'ram_buffer')
        width: Data width in bits
        depth: Number of words
        ram_type: 'single_port', 'dual_port', 'rom'
    """
    try:
        _, proj_dir, revision = resolve_project(project_path)
    except ValueError as e:
        return j({"error": str(e)})

    ram_mode = "SINGLE_PORT" if ram_type == "single_port" else ("DUAL_PORT" if ram_type == "dual_port" else "ROM")

    tcl = textwrap.dedent(f"""\
        package require ::quartus::project
        project_open -revision {{{revision}}} {{{proj_dir}/{revision}.qpf}}

        if {{[catch {{
            create_megawizard -name {name} -format VERILOG \\
                -type ALTSYNCRAM \\
                -params {{
                    operation_mode "{ram_mode}"
                    width_a {width}
                    widthad_a [expr int(ceil(log({depth})/log(2)))]
                    numwords_a {depth}
                }} \\
                -directory {{{proj_dir}/{name}}}

            set_global_assignment -name QIP_FILE {{{proj_dir}/{name}/{name}.qip}}
            export_assignments
            puts "RAM_CREATED:{name}"
        }} err]}} {{
            puts "RAM_ERROR: $err"
        }}

        project_close
    """)
    r = run_tcl(tcl, cwd=proj_dir, timeout=120)

    return j({
        "created": "RAM_CREATED" in r["stdout"],
        "name": name,
        "width": width,
        "depth": depth,
        "ram_type": ram_type,
        "directory": str(Path(proj_dir) / name),
        "stdout": r["stdout"][-1000:],
        "stderr": r["stderr"][-500:],
    })


@mcp.tool()
def create_fifo_ip(
    project_path: str,
    name: str,
    width: int = 8,
    depth: int = 256,
    fifo_type: str = "SCFIFO",
) -> str:
    """Generate a FIFO IP core (SCFIFO/DCFIFO).

    Args:
        project_path: Path to .qpf file or project directory
        name: FIFO instance name (e.g. 'fifo_data')
        width: Data width in bits
        depth: Number of words
        fifo_type: 'SCFIFO' (single-clock) or 'DCFIFO' (dual-clock)
    """
    try:
        _, proj_dir, revision = resolve_project(project_path)
    except ValueError as e:
        return j({"error": str(e)})

    tcl = textwrap.dedent(f"""\
        package require ::quartus::project
        project_open -revision {{{revision}}} {{{proj_dir}/{revision}.qpf}}

        if {{[catch {{
            create_megawizard -name {name} -format VERILOG \\
                -type {fifo_type} \\
                -params {{
                    width {width}
                    depth {depth}
                }} \\
                -directory {{{proj_dir}/{name}}}

            set_global_assignment -name QIP_FILE {{{proj_dir}/{name}/{name}.qip}}
            export_assignments
            puts "FIFO_CREATED:{name}"
        }} err]}} {{
            puts "FIFO_ERROR: $err"
        }}

        project_close
    """)
    r = run_tcl(tcl, cwd=proj_dir, timeout=120)

    return j({
        "created": "FIFO_CREATED" in r["stdout"],
        "name": name,
        "width": width,
        "depth": depth,
        "fifo_type": fifo_type,
        "directory": str(Path(proj_dir) / name),
        "stdout": r["stdout"][-1000:],
        "stderr": r["stderr"][-500:],
    })


@mcp.tool()
def convert_programming_file(
    project_path: str,
    input_file: str = "",
    output_format: str = "jic",
) -> str:
    """Convert .sof to other programming file formats (.jic, .pof, .rbf, .hex).

    Args:
        project_path: Path to .qpf file or project directory
        input_file: Input .sof file path (default: latest .sof in project)
        output_format: Target format — 'jic', 'pof', 'rbf', 'hex', 'ttf', 'rpd'
    """
    try:
        _, proj_dir, revision = resolve_project(project_path)
    except ValueError as e:
        return j({"error": str(e)})

    # Find input SOF
    if input_file:
        sof_path = str(Path(input_file))
    else:
        sof_files = sorted(
            Path(proj_dir).glob("**/*.sof"),
            key=lambda f: f.stat().st_mtime, reverse=True,
        )
        if not sof_files:
            return j({"error": "No .sof file found. Run compile_project first."})
        sof_path = str(sof_files[0])

    output_path = str(Path(sof_path).with_suffix(f".{output_format}"))

    r = run_quartus(
        [QUARTUS_CPF, "-c", sof_path, output_path],
        cwd=proj_dir, timeout=120,
    )

    return j({
        "success": r["success"] and Path(output_path).exists(),
        "input": sof_path,
        "output": output_path,
        "format": output_format,
        "stdout": truncate(r["stdout"], 2000),
        "stderr": truncate(r["stderr"], 2000),
    })


# ---------------------------------------------------------------------------
# 11. Reports & Analysis
# ---------------------------------------------------------------------------

@mcp.tool()
def get_flow_summary(project_path: str) -> str:
    """Read the full compilation flow summary report (.flow.rpt).

    Args:
        project_path: Path to .qpf file or project directory
    """
    try:
        _, proj_dir, _ = resolve_project(project_path)
    except ValueError as e:
        return j({"error": str(e)})
    rpts = sorted(Path(proj_dir).glob("*.flow.rpt"),
                  key=lambda f: f.stat().st_mtime, reverse=True)
    if not rpts:
        return j({"error": "No .flow.rpt found. Run compile_project first."})
    text = rpts[0].read_text(errors="replace")
    # Extract the summary section (first ~100 lines usually contain it)
    summary_lines, capture = [], False
    for line in text.splitlines():
        if "Flow Summary" in line or "Flow Status" in line:
            capture = True
        if capture:
            summary_lines.append(line)
            if len(summary_lines) > 100:
                break
    return j({"file": str(rpts[0]),
              "summary": "\n".join(summary_lines),
              "total_lines": len(text.splitlines())})


@mcp.tool()
def get_resource_usage(project_path: str) -> str:
    """Get FPGA resource utilization (LEs, registers, memory, pins) from compilation reports.

    Args:
        project_path: Path to .qpf file or project directory
    """
    try:
        _, proj_dir, _ = resolve_project(project_path)
    except ValueError as e:
        return j({"error": str(e)})
    resources: dict = {}
    for pattern in ("*.fit.rpt", "*.map.rpt"):
        rpts = sorted(Path(proj_dir).glob(pattern),
                      key=lambda f: f.stat().st_mtime, reverse=True)
        if not rpts:
            continue
        text = rpts[0].read_text(errors="replace")
        in_section = False
        for line in text.splitlines():
            if "Resource Usage Summary" in line or "Total logic elements" in line:
                in_section = True
            if in_section and ";" in line:
                parts = [p.strip() for p in line.split(";")]
                if len(parts) >= 3 and parts[1] and parts[2]:
                    resources[parts[1]] = parts[2]
            if in_section and not line.strip() and resources:
                break
        if resources:
            return j({"resources": resources, "source": str(rpts[0])})
    return j({"resources": resources, "note": "No fit or map report found"})


@mcp.tool()
def read_report_file(project_path: str, report_type: str = "flow") -> str:
    """Read a specific Quartus compilation report file.

    Args:
        project_path: Path to .qpf file or project directory
        report_type: One of: 'flow', 'map', 'fit', 'asm', 'sta', 'pow'
    """
    try:
        _, proj_dir, _ = resolve_project(project_path)
    except ValueError as e:
        return j({"error": str(e)})
    pattern_map = {
        "flow": "*.flow.rpt", "map": "*.map.rpt", "fit": "*.fit.rpt",
        "asm": "*.asm.rpt",   "sta": "*.sta.summary", "pow": "*.pow.rpt",
    }
    pattern = pattern_map.get(report_type, f"*.{report_type}.rpt")
    rpts = sorted(Path(proj_dir).glob(pattern),
                  key=lambda f: f.stat().st_mtime, reverse=True)
    if not rpts:
        return j({"error": f"No {report_type} report found in {proj_dir}"})
    text = rpts[0].read_text(errors="replace")
    return j({"file": str(rpts[0]),
              "content": text[:12000],
              "total_chars": len(text),
              "note": "content truncated to 12000 chars" if len(text) > 12000 else ""})


@mcp.tool()
def get_power_report(project_path: str) -> str:
    """Run power analysis (quartus_pow) and return the power estimate report.

    Args:
        project_path: Path to .qpf file or project directory
    """
    try:
        qpf, proj_dir, revision = resolve_project(project_path)
    except ValueError as e:
        return j({"error": str(e)})
    r = run_quartus([QUARTUS_POW, revision], cwd=proj_dir, timeout=180)
    pow_rpts = sorted(Path(proj_dir).glob("*.pow.rpt"),
                      key=lambda f: f.stat().st_mtime, reverse=True)
    report = (pow_rpts[0].read_text(errors="replace")[:5000]
              if pow_rpts else r["stdout"][-3000:])
    return j({"success": r["success"], "report": report})


@mcp.tool()
def run_design_rule_check(project_path: str) -> str:
    """Run the Design Rule Check (quartus_drc) on a compiled project.

    Args:
        project_path: Path to .qpf file or project directory
    """
    try:
        qpf, proj_dir, revision = resolve_project(project_path)
    except ValueError as e:
        return j({"error": str(e)})
    r = run_quartus([QUARTUS_DRC, revision], cwd=proj_dir, timeout=120)
    violations = [l.strip() for l in r["stdout"].splitlines()
                  if "Violation" in l or re.match(r'\s*Error', l)]
    return j({"success": r["success"], "violations": violations,
              "stdout": r["stdout"][-2000:]})


# ---------------------------------------------------------------------------
# 12. File Management (QSF-based — no Tcl needed for reads)
# ---------------------------------------------------------------------------

@mcp.tool()
def list_project_files(project_path: str) -> str:
    """List all source files registered in a project's .qsf settings file.

    Args:
        project_path: Path to .qpf file or project directory
    """
    try:
        qpf, proj_dir, revision = resolve_project(project_path)
    except ValueError as e:
        return j({"error": str(e)})
    qsf_path = find_qsf(proj_dir, revision)
    if not qsf_path:
        return j({"error": f"No .qsf file found in {proj_dir}"})
    assignments = parse_qsf(qsf_path)
    files = [{"type": a["name"], "path": a["value"]}
             for a in assignments if a["name"].endswith("_FILE")]
    return j({"files": files, "count": len(files), "qsf": qsf_path})


@mcp.tool()
def add_file_to_project(project_path: str, file_path: str,
                        file_type: str = "SYSTEMVERILOG_FILE") -> str:
    """Add a source file to the project (.qsf) via Quartus Tcl.
    file_type: VERILOG_FILE, VHDL_FILE, SYSTEMVERILOG_FILE, SDC_FILE, MIF_FILE, etc.

    Args:
        project_path: Path to .qpf file or project directory
        file_path: Absolute or relative path to the source file to add
        file_type: Quartus file type assignment name (default: SYSTEMVERILOG_FILE)
    """
    try:
        qpf, proj_dir, revision = resolve_project(project_path)
    except ValueError as e:
        return j({"error": str(e)})
    tcl = textwrap.dedent(f"""\
        package require ::quartus::project
        project_open -revision {{{revision}}} {{{qpf}}}
        set_global_assignment -name {file_type} {{{file_path}}}
        export_assignments
        project_close
        puts "FILE_ADDED"
    """)
    r = run_tcl(tcl, cwd=proj_dir)
    return j({"added": "FILE_ADDED" in r["stdout"], "file": file_path,
              "type": file_type, "stdout": r["stdout"][-300:]})


@mcp.tool()
def remove_file_from_project(project_path: str, file_path: str) -> str:
    """Remove a source file from the project by editing the .qsf directly.

    Args:
        project_path: Path to .qpf file or project directory
        file_path: Path of the file to remove (as listed in the .qsf)
    """
    try:
        qpf, proj_dir, revision = resolve_project(project_path)
    except ValueError as e:
        return j({"error": str(e)})
    qsf_path = find_qsf(proj_dir, revision)
    if not qsf_path:
        return j({"error": f"No .qsf file found in {proj_dir}"})
    norm_target = file_path.replace("\\", "/").lower()
    text = Path(qsf_path).read_text(errors="replace")
    new_lines, removed = [], 0
    for line in text.splitlines():
        norm_line = line.replace("\\", "/").lower()
        if norm_target in norm_line and "_file" in norm_line:
            removed += 1
        else:
            new_lines.append(line)
    if removed:
        Path(qsf_path).write_text("\n".join(new_lines) + "\n")
    return j({"removed": removed > 0, "lines_removed": removed, "file": file_path})


@mcp.tool()
def read_qsf(project_path: str) -> str:
    """Read and return the raw content of a project's .qsf settings file.

    Args:
        project_path: Path to .qpf file or project directory
    """
    try:
        qpf, proj_dir, revision = resolve_project(project_path)
    except ValueError as e:
        return j({"error": str(e)})
    qsf_path = find_qsf(proj_dir, revision)
    if not qsf_path:
        return j({"error": f"No .qsf found in {proj_dir}"})
    text = Path(qsf_path).read_text(errors="replace")
    return j({"qsf_path": qsf_path, "content": text, "lines": len(text.splitlines())})


# ---------------------------------------------------------------------------
# 13. Simulation
# ---------------------------------------------------------------------------

@mcp.tool()
def create_testbench(project_path: str, top_entity: str = "", testbench_name: str = "",
                     language: str = "verilog", duration_ns: int = 1000,
                     overwrite: bool = False) -> str:
    """Create a simple simulation testbench and register it for run_simulation.

    Args:
        project_path: Path to .qpf file or project directory
        top_entity: Design top module/entity. Defaults to TOP_LEVEL_ENTITY from .qsf
        testbench_name: Testbench module name. Defaults to <top_entity>_tb
        language: Currently supports verilog
        duration_ns: Simulation duration after reset/input setup
        overwrite: Replace an existing testbench file when true
    """
    if language.lower() not in {"verilog", "v"}:
        return j({"error": "create_testbench currently supports Verilog testbenches"})
    try:
        _, proj_dir, revision = resolve_project(project_path)
    except ValueError as e:
        return j({"error": str(e)})
    top = top_entity or _top_entity_from_project(proj_dir, revision)
    tb_name = testbench_name or f"{top}_tb"
    sim_dir = simulation_dir(proj_dir)
    sim_dir.mkdir(parents=True, exist_ok=True)
    tb_path = sim_dir / f"{tb_name}.v"
    if tb_path.exists() and not overwrite:
        manifest = _add_simulation_manifest_file(proj_dir, tb_path, "testbench", "verilog")
        return j({
            "created": False,
            "exists": True,
            "testbench": str(tb_path),
            "top_module": tb_name,
            "manifest": str(simulation_manifest_path(proj_dir)),
            "files": manifest.get("files", []),
        })
    ports = _verilog_ports_for_top(proj_dir, top)
    tb_path.write_text(_create_verilog_testbench(top, tb_name, ports, duration_ns), encoding="utf-8")
    manifest = _add_simulation_manifest_file(proj_dir, tb_path, "testbench", "verilog")
    return j({
        "created": True,
        "testbench": str(tb_path),
        "top_entity": top,
        "top_module": tb_name,
        "ports": ports,
        "manifest": str(simulation_manifest_path(proj_dir)),
        "files": manifest.get("files", []),
    })


@mcp.tool()
def add_simulation_file(project_path: str, file_path: str, file_type: str = "auto",
                        role: str = "testbench", contents: str = "") -> str:
    """Register or create a simulation-only file.

    Args:
        project_path: Path to .qpf file or project directory
        file_path: Absolute path or project-relative path
        file_type: auto, verilog, systemverilog, vhdl, or tcl
        role: testbench, design, ip, library, or ip_setup
        contents: Optional file contents to write before registering
    """
    try:
        _, proj_dir, _ = resolve_project(project_path)
    except ValueError as e:
        return j({"error": str(e)})
    target = _resolve_project_file(proj_dir, file_path)
    if contents:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(contents, encoding="utf-8")
    if not target.exists():
        return j({"error": f"Simulation file not found: {target}"})
    inferred_type = "tcl" if target.suffix.lower() == ".tcl" else _infer_hdl_kind(target, file_type)
    manifest = _add_simulation_manifest_file(proj_dir, target, role, inferred_type)
    return j({
        "added": True,
        "file": str(target),
        "relative_path": _as_project_relative(proj_dir, target),
        "type": inferred_type,
        "role": role,
        "manifest": str(simulation_manifest_path(proj_dir)),
        "files": manifest.get("files", []),
    })


@mcp.tool()
def run_simulation(project_path: str, top_module: str = "", run_time: str = "all",
                   include_project_files: bool = True, include_simulation_files: bool = True,
                   include_intel_libraries: bool = True, use_ip_setup_scripts: bool = True,
                   generate_eda_netlist: bool = False, clean: bool = True,
                   timeout: int = 600) -> str:
    """Run a ModelSim simulation for the project.

    The default flow compiles project HDL files, registered simulation files,
    Intel Quartus simulation libraries, and any IP-generated mentor/msim_setup.tcl
    scripts before launching ModelSim vsim in batch mode.

    Args:
        project_path: Path to .qpf file or project directory
        top_module: Testbench top module. Defaults to manifest top_module or <top>_tb
        run_time: ModelSim run argument, usually all or a duration like 2 us
        include_project_files: Compile HDL files listed in the project .qsf
        include_simulation_files: Compile files registered via add_simulation_file/create_testbench
        include_intel_libraries: Compile Quartus sim_lib libraries for Intel IP/LPM/atoms
        use_ip_setup_scripts: Execute discovered mentor/msim_setup.tcl scripts for generated IP
        generate_eda_netlist: Run quartus_eda --simulation before compiling
        clean: Remove previous work library before running
        timeout: Timeout in seconds for the final vsim run
    """
    try:
        _, proj_dir, revision = resolve_project(project_path)
    except ValueError as e:
        return j({"error": str(e)})
    if not MODELSIM.get("available"):
        return j({"success": False, "error": MODELSIM.get("error", "ModelSim not available"), "modelsim": MODELSIM})
    sim_dir = simulation_dir(proj_dir)
    sim_dir.mkdir(parents=True, exist_ok=True)
    log_parts: list[str] = []
    if clean:
        for child in [
            "work", "lpm", "sgate", "altera_mf", "altera_lnsim", "altera_primitives",
            "cycloneive", "cycloneiv", "cyclonev", "cyclone10lp", "fiftyfivenm", "maxv", "maxii",
        ]:
            target = sim_dir / child
            if target.exists():
                shutil.rmtree(target, ignore_errors=True)
    if generate_eda_netlist:
        _generate_eda_simulation_files(proj_dir, revision, log_parts)

    r = run_quartus([SIM_VLIB, str(sim_dir / "work")], cwd=str(sim_dir), timeout=120)
    log_parts.append(f"\n$ vlib work\n{r['stdout']}{r['stderr']}")
    r = run_quartus([SIM_VMAP, "work", str(sim_dir / "work")], cwd=str(sim_dir), timeout=120)
    log_parts.append(f"\n$ vmap work {sim_dir / 'work'}\n{r['stdout']}{r['stderr']}")

    family = _project_family(proj_dir, revision)
    library_names: list[str] = []
    if include_intel_libraries:
        library_names.extend(_compile_intel_sim_libraries(sim_dir, family, log_parts))
    if use_ip_setup_scripts:
        library_names.extend(_run_ip_setup_scripts(proj_dir, sim_dir, log_parts))
    library_names = list(dict.fromkeys(library_names))

    compile_files: list[dict] = []
    if include_project_files:
        compile_files.extend(_project_hdl_files(proj_dir, revision))
    manifest = _read_simulation_manifest(proj_dir)
    if include_simulation_files:
        for entry in manifest.get("files", []):
            if entry.get("type") == "tcl":
                continue
            path = _resolve_project_file(proj_dir, entry.get("path", ""))
            if path.exists():
                compile_files.append({
                    "path": path,
                    "type": entry.get("type") or _infer_hdl_kind(path),
                    "role": entry.get("role", "testbench"),
                })
    unique_files: list[dict] = []
    seen_files: set[str] = set()
    for file_info in compile_files:
        key = str(file_info["path"].resolve()).lower()
        if key not in seen_files:
            seen_files.add(key)
            unique_files.append(file_info)

    compile_success = True
    for file_info in unique_files:
        cmd = _modelsim_compile_command(file_info)
        r = run_quartus(cmd, cwd=str(sim_dir), timeout=240)
        log_parts.append(f"\n$ {' '.join(cmd)}\n{r['stdout']}{r['stderr']}")
        compile_success = compile_success and r["success"]

    tb_top = top_module or manifest.get("top_module") or f"{_top_entity_from_project(proj_dir, revision)}_tb"
    vsim_cmd = [SIM_VSIM, "-c"]
    for lib_name in library_names:
        vsim_cmd.extend(["-L", lib_name])
    do_cmd = "run -all; quit -f" if run_time.strip().lower() == "all" else f"run {run_time}; quit -f"
    vsim_cmd.extend([f"work.{tb_top}", "-do", do_cmd])
    r = run_quartus(vsim_cmd, cwd=str(sim_dir), timeout=timeout)
    log_parts.append(f"\n$ {' '.join(vsim_cmd)}\n{r['stdout']}{r['stderr']}")
    log_text = "".join(log_parts)
    log_path = sim_dir / "simulation.log"
    transcript_path = sim_dir / "transcript"
    log_path.write_text(log_text, encoding="utf-8", errors="replace")
    pass_markers = ("MCP_SIMULATION_PASS" in log_text) or ("$finish" in log_text) or ("End time:" in log_text and r["success"])
    return j({
        "success": bool(compile_success and r["success"] and pass_markers),
        "compile_success": compile_success,
        "modelsim": MODELSIM,
        "top_module": tb_top,
        "family": family,
        "intel_libraries": library_names,
        "compiled_files": [str(info["path"]) for info in unique_files],
        "log": str(log_path),
        "transcript": str(transcript_path) if transcript_path.exists() else None,
        "returncode": r["returncode"],
        "stdout": truncate(r["stdout"], 4000),
        "stderr": truncate(r["stderr"], 2000),
        "artifacts": _simulation_artifacts(proj_dir),
    })


@mcp.tool()
def read_simulation_log(project_path: str, limit: int = 12000) -> str:
    """Read the latest simulation log/transcript for a project."""
    try:
        _, proj_dir, _ = resolve_project(project_path)
    except ValueError as e:
        return j({"error": str(e)})
    sim_dir = simulation_dir(proj_dir)
    candidates = [sim_dir / "simulation.log", sim_dir / "transcript"]
    for path in candidates:
        if path.exists():
            text = path.read_text(errors="replace")
            error_lines = []
            warning_lines = []
            for line in text.splitlines():
                stripped = line.strip()
                if re.search(r"(\*\*\s+(Error|Fatal):|\bFatal:)", stripped):
                    error_lines.append(stripped)
                elif re.search(r"\*\*\s+Warning:", stripped):
                    warning_lines.append(stripped)
            return j({
                "file": str(path),
                "content": truncate(text, limit),
                "total_chars": len(text),
                "pass": "MCP_SIMULATION_PASS" in text,
                "errors": error_lines[:50],
                "warnings": warning_lines[:50],
            })
    return j({"error": f"No simulation log found in {sim_dir}"})


@mcp.tool()
def list_simulation_artifacts(project_path: str) -> str:
    """List simulation files, logs, waveforms, scripts, and registered metadata."""
    try:
        _, proj_dir, _ = resolve_project(project_path)
    except ValueError as e:
        return j({"error": str(e)})
    manifest = _read_simulation_manifest(proj_dir)
    return j({
        "simulation_dir": str(simulation_dir(proj_dir)),
        "manifest": str(simulation_manifest_path(proj_dir)),
        "registered_files": manifest.get("files", []),
        "top_module": manifest.get("top_module", ""),
        "artifacts": _simulation_artifacts(proj_dir),
    })


# ---------------------------------------------------------------------------
# 14. Tcl Execution
# ---------------------------------------------------------------------------

@mcp.tool()
def run_tcl_script(script_path: str, project_path: str = "") -> str:
    """Execute an existing Tcl script file through quartus_sh -t.

    Args:
        script_path: Absolute path to the .tcl script file
        project_path: Optional project directory for working directory context
    """
    if not Path(script_path).exists():
        return j({"error": f"Script not found: {script_path}"})
    cwd = DEFAULT_PROJECT_DIR
    if project_path:
        try:
            _, cwd, _ = resolve_project(project_path)
        except ValueError:
            if Path(project_path).is_dir():
                cwd = project_path
    r = run_quartus([QUARTUS_SH, "-t", script_path], cwd=cwd, timeout=300)
    return j({"success": r["success"],
              "stdout": truncate(r["stdout"], 6000),
              "stderr": r["stderr"][-2000:]})


@mcp.tool()
def execute_tcl_command(tcl_code: str, project_path: str = "") -> str:
    """Execute arbitrary inline Tcl code through quartus_sh -t.
    Load packages as needed, e.g.: package require ::quartus::project

    Args:
        tcl_code: The Tcl code to execute
        project_path: Optional project path for working directory context
    """
    cwd = DEFAULT_PROJECT_DIR
    if project_path:
        try:
            _, cwd, _ = resolve_project(project_path)
        except ValueError:
            if Path(project_path).is_dir():
                cwd = project_path
    r = run_tcl(tcl_code, cwd=cwd, timeout=120)
    return j({"success": r["success"],
              "stdout": truncate(r["stdout"], 6000),
              "stderr": r["stderr"][-2000:]})


# ---------------------------------------------------------------------------
# 15. Design Space Explorer (DSE)
# --------------------------------------------------------------------------

@mcp.tool()
def run_design_space_explorer(
    project_path: str,
    seeds: int = 3,
    explore_mode: str = "extra_effort",
    focus: str = "fmax",
    timeout: int = 3600,
) -> str:
    """Run Design Space Explorer to find optimal compilation settings.

    DSE tries multiple compilations with different seeds/options to find
    the best timing, area, or power results. Critical for timing closure.

    Args:
        seeds: Number of seeds to try (1-50). More = better chance of closing timing.
        explore_mode: "quick" (rev-level), "extra_effort" (rev+Fitter seed), "full" (all knobs)
        focus: Optimization goal — "fmax", "area", "power", or "balanced"
        timeout: Max seconds for the entire exploration (DSE can run hours)
    """
    try:
        _, proj_dir, revision = resolve_project(project_path)
    except ValueError as e:
        return j({"error": str(e)})

    if not Path(QUARTUS_DSE).exists():
        return j({"error": "quartus_dse not found. Pro/Standard edition only.",
                   "dse_path": QUARTUS_DSE})

    # Write exploration settings file
    settings = textwrap.dedent(f"""\
        # DSE settings generated by QuartusMCP
        set seed        {seeds}
        set max_seed    {seeds}
        set exploration_mode   {explore_mode}
        set optimization_goal  {focus}
        """)
    dse_dir = Path(proj_dir) / "dse"
    dse_dir.mkdir(exist_ok=True)
    settings_path = dse_dir / "dse_settings.tcl"
    settings_path.write_text(settings)

    cmd = [str(QUARTUS_DSE), str(proj_dir / f"{revision}.qpf"),
           "--seed", str(seeds),
           "--exploration_mode", explore_mode,
           "--optimization_goal", focus]

    r = run_quartus(cmd, cwd=str(dse_dir), timeout=timeout)

    # Parse results from dse folder
    reports = list(dse_dir.glob("dse_*.rpt"))
    best = {}
    for rp in sorted(reports):
        txt = rp.read_text(errors="replace")[:5000]
        fmax_match = re.search(r"Fmax[:\s]+([0-9.]+)\s*MHz", txt)
        if fmax_match:
            best[rp.name] = {"fmax_mhz": float(fmax_match.group(1))}

    return j({"success": r["success"],
              "settings_file": str(settings_path),
              "mode": explore_mode, "focus": focus, "seeds": seeds,
              "reports": {k: v for k, v in list(best.items())[:10]},
              "stdout": truncate(r["stdout"], 4000),
              "stderr": r["stderr"][-1000:]})


@mcp.tool()
def run_seed_sweep(
    project_path: str,
    seeds: str = "1-5",
    target: str = "",
    timeout: int = 3600,
) -> str:
    """Quick seed sweep — compile with multiple seeds and compare Fmax.

    Lightweight alternative to full DSE. Try seeds 1-5 or 1-10 to see if
    timing improves with different fitter seeds.

    Args:
        seeds: Range like "1-5" or comma-separated "1,3,5,7"
        target: Specific clock to measure Fmax for (default: worst clock)
    """
    try:
        _, proj_dir, revision = resolve_project(project_path)
    except ValueError as e:
        return j({"error": str(e)})

    if "-" in seeds:
        parts = seeds.split("-")
        seed_list = list(range(int(parts[0]), int(parts[1]) + 1))
    else:
        seed_list = [int(s.strip()) for s in seeds.split(",")]

    results = []
    for seed in seed_list:
        tcl_code = textwrap.dedent(f"""\
            project_open {revision}
            set_global_assignment -name SEED {seed}
            execute_flow -compile
            project_close
            """)
        r = run_tcl(tcl_code, cwd=proj_dir, timeout=timeout // len(seed_list))

        # Quick parse STA report
        fmax = None
        sta_files = list((Path(proj_dir) / "output_files").glob("*.sta.rpt"))
        for sf in sta_files:
            txt = sf.read_text(errors="replace")
            fm = re.search(r";\s*([0-9.]+)\s*MHz", txt)
            if fm:
                fm_v = float(fm.group(1))
                if fmax is None or fm_v > fmax:
                    fmax = fm_v
        results.append({"seed": seed, "success": r["success"], "fmax_mhz": fmax})

    return j({"sweep_results": results,
              "best_seed": min(results, key=lambda x: -(x.get("fmax_mhz") or 0)) if results else None})


# ---------------------------------------------------------------------------
# 16. Gate-Level Simulation (quartus_eda → SDF + netlist)
# ---------------------------------------------------------------------------

@mcp.tool()
def generate_gate_level_simulation(
    project_path: str,
    tool: str = "modelsim",
    language: str = "verilog",
    timing_model: str = "slow",
) -> str:
    """Generate gate-level netlist + SDF delay files for post-synthesis/route simulation.

    This is the critical link between Quartus and ModelSim for timing-accurate
    back-annotated simulation. Produces:
      - <revision>.vo (gate-level Verilog netlist)
      - <revision>_v.sdo (Standard Delay Format, back-annotation)

    After running this, use ModelSimMCP's run_gate_level_simulation tool
    with the generated files.

    Args:
        tool: Target simulator — "modelsim", "questa", or "none" (just generate files)
        language: "verilog" or "vhdl"
        timing_model: "slow" (worst-case), "fast", or "zero_delays"
    """
    try:
        _, proj_dir, revision = resolve_project(project_path)
    except ValueError as e:
        return j({"error": str(e)})

    output_dir = Path(proj_dir) / "simulation" / "modelsim"
    output_dir.mkdir(parents=True, exist_ok=True)

    tool_map = {"modelsim": "modelsim", "questa": "questa", "none": "none"}
    timing_opt = {
        "slow": "--simulation_timing_model=slow",
        "fast": "--simulation_timing_model=fast",
        "zero_delays": "",
    }

    cmd = [str(QUARTUS_EDA), str(proj_dir / revision),
           f"--simulation",
           f"--tool={tool_map.get(tool, 'modelsim')}",
           f"--format={language}"]
    if timing_model != "zero_delays":
        cmd.append(f"--simulation_timing_model={timing_model}")

    r = run_quartus(cmd, cwd=str(proj_dir), timeout=300)

    # Discover output files
    found = {}
    for pat, label in [("*.vo", "gate_netlist"), ("*.sdo", "sdf_file"),
                        ("*_v.sdo", "sdf_vital"), ("*.vho", "vhdl_netlist")]:
        matches = list(output_dir.glob(pat))
        if matches:
            found[label] = str(matches[0])

    # Also check in proj_dir root simulation
    sim_root = Path(proj_dir) / "simulation"
    for pat, label in [("**/*.vo", "gate_netlist_alt"), ("**/*.sdo", "sdf_file_alt")]:
        if label.replace("_alt", "") not in found:
            matches = list(sim_root.glob(pat))
            if matches:
                found[label] = str(matches[0])

    return j({"success": r["success"],
              "tool": tool, "language": language, "timing_model": timing_model,
              "output_files": found,
              "simulation_dir": str(output_dir),
              "next_step": ("Run run_gate_level_simulation in ModelSimMCP with netlist=" +
                             found.get("gate_netlist", "N/A") +
                             " and sdf=" + found.get("sdf_file", "N/A")) if found else "No files found",
              "stdout": truncate(r["stdout"], 2000),
              "stderr": r["stderr"][-1000:]})


@mcp.tool()
def get_gate_sim_context(
    project_path: str,
) -> str:
    """Check if gate-level simulation files exist for a project.

    Returns paths to netlist (.vo) and SDF (.sdo) files, ready to feed into
    ModelSimMCP run_gate_level_simulation.
    """
    try:
        _, proj_dir, revision = resolve_project(project_path)
    except ValueError as e:
        return j({"error": str(e)})

    sim_dir = Path(proj_dir) / "simulation" / "modelsim"
    context = {
        "project": revision,
        "has_gate_netlist": False,
        "has_sdf": False,
        "gate_netlists": [str(p) for p in sim_dir.glob("*.vo")],
        "sdf_files": [str(p) for p in sim_dir.glob("*.sdo")],
        "vhd_netlists": [str(p) for p in sim_dir.glob("*.vho")],
    }
    context["has_gate_netlist"] = len(context["gate_netlists"]) > 0
    context["has_sdf"] = len(context["sdf_files"]) > 0
    return j(context)


# ---------------------------------------------------------------------------
# 17. Power Analysis
# ---------------------------------------------------------------------------

@mcp.tool()
def run_power_analysis(
    project_path: str,
    vcd_file: str = "",
    saif_file: str = "",
    glitch_filtering: bool = True,
) -> str:
    """Run standalone power analysis with quartus_pow.

    Requires a compiled project. For signal-activity-based power:
    provide a VCD (simulation dump) or SAIF file.

    Args:
        vcd_file: Optional VCD file from gate-level simulation for activity-based power
        saif_file: Optional SAIF file for switching activity
        glitch_filtering: Apply glitch filtering for more realistic estimates
    """
    try:
        _, proj_dir, revision = resolve_project(project_path)
    except ValueError as e:
        return j({"error": str(e)})
    if not Path(QUARTUS_POW).exists():
        return j({"error": "quartus_pow not found"})

    cmd = [str(QUARTUS_POW), str(proj_dir / revision)]
    if vcd_file:
        cmd.extend(["--input_vcd", vcd_file])
    if saif_file:
        cmd.extend(["--input_saif", saif_file])
    if glitch_filtering:
        cmd.append("--use_glitch_filtering=on")

    r = run_quartus(cmd, cwd=str(proj_dir), timeout=300)

    # Parse power report for key numbers
    pow_report = Path(proj_dir) / f"{revision}.pow.rpt"
    summary = {}
    if pow_report.exists():
        txt = pow_report.read_text(errors="replace")
        for key_pat, label in [
            (r"Total Thermal Power Dissipation[^0-9]*([0-9.]+)\s*mW", "total_power_mw"),
            (r"Core Dynamic Thermal Power[^0-9]*([0-9.]+)\s*mW", "dynamic_power_mw"),
            (r"Core Static Thermal Power[^0-9]*([0-9.]+)\s*mW", "static_power_mw"),
            (r"I/O Thermal Power[^0-9]*([0-9.]+)\s*mW", "io_power_mw"),
        ]:
            m = re.search(key_pat, txt)
            if m:
                summary[label] = float(m.group(1))

    return j({"success": r["success"],
              "power_summary": summary,
              "report": str(pow_report) if pow_report.exists() else "",
              "stdout": truncate(r["stdout"], 3000),
              "stderr": r["stderr"][-1000:]})


# ---------------------------------------------------------------------------
# 18. Timing Closure Assistant
# ---------------------------------------------------------------------------

@mcp.tool()
def get_worst_timing_paths(
    project_path: str,
    n_paths: int = 10,
    detailed: bool = False,
) -> str:
    """Get the N worst timing paths from STA report with closure suggestions.

    Analyzes the timing report and provides actionable suggestions for
    each failing or near-failing path.

    Args:
        n_paths: Number of worst paths to extract (default 10)
        detailed: Include full path details (nodes, delays, cell types)
    """
    try:
        _, proj_dir, revision = resolve_project(project_path)
    except ValueError as e:
        return j({"error": str(e)})

    sta_files = list((Path(proj_dir) / "output_files").glob("*.sta.rpt"))
    if not sta_files:
        return j({"error": "No STA report found. Run compile_project or run_timing_analysis first."})

    sta_text = sta_files[0].read_text(errors="replace")

    # Find all timing path entries
    paths = []
    path_pattern = re.compile(
        r"(?:Slack|Setup|Hold)[^:]*:\s*([-\d.]+)\s*(?:ns|clock period)"
        r"[^;]*;\s*([-\d.]+)\s*MHz", re.DOTALL)

    # More robust: parse the structured STA report
    # Pattern for a timing summary line
    summary_pattern = re.compile(
        r"(?:Type|Path)\s*:\s*(Setup|Hold|Recovery|Removal)\s*"
        r"Slack\s*:\s*([-\d.]+)\s*"
        r"(?:.*?Fmax[^:]*:\s*([\d.]+)\s*MHz)?",
        re.IGNORECASE | re.DOTALL)

    # Simpler: extract slack values with context
    slack_lines = []
    for match in re.finditer(
        r"Slack\s*:\s*(-?[\d.]+)\s*(?:ns)?",
        sta_text, re.IGNORECASE
    ):
        val = float(match.group(1))
        # Get surrounding context
        start = max(0, match.start() - 300)
        end = min(len(sta_text), match.end() + 200)
        ctx = sta_text[start:end].replace("\n", " ").strip()[:400]
        slack_lines.append({"slack_ns": val, "context": ctx})
        if len(slack_lines) >= n_paths * 3:
            break

    # Sort by slack (worst first)
    slack_lines.sort(key=lambda x: x["slack_ns"])
    worst_paths = slack_lines[:n_paths]

    # Generate suggestions
    suggestions = []
    failing = [p for p in worst_paths if p["slack_ns"] < -0.1]
    near_fail = [p for p in worst_paths if -0.1 <= p["slack_ns"] < 0.5]

    if failing:
        suggestions.append({
            "severity": "critical",
            "paths_failing": len(failing),
            "actions": [
                "1. Reduce clock frequency or add multicycle constraints",
                "2. Pipeline long combinational paths (insert registers)",
                "3. Add false_path for async crossings",
                "4. Try seed sweep (different fitter seeds)",
                "5. Check for inferred latches (avoid #delay in RTL)",
                "6. Enable register retiming optimization",
            ]
        })
    if near_fail:
        suggestions.append({
            "severity": "warning",
            "paths_near_limit": len(near_fail),
            "actions": [
                "Run seed sweep to see if timing can close",
                "Consider adding moderate pipeline stages",
                "Check clock uncertainty settings are realistic",
            ]
        })
    if not failing and not near_fail:
        suggestions.append({"severity": "clean", "message": "All timing paths pass with margin."})

    return j({
        "worst_slack_ns": worst_paths[0]["slack_ns"] if worst_paths else None,
        "paths_analyzed": len(worst_paths),
        "worst_paths": worst_paths[:n_paths] if not detailed else worst_paths[:n_paths],
        "suggestions": suggestions,
    })


@mcp.tool()
def get_clock_domain_crossings(
    project_path: str,
) -> str:
    """Detect clock domain crossings (CDC) from clock and constraint analysis.

    Cross-clock-domain paths are a common source of metastability.
    This tool identifies them and suggests synchronization strategies.
    """
    try:
        _, proj_dir, revision = resolve_project(project_path)
    except ValueError as e:
        return j({"error": str(e)})

    # Read QSF for clock assignments
    qsf_path = find_qsf(proj_dir, revision)
    if not qsf_path:
        return j({"error": "No QSF found"})

    qsf_text = Path(qsf_path).read_text(errors="replace")

    # Find all clock assignments
    clocks = set()
    for m in re.finditer(r'set_location_assignment.*?-to\s+(\S+)', qsf_text):
        pass  # pin assignments, not clocks

    # Find SDC-style clock constraints if stored in QSF as global assignments
    for m in re.finditer(r'set_global_assignment.*?CLOCK.*?(\S+)', qsf_text, re.IGNORECASE):
        clocks.add(m.group(1))

    # Read STA report for clock sections
    sta_files = list((Path(proj_dir) / "output_files").glob("*.sta.rpt"))
    cross_domains = []
    if sta_files:
        txt = sta_files[0].read_text(errors="replace")
        # Look for clock transfer information
        for m in re.finditer(r"from clock[^.]+\.\.\.(\S+)[^t]+to clock[^.]+\.\.\.(\S+)",
                            txt, re.IGNORECASE):
            src_clk = m.group(1).strip('{}')
            dst_clk = m.group(2).strip('{}')
            if src_clk != dst_clk:
                cross_domains.append({"from": src_clk, "to": dst_clk})

    return j({
        "clocks_found": sorted(list(clocks)),
        "cross_domain_paths": len(cross_domains),
        "samples": cross_domains[:10],
        "recommendation": ("CDC paths need synchronization (2-FF synchronizer, "
                          "async FIFO, or handshake). Add false_path on "
                          "unsynchronized crossings.") if cross_domains else "No CDC issues detected.",
    })


# ---------------------------------------------------------------------------
# 19. IP Catalog — Complete (qmegawiz Tcl automation)
# ---------------------------------------------------------------------------

@mcp.tool()
def list_ip_catalog(
    project_path: str = "",
    device_family: str = "",
    category: str = "",
) -> str:
    """List all available IP cores with categories using qmegawiz Tcl.

    Args:
        device_family: Filter IP compatible with this family (e.g. "Cyclone IV E")
        category: Filter by category — "Basic", "DSP", "Interface", "Processor",
                  "Memory", "PLL", "Transceiver", "" for all
    """
    if not Path(QUARTUS_SH).exists():
        return j({"error": "quartus_sh not found"})

    family_flag = f"-family \"{device_family}\"" if device_family else ""
    cat_flag = f"-category \"{category}\"" if category else ""

    tcl = textwrap.dedent(f"""\
        package require ::quartus::ip
        set ips [get_available_ip_cores {family_flag} {cat_flag}]
        foreach ip $ips {{
            puts "IP:$ip"
        }}
        """)

    cwd = None
    if project_path:
        try:
            _, proj_dir, _ = resolve_project(project_path)
            cwd = proj_dir
        except ValueError:
            pass

    r = run_tcl(tcl, cwd=cwd, timeout=120)
    ip_list = re.findall(r"IP:(.+)", r["stdout"])

    return j({
        "available": len(ip_list) > 0,
        "device_family": device_family or "default",
        "category": category or "all",
        "ip_cores": ip_list[:100],
        "total": len(ip_list),
    })


@mcp.tool()
def create_ip_core(
    project_path: str,
    ip_name: str,
    output_name: str,
    parameters: str = "",
) -> str:
    """Create an IP core variant using qmegawiz Tcl automation.

    Supports any IP in the catalog. Parameters are passed as key=value pairs.

    Args:
        ip_name: IP core name, e.g. "altsyncram", "altmult_add", "altshift_taps"
        output_name: Output variant name (becomes <name>.v/.qip)
        parameters: Semicolon-separated key=value pairs,
                    e.g. "WIDTH_A=16;WIDTH_B=16;NUMWORDS_A=1024"
    """
    try:
        _, proj_dir, revision = resolve_project(project_path)
    except ValueError as e:
        return j({"error": str(e)})

    param_pairs = []
    if parameters:
        for pair in parameters.split(";"):
            if "=" in pair:
                k, v = pair.split("=", 1)
                param_pairs.append(f'set_parameter -name {k.strip()} "{v.strip()}"')

    param_block = "\n".join(param_pairs)

    tcl = textwrap.dedent(f"""\
        package require ::quartus::ip
        load_package flow
        project_open {revision}
        set ipcore [create_ip -name {ip_name} -output_name {output_name}]
        {param_block}
        generate_ip -ip $ipcore -output_directory ip/{output_name}
        qexec "quartus_map {revision} --generate_functional_sim_netlist"
        project_close
        """)

    r = run_tcl(tcl, cwd=proj_dir, timeout=300)
    ip_dir = Path(proj_dir) / "ip" / output_name

    return j({
        "success": r["success"],
        "ip_name": ip_name,
        "output_name": output_name,
        "output_dir": str(ip_dir),
        "files": [str(f.relative_to(ip_dir)) for f in ip_dir.rglob("*") if f.is_file()][:20] if ip_dir.exists() else [],
        "stdout": truncate(r["stdout"], 2000),
        "stderr": r["stderr"][-1000:],
    })


@mcp.tool()
def create_dsp_ip(
    project_path: str,
    dsp_type: str = "mult_add",
    input_width_a: int = 18,
    input_width_b: int = 18,
    num_multipliers: int = 1,
    pipeline_stages: int = 2,
    output_name: str = "dsp_ip",
) -> str:
    """Create a DSP IP core (multiplier, multiply-add, multiply-accumulate).

    Args:
        dsp_type: "mult" (multiplier), "mult_add" (multiply-add),
                  "mult_accum" (multiply-accumulate), "altmult_add", "altsqrt"
        input_width_a/b: Input data width in bits
        num_multipliers: Number of multiplier units
        pipeline_stages: Pipeline register stages for Fmax
    """
    try:
        _, proj_dir, revision = resolve_project(project_path)
    except ValueError as e:
        return j({"error": str(e)})

    ip_map = {
        "mult": ("ALTMULT_ADD", "NUMBER_OF_MULTIPLIERS", "1"),
        "mult_add": ("ALTMULT_ADD", "NUMBER_OF_MULTIPLIERS", str(num_multipliers)),
        "mult_accum": ("ALTMULT_ACCUM", "WIDTH_A", str(input_width_a)),
    }
    core_name, key_param, key_val = ip_map.get(dsp_type, ip_map["mult_add"])

    tcl = textwrap.dedent(f"""\
        package require ::quartus::ip
        load_package flow
        project_open {revision}
        set ipcore [create_ip -name {core_name} -output_name {output_name}]
        set_parameter -name {key_param} "{key_val}"
        set_parameter -name WIDTH_A {input_width_a}
        set_parameter -name WIDTH_B {input_width_b}
        set_parameter -name PIPELINE {pipeline_stages}
        generate_ip -ip $ipcore -output_directory ip/{output_name}
        project_close
        """)

    r = run_tcl(tcl, cwd=proj_dir, timeout=300)
    return j({"success": r["success"], "dsp_type": dsp_type,
              "output_name": output_name,
              "stdout": truncate(r["stdout"], 2000),
              "stderr": r["stderr"][-1000:]})


# ---------------------------------------------------------------------------
# 20. Batch Production Programming
# ---------------------------------------------------------------------------

@mcp.tool()
def batch_program_jam(
    sof_file: str,
    cable: str = "USB-Blaster",
    device_index: int = 1,
    jli_script_path: str = "",
) -> str:
    """Program devices using JAM/STAPL for batch/production programming.

    JAM (JTAG Algorithmic Methodology) enables scripting-based production
    programming without the full Quartus GUI.

    Args:
        sof_file: Path to .sof file to program
        cable: JTAG cable name (USB-Blaster, EthernetBlaster, etc.)
        device_index: Device index in JTAG chain (usually 1)
        jli_script_path: Optional custom JAM script
    """
    if not Path(QUARTUS_JLI).exists():
        return j({"error": "quartus_jli not found"})

    sof_path = Path(sof_file)
    if not sof_path.exists():
        return j({"error": f"SOF not found: {sof_file}"})

    cmd = [str(QUARTUS_JLI), "-c", cable, "-a", "PROGRAM", str(sof_path)]
    if jli_script_path:
        cmd = [str(QUARTUS_JLI), "-c", cable, jli_script_path]

    r = run_quartus(cmd, cwd=str(sof_path.parent), timeout=300)
    return j({"success": r["success"],
              "sof": str(sof_path),
              "cable": cable,
              "stdout": truncate(r["stdout"], 3000),
              "stderr": r["stderr"][-1000:]})


@mcp.tool()
def create_jam_file(
    sof_file: str,
    output_jam: str = "",
    jam_type: str = "jam",
) -> str:
    """Convert .sof to JAM/STAPL file for production batch programming.

    JAM files are text-based, can be embedded in test scripts, and
    used with quartus_jli for automated production flows.

    Args:
        sof_file: Input .sof programming file
        output_jam: Output .jam or .stp file path
        jam_type: "jam" (standard) or "stap" (advaNced)
    """
    sof_path = Path(sof_file)
    if not sof_path.exists():
        return j({"error": f"SOF not found: {sof_file}"})

    out = output_jam or str(sof_path.with_suffix(f".{jam_type}"))

    tcl = textwrap.dedent(f"""\
        package require ::quartus::jam
        create_jam_file -input_file {{{sof_file}}} -output_file {{{out}}}
        """)

    r = run_tcl(tcl, cwd=str(sof_path.parent), timeout=120)
    success = Path(out).exists()

    return j({"success": success,
              "input": str(sof_path),
              "output": out,
              "jam_type": jam_type,
              "file_size": Path(out).stat().st_size if success else 0,
              "stdout": truncate(r["stdout"], 2000),
              "stderr": r["stderr"][-1000:]})


@mcp.tool()
def program_cvp(
    project_path: str,
    cable: str = "USB-Blaster",
) -> str:
    """Program via Configuration via Protocol (CvP) — high-speed PCIe configuration.

    CvP dramatically reduces FPGA configuration time over PCIe (ms vs seconds).
    Requires a design with CvP enabled and a PCIe link.

    Args:
        project_path: Path to project
        cable: JTAG cable for initial configuration
    """
    try:
        _, proj_dir, revision = resolve_project(project_path)
    except ValueError as e:
        return j({"error": str(e)})
    if not Path(QUARTUS_CVP).exists():
        return j({"error": "quartus_cvp not found. Requires device support."})

    sof = Path(proj_dir) / "output_files" / f"{revision}.sof"
    if not sof.exists():
        return j({"error": f"No .sof found at {sof}. Compile first."})

    cmd = [str(QUARTUS_CVP), "-c", cable, str(sof)]
    r = run_quartus(cmd, cwd=str(proj_dir), timeout=120)
    return j({"success": r["success"],
              "sof": str(sof),
              "method": "CvP (Configuration via Protocol)",
              "stdout": truncate(r["stdout"], 2000),
              "stderr": r["stderr"][-1000:]})


# ---------------------------------------------------------------------------
# 21. Qsys / Platform Designer
# ---------------------------------------------------------------------------

@mcp.tool()
def create_qsys_system(
    project_path: str,
    system_name: str,
    device_family: str = "",
    clock_frequency: str = "50000000",
) -> str:
    """Create a new Platform Designer (Qsys) system with default clock.

    Platform Designer is the drag-and-drop system integration tool for
    connecting processors, peripherals, and custom IP via Avalon bus.

    Args:
        project_path: Project path
        system_name: Name for the .qsys system file
        device_family: Device family (auto-detects if empty)
        clock_frequency: Default clock frequency in Hz (e.g. "50000000" = 50MHz)
    """
    try:
        _, proj_dir, revision = resolve_project(project_path)
    except ValueError as e:
        return j({"error": str(e)})
    if not Path(QSYS_SCRIPT).exists():
        return j({"error": f"qsys-script{_EXE_SUFFIX} not found"})

    family = device_family or _detect_device_family(proj_dir, revision)
    system_path = Path(proj_dir) / f"{system_name}.qsys"
    ip_ver = _ip_version_string()

    tcl = textwrap.dedent(f"""\
        package require qsys
        set system [qsys::create_system {{proj_dir = "{str(proj_dir)}"}}]
        set_project_property DEVICE_FAMILY "{family}"
        set_project_property DEVICE {_detect_device_part(proj_dir, revision)}

        # Create default clock source
        add_instance clk_0 clock_source {ip_ver}
        set_instance_parameter_value clk_0 clockFrequency {{50000000}}
        set_instance_parameter_value clk_0 clockFrequencyKnown {{1}}
        set_instance_parameter_value clk_0 resetSynchronousEdges {{none}}

        # Add clock and reset interfaces
        add_interface clk clock sink
        set_interface_property clk EXPORT_OF clk_0.clk_in
        add_interface reset reset sink
        set_interface_property reset EXPORT_OF clk_0.clk_in_reset

        save_system {{{str(system_path)}}}
        """)

    r = _run_qsys_cmd(tcl, cwd=str(proj_dir), timeout=60)
    return j({"success": system_path.exists(),
              "system_file": str(system_path),
              "system_name": system_name,
              "device_family": family,
              "stdout": truncate(r["stdout"], 2000),
              "stderr": r["stderr"][-1000:]})


@mcp.tool()
def add_qsys_component(
    project_path: str,
    system_file: str,
    component_type: str,
    instance_name: str,
    parameters: str = "",
) -> str:
    """Add a component to an existing Qsys system.

    Args:
        project_path: Project path
        system_file: Path to .qsys system file
        component_type: IP component name to add
                       (use list_qsys_components to see available)
        instance_name: Unique instance name in the system
        parameters: Semicolon-separated key=value pairs
    """
    try:
        _, proj_dir, _ = resolve_project(project_path)
    except ValueError as e:
        return j({"error": str(e)})
    if not Path(QSYS_SCRIPT).exists():
        return j({"error": f"qsys-script{_EXE_SUFFIX} not found"})

    sys_path = Path(proj_dir) / system_file if not isabs(system_file) else Path(system_file)
    if not sys_path.exists():
        return j({"error": f"System file not found: {sys_path}"})

    param_lines = []
    if parameters:
        for pair in parameters.split(";"):
            if "=" in pair:
                k, v = pair.split("=", 1)
                param_lines.append(
                    f'set_instance_parameter_value {instance_name} {k.strip()} {{{v.strip()}}}')

    param_block = "\n".join(param_lines)
    ip_ver = _ip_version_string()

    tcl = textwrap.dedent(f"""\
        package require qsys
        load_system {{{str(sys_path)}}}
        add_instance {instance_name} {component_type} {ip_ver}
        {param_block}
        save_system {{{str(sys_path)}}}
        """)

    r = _run_qsys_cmd(tcl, cwd=str(sys_path.parent), timeout=120)
    return j({"success": r["success"],
              "component": component_type,
              "instance": instance_name,
              "system": str(sys_path),
              "stdout": truncate(r["stdout"], 2000),
              "stderr": r["stderr"][-1000:]})


@mcp.tool()
def generate_qsys(
    project_path: str,
    system_file: str,
    synthesis: str = "VERILOG",
    simulation: str = "NONE",
) -> str:
    """Generate HDL from a Qsys/Platform Designer system.

    Produces synthesizable Verilog/VHDL and optionally a simulation model.

    Args:
        project_path: Project path
        system_file: Path to .qsys system file
        synthesis: "VERILOG", "VHDL", or "NONE"
        simulation: "VERILOG", "VHDL", or "NONE"
    """
    try:
        _, proj_dir, _ = resolve_project(project_path)
    except ValueError as e:
        return j({"error": str(e)})
    if not Path(QSYS_GENERATE).exists():
        return j({"error": f"qsys-generate{_EXE_SUFFIX} not found"})

    sys_path = Path(proj_dir) / system_file if not isabs(system_file) else Path(system_file)
    if not sys_path.exists():
        return j({"error": f"System file not found: {sys_path}"})

    cmd = [str(QSYS_GENERATE), str(sys_path),
           f"--synthesis={synthesis}"]
    if simulation != "NONE":
        cmd.append(f"--simulation={simulation}")
    cmd.append("--block-symbol-file")

    r = run_quartus(cmd, cwd=str(sys_path.parent), timeout=300)
    gen_dir = sys_path.parent / sys_path.stem / "synthesis"

    return j({"success": r["success"],
              "system": str(sys_path),
              "synthesis": synthesis,
              "generated_dir": str(gen_dir),
              "files": [str(f.relative_to(gen_dir)) for f in gen_dir.rglob("*") if f.is_file()][:30] if gen_dir.exists() else [],
              "stdout": truncate(r["stdout"], 3000),
              "stderr": r["stderr"][-1000:]})


@mcp.tool()
def list_qsys_components(
    project_path: str = "",
    filter_str: str = "",
) -> str:
    """List available Platform Designer components.

    Returns all available IP components that can be added to a Qsys system.

    Args:
        project_path: Project path (optional, used for search paths)
        filter_str: Filter by name (case-insensitive substring match)
    """
    cwd = None
    if project_path:
        try:
            _, proj_dir, _ = resolve_project(project_path)
            cwd = proj_dir
        except ValueError:
            pass

    tcl = """
        package require qsys
        set components [get_available_components]
        foreach comp $components {
            puts "COMP:$comp"
        }
    """
    r = _run_qsys_cmd(tcl, cwd=cwd, timeout=120)
    comps = re.findall(r"COMP:(.+)", r["stdout"])
    if filter_str:
        comps = [c for c in comps if filter_str.lower() in c.lower()]

    return j({"total": len(comps),
              "filter": filter_str or "none",
              "components": comps[:100]})


@mcp.tool()
def connect_qsys_components(
    project_path: str,
    system_file: str,
    master_instance: str,
    master_role: str = "data_master",
    slave_instance: str = "",
    slave_role: str = "s1",
    clock_bridge: bool = False,
    reset_bridge: bool = False,
) -> str:
    """Connect two components in a Qsys system via Avalon bus.

    Args:
        project_path: Project path
        system_file: Path to .qsys system file
        master_instance: Master component instance name
        master_role: Master interface role (default: "data_master")
        slave_instance: Slave component instance (if empty, connects all slaves)
        slave_role: Slave interface role (default: "s1")
        clock_bridge: Auto-insert clock-crossing bridge if needed
        reset_bridge: Auto-insert reset bridge if needed
    """
    try:
        _, proj_dir, _ = resolve_project(project_path)
    except ValueError as e:
        return j({"error": str(e)})

    sys_path = Path(proj_dir) / system_file if not isabs(system_file) else Path(system_file)
    if not sys_path.exists():
        return j({"error": f"System file not found: {sys_path}"})

    tcl = textwrap.dedent(f"""\
        package require qsys
        load_system {{{str(sys_path)}}}
        add_connection {master_instance}.{master_role} {slave_instance}.{slave_role}
        {"" if not clock_bridge else "set_connection_parameter_value " + master_instance + "." + master_role + "/" + slave_instance + "." + slave_role + " clockCrossingAdapter AUTO"}
        {"" if not reset_bridge else "set_connection_parameter_value " + master_instance + "." + master_role + "/" + slave_instance + "." + slave_role + " autoExportResetBridge 1"}
        save_system {{{str(sys_path)}}}
        """)

    r = _run_qsys_cmd(tcl, cwd=str(sys_path.parent), timeout=60)
    return j({"success": r["success"],
              "connection": f"{master_instance}.{master_role} -> {slave_instance}.{slave_role}",
              "clock_bridge": clock_bridge,
              "stdout": truncate(r["stdout"], 2000),
              "stderr": r["stderr"][-1000:]})


# ---------------------------------------------------------------------------
# 22. Nios II Soft Processor
# ---------------------------------------------------------------------------

@mcp.tool()
def compile_nios2_bsp(
    bsp_dir: str,
    cpu_name: str = "cpu",
    nios2_eds: str = "",
) -> str:
    """Compile a Nios II BSP (Board Support Package).

    Creates/updates the BSP from a .sopcinfo file generated by Qsys.
    Required before compiling Nios II software.

    Args:
        bsp_dir: Path to BSP directory (creates if not exists)
        cpu_name: CPU instance name in the Qsys system
        nios2_eds: Path to Nios II EDS (auto-discovered if empty)
    """
    if not nios2_eds:
        nios2_eds = _discover_nios2_eds()
    if not nios2_eds:
        return j({"error": "Nios II EDS not found. Install it with Quartus."})

    sopcinfo_files = list(Path(bsp_dir).parent.rglob("*.sopcinfo"))
    if not sopcinfo_files:
        return j({"error": f"No .sopcinfo found in {Path(bsp_dir).parent} or subdirectories"})

    bsp_exe = Path(nios2_eds) / "sdk2" / "bin" / f"nios2-bsp-create-settings{_EXE_SUFFIX}"
    if not bsp_exe.exists():
        # Try alternative
        bsp_exe = Path(nios2_eds) / "bin" / "nios2-bsp"
        if not bsp_exe.exists():
            return j({"error": f"nios2-bsp not found in {nios2_eds}"})

    sopcinfo = str(sopcinfo_files[0])
    bsp_path = Path(bsp_dir)
    bsp_path.mkdir(parents=True, exist_ok=True)

    r = subprocess.run(
        [str(bsp_exe), "create-settings", "--sopc", sopcinfo,
         "--bsp-dir", str(bsp_path), "--cpu-name", cpu_name],
        cwd=str(bsp_path), capture_output=True, text=True, timeout=120
    )
    return j({"success": r.returncode == 0,
              "bsp_dir": str(bsp_path),
              "cpu_name": cpu_name,
              "sopcinfo": sopcinfo,
              "stdout": truncate(r.stdout, 2000),
              "stderr": r.stderr[-1000:]})


@mcp.tool()
def convert_nios2_files(
    elf_file: str = "",
    sof_file: str = "",
    output_type: str = "hex",
    output_file: str = "",
    nios2_eds: str = "",
) -> str:
    """Convert Nios II files between formats.

    Supports:
      elf2hex  — .elf to Intel HEX for boot copier
      elf2flash — .elf to .flash for CFI/EPCS
      sof2flash — .sof to .flash for FPGA config + software
      bin2flash — .bin to .flash

    Args:
        elf_file: Input .elf (for elf2hex/elf2flash)
        sof_file: Input .sof (for sof2flash)
        output_type: "hex", "flash", or "sof2flash"
        output_file: Output file path (auto-generates if empty)
        nios2_eds: Nios II EDS path (auto-discovered if empty)
    """
    if not nios2_eds:
        nios2_eds = _discover_nios2_eds()
    if not nios2_eds:
        return j({"error": "Nios II EDS not found"})

    bin_dir = Path(nios2_eds) / "bin"
    tools = {
        "hex": str(bin_dir / f"elf2hex{_EXE_SUFFIX}") if (bin_dir / f"elf2hex{_EXE_SUFFIX}").exists() else None,
        "flash": str(bin_dir / f"elf2flash{_EXE_SUFFIX}") if (bin_dir / f"elf2flash{_EXE_SUFFIX}").exists() else None,
        "sof2flash": str(bin_dir / f"sof2flash{_EXE_SUFFIX}") if (bin_dir / f"sof2flash{_EXE_SUFFIX}").exists() else None,
    }

    if output_type == "hex":
        if not tools["hex"]:
            return j({"error": "elf2hex not found"})
        if not elf_file:
            return j({"error": "elf_file required for hex conversion"})
        out = output_file or str(Path(elf_file).with_suffix(".hex"))
        r = subprocess.run([tools["hex"], elf_file, out],
                           capture_output=True, text=True, timeout=60)
        success = Path(out).exists()
        return j({"success": success, "output": out,
                  "stdout": r.stdout[:2000], "stderr": r.stderr[-500:]})

    elif output_type == "flash":
        if not tools["flash"]:
            return j({"error": "elf2flash not found"})
        if not elf_file:
            return j({"error": "elf_file required"})
        out = output_file or str(Path(elf_file).with_suffix(".flash"))
        r = subprocess.run([tools["flash"], "--input=" + elf_file,
                            "--output=" + out, "--epcs"],
                           capture_output=True, text=True, timeout=60)
        success = Path(out).exists()
        return j({"success": success, "output": out,
                  "stdout": r.stdout[:2000], "stderr": r.stderr[-500:]})

    elif output_type == "sof2flash":
        if not tools["sof2flash"]:
            return j({"error": "sof2flash not found"})
        if not sof_file:
            return j({"error": "sof_file required"})
        out = output_file or str(Path(sof_file).with_suffix(".flash"))
        r = subprocess.run([tools["sof2flash"], "--input=" + sof_file,
                            "--output=" + out, "--epcs"],
                           capture_output=True, text=True, timeout=60)
        success = Path(out).exists()
        return j({"success": success, "output": out,
                  "stdout": r.stdout[:2000], "stderr": r.stderr[-500:]})

    return j({"error": f"Unknown output_type: {output_type}",
              "supported": ["hex", "flash", "sof2flash"]})


# ---------------------------------------------------------------------------
# 23. Project Utility Tools
# ---------------------------------------------------------------------------

@mcp.tool()
def clean_project(
    project_path: str,
    clean_type: str = "full",
) -> str:
    """Clean a Quartus project — remove generated files to save disk space.

    Quartus projects can accumulate GB of intermediate files. This tool
    provides selective cleaning without removing source files.

    Args:
        project_path: Project path
        clean_type: "full" (all generated), "db" (database only),
                    "reports" (keep database, remove reports),
                    "simulation" (remove sim outputs)
    """
    try:
        _, proj_dir, revision = resolve_project(project_path)
    except ValueError as e:
        return j({"error": str(e)})

    if clean_type == "full":
        tcl = textwrap.dedent(f"""\
            project_open {revision}
            project_clean
            project_close
            """)
        r = run_tcl(tcl, cwd=proj_dir, timeout=120)
        return j({"success": r["success"], "clean_type": "full",
                  "stdout": truncate(r["stdout"], 2000)})

    patterns = {
        "db": ["db/", "incremental_db/"],
        "reports": ["output_files/*.rpt", "output_files/*.summary"],
        "simulation": ["simulation/", "*.wlf", "work/"]
    }
    removed = []
    p = Path(proj_dir)
    for pat in patterns.get(clean_type, []):
        for match in p.glob(pat):
            try:
                if match.is_dir():
                    shutil.rmtree(match)
                    removed.append(str(match.relative_to(p)) + "/")
                else:
                    match.unlink()
                    removed.append(str(match.relative_to(p)))
            except Exception as e:
                removed.append(f"SKIP: {match.relative_to(p)} ({e})")

    return j({"success": True, "clean_type": clean_type,
              "removed_count": len(removed),
              "removed": removed[:30]})


@mcp.tool()
def upgrade_ip(
    project_path: str,
) -> str:
    """Upgrade all IP cores in a project to the current Quartus version.

    When moving a project to a newer Quartus version, IP cores need
    regeneration. This automates the upgrade.

    Args:
        project_path: Project path
    """
    try:
        _, proj_dir, revision = resolve_project(project_path)
    except ValueError as e:
        return j({"error": str(e)})

    r = run_quartus(
        [str(QUARTUS_SH), "--ip_upgrade", str(proj_dir / revision)],
        cwd=str(proj_dir), timeout=300
    )
    return j({"success": r["success"],
              "stdout": truncate(r["stdout"], 3000),
              "stderr": r["stderr"][-1000:]})


@mcp.tool()
def generate_flow_template(
    output_path: str,
    flow_type: str = "compile",
) -> str:
    """Generate a Tcl flow template script for custom build automation.

    Args:
        output_path: Where to save the .tcl template
        flow_type: "compile", "simulation", or "programming"
    """
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    templates = {
        "compile": '''# Compilation Flow Template (generated by QuartusMCP)
project_open {revision}
load_package flow
execute_flow -compile
project_close''',
        "simulation": '''# Simulation Flow Template
project_open {revision}
load_package flow
execute_flow -simulate
project_close''',
        "programming": '''# Programming Flow Template
project_open {revision}
load_package flow
execute_flow -program
project_close''',
    }

    content = templates.get(flow_type, templates["compile"])
    out.write_text(content)
    return j({"success": True, "flow_type": flow_type, "template": str(out),
              "content": content[:300]})


# ---------------------------------------------------------------------------
# 24. CDB Design Database Operations
# ---------------------------------------------------------------------------

@mcp.tool()
def export_design_partition(
    project_path: str,
    partition_name: str,
    output_qxp: str = "",
    post_fit: bool = False,
) -> str:
    """Export a design partition as .qxp for reuse or incremental compilation.

    Design partitions enable team-based design: each team works on a
    partition, exports it as .qxp, and the top-level integrates them.

    Args:
        project_path: Project path
        partition_name: Name of the design partition to export
        output_qxp: Output .qxp path (auto-generates if empty)
        post_fit: Export post-fit (with placement) vs post-synth only
    """
    try:
        _, proj_dir, revision = resolve_project(project_path)
    except ValueError as e:
        return j({"error": str(e)})
    if not Path(QUARTUS_CDB).exists():
        return j({"error": "quartus_cdb not found"})

    out = output_qxp or str(Path(proj_dir) / f"{partition_name}.qxp")
    cmd = [str(QUARTUS_CDB), str(proj_dir / revision),
           f"--incremental_compilation_export={out}",
           f"--incremental_compilation_export_partition_name={partition_name}"]
    if post_fit:
        cmd.append("--incremental_compilation_export_post_fit=on")
    else:
        cmd.append("--incremental_compilation_export_post_synth=on")

    r = run_quartus(cmd, cwd=str(proj_dir), timeout=300)
    success = Path(out).exists()
    return j({"success": success,
              "partition": partition_name,
              "qxp_file": out,
              "post_fit": post_fit,
              "file_size": Path(out).stat().st_size if success else 0,
              "stdout": truncate(r["stdout"], 2000),
              "stderr": r["stderr"][-1000:]})


@mcp.tool()
def import_design_partition(
    project_path: str,
    qxp_file: str,
    partition_name: str = "",
) -> str:
    """Import a design partition from .qxp into the project.

    Args:
        project_path: Project path
        qxp_file: Path to .qxp file to import
        partition_name: Partition name (auto-detected from filename if empty)
    """
    try:
        _, proj_dir, revision = resolve_project(project_path)
    except ValueError as e:
        return j({"error": str(e)})
    if not Path(QUARTUS_CDB).exists():
        return j({"error": "quartus_cdb not found"})

    if not partition_name:
        partition_name = Path(qxp_file).stem

    cmd = [str(QUARTUS_CDB), str(proj_dir / revision),
           "--import_design",
           f"--import_partition={qxp_file}"]

    r = run_quartus(cmd, cwd=str(proj_dir), timeout=300)
    return j({"success": r["success"],
              "qxp": qxp_file,
              "partition_name": partition_name,
              "stdout": truncate(r["stdout"], 2000),
              "stderr": r["stderr"][-1000:]})


@mcp.tool()
def export_design_database(
    project_path: str,
    output_dir: str = "",
) -> str:
    """Export the entire compiled design database for archiving or transfer.

    The exported database can be imported into another Quartus instance
    for verification or further processing.

    Args:
        project_path: Project path
        output_dir: Export directory (auto-generates if empty)
    """
    try:
        _, proj_dir, revision = resolve_project(project_path)
    except ValueError as e:
        return j({"error": str(e)})
    if not Path(QUARTUS_CDB).exists():
        return j({"error": "quartus_cdb not found"})

    out = output_dir or str(Path(proj_dir) / f"{revision}_db_export")
    cmd = [str(QUARTUS_CDB), str(proj_dir / revision),
           f"--export_database={out}"]

    r = run_quartus(cmd, cwd=str(proj_dir), timeout=300)
    out_path = Path(out)
    file_count = sum(1 for _ in out_path.rglob("*")) if out_path.exists() else 0
    return j({"success": out_path.exists(),
              "export_dir": out,
              "files_count": file_count,
              "stdout": truncate(r["stdout"], 2000),
              "stderr": r["stderr"][-1000:]})


@mcp.tool()
def back_annotate(
    project_path: str,
    annotation_type: str = "demotion",
) -> str:
    """Back-annotate results into the project (pin, cell, or routing info).

    After compilation, back-annotation stores the results in the QSF so
    they can be locked down for future compilations.

    Args:
        project_path: Project path
        annotation_type: "demotion" (cell degradation for ECO),
                         "pin" (lock pin placement),
                         "routing" (lock routing)
    """
    try:
        _, proj_dir, revision = resolve_project(project_path)
    except ValueError as e:
        return j({"error": str(e)})
    if not Path(QUARTUS_CDB).exists():
        return j({"error": "quartus_cdb not found"})

    tcl = textwrap.dedent(f"""\
        project_open {revision}
        qexec "quartus_cdb {revision} --back_annotate={annotation_type}"
        project_close
        """)

    r = run_tcl(tcl, cwd=proj_dir, timeout=120)
    return j({"success": r["success"],
              "annotation_type": annotation_type,
              "stdout": truncate(r["stdout"], 2000),
              "stderr": r["stderr"][-1000:]})


# ---------------------------------------------------------------------------
# Helper functions for Qsys/Nios
# ---------------------------------------------------------------------------

def _run_qsys_cmd(tcl_code: str, cwd=None, timeout=60) -> dict:
    """Run Tcl code through qsys-script."""
    work_dir = cwd or DEFAULT_PROJECT_DIR
    os.makedirs(work_dir, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".tcl", delete=False, dir=work_dir
    ) as tf:
        tf.write(tcl_code)
        tmp = tf.name
    try:
        r = run_quartus(
            [str(QSYS_SCRIPT), f"--script={tmp}"],
            cwd=cwd, timeout=timeout
        )
        return r
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def _detect_device_family(proj_dir, revision):
    """Try to detect device family from QSF."""
    qsf_path = find_qsf(proj_dir, revision)
    if qsf_path:
        txt = Path(qsf_path).read_text(errors="replace")
        m = re.search(r'set_global_assignment.*?FAMILY\s+(\S+)', txt)
        if m:
            return m.group(1).strip('"')
    return "Cyclone IV E"


def _detect_device_part(proj_dir, revision):
    qsf_path = find_qsf(proj_dir, revision)
    if qsf_path:
        txt = Path(qsf_path).read_text(errors="replace")
        m = re.search(r'set_global_assignment.*?DEVICE\s+(\S+)', txt)
        if m:
            return m.group(1).strip('"')
    return ""


def _discover_nios2_eds():
    """Auto-discover Nios II EDS installation, preferring the version matching Quartus."""
    major, minor = _parse_quartus_version()
    ver_str = f"{major}.{minor}" if major > 0 else ""

    for drive in ("E:/", "C:/", "D:/", "F:/"):
        for pattern in [f"{drive}intelFPGA_lite/*/nios2eds",
                        f"{drive}intelFPGA/*/nios2eds"]:
            matches = glob.glob(pattern)
            if matches:
                # Prefer the version matching the active Quartus version
                if ver_str:
                    for m in sorted(matches, reverse=True):
                        if ver_str in m:
                            return m
                return sorted(matches, reverse=True)[0]
    return None


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    log.info("Quartus MCP Server starting")
    log.info("Python: %s", sys.version.split()[0])
    log.info("Quartus bin: %s", QUARTUS_BIN)
    log.info("quartus_sh exists: %s", Path(QUARTUS_SH).exists())
    log.info("Default project dir: %s", DEFAULT_PROJECT_DIR)
    mcp.run()
