import ctypes
import json
import struct

from sigscan.wifi.linux import parse_iw_dev, parse_iw_scan, parse_nmcli, split_terse
from sigscan.wifi.macos import parse_system_profiler
from sigscan.wifi.windows import WLAN_BSS_ENTRY, parse_netsh, security_from_ies

NMCLI_OUTPUT = (
    "*:A4\\:2B\\:B0\\:11\\:22\\:33:HomeNet:36:5180 MHz:92:WPA2 WPA3\n"
    " :F0\\:9F\\:C2\\:AA\\:BB\\:02:Cafe\\: Free WiFi:6:2437 MHz:45:\n"
    " :50\\:C7\\:BF\\:8F\\:3C\\:10::11:2462 MHz:20:WPA2\n"
)


def test_split_terse_unescapes_colons():
    assert split_terse("a\\:b:c\\\\d:") == ["a:b", "c\\d", ""]


def test_parse_nmcli():
    rs = parse_nmcli(NMCLI_OUTPUT, now=1.0)
    assert [r.id for r in rs] == ["a4:2b:b0:11:22:33", "f0:9f:c2:aa:bb:02", "50:c7:bf:8f:3c:10"]
    home, cafe, hidden = rs
    assert home.connected and not cafe.connected
    assert home.name == "HomeNet" and home.channel == 36 and home.frequency == 5180 and home.band == "5G"
    assert home.rssi_estimated
    assert home.rssi == round(-100 + 92 * 0.6)
    assert cafe.name == "Cafe: Free WiFi" and cafe.security == "Open"
    assert hidden.name == "" and hidden.security == "WPA2"


IW_OUTPUT = """\
BSS a4:2b:b0:11:22:33(on wlp2s0) -- associated
\tlast seen: 2134.124s [boottime]
\tTSF: 1234 usec (0d, 00:00:00)
\tfreq: 5180.0
\tbeacon interval: 100 TUs
\tcapability: ESS Privacy SpectrumMgmt (0x1111)
\tsignal: -41.00 dBm
\tlast seen: 120 ms ago
\tSSID: HomeNet
\tRSN:\t * Version: 1
\t\t * Group cipher: CCMP
\t\t * Pairwise ciphers: CCMP
\t\t * Authentication suites: PSK SAE
\t\t * Capabilities: 16-PTKSA-RC 1-GTKSA-RC MFP-capable (0x008c)
BSS 00:1a:1e:0c:0d:0e(on wlp2s0)
\tfreq: 2462
\tcapability: ESS ShortSlotTime (0x0401)
\tsignal: -77.00 dBm
\tSSID: CoffeeShop Guest
BSS 3c:37:12:00:00:aa(on wlp2s0)
\tfreq: 6115
\tcapability: ESS Privacy (0x0011)
\tsignal: -73.00 dBm
\tSSID: Office-6E
\tRSN:\t * Version: 1
\t\t * Authentication suites: SAE
"""


def test_parse_iw_scan():
    rs = parse_iw_scan(IW_OUTPUT, now=1.0)
    assert len(rs) == 3
    home, cafe, office = rs
    assert home.connected and home.rssi == -41.0 and not home.rssi_estimated
    assert home.channel == 36 and home.frequency == 5180 and home.security == "WPA2/WPA3"
    assert cafe.security == "Open" and cafe.channel == 11
    assert office.security == "WPA3" and office.band == "6G" and office.channel == 33


def test_parse_iw_dev():
    out = "phy#0\n\tInterface wlp2s0\n\t\tifindex 3\n\t\ttype managed\n"
    assert parse_iw_dev(out) == ["wlp2s0"]


NETSH_OUTPUT = """\

Interface name : Wi-Fi
There are 2 networks currently visible.

SSID 1 : HomeNet
    Network type            : Infrastructure
    Authentication          : WPA2-Personal
    Encryption              : CCMP
    BSSID 1                 : a4:2b:b0:11:22:33
         Signal             : 96%
         Radio type         : 802.11ax
         Band               : 5 GHz
         Channel            : 36
         Bss Load:
             Connected Stations:        3
             Channel Utilization:       24 (9 %)
         Basic rates (Mbps) : 6 12 24
         Other rates (Mbps) : 9 18 36 48 54
    BSSID 2                 : a4:2b:b0:11:22:34
         Signal             : 80%
         Radio type         : 802.11n
         Band               : 2.4 GHz
         Channel            : 6
         Basic rates (Mbps) : 1 2 5.5 11

SSID 2 :
    Network type            : Infrastructure
    Authentication          : Open
    Encryption              : None
    BSSID 1                 : 00:1a:1e:0c:0d:0e
         Signal             : 30%
         Radio type         : 802.11n
         Channel            : 11
"""

# The same output on a German Windows: labels are translated, values are not.
NETSH_GERMAN = """\
SSID 1 : HomeNet
    Netzwerktyp             : Infrastruktur
    Authentifizierung       : WPA2-Personal
    Verschlüsselung         : CCMP
    BSSID 1                 : a4:2b:b0:11:22:33
         Signal             : 96%
         Funktyp            : 802.11ax
         Band               : 5 GHz
         Kanal              : 36
"""


def test_parse_netsh():
    rs = parse_netsh(NETSH_OUTPUT, now=1.0)
    assert [r.id for r in rs] == ["a4:2b:b0:11:22:33", "a4:2b:b0:11:22:34", "00:1a:1e:0c:0d:0e"]
    a, b, c = rs
    assert a.name == b.name == "HomeNet" and c.name == ""
    assert a.rssi == -52 and a.channel == 36 and a.frequency == 5180 and a.security == "WPA2"
    assert b.channel == 6 and b.frequency == 2437
    assert c.rssi == -85 and c.security == "Open" and c.channel == 11


