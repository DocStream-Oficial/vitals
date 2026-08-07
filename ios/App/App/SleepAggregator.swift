import Foundation

/// Valores crudos de HKCategoryValueSleepAnalysis que nos importan. Se pasan
/// como Int para que este archivo NO dependa de HealthKit y pueda compilarse y
/// probarse en el Mac con swiftc (el proyecto no tiene target de tests).
enum SleepStage: Int {
    case inBed = 0
    case asleepUnspecified = 1
    case awake = 2
    case asleepCore = 3
    case asleepDeep = 4
    case asleepREM = 5
}

/// Una muestra de sueño, aplanada desde HKCategorySample.
struct SleepSpan {
    let start: Date
    let end: Date
    let value: Int
    let sourceID: String
}

/// Reconstruye "noches" de sueño reales a partir de muestras de HealthKit que
/// pueden venir de VARIOS dispositivos (Apple Watch + Fitbit escribiendo ambos
/// en HealthKit). El bug que esto reemplaza sumaba TODAS las muestras
/// traslapadas de la noche sin importar de qué reloj venían, produciendo
/// noches de `asleep > inbed` (eficiencia >100%, muy por encima de 100% en datos de campo).
///
/// Decisión (ver roadmap sueno-y-duraciones): reconstruir la noche POR FUENTE
/// y quedarse, por fecha de despertar, con la de mayor `asleep` — NUNCA unir
/// entre fuentes. Unir entre fuentes obligaría a arbitrar fases minuto a
/// minuto (deep de un reloj vs light del otro en la misma ventana), que ya
/// exige elegir una fuente de todos modos.
enum SleepAggregator {

    /// Unión de intervalos clásica: ordena por inicio y funde mientras el
    /// siguiente intervalo empiece antes o al mismo tiempo que termina el
    /// acumulado. Devuelve la suma de las duraciones YA fundidas, en minutos.
    /// Lista vacía -> 0. Esto es lo que reemplaza al `asleepMin += durMin` que
    /// sumaba muestras traslapadas del mismo dispositivo (re-sincronizaciones).
    static func unionMinutes(_ spans: [(Date, Date)]) -> Double {
        guard !spans.isEmpty else { return 0 }
        let sorted = spans.sorted { $0.0 < $1.0 }
        var totalSeconds: Double = 0
        var curStart = sorted[0].0
        var curEnd = sorted[0].1
        for span in sorted.dropFirst() {
            if span.0 <= curEnd {
                if span.1 > curEnd { curEnd = span.1 }
            } else {
                totalSeconds += curEnd.timeIntervalSince(curStart)
                curStart = span.0
                curEnd = span.1
            }
        }
        totalSeconds += curEnd.timeIntervalSince(curStart)
        return totalSeconds / 60.0
    }

    /// Parte una lista de spans YA de una sola fuente y YA ordenados por
    /// inicio en "noches": gap > 3h desde el fin acumulado abre noche nueva.
    /// Misma regla que usaba HealthSyncManager.readSleep antes de este
    /// cambio (línea 700), ahora aplicada DENTRO de cada fuente por separado
    /// — evita que muestras intercaladas de dos relojes puenteen un hueco
    /// real y peguen dos noches distintas en una.
    private static func splitNights(_ sortedSpans: [SleepSpan]) -> [[SleepSpan]] {
        var nights: [[SleepSpan]] = []
        var current: [SleepSpan] = []
        var lastEnd: Date?

        for span in sortedSpans {
            if let le = lastEnd, span.start.timeIntervalSince(le) > 3 * 3600 {
                if !current.isEmpty { nights.append(current) }
                current = []
            }
            current.append(span)
            if lastEnd == nil || span.end > lastEnd! {
                lastEnd = span.end
            }
        }
        if !current.isEmpty { nights.append(current) }
        return nights
    }

