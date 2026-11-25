"""Windows Wi-Fi scanning.

Preferred: the native WLAN API (wlanapi.dll) via ctypes. It can trigger a
fresh scan and reports the exact RSSI in dBm for every BSSID.
Fallback:  parsing `netsh wlan show networks mode=bssid` (signal in %).

On Windows 11 24H2 and later, Wi-Fi scan results are only returned when
Location services are on and desktop apps may use location:
Settings > Privacy & security > Location.
"""

from __future__ import annotations

import ctypes
import re
import struct
import time
from ctypes import POINTER, Structure, byref, c_int32, c_uint16, c_uint32, c_uint64, c_ubyte, c_void_p, c_wchar
from typing import List, Optional

from ..estimate import percent_to_dbm
from ..models import WIFI, Reading, channel_for_frequency, frequency_for_channel
from . import WifiError, WifiScanner, run

# --------------------------------------------------------------------------- #
# Information-element parsing (pure Python, platform independent)
# --------------------------------------------------------------------------- #

_SAE_AKMS = {8, 9, 24, 25}
_ENTERPRISE_AKMS = {1, 3, 5, 11, 12, 13}
_OWE_AKM = 18


def _rsn_akms(body: bytes) -> List[int]:
    """AKM suite types (00-0F-AC:x) from an RSN information element body."""
    try:
        off = 2 + 4  # version, group cipher
        (n_pairwise,) = struct.unpack_from("<H", body, off)
        off += 2 + 4 * n_pairwise
        (n_akm,) = struct.unpack_from("<H", body, off)
        off += 2
        akms = []
        for _ in range(n_akm):
            suite = body[off : off + 4]
            off += 4
            if len(suite) == 4 and suite[:3] == b"\x00\x0f\xac":
                akms.append(suite[3])
        return akms
    except struct.error:
        return []


def security_from_ies(ies: bytes, privacy: bool = False) -> str:
    rsn: Optional[bytes] = None
    wpa = False
    i = 0
    while i + 2 <= len(ies):
        eid, length = ies[i], ies[i + 1]
        body = ies[i + 2 : i + 2 + length]
        i += 2 + length
        if eid == 48:
            rsn = body
        elif eid == 221 and body[:4] == b"\x00\x50\xf2\x01":
            wpa = True
    if rsn is not None:
        akms = set(_rsn_akms(rsn))
        if _OWE_AKM in akms:
            return "OWE"
        sae = bool(akms & _SAE_AKMS)
        if akms & _ENTERPRISE_AKMS:
            return "WPA3-Ent" if 12 in akms or 13 in akms else "WPA2-Ent"
        if sae and akms - _SAE_AKMS:
            return "WPA2/WPA3"
        if sae:
            return "WPA3"
        return "WPA/WPA2" if wpa else "WPA2"
    if wpa:
        return "WPA"
    return "WEP" if privacy else "Open"


# --------------------------------------------------------------------------- #
# wlanapi.dll structures. Fixed-width types keep the layout identical to the
# Windows headers (c_wchar is 2 bytes on Windows, which is the only place the
# structs containing it are used).
# --------------------------------------------------------------------------- #


class GUID(Structure):
    _fields_ = [("Data1", c_uint32), ("Data2", c_uint16), ("Data3", c_uint16), ("Data4", c_ubyte * 8)]


class WLAN_INTERFACE_INFO(Structure):
    _fields_ = [("InterfaceGuid", GUID), ("strInterfaceDescription", c_wchar * 256), ("isState", c_uint32)]


class WLAN_INTERFACE_INFO_LIST(Structure):
    _fields_ = [("dwNumberOfItems", c_uint32), ("dwIndex", c_uint32), ("InterfaceInfo", WLAN_INTERFACE_INFO * 1)]


class DOT11_SSID(Structure):
    _fields_ = [("uSSIDLength", c_uint32), ("ucSSID", c_ubyte * 32)]


class WLAN_RATE_SET(Structure):
    _fields_ = [("uRateSetLength", c_uint32), ("usRateSet", c_uint16 * 126)]


class WLAN_BSS_ENTRY(Structure):
    _fields_ = [
        ("dot11Ssid", DOT11_SSID),
        ("uPhyId", c_uint32),
        ("dot11Bssid", c_ubyte * 6),
        ("dot11BssType", c_uint32),
        ("dot11BssPhyType", c_uint32),
        ("lRssi", c_int32),
        ("uLinkQuality", c_uint32),
        ("bInRegDomain", c_ubyte),
        ("usBeaconPeriod", c_uint16),
        ("ullTimestamp", c_uint64),
        ("ullHostTimestamp", c_uint64),
        ("usCapabilityInformation", c_uint16),
        ("ulChCenterFrequency", c_uint32),  # kHz
        ("wlanRateSet", WLAN_RATE_SET),
        ("ulIeOffset", c_uint32),
        ("ulIeSize", c_uint32),
    ]


