import CoreLocation
import CoreWLAN
import Foundation

enum SourceStatus: Equatable {
    case starting
    case ok
    case off(String)
    case denied(String)
    case error(String)
}

enum LocationAccess {
    case notDetermined, granted, denied
}

/// Scans for Wi-Fi networks with CoreWLAN on a background queue.
///
/// macOS only reveals network names (SSIDs) and BSSIDs to apps that have
/// Location Services permission, so this also manages that permission.
final class WiFiScanner: NSObject, CLLocationManagerDelegate {
    var onResults: (([Reading]) -> Void)?
    var onStatus: ((SourceStatus) -> Void)?
    var onLocation: ((LocationAccess) -> Void)?

    var interval: TimeInterval = 5

    private let queue = DispatchQueue(label: "sigscan.wifi", qos: .userInitiated)
    private let locationManager = CLLocationManager()
    private var running = false
    private var generation = 0
    private var consecutiveFailures = 0

    override init() {
        super.init()
        locationManager.delegate = self
    }

    func start() {
        reportLocation()
        if locationManager.authorizationStatus == .notDetermined {
            requestLocation()
        }
        guard !running else { return }
        running = true
        generation += 1
        let gen = generation
        queue.async { [weak self] in self?.loop(gen) }
    }

    func stop() {
        running = false
    }

    func requestLocation() {
        locationManager.requestWhenInUseAuthorization()
        // On some macOS versions the prompt only appears once updates are requested.
        locationManager.startUpdatingLocation()
    }

    // MARK: - Scanning

    private func loop(_ gen: Int) {
        guard running, gen == generation else { return }
        let started = Date()
        scanOnce()
        let wait = max(0.5, interval - Date().timeIntervalSince(started))
        queue.asyncAfter(deadline: .now() + wait) { [weak self] in self?.loop(gen) }
    }

    private func scanOnce() {
        guard let iface = CWWiFiClient.shared().interface() else {
            post(status: .error("No Wi-Fi adapter found."))
            return
        }
        guard iface.powerOn() else {
            post(status: .off("Wi-Fi is turned off. Turn it on in Control Centre to scan."))
            return
        }
        do {
            let networks = try iface.scanForNetworks(withSSID: nil)
            consecutiveFailures = 0
            let connectedBSSID = iface.bssid()?.lowercased()
            let connectedSSID = iface.ssid()
            let now = Date()
            let sorted = networks.sorted {
                ($0.wlanChannel?.channelNumber ?? 0, $0.rssiValue) < ($1.wlanChannel?.channelNumber ?? 0, $1.rssiValue)
            }
            var readings: [Reading] = []
            for (index, net) in sorted.enumerated() {
                let channel = net.wlanChannel?.channelNumber
                let band = Self.bandName(net.wlanChannel?.channelBand.rawValue)
                let ssid = net.ssid ?? ""
                let bssid = net.bssid?.lowercased() ?? ""
                let id = !bssid.isEmpty ? bssid : "\(ssid.isEmpty ? "hidden" : ssid)@\(channel ?? 0)#\(index)"
                let connected = (!bssid.isEmpty && bssid == connectedBSSID)
                    || (bssid.isEmpty && !ssid.isEmpty && ssid == connectedSSID)
                readings.append(Reading(
                    kind: .wifi,
                    id: id,
                    name: ssid,
                    rssi: Double(net.rssiValue),
                    time: now,
                    channel: channel,
                    band: band,
                    security: Self.security(of: net),
                    connected: connected,
                    noise: Double(net.noiseMeasurement)
                ))
            }
            DispatchQueue.main.async { [weak self] in
                self?.onResults?(readings)
                self?.onStatus?(.ok)
            }
        } catch {
            // CoreWLAN refuses scans that come too quickly ("resource busy"); only
            // report a problem if it keeps happening.
            consecutiveFailures += 1
            if consecutiveFailures >= 3 {
                post(status: .error("Wi-Fi scan failed: \(error.localizedDescription)"))
            }
        }
    }

    private func post(status: SourceStatus) {
        DispatchQueue.main.async { [weak self] in self?.onStatus?(status) }
    }

    private static func bandName(_ raw: Int?) -> String? {
        switch raw {
        case 1: return "2.4 GHz"
        case 2: return "5 GHz"
        case 3: return "6 GHz"
        default: return nil
        }
    }

    /// CWSecurity raw values, most specific first (a WPA2/WPA3 transition
    /// network also "supports" plain WPA3 and WPA2).
    private static let securityChecks: [(Int, String)] = [
        (13, "WPA2/WPA3"), (12, "WPA3 Enterprise"), (11, "WPA3"), (9, "WPA2 Enterprise"),
        (4, "WPA2"), (3, "WPA/WPA2"), (7, "WPA Enterprise"), (2, "WPA"),
        (15, "OWE"), (14, "OWE"), (6, "WEP"), (1, "WEP"),
    ]

    private static func security(of net: CWNetwork) -> String {
        for (raw, label) in securityChecks {
            if let s = CWSecurity(rawValue: raw), net.supportsSecurity(s) {
                return label
            }
        }
        return "Open"
    }

    // MARK: - Location permission

    private func reportLocation() {
        // Raw values: 0 not determined, 1 restricted, 2 denied, 3 always, 4 when in use.
        let raw = locationManager.authorizationStatus.rawValue
        let access: LocationAccess = raw == 0 ? .notDetermined : (raw >= 3 ? .granted : .denied)
        DispatchQueue.main.async { [weak self] in self?.onLocation?(access) }
    }

    func locationManagerDidChangeAuthorization(_ manager: CLLocationManager) {
        reportLocation()
        if manager.authorizationStatus != .notDetermined {
            manager.stopUpdatingLocation()
        }
    }

    func locationManager(_ manager: CLLocationManager, didUpdateLocations locations: [CLLocation]) {
        manager.stopUpdatingLocation()  // we never use the location itself
    }

    func locationManager(_ manager: CLLocationManager, didFailWithError error: Error) {
        manager.stopUpdatingLocation()
    }
}
