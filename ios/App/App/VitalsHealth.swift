import Foundation
import Capacitor

/// VitalsHealth — CAPPlugin (cascara delgada) que expone HealthSyncManager a JS.
///
/// Capacitor 6+/8 registra plugins via `CAPBridgedPlugin` (identifier/jsName/
/// pluginMethods), sin la macro vieja `CAP_PLUGIN`. Registrado en
/// `capacitor.config.json` -> `packageClassList: ["VitalsHealth"]`.
///
/// Metodos expuestos a JS (window.Capacitor.Plugins.VitalsHealth):
///   - setConfig({url, token})        -> {}
///   - requestAuthorization()         -> {granted: Bool}
///   - sync()                         -> {status, http?, n_days?, message?}
///
/// Toda la logica HealthKit vive en HealthSyncManager (Swift puro), para que
/// SceneDelegate pueda dispararla en auto-sync sin pasar por el bridge.
@objc(VitalsHealth)
public class VitalsHealth: CAPPlugin, CAPBridgedPlugin {
    public let identifier = "VitalsHealth"
    public let jsName = "VitalsHealth"
    public let pluginMethods: [CAPPluginMethod] = [
        CAPPluginMethod(name: "setConfig", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "requestAuthorization", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "sync", returnType: CAPPluginReturnPromise),
    ]

    @objc func setConfig(_ call: CAPPluginCall) {
        let url = call.getString("url") ?? ""
        let token = call.getString("token") ?? ""
        // Fase 8D (paso D3, household): 'user' es opcional — el QR de conexión
        // puede traerlo embebido (instancias multi-usuario); si no viene,
        // getString devuelve nil y setConfig no toca el usuario ya configurado.
        let user = call.getString("user")
        HealthSyncManager.shared.setConfig(url: url, token: token, user: user)
        call.resolve()
    }

    @objc func requestAuthorization(_ call: CAPPluginCall) {
        HealthSyncManager.shared.requestAuthorization { granted, error in
            if let error = error {
                // Error real al pedir autorizacion (no "usuario nego") -> aun asi
                // resolvemos con granted:false en vez de reject, para que el JS
                // no tenga que manejar dos caminos de error distintos.
                call.resolve(["granted": false, "message": error.localizedDescription])
                return
            }
            call.resolve(["granted": granted])
        }
    }

    @objc func sync(_ call: CAPPluginCall) {
        HealthSyncManager.shared.sync { result in
            call.resolve(result)
        }
    }
}
