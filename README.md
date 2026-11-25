# sigscan

Measure the signal strength of nearby **Wi-Fi access points** and **Bluetooth devices** from your terminal.
Works on Linux, macOS and Windows.

```
Wi-Fi access points  (11)
 SSID                BSSID              Ch  Band  dBm  Signal      Quality    Trend  History   Distance  Security
 ● HomeNet           a4:2b:b0:11:22:33  36  5G    -42  ████████░░  Excellent      ·  ▆▆▆▆▆▆      ~1.5 m  WPA2/WPA3
 Neighbour           f0:9f:c2:aa:bb:02   1  2.4G  -62  █████░░░░░  Fair           ·  ▄▄▄▄▄▄       ~12 m  WPA2

Bluetooth devices  (11)
 Name               Address            Vendor / type          dBm  Signal      Proximity   Trend  Distance  Seen
 Pixel 9            4C:11:AE:10:20:30  Google · phone         -44  █████████░  Very close    ▲+7      <1 m    1s
 <unnamed>          6A:3F:91:C2:05:77  Apple · Find My tag    -64  ██████░░░░  Near          ▲+4    ~1.5 m    2s
```

## Install

Requires Python 3.10+.

```bash
pip install .
```

This installs the `sigscan` command along with its dependencies: [`rich`](https://github.com/Textualize/rich) for the UI, [`bleak`](https://github.com/hbldh/bleak) for Bluetooth, and, on macOS, PyObjC's CoreWLAN bindings.

To try it without any radio hardware, use simulated devices:

```bash
sigscan --demo
```

## Usage

| Command | What it does |
| --- | --- |
| `sigscan` | Live dashboard of every Wi-Fi network and Bluetooth device, strongest first, with trend arrows and history. |
| `sigscan scan` | One-off snapshot. Listens for Bluetooth for 8 s (`--duration`) and prints min/avg/max per device. |
| `sigscan track <name-or-address>` | Follow one device's signal with a big meter. Walk around: the bar grows and the arrow turns green as you get closer. Add `--beep` for a Geiger-counter beep. |

Useful options (work with every command):

* `--only wifi` / `--only bt`: scan just one radio.
* `--limit N`: show at most N rows per table.
* `--iface wlan0`: choose the Wi-Fi adapter. `--bt-adapter hci1` chooses the Bluetooth adapter (Linux).
* `--classic`: also find classic, non-LE Bluetooth devices (Linux only).
* `--path-loss N`: tune distance estimates. Use 2 for open space, 3 for a typical home (the default), 4 for a building with many walls.
* `scan --json`, `scan --csv out.csv`: machine-readable output.
* `monitor --log signals.csv`, `track ... --log`: record every reading over time, e.g. to compare spots in your house.
* `--interval SEC`: time between Wi-Fi scans (default 5 s for monitor, 2 s for track).
* `--no-screen`: draw in the normal terminal instead of full-screen.

Examples:

```bash
sigscan scan --only wifi                 # which access point is strongest here?
sigscan track HomeNet --kind wifi        # walk around and watch your router's signal
sigscan track "AirPods" --beep           # find lost earbuds
sigscan monitor --log kitchen.csv        # record the signal while you test a spot
```

## Platform notes

### Linux
* Wi-Fi uses NetworkManager (`nmcli`) and/or `iw`. With both installed you get exact dBm values without root. With only `nmcli`, dBm is converted from NetworkManager's 0-100 % value (shown with a `~`). Running as root makes `iw` trigger the scans itself.
* NetworkManager limits how often scans can be triggered. In between, sigscan uses NetworkManager's most recent results.
* Bluetooth needs BlueZ (`bluetoothd`) running and the adapter powered on (`bluetoothctl power on`).

### macOS
* **Location Services:** since macOS 14, network names and BSSIDs are hidden unless your terminal app (Terminal, iTerm, …) has location access. Grant it in *System Settings → Privacy & Security → Location Services*. Signal strengths still work without it.
* **Bluetooth:** the first run asks for permission for your terminal app. macOS never exposes Bluetooth MAC addresses, so devices show a per-Mac UUID instead.

### Windows
* Wi-Fi uses the native WLAN API, which starts a fresh scan and reports exact dBm. If that fails, it falls back to `netsh wlan show networks mode=bssid`.
* **Windows 11 24H2+:** Wi-Fi scan results require *Settings → Privacy & security → Location* to be on, with "Let desktop apps access your location" enabled.
* Bluetooth scanning finds BLE devices through the WinRT API (built into Windows 10/11).

## Reading the numbers

* **dBm** is the received signal strength. It's negative, and closer to 0 is stronger.
  * Wi-Fi: −30 to −50 excellent, −50 to −60 good, −60 to −67 fair (fine for video calls), −67 to −75 weak, below −80 unusable.
  * Bluetooth LE transmits much less power: around −50 is right next to you, −70 is across a room, below −90 is at the edge of range.
* **Trend** compares the last few readings with the ones before. ▲ means the signal is getting stronger.
* **Distance** is an estimate from a path-loss model, not a measurement. A body or a wall can shift a reading by 10 dB, roughly doubling or halving the estimate. Use it as near/mid/far.
* Bluetooth readings are averaged to about one per second per device, because raw BLE RSSI jumps around a lot.

## Limitations

* Phones, watches and many other devices change their Bluetooth address every few minutes for privacy, so one device may appear as several entries over time.
* Only devices that are advertising show up. A connected device that has stopped advertising (e.g. headphones in use) may disappear from the list.
* Classic Bluetooth discovery (`--classic`) is only available on Linux. macOS and Windows expose only BLE scanning to apps.

## Development

```bash
pip install -e '.[dev]'
pytest
```

Layout:

```
sigscan/
  cli.py          argument parsing
  app.py          scan / monitor / track modes, background Wi-Fi thread, CSV logging
  ui.py           rich tables, bars, sparklines, tracking meter
  models.py       Reading, per-device rolling stats, Tracker
  estimate.py     dBm/% conversion, quality ratings, distance estimates
  bluetooth.py    bleak-based BLE scanner, vendor/device-type hints
  demo.py         simulated devices for --demo
  wifi/linux.py   nmcli + iw
  wifi/macos.py   CoreWLAN + system_profiler
  wifi/windows.py wlanapi.dll (ctypes) + netsh
```
