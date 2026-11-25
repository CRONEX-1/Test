"""Simulated Wi-Fi and Bluetooth sources for `--demo` (no radio hardware needed)."""

from __future__ import annotations

import asyncio
import math
import random
import time
from typing import Callable, List, Optional

from .models import BT, WIFI, Reading, channel_for_frequency

_WIFI = [
    # ssid, bssid, freq, base dBm, security
    ("HomeNet", "a4:2b:b0:11:22:33", 5180, -42, "WPA2/WPA3"),
    ("HomeNet", "a4:2b:b0:11:22:34", 2437, -47, "WPA2/WPA3"),
    ("HomeNet-IoT", "a4:2b:b0:11:22:35", 2437, -49, "WPA2"),
    ("Neighbour_5G", "f0:9f:c2:aa:bb:01", 5745, -68, "WPA2"),
    ("Neighbour", "f0:9f:c2:aa:bb:02", 2412, -63, "WPA2"),
    ("CoffeeShop Guest", "00:1a:1e:0c:0d:0e", 2462, -77, "Open"),
    ("DIRECT-7B-Printer", "9c:b6:d0:44:55:66", 2437, -71, "WPA2"),
    ("TP-Link_8F3C", "50:c7:bf:8f:3c:10", 2412, -84, "WPA2"),
    ("Office-6E", "3c:37:12:00:00:aa", 6115, -73, "WPA3"),
    ("", "3c:37:12:00:00:ab", 5500, -86, "WPA2"),
    ("xfinitywifi", "e8:ed:05:12:34:56", 2462, -88, "Open"),
]

_BT = [
    # name, address, vendor, type, base dBm, tx_power
    ("Pixel 9", "4c:11:ae:10:20:30", "Google", "phone", -58, None),
    ("", "6a:3f:91:c2:05:77", "Apple", "Find My tag", -66, None),
    ("WH-1000XM5", "f8:4e:17:aa:bb:cc", "Sony", "headset", -61, -4),
    ("MX Keys", "d4:2a:7e:01:02:03", None, "keyboard", -53, 4),
    ("", "7c:d1:c3:9e:aa:01", "Apple", "AirPods/Beats", -72, None),
    ("Galaxy Watch6", "b0:4a:6a:77:88:99", "Samsung", "watch", -79, None),
    ("Mi Band 8", "e4:5f:01:23:45:67", "Xiaomi", None, -83, None),
    ("RuuviTag 3F2A", "c9:3f:2a:10:00:01", "Ruuvi", None, -88, 4),
    ("", "5e:aa:10:42:13:9d", "Microsoft", None, -91, None),
    ("[TV] Living Room", "8c:79:f5:00:11:22", "Samsung", "tv", -75, None),
    ("ESP32-Sensor", "24:0a:c4:de:ad:01", "Espressif", None, -69, 9),
]


class _Walker:
    """A noisy random walk around a base value, optionally with a slow swing."""

    def __init__(self, base: float, noise: float, swing: float = 0.0, period: float = 40.0, rng=None):
        self.base, self.noise, self.swing, self.period = base, noise, swing, period
        self.drift = 0.0
        self.phase = (rng or random).random() * period
        self.rng = rng or random

    def sample(self, t: float) -> float:
        self.drift = max(-6.0, min(6.0, self.drift + self.rng.gauss(0, 0.6)))
        wave = self.swing * math.sin(2 * math.pi * (t + self.phase) / self.period)
        return max(-99.0, min(-25.0, self.base + self.drift + wave + self.rng.gauss(0, self.noise)))


class DemoWifiScanner:
    name = "demo"

    def __init__(self, seed: Optional[int] = None):
        self.rng = random.Random(seed)
        self.walkers = {b: _Walker(base, 1.5, rng=self.rng) for _, b, _, base, _ in _WIFI}
        self.first = True

    def scan(self) -> List[Reading]:
        if not self.first:
            time.sleep(1.5)  # pretend to scan
        self.first = False
        now = time.time()
        out = []
        for ssid, bssid, freq, _, sec in _WIFI:
            if self.rng.random() < 0.08:  # weak networks are sometimes missed
                continue
            out.append(
                Reading(
                    kind=WIFI,
                    id=bssid,
                    name=ssid,
                    rssi=round(self.walkers[bssid].sample(now)),
                    timestamp=now,
                    channel=channel_for_frequency(freq),
                    frequency=freq,
                    security=sec,
                    connected=bssid.endswith("33"),
                )
            )
        return out


class DemoBluetoothScanner:
    def __init__(self, on_reading: Callable[[Reading], None], seed: Optional[int] = None, sample_interval: float = 1.0):
        self.on_reading = on_reading
        self.rng = random.Random(seed)
        self.sample_interval = sample_interval
        # The first device swings strongly, as if it is being carried around.
        self.walkers = {
            d[1]: _Walker(d[4], 3.0, swing=14.0 if i == 0 else 0.0, period=30.0, rng=self.rng)
            for i, d in enumerate(_BT)
        }
        self._task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        self._emit_all()
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    def _emit_all(self) -> None:
        now = time.time()
        for name, addr, vendor, dtype, _, tx in _BT:
            if self.rng.random() < 0.15:
                continue
            self.on_reading(
                Reading(
                    kind=BT,
                    id=addr.upper(),
                    name=name,
                    rssi=round(self.walkers[addr].sample(now), 1),
                    timestamp=now,
                    vendor=vendor,
                    device_type=dtype,
                    tx_power=tx,
                )
            )

    async def _run(self) -> None:
        while True:
            await asyncio.sleep(self.sample_interval)
            self._emit_all()
