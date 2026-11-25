import CoreBluetooth
import Foundation

/// Listens for Bluetooth LE advertisements with CoreBluetooth.
///
/// Devices advertise many times a second and BLE RSSI is noisy, so readings are
/// averaged into one sample per device per `sampleInterval`.
final class BluetoothScanner: NSObject, CBCentralManagerDelegate {
    var onReading: ((Reading) -> Void)?
    var onStatus: ((SourceStatus) -> Void)?

    var sampleInterval: TimeInterval = 1.0

    private var central: CBCentralManager?
    private var wantScanning = false
    private var pending: [UUID: [Double]] = [:]
    private var lastEmit: [UUID: Date] = [:]

    func start() {
        wantScanning = true
        if let central = central {
            if central.state == .poweredOn { beginScan() }
        } else {
            // Callbacks arrive on the main queue.
            central = CBCentralManager(delegate: self, queue: nil, options: [CBCentralManagerOptionShowPowerAlertKey: false])
        }
    }

    func stop() {
        wantScanning = false
        central?.stopScan()
    }

    private func beginScan() {
        central?.scanForPeripherals(withServices: nil, options: [CBCentralManagerScanOptionAllowDuplicatesKey: true])
    }

    func centralManagerDidUpdateState(_ central: CBCentralManager) {
        switch central.state {
        case .poweredOn:
            onStatus?(.ok)
            if wantScanning { beginScan() }
        case .poweredOff:
            onStatus?(.off("Bluetooth is turned off. Turn it on in Control Centre to scan."))
        case .unauthorized:
            onStatus?(.denied("Sigscan isn't allowed to use Bluetooth."))
        case .unsupported:
            onStatus?(.error("This Mac doesn't support Bluetooth LE scanning."))
        case .resetting, .unknown:
            onStatus?(.starting)
        @unknown default:
            onStatus?(.starting)
        }
    }

    func centralManager(
        _ central: CBCentralManager,
        didDiscover peripheral: CBPeripheral,
        advertisementData: [String: Any],
        rssi RSSI: NSNumber
    ) {
        let rssi = RSSI.doubleValue
        guard rssi > -127, rssi < 20 else { return }  // 127 means "not available"

        let id = peripheral.identifier
        pending[id, default: []].append(rssi)
        let now = Date()
        if let last = lastEmit[id], now.timeIntervalSince(last) < sampleInterval { return }
        let values = pending[id] ?? [rssi]
        pending[id] = []
        lastEmit[id] = now
        let average = values.reduce(0, +) / Double(values.count)

        let manufacturer = advertisementData[CBAdvertisementDataManufacturerDataKey] as? Data
        let services = (advertisementData[CBAdvertisementDataServiceUUIDsKey] as? [CBUUID]) ?? []
        let name = (advertisementData[CBAdvertisementDataLocalNameKey] as? String) ?? peripheral.name ?? ""
        let tx = (advertisementData[CBAdvertisementDataTxPowerLevelKey] as? NSNumber)?.intValue

        onReading?(Reading(
            kind: .bluetooth,
            id: id.uuidString,
            name: name,
            rssi: (average * 10).rounded() / 10,
            time: now,
            txPower: tx,
            vendor: Vendors.vendor(manufacturer),
            deviceType: Vendors.deviceType(manufacturer, services: services)
        ))
    }
}

enum Vendors {
    /// Bluetooth SIG company identifiers for some common manufacturers.
    static let companies: [UInt16: String] = [
        0x0002: "Intel", 0x0006: "Microsoft", 0x000A: "Qualcomm", 0x000D: "Texas Instruments",
        0x000F: "Broadcom", 0x001D: "Qualcomm", 0x0046: "MediaTek", 0x004C: "Apple",
        0x0059: "Nordic Semi", 0x005D: "Realtek", 0x0075: "Samsung", 0x0087: "Garmin",
        0x009E: "Bose", 0x00C4: "LG", 0x00E0: "Google", 0x012D: "Sony",
        0x0157: "Huami (Amazfit)", 0x0171: "Amazon", 0x027D: "Huawei", 0x02E5: "Espressif",
        0x038F: "Xiaomi", 0x0499: "Ruuvi",
    ]

    /// Apple "Continuity" message types (third byte of Apple manufacturer data).
    static let appleTypes: [UInt8: String] = [
        0x02: "iBeacon", 0x05: "AirDrop", 0x07: "AirPods / Beats", 0x09: "AirPlay",
        0x0C: "Handoff", 0x0F: "Nearby action", 0x10: "Apple device", 0x12: "Find My tag",
    ]

    /// 16-bit service UUIDs that identify a device category.
    static let services: [String: String] = [
        "FEED": "Tile tracker", "FD5A": "SmartTag", "FE2C": "Fast Pair", "180D": "Heart-rate sensor",
        "1812": "Keyboard / mouse", "FE9F": "Google device", "FD6F": "Exposure notification",
    ]

    private static func companyID(_ data: Data?) -> UInt16? {
        guard let data = data, data.count >= 2 else { return nil }
        let bytes = [UInt8](data)
        return UInt16(bytes[0]) | (UInt16(bytes[1]) << 8)
    }

    static func vendor(_ data: Data?) -> String? {
        guard let id = companyID(data) else { return nil }
        return companies[id] ?? String(format: "0x%04X", id)
    }

    static func deviceType(_ data: Data?, services uuids: [CBUUID]) -> String? {
        if companyID(data) == 0x004C, let data = data, data.count >= 3 {
            return appleTypes[[UInt8](data)[2]]
        }
        for uuid in uuids {
            if let hint = services[uuid.uuidString.uppercased()] { return hint }
        }
        return nil
    }
}
