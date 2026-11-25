"""Runs the scanners and drives the three modes: scan, monitor and track."""

from __future__ import annotations

import asyncio
import csv
import json
import threading
import time
from datetime import datetime
from typing import Callable, List, Optional

from rich.console import Console
from rich.live import Live

from . import ui
from .bluetooth import BluetoothError, BluetoothScanner
from .estimate import estimate_distance
from .models import BT, WIFI, Reading, Tracker
from .wifi import WifiError, create_wifi_scanner


class WifiWorker(threading.Thread):
    """Runs blocking Wi-Fi scans in a daemon thread and hands results to the event loop."""

    def __init__(self, scanner, interval: float, loop: asyncio.AbstractEventLoop, deliver: Callable, once: bool = False):
        super().__init__(daemon=True, name="wifi-scan")
        self.scanner = scanner
        self.interval = interval
        self.loop = loop
        self.deliver = deliver
        self.once = once
        self.stop_event = threading.Event()

    def run(self) -> None:
        while not self.stop_event.is_set():
            started = time.time()
            readings: List[Reading] = []
            error: Optional[str] = None
            try:
                readings = self.scanner.scan()
            except WifiError as e:
                error = str(e)
            except Exception as e:  # never let the thread die silently
                error = f"{type(e).__name__}: {e}"
            try:
                self.loop.call_soon_threadsafe(self.deliver, readings, error)
            except RuntimeError:  # event loop already closed
                return
            if self.once:
                return
            self.stop_event.wait(max(0.5, self.interval - (time.time() - started)))

    def stop(self) -> None:
        self.stop_event.set()


class CsvLogger:
    FIELDS = [
        "time", "kind", "id", "name", "rssi_dbm", "rssi_estimated", "channel", "frequency_mhz",
        "band", "security", "vendor", "device_type", "tx_power", "est_distance_m",
    ]

    def __init__(self, path: str, path_loss: float):
        self.path = path
        self.path_loss = path_loss
        self._fh = open(path, "a", newline="", encoding="utf-8")
        self._writer = csv.DictWriter(self._fh, fieldnames=self.FIELDS)
        if self._fh.tell() == 0:
            self._writer.writeheader()

    def __call__(self, r: Reading) -> None:
        self._writer.writerow(
            {
                "time": datetime.fromtimestamp(r.timestamp).isoformat(timespec="seconds"),
                "kind": r.kind,
                "id": r.id,
                "name": r.name,
                "rssi_dbm": r.rssi,
                "rssi_estimated": int(r.rssi_estimated),
                "channel": r.channel or "",
                "frequency_mhz": r.frequency or "",
                "band": r.band or "",
                "security": r.security or "",
                "vendor": r.vendor or "",
                "device_type": r.device_type or "",
                "tx_power": "" if r.tx_power is None else r.tx_power,
                "est_distance_m": round(estimate_distance(r, path_loss_exponent=self.path_loss), 2),
            }
        )
        self._fh.flush()

    def close(self) -> None:
        self._fh.close()


class Session:
    """Starts and stops whichever sources the user asked for."""

    def __init__(self, args, tracker: Tracker, status: ui.Status, wifi_interval: float, bt_sample_interval: float = 1.0):
        self.args = args
        self.tracker = tracker
        self.status = status
        self.wifi_interval = wifi_interval
        self.bt_sample_interval = bt_sample_interval
        self.wifi_worker: Optional[WifiWorker] = None
        self.bt = None
        self.wifi_done = asyncio.Event()

    def _deliver_wifi(self, readings: List[Reading], error: Optional[str]) -> None:
        s = self.status
        if error:
            s.wifi_error = error
        else:
            s.wifi_error = None
            s.wifi_scans += 1
            s.wifi_last = time.time()
            s.wifi_estimated = any(r.rssi_estimated for r in readings)
            self.tracker.update_many(readings)
        self.wifi_done.set()

    async def start(self, once: bool = False) -> None:
        loop = asyncio.get_running_loop()
        demo = getattr(self.args, "demo", False)
        if self.status.wifi_enabled:
            try:
                if demo:
                    from .demo import DemoWifiScanner

                    scanner = DemoWifiScanner(seed=getattr(self.args, "seed", None))
                else:
                    scanner = create_wifi_scanner(self.args.iface)
                self.status.wifi_backend = getattr(scanner, "name", "")
                self.wifi_worker = WifiWorker(scanner, self.wifi_interval, loop, self._deliver_wifi, once=once)
                self.wifi_worker.start()
            except WifiError as e:
                self.status.wifi_error = str(e)
                self.wifi_done.set()
        else:
            self.wifi_done.set()

        if self.status.bt_enabled:
            if demo:
                from .demo import DemoBluetoothScanner

                self.bt = DemoBluetoothScanner(self.tracker.update, seed=getattr(self.args, "seed", None), sample_interval=self.bt_sample_interval)
            else:
                self.bt = BluetoothScanner(
                    self.tracker.update,
                    adapter=self.args.bt_adapter,
                    include_classic=self.args.classic,
                    sample_interval=self.bt_sample_interval,
                )
            try:
                await self.bt.start()
                self.status.bt_state = "scanning"
            except BluetoothError as e:
                self.status.bt_error = str(e)
                self.status.bt_state = "unavailable"
                self.bt = None

    async def stop(self) -> None:
        if self.wifi_worker:
            self.wifi_worker.stop()
        if self.bt:
            await self.bt.stop()
            self.status.bt_state = "stopped"


