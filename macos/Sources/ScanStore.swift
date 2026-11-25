import AppKit
import Foundation
import SwiftUI
import UniformTypeIdentifiers

/// Collects readings from both scanners and publishes sorted snapshots to the UI.
/// All mutation happens on the main thread (the scanners deliver there).
final class ScanStore: ObservableObject {
    @Published private(set) var wifi: [DeviceRecord] = []
    @Published private(set) var bluetooth: [DeviceRecord] = []
    @Published private(set) var wifiStatus: SourceStatus = .starting
    @Published private(set) var bluetoothStatus: SourceStatus = .starting
    @Published private(set) var location: LocationAccess = .notDetermined
    @Published private(set) var paused = false
    @Published private(set) var wifiScans = 0
    @Published private(set) var lastWiFiScan: Date?

    /// Devices not heard from for this long are hidden.
    let staleAfter: TimeInterval = 30

    private var records: [String: DeviceRecord] = [:]
    private let wifiScanner = WiFiScanner()
    private let bluetoothScanner = BluetoothScanner()
    private var timer: Timer?
    private var started = false
    private var pausedAt: Date?

    init() {
        wifiScanner.onResults = { [weak self] readings in self?.ingestWiFi(readings) }
        wifiScanner.onStatus = { [weak self] status in self?.wifiStatus = status }
        wifiScanner.onLocation = { [weak self] access in self?.location = access }
        bluetoothScanner.onReading = { [weak self] reading in self?.ingest(reading) }
        bluetoothScanner.onStatus = { [weak self] status in self?.bluetoothStatus = status }
    }

    func start() {
        guard !started else { return }
        started = true
        wifiScanner.start()
        bluetoothScanner.start()
        let timer = Timer(timeInterval: 0.5, repeats: true) { [weak self] _ in self?.publish() }
        RunLoop.main.add(timer, forMode: .common)
        self.timer = timer
    }

    func togglePause() {
        paused.toggle()
        if paused {
            pausedAt = Date()
            wifiScanner.stop()
            bluetoothScanner.stop()
        } else {
            pausedAt = nil
            wifiScanner.start()
            bluetoothScanner.start()
        }
        publish()
    }

    func clear() {
        records.removeAll()
        publish()
    }

    func requestLocation() {
        wifiScanner.requestLocation()
    }

    func record(_ id: String) -> DeviceRecord? {
        records[id]
    }

    // MARK: - Ingest

    private func ingestWiFi(_ readings: [Reading]) {
        guard !paused else { return }
        readings.forEach(ingest)
        wifiScans += 1
        lastWiFiScan = Date()
    }

    private func ingest(_ reading: Reading) {
        guard !paused else { return }
        let key = DeviceRecord.key(reading.kind, reading.id)
        if records[key] != nil {
            records[key]!.add(reading)
        } else {
            records[key] = DeviceRecord(reading)
        }
    }

    private func publish() {
        let now = pausedAt ?? Date()
        let live = records.values.filter { $0.age(now) <= staleAfter }
        let sorted = live.sorted { $0.smoothed > $1.smoothed }
        wifi = sorted.filter { $0.kind == .wifi }
        bluetooth = sorted.filter { $0.kind == .bluetooth }
    }

    // MARK: - Export

    func exportCSV() {
        let panel = NSSavePanel()
        panel.allowedContentTypes = [.commaSeparatedText]
        let stamp = Self.fileStamp.string(from: Date())
        panel.nameFieldStringValue = "sigscan-\(stamp).csv"
        guard panel.runModal() == .OK, let url = panel.url else { return }

        let iso = ISO8601DateFormatter()
        var lines = [
            "kind,id,name,rssi_avg_dbm,rssi_min_dbm,rssi_max_dbm,samples,channel,band,security,vendor,device_type,est_distance_m,last_seen"
        ]
        for d in wifi + bluetooth {
            let r = d.latest
            let avg = d.average(last: d.history.count)
            let fields: [String] = [
                r.kind.rawValue, r.id, r.name,
                String(format: "%.1f", avg), String(format: "%.0f", d.minRSSI), String(format: "%.0f", d.maxRSSI),
                String(d.count), r.channel.map(String.init) ?? "", r.band ?? "", r.security ?? "",
                r.vendor ?? "", r.deviceType ?? "",
                String(format: "%.2f", Signal.distance(for: r, rssi: avg)), iso.string(from: d.lastSeen),
            ]
            lines.append(fields.map(Self.csvEscape).joined(separator: ","))
        }
        do {
            try (lines.joined(separator: "\n") + "\n").write(to: url, atomically: true, encoding: .utf8)
        } catch {
            let alert = NSAlert(error: error)
            alert.runModal()
        }
    }

    private static let fileStamp: DateFormatter = {
        let f = DateFormatter()
        f.dateFormat = "yyyy-MM-dd-HHmm"
        return f
    }()

    private static func csvEscape(_ s: String) -> String {
        if s.contains(",") || s.contains("\"") || s.contains("\n") {
            return "\"" + s.replacingOccurrences(of: "\"", with: "\"\"") + "\""
        }
        return s
    }
}
