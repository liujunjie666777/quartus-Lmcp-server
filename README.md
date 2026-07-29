# Quartus MCP Server / Quartus MCP 服务

English and Chinese are provided side by side throughout this README.

README 全文已按中英双语并列说明。

A [Model Context Protocol (MCP)](https://modelcontextprotocol.io) server for automating a local Intel/Altera Quartus installation, ModelSim simulation flow, timing analysis, reports, and JTAG programming from Claude Code.

本项目是一个面向 Intel/Altera Quartus 的 MCP 服务，可让 Claude Code 调用本机 Quartus/ModelSim 命令行工具，完成工程创建、HDL 文件管理、编译、仿真、时序分析、报告读取和 JTAG 下载等流程。

This fork modernizes the original Quartus II 13.1-oriented workflow for a Quartus Prime Lite 23.1std environment while keeping discovery configurable through environment variables.

本版本将原本偏 Quartus II 13.1 的流程适配到 Quartus Prime Lite 23.1std 一类的新环境，并通过环境变量做工具链自动发现，避免把个人机器路径写死在代码里。

## Features

- Create, open, inspect, archive, and list Quartus projects.
- Add/remove HDL, SDC, MIF, and simulation files.
- Set global assignments, pin assignments, clocks, and device metadata.
- Run map, fit, assembler, full compile, STA, DRC, and power analysis.
- Read flow, map, fit, asm, STA, power, and QSF reports.
- Create Verilog testbenches and run ModelSim batch simulations.
- Compile Intel Quartus simulation libraries for LPM, megafunction, IP, and family atom co-simulation.
- Detect JTAG cables/devices and program generated SOF files.
- Provide project-level Claude Code skills and agents for FPGA workflows.

## 功能概览

- 创建、打开、查看、归档和枚举 Quartus 工程。
- 添加或移除 HDL、SDC、MIF 和仿真文件。
- 设置全局约束、管脚约束、时钟和器件信息。
- 运行 map、fit、assembler、完整编译、STA、DRC 和功耗分析。
- 读取 flow、map、fit、asm、STA、power 和 QSF 报告。
- 自动生成 Verilog testbench，并调用 ModelSim 批处理仿真。
- 编译 Quartus 仿真库，支持 LPM、megafunction、Intel IP 和器件 atom 联合仿真。
- 检测 JTAG 线缆/器件，并下载生成的 SOF。
- 提供 Claude Code 项目级 FPGA skills 和 agents。

## Requirements

| Requirement | Version / Notes |
| --- | --- |
| Quartus command-line tools | Tested with Quartus Prime Lite 23.1std.0 Build 991 |
| ModelSim command-line tools | Tested with ModelSim SE-64 2020.4 |
| Python | 3.10 or later |
| MCP Python package | `mcp>=1.0.0` |
| Claude Code | Current release |

## 环境要求

| 依赖 | 版本 / 说明 |
| --- | --- |
| Quartus 命令行工具 | 已用 Quartus Prime Lite 23.1std.0 Build 991 测试 |
| ModelSim 命令行工具 | 已用 ModelSim SE-64 2020.4 测试 |
| Python | 3.10 或更高版本 |
| MCP Python 包 | `mcp>=1.0.0` |
| Claude Code | 当前版本 |

## Installation

```powershell
git clone https://github.com/<your-name>/quartus_mcp_server.git
cd quartus_mcp_server
python -m pip install -r requirements.txt
```

## 安装

```powershell
git clone https://github.com/<your-name>/quartus_mcp_server.git
cd quartus_mcp_server
python -m pip install -r requirements.txt
```

## Tool Discovery

The server resolves Quartus in this order:

1. `QUARTUS_MCP_ROOT`
2. `QUARTUS_ROOTDIR`
3. `QUARTUS_ROOTDIR_OVERRIDE`
4. `QUARTUS_BIN`
5. Common Windows/WSL install bases:
   `C:\intelFPGA_lite`–`E:\intelFPGA_lite`, `C:\intelFPGA`–`E:\intelFPGA`, `C:\altera`–`E:\altera`,
   `/mnt/c/intelFPGA_lite`–`/mnt/e/intelFPGA_lite`, `/mnt/c/intelFPGA`–`/mnt/e/intelFPGA`, `/mnt/c/altera`–`/mnt/e/altera`

The server resolves ModelSim in this order:

1. `QUARTUS_MCP_MODELSIM_BIN`
2. `MODELSIM_BIN`
3. Common Windows ModelSim directories such as `C:\modeltech*\win64` and `win32aloem`
4. Intel bundled `modelsim_ase` directories across all drives (`C:`–`E:`)

Use `get_quartus_installation` to confirm the active binary paths and tool availability.

## 工具链发现

Quartus 的查找顺序如下：

1. `QUARTUS_MCP_ROOT`
2. `QUARTUS_ROOTDIR`
3. `QUARTUS_ROOTDIR_OVERRIDE`
4. `QUARTUS_BIN`
5. 常见 Windows/WSL 安装目录：
   `C:\intelFPGA_lite`–`E:\intelFPGA_lite`、`C:\intelFPGA`–`E:\intelFPGA`、`C:\altera`–`E:\altera`、
   `/mnt/c/intelFPGA_lite`–`/mnt/e/intelFPGA_lite`、`/mnt/c/intelFPGA`–`/mnt/e/intelFPGA`、`/mnt/c/altera`–`/mnt/e/altera`

ModelSim 的查找顺序如下：

1. `QUARTUS_MCP_MODELSIM_BIN`
2. `MODELSIM_BIN`
3. 常见 Windows ModelSim 目录，例如 `C:\modeltech*\win64` 和 `win32aloem`
4. 所有驱动器上 Intel 自带的 `modelsim_ase` 目录（`C:`–`E:`）

可以调用 `get_quartus_installation` 确认当前使用的可执行文件路径和可用状态。

## MCP Configuration / MCP 配置

### WorkBuddy (Claude Code)

Edit `.mcp.json` in your project or home directory:

编辑项目或用户目录下的 `.mcp.json`：

```json
{
  "mcpServers": {
    "quartus-Lmcp": {
      "command": "python",
      "args": ["quartus_mcp_server.py"],
      "cwd": "/path/to/quartus-Lmcp-server",
      "env": {
        "QUARTUS_ROOTDIR": "/path/to/intelFPGA_lite/quartus",
        "QUARTUS_MCP_MODELSIM_BIN": "/path/to/modelsim_ase/win32aloem",
        "QUARTUS_MCP_PROJECT_DIR": "/path/to/quartus_projects"
      }
    }
  }
}
```

### QClaw

```bash
openclaw mcp add quartus-Lmcp \
  --command python \
  --arg /path/to/quartus_mcp_server.py \
  --cwd /path/to/quartus-Lmcp-server \
  --env QUARTUS_ROOTDIR="/path/to/intelFPGA_lite/quartus" \
  --env QUARTUS_MCP_MODELSIM_BIN="/path/to/modelsim_ase/win32aloem" \
  --env QUARTUS_MCP_PROJECT_DIR="/path/to/quartus_projects"
```

Or edit `openclaw.json` directly / 或直接编辑 `openclaw.json`：

```json5
{
  mcp: {
    servers: {
      "quartus-Lmcp": {
        command: "python",
        args: ["quartus_mcp_server.py"],
        cwd: "/path/to/quartus-Lmcp-server",
        env: {
          QUARTUS_ROOTDIR: "/path/to/intelFPGA_lite/quartus",
          QUARTUS_MCP_MODELSIM_BIN: "/path/to/modelsim_ase/win32aloem",
          QUARTUS_MCP_PROJECT_DIR: "/path/to/quartus_projects"
        }
      }
    }
  }
}
```

### Codex (OpenAI Codex CLI)

```json
{
  "mcpServers": {
    "quartus-Lmcp": {
      "type": "stdio",
      "command": "python",
      "args": ["quartus_mcp_server.py"],
      "cwd": "/path/to/quartus-Lmcp-server",
      "env": {
        "QUARTUS_ROOTDIR": "/path/to/intelFPGA_lite/quartus",
        "QUARTUS_MCP_MODELSIM_BIN": "/path/to/modelsim_ase/win32aloem",
        "QUARTUS_MCP_PROJECT_DIR": "/path/to/quartus_projects"
      }
    }
  }
}
```

Restart the client after changing the configuration file.
修改配置后请重启对应的客户端。


## Verify Locally

Run the local checks:

```powershell
python scripts\validate_claude_skills.py
python scripts\validate_claude_agents.py
python -m unittest discover -s tests
python scripts\smoke_test_tools.py
```

The smoke test calls every MCP tool function directly, including ModelSim simulation. Hardware-dependent programmer calls may return a structured no-cable/no-device result when no FPGA board is connected; that still verifies the MCP tool does not crash and that the Quartus command is callable.

## 本地验证

运行以下检查：

```powershell
python scripts\validate_claude_skills.py
python scripts\validate_claude_agents.py
python -m unittest discover -s tests
python scripts\smoke_test_tools.py
```

`smoke_test_tools.py` 会直接调用所有 MCP tool 函数，包括 ModelSim 仿真。依赖硬件的下载器相关工具在没有开发板时可能返回结构化的 no-cable/no-device 结果；这仍然能验证 MCP tool 本身不会崩溃，并且 Quartus 命令可被调用。

## Project Skills / 项目技能

This repository includes a project-level WorkBuddy (Claude Code) skill suite under `.workbuddy/skills`. The skills sit above the MCP tools: they decide which Quartus/ModelSim tools to call, what evidence to read, and when to hand off to a narrower workflow.

本仓库在 `.workbuddy/skills` 下提供项目级 WorkBuddy 技能。这些 skills 位于 MCP tools 之上，用来决定调用哪些 Quartus/ModelSim 工具、读取哪些证据，以及什么时候切换到更具体的 FPGA 工作流。

| Skill | Purpose / 用途 |
| --- | --- |
| `quartus-project-bringup` | Create/import projects, set top entity, register HDL/SDC, and run the first compile sanity check / 创建或导入工程，设置顶层实体，注册 HDL/SDC，并运行首次编译检查 |
| `quartus-constraints-board` | Manage QSF/SDC, pins, I/O standards, clock constraints, and board setup / 管理 QSF/SDC、管脚、I/O 标准、时钟约束和板级配置 |
| `quartus-compile-debug` | Triage map/fit/assembler/DRC failures from reports and messages / 根据报告和消息定位 map/fit/assembler/DRC 问题 |
| `modelsim-rtl-simulation` | Create/register testbenches and run ModelSim RTL simulation / 创建或注册 testbench，并运行 ModelSim RTL 仿真 |
| `intel-ip-cosimulation` | Run ModelSim with Quartus sim libraries, IP setup scripts, LPM, and family atoms / 使用 Quartus 仿真库、IP 脚本、LPM 和器件 atom 运行联合仿真 |
| `quartus-timing-closure` | Drive TimeQuest timing closure with clock/path/report evidence / 基于时钟、路径和报告证据推进时序收敛 |
| `quartus-qor-power-review` | Summarize resources, DRC, flow status, warnings, and power reports / 汇总资源、DRC、流程状态、警告和功耗报告 |
| `fpga-board-programming` | Detect JTAG cables/devices, verify SOF output, and program the board / 检测 JTAG 线缆和器件，确认 SOF 并下载到开发板 |
| `quartus-flow-signoff` | Run compile, simulation, timing, DRC, power, and archive gates / 运行编译、仿真、时序、DRC、功耗和归档验收 |
| `quartus-mcp-maintainer` | Keep MCP tools, README, tests, smoke checks, and skill references synchronized / 维护 MCP tools、README、测试、smoke 检查和 skill 引用一致性 |

## Project Agents / 项目代理

This repository also includes project-level WorkBuddy (Claude Code) subagents under `.workbuddy/agents`.

本仓库还在 `.workbuddy/agents` 下提供项目级 WorkBuddy 子代理。

| Agent | Owns / 职责 |
| --- | --- |
| `fpga-spec-architect` | Requirements, interfaces, reset/clock conventions, latency, verification plan, and handoff contract / 需求、接口、复位/时钟约定、延迟、验证计划和交接约定 |
| `fpga-rtl-engineer` | Synthesizable RTL implementation, top-level consistency, source registration, and compile checks / 可综合 RTL、顶层一致性、源文件注册和编译检查 |
| `fpga-verification-engineer` | Testbench code, stimulus/checkers, pass/fail markers, ModelSim logs, and simulation artifacts / testbench、激励/检查器、通过/失败标记、ModelSim 日志和仿真产物 |
| `fpga-ip-cosim-engineer` | Intel IP/LPM/megafunction/device-atom simulation setup and debug / Intel IP、LPM、megafunction 和器件 atom 仿真配置与调试 |
| `quartus-integration-engineer` | Project coherence across QSF/SDC, source files, map/fit/asm/STA, and reports / 维护 QSF/SDC、源文件、map/fit/asm/STA 和报告的一致性 |
| `fpga-signoff-reviewer` | Final compile/sim/timing/DRC/QoR/power/archive and board readiness / 最终编译、仿真、时序、DRC、QoR、功耗、归档和开发板下载准备 |
| `quartus-mcp-maintainer` | MCP server, README, tests, smoke checks, skills, agents, and discovery behavior / MCP 服务、README、测试、smoke、skills、agents 和工具发现行为 |

## Available Tools (87)

### Installation

| Tool | Description / 说明 |
| --- | --- |
| `get_quartus_installation` | Show detected Quartus paths, version, and executable availability / 显示 Quartus 路径、版本和可执行文件状态 |

### Project Management

| Tool | Description / 说明 |
| --- | --- |
| `create_project` | Create a new project with family and device assignments / 创建新工程并设置 family 和 device |
| `open_project` | Open and verify an existing project, then close it / 打开并验证现有工程后关闭 |
| `close_project` | Explain the stateless project-close behavior / 说明无状态 close 行为 |
| `get_project_info` | Read family, device, revision, and top-level entity / 读取 family、device、revision 和顶层实体 |
| `list_projects` | Find `.qpf` projects recursively / 递归查找 `.qpf` 工程 |
| `archive_project` | Archive a project to `.qar` / 将工程归档为 `.qar` |

### Compilation

| Tool | Description / 说明 |
| --- | --- |
| `compile_project` | Run full or partial flow: `full`, `map`, `fit`, `asm`, `sta` / 运行完整或部分流程 |
| `run_analysis_synthesis` | Run Analysis and Synthesis through `quartus_map` / 通过 `quartus_map` 运行分析综合 |
| `run_fitter` | Run Fitter through `quartus_fit` / 通过 `quartus_fit` 运行 Fitter |
| `run_assembler` | Run Assembler through `quartus_asm` / 通过 `quartus_asm` 运行 Assembler |
| `get_compilation_status` | Check last compile status and generated artifacts / 查看最近编译状态和产物 |
| `get_compilation_messages` | Extract errors, warnings, and critical warnings / 提取错误、警告和关键警告 |

### Incremental Compilation

| Tool | Description / 说明 |
| --- | --- |
| `enable_incremental_compilation` | Turn incremental compilation on/off / 开启关闭增量编译 |
| `create_design_partition` | Create a design partition for incremental/team-based flow / 创建设计分区 |
| `get_design_partitions` | List all design partitions / 列出设计分区 |
| `run_incremental_compile` | Recompile only changed partitions / 仅编译变更分区 |

### RTL Static Analysis

| Tool | Description / 说明 |
| --- | --- |
| `analyze_rtl_structure` | Parse module hierarchy, ports, instances, FSM patterns / 分析模块层次、端口、实例、状态机 |
| `check_coding_style` | Detect missing resets, latches, nonblocking-in-comb / 检测复位缺失、锁存器、风格错误 |

### Timing Analysis

| Tool | Description / 说明 |
| --- | --- |
| `run_timing_analysis` | Run Static Timing Analysis through `quartus_sta` / 通过 `quartus_sta` 运行静态时序分析 |
| `get_timing_summary` | Read the latest `.sta.summary` report / 读取最新 `.sta.summary` 报告 |
| `get_clock_summary` | Extract clock names and periods from STA output / 从 STA 输出提取时钟名和周期 |
| `get_timing_paths` | Query timing paths between two node patterns / 查询两个节点模式之间的时序路径 |

### Pin Assignments and Device

| Tool | Description / 说明 |
| --- | --- |
| `get_device_families` | List supported device families / 列出支持的器件 family |
| `get_devices` | List part numbers for a family / 列出指定 family 的器件型号 |
| `get_pin_assignments` | Read `set_location_assignment` entries / 读取管脚分配 |
| `set_pin_assignment` | Assign a signal to a pin with optional I/O standard / 给信号分配管脚和可选 I/O 标准 |
| `remove_pin_assignment` | Remove pin and I/O standard assignments for a signal / 移除某信号的管脚和 I/O 标准 |
| `get_global_assignments` | Read all `set_global_assignment` entries / 读取全局分配 |
| `set_global_assignment` | Write a global assignment such as `TOP_LEVEL_ENTITY` / 写入全局分配 |

### SDC Constraint Generation

| Tool | Description / 说明 |
| --- | --- |
| `generate_sdc_constraints` | Auto-generate SDC from pin assignments / 根据管脚分配自动生成 SDC |
| `set_clock_constraint` | Add or update a create_clock constraint / 添加或更新 create_clock 约束 |
| `set_false_path_constraint` | Add a set_false_path constraint / 添加 set_false_path 约束 |

### JTAG and Programmer

| Tool | Description / 说明 |
| --- | --- |
| `detect_jtag_devices` | Detect JTAG-connected devices / 检测 JTAG 器件 |
| `get_programmer_cables` | List available programmer cables / 列出可用下载线 |
| `program_device` | Program the latest `.sof` over JTAG / 通过 JTAG 下载最新 `.sof` |

### SignalTap II Logic Analyzer

| Tool | Description / 说明 |
| --- | --- |
| `create_signaltap_file` | Create a SignalTap II .stp logic analyzer file / 创建 SignalTap II .stp 逻辑分析文件 |
| `get_signaltap_context` | Check SignalTap status and list .stp files / 检查 SignalTap 状态并列出 .stp 文件 |
| `program_with_signaltap` | Compile with SignalTap and program the FPGA / 编译含 SignalTap 的工程并下载 |

### IP Core Generation

| Tool | Description / 说明 |
| --- | --- |
| `list_available_ip` | List available MegaWizard/IP cores / 列出可用 IP 核 |
| `create_pll_ip` | Generate an ALTPLL IP core / 生成 ALTPLL 锁相环 IP 核 |
| `create_ram_ip` | Generate on-chip RAM/ROM IP (ALTSYNCRAM) / 生成片上 RAM/ROM IP |
| `create_fifo_ip` | Generate a FIFO IP core (SCFIFO/DCFIFO) / 生成 FIFO IP 核 |
| `convert_programming_file` | Convert .sof to .jic/.pof/.rbf/.hex / 转换 .sof 为其他编程文件格式 |

### Reports and Analysis

### Reports and Analysis

| Tool | Description / 说明 |
| --- | --- |
| `get_flow_summary` | Read the flow summary report / 读取 flow summary 报告 |
| `get_resource_usage` | Extract resource utilization / 提取资源利用率 |
| `read_report_file` | Read a specific report type such as `flow`, `map`, `fit`, `asm`, `sta`, or `pow` / 读取指定报告类型 |
| `get_power_report` | Run `quartus_pow` and return the power report / 运行 `quartus_pow` 并返回功耗报告 |
| `run_design_rule_check` | Run `quartus_drc` / 运行 `quartus_drc` |

### File Management

| Tool | Description / 说明 |
| --- | --- |
| `list_project_files` | List HDL/SDC/MIF files registered in the `.qsf` / 列出 `.qsf` 中注册的 HDL/SDC/MIF 文件 |
| `add_file_to_project` | Add a file assignment to the project / 向工程添加文件分配 |
| `remove_file_from_project` | Remove a file assignment from the `.qsf` / 从 `.qsf` 移除文件分配 |
| `read_qsf` | Read the raw `.qsf` file / 读取原始 `.qsf` 文件 |

### ModelSim Simulation

| Tool | Description / 说明 |
| --- | --- |
| `create_testbench` | Create a Verilog testbench from the project top-level ports / 根据顶层端口创建 Verilog testbench |
| `add_simulation_file` | Register or create simulation-only HDL/Tcl files / 注册或创建仅仿真使用的 HDL/Tcl 文件 |
| `run_simulation` | Run ModelSim batch simulation with project HDL, testbench files, Intel sim libraries, and IP setup scripts / 使用工程 HDL、testbench、Intel 仿真库和 IP 脚本运行 ModelSim 批处理仿真 |
| `read_simulation_log` | Read `simulation.log` or `transcript`, extracting pass/error/warning signals / 读取仿真日志并提取通过、错误和警告信号 |
| `list_simulation_artifacts` | List simulation manifests, logs, waveforms, and generated ModelSim files / 列出仿真 manifest、日志、波形和 ModelSim 产物 |

### Tcl Scripting

| Tool | Description / 说明 |
| --- | --- |
| `run_tcl_script` | Run a `.tcl` file through `quartus_sh -t` / 通过 `quartus_sh -t` 运行 `.tcl` 文件 |
| `execute_tcl_command` | Run inline Tcl through a temporary script / 通过临时脚本运行内联 Tcl |

## Notes and Limitations

- Programmer/JTAG tools require a connected FPGA board and cable.
- ModelSim simulation requires a working ModelSim license.
- This project invokes local command-line tools only; it does not include Quartus, ModelSim, Intel IP, or Intel simulation library files.
- `execute_tcl_command` intentionally runs arbitrary Tcl locally. Use it only with trusted commands.

## 注意事项与限制

- Programmer/JTAG 相关工具需要连接 FPGA 开发板和下载线。
- ModelSim 仿真需要可用的 ModelSim license。
- 本项目只调用本机命令行工具，不包含 Quartus、ModelSim、Intel IP 或 Intel 仿真库文件。
- `execute_tcl_command` 会在本机执行任意 Tcl，请只运行可信命令。

## Acknowledgements

This project is based on and extends [irumvag/quartus_mcp_server](https://github.com/irumvag/quartus_mcp_server), which declares MIT licensing in its README.

Thanks to `@irumvag` for the original Quartus MCP server work.

## 致谢

本项目基于并扩展了 [irumvag/quartus_mcp_server](https://github.com/irumvag/quartus_mcp_server)。该上游项目在 README 中声明采用 MIT license。

感谢 `@irumvag` 的原始 Quartus MCP server 工作。

## License

MIT. See [LICENSE](LICENSE).

## 许可证

MIT。详见 [LICENSE](LICENSE)。
