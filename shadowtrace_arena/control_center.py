"""Tkinter control center for deployment and relay operations."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Callable

from . import deployer
from .product import PRODUCT_NAME, PROJECT_ROOT, VERSION


def relay_command(
    upstream: str,
    continuity: bool,
    max_retries: int,
    dashboard_port: int,
) -> list[str]:
    command = [
        sys.executable,
        "-m",
        "shadowtrace_arena",
        "relay",
        "--upstream",
        upstream,
        "--max-retries",
        str(max(0, min(int(max_retries), 8))),
        "--dashboard-port",
        str(int(dashboard_port)),
    ]
    if continuity:
        command.append("--continuity")
    return command


class ControlCenter:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(f"{PRODUCT_NAME} {VERSION}")
        self.root.geometry("920x640")
        self.root.minsize(760, 520)
        self.relay_process: subprocess.Popen[bytes] | None = None

        detected = deployer.locate_codex_home()
        self.codex_home = tk.StringVar(value=str(detected or ""))
        self.bridge_file = tk.StringVar(value="")
        self.upstream = tk.StringVar(value="https://api.openai.com")
        self.max_retries = tk.IntVar(value=2)
        self.dashboard_port = tk.IntVar(value=8090)
        self.continuity = tk.BooleanVar(value=True)
        self.skip_hooks = tk.BooleanVar(value=False)
        self.status_text = tk.StringVar(value="Checking")
        self.relay_text = tk.StringVar(value="Relay stopped")

        self._build()
        self.refresh()
        self.root.protocol("WM_DELETE_WINDOW", self.close)

    def _build(self) -> None:
        shell = ttk.Frame(self.root, padding=16)
        shell.pack(fill="both", expand=True)
        header = ttk.Frame(shell)
        header.pack(fill="x")
        ttk.Label(header, text=PRODUCT_NAME, font=("Segoe UI", 18, "bold")).pack(
            side="left"
        )
        ttk.Label(header, textvariable=self.status_text).pack(side="right")

        target = ttk.Frame(shell)
        target.pack(fill="x", pady=(16, 8))
        ttk.Label(target, text="Codex home", width=14).pack(side="left")
        ttk.Entry(target, textvariable=self.codex_home).pack(
            side="left", fill="x", expand=True
        )
        ttk.Button(target, text="Browse", command=self.browse).pack(
            side="left", padx=(8, 0)
        )
        ttk.Checkbutton(
            target,
            text="Keep hooks active",
            variable=self.skip_hooks,
        ).pack(side="left", padx=(12, 0))

        bridge = ttk.Frame(shell)
        bridge.pack(fill="x", pady=(0, 8))
        ttk.Label(bridge, text="Bridge file", width=14).pack(side="left")
        ttk.Entry(bridge, textvariable=self.bridge_file).pack(
            side="left", fill="x", expand=True
        )
        ttk.Button(bridge, text="Browse", command=self.browse_bridge).pack(
            side="left", padx=(8, 0)
        )

        actions = ttk.Frame(shell)
        actions.pack(fill="x", pady=8)
        for label, callback in (
            (
                "Install",
                lambda: self.run_action(
                    lambda home: deployer.install(
                        home,
                        skip_hooks_isolation=self.skip_hooks.get(),
                        bridge_file=self.bridge_file.get().strip() or None,
                    )
                ),
            ),
            ("Remove", lambda: self.run_action(deployer.remove)),
            ("Repair", lambda: self.run_action(deployer.repair)),
            ("Recover", lambda: self.run_action(deployer.recover_transaction)),
            ("Restore hooks", lambda: self.run_action(deployer.restore_hooks)),
            ("Refresh", self.refresh),
        ):
            ttk.Button(actions, text=label, command=callback).pack(
                side="left", padx=(0, 8)
            )
        ttk.Button(
            actions,
            text="Install preview",
            command=lambda: self.preview(
                lambda home: deployer.preview_install(
                    home,
                    skip_hooks_isolation=self.skip_hooks.get(),
                    bridge_file=self.bridge_file.get().strip() or None,
                )
            ),
        ).pack(side="right", padx=(8, 0))
        ttk.Button(
            actions,
            text="Remove preview",
            command=lambda: self.preview(deployer.preview_remove),
        ).pack(side="right")

        ttk.Separator(shell).pack(fill="x", pady=10)
        relay = ttk.Frame(shell)
        relay.pack(fill="x")
        ttk.Label(relay, text="Upstream", width=14).grid(row=0, column=0, sticky="w")
        ttk.Entry(relay, textvariable=self.upstream).grid(
            row=0, column=1, columnspan=5, sticky="ew"
        )
        ttk.Checkbutton(relay, text="Continuity", variable=self.continuity).grid(
            row=1, column=1, sticky="w", pady=8
        )
        ttk.Label(relay, text="Retries").grid(row=1, column=2, sticky="e")
        ttk.Spinbox(relay, from_=0, to=8, width=5, textvariable=self.max_retries).grid(
            row=1, column=3, sticky="w", padx=(6, 16)
        )
        ttk.Label(relay, text="Dashboard port").grid(row=1, column=4, sticky="e")
        ttk.Spinbox(
            relay,
            from_=1024,
            to=65535,
            width=7,
            textvariable=self.dashboard_port,
        ).grid(row=1, column=5, sticky="w", padx=(6, 0))
        relay.columnconfigure(1, weight=1)

        relay_actions = ttk.Frame(shell)
        relay_actions.pack(fill="x", pady=(0, 10))
        ttk.Button(relay_actions, text="Start relay", command=self.start_relay).pack(
            side="left", padx=(0, 8)
        )
        ttk.Button(relay_actions, text="Stop relay", command=self.stop_relay).pack(
            side="left", padx=(0, 8)
        )
        ttk.Button(
            relay_actions,
            text="Configure Codex URL",
            command=lambda: self.run_action(deployer.configure_relay),
        ).pack(side="left", padx=(0, 8))
        ttk.Button(
            relay_actions,
            text="Restore Codex URL",
            command=lambda: self.run_action(deployer.restore_relay),
        ).pack(side="left")
        ttk.Label(relay_actions, textvariable=self.relay_text).pack(side="right")

        self.output = tk.Text(
            shell,
            wrap="word",
            height=18,
            font=("Cascadia Mono", 10),
            borderwidth=1,
            relief="solid",
        )
        self.output.pack(fill="both", expand=True)
        self.output.configure(state="disabled")

    def _home(self) -> str | None:
        value = self.codex_home.get().strip()
        return value or None

    def browse(self) -> None:
        selected = filedialog.askdirectory(initialdir=self._home() or str(Path.home()))
        if selected:
            self.codex_home.set(selected)
            self.refresh()

    def browse_bridge(self) -> None:
        selected = filedialog.askopenfilename(
            initialdir=str(PROJECT_ROOT),
            filetypes=(("Markdown", "*.md"), ("All files", "*.*")),
        )
        if selected:
            self.bridge_file.set(selected)

    def write(self, value: object) -> None:
        if hasattr(value, "to_dict"):
            value = value.to_dict()
        text = json.dumps(value, ensure_ascii=False, indent=2, default=str)
        self.output.configure(state="normal")
        self.output.delete("1.0", "end")
        self.output.insert("end", text + "\n")
        self.output.configure(state="disabled")

    def refresh(self) -> None:
        inspection = deployer.inspect_deployment(self._home())
        self.status_text.set(inspection.state)
        self.write(inspection.to_dict())

    def preview(self, operation: Callable[[str | None], object]) -> None:
        try:
            self.write(operation(self._home()))
        except Exception as exc:
            messagebox.showerror(PRODUCT_NAME, str(exc))

    def run_action(self, operation: Callable[[str | None], object]) -> None:
        def worker() -> None:
            try:
                result = operation(self._home())
            except Exception as exc:
                message = str(exc)
                self.root.after(
                    0,
                    lambda message=message: messagebox.showerror(PRODUCT_NAME, message),
                )
                return
            self.root.after(0, lambda: self.write(result))
            self.root.after(0, self.refresh)

        threading.Thread(target=worker, daemon=True).start()

    def start_relay(self) -> None:
        if self.relay_process and self.relay_process.poll() is None:
            self.relay_text.set(f"Relay running, PID {self.relay_process.pid}")
            return
        command = relay_command(
            self.upstream.get().strip(),
            self.continuity.get(),
            self.max_retries.get(),
            self.dashboard_port.get(),
        )
        creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        try:
            self.relay_process = subprocess.Popen(
                command,
                cwd=PROJECT_ROOT,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creationflags,
            )
        except OSError as exc:
            messagebox.showerror(PRODUCT_NAME, str(exc))
            return
        self.relay_text.set(f"Relay running, PID {self.relay_process.pid}")
        self.write({"command": command, "pid": self.relay_process.pid})

    def stop_relay(self) -> None:
        process = self.relay_process
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        self.relay_process = None
        self.relay_text.set("Relay stopped")

    def close(self) -> None:
        self.stop_relay()
        self.root.destroy()


def main() -> int:
    root = tk.Tk()
    ControlCenter(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
