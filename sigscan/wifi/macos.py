"""macOS Wi-Fi scanning.

Preferred: Apple's CoreWLAN framework through PyObjC (exact RSSI, BSSID, noise).
Fallback:  `system_profiler SPAirPortDataType -json` (slower, no BSSIDs).

Since macOS 14 (Sonoma), SSIDs and BSSIDs are hidden unless the app running
the scan (your terminal, e.g. Terminal or iTerm) has Location Services access:
System Settings > Privacy & Security > Location Services.
"""

from __future__ import annotations

import json
import re
import time
from typing import List, Optional

from ..models import WIFI, Reading, frequency_for_channel
from . import WifiError, WifiScanner, run

# CWChannelBand values
_CW_BANDS = {1: "2.4G", 2: "5G", 3: "6G"}


class MacWifiScanner(WifiScanner):
    name = "macos"

    def __init__(self, interface: Optional[str] = None):
        self.interface_name = interface
        self._cw_iface = None
        try:
            import CoreWLAN  # type: ignore

            client = CoreWLAN.CWWiFiClient.sharedWiFiClient()
            self._cw_iface = (
                client.interfaceWithName_(interface) if interface else client.interface()
            )
        except Exception:
            self._cw_iface = None

    def scan(self) -> List[Reading]:
        if self._cw_iface is not None:
            try:
                return self._scan_corewlan()
            except Exception:
                pass
        return parse_system_profiler(run(["system_profiler", "SPAirPortDataType", "-json"], timeout=60))

    def _scan_corewlan(self) -> List[Reading]:
        iface = self._cw_iface
        networks, error = iface.scanForNetworksWithName_error_(None, None)
        if networks is None:
            raise WifiError(f"CoreWLAN scan failed: {error}")
        current_bssid = iface.bssid()
        current_ssid = iface.ssid()
        now = time.time()
        readings = []
        for i, net in enumerate(networks.allObjects()):
            ch = net.wlanChannel()
            channel = int(ch.channelNumber()) if ch is not None else None
            band = _CW_BANDS.get(int(ch.channelBand())) if ch is not None else None
            ssid = net.ssid() or ""
            bssid = (net.bssid() or "").lower()
            ident = bssid or f"{ssid or 'hidden'}@ch{channel}#{i}"
            readings.append(
                Reading(
                    kind=WIFI,
                    id=ident,
                    name=ssid or "<hidden: allow Location access>",
                    rssi=float(net.rssiValue()),
                    noise=float(net.noiseMeasurement()),
                    timestamp=now,
                    channel=channel,
                    frequency=frequency_for_channel(channel, band),
                    security=_corewlan_security(net),
                    connected=bool(bssid and current_bssid and bssid == current_bssid.lower())
                    or bool(ssid and ssid == current_ssid and not bssid),
                )
            )
        return readings


# CWSecurity enum values, most specific first: a WPA2/WPA3 transition network
# also "supports" plain WPA3 and WPA2, so it has to be checked before them.
_CW_SECURITY_CHECKS = [
    (13, "WPA2/WPA3"),  # kCWSecurityWPA3Transition
    (12, "WPA3-Ent"),  # kCWSecurityWPA3Enterprise
    (11, "WPA3"),  # kCWSecurityWPA3Personal
    (9, "WPA2-Ent"),  # kCWSecurityWPA2Enterprise
    (4, "WPA2"),  # kCWSecurityWPA2Personal
    (3, "WPA/WPA2"),  # kCWSecurityWPAPersonalMixed
    (7, "WPA-Ent"),  # kCWSecurityWPAEnterprise
    (2, "WPA"),  # kCWSecurityWPAPersonal
    (15, "OWE"),  # kCWSecurityOWETransition
    (14, "OWE"),  # kCWSecurityOWE
    (6, "WEP"),  # kCWSecurityDynamicWEP
    (1, "WEP"),  # kCWSecurityWEP
]


def _corewlan_security(net) -> str:
    try:
        for value, label in _CW_SECURITY_CHECKS:
            if net.supportsSecurity_(value):
                return label
    except Exception:
        return "?"
    return "Open"


_SIGNAL_RE = re.compile(r"(-?\d+)\s*dBm\s*/\s*(-?\d+)\s*dBm")
_CHANNEL_RE = re.compile(r"(\d+)\s*(?:\((\d)GHz)?")


def _parse_channel(text) -> tuple:
    m = _CHANNEL_RE.match(str(text or ""))
    if not m:
        return None, None
    channel = int(m.group(1))
    band = {"2": "2.4G", "5": "5G", "6": "6G"}.get(m.group(2) or "")
    return channel, band


def _security_label(raw: Optional[str]) -> str:
    if not raw:
        return "?"
    s = raw.replace("spairport_security_mode_", "").replace("pairport_security_mode_", "")
    s = s.replace("_", " ").strip()
    if s in ("none", "open"):
        return "Open"
    return s.upper().replace(" PERSONAL", "").replace(" ENTERPRISE", "-Ent").replace(" MIXED", "")


def parse_system_profiler(output: str, now: Optional[float] = None) -> List[Reading]:
    now = now or time.time()
    try:
        data = json.loads(output)
    except json.JSONDecodeError as e:
        raise WifiError(f"could not parse system_profiler output: {e}") from e
    readings: List[Reading] = []
    for section in data.get("SPAirPortDataType", []):
        for iface in section.get("spairport_airport_interfaces", []):
            entries = []
            current = iface.get("spairport_current_network_information")
            if isinstance(current, dict):
                entries.append((current, True))
            for net in iface.get("spairport_airport_other_local_wireless_networks", []) or []:
                entries.append((net, False))
            for i, (net, connected) in enumerate(entries):
                m = _SIGNAL_RE.search(str(net.get("spairport_signal_noise", "")))
                if not m:
                    continue
                channel, band = _parse_channel(net.get("spairport_network_channel"))
                ssid = net.get("_name", "")
                readings.append(
                    Reading(
                        kind=WIFI,
                        id=f"{ssid}@ch{channel}" if ssid and ssid != "<redacted>" else f"hidden@ch{channel}#{i}",
                        name=ssid,
                        rssi=float(m.group(1)),
                        noise=float(m.group(2)),
                        timestamp=now,
                        channel=channel,
                        frequency=frequency_for_channel(channel, band),
                        security=_security_label(net.get("spairport_security_mode")),
                        connected=connected,
                    )
                )
    if not readings and '"spairport_airport_interfaces"' not in output:
        raise WifiError("no Wi-Fi interface found by system_profiler")
    return readings
