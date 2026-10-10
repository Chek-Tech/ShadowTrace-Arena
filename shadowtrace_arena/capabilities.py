"""Original capability catalog and multi-backend command execution."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from typing import Iterable

MAX_OUTPUT_CHARS = 100_000
MAX_ARGUMENTS = 128
MAX_ARGUMENT_CHARS = 16_384


@dataclass(frozen=True)
class Capability:
    name: str
    executable: str
    category: str
    description: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


def _cap(name: str, executable: str, category: str, description: str) -> Capability:
    return Capability(name, executable, category, description)


# The catalog intentionally describes integrations rather than copying command
# templates from another project. Arguments are passed as an argv array.
CAPABILITIES = (
    _cap("nmap", "nmap", "network", "Map hosts, ports, and services."),
    _cap("masscan", "masscan", "network", "Perform high-rate port discovery."),
    _cap("tcpdump", "tcpdump", "network", "Capture and filter packets."),
    _cap("netcat", "nc", "network", "Open a TCP or UDP connection."),
    _cap("bettercap", "bettercap", "network", "Run a network assessment session."),
    _cap("responder", "responder", "network", "Assess name-resolution exposure."),
    _cap("impacket", "impacket-scripts", "network", "Use the Impacket tool suite."),
    _cap("aircrack_ng", "aircrack-ng", "wireless", "Analyze wireless captures."),
    _cap("reaver", "reaver", "wireless", "Assess WPS configuration."),
    _cap("wireshark", "tshark", "network", "Inspect packet captures."),
    _cap("socat", "socat", "network", "Create bidirectional data channels."),
    _cap("sqlmap", "sqlmap", "web", "Test database query handling."),
    _cap("dirb", "dirb", "web", "Enumerate web paths."),
    _cap("nikto", "nikto", "web", "Inspect web server configuration."),
    _cap("wpscan", "wpscan", "web", "Assess WordPress installations."),
    _cap("ffuf", "ffuf", "web", "Fuzz web inputs and paths."),
    _cap("curl", "curl", "web", "Send HTTP requests."),
    _cap("gobuster", "gobuster", "web", "Enumerate web, DNS, and vhost names."),
    _cap("burp", "burpsuite", "web", "Launch an intercepting web workbench."),
    _cap("mitmproxy", "mitmproxy", "web", "Inspect and transform HTTP traffic."),
    _cap("subfinder", "subfinder", "web", "Discover subdomains."),
    _cap("amass", "amass", "web", "Map external assets and DNS."),
    _cap("whatweb", "whatweb", "web", "Fingerprint web technology."),
    _cap("wafw00f", "wafw00f", "web", "Identify web application firewalls."),
    _cap("nuclei", "nuclei", "web", "Run template-based checks."),
    _cap("xray", "xray", "web", "Run web security checks."),
    _cap("xsstrike", "xsstrike", "web", "Test browser injection handling."),
    _cap("nosqlmap", "nosqlmap", "web", "Assess NoSQL query handling."),
    _cap("commix", "commix", "web", "Assess command input handling."),
    _cap("metasploit", "msfconsole", "exploit", "Use the Metasploit console."),
    _cap("searchsploit", "searchsploit", "exploit", "Search local exploit references."),
    _cap("setoolkit", "setoolkit", "social", "Run a social-engineering lab workflow."),
    _cap("gophish", "gophish", "social", "Run a campaign simulation service."),
    _cap("crackmapexec", "crackmapexec", "exploit", "Assess Windows network access."),
    _cap("nessus", "nessuscli", "scanner", "Control a Nessus scanner."),
    _cap("hydra", "hydra", "password", "Test credential sets."),
    _cap("john", "john", "password", "Audit password hashes."),
    _cap("hashcat", "hashcat", "password", "Run accelerated hash audits."),
    _cap("medusa", "medusa", "password", "Test network credentials."),
    _cap("cewl", "cewl", "password", "Build a site-derived word list."),
    _cap("crunch", "crunch", "password", "Generate constrained word lists."),
    _cap("strings", "strings", "reverse", "Extract printable strings."),
    _cap("objdump", "objdump", "reverse", "Inspect object files and disassembly."),
    _cap("radare2", "r2", "reverse", "Analyze binaries with radare2."),
    _cap("binwalk", "binwalk", "reverse", "Inspect embedded file signatures."),
    _cap("ghidra", "analyzeHeadless", "reverse", "Run headless Ghidra analysis."),
    _cap("apktool", "apktool", "reverse", "Decode and rebuild Android packages."),
    _cap("jadx", "jadx", "reverse", "Decompile Android bytecode."),
    _cap("frida", "frida", "reverse", "Observe application runtime behavior."),
    _cap("ida", "idat64", "reverse", "Run IDA batch analysis."),
    _cap("x64dbg", "x64dbg", "reverse", "Launch a Windows debugger."),
    _cap("ollydbg", "ollydbg", "reverse", "Launch a 32-bit debugger."),
    _cap("dnspy", "dnSpy", "reverse", "Inspect managed assemblies."),
    _cap("dex2jar", "d2j-dex2jar", "reverse", "Convert Android bytecode to JAR."),
    _cap("smali", "smali", "reverse", "Assemble Android bytecode."),
    _cap("angr", "angr", "reverse", "Run symbolic analysis helpers."),
    _cap("die", "die", "reverse", "Identify executable formats and packers."),
    _cap("upx", "upx", "reverse", "Inspect or unpack UPX files."),
    _cap("scylla", "Scylla", "reverse", "Rebuild Windows imports."),
    _cap("peframe", "peframe", "reverse", "Triage PE files."),
    _cap("pestudio", "pestudio", "reverse", "Inspect Windows executables."),
    _cap("dumpbin", "dumpbin", "reverse", "Inspect Microsoft binary metadata."),
    _cap("cheat_engine", "cheatengine", "reverse", "Open a memory analysis workbench."),
    _cap("process_hacker", "ProcessHacker", "reverse", "Inspect Windows processes."),
    _cap("yara", "yara", "reverse", "Match artifact rules."),
    _cap("exiftool", "exiftool", "forensics", "Read file metadata."),
    _cap("foremost", "foremost", "forensics", "Recover files by signature."),
    _cap("volatility", "vol", "forensics", "Analyze memory images."),
    _cap("openssl", "openssl", "crypto", "Inspect certificates and cryptographic data."),
    _cap("powershell", "powershell", "windows", "Run a PowerShell command."),
    _cap("reg_query", "reg", "windows", "Query the Windows registry."),
    _cap("wmic", "wmic", "windows", "Query Windows management data."),
    _cap("mimikatz", "mimikatz", "windows", "Run a credential exposure lab check."),
    _cap("bloodhound", "bloodhound", "windows", "Collect directory relationship data."),
    _cap("python_runtime", sys.executable, "scripting", "Run a Python snippet."),
    _cap("command_runtime", "shell", "scripting", "Run a command through the selected backend."),
    _cap("theharvester", "theHarvester", "social", "Collect public-source identifiers."),
    _cap("sherlock", "sherlock", "social", "Check public account-name presence."),
)

CAPABILITY_BY_NAME = {item.name: item for item in CAPABILITIES}


@dataclass(frozen=True)
class BackendConfig:
    kind: str = "auto"
    wsl_distro: str = "kali-linux"
    container: str = "kali-tools"
    ssh_target: str | None = None


def _shell_join(parts: Iterable[str]) -> str:
    return shlex.join([str(part) for part in parts])


class ToolExecutor:
    def __init__(self, config: BackendConfig | None = None):
        self.config = config or BackendConfig()
        if self.config.kind == "auto":
            self.config = detect_backend(self.config)
        if self.config.kind not in {"local", "wsl", "docker", "ssh"}:
            raise ValueError(f"Unknown backend: {self.config.kind}")
        if self.config.kind == "ssh" and not self.config.ssh_target:
            raise ValueError("ssh_target is required for the SSH backend")

    def command(self, capability: Capability, arguments: list[str]) -> list[str]:
        if len(arguments) > MAX_ARGUMENTS:
            raise ValueError("Too many command arguments")
        args = [str(value) for value in arguments]
        if any(len(value) > MAX_ARGUMENT_CHARS for value in args):
            raise ValueError("Command argument is too long")

        if capability.name == "python_runtime":
            if len(args) != 1:
                raise ValueError("python_runtime expects one code argument")
            local = [sys.executable, "-c", args[0]]
        elif capability.name == "command_runtime":
            if len(args) != 1:
                raise ValueError("command_runtime expects one command argument")
            if self.config.kind == "local":
                return (
                    ["powershell", "-NoProfile", "-Command", args[0]]
                    if os.name == "nt"
                    else ["sh", "-lc", args[0]]
                )
            local = ["sh", "-lc", args[0]]
        else:
            local = [capability.executable, *args]

        kind = self.config.kind
        if kind == "local":
            return local
        command_text = _shell_join(local)
        if kind == "wsl":
            return ["wsl", "-d", self.config.wsl_distro, "--", "sh", "-lc", command_text]
        if kind == "docker":
            return ["docker", "exec", self.config.container, "sh", "-lc", command_text]
        return [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=5",
            str(self.config.ssh_target),
            command_text,
        ]

    def run(
        self,
        name: str,
        arguments: list[str] | None = None,
        timeout: float = 600.0,
    ) -> dict[str, object]:
        capability = CAPABILITY_BY_NAME.get(name)
        if capability is None:
            raise KeyError(f"Unknown capability: {name}")
        timeout = max(0.1, min(float(timeout), 3600.0))
        argv = self.command(capability, arguments or [])
        started = __import__("time").monotonic()
        try:
            completed = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                errors="replace",
                timeout=timeout,
                check=False,
            )
            output = (completed.stdout + completed.stderr)[:MAX_OUTPUT_CHARS]
            return {
                "name": name,
                "backend": self.config.kind,
                "argv": argv,
                "returncode": completed.returncode,
                "output": output,
                "truncated": len(completed.stdout + completed.stderr) > MAX_OUTPUT_CHARS,
                "elapsed_ms": int((__import__("time").monotonic() - started) * 1000),
            }
        except subprocess.TimeoutExpired as exc:
            return {
                "name": name,
                "backend": self.config.kind,
                "argv": argv,
                "timeout": True,
                "output": str(exc),
                "elapsed_ms": int((__import__("time").monotonic() - started) * 1000),
            }


def availability() -> list[dict[str, object]]:
    results = []
    for item in CAPABILITIES:
        if item.name in {"python_runtime", "command_runtime"}:
            found = True
        else:
            found = shutil.which(item.executable) is not None
        results.append({**item.to_dict(), "available_local": found})
    return results


def _probe(argv: list[str], marker: str, timeout: float) -> bool:
    try:
        result = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
            check=False,
        )
        return result.returncode == 0 and marker in result.stdout
    except (OSError, subprocess.SubprocessError):
        return False


def detect_backend(config: BackendConfig | None = None) -> BackendConfig:
    requested = config or BackendConfig()
    marker = "SHADOWTRACE_BACKEND_READY"
    if _probe(
        ["wsl", "-d", requested.wsl_distro, "--", "sh", "-lc", f"printf {marker}"],
        marker,
        15,
    ):
        return BackendConfig(
            kind="wsl",
            wsl_distro=requested.wsl_distro,
            container=requested.container,
            ssh_target=requested.ssh_target,
        )
    if _probe(
        ["docker", "exec", requested.container, "sh", "-lc", f"printf {marker}"],
        marker,
        10,
    ):
        return BackendConfig(
            kind="docker",
            wsl_distro=requested.wsl_distro,
            container=requested.container,
            ssh_target=requested.ssh_target,
        )
    return BackendConfig(
        kind="local",
        wsl_distro=requested.wsl_distro,
        container=requested.container,
        ssh_target=requested.ssh_target,
    )