    /// Construye el registro de UNA noche de UNA fuente, con unión de
    /// intervalos por fase. `nil` si la noche no cumple la guardia mínima
    /// (sin `asleep`, sin inicio/fin detectable).
    private static func buildNightEntry(
        _ night: [SleepSpan], iOS16OrLater: Bool, sourceID: String
    ) -> (date: String, asleep: Double, inbed: Double, sourceID: String, dict: [String: Any])? {
        let asleepValues: Set<Int> = {
            var s: Set<Int> = [SleepStage.asleepUnspecified.rawValue]
            if iOS16OrLater {
                s.insert(SleepStage.asleepCore.rawValue)
                s.insert(SleepStage.asleepDeep.rawValue)
                s.insert(SleepStage.asleepREM.rawValue)
            }
            return s
        }()

        var earliestStart: Date?
        var latestSegmentEnd: Date?
        var latestAsleepEnd: Date?
        var hasInBed = false

        var asleepIntervals: [(Date, Date)] = []
        var deepIntervals: [(Date, Date)] = []
        var remIntervals: [(Date, Date)] = []
        var coreIntervals: [(Date, Date)] = []
        var inBedIntervals: [(Date, Date)] = []

        for span in night {
            if earliestStart == nil || span.start < earliestStart! {
                earliestStart = span.start
            }
            if latestSegmentEnd == nil || span.end > latestSegmentEnd! {
                latestSegmentEnd = span.end
            }

            if span.value == SleepStage.inBed.rawValue {
                inBedIntervals.append((span.start, span.end))
                hasInBed = true
                continue
            }

            if asleepValues.contains(span.value) {
                asleepIntervals.append((span.start, span.end))
                if latestAsleepEnd == nil || span.end > latestAsleepEnd! {
                    latestAsleepEnd = span.end
                }
                if iOS16OrLater {
                    if span.value == SleepStage.asleepDeep.rawValue {
                        deepIntervals.append((span.start, span.end))
                    } else if span.value == SleepStage.asleepREM.rawValue {
                        remIntervals.append((span.start, span.end))
                    } else if span.value == SleepStage.asleepCore.rawValue {
                        coreIntervals.append((span.start, span.end))
                    }
                }
            }
        }

        // Guardia idéntica a la de HealthSyncManager de antes: sin waketime,
        // sin bedtime o sin nada de asleep, la noche no se emite.
        guard let waketime = latestAsleepEnd, let bedtime = earliestStart else {
            return nil
        }

        var asleepMin = unionMinutes(asleepIntervals)
        guard asleepMin > 0 else { return nil }

        var deepMin = unionMinutes(deepIntervals)
        var remMin = unionMinutes(remIntervals)
        var coreMin = unionMinutes(coreIntervals)
        let inBedMin = unionMinutes(inBedIntervals)

        // inbed = unión de segmentos inBed si existen; si no, span TOTAL de la
        // noche (desde el primer segmento hasta el ultimo, sea asleep o inBed)
        // — no "asleepMin" (eso forzaria eff=100 siempre que falte inBed,
        // que es el caso tipico de Apple Watch, que normalmente NO emite
        // segmentos inBed explicitos).
        let spanEnd = latestSegmentEnd ?? waketime
        let totalInbed = hasInBed ? inBedMin : max(asleepMin, spanEnd.timeIntervalSince(bedtime) / 60.0)

        // Techo duro: nunca se emite asleep > inbed, aunque una fuente futura
        // mienta o dos relojes se traslapen entre fases de forma rara.
        asleepMin = min(asleepMin, totalInbed)

        // Coherencia de fases: si la fuente emitió fases traslapadas ENTRE SÍ
        // (deep y light del mismo dispositivo cubriendo el mismo minuto), su
        // suma puede superar a asleepMin (que ya está topado). Recortar
        // proporcionalmente es preferible a emitir partes que superan al todo.
        let phaseSum = deepMin + remMin + coreMin
        if phaseSum > asleepMin && phaseSum > 0 {
            let factor = asleepMin / phaseSum
            deepMin *= factor
            remMin *= factor
            coreMin *= factor
        }

        let eff: Double? = totalInbed > 0 ? (asleepMin / totalInbed) * 100.0 : nil

        let dateKey = dayString(waketime)

        var entry: [String: Any] = [
            "date": dateKey,
            "asleep": Int(asleepMin.rounded()),
            "deep": Int(deepMin.rounded()),
            "rem": Int(remMin.rounded()),
            "light": Int(coreMin.rounded()),
            "bedtime": hmString(bedtime),
            "waketime": hmString(waketime),
            "inbed": Int(totalInbed.rounded()),
        ]
        if let eff = eff {
            entry["eff"] = Int(eff.rounded())
        }
        if let segments = buildSegments(night, bedtime: bedtime, iOS16OrLater: iOS16OrLater) {
            entry["segments"] = segments
        }

        return (dateKey, asleepMin, totalInbed, sourceID, entry)
    }

    /// Etapas de HealthKit -> las 4 etiquetas que acepta el backend
    /// (`app/sleep_segments.py::validate_segments`). `asleepUnspecified` NO
    /// está aquí a propósito: significa "dormido, etapa desconocida" y no hay
    /// bucket para eso — meterlo como "light" sería inventar. Queda fuera del
    /// hipnograma igual que ya queda fuera de los totales deep/rem/light.
    private static let stageLabels: [Int: String] = [
        SleepStage.awake.rawValue: "awake",
        SleepStage.asleepCore.rawValue: "light",
        SleepStage.asleepREM.rawValue: "rem",
        SleepStage.asleepDeep.rawValue: "deep",
    ]

