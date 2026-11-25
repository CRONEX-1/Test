import AppKit
import Charts
import SwiftUI

// MARK: - Main window

struct ContentView: View {
    @EnvironmentObject var store: ScanStore
    @State private var section: DeviceKind? = .wifi
    @State private var selection: String?
    @State private var search = ""
    @AppStorage("sortByName") private var sortByName = false

    var body: some View {
        NavigationSplitView {
            List(selection: $section) {
                Section("Nearby") {
                    Label(DeviceKind.wifi.title, systemImage: DeviceKind.wifi.icon)
                        .badge(store.wifi.count)
                        .tag(DeviceKind.wifi)
                    Label(DeviceKind.bluetooth.title, systemImage: DeviceKind.bluetooth.icon)
                        .badge(store.bluetooth.count)
                        .tag(DeviceKind.bluetooth)
                }
            }
            .navigationSplitViewColumnWidth(min: 190, ideal: 210)
        } content: {
            DeviceListView(kind: section ?? .wifi, selection: $selection, search: search, sortByName: sortByName)
                .searchable(text: $search, placement: .toolbar, prompt: "Filter by name or address")
                .navigationSplitViewColumnWidth(min: 560, ideal: 760)
                .toolbar {
                    ToolbarItemGroup(placement: .primaryAction) {
                        Picker("Sort", selection: $sortByName) {
                            Text("Strongest").tag(false)
                            Text("Name").tag(true)
                        }
                        .pickerStyle(.segmented)
                        .help("Sort order")

                        Button {
                            store.togglePause()
                        } label: {
                            Label(store.paused ? "Resume" : "Pause", systemImage: store.paused ? "play.fill" : "pause.fill")
                        }
                        .help(store.paused ? "Resume scanning" : "Pause scanning")

                        Button {
                            store.exportCSV()
                        } label: {
                            Label("Export", systemImage: "square.and.arrow.up")
                        }
                        .help("Save the current list as a CSV file")
                    }
                }
        } detail: {
            if let id = selection, let record = store.record(id) {
                TrackView(record: record)
            } else {
                EmptyDetail()
            }
        }
    }
}

struct DeviceListView: View {
    @EnvironmentObject var store: ScanStore
    let kind: DeviceKind
    @Binding var selection: String?
    let search: String
    let sortByName: Bool

    private var rows: [DeviceRecord] {
        var list = kind == .wifi ? store.wifi : store.bluetooth
        let q = search.trimmingCharacters(in: .whitespaces).lowercased()
        if !q.isEmpty {
            list = list.filter {
                $0.latest.name.lowercased().contains(q) || $0.latest.id.lowercased().contains(q)
                    || ($0.latest.vendor ?? "").lowercased().contains(q)
            }
        }
        if sortByName {
            list.sort { $0.displayName.localizedCaseInsensitiveCompare($1.displayName) == .orderedAscending }
        }
        return list
    }

    var body: some View {
        VStack(spacing: 0) {
            StatusBanners(kind: kind)
            if kind == .wifi {
                WiFiTable(rows: rows, selection: $selection)
            } else {
                BluetoothTable(rows: rows, selection: $selection)
            }
            Divider()
            footer
        }
        .navigationTitle(kind.title)
    }

    private var footer: some View {
        HStack {
            Text("\(rows.count) \(kind == .wifi ? "networks" : "devices")")
            if store.paused {
                Text("· Paused").foregroundStyle(.orange)
            }
            Spacer()
            Text(kind == .wifi ? "Rescans every 5 s" : "Listening continuously")
            Text("· Distances are rough estimates")
        }
        .font(.caption)
        .foregroundStyle(.secondary)
        .padding(.horizontal, 12)
        .padding(.vertical, 6)
    }
}

// MARK: - Tables

struct WiFiTable: View {
    let rows: [DeviceRecord]
    @Binding var selection: String?

