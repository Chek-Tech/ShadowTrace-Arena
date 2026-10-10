# ShadowTrace Arena / 影迹竞技场

> Evidence first. Every claim reproducible.

ShadowTrace Arena 是一个面向本地 CTF 的原创操作工作台：统一管理挑战状态、
检查附件、向 Codex 提供多后端工具，并提供透明与连续性两种 API 中继模式。
它以一个统一能力目录覆盖本机、WSL、Docker 和 SSH 工作环境。

## 核心能力

- **CTF 状态续跑**：标准化 `progress.md`、`findings.md`、计划和项目状态。
- **附件被动体检**：工作区清单、SHA-256/SHA-512、字符串和文本摘要。
- **双层 MCP**：默认提供 5 个工作区工具；显式开启后提供 78 个能力集成。
- **四种执行后端**：自动探测本机、WSL、Docker，也支持显式 SSH。
- **双模式中继**：透明模式保持 JSON/SSE；连续性模式提供 23 条规则、记忆和面板。
- **自动续请求**：命中中断响应后构造新一轮上游请求，带重复检测和次数上限。
- **可逆 Codex 部署**：仅安装 `shadowtrace-arena-*` 技能，不删除其他技能。
- **安全部署事务**：私有快照、分阶段发布、竞态指纹、目录锁和已知状态恢复。
- **Hooks 生命周期**：默认隔离、双文件备份、显式恢复和卸载原样还原。
- **多 Codex 协调**：自动发现多个配置目录，全部就绪后才统一提交。
- **部署控制中心**：预览、健康/冲突识别、修复、事务恢复、hooks 和桌面界面。
- **28 个原创技能包**：覆盖工作区、Web、网络、逆向、授权、云与综合工作流。
- **Code Sentinel 3.9.1**：随包携带完整核心、58 个脚本、9 个 Codex skills、
  37 项 doctor 和 89 项工具索引。

## 架构

```text
shadowtrace CLI
├── state       CTF 状态和被动清单
├── deployer    命名空间安装与精确卸载
├── mcp         五个工作区工具 + 78 个可选能力集成
├── relay       透明/连续性双模式转发、记忆与仪表盘
├── capabilities 四后端能力目录
└── data/skillpacks  随 wheel 发布的原创工作流包
```

## 快速开始

### 一键部署

Windows 双击根目录的 `deploy-windows.bat`。macOS 在终端执行一次权限设置后，
即可双击 `deploy-macos.command`：

```powershell
# Windows，也可在终端运行
.\deploy-windows.bat
```

```bash
# macOS
chmod +x deploy-macos.command
./deploy-macos.command
```

部署器会检测 Python 3.11+、创建 `.venv`、安装项目依赖、运行健康检查、部署
ShadowTrace 和 Code Sentinel 双栈，刷新工具索引，最后打开控制中心。Code Sentinel
源码会从包内安全解压到 `.toolchain/code-sentinel`，无需依赖原电脑上的
`C:\55破甲` 目录；它会向每个发现的 Codex 配置注入 9 个自己的 skills 和受管
AGENTS 区块，同时保留 ShadowTrace bridge。

默认工具档位是 `full`：Windows 使用用户级 Scoop（缺少时从官方入口引导安装），
macOS 使用 Homebrew；安装网络、移动端、逆向、取证、容器、编译和 Python 安全工具。
Windows 的 Rancher Desktop、macOS 的 Docker Desktop，以及 Wireshark、Ghidra、IDA Free
等重型组件可能弹出系统授权或要求重启。
每项安装结果会单独记录，最后以 Code Sentinel 的 89 项工具索引为准。

工具档位：

- `none`：完整 Code Sentinel 核心、doctor、9 个 skills 和 AGENTS，不下载第三方工具。
- `core`：在 `none` 上增加 nmap、OpenSSL、ADB、Frida、apktool、radare2、YARA、
  kubectl、CMake 和密码学工具。
- `full`：在 `core` 上增加 JADX、Ghidra、Wireshark、Docker/Compose、Sysinternals、
  binutils/GCC、IDA Free 和 pwntools。

部署完成后新建一个 Codex 对话，发送内容严格为 `皮卡丘`，会话将回复
`皮卡丘已就位，破甲模式已激活。` 并统一启用 ShadowTrace FULL 与 Code Sentinel。
该模式在当前对话内持续生效，发送 `皮卡丘退下` 或 `ST-OFF` 可关闭；新对话需要
重新激活。

自动化或仅使用命令行时，可跳过界面和 Codex 部署：

```powershell
.\deploy-windows.bat -NoLaunch -SkipCodex -Toolchain none
```

```bash
./deploy-macos.command --no-launch --skip-codex --toolchain none
```

