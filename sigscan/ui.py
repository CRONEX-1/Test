"""Rendering with rich: tables, signal bars, sparklines and the tracking meter."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional

from rich import box
from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .estimate import dbm_to_percent, estimate_distance, format_distance, quality
from .models import BT, WIFI, DeviceStats, Tracker

SPARK_CHARS = "▁▂▃▄▅▆▇█"


@dataclass
class Status:
    wifi_enabled: bool = True
    bt_enabled: bool = True
    wifi_backend: str = ""
    wifi_scans: int = 0
    wifi_last: Optional[float] = None
    wifi_error: Optional[str] = None
    wifi_estimated: bool = False
    bt_state: str = "starting"
    bt_error: Optional[str] = None
    started: float = field(default_factory=time.time)
    log_path: Optional[str] = None


@dataclass
class ViewOptions:
    path_loss: float = 3.0
    limit: Optional[int] = None
    history: bool = True  # show sparkline/trend columns (live views)


def signal_bar(dbm: float, kind: str, width: int = 10) -> Text:
    pct = dbm_to_percent(dbm, ceiling=-30 if kind == WIFI else -40)
    filled = max(1 if pct > 0 else 0, round(pct / 100 * width))
    _, colour = quality(dbm, kind)
    return Text("█" * filled, style=colour) + Text("░" * (width - filled), style="grey35")


def sparkline(values: List[float], width: int = 16, floor: float = -100, ceil: float = -30) -> Text:
    if not values:
        return Text("")
    vals = values[-width:]
    out = Text()
    span = ceil - floor
    for v in vals:
        idx = int((min(max(v, floor), ceil) - floor) / span * (len(SPARK_CHARS) - 1))
        out.append(SPARK_CHARS[idx], style="cyan")
    return out


def trend_text(delta: float, threshold: float = 2.0) -> Text:
    if delta >= threshold:
        return Text(f"▲{delta:+.0f}", style="bright_green")
    if delta <= -threshold:
        return Text(f"▼{delta:+.0f}", style="red")
    return Text("·", style="grey50")


def _dbm(stats: DeviceStats, live: bool) -> Text:
    value = stats.smoothed() if live else stats.average(len(stats.history))
    _, colour = quality(value, stats.latest.kind)
    approx = "~" if stats.latest.rssi_estimated else ""
    return Text(f"{approx}{value:.0f}", style=f"bold {colour}")


def _age(stats: DeviceStats) -> Text:
    age = stats.age()
    style = "grey50" if age < 10 else "dark_orange"
    return Text(f"{age:.0f}s" if age < 100 else f"{age / 60:.0f}m", style=style)


@dataclass
class Col:
    """A table column: `width` is its typical content width, used to decide what fits;
    when the terminal is too narrow the lowest-`priority` columns are dropped first."""

    header: str
    width: int
    priority: int
    cell: Callable[[DeviceStats, float], RenderableType]
    kwargs: dict = field(default_factory=dict)


def fit_columns(cols: List[Col], width: Optional[int]) -> List[Col]:
    """Greedily keep the most important columns that fit in `width`, in their original order."""
    if width is None:
        return list(cols)
    keep, used = set(), 0
    for c in sorted(cols, key=lambda c: c.priority, reverse=True):
        if used + c.width + 3 <= width or len(keep) < 3:
            keep.add(id(c))
            used += c.width + 3
    return [c for c in cols if id(c) in keep]


def _build_table(title: str, total: int, cols: List[Col], devices: List[DeviceStats], live: bool, empty: str) -> Table:
    t = Table(box=box.SIMPLE_HEAD, expand=False, pad_edge=False, title_justify="left")
    t.title = Text.assemble((title, "bold"), (f"  ({total})", "grey50"))
    for c in cols:
        t.add_column(c.header, **c.kwargs)
    for d in devices:
        value = d.smoothed() if live else d.average(len(d.history))
        t.add_row(*[c.cell(d, value) for c in cols])
    if not devices:
        t.add_row(Text(empty, style="grey50"), *[""] * (len(cols) - 1))
    return t


def _wifi_name(d: DeviceStats, _v: float) -> Text:
    r = d.latest
    name = Text(r.name or "<hidden>", style="italic grey50" if not r.name else "")
    return Text("● ", style="bright_green") + name if r.connected else name


def wifi_table(
    devices: List[DeviceStats], opts: ViewOptions, total: Optional[int] = None, width: Optional[int] = None
) -> Table:
    live = opts.history
    pl = opts.path_loss
    cols = [
        Col("SSID", 18, 100, _wifi_name, dict(no_wrap=True, max_width=24, overflow="ellipsis")),
        Col("BSSID", 17, 60, lambda d, v: d.latest.id if ":" in d.latest.id else "", dict(no_wrap=True, style="grey62")),
        Col("Ch", 3, 80, lambda d, v: str(d.latest.channel or ""), dict(justify="right")),
        Col("Band", 4, 40, lambda d, v: d.latest.band or "", dict(no_wrap=True)),
        Col("dBm", 4, 95, lambda d, v: _dbm(d, live), dict(justify="right")),
        Col("Signal", 10, 90, lambda d, v: signal_bar(v, WIFI), dict(no_wrap=True)),
        Col("Quality", 9, 70, lambda d, v: Text(*quality(v, WIFI)), dict(no_wrap=True)),
    ]
    if live:
        cols += [
            Col("Trend", 4, 50, lambda d, v: trend_text(d.trend()), dict(justify="right", no_wrap=True)),
            Col("History", 16, 35, lambda d, v: sparkline(d.values()), dict(no_wrap=True)),
        ]
    else:
        cols.append(Col("Min/Max", 7, 20, lambda d, v: f"{d.min:.0f}/{d.max:.0f}", dict(justify="right", no_wrap=True, style="grey62")))
    cols += [
        Col("Distance", 8, 65, lambda d, v: format_distance(estimate_distance(d.latest, v, pl)), dict(justify="right", no_wrap=True)),
        Col("Security", 9, 30, lambda d, v: d.latest.security or "", dict(no_wrap=True, max_width=10, overflow="ellipsis", style="grey62")),
    ]
    return _build_table(
        "Wi-Fi access points", len(devices) if total is None else total, fit_columns(cols, width), devices, live, "no networks seen yet…"
    )


def bt_table(
    devices: List[DeviceStats], opts: ViewOptions, total: Optional[int] = None, width: Optional[int] = None
) -> Table:
    live = opts.history
    pl = opts.path_loss
    cols = [
        Col(
            "Name", 16, 100,
            lambda d, v: Text(d.latest.name or "<unnamed>", style="" if d.latest.name else "italic grey50"),
            dict(no_wrap=True, max_width=22, overflow="ellipsis"),
        ),
        Col("Address", 17, 60, lambda d, v: d.latest.id, dict(no_wrap=True, style="grey62", max_width=17, overflow="ellipsis")),
        Col(
            "Vendor / type", 18, 55,
            lambda d, v: " · ".join(x for x in (d.latest.vendor, d.latest.device_type) if x),
            dict(no_wrap=True, max_width=24, overflow="ellipsis"),
        ),
        Col("dBm", 4, 95, lambda d, v: _dbm(d, live), dict(justify="right")),
        Col("Signal", 10, 90, lambda d, v: signal_bar(v, BT), dict(no_wrap=True)),
        Col("Proximity", 10, 52, lambda d, v: Text(*quality(v, BT)), dict(no_wrap=True)),
    ]
    if live:
        cols += [
            Col("Trend", 4, 50, lambda d, v: trend_text(d.trend()), dict(justify="right", no_wrap=True)),
            Col("History", 16, 35, lambda d, v: sparkline(d.values()), dict(no_wrap=True)),
        ]
    else:
        cols += [
            Col("Min/Max", 7, 20, lambda d, v: f"{d.min:.0f}/{d.max:.0f}", dict(justify="right", no_wrap=True, style="grey62")),
            Col("Samples", 7, 15, lambda d, v: str(d.count), dict(justify="right", style="grey62")),
        ]
    cols += [
        Col("Distance", 8, 65, lambda d, v: format_distance(estimate_distance(d.latest, v, pl)), dict(justify="right", no_wrap=True)),
        Col("Seen", 4, 30, lambda d, v: _age(d), dict(justify="right", no_wrap=True)),
    ]
    return _build_table(
        "Bluetooth devices", len(devices) if total is None else total, fit_columns(cols, width), devices, live, "no devices seen yet…"
    )


def status_line(status: Status, tracker: Tracker) -> Text:
    parts = Text()
    if status.wifi_enabled:
        parts.append("Wi-Fi ", style="bold")
        if status.wifi_error:
            parts.append(f"⚠ {status.wifi_error}", style="red")
        else:
            backend = status.wifi_backend + (" (dBm from %)" if status.wifi_estimated else "")
            last = f", last {time.time() - status.wifi_last:.0f}s ago" if status.wifi_last else ", scanning…"
            parts.append(f"{backend}: {status.wifi_scans} scans{last}", style="grey62")
        parts.append("   ")
    if status.bt_enabled:
        parts.append("Bluetooth ", style="bold")
        if status.bt_error:
            parts.append(f"⚠ {status.bt_error}", style="red")
        else:
            parts.append(f"{status.bt_state}, {len(tracker.devices(BT))} devices", style="grey62")
    if status.log_path:
        parts.append(f"   logging → {status.log_path}", style="grey50")
    return parts


def _split_rows(available: int, n_wifi: int, n_bt: int) -> tuple:
    """Share the terminal rows between the two tables."""
    if n_wifi + n_bt <= available:
        return n_wifi, n_bt
    half = available // 2
    if n_wifi < half:
        return n_wifi, available - n_wifi
    if n_bt < half:
        return available - n_bt, n_bt
    return half, available - half


def monitor_view(
    tracker: Tracker, status: Status, opts: ViewOptions, height: int = 40, width: Optional[int] = None
) -> RenderableType:
    wifi = tracker.devices(WIFI) if status.wifi_enabled else []
    bt = tracker.devices(BT) if status.bt_enabled else []
    tables = int(status.wifi_enabled) + int(status.bt_enabled)
    available = max(4, height - 4 - 5 * tables)
    n_wifi, n_bt = _split_rows(available, len(wifi), len(bt))
    if opts.limit:
        n_wifi, n_bt = min(n_wifi, opts.limit), min(n_bt, opts.limit)

    elapsed = time.time() - status.started
    header = Text.assemble(
        ("sigscan", "bold cyan"),
        (f"  live monitor · {elapsed:.0f}s · strongest first · Ctrl+C to stop", "grey50"),
    )
    parts: List[RenderableType] = [header, status_line(status, tracker)]
    if status.wifi_enabled:
        parts.append(wifi_table(wifi[:n_wifi], opts, total=len(wifi), width=width))
    if status.bt_enabled:
        parts.append(bt_table(bt[:n_bt], opts, total=len(bt), width=width))
    return Group(*parts)


def _session_avg(d: DeviceStats) -> float:
    return d.average(len(d.history))


def snapshot_view(tracker: Tracker, status: Status, opts: ViewOptions, width: Optional[int] = None) -> RenderableType:
    parts: List[RenderableType] = []
    if status.wifi_enabled:
        wifi = sorted(tracker.devices(WIFI, include_stale=True), key=_session_avg, reverse=True)
        wifi_total = len(wifi)
        if opts.limit:
            wifi = wifi[: opts.limit]
        if wifi or not status.wifi_error:
            parts.append(wifi_table(wifi, opts, total=wifi_total, width=width))
        if status.wifi_error:
            parts.append(Text(f"Wi-Fi unavailable: {status.wifi_error}\n", style="red"))
    if status.bt_enabled:
        bt = sorted(tracker.devices(BT, include_stale=True), key=_session_avg, reverse=True)
        bt_total = len(bt)
        if opts.limit:
            bt = bt[: opts.limit]
        if bt or not status.bt_error:
            parts.append(bt_table(bt, opts, total=bt_total, width=width))
        if status.bt_error:
            parts.append(Text(f"Bluetooth unavailable: {status.bt_error}\n", style="red"))
    if any(d.latest.rssi_estimated for d in tracker.devices(WIFI, include_stale=True)):
        parts.append(Text("~ = converted from a 0-100% signal value (approximate dBm).", style="grey50"))
    if tracker.devices(include_stale=True):
        parts.append(Text("Distances are rough estimates: walls and bodies change them a lot.", style="grey50"))
    return Group(*parts)


def track_view(
    target: str,
    stats: Optional[DeviceStats],
    candidates: List[DeviceStats],
    status: Status,
    tracker: Tracker,
    opts: ViewOptions,
    width: int = 80,
) -> RenderableType:
    header = Text.assemble(("sigscan", "bold cyan"), ("  tracking mode · Ctrl+C to stop", "grey50"))
    if stats is None:
        body = Text.assemble(("Searching for ", "grey62"), (repr(target), "bold"), ("…\n\n", "grey62"))
        if candidates:
            body.append("Strongest devices right now (use part of a name or address as the target):\n", style="grey62")
            for d in candidates[:8]:
                r = d.latest
                v = d.smoothed()
                body.append(f"  {v:>4.0f} dBm  ", style=quality(v, r.kind)[1])
                body.append(f"{'Wi-Fi' if r.kind == WIFI else 'BT   '}  {r.name or '<unnamed>':<24.24}  {r.id}\n")
        return Group(header, status_line(status, tracker), Panel(body, border_style="grey50"))

    r = stats.latest
    value = stats.smoothed()
    label, colour = quality(value, r.kind)
    bar_width = max(20, min(60, width - 14))
    dist = estimate_distance(r, value, opts.path_loss)
    delta = stats.trend(window=3)

    title = Text.assemble((r.name or "<unnamed>", "bold"), (f"  {'Wi-Fi' if r.kind == WIFI else 'Bluetooth'}", "grey62"))
    info = [r.id]
    if r.kind == WIFI:
        info += [x for x in (f"ch {r.channel}" if r.channel else "", r.band or "", r.security or "") if x]
    else:
        info += [x for x in (r.vendor, r.device_type, f"tx {r.tx_power} dBm" if r.tx_power is not None else "") if x]

    if delta >= 2:
        hint = Text(f"▲ getting stronger ({delta:+.1f} dB): you are moving closer", style="bright_green")
    elif delta <= -2:
        hint = Text(f"▼ getting weaker ({delta:+.1f} dB): you are moving away", style="red")
    else:
        hint = Text("· steady", style="grey62")

    age = stats.age()
    lines = [
        Text(" · ".join(info), style="grey62"),
        Text(""),
        Text.assemble(
            (f"{value:.0f} dBm", f"bold {colour}"),
            ("   ", ""),
            (label, colour),
            ("   ", ""),
            (format_distance(dist), "bold"),
            ("  (estimate)", "grey50"),
        ),
        signal_bar(value, r.kind, width=bar_width),
        hint,
        Text(""),
        Text.assemble(("history  ", "grey50"), sparkline(stats.values(), width=bar_width)),
        Text(
            f"min {stats.min:.0f}   avg {stats.average(30):.0f}   max {stats.max:.0f} dBm   "
            f"samples {stats.count}   last seen {age:.1f}s ago",
            style="dark_orange" if age > 10 else "grey50",
        ),
    ]
    others = [d for d in candidates if d is not stats][:3]
    if others:
        lines.append(Text(""))
        lines.append(Text("Other matches: " + ", ".join(f"{d.latest.name or d.latest.id} ({d.latest.rssi:.0f})" for d in others), style="grey50"))
    return Group(header, status_line(status, tracker), Panel(Group(*lines), title=title, title_align="left", border_style=colour))
