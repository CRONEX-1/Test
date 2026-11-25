"""Linux Wi-Fi scanning via NetworkManager (nmcli) and/or iw.

* As root, `iw dev <iface> scan` triggers a fresh scan and reports exact dBm.
* As a normal user, `nmcli` triggers the scan (NetworkManager is allowed to),
  then `iw ... scan dump` is used, if available, to read exact dBm values for
  those results. If that fails, nmcli's 0-100% value is converted to dBm.
"""

from __future__ import annotations

import os
import re
import shutil
import time
from typing import Dict, List, Optional

from ..estimate import percent_to_dbm
from ..models import WIFI, Reading, channel_for_frequency
from . import WifiError, WifiScanner, run

NMCLI_FIELDS = "IN-USE,BSSID,SSID,CHAN,FREQ,SIGNAL,SECURITY"


def split_terse(line: str) -> List[str]:
    """Split one line of `nmcli -t` output on unescaped ':' and unescape '\\:' / '\\\\'."""
    fields, cur, i = [], [], 0
    while i < len(line):
        ch = line[i]
        if ch == "\\" and i + 1 < len(line):
            cur.append(line[i + 1])
            i += 2
            continue
        if ch == ":":
            fields.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
        i += 1
    fields.append("".join(cur))
    return fields


def _leading_int(text: str) -> Optional[int]:
    m = re.match(r"\s*(\d+)", text or "")
    return int(m.group(1)) if m else None


def parse_nmcli(output: str, now: Optional[float] = None) -> List[Reading]:
    now = now or time.time()
    readings = []
    for line in output.splitlines():
        if not line.strip():
            continue
        parts = split_terse(line)
        if len(parts) < 7:
            continue
        in_use, bssid, ssid, chan, freq, signal, security = parts[:7]
        pct = _leading_int(signal)
        if pct is None or not bssid:
            continue
        freq_mhz = _leading_int(freq)
        readings.append(
            Reading(
                kind=WIFI,
                id=bssid.lower(),
                name=ssid,
                rssi=round(percent_to_dbm(pct, "networkmanager")),
                timestamp=now,
                channel=_leading_int(chan) or channel_for_frequency(freq_mhz),
                frequency=freq_mhz,
                security=(security.strip() if security.strip() not in ("", "--") else "Open"),
                connected=in_use.strip() == "*",
                rssi_estimated=True,
            )
        )
    return readings


_BSS_RE = re.compile(r"^BSS ([0-9a-fA-F:]{17})")


def parse_iw_scan(output: str, now: Optional[float] = None) -> List[Reading]:
    """Parse `iw dev <iface> scan` / `scan dump` output."""
    now = now or time.time()
    readings: List[Reading] = []
    cur: Optional[Dict] = None

    def flush():
        if cur and cur.get("signal") is not None:
            freq = cur.get("freq")
            if cur.get("rsn"):
                sec = "WPA3" if cur.get("sae") and not cur.get("psk") else "WPA2"
                if cur.get("sae") and cur.get("psk"):
                    sec = "WPA2/WPA3"
            elif cur.get("wpa"):
                sec = "WPA"
            elif cur.get("privacy"):
                sec = "WEP"
            else:
                sec = "Open"
            readings.append(
                Reading(
                    kind=WIFI,
                    id=cur["bssid"],
                    name=cur.get("ssid", ""),
                    rssi=cur["signal"],
                    timestamp=now,
                    channel=channel_for_frequency(freq),
                    frequency=int(round(freq)) if freq else None,
                    security=sec,
                    connected=cur.get("associated", False),
                )
            )

    for raw in output.splitlines():
        m = _BSS_RE.match(raw)
        if m:
            flush()
            cur = {"bssid": m.group(1).lower(), "associated": "associated" in raw}
            continue
        if cur is None:
            continue
        line = raw.strip()
        if line.startswith("freq:"):
            try:
                cur["freq"] = float(line.split(":", 1)[1])
            except ValueError:
                pass
        elif line.startswith("signal:"):
            m2 = re.search(r"(-?\d+(?:\.\d+)?)\s*dBm", line)
            if m2:
                cur["signal"] = float(m2.group(1))
        elif line.startswith("SSID:"):
            cur["ssid"] = line[5:].strip()
        elif line.startswith("capability:"):
            cur["privacy"] = "Privacy" in line
        elif line.startswith("RSN:"):
            cur["rsn"] = True
        elif line.startswith("WPA:"):
            cur["wpa"] = True
        elif "Authentication suites:" in line:
            cur["sae"] = cur.get("sae") or "SAE" in line
            cur["psk"] = cur.get("psk") or bool(re.search(r"\bPSK\b|802\.1X", line))
    flush()
    return readings


