import ast
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import quartus_mcp_server as qms


ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "quartus_mcp_server.py"
README = ROOT / "README.md"
SETTINGS = ROOT / ".claude" / "settings.json"
SKILLS_DIR = ROOT / ".claude" / "skills"
AGENTS_DIR = ROOT / ".claude" / "agents"

EXPECTED_SKILLS = {
    "fpga-board-programming",
    "intel-ip-cosimulation",
    "modelsim-rtl-simulation",
    "quartus-compile-debug",
    "quartus-constraints-board",
    "quartus-flow-signoff",
    "quartus-mcp-maintainer",
    "quartus-project-bringup",
    "quartus-qor-power-review",
    "quartus-timing-closure",
}

EXPECTED_AGENTS = {
    "fpga-ip-cosim-engineer",
    "fpga-rtl-engineer",
    "fpga-signoff-reviewer",
    "fpga-spec-architect",
    "fpga-verification-engineer",
    "quartus-integration-engineer",
    "quartus-mcp-maintainer",
}


def decorated_tool_names() -> list[str]:
    tree = ast.parse(SERVER.read_text(encoding="utf-8"))
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


def readme_tool_names() -> list[str]:
    names: list[str] = []
    for line in README.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^\|\s*`([A-Za-z_][A-Za-z0-9_]*)`\s*\|", line)
        if match:
            names.append(match.group(1))
    return sorted(names)


class ToolInventoryTests(unittest.TestCase):
    def test_source_exposes_43_tools(self) -> None:
        self.assertEqual(len(decorated_tool_names()), 43)

    def test_readme_matches_source_tools(self) -> None:
        self.assertEqual(readme_tool_names(), decorated_tool_names())
        self.assertIn("Available Tools (43)", README.read_text(encoding="utf-8"))

    def test_local_claude_settings_points_to_this_workspace(self) -> None:
        settings = json.loads(SETTINGS.read_text(encoding="utf-8"))
        server = settings["mcpServers"]["quartus"]
        self.assertEqual(server["command"], "python")
        self.assertIn("quartus_mcp_server.py", server["args"][0])
        self.assertIn("QUARTUS_ROOTDIR", server["env"])
        self.assertIn("QUARTUS_MCP_PROJECT_DIR", server["env"])
        self.assertNotIn("C:\\Users\\", json.dumps(settings))

    def test_project_skills_reference_existing_mcp_tools(self) -> None:
        actual_skills = {path.name for path in SKILLS_DIR.iterdir() if path.is_dir()}
        self.assertEqual(actual_skills, EXPECTED_SKILLS)

        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "validate_claude_skills.py")],
            cwd=str(ROOT),
            text=True,
            capture_output=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        data = json.loads(result.stdout)
        self.assertTrue(data["ok"])
        self.assertEqual(data["skill_count"], len(EXPECTED_SKILLS))
        self.assertEqual(data["tool_count"], 43)

    def test_project_agents_bind_existing_skills(self) -> None:
        actual_agents = {path.stem for path in AGENTS_DIR.glob("*.md")}
        self.assertEqual(actual_agents, EXPECTED_AGENTS)

        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "validate_claude_agents.py")],
            cwd=str(ROOT),
            text=True,
            capture_output=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        data = json.loads(result.stdout)
        self.assertTrue(data["ok"])
        self.assertEqual(data["agent_count"], len(EXPECTED_AGENTS))
        self.assertEqual(data["skill_count"], len(EXPECTED_SKILLS))


class QuartusDiscoveryTests(unittest.TestCase):
    def test_env_root_is_preferred(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            install_root = Path(tmp) / "23.1std"
            bin_dir = install_root / "quartus" / "bin64"
            bin_dir.mkdir(parents=True)
            for exe_name in qms.EXECUTABLE_NAMES.values():
                (bin_dir / exe_name).write_text("", encoding="utf-8")

            with patch.dict(os.environ, {"QUARTUS_MCP_ROOT": str(install_root)}, clear=False):
                discovered = qms.discover_quartus()

            self.assertTrue(discovered["available"])
            self.assertEqual(Path(discovered["bin_dir"]), bin_dir)
            self.assertEqual(discovered["quartus_sh"], str(bin_dir / "quartus_sh.exe"))

    def test_modelsim_env_bin_is_preferred(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp) / "win64"
            bin_dir.mkdir(parents=True)
            for exe_name in qms.MODELSIM_EXECUTABLE_NAMES.values():
                (bin_dir / exe_name).write_text("", encoding="utf-8")

            with patch.dict(os.environ, {"QUARTUS_MCP_MODELSIM_BIN": str(bin_dir)}, clear=False):
                discovered = qms.discover_modelsim()

            self.assertTrue(discovered["available"])
            self.assertEqual(Path(discovered["bin_dir"]), bin_dir)
            self.assertEqual(discovered["tools"]["vsim"], str(bin_dir / "vsim.exe"))

    def test_missing_executable_returns_structured_error(self) -> None:
        missing = str(Path(tempfile.gettempdir()) / "definitely_missing_quartus.exe")
        result = qms.run_quartus([missing, "--version"], timeout=1)
        self.assertFalse(result["success"])
        self.assertEqual(result["returncode"], -1)
        self.assertIn("Executable not found", result["stderr"])


class ProjectParsingTests(unittest.TestCase):
    def test_resolve_project_reads_revision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            qpf = root / "demo.qpf"
            qpf.write_text('PROJECT_REVISION = "rev_a"\n', encoding="utf-8")
            (root / "rev_a.qsf").write_text(
                'set_global_assignment -name FAMILY "Cyclone IV E"\n',
                encoding="utf-8",
            )

            resolved_qpf, project_dir, revision = qms.resolve_project(str(root))

            self.assertEqual(Path(resolved_qpf), qpf)
            self.assertEqual(Path(project_dir), root)
            self.assertEqual(revision, "rev_a")

    def test_quartus_prime_flow_status_success_is_recognized(self) -> None:
        text = "; Flow Status                     ; Successful - Fri Jul  3 15:37:51 2026          ;"
        self.assertTrue(qms._flow_report_successful(text))


if __name__ == "__main__":
    unittest.main()
