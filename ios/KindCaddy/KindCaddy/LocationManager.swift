import CoreLocation
import Foundation

class LocationManager: NSObject, ObservableObject, CLLocationManagerDelegate {
    @Published var latitude: Double?
    @Published var longitude: Double?
    @Published var authorizationStatus: CLAuthorizationStatus = .notDetermined
    @Published var locationError: String?

    private let manager = CLLocationManager()

    override init() {
        super.init()
        manager.delegate = self
        manager.desiredAccuracy = kCLLocationAccuracyHundredMeters
        manager.distanceFilter = 50
        authorizationStatus = manager.authorizationStatus
        // Seed immediately from iOS cached location — same source Apple Weather uses.
        // Fresh GPS updates will overwrite as they arrive.
        if let cached = manager.location {
            latitude = cached.coordinate.latitude
            longitude = cached.coordinate.longitude
        }
    }

    func requestPermission() {
        manager.requestWhenInUseAuthorization()
    }

    func startUpdating() {
        guard authorizationStatus == .authorizedWhenInUse || authorizationStatus == .authorizedAlways else { return }
        manager.startUpdatingLocation()
    }

    func stopUpdating() {
        manager.stopUpdatingLocation()
    }

    var hasLocation: Bool {
        latitude != nil && longitude != nil
    }

    // MARK: - CLLocationManagerDelegate

    func locationManager(_ manager: CLLocationManager, didUpdateLocations locations: [CLLocation]) {
        guard let loc = locations.last else { return }
        latitude = loc.coordinate.latitude
        longitude = loc.coordinate.longitude
    }

    func locationManagerDidChangeAuthorization(_ manager: CLLocationManager) {
        authorizationStatus = manager.authorizationStatus
        switch manager.authorizationStatus {
        case .authorizedWhenInUse, .authorizedAlways:
            locationError = nil
            manager.startUpdatingLocation()
        case .denied:
            locationError = "Location access denied. Enable in Settings > KindCaddy for live weather."
        case .restricted:
            locationError = "Location access restricted on this device."
        default:
            break
        }
    }

    func locationManager(_ manager: CLLocationManager, didFailWithError error: Error) {
        if let clError = error as? CLError, clError.code == .denied {
            locationError = "Location access denied. Enable in Settings > KindCaddy for live weather."
        }
    }
}
