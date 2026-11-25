import Foundation
import SwiftUI

enum DeviceKind: String, Hashable, CaseIterable {
    case wifi, bluetooth

    var title: String { self == .wifi ? "Wi-Fi Networks" : "Bluetooth Devices" }
    var icon: String { self == .wifi ? "wifi" : "dot.radiowaves.left.and.right" }
}

/// One observation of one transmitter.
struct Reading {
    var kind: DeviceKind
    var id: String
    var name: String
    var rssi: Double
    var time: Date = Date()
    var channel: Int? = nil
    var band: String? = nil
    var security: String? = nil
    var connected: Bool = false
    var noise: Double? = nil
    var txPower: Int? = nil
    var vendor: String? = nil
    var deviceType: String? = nil

    var frequency: Int? { Signal.frequency(channel: channel, band: band) }
}

struct Sample {
    let time: Date
    let rssi: Double
}

/// Everything we know about one device, plus its recent signal history.
struct DeviceRecord: Identifiable {
    static let historyLimit = 150

    let id: String
    private(set) var latest: Reading
    private(set) var history: [Sample] = []
    private(set) var minRSSI: Double
    private(set) var maxRSSI: Double
    private(set) var count = 0
    let firstSeen: Date

    static func key(_ kind: DeviceKind, _ id: String) -> String { "\(kind.rawValue):\(id)" }

    init(_ reading: Reading) {
        id = DeviceRecord.key(reading.kind, reading.id)
        latest = reading
        minRSSI = reading.rssi
        maxRSSI = reading.rssi
        firstSeen = reading.time
        add(reading)
    }

    mutating func add(_ new: Reading) {
        var r = new
        // Keep details that only arrive in some advertisements / scans.
        if r.name.isEmpty { r.name = latest.name }
        if r.vendor == nil { r.vendor = latest.vendor }
        if r.deviceType == nil { r.deviceType = latest.deviceType }
        if r.txPower == nil { r.txPower = latest.txPower }
        if r.channel == nil { r.channel = latest.channel; r.band = latest.band }
        if r.security == nil { r.security = latest.security }
        latest = r
        count += 1
        minRSSI = min(minRSSI, r.rssi)
        maxRSSI = max(maxRSSI, r.rssi)
        history.append(Sample(time: r.time, rssi: r.rssi))
        if history.count > Self.historyLimit {
            history.removeFirst(history.count - Self.historyLimit)
        }
    }

    var kind: DeviceKind { latest.kind }
    var lastSeen: Date { latest.time }
    var hasName: Bool { !latest.name.isEmpty }
    var displayName: String {
        hasName ? latest.name : (kind == .wifi ? "Hidden network" : "Unnamed device")
    }

    func average(last n: Int) -> Double {
        let values = history.suffix(n).map(\.rssi)
        return values.isEmpty ? latest.rssi : values.reduce(0, +) / Double(values.count)
    }

    /// Average of the last few readings: steadier than the raw value.
    var smoothed: Double { average(last: 5) }

    /// dB change between the newest 3 readings and the 3 before them. Positive = getting stronger.
    var trend: Double {
        let values = history.map(\.rssi)
        let window = 3
        guard values.count > window else { return 0 }
        let recent = Array(values.suffix(window))
        let before = Array(values.dropLast(window).suffix(window))
        func mean(_ a: [Double]) -> Double { a.reduce(0, +) / Double(a.count) }
        return mean(recent) - mean(before)
    }

    func age(_ now: Date = Date()) -> TimeInterval { now.timeIntervalSince(lastSeen) }

    var distance: Double { Signal.distance(for: latest, rssi: smoothed) }
}

/// Signal-strength maths: quality ratings and rough distance estimates.
enum Signal {
    /// 2 = open space, ~3 = indoors, 4+ = many walls.
    static let pathLossExponent = 3.0

    struct Quality {
        let label: String
        let level: Int  // 0 (worst) ... 4 (best)
        var color: Color { Signal.color(level: level) }
    }

    static func quality(_ dbm: Double, _ kind: DeviceKind) -> Quality {
        let levels: [(Double, String)] = kind == .wifi
            ? [(-50, "Excellent"), (-60, "Good"), (-67, "Fair"), (-75, "Weak")]
            : [(-55, "Very close"), (-67, "Near"), (-80, "Medium"), (-90, "Far")]
        for (i, (threshold, label)) in levels.enumerated() where dbm >= threshold {
            return Quality(label: label, level: 4 - i)
        }
        return Quality(label: kind == .wifi ? "Very weak" : "Very far", level: 0)
    }

    static func color(level: Int) -> Color {
        switch level {
        case 4: return .green
        case 3: return Color(nsColor: .systemGreen).opacity(0.8)
        case 2: return Color(nsColor: .systemYellow)
        case 1: return .orange
        default: return .red
        }
    }

    /// 0...1 for drawing meters.
    static func fraction(_ dbm: Double, _ kind: DeviceKind) -> Double {
        let floor = -100.0
        let ceiling = kind == .wifi ? -30.0 : -40.0
        return min(1, max(0, (dbm - floor) / (ceiling - floor)))
    }

    static func distance(for reading: Reading, rssi: Double) -> Double {
        let n = pathLossExponent
        if reading.kind == .bluetooth {
            // RSSI expected at 1 m: advertised TX power minus ~41 dB, or a typical -59 dBm.
            let ref = reading.txPower.map { Double($0) - 41 } ?? -59
            return pow(10, (ref - rssi) / (10 * n))
        }
        // Wi-Fi: ~20 dBm access point, free-space loss at 1 m plus ~10 dB of real-world losses.
        let f = Double(reading.frequency ?? 2437)
        let lossAt1m = 20 * log10(f) - 27.55 + 10
        return pow(10, ((20 - rssi) - lossAt1m) / (10 * n))
    }

    static func formatDistance(_ metres: Double) -> String {
        if metres < 1 { return "< 1 m" }
        if metres < 10 { return String(format: "~%.1f m", metres) }
        if metres > 200 { return "> 200 m" }
        return String(format: "~%.0f m", metres)
    }

    static func frequency(channel: Int?, band: String?) -> Int? {
        guard let ch = channel, ch > 0 else { return nil }
        switch band {
        case "6 GHz": return 5950 + ch * 5
        case "5 GHz": return 5000 + ch * 5
        default:
            if ch == 14 { return 2484 }
            if ch <= 13 { return 2407 + ch * 5 }
            return 5000 + ch * 5
        }
    }

    static func dbmText(_ dbm: Double) -> String { String(format: "%.0f dBm", dbm) }
}