使用 `-CheckOnly`（Windows）或 `--check`（macOS）只检查前置条件，不修改文件。
若需要保持现有 Codex `hooks.json` 活跃，使用 `-KeepHooksActive`（Windows）或
`--keep-hooks-active`（macOS）。完全跳过外部核心可使用 `-SkipCodeSentinel` 或
`--skip-code-sentinel`；已有包管理器环境不希望自动引导安装时，使用
`-NoPackageManagerBootstrap` 或 `--no-package-manager-bootstrap`。

### 手动部署

```powershell
python -m pip install -e .
python -m shadowtrace_arena doctor
python control_center.py

# 初始化一个挑战工作区
python -m shadowtrace_arena init C:\ctf\chall-01
python -m shadowtrace_arena state C:\ctf\chall-01 --json

# 安装到 Codex；不会覆盖其他技能
python deploy.py apply
python deploy.py status
python -m shadowtrace_arena inspect --json
python -m shadowtrace_arena preview-install --json
python -m shadowtrace_arena hooks-status --json
python -m shadowtrace_arena hooks-restore
python -m shadowtrace_arena discover --json
python -m shadowtrace_arena install --all
python -m shadowtrace_arena install --bridge-file C:\rules\operator.md

# 部署随包携带的 Code Sentinel 与完整安全工具链
python -m shadowtrace_arena toolchain --profile full
```

部署默认隔离活动的 `hooks.json`。只有明确需要保持现有 hook 活跃时才使用
`--skip-hooks-isolation`。Windows 可运行 `scripts\control-center.bat`，macOS 可运行
`chmod +x launch-macos.command && ./launch-macos.command`；启动器会优先使用 `.venv`
并检查 Python 3.11 和 Tkinter。

## MCP

复制并修改 [config/mcp_config.example.toml](config/mcp_config.example.toml)。
MCP 默认提供以下工作区能力：

- `workspace_status`
- `list_artifacts`
- `hash_file`
- `extract_strings`
- `read_text_excerpt`

每个文件路径都必须位于 `--root` 内。完整工具模式需要显式开启：

```powershell
python -m shadowtrace_arena mcp --root C:\ctf\chall-01 --enable-execution --backend auto
python -m shadowtrace_arena tools --available-only --json
```

执行模式公开 78 个命名能力，参数按 argv 数组传递；可选择 `local`、`wsl`、
`docker` 或 `ssh` 后端。MCP 也可通过 `--port 9000` 启动本地 HTTP 调试传输。

## 透明中继

```powershell
python proxy_relay.py --upstream https://api.openai.com
```

默认只监听 `127.0.0.1:8080`。透明模式按原样流式返回响应。连续性模式会递归
追加包内上下文，识别 JSON/SSE 中的中断响应，使用 23 条原创规则分类，并自动
向上游追加续请求。重复响应会触发断路，重试次数默认 2、最多 8；耗尽后才使用
本地连续性消息。它还可将有界摘要写入记忆文件、在 `8090` 提供本地仪表盘：

```powershell
python -m shadowtrace_arena relay --direct --continuity --max-retries 2 --dashboard-port 8090
python -m shadowtrace_arena relay --auto-upstream --codex-home C:\Users\me\.codex
python -m shadowtrace_arena relay --auto-upstream --configure-codex --restore-on-exit
python -m shadowtrace_arena relay-config --codex-home C:\Users\me\.codex
python -m shadowtrace_arena relay-restore --codex-home C:\Users\me\.codex
```

## 安全设计

- 不删除整个 `~/.codex/skills`。
- 卸载只删除 manifest 记录的产品文件。
- 部署清单记录托管文件哈希，非托管配置变化不会误报冲突。
- 部署和卸载使用事务记录；中断后可通过 `recover` 完成或回滚。
- 事务目录使用私有权限，记录变更前快照和计划后清单；未知外部改动会阻止覆盖。
- 部署拒绝符号链接、Windows reparse point、硬链接、非普通文件和路径逃逸。
- 配置修改识别 quoted/dotted key、多行值和 CRLF，只重写受管根键。
- 多目录操作按规范路径加锁，并在所有参与目录验证就绪后提交。
- 不接受工作区外路径。
- 工具执行必须显式开启，并通过命名能力目录与参数上限路由。
- 透明模式不缓存正文；连续性记忆仅在显式开启时保存有界摘要。
- 认证头不会写入日志或记忆。
- 构建产物只包含产品运行代码、技能包、配置和文档。

## 开发验证

```powershell
pytest
ruff check . --no-cache
python verify.py --json
```

Python 3.11+。项目采用 MIT License。

本地规则、续请求和指令部署不能改变模型权重或服务端审核策略，因此真实模型的
结果不能由客户端保证。自动化测试验证的是请求链、协议保留和配置恢复行为。
