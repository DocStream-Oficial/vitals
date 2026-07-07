import Foundation
import HealthKit
import BackgroundTasks

/// HealthSyncManager — logica pura de HealthKit (Fase 5D-B).
///
/// Singleton Swift puro (sin dependencia del bridge Capacitor) para que tanto
/// el plugin `VitalsHealth` (llamado desde JS) como `SceneDelegate.sceneDidBecomeActive`
/// (auto-sync en foreground, sin pasar por el bridge) puedan dispararlo.
///
/// Responsabilidades:
///   - Guardar config (url, token, user opcional) — url/user en UserDefaults,
///     token en Keychain (Fase 8D, paso D4: antes vivía en UserDefaults sin
///     cifrar; se migra una sola vez desde ahí y se borra la copia vieja).
///   - Pedir autorizacion de lectura de HealthKit para los ~13 tipos del contrato.
///   - Leer y agregar por dia LOCAL (Calendar.current) los ultimos `windowDays` dias (365).
///   - Armar el payload JSON exacto que espera `app/sources/healthkit.py`
///     (ver HealthKitSource._normalize: claves hrv/rhr/resp/spo2/skin_temp/steps/
///     vo2/distance_km/energy_kcal como [{date,value}], sleep[] y workouts[]).
///   - POST a {url}/api/ingest con header X-Vitals-Token (+ X-Vitals-User si hay
///     usuario configurado — Fase 8D, household).
///
/// Nunca crashea: errores de red/parseo resuelven con {status:"error", message}.
final class HealthSyncManager {

    static let shared = HealthSyncManager()

    private init() {
        migrateTokenToKeychainIfNeeded()
    }

    private let store = HKHealthStore()

    // 365 días (1 año) — ampliado desde 45 tras feedback del Doc: HealthKit puede tener años
    // de historial de Apple Watch, no solo las últimas semanas. Ver ROADMAP-vitals-fix-ingest-
    // merge-y-ventana-healthkit.md para el porqué de 365 (no ilimitado) y el trade-off de
    // performance on-device con HKSampleQuery (sueño/workouts) sobre un año de muestras.
    private let windowDays = 365

    // MARK: - UserDefaults / Keychain keys

    private let kUrl = "vitals_url"
    private let kUser = "vitals_user"
    // Key de Keychain para el token (Fase 8D, paso D4). Mismo nombre lógico que
    // la key vieja de UserDefaults para que la migración sea 1:1 sin renombrar.
    private let kToken = "vitals_token"
    // Flag de UserDefaults (NO el token en sí) que marca si ya migramos — evita
    // releer Keychain/UserDefaults en cada init si ya no hay nada que migrar.
    private let kTokenMigratedFlag = "vitals_token_migrated_v1"

    /// Migra el token desde UserDefaults (legacy, plano) a Keychain UNA SOLA VEZ,
    /// y borra la copia vieja de UserDefaults tras la migración exitosa. Nunca
    /// lanza — si Keychain falla por cualquier motivo, deja el valor en
    /// UserDefaults intacto (no perder el token del usuario, que tendría que
    /// re-escanear el QR — ver roadmap, riesgo D4 "Keychain iOS").
    private func migrateTokenToKeychainIfNeeded() {
        let defaults = UserDefaults.standard
        guard !defaults.bool(forKey: kTokenMigratedFlag) else { return }

        if let legacyToken = defaults.string(forKey: kToken), !legacyToken.isEmpty {
            // Solo migra si Keychain aún NO tiene un valor (no pisar algo ya
            // migrado por una corrida anterior que se interrumpió después de
            // escribir en Keychain pero antes de marcar el flag).
            if KeychainStore.get(forKey: kToken) == nil {
                KeychainStore.set(legacyToken, forKey: kToken)
            }
            defaults.removeObject(forKey: kToken)
        }
        defaults.set(true, forKey: kTokenMigratedFlag)
    }

    // MARK: - HealthKit types (contrato Fase 5D-B)

    private var quantityTypeIdentifiers: [HKQuantityTypeIdentifier] {
        [
            .heartRateVariabilitySDNN,
            .restingHeartRate,
            .respiratoryRate,
            .oxygenSaturation,
            .appleSleepingWristTemperature,
            .stepCount,
            .vo2Max,
            .distanceWalkingRunning,
            .activeEnergyBurned,
            .basalEnergyBurned,
            // Fase 7 (salud femenina, opt-in): temperatura basal absoluta —
            // complementa appleSleepingWristTemperature (que ya viaja como
            // desviacion) con la lectura cruda que algunas usuarias registran
            // manualmente en la app Salud / termometros compatibles.
            .basalBodyTemperature,
        ]
    }

    // Fase 7 (salud femenina, opt-in): tipos de categoria de Apple Cycle Tracking.
    // Se leen SIEMPRE que HealthKit los conceda (mismo patron que el resto de
    // readTypes — pedir el permiso no activa el modulo de ciclo en el backend,
    // eso lo controla el toggle profile.cycle_tracking). Si el usuario nunca
    // activo Cycle Tracking en Salud, estas queries simplemente no devuelven
    // muestras (None-safe, igual que cualquier otro tipo sin datos).
    private var cycleCategoryTypeIdentifiers: [HKCategoryTypeIdentifier] {
        [
            .menstrualFlow,
            .ovulationTestResult,
            .intermenstrualBleeding,
            .sexualActivity,
        ]
    }