def parse_iw_dev(output: str) -> List[str]:
    return re.findall(r"^\s*Interface\s+(\S+)", output, flags=re.MULTILINE)


class LinuxWifiScanner(WifiScanner):
    name = "linux"

    def __init__(self, interface: Optional[str] = None):
        self.interface = interface
        self.has_nmcli = bool(shutil.which("nmcli"))
        self.has_iw = bool(shutil.which("iw"))
        self.is_root = hasattr(os, "geteuid") and os.geteuid() == 0
        if not (self.has_nmcli or self.has_iw):
            raise WifiError(
                "Neither 'nmcli' (NetworkManager) nor 'iw' is installed. "
                "Install one, e.g. 'sudo apt install network-manager' or 'sudo apt install iw'."
            )

    def _iw_interface(self) -> Optional[str]:
        if self.interface:
            return self.interface
        try:
            names = parse_iw_dev(run(["iw", "dev"], timeout=5))
        except WifiError:
            return None
        if names:
            self.interface = names[0]
        return self.interface

    def scan(self) -> List[Reading]:
        if self.is_root and self.has_iw:
            iface = self._iw_interface()
            if iface:
                try:
                    results = parse_iw_scan(run(["iw", "dev", iface, "scan"], timeout=30))
                    if results:
                        self._mark_connected(results)
                        return results
                except WifiError:
                    if not self.has_nmcli:
                        raise
        if self.has_nmcli:
            return self._scan_nmcli()
        raise WifiError("'iw' needs root to trigger a scan. Re-run with sudo, or install NetworkManager.")

    def _scan_nmcli(self) -> List[Reading]:
        base = ["nmcli", "-t", "-f", NMCLI_FIELDS, "device", "wifi", "list"]
        if self.interface:
            base += ["ifname", self.interface]
        try:
            out = run(base + ["--rescan", "yes"], timeout=30)
        except WifiError:
            # NetworkManager refuses rescans that are too close together; use its cache.
            out = run(base + ["--rescan", "no"], timeout=15)
        readings = parse_nmcli(out)
        self._refine_with_iw(readings)
        return readings

    def _refine_with_iw(self, readings: List[Reading]) -> None:
        """Replace nmcli's percentage-derived dBm with iw's exact values where possible."""
        if not (self.has_iw and readings):
            return
        iface = self._iw_interface()
        if not iface:
            return
        try:
            exact = {r.id: r for r in parse_iw_scan(run(["iw", "dev", iface, "scan", "dump"], timeout=10))}
        except WifiError:
            return
        for r in readings:
            e = exact.get(r.id)
            if e is not None:
                r.rssi = e.rssi
                r.rssi_estimated = False
                if e.security and r.security in (None, "Open") and e.security != "Open":
                    r.security = e.security

    def _mark_connected(self, readings: List[Reading]) -> None:
        if any(r.connected for r in readings) or not self.interface:
            return
        try:
            out = run(["iw", "dev", self.interface, "link"], timeout=5, check=False)
        except WifiError:
            return
        m = re.search(r"Connected to ([0-9a-fA-F:]{17})", out)
        if m:
            for r in readings:
                if r.id == m.group(1).lower():
                    r.connected = True