def _status_for(args) -> ui.Status:
    only = getattr(args, "only", None)
    return ui.Status(wifi_enabled=only in (None, WIFI), bt_enabled=only in (None, BT))


def _attach_logger(args, tracker: Tracker, status: ui.Status) -> Optional[CsvLogger]:
    path = getattr(args, "log", None)
    if not path:
        return None
    logger = CsvLogger(path, args.path_loss)
    tracker.listeners.append(logger)
    status.log_path = path
    return logger


# --------------------------------------------------------------------------- #
# scan: one snapshot
# --------------------------------------------------------------------------- #


async def run_scan(args, console: Console) -> int:
    tracker = Tracker(stale_after=float("inf"))
    status = _status_for(args)
    session = Session(args, tracker, status, wifi_interval=0)
    opts = ui.ViewOptions(path_loss=args.path_loss, limit=args.limit, history=False)
    try:
        with console.status("Scanning…", spinner="dots") as spinner:
            await session.start(once=True)
            deadline = time.time() + (args.duration if status.bt_enabled and session.bt else 0)
            while time.time() < deadline or not session.wifi_done.is_set():
                remaining = max(0, deadline - time.time())
                bits = []
                if status.bt_enabled and session.bt:
                    bits.append(f"Bluetooth {len(tracker.devices(BT))} devices, {remaining:.0f}s left")
                if status.wifi_enabled and not session.wifi_done.is_set():
                    bits.append("Wi-Fi scanning")
                spinner.update("Scanning… " + " · ".join(bits))
                await asyncio.sleep(0.2)
    finally:
        await session.stop()

    if args.json:
        devices = tracker.devices(include_stale=True)
        out = []
        for d in devices:
            item = d.latest.to_dict()
            item.update(
                rssi=round(d.average(len(d.history)), 1),
                rssi_min=d.min,
                rssi_max=d.max,
                samples=d.count,
                est_distance_m=round(estimate_distance(d.latest, d.average(len(d.history)), args.path_loss), 2),
            )
            out.append(item)
        payload = {
            "time": datetime.now().isoformat(timespec="seconds"),
            "devices": out,
            "errors": {k: v for k, v in (("wifi", status.wifi_error), ("bluetooth", status.bt_error)) if v},
        }
        print(json.dumps(payload, indent=2))
    else:
        console.print(ui.snapshot_view(tracker, status, opts, width=console.size.width))

    if args.csv:
        logger = CsvLogger(args.csv, args.path_loss)
        for d in tracker.devices(include_stale=True):
            logger(d.latest)
        logger.close()
        if not args.json:
            console.print(f"[grey50]Saved {len(tracker.devices(include_stale=True))} rows to {args.csv}[/]")

    nothing = not tracker.devices(include_stale=True)
    failed = (not status.wifi_enabled or status.wifi_error) and (not status.bt_enabled or status.bt_error)
    return 1 if nothing and failed else 0


# --------------------------------------------------------------------------- #
# monitor: live dashboard
# --------------------------------------------------------------------------- #


async def run_monitor(args, console: Console) -> int:
    tracker = Tracker(stale_after=args.stale)
    status = _status_for(args)
    session = Session(args, tracker, status, wifi_interval=args.interval)
    opts = ui.ViewOptions(path_loss=args.path_loss, limit=args.limit, history=True)
    logger = _attach_logger(args, tracker, status)
    try:
        await session.start()
        with Live(console=console, auto_refresh=False, screen=not args.no_screen, transient=True) as live:
            while True:
                live.update(ui.monitor_view(tracker, status, opts, height=console.size.height, width=console.size.width), refresh=True)
                await asyncio.sleep(0.5)
    finally:
        await session.stop()
        if logger:
            logger.close()
        console.print(ui.monitor_view(tracker, status, opts, height=10_000, width=console.size.width))
    return 0


# --------------------------------------------------------------------------- #
# track: follow one device, for finding it
# --------------------------------------------------------------------------- #


def _beep_interval(dbm: float, kind: str) -> float:
    """Geiger-counter style: the stronger the signal, the faster the beeps."""
    low, high = (-95.0, -45.0) if kind == BT else (-90.0, -35.0)
    frac = min(1.0, max(0.0, (dbm - low) / (high - low)))
    return 2.5 - frac * 2.3


async def run_track(args, console: Console) -> int:
    tracker = Tracker(stale_after=args.stale)
    status = _status_for(args)
    if args.kind:
        status.wifi_enabled = args.kind == WIFI
        status.bt_enabled = args.kind == BT
    session = Session(args, tracker, status, wifi_interval=args.interval, bt_sample_interval=0.5)
    opts = ui.ViewOptions(path_loss=args.path_loss)
    logger = _attach_logger(args, tracker, status)
    locked = None  # (kind, id) once we've found the target
    next_beep = 0.0
    view = None
    try:
        await session.start()
        with Live(console=console, auto_refresh=False, screen=not args.no_screen, transient=True) as live:
            while True:
                matches = tracker.find(args.target, args.kind)
                if locked is None and matches:
                    locked = matches[0].key
                stats = tracker.get(*locked) if locked else None
                candidates = matches if stats else tracker.devices()
                view = ui.track_view(args.target, stats, candidates, status, tracker, opts, width=console.size.width)
                live.update(view, refresh=True)
                now = time.time()
                if args.beep and stats and stats.age() < 5 and now >= next_beep:
                    console.bell()
                    next_beep = now + _beep_interval(stats.smoothed(), stats.latest.kind)
                await asyncio.sleep(0.25)
    finally:
        await session.stop()
        if logger:
            logger.close()
        if view is not None:
            console.print(view)
    return 0
