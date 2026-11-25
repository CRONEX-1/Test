"""Wi-Fi scanning. Each platform has its own backend; `create_wifi_scanner` picks one."""

from __future__ import annotations

import shutil
import subprocess
import sys
from typing import List, Optional, Sequence

from ..models import Reading


class WifiError(RuntimeError):
    """Raised when a scan cannot be performed; the message is shown to the user."""


class WifiScanner:
    name = "base"

    def scan(self) -> List[Reading]:  # pragma: no cover - interface
        raise NotImplementedError


def run(cmd: Sequence[str], timeout: float = 30.0, check: bool = True) -> str:
    """Run a command and return stdout, raising WifiError with a useful message."""
    if not shutil.which(cmd[0]):
        raise WifiError(f"'{cmd[0]}' was not found on this system")
    try:
        proc = subprocess.run(
            list(cmd),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=_c_locale_env(),
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.TimeoutExpired as e:
        raise WifiError(f"'{' '.join(cmd)}' timed out after {timeout:.0f}s") from e
    if check and proc.returncode != 0:
        msg = (proc.stderr or proc.stdout).strip().splitlines()
        raise WifiError(f"'{' '.join(cmd)}' failed: {msg[-1] if msg else proc.returncode}")
    return proc.stdout


def _c_locale_env():
    import os

    env = dict(os.environ)
    if sys.platform.startswith("linux"):
        # English, machine-stable output, but UTF-8 so non-ASCII SSIDs survive.
        env["LC_ALL"] = "C.UTF-8"
    return env


def create_wifi_scanner(interface: Optional[str] = None) -> WifiScanner:
    if sys.platform.startswith("linux"):
        from .linux import LinuxWifiScanner

        return LinuxWifiScanner(interface)
    if sys.platform == "darwin":
        from .macos import MacWifiScanner

        return MacWifiScanner(interface)
    if sys.platform == "win32":
        from .windows import WindowsWifiScanner

        return WindowsWifiScanner(interface)
    raise WifiError(f"Wi-Fi scanning is not supported on {sys.platform}")
