"""Signal-strength maths: unit conversion, quality ratings and rough distance estimates.

Distance from RSSI is an *estimate*. Walls, bodies, antenna orientation and
reflections can easily swing a reading by 10 dB, which is roughly a factor of
two in distance. Treat the numbers as "near / mid / far", not a tape measure.
"""

from __future__ import annotations

import math
from typing import Optional

from .models import BT, WIFI

# Typical transmit power of a home/office access point (dBm, ~100 mW).
DEFAULT_AP_TX_POWER = 20.0
# Real receivers lose roughly this much to antenna efficiency, polarisation and
# the user's body compared with ideal free space; phones read ~-30 dBm at 1 m.
WIFI_SYSTEM_LOSS = 10.0
# Typical BLE "measured power": the RSSI seen 1 m away from the transmitter.
DEFAULT_BLE_POWER_AT_1M = -59.0
# Path-loss exponent: 2.0 = open space, ~2.7-3.5 = indoors, 4+ = many walls.
DEFAULT_PATH_LOSS_EXPONENT = 3.0


def percent_to_dbm(percent: float, scheme: str = "networkmanager") -> float:
    """Convert a 0-100% signal value to an approximate dBm figure.

    networkmanager: NM maps -100..-40 dBm linearly onto 0..100%.
    windows:        Windows maps -100..-50 dBm linearly onto 0..100%.
    """
    p = max(0.0, min(100.0, float(percent)))
    if scheme == "windows":
        return -100.0 + p / 2.0
    return -100.0 + p * 0.6


def dbm_to_percent(dbm: float, floor: float = -100.0, ceiling: float = -30.0) -> int:
    """Map dBm onto 0-100 for drawing bars (not a standard: just for display)."""
    if dbm <= floor:
        return 0
    if dbm >= ceiling:
        return 100
    return int(round((dbm - floor) / (ceiling - floor) * 100))


# (lower bound in dBm, label, rich colour). The first matching row wins.
_WIFI_LEVELS = [
    (-50, "Excellent", "bright_green"),
    (-60, "Good", "green"),
    (-67, "Fair", "yellow"),
    (-75, "Weak", "dark_orange"),
    (-200, "Very weak", "red"),
]
# BLE radios transmit far less power than access points, so the scale is lower.
_BT_LEVELS = [
    (-55, "Very close", "bright_green"),
    (-67, "Near", "green"),
    (-80, "Medium", "yellow"),
    (-90, "Far", "dark_orange"),
    (-200, "Very far", "red"),
]


def quality(dbm: float, kind: str = WIFI) -> tuple[str, str]:
    """Return (label, colour) for a signal level."""
    levels = _BT_LEVELS if kind == BT else _WIFI_LEVELS
    for threshold, label, colour in levels:
        if dbm >= threshold:
            return label, colour
    return levels[-1][1], levels[-1][2]


def estimate_distance_wifi(
    rssi: float,
    frequency_mhz: Optional[int] = None,
    tx_power: float = DEFAULT_AP_TX_POWER,
    path_loss_exponent: float = DEFAULT_PATH_LOSS_EXPONENT,
) -> float:
    """Log-distance path-loss model anchored to free-space loss at 1 m.

    loss(d) = FSPL(1 m, f) + system losses + 10 * n * log10(d)
    """
    f = frequency_mhz or 2437
    loss_1m = 20 * math.log10(f) - 27.55 + WIFI_SYSTEM_LOSS  # FSPL at 1 m is ~40 dB at 2.4 GHz
    loss = tx_power - rssi
    return 10 ** ((loss - loss_1m) / (10 * path_loss_exponent))


def estimate_distance_ble(
    rssi: float,
    tx_power: Optional[int] = None,
    path_loss_exponent: float = DEFAULT_PATH_LOSS_EXPONENT,
) -> float:
    """Distance from a BLE advertisement.

    If the device advertises its TX power level (the power at the antenna), the
    1 m reference is that minus ~41 dB of free-space loss at 2.4 GHz. Otherwise a
    typical -59 dBm @ 1 m is assumed.
    """
    ref_1m = (tx_power - 41.0) if tx_power is not None else DEFAULT_BLE_POWER_AT_1M
    return 10 ** ((ref_1m - rssi) / (10 * path_loss_exponent))


def estimate_distance(reading, rssi: Optional[float] = None, path_loss_exponent: float = DEFAULT_PATH_LOSS_EXPONENT) -> float:
    value = reading.rssi if rssi is None else rssi
    if reading.kind == BT:
        return estimate_distance_ble(value, reading.tx_power, path_loss_exponent)
    return estimate_distance_wifi(value, reading.frequency, path_loss_exponent=path_loss_exponent)


def format_distance(metres: float) -> str:
    if metres < 1:
        return "<1 m"
    if metres < 10:
        return f"~{metres:.1f} m"
    if metres > 200:
        return ">200 m"
    return f"~{metres:.0f} m"