    private var readTypes: Set<HKObjectType> {
        var types: Set<HKObjectType> = []
        for id in quantityTypeIdentifiers {
            if let t = HKObjectType.quantityType(forIdentifier: id) {
                types.insert(t)
            }
        }
        if let sleep = HKObjectType.categoryType(forIdentifier: .sleepAnalysis) {
            types.insert(sleep)
        }
        for id in cycleCategoryTypeIdentifiers {
            if let t = HKObjectType.categoryType(forIdentifier: id) {
                types.insert(t)
            }
        }
        types.insert(HKObjectType.workoutType())
        // ECG (Roadmap ECG, Paso 3) — read type dentro de HealthKit, sin entitlement
        // nuevo (usa el mismo NSHealthShareUsageDescription existente). Guard
        // #available: electrocardiogramType() existe desde iOS 14 (el target ya es 16).
        if #available(iOS 14.0, *) {
            types.insert(HKObjectType.electrocardiogramType())
        }
        return types
    }

    // MARK: - Config

    /// Guarda url/token/user. Llamado desde `VitalsHealth.setConfig` (vía JS,
    /// pantalla de conexión) y desde `www/index.html` al reconectar. El token
    /// va a Keychain (Fase 8D, paso D4); url/user siguen en UserDefaults (no
    /// son secretos — url es pública, user es solo un identificador de perfil
    /// household, no una credencial).
    ///
    /// `user` es opcional (household, Fase 8D paso D3): instalaciones
    /// single-user no lo configuran y el header X-Vitals-User simplemente no
    /// se manda (mismo comportamiento de hoy, backward-compat total).
    func setConfig(url: String, token: String, user: String? = nil) {
        let defaults = UserDefaults.standard
        let trimmedUrl = url.trimmingCharacters(in: .whitespacesAndNewlines)
        let trimmedToken = token.trimmingCharacters(in: .whitespacesAndNewlines)
        if trimmedUrl.isEmpty {
            defaults.removeObject(forKey: kUrl)
        } else {
            defaults.set(trimmedUrl, forKey: kUrl)
        }
        if trimmedToken.isEmpty {
            KeychainStore.delete(forKey: kToken)
        } else {
            KeychainStore.set(trimmedToken, forKey: kToken)
        }
        if let user = user?.trimmingCharacters(in: .whitespacesAndNewlines), !user.isEmpty {
            defaults.set(user, forKey: kUser)
        } else if user != nil {
            // user explícitamente vacío -> borra la config previa (permite
            // "desasignar" el usuario sin tener que reinstalar la app).
            defaults.removeObject(forKey: kUser)
        }
        // user == nil (parámetro omitido, caller viejo) -> no tocar kUser, deja
        // lo que ya estuviera configurado (backward-compat con callers que aún
        // no pasan user).
    }

    private var configuredUrl: String? {
        UserDefaults.standard.string(forKey: kUrl)
    }

    private var configuredToken: String? {
        KeychainStore.get(forKey: kToken)
    }

    /// Usuario household configurado (Fase 8D, paso D3) — nil en instalaciones
    /// single-user o si nunca se configuró (comportamiento idéntico a antes).
    private var configuredUser: String? {
        UserDefaults.standard.string(forKey: kUser)
    }

    private var hasConfig: Bool {
        guard let u = configuredUrl, !u.isEmpty,
              let t = configuredToken, !t.isEmpty else { return false }
        return true
    }

    // MARK: - Authorization

    func requestAuthorization(completion: @escaping (Bool, Error?) -> Void) {
        guard HKHealthStore.isHealthDataAvailable() else {
            completion(false, nil)
            return
        }
        store.requestAuthorization(toShare: [], read: readTypes) { granted, error in
            DispatchQueue.main.async {
                completion(granted, error)
            }
        }
    }

    // MARK: - Sync (orquestación)

    /// Orquesta: valida config -> asegura autorización -> lee+agrega -> POST.
    /// Nunca lanza. Resuelve siempre con un dict serializable a JSON.
    func sync(completion: @escaping ([String: Any]) -> Void) {
        guard hasConfig, let urlString = configuredUrl, let token = configuredToken else {
            completion(["status": "no_config"])
            return
        }
        guard HKHealthStore.isHealthDataAvailable() else {
            completion(["status": "error", "message": "HealthKit no disponible en este dispositivo"])
            return
        }

        let proceed = {
            self.buildPayload { payload in
                self.postPayload(payload, urlString: urlString, token: token) { result in
                    // ECG (Roadmap ECG, Paso 3): visor INDEPENDIENTE — corre DESPUÉS del
                    // sync normal, sobre su propio endpoint /api/ecg, y nunca hace fallar
                    // ni bloquea el resultado del sync principal (payload de arriba). Si
                    // algo sale mal en el push de ECG, el resultado del sync normal se
                    // devuelve igual; solo se enriquece con ecg_found/ecg_pushed=0.
                    self.syncEcg(urlString: urlString, token: token) { ecgFound, ecgPushed in
                        var merged = result
                        merged["ecg_found"] = ecgFound
                        merged["ecg_pushed"] = ecgPushed
                        completion(merged)
                    }
                }
            }
        }

        // SIEMPRE pedir autorización antes de leer. requestAuthorization solo
        // presenta la hoja de permiso para tipos .notDetermined y no-op para los ya
        // resueltos — así, al AGREGAR tipos nuevos (ECG, tipos de ciclo de Fase 7)
        // la hoja aparece aunque los viejos (HRV, etc.) ya estén concedidos.
        // 🔴 Antes esto se gateaba con authorizationStatusNeedsRequest(), que solo
        // miraba HRV: al estar HRV ya autorizado de un build anterior, el request se
        // saltaba y iOS NUNCA mostraba la hoja de los tipos nuevos → sus lecturas
        // salían vacías (bug real: rebuild sin prompt de ECG). Además, para permisos
        // de solo-lectura authorizationStatus() no es fiable (privacidad de Apple),
        // así que no se debe decidir con él. requestAuthorization es idempotente.
        requestAuthorization { granted, error in
            if let error = error {
                completion(["status": "error", "message": error.localizedDescription])
                return
            }
            // Aunque granted sea false para algunos tipos, HealthKit no informa
            // por-tipo de forma fiable (privacidad) — seguimos e intentamos leer;
            // los tipos sin permiso simplemente no devuelven muestras (None-safe).
            proceed()
        }
    }

    /// Best-effort: llamado desde SceneDelegate.sceneDidBecomeActive. No hace nada
    /// si no hay config (usuarios no-HealthKit intactos, cero POSTs/permission prompts).
    func autoSyncIfConfigured() {
        guard hasConfig else { return }
        sync { result in
            #if DEBUG
            print("[HealthSyncManager] autoSyncIfConfigured -> \(result)")
            #endif
        }
    }

    // MARK: - Background sync (Fase 8D, paso D4: BGAppRefreshTask)
    //
    // El foreground sync existente (sceneDidBecomeActive -> autoSyncIfConfigured)
    // se MANTIENE intacto — esto es aditivo, un sync adicional cuando el sistema
    // despierta la app en background (BGAppRefreshTask, ~cada 4h si el sistema lo
    // permite; iOS decide el timing real según uso de la app / batería).
    //
    // Identifier registrado en Info.plist (BGTaskSchedulerPermittedIdentifiers) y
    // en AppDelegate (BGTaskScheduler.shared.register). Debe coincidir EXACTO en
    // los 3 lugares (Info.plist, AppDelegate.register, este scheduleBackgroundSync).
    static let backgroundTaskIdentifier = "tv.docstream.vitals.sync"

    /// Agenda la próxima ejecución del BGAppRefreshTask (~4h desde ahora, el
    /// sistema puede retrasarlo más). Se llama al terminar CADA ejecución
    /// (foreground launch inicial + al final de cada handleBackgroundSync) para
    /// mantener la cadena de re-agendado viva — BGAppRefreshTask es "one-shot",
    /// no un timer recurrente nativo.
    func scheduleBackgroundSync() {
        guard hasConfig else { return } // sin config -> nada que sincronizar en background
        let request = BGAppRefreshTaskRequest(identifier: Self.backgroundTaskIdentifier)
        request.earliestBeginDate = Date(timeIntervalSinceNow: 4 * 60 * 60) // ~4h
        do {
            try BGTaskScheduler.shared.submit(request)
        } catch {
            #if DEBUG
            print("[HealthSyncManager] scheduleBackgroundSync falló: \(error)")
            #endif
        }
    }

    /// Handler del BGAppRefreshTask, llamado desde AppDelegate cuando el
    /// sistema despierta la app en background. Contrato de BGTaskScheduler:
    /// SIEMPRE llamar `task.setTaskCompleted(success:)` (incluso en error/no
    /// config) y SIEMPRE re-agendar antes de terminar (si no, la cadena de
    /// wake-ups se corta silenciosamente).
    func handleBackgroundSync(task: BGAppRefreshTask) {
        // Re-agenda de inmediato — si el sync mismo tarda o el proceso se mata,
        // la próxima ejecución ya quedó en la cola (evita que un solo fallo
        // corte la cadena de background refresh indefinidamente).
        scheduleBackgroundSync()

        guard hasConfig else {
            task.setTaskCompleted(success: true)
            return
        }

        task.expirationHandler = { [weak task] in
            guard let task = task else { return }
            task.setTaskCompleted(success: false)
        }

        sync { result in
            #if DEBUG
            print("[HealthSyncManager] handleBackgroundSync -> \(result)")
            #endif
            let status = result["status"] as? String
            let success = (status == "ok" || status == "no_change" || status == "wrong_source")
            task.setTaskCompleted(success: success)
        }
    }

    // MARK: - Lectura + agregación

    private struct WindowBounds {
        let start: Date
        let end: Date
    }

    private func windowBounds() -> WindowBounds {
        let cal = Calendar.current
        let end = Date()
        let start = cal.date(byAdding: .day, value: -windowDays, to: end) ?? end
        return WindowBounds(start: start, end: end)
    }

    private func dayString(_ date: Date) -> String {
        let cal = Calendar.current
        let formatter = DateFormatter()
        // Fixed-format dates DEBEN usar Gregorian + en_US_POSIX para no depender del
        // calendario/locale de pantalla del dispositivo (p.ej. calendario Budista/Japones
        // a nivel sistema cambiaria el "yyyy" y romperia las claves de fecha del backend).
        formatter.calendar = Calendar(identifier: .gregorian)
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = cal.timeZone
        formatter.dateFormat = "yyyy-MM-dd"
        return formatter.string(from: date)
    }

    private func hmString(_ date: Date) -> String {
        let cal = Calendar.current
        let formatter = DateFormatter()
        formatter.calendar = Calendar(identifier: .gregorian)
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = cal.timeZone
        formatter.dateFormat = "HH:mm"
        return formatter.string(from: date)
    }

    /// Orquesta todas las lecturas async y arma el payload final.
    private func buildPayload(completion: @escaping ([String: Any]) -> Void) {
        let bounds = windowBounds()
        let group = DispatchGroup()

        var hrv: [[String: Any]] = []
        var rhr: [[String: Any]] = []
        var resp: [[String: Any]] = []
        var spo2: [[String: Any]] = []
        var skinTemp: [[String: Any]] = []
        var steps: [[String: Any]] = []
        var vo2: [[String: Any]] = []
        var distanceKm: [[String: Any]] = []
        var energyKcal: [[String: Any]] = []
        var sleep: [[String: Any]] = []
        var workouts: [[String: Any]] = []
        // Fase 7 (salud femenina, opt-in): estos arrays quedan vacios (y por
        // tanto fuera del payload, ver `if !x.isEmpty` abajo) para cualquier
        // usuaria que no tenga Apple Cycle Tracking activado — None-safe, cero
        // impacto en el payload/backend existente si no hay datos.
        var basalTemp: [[String: Any]] = []
        var menstrualFlow: [[String: Any]] = []
        var ovulationTest: [[String: Any]] = []

        // hrv (media diaria, ms)
        if let type = HKObjectType.quantityType(forIdentifier: .heartRateVariabilitySDNN) {
            group.enter()
            dailyAverage(type: type, unit: HKUnit.secondUnit(with: .milli), bounds: bounds) { result in
                hrv = result
                group.leave()
            }
        }

        // rhr (media diaria, lpm)
        if let type = HKObjectType.quantityType(forIdentifier: .restingHeartRate) {
            group.enter()
            dailyAverage(type: type, unit: HKUnit.count().unitDivided(by: .minute()), bounds: bounds) { result in
                rhr = result
                group.leave()
            }
        }

        // resp (media diaria, rpm)
        if let type = HKObjectType.quantityType(forIdentifier: .respiratoryRate) {
            group.enter()
            dailyAverage(type: type, unit: HKUnit.count().unitDivided(by: .minute()), bounds: bounds) { result in
                resp = result
                group.leave()
            }
        }

        // spo2 (media diaria *100, % desde fraccion 0-1)
        if let type = HKObjectType.quantityType(forIdentifier: .oxygenSaturation) {
            group.enter()
            dailyAverage(type: type, unit: HKUnit.percent(), bounds: bounds) { result in
                spo2 = result.map { entry in
                    var e = entry
                    if let v = e["value"] as? Double {
                        e["value"] = v * 100.0
                    }
                    return e
                }
                group.leave()
            }
        }

        // skin_temp: primero la media ABSOLUTA diaria, luego se convierte a
        // desviacion (dia - media de la ventana) tras juntar todo (ver abajo).
        var skinAbsolute: [[String: Any]] = []
        if let type = HKObjectType.quantityType(forIdentifier: .appleSleepingWristTemperature) {
            group.enter()
            dailyAverage(type: type, unit: HKUnit.degreeCelsius(), bounds: bounds) { result in
                skinAbsolute = result
                group.leave()
            }
        }

        // steps (suma diaria)
        if let type = HKObjectType.quantityType(forIdentifier: .stepCount) {
            group.enter()
            dailySum(type: type, unit: HKUnit.count(), bounds: bounds) { result in
                steps = result
                group.leave()
            }
        }

        // vo2 (media diaria si hay muestra ese dia; si no, se omite por dia — no forward-fill)
        if let type = HKObjectType.quantityType(forIdentifier: .vo2Max) {
            group.enter()
            let unit = HKUnit.literUnit(with: .milli).unitDivided(by: HKUnit.gramUnit(with: .kilo).unitMultiplied(by: .minute()))
            dailyAverage(type: type, unit: unit, bounds: bounds) { result in
                vo2 = result
                group.leave()
            }
        }

        // distance_km (suma diaria, m -> km)
        if let type = HKObjectType.quantityType(forIdentifier: .distanceWalkingRunning) {
            group.enter()
            dailySum(type: type, unit: HKUnit.meter(), bounds: bounds) { result in
                distanceKm = result.map { entry in
                    var e = entry
                    if let v = e["value"] as? Double {
                        e["value"] = v / 1000.0
                    }
                    return e
                }
                group.leave()
            }
        }

        // energy_kcal (suma diaria de active + basal)
        var activeEnergy: [String: Double] = [:]
        var basalEnergy: [String: Double] = [:]
        if let type = HKObjectType.quantityType(forIdentifier: .activeEnergyBurned) {
            group.enter()
            dailySumDict(type: type, unit: HKUnit.kilocalorie(), bounds: bounds) { result in
                activeEnergy = result
                group.leave()
            }
        }
        if let type = HKObjectType.quantityType(forIdentifier: .basalEnergyBurned) {
            group.enter()
            dailySumDict(type: type, unit: HKUnit.kilocalorie(), bounds: bounds) { result in
                basalEnergy = result
                group.leave()
            }
        }

        // sleep
        if let type = HKObjectType.categoryType(forIdentifier: .sleepAnalysis) {
            group.enter()
            readSleep(type: type, bounds: bounds) { result in
                sleep = result
                group.leave()
            }
        }

        // Fase 7 (salud femenina, opt-in): basal_temp (media diaria ABSOLUTA,
        // a diferencia de skin_temp que viaja como desviacion — el backend
        // (app/sources/healthkit.py) espera basal_temp en °C tal cual).
        if let type = HKObjectType.quantityType(forIdentifier: .basalBodyTemperature) {
            group.enter()
            dailyAverage(type: type, unit: HKUnit.degreeCelsius(), bounds: bounds) { result in
                basalTemp = result
                group.leave()
            }
        }

        // Fase 7 (salud femenina, opt-in): menstrual_flow (patron de readSleep,
        // pero para categoria simple sin agrupacion en "noches" — cada muestra
        // es un dia con su nivel de flujo).
        if let type = HKObjectType.categoryType(forIdentifier: .menstrualFlow) {
            group.enter()
            readMenstrualFlow(type: type, bounds: bounds) { result in
                menstrualFlow = result
                group.leave()
            }
        }

        // Fase 7 (salud femenina, opt-in): ovulation_test (positive/negative/
        // indeterminate/luteinizingHormoneSurge segun HKCategoryValueOvulationTestResult).
        if let type = HKObjectType.categoryType(forIdentifier: .ovulationTestResult) {
            group.enter()
            readOvulationTest(type: type, bounds: bounds) { result in
                ovulationTest = result
                group.leave()
            }
        }

        // workouts
        group.enter()
        readWorkouts(bounds: bounds) { result in
            workouts = result
            group.leave()
        }

        group.notify(queue: .main) {
            // energy_kcal: combinar active + basal por dia (solo dias con al menos una muestra)
            var allDays = Set(activeEnergy.keys)
            allDays.formUnion(basalEnergy.keys)
            for day in allDays {
                let total = (activeEnergy[day] ?? 0) + (basalEnergy[day] ?? 0)
                energyKcal.append(["date": day, "value": total])
            }

            // skin_temp -> desviacion = valor_dia - media de TODA la ventana
            if !skinAbsolute.isEmpty {
                let values = skinAbsolute.compactMap { $0["value"] as? Double }
                if !values.isEmpty {
                    let mean = values.reduce(0, +) / Double(values.count)
                    skinTemp = skinAbsolute.map { entry in
                        var e = entry
                        if let v = e["value"] as? Double {
                            e["value"] = v - mean
                        }
                        return e
                    }
                }
            }

            var payload: [String: Any] = [:]
            if !hrv.isEmpty { payload["hrv"] = hrv }
            if !rhr.isEmpty { payload["rhr"] = rhr }
            if !resp.isEmpty { payload["resp"] = resp }
            if !spo2.isEmpty { payload["spo2"] = spo2 }
            if !skinTemp.isEmpty { payload["skin_temp"] = skinTemp }
            if !steps.isEmpty { payload["steps"] = steps }
            if !vo2.isEmpty { payload["vo2"] = vo2 }
            if !distanceKm.isEmpty { payload["distance_km"] = distanceKm }
            if !energyKcal.isEmpty { payload["energy_kcal"] = energyKcal }
            if !sleep.isEmpty { payload["sleep"] = sleep }
            if !workouts.isEmpty { payload["workouts"] = workouts }
            // Fase 7 (salud femenina, opt-in): solo se agregan al payload si hay
            // datos (misma convencion `if !x.isEmpty` que el resto) — una usuaria
            // sin Cycle Tracking activado en Salud nunca manda estas claves,
            // payload IDENTICO al actual (retrocompatibilidad estricta).
            if !basalTemp.isEmpty { payload["basal_temp"] = basalTemp }
            if !menstrualFlow.isEmpty { payload["menstrual_flow"] = menstrualFlow }
            if !ovulationTest.isEmpty { payload["ovulation_test"] = ovulationTest }

            completion(payload)
        }
    }

    // MARK: - Helpers de agregación genéricos

    private func dailyIntervalComponents() -> DateComponents {
        var c = DateComponents()
        c.day = 1
        return c
    }

    /// Suma diaria -> [{date, value}], solo dias con dato (no manda 0 para dias sin muestras).
    private func dailySum(type: HKQuantityType, unit: HKUnit, bounds: WindowBounds,
                           completion: @escaping ([[String: Any]]) -> Void) {
        dailySumDict(type: type, unit: unit, bounds: bounds) { dict in
            let arr = dict.map { (day, value) -> [String: Any] in
                ["date": day, "value": value]
            }
            completion(arr)
        }
    }

    /// Igual que dailySum pero devuelve {date: value} (para combinar active+basal energy).
    private func dailySumDict(type: HKQuantityType, unit: HKUnit, bounds: WindowBounds,
                               completion: @escaping ([String: Double]) -> Void) {
        let cal = Calendar.current
        let predicate = HKQuery.predicateForSamples(withStart: bounds.start, end: bounds.end, options: .strictStartDate)
        let query = HKStatisticsCollectionQuery(
            quantityType: type,
            quantitySamplePredicate: predicate,
            options: .cumulativeSum,
            anchorDate: cal.startOfDay(for: bounds.start),
            intervalComponents: dailyIntervalComponents()
        )
        query.initialResultsHandler = { _, results, error in
            var out: [String: Double] = [:]
            if let results = results {
                results.enumerateStatistics(from: bounds.start, to: bounds.end) { stats, _ in
                    if let sum = stats.sumQuantity() {
                        let value = sum.doubleValue(for: unit)
                        let day = self.dayString(stats.startDate)
                        out[day] = value
                    }
                }
            }
            DispatchQueue.main.async { completion(out) }
        }
        store.execute(query)
    }

    /// Media diaria -> [{date, value}], solo dias con dato.
    private func dailyAverage(type: HKQuantityType, unit: HKUnit, bounds: WindowBounds,
                               completion: @escaping ([[String: Any]]) -> Void) {
        let cal = Calendar.current
        let predicate = HKQuery.predicateForSamples(withStart: bounds.start, end: bounds.end, options: .strictStartDate)
        let query = HKStatisticsCollectionQuery(
            quantityType: type,
            quantitySamplePredicate: predicate,
            options: .discreteAverage,
            anchorDate: cal.startOfDay(for: bounds.start),
            intervalComponents: dailyIntervalComponents()
        )
        query.initialResultsHandler = { _, results, error in
            var out: [[String: Any]] = []
            if let results = results {
                results.enumerateStatistics(from: bounds.start, to: bounds.end) { stats, _ in
                    if let avg = stats.averageQuantity() {
                        let value = avg.doubleValue(for: unit)
                        let day = self.dayString(stats.startDate)
                        out.append(["date": day, "value": value])
                    }
                }
            }
            DispatchQueue.main.async { completion(out) }
        }
        store.execute(query)
    }

    // MARK: - Sleep

    /// Agrupa muestras de sueño en "noches" (gap > 3h entre segmentos separa noches),
    /// calcula fases y asigna la noche al dia LOCAL de despertar.
    private func readSleep(type: HKCategoryType, bounds: WindowBounds,
                            completion: @escaping ([[String: Any]]) -> Void) {
        let predicate = HKQuery.predicateForSamples(withStart: bounds.start, end: bounds.end, options: .strictStartDate)
        let sort = NSSortDescriptor(key: HKSampleSortIdentifierStartDate, ascending: true)
        let query = HKSampleQuery(sampleType: type, predicate: predicate, limit: HKObjectQueryNoLimit, sortDescriptors: [sort]) { _, samples, _ in
            guard let categorySamples = samples as? [HKCategorySample], !categorySamples.isEmpty else {
                DispatchQueue.main.async { completion([]) }
                return
            }

            // Agrupar en noches: nueva noche si el gap desde el fin del segmento anterior es > 3h.
            var nights: [[HKCategorySample]] = []
            var current: [HKCategorySample] = []
            var lastEnd: Date?

            for sample in categorySamples {
                if let le = lastEnd, sample.startDate.timeIntervalSince(le) > 3 * 3600 {
                    if !current.isEmpty { nights.append(current) }
                    current = []
                }
                current.append(sample)
                if lastEnd == nil || sample.endDate > lastEnd! {
                    lastEnd = sample.endDate
                }
            }
            if !current.isEmpty { nights.append(current) }

            let asleepValues: Set<Int> = {
                var s: Set<Int> = [
                    HKCategoryValueSleepAnalysis.asleepUnspecified.rawValue,
                ]
                if #available(iOS 16.0, *) {
                    s.insert(HKCategoryValueSleepAnalysis.asleepCore.rawValue)
                    s.insert(HKCategoryValueSleepAnalysis.asleepDeep.rawValue)
                    s.insert(HKCategoryValueSleepAnalysis.asleepREM.rawValue)
                }
                return s
            }()

            var out: [[String: Any]] = []

            for night in nights {
                var asleepMin: Double = 0
                var deepMin: Double = 0
                var remMin: Double = 0
                var coreMin: Double = 0
                var inBedMin: Double = 0
                var hasInBed = false
                var earliestStart: Date?
                var latestAsleepEnd: Date?
                var latestSegmentEnd: Date?

                for sample in night {
                    let durMin = sample.endDate.timeIntervalSince(sample.startDate) / 60.0
                    let value = sample.value

                    if earliestStart == nil || sample.startDate < earliestStart! {
                        earliestStart = sample.startDate
                    }
                    if latestSegmentEnd == nil || sample.endDate > latestSegmentEnd! {
                        latestSegmentEnd = sample.endDate
                    }

                    if value == HKCategoryValueSleepAnalysis.inBed.rawValue {
                        inBedMin += durMin
                        hasInBed = true
                        continue
                    }

                    if asleepValues.contains(value) {
                        asleepMin += durMin
                        if latestAsleepEnd == nil || sample.endDate > latestAsleepEnd! {
                            latestAsleepEnd = sample.endDate
                        }
                        if #available(iOS 16.0, *) {
                            if value == HKCategoryValueSleepAnalysis.asleepDeep.rawValue {
                                deepMin += durMin
                            } else if value == HKCategoryValueSleepAnalysis.asleepREM.rawValue {
                                remMin += durMin
                            } else if value == HKCategoryValueSleepAnalysis.asleepCore.rawValue {
                                coreMin += durMin
                            }
                        }
                    }
                }

                guard let waketime = latestAsleepEnd, let bedtime = earliestStart, asleepMin > 0 else {
                    continue
                }

                // inbed = suma de segmentos inBed si existen; si no, span TOTAL de la
                // noche (desde el primer segmento hasta el ultimo, sea asleep o inBed)
                // — no "asleepMin" (eso forzaria eff=100 siempre que falte inBed,
                // que es el caso tipico de Apple Watch, que normalmente NO emite
                // segmentos inBed explicitos).
                let spanEnd = latestSegmentEnd ?? waketime
                let totalInbed = hasInBed ? inBedMin : max(asleepMin, spanEnd.timeIntervalSince(bedtime) / 60.0)
                let eff: Double? = totalInbed > 0 ? (asleepMin / totalInbed) * 100.0 : nil

                // Dia LOCAL de despertar
                let dateKey = self.dayString(waketime)

                var entry: [String: Any] = [
                    "date": dateKey,
                    "asleep": Int(asleepMin.rounded()),
                    "deep": Int(deepMin.rounded()),
                    "rem": Int(remMin.rounded()),
                    "light": Int(coreMin.rounded()),
                    "bedtime": self.hmString(bedtime),
                    "waketime": self.hmString(waketime),
                    "inbed": Int(totalInbed.rounded()),
                ]
                if let eff = eff {
                    entry["eff"] = Int(eff.rounded())
                }
                out.append(entry)
            }

            DispatchQueue.main.async { completion(out) }
        }
        store.execute(query)
    }

    // MARK: - Fase 7 (salud femenina, opt-in): categorias de Cycle Tracking

    /// Convierte HKCategoryValueVaginalBleeding (Apple Cycle Tracking) a la
    /// etiqueta de texto que espera el backend (`menstrual_flow[].value`).
    private func menstrualFlowLabel(for rawValue: Int) -> String? {
        // HKCategoryValueVaginalBleeding (Apple Cycle Tracking) existe desde iOS 18;
        // el deployment target es 17.6, así que en <18 degradamos a nil (la salud
        // femenina es opt-in y este flujo no aplica en versiones sin el tipo).
        guard #available(iOS 18.0, *) else { return nil }
        guard let v = HKCategoryValueVaginalBleeding(rawValue: rawValue) else { return nil }
        switch v {
        case .unspecified: return "unspecified"
        case .light: return "light"
        case .medium: return "medium"
        case .heavy: return "heavy"
        case .none: return "none"
        @unknown default: return "unspecified"
        }
    }

    /// menstrual_flow[] -> [{date, value}], un dia LOCAL por muestra (patron
    /// simple, a diferencia de readSleep que agrupa en "noches" — el flujo
    /// menstrual no cruza medianoche de la misma forma que el sueño).
    private func readMenstrualFlow(type: HKCategoryType, bounds: WindowBounds,
                                    completion: @escaping ([[String: Any]]) -> Void) {
        let predicate = HKQuery.predicateForSamples(withStart: bounds.start, end: bounds.end, options: .strictStartDate)
        let sort = NSSortDescriptor(key: HKSampleSortIdentifierStartDate, ascending: true)
        let query = HKSampleQuery(sampleType: type, predicate: predicate, limit: HKObjectQueryNoLimit, sortDescriptors: [sort]) { _, samples, _ in
            guard let categorySamples = samples as? [HKCategorySample], !categorySamples.isEmpty else {
                DispatchQueue.main.async { completion([]) }
                return
            }
            var byDay: [String: String] = [:]
            for sample in categorySamples {
                guard let label = self.menstrualFlowLabel(for: sample.value) else { continue }
                let day = self.dayString(sample.startDate)
                byDay[day] = label
            }
            let out = byDay.map { (day, value) -> [String: Any] in ["date": day, "value": value] }
            DispatchQueue.main.async { completion(out) }
        }
        store.execute(query)
    }

    /// Convierte HKCategoryValueOvulationTestResult a la etiqueta de texto que
    /// espera el backend (`ovulation_test[].value`).
    private func ovulationTestLabel(for rawValue: Int) -> String? {
        guard let v = HKCategoryValueOvulationTestResult(rawValue: rawValue) else { return nil }
        switch v {
        case .negative: return "negative"
        case .positive: return "positive"
        case .indeterminate: return "indeterminate"
        case .estrogenSurge: return "estrogen_surge"
        @unknown default: return "indeterminate"
        }
    }

    /// ovulation_test[] -> [{date, value}].
    private func readOvulationTest(type: HKCategoryType, bounds: WindowBounds,
                                    completion: @escaping ([[String: Any]]) -> Void) {
        let predicate = HKQuery.predicateForSamples(withStart: bounds.start, end: bounds.end, options: .strictStartDate)
        let sort = NSSortDescriptor(key: HKSampleSortIdentifierStartDate, ascending: true)
        let query = HKSampleQuery(sampleType: type, predicate: predicate, limit: HKObjectQueryNoLimit, sortDescriptors: [sort]) { _, samples, _ in
            guard let categorySamples = samples as? [HKCategorySample], !categorySamples.isEmpty else {
                DispatchQueue.main.async { completion([]) }
                return
            }
            var out: [[String: Any]] = []
            for sample in categorySamples {
                guard let label = self.ovulationTestLabel(for: sample.value) else { continue }
                out.append(["date": self.dayString(sample.startDate), "value": label])
            }
            DispatchQueue.main.async { completion(out) }
        }
        store.execute(query)
    }

    // MARK: - Workouts

    private func readWorkouts(bounds: WindowBounds, completion: @escaping ([[String: Any]]) -> Void) {
        let predicate = HKQuery.predicateForSamples(withStart: bounds.start, end: bounds.end, options: .strictStartDate)
        let sort = NSSortDescriptor(key: HKSampleSortIdentifierStartDate, ascending: true)
        let query = HKSampleQuery(sampleType: HKObjectType.workoutType(), predicate: predicate, limit: HKObjectQueryNoLimit, sortDescriptors: [sort]) { _, samples, _ in
            guard let workoutSamples = samples as? [HKWorkout], !workoutSamples.isEmpty else {
                DispatchQueue.main.async { completion([]) }
                return
            }

            let group = DispatchGroup()
            var results: [(Date, [String: Any])] = []
            let lock = NSLock()

            for workout in workoutSamples {
                group.enter()
                self.workoutEnergyKcal(workout: workout) { kcal in
                    var entry: [String: Any] = [
                        "date": self.dayString(workout.startDate),
                        "name": self.workoutName(for: workout.workoutActivityType),
                        "dur_min": Int((workout.duration / 60.0).rounded()),
                    ]
                    if let kcal = kcal {
                        entry["kcal"] = Int(kcal.rounded())
                    }
                    let distanceMeters = workout.totalDistance?.doubleValue(for: .meter())
                    if let distanceMeters = distanceMeters, distanceMeters > 0 {
                        entry["distance_km"] = distanceMeters / 1000.0
                    }
                    lock.lock()
                    results.append((workout.startDate, entry))
                    lock.unlock()
                    group.leave()
                }
            }

            group.notify(queue: .main) {
                let sorted = results.sorted { $0.0 < $1.0 }.map { $0.1 }
                completion(sorted)
            }
        }
        store.execute(query)
    }

    private func workoutEnergyKcal(workout: HKWorkout, completion: @escaping (Double?) -> Void) {
        if #available(iOS 16.0, *), let energyType = HKObjectType.quantityType(forIdentifier: .activeEnergyBurned) {
            let stats = workout.statistics(for: energyType)
            if let sum = stats?.sumQuantity() {
                completion(sum.doubleValue(for: .kilocalorie()))
                return
            }
        }
        // Fallback API antigua
        if let total = workout.totalEnergyBurned {
            completion(total.doubleValue(for: .kilocalorie()))
            return
        }
        completion(nil)
    }

    private func workoutName(for type: HKWorkoutActivityType) -> String {
        switch type {
        case .running: return "Run"
        case .walking: return "Walk"
        case .cycling: return "Cycling"
        case .swimming: return "Swimming"
        case .functionalStrengthTraining, .traditionalStrengthTraining: return "Strength"
        case .yoga: return "Yoga"
        case .hiking: return "Hiking"
        case .rowing: return "Rowing"
        case .elliptical: return "Elliptical"
        case .coreTraining: return "Core Training"
        case .highIntensityIntervalTraining: return "HIIT"
        case .soccer: return "Soccer"
        case .basketball: return "Basketball"
        case .tennis: return "Tennis"
        case .golf: return "Golf"
        case .pilates: return "Pilates"
        case .dance: return "Dance"
        case .stairClimbing: return "Stair Climbing"
        case .crossTraining: return "Cross Training"
        case .mixedCardio: return "Cardio"
        case .paddleSports: return "Paddle Sports"
        default: return "Workout"
        }
    }

    // MARK: - POST /api/ingest

    private func postPayload(_ payload: [String: Any], urlString: String, token: String,
                              completion: @escaping ([String: Any]) -> Void) {
        guard !payload.isEmpty else {
            completion(["status": "error", "message": "Sin datos de HealthKit en la ventana"])
            return
        }

        var base = urlString.trimmingCharacters(in: .whitespacesAndNewlines)
        if base.hasSuffix("/") { base.removeLast() }
        guard let url = URL(string: base + "/api/ingest") else {
            completion(["status": "error", "message": "URL invalida"])
            return
        }

        guard let body = try? JSONSerialization.data(withJSONObject: payload, options: []) else {
            completion(["status": "error", "message": "No se pudo serializar el payload"])
            return
        }

        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue(token, forHTTPHeaderField: "X-Vitals-Token")
        // Fase 8D (paso D3, household): si hay un usuario configurado, el
        // backend lo usa para rutear a data/users/<uid>/. Sin config (caso
        // single-user de hoy) el header simplemente no se manda.
        if let user = configuredUser, !user.isEmpty {
            request.setValue(user, forHTTPHeaderField: "X-Vitals-User")
        }
        request.httpBody = body
        request.timeoutInterval = 30

        let nDays = self.countDistinctDays(payload)

        let task = URLSession.shared.dataTask(with: request) { data, response, error in
            DispatchQueue.main.async {
                if let error = error {
                    completion(["status": "error", "message": error.localizedDescription])
                    return
                }
                guard let http = response as? HTTPURLResponse else {
                    completion(["status": "error", "message": "Sin respuesta HTTP"])
                    return
                }

                // Parsear el body — el backend SIEMPRE responde JSON con 'status' real,
                // incluso en 200 (wrong_source/error) — el código HTTP NO es la fuente de verdad.
                var body: [String: Any]? = nil
                if let data = data, !data.isEmpty {
                    body = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
                }

                if http.statusCode == 401 {
                    completion(["status": "unauthorized", "http": http.statusCode])
                    return
                }

                if let body = body, let backendStatus = body["status"] as? String {
                    var result: [String: Any] = ["status": backendStatus, "http": http.statusCode]
                    if let nDaysBody = body["n_days"] { result["n_days"] = nDaysBody } else { result["n_days"] = nDays }
                    if let active = body["active"] { result["active"] = active }
                    if let message = body["message"] { result["message"] = message }
                    completion(result)
                    return
                }

                // Fallback defensivo: body no parseable — no inventar "ok", derivar de HTTP.
                if http.statusCode >= 200 && http.statusCode < 300 {
                    completion(["status": "ok", "http": http.statusCode, "n_days": nDays])
                } else {
                    completion(["status": "error", "http": http.statusCode, "message": "HTTP \(http.statusCode)"])
                }
            }
        }
        task.resume()
    }

    private func countDistinctDays(_ payload: [String: Any]) -> Int {
        var days = Set<String>()
        for (key, value) in payload {
            guard key != "workouts" else {
                if let arr = value as? [[String: Any]] {
                    for e in arr { if let d = e["date"] as? String { days.insert(d) } }
                }
                continue
            }
            if let arr = value as? [[String: Any]] {
                for e in arr { if let d = e["date"] as? String { days.insert(d) } }
            }
        }
        return days.count
    }

    // MARK: - ECG (Roadmap ECG, Paso 3)
    //
    // VISOR INDEPENDIENTE: este bloque es la ÚNICA parte de HealthSyncManager que toca
    // HKElectrocardiogram / voltajes. NO participa en buildPayload()/postPayload() de
    // arriba (el payload de /api/ingest que alimenta recovery/strain/bodyage) — los ECG
    // van por su cuenta a /api/ecg. Nunca crashea: cualquier error resuelve con
    // (found: N, pushed: 0) para que sync() pueda reportarlo sin romper el flujo normal.
    //
    // Dedup: UserDefaults guarda el set de UUIDs de ECG ya empujados con éxito (persiste
    // entre lanzamientos de la app). Solo se hace query completo de HKElectrocardiogramQuery
    // (que es relativamente costoso, junta miles de muestras de voltaje) para los UUIDs
    // que AÚN no están en ese set — así un foreground repetido no re-lee ni re-postea
    // ECGs que ya se sincronizaron antes.

    private let kPushedEcgUuids = "vitals_pushed_ecg_uuids"

    private var pushedEcgUuids: Set<String> {
        get {
            let arr = UserDefaults.standard.stringArray(forKey: kPushedEcgUuids) ?? []
            return Set(arr)
        }
        set {
            UserDefaults.standard.set(Array(newValue), forKey: kPushedEcgUuids)
        }
    }

    private func markEcgPushed(_ uuid: String) {
        var s = pushedEcgUuids
        s.insert(uuid)
        pushedEcgUuids = s
    }

    /// Mapea la clasificación de Apple a un string ESTABLE (no depende de localización
    /// ni de que el enum de HealthKit cambie de nombre visible entre versiones de iOS).
    @available(iOS 14.0, *)
    private func ecgClassificationString(_ c: HKElectrocardiogram.Classification) -> String {
        switch c {
        case .sinusRhythm: return "sinusRhythm"
        case .atrialFibrillation: return "atrialFibrillation"
        case .inconclusiveLowHeartRate: return "inconclusiveLowHeartRate"
        case .inconclusiveHighHeartRate: return "inconclusiveHighHeartRate"
        case .inconclusivePoorReading: return "inconclusivePoorReading"
        case .inconclusiveOther: return "inconclusiveOther"
        case .unrecognized: return "unreadable"
        case .notSet: return "notSet"
        @unknown default: return "unreadable"
        }
    }

    @available(iOS 14.0, *)
    private func ecgSymptomsStatusString(_ s: HKElectrocardiogram.SymptomsStatus) -> String {
        switch s {
        case .none: return "none"
        case .present: return "present"
        case .notSet: return "notSet"
        @unknown default: return "notSet"
        }
    }

    /// Orquesta: lista ECGs en la ventana -> filtra los NO empujados -> por cada uno,
    /// junta sus voltajes (HKElectrocardiogramQuery) -> POST a /api/ecg -> marca dedup.
    /// completion(found, pushed): found = total de ECGs vistos en la ventana (empujados
    /// o no); pushed = cuántos se postearon con éxito en ESTA corrida.
    private func syncEcg(urlString: String, token: String, completion: @escaping (Int, Int) -> Void) {
        guard #available(iOS 14.0, *) else {
            completion(0, 0)
            return
        }
        let ecgType = HKObjectType.electrocardiogramType()
        // Sin permiso / tipo no disponible: HealthKit simplemente no devuelve muestras
        // (None-safe) — no hace falta chequear authorizationStatus aparte.
        let bounds = windowBounds()
        let predicate = HKQuery.predicateForSamples(withStart: bounds.start, end: bounds.end, options: .strictStartDate)
        let sort = NSSortDescriptor(key: HKSampleSortIdentifierStartDate, ascending: false)
        let query = HKSampleQuery(sampleType: ecgType, predicate: predicate, limit: HKObjectQueryNoLimit, sortDescriptors: [sort]) { _, samples, error in
            guard error == nil, let ecgSamples = samples as? [HKElectrocardiogram], !ecgSamples.isEmpty else {
                DispatchQueue.main.async { completion(0, 0) }
                return
            }

            let found = ecgSamples.count
            let already = self.pushedEcgUuids
            let pending = ecgSamples.filter { !already.contains($0.uuid.uuidString) }

            if pending.isEmpty {
                DispatchQueue.main.async { completion(found, 0) }
                return
            }

            // Uno por uno (no en paralelo): cada ECG trae ~15k puntos de voltaje —
            // mandarlos secuencialmente evita reventar memoria/ancho de banda si hay
            // varias lecturas pendientes a la vez (ver roadmap: "UNO por request o
            // batch chico para no reventar tamaños").
            self.pushEcgSequentially(pending, index: 0, urlString: urlString, token: token, pushedCount: 0) { pushedCount in
                completion(found, pushedCount)
            }
        }
        store.execute(query)
    }

    @available(iOS 14.0, *)
    private func pushEcgSequentially(_ samples: [HKElectrocardiogram], index: Int, urlString: String, token: String,
                                      pushedCount: Int, completion: @escaping (Int) -> Void) {
        if index >= samples.count {
            completion(pushedCount)
            return
        }
        let sample = samples[index]
        self.readEcgVoltages(sample) { payload in
            self.postEcgPayload(payload, urlString: urlString, token: token) { ok in
                if ok { self.markEcgPushed(sample.uuid.uuidString) }
                self.pushEcgSequentially(samples, index: index + 1, urlString: urlString, token: token,
                                          pushedCount: pushedCount + (ok ? 1 : 0), completion: completion)
            }
        }
    }

    /// Junta las muestras de voltaje de UN HKElectrocardiogram vía HKElectrocardiogramQuery
    /// y arma el dict del contrato con el backend. NO diezma (roadmap: por defecto NO
    /// diezmar, la onda cruda es el punto) — manda sample_count tal cual lo reporta Apple.
    @available(iOS 14.0, *)
    private func readEcgVoltages(_ sample: HKElectrocardiogram, completion: @escaping ([String: Any]) -> Void) {
        var voltages: [Double] = []
        voltages.reserveCapacity(sample.numberOfVoltageMeasurements)

        let voltageQuery = HKElectrocardiogramQuery(sample) { _, result in
            switch result {
            case .measurement(let measurement):
                if let quantity = measurement.quantity(for: .appleWatchSimilarToLeadI) {
                    // µV — mismo esquema que el resto del payload de HealthKit (hrv en ms,
                    // no en s) para que el backend/frontend trabajen en una sola escala.
                    voltages.append(quantity.doubleValue(for: HKUnit.voltUnit(with: .micro)))
                } else {
                    // Muestra sin lectura en este instante (gap): mantener alineación
                    // temporal con un placeholder none-safe en vez de saltarla en silencio
                    // (saltar correría el resto del trazo respecto al eje de tiempo real).
                    voltages.append(0.0)
                }
            case .done:
                DispatchQueue.main.async {
                    completion(self.ecgPayloadDict(sample, voltages: voltages))
                }
            case .error:
                // Error a medio-query: devolver lo juntado hasta ahora (nunca crashea);
                // postEcgPayload/backend son None-safe con arrays parciales o vacíos.
                DispatchQueue.main.async {
                    completion(self.ecgPayloadDict(sample, voltages: voltages))
                }
            @unknown default:
                DispatchQueue.main.async {
                    completion(self.ecgPayloadDict(sample, voltages: voltages))
                }
            }
        }
        store.execute(voltageQuery)
    }

    @available(iOS 14.0, *)
    private func ecgPayloadDict(_ sample: HKElectrocardiogram, voltages: [Double]) -> [String: Any] {
        var dict: [String: Any] = [
            "uuid": sample.uuid.uuidString,
            "date": iso8601String(sample.startDate),
            "classification": ecgClassificationString(sample.classification),
            "sampling_frequency": sample.samplingFrequency?.doubleValue(for: HKUnit.hertz()) ?? 0,
            "sample_count": sample.numberOfVoltageMeasurements,
            "symptoms_status": ecgSymptomsStatusString(sample.symptomsStatus),
            "voltages": voltages,
        ]
        if let hr = sample.averageHeartRate {
            dict["avg_hr"] = hr.doubleValue(for: HKUnit.count().unitDivided(by: .minute()))
        }
        return dict
    }

    private func iso8601String(_ date: Date) -> String {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime]
        return formatter.string(from: date)
    }

    /// POST a /api/ecg (endpoint separado de /api/ingest). Mismo header X-Vitals-Token.
    /// Nunca lanza: cualquier fallo de red/parseo/HTTP resuelve completion(false) para
    /// que el caller (pushEcgSequentially) siga con el siguiente ECG pendiente.
    private func postEcgPayload(_ payload: [String: Any], urlString: String, token: String,
                                 completion: @escaping (Bool) -> Void) {
        var base = urlString.trimmingCharacters(in: .whitespacesAndNewlines)
        if base.hasSuffix("/") { base.removeLast() }
        guard let url = URL(string: base + "/api/ecg") else {
            completion(false)
            return
        }
        guard let body = try? JSONSerialization.data(withJSONObject: payload, options: []) else {
            completion(false)
            return
        }

        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue(token, forHTTPHeaderField: "X-Vitals-Token")
        if let user = configuredUser, !user.isEmpty {
            request.setValue(user, forHTTPHeaderField: "X-Vitals-User")
        }
        request.httpBody = body
        request.timeoutInterval = 60 // un ECG puede pesar ~150-200 KB; más margen que el payload normal (30s)

        let task = URLSession.shared.dataTask(with: request) { data, response, error in
            DispatchQueue.main.async {
                if error != nil {
                    completion(false)
                    return
                }
                guard let http = response as? HTTPURLResponse else {
                    completion(false)
                    return
                }
                // Igual que postPayload: el status real vive en el body JSON, no solo
                // en el código HTTP — pero para el propósito de dedup, cualquier 2xx con
                // status "ok" es éxito; todo lo demás (401/'error'/etc.) no se marca
                // como empujado, así se reintenta en el próximo sync.
                guard http.statusCode >= 200 && http.statusCode < 300 else {
                    completion(false)
                    return
                }
                if let data = data, !data.isEmpty,
                   let body = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                   let status = body["status"] as? String {
                    completion(status == "ok")
                } else {
                    completion(false)
                }
            }
        }
        task.resume()
    }
}