    var body: some View {
        Table(rows, selection: $selection) {
            TableColumn("Network") { (d: DeviceRecord) in
                HStack(spacing: 6) {
                    if d.latest.connected {
                        Image(systemName: "checkmark.circle.fill").foregroundStyle(.green).help("Connected")
                    }
                    Text(d.displayName).foregroundStyle(d.hasName ? .primary : .secondary)
                }
            }
            .width(min: 140, ideal: 190)
            TableColumn("Signal") { (d: DeviceRecord) in SignalCell(dbm: d.smoothed, kind: .wifi) }
                .width(min: 120, ideal: 140)
            TableColumn("Quality") { (d: DeviceRecord) in QualityCell(dbm: d.smoothed, kind: .wifi) }
                .width(min: 70, ideal: 80)
            TableColumn("Trend") { (d: DeviceRecord) in TrendCell(delta: d.trend) }
                .width(min: 44, ideal: 50)
            TableColumn("Distance") { (d: DeviceRecord) in Text(Signal.formatDistance(d.distance)).monospacedDigit() }
                .width(min: 60, ideal: 70)
            TableColumn("Channel") { (d: DeviceRecord) in Text(d.latest.channel.map(String.init) ?? "").monospacedDigit() }
                .width(min: 50, ideal: 56)
            TableColumn("Band") { (d: DeviceRecord) in Text(d.latest.band ?? "") }
                .width(min: 50, ideal: 60)
            TableColumn("Security") { (d: DeviceRecord) in Text(d.latest.security ?? "").foregroundStyle(.secondary) }
                .width(min: 60, ideal: 90)
            TableColumn("BSSID") { (d: DeviceRecord) in
                Text(d.latest.id.contains(":") ? d.latest.id : "").font(.system(.body, design: .monospaced)).foregroundStyle(.secondary)
            }
            .width(min: 120, ideal: 140)
        }
    }
}

struct BluetoothTable: View {
    let rows: [DeviceRecord]
    @Binding var selection: String?

    var body: some View {
        Table(rows, selection: $selection) {
            TableColumn("Name") { (d: DeviceRecord) in
                Text(d.displayName).foregroundStyle(d.hasName ? .primary : .secondary)
            }
            .width(min: 120, ideal: 170)
            TableColumn("Vendor / type") { (d: DeviceRecord) in
                Text([d.latest.vendor, d.latest.deviceType].compactMap { $0 }.joined(separator: " · "))
            }
            .width(min: 100, ideal: 160)
            TableColumn("Signal") { (d: DeviceRecord) in SignalCell(dbm: d.smoothed, kind: .bluetooth) }
                .width(min: 120, ideal: 140)
            TableColumn("Proximity") { (d: DeviceRecord) in QualityCell(dbm: d.smoothed, kind: .bluetooth) }
                .width(min: 70, ideal: 84)
            TableColumn("Trend") { (d: DeviceRecord) in TrendCell(delta: d.trend) }
                .width(min: 44, ideal: 50)
            TableColumn("Distance") { (d: DeviceRecord) in Text(Signal.formatDistance(d.distance)).monospacedDigit() }
                .width(min: 60, ideal: 70)
            TableColumn("Seen") { (d: DeviceRecord) in
                Text("\(Int(d.age()))s ago").monospacedDigit().foregroundStyle(d.age() > 10 ? Color.orange : Color.secondary)
            }
            .width(min: 50, ideal: 60)
        }
    }
}

// MARK: - Cells and small views

struct Meter: View {
    let fraction: Double
    let color: Color
    var height: CGFloat = 8

    var body: some View {
        GeometryReader { geo in
            ZStack(alignment: .leading) {
                Capsule().fill(Color.secondary.opacity(0.2))
                Capsule().fill(color.gradient).frame(width: max(height, geo.size.width * fraction))
            }
        }
        .frame(height: height)
        .animation(.easeOut(duration: 0.4), value: fraction)
    }
}

struct SignalCell: View {
    let dbm: Double
    let kind: DeviceKind

    var body: some View {
        HStack(spacing: 8) {
            Meter(fraction: Signal.fraction(dbm, kind), color: Signal.quality(dbm, kind).color)
                .frame(width: 60)
            Text(Signal.dbmText(dbm)).monospacedDigit()
        }
    }
}

struct QualityCell: View {
    let dbm: Double
    let kind: DeviceKind

    var body: some View {
        let q = Signal.quality(dbm, kind)
        HStack(spacing: 5) {
            Circle().fill(q.color).frame(width: 7, height: 7)
            Text(q.label)
        }
    }
}

struct TrendCell: View {
    let delta: Double

    var body: some View {
        if delta >= 2 {
            Image(systemName: "arrow.up").foregroundStyle(.green).help(String(format: "%+.0f dB", delta))
        } else if delta <= -2 {
            Image(systemName: "arrow.down").foregroundStyle(.red).help(String(format: "%+.0f dB", delta))
        } else {
            Image(systemName: "minus").foregroundStyle(.tertiary)
        }
    }
}

// MARK: - Permission / status banners