    /// Hipnograma de UNA noche de UNA fuente: [{s, e, st}] en minutos relativos
    /// a `bedtime`. `nil` si la fuente no etiquetó fases (el backend deja la
    /// noche entrar igual, solo sin desglose por bloques).
    ///
    /// 🔑 El backend DESCARTA el campo `segments` COMPLETO ante un solo
    /// traslape (validate_segments devuelve None, nunca una lista parcial), así
    /// que aquí se recortan los traslapes antes de emitir: un mismo dispositivo
    /// puede repetir muestras al re-sincronizar. Se recorta en minutos ENTEROS,
    /// después de redondear, porque dos segmentos que no se traslapan en
    /// segundos sí pueden hacerlo al redondear a minuto.
    ///
    /// Los bloques salen de la MISMA fuente que ganó la noche, así que cuadran
    /// con los totales deep/rem/light que se muestran al lado.
    private static func buildSegments(
        _ night: [SleepSpan], bedtime: Date, iOS16OrLater: Bool
    ) -> [[String: Any]]? {
        guard iOS16OrLater else { return nil }

        var raw: [(s: Int, e: Int, st: String)] = []
        for span in night {
            guard let label = stageLabels[span.value] else { continue }
            let s = Int((span.start.timeIntervalSince(bedtime) / 60.0).rounded())
            let e = Int((span.end.timeIntervalSince(bedtime) / 60.0).rounded())
            // s < 0 no debería pasar (bedtime es el inicio más temprano de la
            // noche), pero si pasara el backend descartaría todo el campo.
            if s < 0 || e <= s { continue }
            raw.append((s, e, label))
        }
        guard !raw.isEmpty else { return nil }

        raw.sort { $0.s != $1.s ? $0.s < $1.s : $0.e < $1.e }

        var out: [(s: Int, e: Int, st: String)] = []
        for var seg in raw {
            if let last = out.last {
                if seg.s < last.e { seg.s = last.e }   // recorte del traslape
                if seg.e <= seg.s { continue }         // quedó vacío -> se cae
                if seg.st == last.st && seg.s == last.e {
                    out[out.count - 1].e = seg.e       // funde contiguos iguales
                    continue
                }
            }
            out.append(seg)
        }
        guard !out.isEmpty else { return nil }

        return out.map { ["s": $0.s, "e": $0.e, "st": $0.st] }
    }

    /// `true` si `a` le gana a `b`: mayor `asleep`; empate -> mayor `inbed`;
    /// sigue empatado -> `sourceID` ascendente (determinismo del test).
    private static func isBetter(
        _ a: (date: String, asleep: Double, inbed: Double, sourceID: String, dict: [String: Any]),
        than b: (date: String, asleep: Double, inbed: Double, sourceID: String, dict: [String: Any])
    ) -> Bool {
        if a.asleep != b.asleep { return a.asleep > b.asleep }
        if a.inbed != b.inbed { return a.inbed > b.inbed }
        return a.sourceID < b.sourceID
    }

    /// Agrupa `spans` por fuente, parte cada fuente en noches, construye el
    /// registro de cada noche (con unión de intervalos) y, por fecha de
    /// despertar, se queda con el de mayor `asleep` entre todas las fuentes.
    static func buildNights(from spans: [SleepSpan], iOS16OrLater: Bool) -> [[String: Any]] {
        guard !spans.isEmpty else { return [] }

        var bySource: [String: [SleepSpan]] = [:]
        for span in spans {
            bySource[span.sourceID, default: []].append(span)
        }

        var candidates: [(date: String, asleep: Double, inbed: Double, sourceID: String, dict: [String: Any])] = []
        for (sourceID, sourceSpans) in bySource {
            // Desempate por `end`: el sort de Swift NO es estable, así que con
            // dos muestras que arrancan en el MISMO instante (p.ej. un bloque
            // deep corto y uno light largo desde las 23:00) el orden quedaba
            // indefinido — y de ese orden depende cómo se recortan los
            // traslapes del hipnograma. Sin este desempate, la misma entrada
            // podía producir bloques distintos entre corridas.
            let sorted = sourceSpans.sorted {
                $0.start != $1.start ? $0.start < $1.start : $0.end < $1.end
            }
            for night in splitNights(sorted) {
                if let entry = buildNightEntry(night, iOS16OrLater: iOS16OrLater, sourceID: sourceID) {
                    candidates.append(entry)
                }
            }
        }

        var bestByDate: [String: (date: String, asleep: Double, inbed: Double, sourceID: String, dict: [String: Any])] = [:]
        for c in candidates {
            if let cur = bestByDate[c.date] {
                if isBetter(c, than: cur) {
                    bestByDate[c.date] = c
                }
            } else {
                bestByDate[c.date] = c
            }
        }

        return bestByDate.keys.sorted().map { bestByDate[$0]!.dict }
    }

    // MARK: - Formato de fechas (movido desde HealthSyncManager)

    static func dayString(_ date: Date) -> String {
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

    static func hmString(_ date: Date) -> String {
        let cal = Calendar.current
        let formatter = DateFormatter()
        formatter.calendar = Calendar(identifier: .gregorian)
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = cal.timeZone
        formatter.dateFormat = "HH:mm"
        return formatter.string(from: date)
    }
}
