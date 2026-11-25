"""Command-line interface."""

from __future__ import annotations

import argparse
import asyncio
import sys
from typing import List, Optional

from rich.console import Console

from . import __version__
from .estimate import DEFAULT_PATH_LOSS_EXPONENT

EPILOG = """\
examples:
  sigscan                      live dashboard of Wi-Fi + Bluetooth signal strength
  sigscan scan                 one-off snapshot (listens to Bluetooth for 8 s)
  sigscan scan --only wifi --json
  sigscan monitor --log signals.csv
  sigscan track "AirPods" --beep    hunt for a device: the bar grows as you get closer
  sigscan --demo               try it with simulated devices
"""


def _common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--only", choices=["wifi", "bt"], help="scan only Wi-Fi or only Bluetooth")
    p.add_argument("--iface", help="Wi-Fi interface (Linux: wlan0, macOS: en0, Windows: part of the adapter name)")
    p.add_argument("--bt-adapter", help="Bluetooth adapter to use (Linux only, e.g. hci1)")
    p.add_argument("--classic", action="store_true", help="also discover classic (non-LE) Bluetooth devices (Linux only)")
    p.add_argument(
        "--path-loss",
        type=float,
        default=DEFAULT_PATH_LOSS_EXPONENT,
        metavar="N",
        help=f"path-loss exponent for distance estimates: 2 open space, 3 indoors, 4 many walls (default {DEFAULT_PATH_LOSS_EXPONENT})",
    )
    p.add_argument("--limit", type=int, help="show at most N devices per table")
    p.add_argument("--demo", action="store_true", help="use simulated devices (no hardware needed)")
    p.add_argument("--seed", type=int, help=argparse.SUPPRESS)


def _live(p: argparse.ArgumentParser, interval: float) -> None:
    p.add_argument("--interval", type=float, default=interval, metavar="SEC", help=f"seconds between Wi-Fi scans (default {interval:g})")
    p.add_argument("--stale", type=float, default=30.0, metavar="SEC", help="hide devices not heard from for this long (default 30)")
    p.add_argument("--log", metavar="FILE.csv", help="append every reading to a CSV file")
    p.add_argument("--no-screen", action="store_true", help="draw in the normal terminal instead of a full-screen view")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sigscan",
        description="Measure the signal strength of nearby Wi-Fi access points and Bluetooth devices.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="{scan,monitor,track}")

    scan = sub.add_parser("scan", help="take a one-off snapshot and print a table")
    _common(scan)
    scan.add_argument("--duration", type=float, default=8.0, metavar="SEC", help="how long to listen for Bluetooth devices (default 8)")
    scan.add_argument("--json", action="store_true", help="print JSON instead of tables")
    scan.add_argument("--csv", metavar="FILE", help="also save the results to a CSV file")

    mon = sub.add_parser("monitor", help="live, continuously updating dashboard (default)")
    _common(mon)
    _live(mon, interval=5.0)

    track = sub.add_parser("track", help="follow one device's signal to locate it")
    track.add_argument("target", help="part of the SSID, BSSID, Bluetooth name or address")
    track.add_argument("--kind", choices=["wifi", "bt"], help="only match Wi-Fi or only Bluetooth")
    track.add_argument("--beep", action="store_true", help="beep faster as the signal gets stronger")
    _common(track)
    _live(track, interval=2.0)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    # `sigscan` or `sigscan --demo` with no subcommand means `sigscan monitor ...`.
    if not argv or (argv[0].startswith("-") and argv[0] not in ("-h", "--help", "--version")):
        argv = ["monitor", *argv]
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0

    from . import app

    console = Console()
    runner = {"scan": app.run_scan, "monitor": app.run_monitor, "track": app.run_track}[args.command]
    try:
        return asyncio.run(runner(args, console))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