def test_parse_netsh_localised_labels():
    (r,) = parse_netsh(NETSH_GERMAN, now=1.0)
    assert r.rssi == -52 and r.channel == 36 and r.band == "5G" and r.security == "WPA2"


SYSTEM_PROFILER = {
    "SPAirPortDataType": [
        {
            "spairport_airport_interfaces": [
                {
                    "_name": "en0",
                    "spairport_current_network_information": {
                        "_name": "HomeNet",
                        "spairport_network_channel": "36 (5GHz, 80MHz)",
                        "spairport_security_mode": "spairport_security_mode_wpa2_personal",
                        "spairport_signal_noise": "-48 dBm / -93 dBm",
                    },
                    "spairport_airport_other_local_wireless_networks": [
                        {
                            "_name": "Neighbour",
                            "spairport_network_channel": "6 (2GHz, 20MHz)",
                            "spairport_security_mode": "spairport_security_mode_wpa3_transition",
                            "spairport_signal_noise": "-70 dBm / -95 dBm",
                        },
                        {"_name": "NoSignalInfo", "spairport_network_channel": "11"},
                    ],
                }
            ]
        }
    ]
}


def test_parse_system_profiler():
    rs = parse_system_profiler(json.dumps(SYSTEM_PROFILER), now=1.0)
    assert len(rs) == 2
    home, other = rs
    assert home.connected and home.rssi == -48 and home.noise == -93
    assert home.channel == 36 and home.frequency == 5180 and home.security == "WPA2"
    assert other.channel == 6 and other.frequency == 2437 and not other.connected


def _rsn_ie(akms):
    body = struct.pack("<H", 1) + b"\x00\x0f\xac\x04" + struct.pack("<H", 1) + b"\x00\x0f\xac\x04"
    body += struct.pack("<H", len(akms)) + b"".join(b"\x00\x0f\xac" + bytes([a]) for a in akms)
    return bytes([48, len(body)]) + body


def test_security_from_ies():
    ssid_ie = bytes([0, 4]) + b"test"
    assert security_from_ies(ssid_ie + _rsn_ie([2])) == "WPA2"
    assert security_from_ies(ssid_ie + _rsn_ie([2, 8])) == "WPA2/WPA3"
    assert security_from_ies(_rsn_ie([8])) == "WPA3"
    assert security_from_ies(_rsn_ie([1])) == "WPA2-Ent"
    assert security_from_ies(_rsn_ie([18])) == "OWE"
    assert security_from_ies(bytes([221, 6]) + b"\x00\x50\xf2\x01\x01\x00") == "WPA"
    assert security_from_ies(ssid_ie, privacy=True) == "WEP"
    assert security_from_ies(ssid_ie) == "Open"
    assert security_from_ies(b"\x30\x02\x01") == "WPA2"  # truncated IE must not crash


def test_wlan_bss_entry_matches_windows_sdk_size():
    # sizeof(WLAN_BSS_ENTRY) is 360 bytes in the Windows SDK.
    assert ctypes.sizeof(WLAN_BSS_ENTRY) == 360
    assert WLAN_BSS_ENTRY.lRssi.offset == 56
    assert WLAN_BSS_ENTRY.ulChCenterFrequency.offset == 92


def _fake_tools(tmp_path, monkeypatch):
    """Put fake `nmcli` and `iw` executables first on PATH."""
    import os
    import stat

    (tmp_path / "nmcli.out").write_text(NMCLI_OUTPUT)
    (tmp_path / "iw.out").write_text(IW_OUTPUT)
    scripts = {
        # Refuses the rescan (as NetworkManager does when scans are too frequent).
        "nmcli": f'''#!/bin/sh
case "$*" in *"--rescan yes"*) echo "Error: Scanning not allowed immediately following previous scan." >&2; exit 1;; esac
cat "{tmp_path}/nmcli.out"
''',
        "iw": f'''#!/bin/sh
if [ "$1" = "dev" ] && [ $# -eq 1 ]; then printf 'phy#0\\n\\tInterface wlp2s0\\n'; exit 0; fi
case "$*" in "dev wlp2s0 scan dump"|"dev wlp2s0 scan") cat "{tmp_path}/iw.out";; *) exit 1;; esac
''',
    }
    for name, body in scripts.items():
        p = tmp_path / name
        p.write_text(body)
        p.chmod(p.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")


def test_linux_scanner_nmcli_with_exact_dbm_from_iw(tmp_path, monkeypatch):
    from sigscan.wifi.linux import LinuxWifiScanner

    _fake_tools(tmp_path, monkeypatch)
    sc = LinuxWifiScanner()
    sc.is_root = False
    rs = {r.id: r for r in sc.scan()}
    assert len(rs) == 3  # nmcli's list, after falling back to its cache
    home = rs["a4:2b:b0:11:22:33"]
    assert home.rssi == -41.0 and not home.rssi_estimated  # refined by `iw scan dump`
    other = rs["f0:9f:c2:aa:bb:02"]
    assert other.rssi_estimated  # not in iw's results: keeps the converted % value


def test_linux_scanner_as_root_uses_iw(tmp_path, monkeypatch):
    from sigscan.wifi.linux import LinuxWifiScanner

    _fake_tools(tmp_path, monkeypatch)
    sc = LinuxWifiScanner()
    sc.is_root = True
    rs = sc.scan()
    assert [r.name for r in rs] == ["HomeNet", "CoffeeShop Guest", "Office-6E"]
    assert all(not r.rssi_estimated for r in rs)