class WLAN_BSS_LIST(Structure):
    _fields_ = [("dwTotalSize", c_uint32), ("dwNumberOfItems", c_uint32), ("wlanBssEntries", WLAN_BSS_ENTRY * 1)]


class WLAN_CONNECTION_ATTRIBUTES_HEAD(Structure):
    """Leading part of WLAN_CONNECTION_ATTRIBUTES, enough to read the connected BSSID."""

    _fields_ = [
        ("isState", c_uint32),
        ("wlanConnectionMode", c_uint32),
        ("strProfileName", c_wchar * 256),
        ("dot11Ssid", DOT11_SSID),
        ("dot11BssType", c_uint32),
        ("dot11Bssid", c_ubyte * 6),
    ]


DOT11_BSS_TYPE_ANY = 3
WLAN_INTF_OPCODE_CURRENT_CONNECTION = 7
ERROR_ACCESS_DENIED = 5
ERROR_SERVICE_NOT_ACTIVE = 1062
CAPABILITY_PRIVACY = 0x0010

_LOCATION_HINT = (
    "Windows blocked the Wi-Fi scan. Turn on Location services and 'Let desktop apps "
    "access your location' in Settings > Privacy & security > Location."
)


def _mac(raw) -> str:
    return ":".join(f"{b:02x}" for b in raw)


class WlanApi:
    def __init__(self):
        self.dll = ctypes.WinDLL("wlanapi.dll")  # type: ignore[attr-defined]
        self.handle = c_void_p()
        version = c_uint32()
        rc = self.dll.WlanOpenHandle(2, None, byref(version), byref(self.handle))
        if rc == ERROR_SERVICE_NOT_ACTIVE:
            raise WifiError("the WLAN AutoConfig service is not running (no Wi-Fi adapter?)")
        if rc:
            raise WifiError(f"WlanOpenHandle failed with error {rc}")

    def interfaces(self) -> List[tuple]:
        ptr = POINTER(WLAN_INTERFACE_INFO_LIST)()
        rc = self.dll.WlanEnumInterfaces(self.handle, None, byref(ptr))
        if rc:
            raise WifiError(f"WlanEnumInterfaces failed with error {rc}")
        try:
            lst = ptr.contents
            base = ctypes.addressof(lst.InterfaceInfo)
            out = []
            for i in range(lst.dwNumberOfItems):
                info = WLAN_INTERFACE_INFO.from_address(base + i * ctypes.sizeof(WLAN_INTERFACE_INFO))
                guid = GUID()
                ctypes.memmove(byref(guid), byref(info.InterfaceGuid), ctypes.sizeof(GUID))
                out.append((guid, info.strInterfaceDescription))
            return out
        finally:
            self.dll.WlanFreeMemory(ptr)

    def trigger_scan(self, guid: GUID) -> None:
        self.dll.WlanScan(self.handle, byref(guid), None, None, None)

    def connected_bssid(self, guid: GUID) -> Optional[str]:
        size = c_uint32()
        data = c_void_p()
        rc = self.dll.WlanQueryInterface(
            self.handle, byref(guid), WLAN_INTF_OPCODE_CURRENT_CONNECTION, None, byref(size), byref(data), None
        )
        if rc or not data:
            return None
        try:
            attrs = WLAN_CONNECTION_ATTRIBUTES_HEAD.from_address(data.value)
            return _mac(attrs.dot11Bssid)
        finally:
            self.dll.WlanFreeMemory(data)

    def bss_list(self, guid: GUID) -> List[Reading]:
        ptr = POINTER(WLAN_BSS_LIST)()
        rc = self.dll.WlanGetNetworkBssList(self.handle, byref(guid), None, DOT11_BSS_TYPE_ANY, False, None, byref(ptr))
        if rc == ERROR_ACCESS_DENIED:
            raise WifiError(_LOCATION_HINT)
        if rc:
            raise WifiError(f"WlanGetNetworkBssList failed with error {rc}")
        now = time.time()
        readings = []
        try:
            lst = ptr.contents
            base = ctypes.addressof(lst.wlanBssEntries)
            step = ctypes.sizeof(WLAN_BSS_ENTRY)
            for i in range(lst.dwNumberOfItems):
                addr = base + i * step
                e = WLAN_BSS_ENTRY.from_address(addr)
                ssid = bytes(e.dot11Ssid.ucSSID[: min(e.dot11Ssid.uSSIDLength, 32)]).decode("utf-8", "replace")
                ies = ctypes.string_at(addr + e.ulIeOffset, e.ulIeSize) if e.ulIeSize else b""
                freq = int(round(e.ulChCenterFrequency / 1000)) or None
                readings.append(
                    Reading(
                        kind=WIFI,
                        id=_mac(e.dot11Bssid),
                        name=ssid,
                        rssi=float(e.lRssi),
                        timestamp=now,
                        channel=channel_for_frequency(freq),
                        frequency=freq,
                        security=security_from_ies(ies, bool(e.usCapabilityInformation & CAPABILITY_PRIVACY)),
                    )
                )
        finally:
            self.dll.WlanFreeMemory(ptr)
        return readings


