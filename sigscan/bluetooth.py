"""Bluetooth scanning with bleak (BlueZ on Linux, CoreBluetooth on macOS, WinRT on Windows).

Every advertisement packet carries an RSSI, so BLE devices update many times a
second. Readings are averaged into one sample per device per `sample_interval`
seconds, which smooths the (very noisy) BLE RSSI and keeps history meaningful.
"""

from __future__ import annotations

import sys
import time
from statistics import mean
from typing import Callable, Dict, List, Optional

from .models import BT, Reading

# Bluetooth SIG company identifiers for some common manufacturers.
COMPANY_IDS = {
    0x0002: "Intel",
    0x0006: "Microsoft",
    0x000A: "Qualcomm",
    0x000D: "Texas Instruments",
    0x000F: "Broadcom",
    0x001D: "Qualcomm",
    0x0046: "MediaTek",
    0x004C: "Apple",
    0x0059: "Nordic Semi",
    0x005D: "Realtek",
    0x0075: "Samsung",
    0x0087: "Garmin",
    0x009E: "Bose",
    0x00C4: "LG",
    0x00E0: "Google",
    0x012D: "Sony",
    0x0157: "Huami (Amazfit)",
    0x0171: "Amazon",
    0x027D: "Huawei",
    0x02E5: "Espressif",
    0x038F: "Xiaomi",
    0x0499: "Ruuvi",
}

# Apple "Continuity" message types (first byte of Apple manufacturer data).
APPLE_TYPES = {
    0x02: "iBeacon",
    0x05: "AirDrop",
    0x07: "AirPods/Beats",
    0x09: "AirPlay",
    0x0C: "Handoff",
    0x0F: "Nearby action",
    0x10: "Apple device",
    0x12: "Find My tag",
}

# 16-bit service UUIDs that identify a device category.
SERVICE_HINTS = {
    "0000feed": "Tile tracker",
    "0000fd5a": "SmartTag",
    "0000fe2c": "Fast Pair",
    "0000180d": "Heart-rate sensor",
    "00001812": "Keyboard/Mouse",
    "0000fe9f": "Google device",
    "0000fd6f": "Exposure notif.",
}


def vendor_from_manufacturer(manufacturer_data: Dict[int, bytes]) -> Optional[str]:
    for company_id in manufacturer_data:
        if company_id in COMPANY_IDS:
            return COMPANY_IDS[company_id]
    if manufacturer_data:
        return f"0x{next(iter(manufacturer_data)):04X}"
    return None


def device_type_hint(manufacturer_data: Dict[int, bytes], service_uuids: List[str], platform_props=None) -> Optional[str]:
    # BlueZ exposes a freedesktop icon name such as "phone" or "audio-headset".
    if isinstance(platform_props, dict) and platform_props.get("Icon"):
        return str(platform_props["Icon"]).replace("audio-", "").replace("input-", "")
    apple = manufacturer_data.get(0x004C)
    if apple:
        return APPLE_TYPES.get(apple[0])
    for uuid in service_uuids or []:
        hint = SERVICE_HINTS.get(uuid.lower()[:8])
        if hint:
            return hint
    return None


def _clean_name(name: Optional[str], address: str) -> str:
    """BlueZ uses the address (with dashes) as the alias of unnamed devices; hide that."""
    if not name:
        return ""
    if name.replace("-", ":").upper() == address.upper():
        return ""
    return name


class BluetoothError(RuntimeError):
    pass


def _friendly_error(exc: Exception) -> str:
    text = str(exc) or type(exc).__name__
    low = text.lower()
    if "no bluetooth adapters" in low or ("adapter" in low and "not found" in low):
        return "No Bluetooth adapter found."
    if "notready" in low or "not ready" in low or "powered off" in low or "turned off" in low:
        return "Bluetooth is turned off. Turn it on and try again."
    if "unauthorized" in low or "not authorized" in low or "permission" in low:
        return "No permission to use Bluetooth. Allow your terminal app Bluetooth access in system settings."
    if "dbus" in low or "no such file" in low:
        return "Cannot reach the Bluetooth service (is bluetoothd running?)."
    return text


class BluetoothScanner:
    def __init__(
        self,
        on_reading: Callable[[Reading], None],
        adapter: Optional[str] = None,
        include_classic: bool = False,
        sample_interval: float = 1.0,
    ):
        self.on_reading = on_reading
        self.adapter = adapter
        self.include_classic = include_classic
        self.sample_interval = sample_interval
        self._scanner = None
        self._pending: Dict[str, List[float]] = {}
        self._last_emit: Dict[str, float] = {}

    def _scanner_kwargs(self) -> dict:
        kwargs: dict = {}
        if sys.platform.startswith("linux"):
            try:
                from bleak.args.bluez import BlueZScannerArgs
            except ImportError:  # older bleak
                from bleak.backends.bluezdbus.scanner import BlueZScannerArgs

            # DuplicateData=True asks BlueZ to report every advertisement, so the
            # RSSI keeps updating instead of being reported once per device.
            filters = {"DuplicateData": True}
            if self.include_classic:
                filters["Transport"] = "auto"
            bluez: dict = {"filters": filters}
            if self.adapter:
                if "adapter" in getattr(BlueZScannerArgs, "__annotations__", {}):
                    bluez["adapter"] = self.adapter
                else:  # bleak < 3
                    kwargs["adapter"] = self.adapter
            kwargs["bluez"] = bluez
        return kwargs

    async def start(self) -> None:
        try:
            from bleak import BleakScanner
        except ImportError as e:
            raise BluetoothError("The 'bleak' package is not installed (pip install bleak).") from e
        try:
            self._scanner = BleakScanner(detection_callback=self._on_advertisement, **self._scanner_kwargs())
            await self._scanner.start()
        except Exception as e:
            self._scanner = None
            raise BluetoothError(_friendly_error(e)) from e

    async def stop(self) -> None:
        if self._scanner is not None:
            try:
                await self._scanner.stop()
            except Exception:
                pass
            self._scanner = None

    def _on_advertisement(self, device, adv) -> None:
        rssi = adv.rssi
        if rssi is None or rssi <= -127 or rssi >= 20:  # -127 = "not available"
            return
        addr = device.address
        bucket = self._pending.setdefault(addr, [])
        bucket.append(float(rssi))
        now = time.time()
        if now - self._last_emit.get(addr, 0.0) < self.sample_interval:
            return
        value = mean(bucket)
        bucket.clear()
        self._last_emit[addr] = now

        props = None
        if isinstance(adv.platform_data, tuple) and len(adv.platform_data) > 1:
            props = adv.platform_data[1]
        self.on_reading(
            Reading(
                kind=BT,
                id=addr,
                name=_clean_name(adv.local_name or device.name, addr),
                rssi=round(value, 1),
                timestamp=now,
                tx_power=adv.tx_power,
                vendor=vendor_from_manufacturer(adv.manufacturer_data),
                device_type=device_type_hint(adv.manufacturer_data, adv.service_uuids, props),
            )
        )
