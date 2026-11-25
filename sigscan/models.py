"""Core data types: a single signal reading and per-device rolling statistics."""

from __future__ import annotations

import time
from collections import deque
from dataclasses import asdict, dataclass, field
from statistics import mean
from typing import Deque, Dict, Iterable, List, Optional, Tuple

WIFI = "wifi"
BT = "bt"


@dataclass
class Reading:
    """One observation of one transmitter (an access point or a Bluetooth device)."""

    kind: str  # WIFI or BT
    id: str  # BSSID for Wi-Fi, address (or macOS UUID) for Bluetooth
    name: str  # SSID for Wi-Fi, advertised name for Bluetooth
    rssi: float  # received signal strength in dBm
    timestamp: float = field(default_factory=time.time)
    channel: Optional[int] = None
    frequency: Optional[int] = None  # MHz
    security: Optional[str] = None
    connected: bool = False
    rssi_estimated: bool = False  # True when dBm was converted from a 0-100% value
    noise: Optional[float] = None  # dBm, when the platform reports it
    tx_power: Optional[int] = None  # advertised BLE TX power level, dBm
    vendor: Optional[str] = None
    device_type: Optional[str] = None

    @property
    def band(self) -> Optional[str]:
        return band_for_frequency(self.frequency)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["band"] = self.band
        return d


def band_for_frequency(freq: Optional[int]) -> Optional[str]:
    if not freq:
        return None
    if freq < 3000:
        return "2.4G"
    if freq < 5925:
        return "5G"
    return "6G"


def channel_for_frequency(freq: Optional[float]) -> Optional[int]:
    """Convert a Wi-Fi centre frequency (MHz) to its channel number."""
    if not freq:
        return None
    f = int(round(freq))
    if f == 2484:
        return 14
    if 2412 <= f <= 2472:
        return (f - 2407) // 5
    if 5955 <= f <= 7115:  # 6 GHz band
        return (f - 5950) // 5
    if f == 5935:
        return 2
    if 5000 <= f <= 5925:
        return (f - 5000) // 5
    if 4910 <= f <= 4980:
        return (f - 4000) // 5
    return None


def frequency_for_channel(channel: Optional[int], band: Optional[str] = None) -> Optional[int]:
    """Best-effort inverse of channel_for_frequency. `band` is '2.4G', '5G' or '6G'."""
    if not channel:
        return None
    if band == "6G":
        return 5950 + channel * 5
    if band == "5G" or (band is None and channel > 14):
        return 5000 + channel * 5
    if channel == 14:
        return 2484
    if 1 <= channel <= 13:
        return 2407 + channel * 5
    return None


class DeviceStats:
    """Rolling statistics for one transmitter across many readings."""

    def __init__(self, reading: Reading, history: int = 120):
        self.latest = reading
        self.first_seen = reading.timestamp
        self.count = 0
        self.min = reading.rssi
        self.max = reading.rssi
        self.history: Deque[Tuple[float, float]] = deque(maxlen=history)
        self.add(reading)

    @property
    def key(self) -> Tuple[str, str]:
        return (self.latest.kind, self.latest.id)

    @property
    def last_seen(self) -> float:
        return self.latest.timestamp

    def add(self, reading: Reading) -> None:
        # Keep useful metadata if a later reading lacks it (e.g. BLE name arrives late).
        prev = self.latest
        if prev is not reading:
            for attr in ("name", "vendor", "device_type", "tx_power", "security", "channel", "frequency"):
                if not getattr(reading, attr) and getattr(prev, attr):
                    setattr(reading, attr, getattr(prev, attr))
        self.latest = reading
        self.count += 1
        self.min = min(self.min, reading.rssi)
        self.max = max(self.max, reading.rssi)
        self.history.append((reading.timestamp, reading.rssi))

    @property
    def rssi(self) -> float:
        return self.latest.rssi

    def values(self) -> List[float]:
        return [v for _, v in self.history]

    def average(self, window: int = 10) -> float:
        vals = self.values()[-window:]
        return mean(vals) if vals else self.rssi

    def smoothed(self) -> float:
        """Average of the last few readings: steadier than the raw value for distance."""
        return self.average(5)

    def trend(self, window: int = 3) -> float:
        """dB change between the newest `window` readings and the `window` before them.

        Positive means the signal is getting stronger (you are probably getting closer).
        """
        vals = self.values()
        if len(vals) < window + 1:
            return 0.0
        recent = vals[-window:]
        before = vals[-2 * window : -window] or vals[:1]
        return mean(recent) - mean(before)

    def age(self, now: Optional[float] = None) -> float:
        return (now or time.time()) - self.last_seen


class Tracker:
    """Collects readings from every scanner and keeps stats per device."""

    def __init__(self, history: int = 120, stale_after: float = 60.0):
        self.history = history
        self.stale_after = stale_after
        self._devices: Dict[Tuple[str, str], DeviceStats] = {}
        self.listeners = []  # callables invoked with every new Reading

    def update(self, reading: Reading) -> DeviceStats:
        key = (reading.kind, reading.id)
        stats = self._devices.get(key)
        if stats is None:
            stats = DeviceStats(reading, history=self.history)
            self._devices[key] = stats
        else:
            stats.add(reading)
        for listener in self.listeners:
            listener(reading)
        return stats

    def update_many(self, readings: Iterable[Reading]) -> None:
        for r in readings:
            self.update(r)

    def devices(self, kind: Optional[str] = None, include_stale: bool = False) -> List[DeviceStats]:
        now = time.time()
        out = [
            d
            for d in self._devices.values()
            if (kind is None or d.latest.kind == kind)
            and (include_stale or d.age(now) <= self.stale_after)
        ]
        out.sort(key=lambda d: d.smoothed(), reverse=True)
        return out

    def get(self, kind: str, id: str) -> Optional[DeviceStats]:
        return self._devices.get((kind, id))

    def find(self, query: str, kind: Optional[str] = None) -> List[DeviceStats]:
        """Devices whose name or id contains `query` (case-insensitive), strongest first."""
        q = query.lower()
        return [
            d
            for d in self.devices(kind, include_stale=True)
            if q in d.latest.id.lower() or q in (d.latest.name or "").lower()
        ]
