import asyncio
import json
import time
from types import SimpleNamespace

import pytest

from sigscan.bluetooth import BluetoothScanner, device_type_hint, vendor_from_manufacturer
from sigscan.cli import build_parser, main
from sigscan.estimate import (
    dbm_to_percent,
    estimate_distance_ble,
    estimate_distance_wifi,
    format_distance,
    percent_to_dbm,
    quality,
)
from sigscan.models import BT, WIFI, Reading, Tracker, channel_for_frequency, frequency_for_channel
from sigscan.ui import Col, fit_columns


@pytest.mark.parametrize(
    "freq,channel",
    [(2412, 1), (2437, 6), (2472, 13), (2484, 14), (5180, 36), (5745, 149), (5955, 1), (6115, 33)],
)
def test_channel_frequency_round_trip(freq, channel):
    assert channel_for_frequency(freq) == channel
    band = "6G" if freq > 5925 else ("5G" if freq > 3000 else "2.4G")
    assert frequency_for_channel(channel, band) == freq


def test_percent_conversions():
    assert percent_to_dbm(100) == -40 and percent_to_dbm(0) == -100
    assert percent_to_dbm(100, "windows") == -50 and percent_to_dbm(50, "windows") == -75
    assert dbm_to_percent(-120) == 0 and dbm_to_percent(-20) == 100


def test_quality_scales():
    assert quality(-45, WIFI)[0] == "Excellent"
    assert quality(-85, WIFI)[0] == "Very weak"
    assert quality(-50, BT)[0] == "Very close"
    assert quality(-95, BT)[0] == "Very far"


def test_distance_grows_as_signal_weakens():
    d = [estimate_distance_wifi(r, 2437) for r in (-40, -55, -70, -85)]
    assert d == sorted(d)
    assert estimate_distance_ble(-59) == pytest.approx(1.0)
    assert estimate_distance_ble(-89, path_loss_exponent=3.0) == pytest.approx(10.0)
    # A device advertising a higher TX power is farther away for the same RSSI.
    assert estimate_distance_ble(-70, tx_power=4) > estimate_distance_ble(-70, tx_power=-12)
    assert format_distance(0.5) == "<1 m" and format_distance(3.14) == "~3.1 m" and format_distance(500) == ">200 m"


def _r(kind, id, rssi, t, name=""):
    return Reading(kind=kind, id=id, name=name, rssi=rssi, timestamp=t)


def test_tracker_stats_trend_and_find():
    tr = Tracker(stale_after=60)
    now = time.time()
    for i, v in enumerate([-80, -80, -80, -70, -65, -60]):
        tr.update(_r(BT, "AA:BB", v, now - 6 + i, name="Pixel 9" if i == 0 else ""))
    d = tr.get(BT, "AA:BB")
    assert d.count == 6 and d.min == -80 and d.max == -60
    assert d.trend() > 10  # getting stronger
    assert d.latest.name == "Pixel 9"  # name kept from an earlier advertisement
    assert tr.find("pixel")[0] is d and tr.find("aa:bb")[0] is d and tr.find("nope") == []


def test_tracker_hides_stale_devices_and_sorts_strongest_first():
    tr = Tracker(stale_after=10)
    now = time.time()
    tr.update(_r(WIFI, "old", -40, now - 60))
    tr.update(_r(WIFI, "weak", -80, now))
    tr.update(_r(WIFI, "strong", -50, now))
    assert [d.latest.id for d in tr.devices()] == ["strong", "weak"]
    assert len(tr.devices(include_stale=True)) == 3


def test_bluetooth_callback_filters_averages_and_throttles():
    got = []
    sc = BluetoothScanner(got.append, sample_interval=60)
    dev = SimpleNamespace(address="AA:BB:CC:DD:EE:FF", name="AA-BB-CC-DD-EE-FF")

    def adv(rssi):
        return SimpleNamespace(
            rssi=rssi, local_name=None, tx_power=None, manufacturer_data={0x004C: b"\x12\x19"},
            service_uuids=[], platform_data=None,
        )

    sc._on_advertisement(dev, adv(-127))  # "no RSSI" marker is ignored
    sc._on_advertisement(dev, adv(-60))  # first sample is emitted right away
    sc._on_advertisement(dev, adv(-70))  # throttled, buffered
    assert len(got) == 1
    r = got[0]
    assert r.rssi == -60 and r.name == "" and r.vendor == "Apple" and r.device_type == "Find My tag"
    sc._last_emit[dev.address] = 0  # pretend the interval elapsed
    sc._on_advertisement(dev, adv(-80))
    assert got[1].rssi == -75  # average of the buffered -70 and -80


def test_vendor_and_type_hints():
    assert vendor_from_manufacturer({0x0075: b""}) == "Samsung"
    assert vendor_from_manufacturer({0xBEEF: b""}) == "0xBEEF"
    assert vendor_from_manufacturer({}) is None
    assert device_type_hint({}, ["0000feed-0000-1000-8000-00805f9b34fb"]) == "Tile tracker"
    assert device_type_hint({}, [], {"Icon": "audio-headset"}) == "headset"


def test_fit_columns_keeps_most_important_in_order():
    cols = [Col("a", 10, 100, None), Col("b", 30, 10, None), Col("c", 10, 90, None), Col("d", 5, 50, None), Col("e", 5, 40, None)]
    assert [c.header for c in fit_columns(cols, 40)] == ["a", "c", "d"]
    assert [c.header for c in fit_columns(cols, 45)] == ["a", "c", "d", "e"]
    assert [c.header for c in fit_columns(cols, None)] == ["a", "b", "c", "d", "e"]


def test_cli_defaults_to_monitor():
    args = build_parser().parse_args(["monitor"])
    assert args.interval == 5.0 and args.only is None
    args = build_parser().parse_args(["track", "phone", "--kind", "bt", "--beep"])
    assert args.target == "phone" and args.kind == "bt" and args.beep


def test_demo_scan_json(capsys):
    code = main(["scan", "--demo", "--seed", "1", "--duration", "1.2", "--json"])
    assert code == 0
    data = json.loads(capsys.readouterr().out)
    kinds = {d["kind"] for d in data["devices"]}
    assert kinds == {"wifi", "bt"}
    assert all(-100 <= d["rssi"] <= -20 for d in data["devices"])
    assert data["errors"] == {}


def test_demo_scan_csv(tmp_path, capsys):
    out = tmp_path / "scan.csv"
    assert main(["scan", "--demo", "--only", "wifi", "--seed", "1", "--csv", str(out)]) == 0
    lines = out.read_text().splitlines()
    assert lines[0].startswith("time,kind,id,name,rssi_dbm")
    assert len(lines) > 5