# --------------------------------------------------------------------------- #
# netsh fallback
# --------------------------------------------------------------------------- #

_SSID_RE = re.compile(r"^SSID\s+\d+\s*:\s?(.*)$")
_BSSID_RE = re.compile(r"^BSSID\s+\d+\s*:\s*([0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5})")
_KV_RE = re.compile(r"^([^:]+?)\s*:\s*(.*)$")


def parse_netsh(output: str, now: Optional[float] = None) -> List[Reading]:
    """Parse `netsh wlan show networks mode=bssid`.

    Labels are localised on non-English Windows, so values are recognised by
    shape where possible: the signal is the "NN%" value and the channel is the
    first plain integer inside a BSSID block.
    """
    now = now or time.time()
    readings: List[Reading] = []
    ssid = ""
    auth: Optional[str] = None
    ssid_kv_index = 0
    cur: Optional[dict] = None

    def flush():
        if cur and cur.get("signal") is not None:
            band = cur.get("band")
            readings.append(
                Reading(
                    kind=WIFI,
                    id=cur["bssid"],
                    name=ssid,
                    rssi=round(percent_to_dbm(cur["signal"], "windows")),
                    timestamp=now,
                    channel=cur.get("channel"),
                    frequency=frequency_for_channel(cur.get("channel"), band),
                    security=auth,
                    rssi_estimated=True,
                )
            )

    for raw in output.splitlines():
        line = raw.strip()
        if not line:
            continue
        m = _SSID_RE.match(line)
        if m:
            flush()
            cur = None
            ssid, auth, ssid_kv_index = m.group(1).strip(), None, 0
            continue
        m = _BSSID_RE.match(line)
        if m:
            flush()
            cur = {"bssid": m.group(1).lower()}
            continue
        kv = _KV_RE.match(line)
        if not kv:
            continue
        key, value = kv.group(1).strip().lower(), kv.group(2).strip()
        if cur is None:
            # Network-level keys: Network type, Authentication, Encryption.
            ssid_kv_index += 1
            if "auth" in key or (auth is None and ssid_kv_index == 2):
                auth = value.replace("-Personal", "").replace("-Enterprise", "-Ent")
            continue
        pct = re.fullmatch(r"(\d{1,3})\s*%", value)
        if pct and "signal" not in cur:
            cur["signal"] = int(pct.group(1))
        elif re.fullmatch(r"\d{1,3}", value) and "channel" not in cur:
            cur["channel"] = int(value)
        elif re.fullmatch(r"[256](?:[.,]4)?\s*GHz", value):
            cur["band"] = {"2": "2.4G", "5": "5G", "6": "6G"}[value[0]]
    flush()
    return readings


class WindowsWifiScanner(WifiScanner):
    name = "windows"
    SCAN_WAIT = 4.0  # seconds; WlanScan is asynchronous and usually finishes within 4 s

    def __init__(self, interface: Optional[str] = None):
        self.interface = interface
        self._api: Optional[WlanApi] = None
        self._api_error: Optional[str] = None
        try:
            self._api = WlanApi()
        except Exception as e:  # missing DLL, service stopped, ...
            self._api_error = str(e)

    def scan(self) -> List[Reading]:
        if self._api is not None:
            try:
                return self._scan_api()
            except WifiError as e:
                if "Location" in str(e):
                    raise
                self._api_error = str(e)
        readings = parse_netsh(run(["netsh", "wlan", "show", "networks", "mode=bssid"], timeout=20))
        if not readings and self._api_error:
            raise WifiError(self._api_error)
        return readings

    def _scan_api(self) -> List[Reading]:
        api = self._api
        assert api is not None
        ifaces = api.interfaces()
        if self.interface:
            ifaces = [i for i in ifaces if self.interface.lower() in i[1].lower()]
        if not ifaces:
            raise WifiError("no Wi-Fi adapter found")
        for guid, _ in ifaces:
            api.trigger_scan(guid)
        time.sleep(self.SCAN_WAIT)
        readings: List[Reading] = []
        for guid, _ in ifaces:
            connected = api.connected_bssid(guid)
            for r in api.bss_list(guid):
                r.connected = r.id == connected
                readings.append(r)
        return readings