struct StatusBanners: View {
    @EnvironmentObject var store: ScanStore
    let kind: DeviceKind

    var body: some View {
        VStack(spacing: 0) {
            if kind == .wifi {
                if store.location != .granted {
                    Banner(
                        icon: "location.slash",
                        message: "Network names are hidden. macOS only shows Wi-Fi names to apps allowed to use Location Services.",
                        button: store.location == .notDetermined ? "Allow…" : "Open Settings"
                    ) {
                        if store.location == .notDetermined {
                            store.requestLocation()
                        } else {
                            SystemSettings.open(.location)
                        }
                    }
                }
                banner(for: store.wifiStatus, settings: nil)
            } else {
                banner(for: store.bluetoothStatus, settings: .bluetooth)
            }
        }
    }

    @ViewBuilder
    private func banner(for status: SourceStatus, settings: SystemSettings.Pane?) -> some View {
        switch status {
        case .off(let message):
            Banner(icon: "power", message: message)
        case .denied(let message):
            Banner(icon: "hand.raised", message: message + " Allow it in System Settings, then reopen Sigscan.", button: "Open Settings") {
                if let pane = settings { SystemSettings.open(pane) }
            }
        case .error(let message):
            Banner(icon: "exclamationmark.triangle", message: message)
        case .starting, .ok:
            EmptyView()
        }
    }
}

struct Banner: View {
    let icon: String
    let message: String
    var button: String? = nil
    var action: (() -> Void)? = nil

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: icon).foregroundStyle(.orange)
            Text(message).fixedSize(horizontal: false, vertical: true)
            Spacer()
            if let button = button, let action = action {
                Button(button, action: action)
            }
        }
        .font(.callout)
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
        .background(Color.orange.opacity(0.12))
    }
}

enum SystemSettings {
    enum Pane { case location, bluetooth }

    static func open(_ pane: Pane) {
        let anchor = pane == .location ? "Privacy_LocationServices" : "Privacy_Bluetooth"
        if let url = URL(string: "x-apple.systempreferences:com.apple.preference.security?\(anchor)") {
            NSWorkspace.shared.open(url)
        }
    }
}

// MARK: - Tracking detail

struct EmptyDetail: View {
    var body: some View {
        VStack(spacing: 12) {
            Image(systemName: "scope")
                .font(.system(size: 44))
                .foregroundStyle(.secondary)
            Text("Select a device")
                .font(.title2.weight(.semibold))
            Text("Pick a network or Bluetooth device to follow its signal live. Walk around with your Mac: the meter grows as you get closer.")
                .multilineTextAlignment(.center)
                .foregroundStyle(.secondary)
                .frame(maxWidth: 320)
        }
        .padding()
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}

struct TrackView: View {
    let record: DeviceRecord
    @AppStorage("beepEnabled") private var beepEnabled = false

