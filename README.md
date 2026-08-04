# Quartus L MCP Server

> 将本机 Intel/Altera Quartus 命令行工具暴露为 MCP 工具，供 AI 助手直接调用。
>
> An MCP server that exposes local Intel/Altera Quartus CLI tools — project management, compilation, timing analysis, SignalTap II, IP cores, Qsys, Nios II, JTAG programming, and more.

---

## 概述 / Overview

**quartus-Lmcp-server** 是一个 [Model Context Protocol (MCP)](https://modelcontextprotocol.io) 服务器，共 **89 个 MCP 工具**，覆盖 FPGA 开发的完整流程。

**核心特性：**
-   **自动版本适配** — 不再需要为每个 Quartus 版本配置独立的 MCP 实例。服务器启动时自动发现所有已安装的 Quartus 版本（13.x ~ 24.x），并可在运行时动态切换。
-   **IP 核版本自适应** — Qsys/Platform Designer 中 `add_instance` 的 IP 版本号自动匹配当前 Quartus 版本，告别硬编码 `18.1` 导致的兼容问题。
-   **多安装发现** — 自动扫描 C:/D:/E:/F: 盘及 WSL 挂载点，发现所有 `intelFPGA_lite`、`intelFPGA`、`altera` 目录下的 Quartus 安装。

不依赖特定 AI 客户端 — 只要支持 MCP 协议即可使用（WorkBuddy、Claude Code、Codex、Cursor、Continue 等）。

---

## 功能概览 / Features

| 分类 | 工具数 | 说明 |
|---|---|---|
| **安装与版本管理** | 3 | 查看安装信息、列出所有版本、运行时切换 Quartus 版本 |
| **项目管理** | 6 | 创建/打开/查看/枚举/关闭/归档 Quartus 工程 |
| **文件管理** | 4 | 添加/移除 HDL 文件、列出工程文件、读写 QSF |
| **编译流程** | 6 | Analysis & Synthesis / Fitter / Assembler / 完整编译，状态与消息 |
| **增量编译** | 4 | 增量编译开关、设计分区创建/查看、增量执行 |
| **RTL 分析** | 2 | 模块层次/端口/FSM 解析、代码风格检查 |
| **时序分析** | 6 | STA 执行、时序摘要、时钟摘要、路径查询、最差路径、时钟域交叉 |
| **SDC 约束** | 3 | 自动生成 SDC 模板、时钟约束、伪路径约束 |
| **管脚分配** | 5 | 管脚设置/移除、全局分配读写 |
| **器件信息** | 2 | 器件家族列表、器件型号列表 |
| **JTAG 下载** | 6 | JTAG 检测、线缆列表、SOF 下载、JAM 编程、CvP 配置 |
| **SignalTap II** | 3 | 逻辑分析仪文件创建、状态检查、含 SignalTap 下载 |
| **IP 核** | 9 | PLL/RAM/FIFO/DSP IP、MegaWizard、IP 目录、文件转换、IP 升级 |
| **Qsys / Platform Designer** | 5 | Qsys 系统创建、组件添加、互联、生成、组件枚举 |
| **Nios II** | 2 | BSP 编译、elf2hex/elf2flash/sof2flash 文件转换 |
| **仿真集成** | 5 | Testbench 创建、仿真文件注册、ModelSim 批处理仿真、日志读取、产物列表 |
| **报告与检查** | 5 | Flow Summary、资源利用率、指定报告读取、功耗分析、DRC |
| **设计数据库** | 4 | 分区导入/导出、数据库导出、Back-annotate |
| **Tcl 脚本** | 2 | Tcl 文件执行、内联 Tcl 命令 |
| **设计空间探索** | 2 | DSE 探索、Seed Sweep |
| **门级仿真** | 2 | 门级仿真生成、上下文获取 |
| **工程维护** | 3 | 清理工程、IP 升级、Flow 模板生成 |

---

## 安装 / Installation

```bash
git clone https://github.com/L-YvY-L/quartus-Lmcp-server.git
cd quartus-Lmcp-server
pip install -r requirements.txt
```

**环境要求：**
-   Windows 10/11
-   Python 3.10+
-   `mcp>=1.0.0`
-   本机安装 Intel/Altera Quartus（支持 Quartus II 13.1 ~ Prime Lite 24.x 各版本）
-   可同时安装多个 Quartus 版本 — 服务器会自动发现并支持运行时切换
-   ModelSim/QuestaSim（可选，仿真工具用）

---

## MCP 配置 / Configuration

### 基础配置（单版本）

```json
{
  "mcpServers": {
    "quartus": {
      "command": "python",
      "args": ["path/to/quartus_Lmcp_server.py"],
      "env": {
        "QUARTUS_ROOTDIR": "D:/intelFPGA_lite/24.1std/quartus",
        "QUARTUS_MCP_MODELSIM_BIN": "D:/intelFPGA_lite/24.1std/questa_fse/win64",
        "QUARTUS_MCP_PROJECT_DIR": "C:/Users/yourname/Documents/QuartusProjects"
      }
    }
  }
}
```

### 多版本配置（推荐）

为每个版本注册独立的 MCP 服务名，或在同一个实例中用 `switch_quartus_installation` 动态切换：

```json
{
  "mcpServers": {
    "quartus24": {
      "command": "python",
      "args": ["path/to/quartus_Lmcp_server.py"],
      "env": {
        "QUARTUS_ROOTDIR": "D:/intelFPGA_lite/24.1std/quartus",
        "QUARTUS_MCP_MODELSIM_BIN": "D:/intelFPGA_lite/24.1std/questa_fse/win64"
      }
    },
    "quartus18": {
      "command": "python",
      "args": ["path/to/quartus_Lmcp_server.py"],
      "env": {
        "QUARTUS_ROOTDIR": "E:/intelFPGA_lite/18.1/quartus",
        "QUARTUS_MCP_MODELSIM_BIN": "E:/intelFPGA_lite/18.1/modelsim_ase/win32aloem"
      }
    }
  }
}
```

### 不同 MCP 客户端配置位置

| 客户端 | 配置文件 |
|---|---|
| **WorkBuddy** | `~\.workbuddy\mcp.json` |
| **Claude Code** | `.mcp.json`（项目目录）或 `~/.mcp.json` |
| **Cursor** | `.cursor/mcp.json` |
| **Continue** | `~/.continue/config.json` |

### 可选环境变量

| 变量 | 说明 | 默认值 |
|---|---|---|
| `QUARTUS_ROOTDIR` | Quartus 安装的 quartus 子目录 | 自动发现 |
| `QUARTUS_ROOTDIR_OVERRIDE` | 覆盖 QUARTUS_ROOTDIR | — |
| `QUARTUS_MCP_ROOT` | 安装根目录（最高优先级） | — |
| `QUARTUS_BIN` | 直接指定 bin 目录 | — |
| `QUARTUS_MCP_MODELSIM_BIN` | ModelSim bin 目录 | 自动发现 |
| `QUARTUS_MCP_PROJECT_DIR` | 默认工程目录 | `~/Documents/quartus_mcp_projects` |

---

## 多版本管理 / Multi-Version Management

服务器启动时自动扫描并发现所有已安装的 Quartus 版本。

### 列出所有安装

调用 `list_quartus_installations` 查看：

```json
{
  "count": 2,
  "current_index": 0,
  "installations": [
    { "index": 0, "active": true,  "path_version": "24.1", "bin_dir": "D:/intelFPGA_lite/24.1std/quartus/bin64" },
    { "index": 1, "active": false, "path_version": "18.1", "bin_dir": "E:/intelFPGA_lite/18.1/quartus/bin64" }
  ]
}
```

### 切换到指定版本

```json
// 调用 switch_quartus_installation(1) 切换到 18.1
{
  "success": true,
  "switched_to": 1,
  "bin_dir": "E:/intelFPGA_lite/18.1/quartus/bin64",
  "version": "Version 18.1.0 Build 625 09/12/2018 SJ Lite Edition"
}
```

切换是**全局生效**的 — 之后所有编译、分析、下载命令都使用新选择的版本。

---

## 使用示例 / Usage Examples

### 工程创建与编译
`create_project` → `add_file_to_project` → `set_pin_assignment` → `compile_project`

### 时序分析
`run_timing_analysis` → `get_timing_summary` → `get_worst_timing_paths`

### 调试流程
`create_signaltap_file` → `compile_project` → `program_with_signaltap`

### IP 核生成
`create_pll_ip`（输入 50MHz → 输出 100/50/25MHz）→ 自动生成 `.qip` 并注册到工程

### Qsys 系统集成
`create_qsys_system` → `add_qsys_component` → `connect_qsys_components` → `generate_qsys`

### Nios II 软件部署
`compile_nios2_bsp` → `convert_nios2_files(type="hex")` → `program_device`

---

## 完整工具目录 / Complete Tool Catalog

### 安装与版本管理
| 工具 | 说明 |
|---|---|
| `get_quartus_installation` | 查看当前 Quartus 安装路径、版本、工具可用性 |
| `list_quartus_installations` | 列出所有发现的 Quartus 版本及索引 |
| `switch_quartus_installation` | 运行时切换到指定 Quartus 版本 |

### 项目管理
| 工具 | 说明 |
|---|---|
| `create_project` | 创建新 Quartus 工程（指定器件家族与型号） |
| `open_project` | 打开已有工程 |
| `get_project_info` | 获取工程信息（名称、器件、文件数等） |
| `list_projects` | 枚举指定目录下的所有工程 |
| `close_project` | 关闭已打开工程 |
| `archive_project` | 归档工程（.qar） |

### 文件管理
| 工具 | 说明 |
|---|---|
| `list_project_files` | 列出工程中所有文件（HDL/SDC/IP/其他） |
| `add_file_to_project` | 向工程添加 HDL/SDC/IP 文件 |
| `remove_file_from_project` | 从工程移除文件 |
| `read_qsf` | 读取 QSF 文件内容 |

### 编译流程
| 工具 | 说明 |
|---|---|
| `compile_project` | 完整编译流程（Analysis → Fitter → Assembler → STA） |
| `run_analysis_synthesis` | 仅执行 Analysis & Synthesis |
| `run_fitter` | 仅执行 Fitter（Place & Route） |
| `run_assembler` | 仅执行 Assembler（生成 .sof/.pof） |
| `get_compilation_status` | 获取编译状态与产物路径 |
| `get_compilation_messages` | 提取错误/警告/关键警告（去重，各限 100 条） |

### 增量编译
| 工具 | 说明 |
|---|---|
| `enable_incremental_compilation` | 开关增量编译 |
| `create_design_partition` | 创建设计分区 |
| `get_design_partitions` | 查看当前分区设置 |
| `run_incremental_compile` | 执行增量编译 |

### RTL 分析
| 工具 | 说明 |
|---|---|
| `analyze_rtl_structure` | 解析 RTL 层次结构、端口列表、FSM 信息 |
| `check_coding_style` | 代码风格检查（组合环、锁存器等） |

### 时序分析
| 工具 | 说明 |
|---|---|
| `run_timing_analysis` | 执行完整 STA |
| `get_timing_summary` | 获取 STA 摘要（Fmax、setup/hold 违例数） |
| `get_clock_summary` | 获取时钟报告（频率、周期、类型） |
| `get_timing_paths` | 查询指定节点间的时序路径 |
| `get_worst_timing_paths` | 获取最差时序路径（setup/hold/recovery/removal） |
| `get_clock_domain_crossings` | 获取时钟域交叉 (CDC) 信息 |

### SDC 约束
| 工具 | 说明 |
|---|---|
| `generate_sdc_constraints` | 自动生成基础 SDC 文件 |
| `set_clock_constraint` | 设置时钟周期约束 |
| `set_false_path_constraint` | 设置伪路径约束 |

### 管脚分配
| 工具 | 说明 |
|---|---|
| `get_pin_assignments` | 查看管脚分配 |
| `set_pin_assignment` | 设置信号到指定管脚 |
| `remove_pin_assignment` | 移除管脚分配 |
| `get_global_assignments` | 查看全局分配 |
| `set_global_assignment` | 设置全局分配 |

### 器件信息
| 工具 | 说明 |
|---|---|
| `get_device_families` | 列举支持的器件家族 |
| `get_devices` | 列举指定家族下的器件型号 |

### JTAG 编程
| 工具 | 说明 |
|---|---|
| `detect_jtag_devices` | 检测 JTAG 链上的器件 |
| `get_programmer_cables` | 列举可用的下载线缆 |
| `program_device` | 下载 .sof 到 FPGA |
| `batch_program_jam` | 批量 JAM 编程 |
| `create_jam_file` | 从 .sof 创建 .jam 文件 |
| `program_cvp` | Configuration via Protocol (CvP) 编程 |

### SignalTap II 逻辑分析仪
| 工具 | 说明 |
|---|---|
| `create_signaltap_file` | 创建 SignalTap II 文件 (.stp) |
| `get_signaltap_context` | 获取 SignalTap 上下文信息 |
| `program_with_signaltap` | 含 SignalTap 编译并下载 |

### IP 核
| 工具 | 说明 |
|---|---|
| `list_available_ip` | 列举可用的 IP 核 |
| `list_ip_catalog` | 列出 IP 目录详细内容 |
| `create_ip_core` | 通用 IP 核创建（MegaWizard） |
| `create_pll_ip` | 创建 PLL IP（自动配置输入/输出频率） |
| `create_ram_ip` | 创建片上 RAM IP |
| `create_fifo_ip` | 创建 FIFO IP |
| `create_dsp_ip` | 创建 DSP 运算 IP（乘法器/累加器等） |
| `convert_programming_file` | 转换编程文件格式 (.sof → .pof/.rbf 等) |
| `upgrade_ip` | 升级工程中所有 IP 核到当前 Quartus 版本 |

### Qsys / Platform Designer
| 工具 | 说明 |
|---|---|
| `create_qsys_system` | 创建 Qsys 系统（含默认时钟源） |
| `add_qsys_component` | 向 Qsys 系统添加组件 |
| `connect_qsys_components` | 连接 Qsys 组件间 Avalon 接口 |
| `generate_qsys` | 生成 Qsys 系统的 HDL 文件 |
| `list_qsys_components` | 枚举可用的 Qsys 组件库 |

### Nios II
| 工具 | 说明 |
|---|---|
| `compile_nios2_bsp` | 编译 Nios II BSP 库 |
| `convert_nios2_files` | elf→hex/flash, sof→flash 格式转换 |

### 仿真集成
| 工具 | 说明 |
|---|---|
| `create_testbench` | 创建 ModelSim Testbench 模板 |
| `add_simulation_file` | 注册仿真文件到工程 |
| `run_simulation` | 运行 ModelSim 批处理仿真 |
| `read_simulation_log` | 读取仿真日志 |
| `list_simulation_artifacts` | 列举仿真产物 |

### 报告
| 工具 | 说明 |
|---|---|
| `get_flow_summary` | 获取编译 Flow Summary |
| `get_resource_usage` | 获取资源利用率报告 |
| `read_report_file` | 读取指定报告文件内容 |
| `get_power_report` | 获取功耗分析报告 |
| `run_design_rule_check` | 执行设计规则检查 (DRC) |

### 设计数据库
| 工具 | 说明 |
|---|---|
| `export_design_partition` | 导出设计分区 |
| `import_design_partition` | 导入设计分区 |
| `export_design_database` | 导出设计数据库 |
| `back_annotate` | Back-annotate 管脚/时序/布线信息 |

### Tcl 脚本
| 工具 | 说明 |
|---|---|
| `run_tcl_script` | 执行 .tcl 脚本文件 |
| `execute_tcl_command` | 执行内联 Tcl 命令（⚠️ 直接执行，注意安全性） |

### 设计空间探索
| 工具 | 说明 |
|---|---|
| `run_design_space_explorer` | 运行 Design Space Explorer |
| `run_seed_sweep` | 多 Seed 编译探索 |

### 门级仿真
| 工具 | 说明 |
|---|---|
| `generate_gate_level_simulation` | 生成门级仿真网表 |
| `get_gate_sim_context` | 获取门级仿真上下文 |

### 工程维护
| 工具 | 说明 |
|---|---|
| `run_power_analysis` | 执行功耗分析 |
| `clean_project` | 清理工程（删除编译中间文件） |
| `generate_flow_template` | 生成编译流程 Tcl 模板 |

---

## 工具自动发现 / Auto-Discovery

服务器启动时自动搜索以下路径：

| 搜索范围 | 路径模式 |
|---|---|
| C:/ D:/ E:/ F: 盘 | `intelFPGA_lite/*`、`intelFPGA/*`、`altera/*` |
| WSL 挂载 | `/mnt/c/intelFPGA_lite`、`/mnt/d/intelFPGA` 等 |
| 环境变量 | `QUARTUS_MCP_ROOT` → `QUARTUS_ROOTDIR` → `QUARTUS_ROOTDIR_OVERRIDE` → `QUARTUS_BIN` |

调用 `list_quartus_installations` 查看所有发现的版本，`get_quartus_installation` 查看当前激活的安装详情。

---

## 已验证环境 / Verified Environments

| 环境 | 版本 | 状态 |
|---|---|---|
| Quartus Prime Lite | 24.1std.0 Build 991 | ✅ |
| Quartus Prime Lite | 18.1.0 Build 625 | ✅ |
| Quartus II Subscription | 13.1.0 Build 162 | ✅ |
| ModelSim-Altera | 10.5b (Quartus 18.1 随附) | ✅ |
| QuestaSim Intel FPGA | 24.1 (Quartus 24.1 随附) | ✅ |

---

## 注意事项 / Notes

-   **JTAG 下载**需要连接开发板和下载线
-   **ModelSim/Questa 仿真**需要有效许可证
-   本项目**只调用本机命令行工具**，不包含 Quartus/ModelSim/IP 库文件
-   `execute_tcl_command` 会在本机执行任意 Tcl，**请只运行可信命令**
-   多版本切换后，已缓存的版本号会重新检测，随后的 IP 核创建会使用新版本号

---

## CQUPT

---

## 许可证 / License

MIT — 详见 [LICENSE](LICENSE)