    var body: some View {
        let value = record.smoothed
        let quality = Signal.quality(value, record.kind)
        let age = record.age()

        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                header

                HStack(alignment: .firstTextBaseline) {
                    Text(String(format: "%.0f", value))
                        .font(.system(size: 64, weight: .bold, design: .rounded))
                        .monospacedDigit()
                        .foregroundStyle(quality.color)
                    Text("dBm").font(.title2).foregroundStyle(.secondary)
                    Spacer()
                    VStack(alignment: .trailing, spacing: 4) {
                        Text(quality.label).font(.title2.weight(.semibold)).foregroundStyle(quality.color)
                        Text("\(Signal.formatDistance(record.distance)) away (estimate)").foregroundStyle(.secondary)
                    }
                }

                Meter(fraction: Signal.fraction(value, record.kind), color: quality.color, height: 18)

                trendMessage

                if age > 10 {
                    Label("Not heard from for \(Int(age)) seconds. It may be out of range or switched off.", systemImage: "exclamationmark.triangle")
                        .foregroundStyle(.orange)
                }

                VStack(alignment: .leading, spacing: 6) {
                    Text("Signal history").font(.headline)
                    HistoryChart(history: record.history)
                        .frame(height: 180)
                }

                HStack(spacing: 12) {
                    Stat(title: "Min", value: Signal.dbmText(record.minRSSI))
                    Stat(title: "Average", value: Signal.dbmText(record.average(last: 30)))
                    Stat(title: "Max", value: Signal.dbmText(record.maxRSSI))
                    Stat(title: "Samples", value: "\(record.count)")
                    Stat(title: "Last seen", value: String(format: "%.0fs ago", age))
                }

                Toggle("Beep faster as the signal gets stronger", isOn: $beepEnabled)

                Text("Signal strength in dBm: closer to 0 is stronger. Bodies, walls and how you hold your Mac can change a reading by 10 dB, so move slowly and watch the trend.")
                    .font(.callout)
                    .foregroundStyle(.secondary)
            }
            .padding(24)
        }
        .task(id: beepEnabled ? record.id : "") {
            guard beepEnabled else { return }
            await beepLoop(id: record.id)
        }
    }

    @EnvironmentObject private var store: ScanStore

    private var header: some View {
        HStack(spacing: 12) {
            Image(systemName: record.kind.icon)
                .font(.system(size: 28))
                .foregroundStyle(.secondary)
                .frame(width: 40)
            VStack(alignment: .leading, spacing: 2) {
                Text(record.displayName).font(.title.weight(.semibold))
                Text(details).foregroundStyle(.secondary).textSelection(.enabled)
            }
        }
    }

    private var details: String {
        let r = record.latest
        var parts: [String] = []
        if r.kind == .wifi {
            if !r.id.contains("#") { parts.append(r.id) }
            if let ch = r.channel { parts.append("Channel \(ch)") }
            if let band = r.band { parts.append(band) }
            if let sec = r.security { parts.append(sec) }
            if r.connected { parts.append("Connected") }
        } else {
            if let v = r.vendor { parts.append(v) }
            if let t = r.deviceType { parts.append(t) }
            if let tx = r.txPower { parts.append("TX \(tx) dBm") }
            parts.append(String(r.id.prefix(8)))
        }
        return parts.joined(separator: " · ")
    }

    @ViewBuilder
    private var trendMessage: some View {
        let delta = record.trend
        if delta >= 2 {
            Label(String(format: "Getting stronger (%+.1f dB): you're moving closer", delta), systemImage: "arrow.up.circle.fill")
                .foregroundStyle(.green).font(.title3)
        } else if delta <= -2 {
            Label(String(format: "Getting weaker (%+.1f dB): you're moving away", delta), systemImage: "arrow.down.circle.fill")
                .foregroundStyle(.red).font(.title3)
        } else {
            Label("Steady", systemImage: "equal.circle").foregroundStyle(.secondary).font(.title3)
        }
    }

    /// Geiger-counter style: the stronger the signal, the faster the beeps.
    private func beepLoop(id: String) async {
        while !Task.isCancelled {
            var wait = 2.5
            if let current = store.record(id), current.age() < 5 {
                let (low, high) = current.kind == .bluetooth ? (-95.0, -45.0) : (-90.0, -35.0)
                let frac = min(1, max(0, (current.smoothed - low) / (high - low)))
                wait = 2.5 - frac * 2.3
                NSSound(named: "Tink")?.play()
            }
            try? await Task.sleep(nanoseconds: UInt64(wait * 1_000_000_000))
        }
    }
}

struct Stat: View {
    let title: String
    let value: String

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(title).font(.caption).foregroundStyle(.secondary)
            Text(value).font(.body.weight(.medium)).monospacedDigit()
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(10)
        .background(RoundedRectangle(cornerRadius: 8).fill(Color.secondary.opacity(0.08)))
    }
}

struct HistoryChart: View {
    let history: [Sample]

    var body: some View {
        let points = Array(history.enumerated())
        Chart(points, id: \.offset) { item in
            AreaMark(
                x: .value("Time", item.element.time),
                yStart: .value("Floor", -100.0),
                yEnd: .value("dBm", clamp(item.element.rssi))
            )
            .foregroundStyle(LinearGradient(colors: [.accentColor.opacity(0.35), .accentColor.opacity(0.02)], startPoint: .top, endPoint: .bottom))
            .interpolationMethod(.monotone)
            LineMark(x: .value("Time", item.element.time), y: .value("dBm", clamp(item.element.rssi)))
                .foregroundStyle(Color.accentColor)
                .interpolationMethod(.monotone)
        }
        .chartYScale(domain: -100.0 ... -20.0)
        .chartYAxis {
            AxisMarks(position: .leading, values: [-100.0, -80.0, -60.0, -40.0, -20.0])
        }
        .chartXAxis {
            AxisMarks(values: .automatic(desiredCount: 4)) { _ in
                AxisGridLine()
                AxisValueLabel(format: .dateTime.hour().minute().second())
            }
        }
    }

    private func clamp(_ v: Double) -> Double { min(-20, max(-100, v)) }
}
