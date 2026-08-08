import Foundation

// verify_sleep_union.swift — arnés de verificación standalone para
// SleepAggregator.swift (Paso 4 del roadmap sueno-y-duraciones).
//
// El proyecto Xcode NO tiene target de tests; esta es la ÚNICA verificación
// del código nuevo de iOS antes de que llegue a un dispositivo. Cada
// caso está diseñado para FALLAR si la implementación regresa a sumar en vez
// de unir, o si se salta alguna de las guardas (techo asleep<=inbed, recorte
// de fases, split de noches por fuente). Ningún caso pasaría igual con la
// lógica vieja — ver el comentario de cada test.
//
// Uso:
//   swiftc -o /tmp/verify_sleep ios/App/App/SleepAggregator.swift \
//          scripts/verify_sleep_union.swift && /tmp/verify_sleep

var failureCount = 0

func report(_ name: String, _ ok: Bool, _ detail: String = "") {
    if ok {
        print("OK   \(name)")
    } else {
        failureCount += 1
        print("FAIL \(name)  \(detail)")
    }
}

func makeDate(_ y: Int, _ mo: Int, _ d: Int, _ h: Int, _ mi: Int) -> Date {
    var comps = DateComponents()
    comps.year = y
    comps.month = mo
    comps.day = d
    comps.hour = h
    comps.minute = mi
    comps.second = 0
    return Calendar.current.date(from: comps)!
}

func entry(for date: String, in nights: [[String: Any]]) -> [String: Any]? {
    nights.first { ($0["date"] as? String) == date }
}

func iVal(_ d: [String: Any], _ k: String) -> Int? { d[k] as? Int }
func sVal(_ d: [String: Any], _ k: String) -> String? { d[k] as? String }

// ─────────────────────────────────────────────────────────────────────────
// test_two_sources_overlap_not_summed — el patrón que motivó el fix: Apple
// Watch Y Fitbit escriben en HealthKit la misma noche. Apple trae inBed algo
// más corto que su propio asleep (479 vs 496, sensor de "en cama" arranca
// después); Fitbit re-sincroniza y manda 3 fragmentos TRASLAPADOS entre sí
// que sin unir suman 499 min (190+150+159) pero cuya unión real es 409 min.
//
// Antes del fix: sumar TODAS las muestras de la noche sin mirar la fuente
// daba 496 (Apple) + 499 (Fitbit sin unir) = 995 — del mismo orden que el
// el orden de magnitud observado en campo. Con este harness:
//   - Si unionMinutes SUMARA en vez de UNIR, Fitbit calcularía asleep=499
//     (> que los 479 de Apple tras su propio techo) y GANARÍA la noche con
//     el número equivocado -> la aserción de asleep==479 falla.
//   - Si el techo asleep<=inbed no existiera, Apple ganaría con asleep=496
//     (sin capar a 479) -> también falla.
// ─────────────────────────────────────────────────────────────────────────
func test_two_sources_overlap_not_summed() -> Bool {
    let d = { (h: Int, m: Int) in makeDate(2024, 3, 15, h, m) }

    let appleInBed = SleepSpan(start: d(1, 0), end: d(8, 59), value: SleepStage.inBed.rawValue, sourceID: "com.apple.health")
    let appleAsleep = SleepSpan(start: d(0, 43), end: d(8, 59), value: SleepStage.asleepUnspecified.rawValue, sourceID: "com.apple.health")

    let fitbit1 = SleepSpan(start: d(0, 50), end: d(4, 0), value: SleepStage.asleepUnspecified.rawValue, sourceID: "com.fitbit.app")
    let fitbit2 = SleepSpan(start: d(3, 30), end: d(6, 0), value: SleepStage.asleepUnspecified.rawValue, sourceID: "com.fitbit.app")
    let fitbit3 = SleepSpan(start: d(5, 0), end: d(7, 39), value: SleepStage.asleepUnspecified.rawValue, sourceID: "com.fitbit.app")

    let nights = SleepAggregator.buildNights(
        from: [appleInBed, appleAsleep, fitbit1, fitbit2, fitbit3], iOS16OrLater: true
    )

    guard nights.count == 1, let rec = entry(for: "2024-03-15", in: nights) else {
        return { report("test_two_sources_overlap_not_summed", false, "esperaba 1 noche en 2024-03-15, obtuvo \(nights)"); return false }()
    }

    var ok = true
    ok = ok && (iVal(rec, "asleep") == 479)
    ok = ok && (iVal(rec, "inbed") == 479)
    ok = ok && (iVal(rec, "eff") == 100)
    ok = ok && (sVal(rec, "bedtime") == "00:43")
    ok = ok && (sVal(rec, "waketime") == "08:59")
    report("test_two_sources_overlap_not_summed", ok, "rec=\(rec)")
    return ok
}

// ─────────────────────────────────────────────────────────────────────────
// test_no_impossible_efficiency — sobre el MISMO caso de arriba: nunca se
// emite asleep > inbed ni eff > 100. Discrimina el techo duro
// (`asleepMin = min(asleepMin, totalInbed)`): sin él, Apple ganaría con
// asleep=496 pero inbed=479 -> 496 <= 479 es falso.
// ─────────────────────────────────────────────────────────────────────────
func test_no_impossible_efficiency() -> Bool {
    let d = { (h: Int, m: Int) in makeDate(2024, 3, 15, h, m) }
    let appleInBed = SleepSpan(start: d(1, 0), end: d(8, 59), value: SleepStage.inBed.rawValue, sourceID: "com.apple.health")
    let appleAsleep = SleepSpan(start: d(0, 43), end: d(8, 59), value: SleepStage.asleepUnspecified.rawValue, sourceID: "com.apple.health")
    let fitbit1 = SleepSpan(start: d(0, 50), end: d(4, 0), value: SleepStage.asleepUnspecified.rawValue, sourceID: "com.fitbit.app")
    let fitbit2 = SleepSpan(start: d(3, 30), end: d(6, 0), value: SleepStage.asleepUnspecified.rawValue, sourceID: "com.fitbit.app")
    let fitbit3 = SleepSpan(start: d(5, 0), end: d(7, 39), value: SleepStage.asleepUnspecified.rawValue, sourceID: "com.fitbit.app")

    let nights = SleepAggregator.buildNights(
        from: [appleInBed, appleAsleep, fitbit1, fitbit2, fitbit3], iOS16OrLater: true
    )

    guard let rec = entry(for: "2024-03-15", in: nights),
          let asleep = iVal(rec, "asleep"), let inbed = iVal(rec, "inbed"), let eff = iVal(rec, "eff")
    else {
        report("test_no_impossible_efficiency", false, "falta el registro esperado: \(nights)")
        return false
    }
    let ok = asleep <= inbed && eff <= 100
    report("test_no_impossible_efficiency", ok, "asleep=\(asleep) inbed=\(inbed) eff=\(eff)")
    return ok
}

// ─────────────────────────────────────────────────────────────────────────
// test_stages_coherent — una fuente emite deep (00:00-01:00) y light/core
// (00:30-01:30) TRASLAPADOS entre sí (30 min de traslape). asleep (unión)
// = 90 min; deep=60, light=60 SIN recortar sumarían 120 > 90. Discrimina el
// recorte proporcional: sin él, deep+rem+light=120 > asleep=90 (viola A3) y
// los valores exactos (45/45) no saldrían.
// ─────────────────────────────────────────────────────────────────────────
func test_stages_coherent() -> Bool {
    let d = { (h: Int, m: Int) in makeDate(2024, 3, 16, h, m) }
    let deep = SleepSpan(start: d(0, 0), end: d(1, 0), value: SleepStage.asleepDeep.rawValue, sourceID: "com.apple.health")
    let core = SleepSpan(start: d(0, 30), end: d(1, 30), value: SleepStage.asleepCore.rawValue, sourceID: "com.apple.health")

    let nights = SleepAggregator.buildNights(from: [deep, core], iOS16OrLater: true)

    guard let rec = entry(for: "2024-03-16", in: nights),
          let asleep = iVal(rec, "asleep"), let deepMin = iVal(rec, "deep"),
          let remMin = iVal(rec, "rem"), let lightMin = iVal(rec, "light")
    else {
        report("test_stages_coherent", false, "falta el registro esperado: \(nights)")
        return false
    }

    var ok = (deepMin + remMin + lightMin) <= asleep
    ok = ok && asleep == 90 && deepMin == 45 && remMin == 0 && lightMin == 45
    report("test_stages_coherent", ok, "asleep=\(asleep) deep=\(deepMin) rem=\(remMin) light=\(lightMin)")
    return ok
}

// ─────────────────────────────────────────────────────────────────────────
// test_single_source_unchanged — UNA sola fuente, SIN traslapes dentro de
// cada categoría (inBed / deep / rem / light son segmentos consecutivos que
// no se tocan). Con cero traslape, unionMinutes == sum de duraciones, así
// que el resultado debe ser BYTE-IGUAL al que daba la fórmula vieja
// (`+=`). Valores calculados A MANO aquí, no copiados del código:
//
//   inBed:  23:00 -> 07:00           = 480 min
//   light:  23:10 -> 01:10           = 120 min
//   deep:   01:10 -> 02:10           =  60 min
//   awake:  02:10 -> 02:20           =  10 min (NO cuenta, valor=2)
//   rem:    02:20 -> 03:50           =  90 min
//   light:  03:50 -> 06:50           = 180 min
//
//   asleep = 120+60+90+180           = 450
//   deep   = 60 ; rem = 90 ; light   = 120+180 = 300  (suma = 450 = asleep, sin recorte)
//   inbed  = 480 (union del único span inBed)
//   eff    = round(450/480*100)      = round(93.75) = 94
//   bedtime  = earliestStart (23:00, el span inBed empieza antes que el primero "asleep")
//   waketime = latestAsleepEnd (06:50, del último tramo "light"; el inBed
//              termina 07:00 pero NO es tipo "asleep" y no cuenta para waketime)
//   date = 2024-03-15 (día de waketime)
// ─────────────────────────────────────────────────────────────────────────
func test_single_source_unchanged() -> Bool {
    let inBed = SleepSpan(start: makeDate(2024, 3, 14, 23, 0), end: makeDate(2024, 3, 15, 7, 0),
                           value: SleepStage.inBed.rawValue, sourceID: "com.apple.health")
    let light1 = SleepSpan(start: makeDate(2024, 3, 14, 23, 10), end: makeDate(2024, 3, 15, 1, 10),
                            value: SleepStage.asleepCore.rawValue, sourceID: "com.apple.health")
    let deep = SleepSpan(start: makeDate(2024, 3, 15, 1, 10), end: makeDate(2024, 3, 15, 2, 10),
                          value: SleepStage.asleepDeep.rawValue, sourceID: "com.apple.health")
    let awake = SleepSpan(start: makeDate(2024, 3, 15, 2, 10), end: makeDate(2024, 3, 15, 2, 20),
                           value: SleepStage.awake.rawValue, sourceID: "com.apple.health")
    let rem = SleepSpan(start: makeDate(2024, 3, 15, 2, 20), end: makeDate(2024, 3, 15, 3, 50),
                         value: SleepStage.asleepREM.rawValue, sourceID: "com.apple.health")
    let light2 = SleepSpan(start: makeDate(2024, 3, 15, 3, 50), end: makeDate(2024, 3, 15, 6, 50),
                            value: SleepStage.asleepCore.rawValue, sourceID: "com.apple.health")

    let nights = SleepAggregator.buildNights(
        from: [inBed, light1, deep, awake, rem, light2], iOS16OrLater: true
    )

    guard nights.count == 1, let rec = entry(for: "2024-03-15", in: nights) else {
        report("test_single_source_unchanged", false, "esperaba 1 noche en 2024-03-15, obtuvo \(nights)")
        return false
    }

    var ok = true
    ok = ok && (iVal(rec, "asleep") == 450)
    ok = ok && (iVal(rec, "deep") == 60)
    ok = ok && (iVal(rec, "rem") == 90)
    ok = ok && (iVal(rec, "light") == 300)
    ok = ok && (iVal(rec, "inbed") == 480)
    ok = ok && (iVal(rec, "eff") == 94)
    ok = ok && (sVal(rec, "bedtime") == "23:00")
    ok = ok && (sVal(rec, "waketime") == "06:50")
    report("test_single_source_unchanged", ok, "rec=\(rec)")
    return ok
}

// ─────────────────────────────────────────────────────────────────────────
// test_night_split_by_gap — una fuente, hueco de 4.5h entre dos segmentos
// -> deben salir DOS noches (dos fechas de despertar distintas), no una.
// ─────────────────────────────────────────────────────────────────────────
func test_night_split_by_gap() -> Bool {
    let nightA = SleepSpan(start: makeDate(2024, 3, 14, 22, 0), end: makeDate(2024, 3, 14, 23, 30),
                            value: SleepStage.asleepUnspecified.rawValue, sourceID: "com.apple.health")
    // hueco: 23:30 -> 04:00 = 4.5h > 3h
    let nightB = SleepSpan(start: makeDate(2024, 3, 15, 4, 0), end: makeDate(2024, 3, 15, 6, 0),
                            value: SleepStage.asleepUnspecified.rawValue, sourceID: "com.apple.health")

    let nights = SleepAggregator.buildNights(from: [nightA, nightB], iOS16OrLater: true)

    let dates = Set(nights.compactMap { $0["date"] as? String })
    var ok = nights.count == 2
    ok = ok && dates == Set(["2024-03-14", "2024-03-15"])
    ok = ok && (iVal(entry(for: "2024-03-14", in: nights) ?? [:], "asleep") == 90)
    ok = ok && (iVal(entry(for: "2024-03-15", in: nights) ?? [:], "asleep") == 120)
    report("test_night_split_by_gap", ok, "nights=\(nights)")
    return ok
}

// ─────────────────────────────────────────────────────────────────────────
// test_interleaved_sources_do_not_bridge_gap — dos fuentes; la fuente A
// tiene un hueco real de 5h entre sus propios dos segmentos (20:00-21:00 y,
// al día siguiente, 02:00-03:00). La fuente B mete UN segmento (22:00-23:00)
// justo en medio de ese hueco. Si se agrupara TODO junto sin mirar la fuente
// (el bug que este roadmap elimina), la secuencia combinada ordenada por
// inicio nunca tiene un hueco > 3h (A1->B1 = 1h; B1->A2 = exactamente 3h, no
// > 3h) y el algoritmo viejo fundiría las tres muestras en UNA sola noche
// larga que termina el 2024-03-16 — puentea el hueco real de A.
//
// Agrupando por fuente PRIMERO (lo correcto), el hueco de 5h dentro de la
// fuente A se detecta igual, sin que le importe lo que haga B: A produce 2
// noches (2024-03-15 y 2024-03-16); B produce 1 noche que cae el mismo
// 2024-03-15 y pierde el desempate contra A1 (asleep empatado 60=60,
// desempata por inbed empatado, sourceID "com.apple.health" < "com.fitbit.app").
// Resultado esperado: 2 noches en total, nunca 1.
// ─────────────────────────────────────────────────────────────────────────
func test_interleaved_sources_do_not_bridge_gap() -> Bool {
    let base = makeDate(2024, 3, 15, 20, 0)
    func at(_ hours: Double) -> Date { base.addingTimeInterval(hours * 3600) }

    let a1 = SleepSpan(start: at(0), end: at(1), value: SleepStage.asleepUnspecified.rawValue, sourceID: "com.apple.health")
    let a2 = SleepSpan(start: at(6), end: at(7), value: SleepStage.asleepUnspecified.rawValue, sourceID: "com.apple.health")
    let b1 = SleepSpan(start: at(2), end: at(3), value: SleepStage.asleepUnspecified.rawValue, sourceID: "com.fitbit.app")

    let nights = SleepAggregator.buildNights(from: [a1, a2, b1], iOS16OrLater: true)

    let dates = Set(nights.compactMap { $0["date"] as? String })
    var ok = nights.count == 2
    ok = ok && dates == Set(["2024-03-15", "2024-03-16"])
    report("test_interleaved_sources_do_not_bridge_gap", ok, "nights=\(nights)")
    return ok
}

// ─────────────────────────────────────────────────────────────────────────
// test_inbed_fallback_uses_night_span_not_asleep  [AÑADIDO EN VALIDACIÓN]
//
// Hueco detectado por mutation-testing: NINGÚN caso del arnés original
// discriminaba la rama `hasInBed ? inBedMin : max(asleepMin, span)`.
// Reemplazar ese fallback por `asleepMin` a secas dejaba los 7 casos en OK —
// y es justo la regresión contra la que advierte el comentario heredado del
// código viejo ("eso forzaria eff=100 siempre que falte inBed, que es el caso
// tipico de Apple Watch"). Los casos previos no lo veían porque en todos
// ellos el span total coincide con el asleep (no hay tramos despierto).
//
// Una fuente, SIN segmentos inBed, con una hora despierto en medio:
//   asleep1: 23:00 -> 01:00 = 120
//   awake:   01:00 -> 02:00 =  60  (valor 2, no cuenta como asleep)
//   asleep2: 02:00 -> 05:00 = 180
//   asleep = 120 + 180              = 300  (unión, tramos disjuntos)
//   span   = 23:00 -> 05:00         = 360
//   inbed  = max(300, 360)          = 360   <- el fallback correcto
//   eff    = round(300/360*100)     = round(83.33) = 83
// Con el fallback roto: inbed=300 y eff=100. Números calculados a mano; son
// además los que daba la fórmula VIEJA (sin traslapes, suma == unión), así
// que este caso refuerza también A4.
// ─────────────────────────────────────────────────────────────────────────
func test_inbed_fallback_uses_night_span_not_asleep() -> Bool {
    let a1 = SleepSpan(start: makeDate(2024, 3, 14, 23, 0), end: makeDate(2024, 3, 15, 1, 0),
                        value: SleepStage.asleepUnspecified.rawValue, sourceID: "com.apple.health")
    let awake = SleepSpan(start: makeDate(2024, 3, 15, 1, 0), end: makeDate(2024, 3, 15, 2, 0),
                           value: SleepStage.awake.rawValue, sourceID: "com.apple.health")
    let a2 = SleepSpan(start: makeDate(2024, 3, 15, 2, 0), end: makeDate(2024, 3, 15, 5, 0),
                        value: SleepStage.asleepUnspecified.rawValue, sourceID: "com.apple.health")

    let nights = SleepAggregator.buildNights(from: [a1, awake, a2], iOS16OrLater: true)

    guard nights.count == 1, let rec = entry(for: "2024-03-15", in: nights) else {
        report("test_inbed_fallback_uses_night_span_not_asleep", false, "esperaba 1 noche, obtuvo \(nights)")
        return false
    }
    var ok = true
    ok = ok && (iVal(rec, "asleep") == 300)
    ok = ok && (iVal(rec, "inbed") == 360)
    ok = ok && (iVal(rec, "eff") == 83)
    ok = ok && (sVal(rec, "bedtime") == "23:00")
    ok = ok && (sVal(rec, "waketime") == "05:00")
    report("test_inbed_fallback_uses_night_span_not_asleep", ok, "rec=\(rec)")
    return ok
}

// ─────────────────────────────────────────────────────────────────────────
// test_source_tiebreak_is_deterministic  [AÑADIDO EN VALIDACIÓN]
//
// Segundo hueco del mutation-testing: invertir el desempate por sourceID
// (`a.sourceID < b.sourceID` -> `>`) dejaba los 7 casos originales en OK.
// El desempate existe precisamente para que el resultado sea REPRODUCIBLE:
// `buildNights` itera un diccionario por fuente, cuyo orden Swift NO
// garantiza. Sin este caso, un desempate roto o eliminado pasa desapercibido
// y el registro emitido cambia entre ejecuciones.
//
// Dos fuentes, misma fecha de despertar, asleep e inbed EMPATADOS (60 y 60):
// gana `com.apple.health` por sourceID ascendente -> waketime "02:00"
// (el de com.zzz.other sería "04:00").
// ─────────────────────────────────────────────────────────────────────────
func test_source_tiebreak_is_deterministic() -> Bool {
    let apple = SleepSpan(start: makeDate(2024, 3, 15, 1, 0), end: makeDate(2024, 3, 15, 2, 0),
                           value: SleepStage.asleepUnspecified.rawValue, sourceID: "com.apple.health")
    let other = SleepSpan(start: makeDate(2024, 3, 15, 3, 0), end: makeDate(2024, 3, 15, 4, 0),
                           value: SleepStage.asleepUnspecified.rawValue, sourceID: "com.zzz.other")

    let nights = SleepAggregator.buildNights(from: [apple, other], iOS16OrLater: true)

    guard nights.count == 1, let rec = entry(for: "2024-03-15", in: nights) else {
        report("test_source_tiebreak_is_deterministic", false, "esperaba 1 noche, obtuvo \(nights)")
        return false
    }
    var ok = (iVal(rec, "asleep") == 60)
    ok = ok && (sVal(rec, "waketime") == "02:00")
    ok = ok && (sVal(rec, "bedtime") == "01:00")
    report("test_source_tiebreak_is_deterministic", ok, "rec=\(rec)")
    return ok
}

// ─────────────────────────────────────────────────────────────────────────
// test_empty_input — lista vacía -> [] sin crash.
// ─────────────────────────────────────────────────────────────────────────
func test_empty_input() -> Bool {
    let nights = SleepAggregator.buildNights(from: [], iOS16OrLater: true)
    let ok = nights.isEmpty
    report("test_empty_input", ok, "nights=\(nights)")
    return ok
}

// ═════════════════════════════════════════════════════════════════════════
// HIPNOGRAMA (segments) — añadido tras detectar que los bloques de sueño
// llevaban muertos desde el 5-jul: solo Google los mandaba, y HealthKit gana
// todas las noches. Ahora los emite la propia app, desde la MISMA fuente que
// ganó la noche, así cuadran con los totales deep/rem/light.
//
// 🔑 El backend (app/sleep_segments.py::validate_segments) descarta el campo
// COMPLETO ante un solo traslape o un orden incorrecto — nunca acepta una
// lista parcial. Por eso estos casos verifican la INVARIANTE, no solo los
// valores.
// ═════════════════════════════════════════════════════════════════════════

func segs(_ d: [String: Any]) -> [[String: Any]]? { d["segments"] as? [[String: Any]] }

/// Chequea el contrato exacto que exige validate_segments: ordenado por `s`,
/// sin traslapes, `e > s`, `s >= 0` y `st` en las 4 etiquetas válidas.
func segmentsContractHolds(_ list: [[String: Any]]) -> String? {
    let valid: Set<String> = ["awake", "light", "rem", "deep"]
    var prevEnd = Int.min
    var prevStart = Int.min
    for seg in list {
        guard let s = seg["s"] as? Int, let e = seg["e"] as? Int,
              let st = seg["st"] as? String else { return "campo faltante o de tipo raro en \(seg)" }
        if !valid.contains(st) { return "etapa invalida '\(st)'" }
        if s < 0 { return "s negativo (\(s))" }
        if e <= s { return "e <= s (\(s),\(e))" }
        if s < prevStart { return "no esta ordenado por s" }
        if s < prevEnd { return "TRASLAPE: \(s) < \(prevEnd)" }
        prevEnd = e
        prevStart = s
    }
    return nil
}

// test_segments_basic — noche etiquetada de una sola fuente. Los minutos son
// RELATIVOS a bedtime (23:00), no absolutos: si alguien cambiara el origen a
// medianoche o a las 00:00 del día, estos números se mueven todos.
func test_segments_basic() -> Bool {
    let spans = [
        SleepSpan(start: makeDate(2024, 4, 10, 23, 0), end: makeDate(2024, 4, 10, 23, 10), value: 2, sourceID: "apple"),
        SleepSpan(start: makeDate(2024, 4, 10, 23, 10), end: makeDate(2024, 4, 11, 0, 0), value: 3, sourceID: "apple"),
        SleepSpan(start: makeDate(2024, 4, 11, 0, 0), end: makeDate(2024, 4, 11, 1, 0), value: 4, sourceID: "apple"),
        SleepSpan(start: makeDate(2024, 4, 11, 1, 0), end: makeDate(2024, 4, 11, 1, 30), value: 5, sourceID: "apple"),
    ]
    let nights = SleepAggregator.buildNights(from: spans, iOS16OrLater: true)
    guard let e = entry(for: "2024-04-11", in: nights), let list = segs(e) else {
        report("test_segments_basic", false, "no se emitio segments")
        return false
    }
    if let bad = segmentsContractHolds(list) {
        report("test_segments_basic", false, bad)
        return false
    }
    let got = list.map { "\($0["s"] as! Int)-\($0["e"] as! Int):\($0["st"] as! String)" }.joined(separator: " ")
    let want = "0-10:awake 10-60:light 60-120:deep 120-150:rem"
    report("test_segments_basic", got == want, "esperado [\(want)] pero vino [\(got)]")
    return got == want
}

// test_segments_no_overlap_after_clipping — el caso que MATA el campo entero
// en el servidor: un mismo dispositivo re-sincroniza y repite muestras
// traslapadas. Sin el recorte, validate_segments devuelve None y el usuario
// se queda sin bloques otra vez, exactamente el bug que vinimos a arreglar.
func test_segments_no_overlap_after_clipping() -> Bool {
    let spans = [
        SleepSpan(start: makeDate(2024, 4, 12, 23, 0), end: makeDate(2024, 4, 13, 0, 0), value: 3, sourceID: "fitbit"),
        SleepSpan(start: makeDate(2024, 4, 12, 23, 30), end: makeDate(2024, 4, 13, 0, 30), value: 3, sourceID: "fitbit"),
        SleepSpan(start: makeDate(2024, 4, 13, 0, 15), end: makeDate(2024, 4, 13, 1, 0), value: 4, sourceID: "fitbit"),
    ]
    let nights = SleepAggregator.buildNights(from: spans, iOS16OrLater: true)
    guard let e = entry(for: "2024-04-13", in: nights), let list = segs(e) else {
        report("test_segments_no_overlap_after_clipping", false, "no se emitio segments")
        return false
    }
    if let bad = segmentsContractHolds(list) {
        report("test_segments_no_overlap_after_clipping", false, bad)
        return false
    }
    // Los dos 'light' traslapados se funden en 0-90; el deep arranca donde
    // termina, recortado de 75 a 90.
    let got = list.map { "\($0["s"] as! Int)-\($0["e"] as! Int):\($0["st"] as! String)" }.joined(separator: " ")
    let want = "0-90:light 90-120:deep"
    report("test_segments_no_overlap_after_clipping", got == want, "esperado [\(want)] pero vino [\(got)]")
    return got == want
}

// test_segments_absent_when_unstaged — asleepUnspecified NO se mapea a
// ninguna etapa (sería inventar). La noche entra igual, solo sin bloques.
func test_segments_absent_when_unstaged() -> Bool {
    let spans = [
        SleepSpan(start: makeDate(2024, 4, 14, 23, 0), end: makeDate(2024, 4, 15, 6, 0), value: 1, sourceID: "viejo"),
    ]
    let nights = SleepAggregator.buildNights(from: spans, iOS16OrLater: true)
    guard let e = entry(for: "2024-04-15", in: nights) else {
        report("test_segments_absent_when_unstaged", false, "la noche no se emitio")
        return false
    }
    let ok = segs(e) == nil && iVal(e, "asleep") == 420
    report("test_segments_absent_when_unstaged", ok,
           "segments=\(String(describing: segs(e))) asleep=\(String(describing: iVal(e, "asleep")))")
    return ok
}

// test_segments_excludes_inbed — inBed cubre TODA la noche; si se colara como
// bloque, se traslaparía con todo y el servidor tiraría el campo entero.
// Además fija el origen: bedtime es el inicio del inBed (22:50), no el del
// primer segmento dormido.
func test_segments_excludes_inbed() -> Bool {
    let spans = [
        SleepSpan(start: makeDate(2024, 4, 16, 22, 50), end: makeDate(2024, 4, 17, 6, 0), value: 0, sourceID: "apple"),
        SleepSpan(start: makeDate(2024, 4, 16, 23, 0), end: makeDate(2024, 4, 17, 2, 0), value: 3, sourceID: "apple"),
        SleepSpan(start: makeDate(2024, 4, 17, 2, 0), end: makeDate(2024, 4, 17, 3, 0), value: 4, sourceID: "apple"),
    ]
    let nights = SleepAggregator.buildNights(from: spans, iOS16OrLater: true)
    guard let e = entry(for: "2024-04-17", in: nights), let list = segs(e) else {
        report("test_segments_excludes_inbed", false, "no se emitio segments")
        return false
    }
    if let bad = segmentsContractHolds(list) {
        report("test_segments_excludes_inbed", false, bad)
        return false
    }
    let got = list.map { "\($0["s"] as! Int)-\($0["e"] as! Int):\($0["st"] as! String)" }.joined(separator: " ")
    let want = "10-190:light 190-250:deep"
    report("test_segments_excludes_inbed", got == want, "esperado [\(want)] pero vino [\(got)]")
    return got == want
}

// test_segments_merge_adjacent_same_stage — dos bloques contiguos de la misma
// etapa salen como UNO. Si no se fundieran, awakenings() del backend seguiría
// contando bien, pero el hipnograma tendría costuras artificiales.
func test_segments_merge_adjacent_same_stage() -> Bool {
    let spans = [
        SleepSpan(start: makeDate(2024, 4, 18, 23, 0), end: makeDate(2024, 4, 19, 0, 0), value: 3, sourceID: "apple"),
        SleepSpan(start: makeDate(2024, 4, 19, 0, 0), end: makeDate(2024, 4, 19, 1, 0), value: 3, sourceID: "apple"),
    ]
    let nights = SleepAggregator.buildNights(from: spans, iOS16OrLater: true)
    guard let e = entry(for: "2024-04-19", in: nights), let list = segs(e) else {
        report("test_segments_merge_adjacent_same_stage", false, "no se emitio segments")
        return false
    }
    let got = list.map { "\($0["s"] as! Int)-\($0["e"] as! Int):\($0["st"] as! String)" }.joined(separator: " ")
    let want = "0-120:light"
    report("test_segments_merge_adjacent_same_stage", got == want, "esperado [\(want)] pero vino [\(got)]")
    return got == want
}

// test_segments_come_from_winning_source — la razón de ser de todo esto: los
// bloques tienen que venir del MISMO reloj que aportó los totales. Apple gana
// la noche (420 min vs 180) y es su hipnograma el que debe salir; si saliera
// el de Fitbit, la gráfica contradiría los números de al lado.
func test_segments_come_from_winning_source() -> Bool {
    let spans = [
        SleepSpan(start: makeDate(2024, 4, 20, 23, 0), end: makeDate(2024, 4, 21, 5, 0), value: 3, sourceID: "apple"),
        SleepSpan(start: makeDate(2024, 4, 21, 5, 0), end: makeDate(2024, 4, 21, 6, 0), value: 4, sourceID: "apple"),
        SleepSpan(start: makeDate(2024, 4, 20, 23, 0), end: makeDate(2024, 4, 21, 2, 0), value: 5, sourceID: "fitbit"),
    ]
    let nights = SleepAggregator.buildNights(from: spans, iOS16OrLater: true)
    guard let e = entry(for: "2024-04-21", in: nights), let list = segs(e) else {
        report("test_segments_come_from_winning_source", false, "no se emitio segments")
        return false
    }
    let stages = list.compactMap { $0["st"] as? String }
    // Apple: light + deep. Fitbit habría dado un unico bloque 'rem'.
    let ok = iVal(e, "asleep") == 420 && stages == ["light", "deep"]
    report("test_segments_come_from_winning_source", ok,
           "asleep=\(String(describing: iVal(e, "asleep"))) etapas=\(stages)")
    return ok
}

// test_segments_same_start_is_deterministic — dos muestras que ARRANCAN en el
// mismo instante (deep corto + light largo, cosa que Apple emite). El sort de
// Swift no es estable, así que sin desempatar por `end` el orden quedaba
// indefinido y el recorte de traslapes producía bloques distintos entre
// corridas. Se pasan a propósito en el orden "malo" (el largo primero): con
// el desempate correcto gana igual el corto.
//
// Nota honesta sobre `raw.sort` dentro de buildSegments: es un mutante
// EQUIVALENTE — buildNights ya entrega los spans ordenados, así que ningún
// caso puede matarlo. Se conserva porque su violación no daría un número mal,
// daría CERO bloques (el backend descarta el campo entero ante un traslape),
// y cuesta nada.
func test_segments_same_start_is_deterministic() -> Bool {
    let spans = [
        SleepSpan(start: makeDate(2024, 4, 22, 23, 0), end: makeDate(2024, 4, 23, 0, 0), value: 3, sourceID: "apple"),
        SleepSpan(start: makeDate(2024, 4, 22, 23, 0), end: makeDate(2024, 4, 22, 23, 30), value: 4, sourceID: "apple"),
    ]
    var seen = Set<String>()
    for _ in 0..<20 {
        let nights = SleepAggregator.buildNights(from: spans, iOS16OrLater: true)
        guard let e = entry(for: "2024-04-23", in: nights), let list = segs(e) else {
            report("test_segments_same_start_is_deterministic", false, "no se emitio segments")
            return false
        }
        if let bad = segmentsContractHolds(list) {
            report("test_segments_same_start_is_deterministic", false, bad)
            return false
        }
        seen.insert(list.map { "\($0["s"] as! Int)-\($0["e"] as! Int):\($0["st"] as! String)" }.joined(separator: " "))
    }
    let want = "0-30:deep 30-60:light"
    let ok = seen == [want]
    let why = seen.count > 1
        ? "NO determinista, resultados distintos entre corridas: \(seen)"
        : "determinista pero equivocado: esperado [\(want)] y vino \(seen)"
    report("test_segments_same_start_is_deterministic", ok, why)
    return ok
}

// test_segments_sum_matches_totals_on_clean_night — la propiedad que hace
// creíble la pantalla: los bloques tienen que sumar EXACTAMENTE los totales
// deep/rem/light que se muestran al lado.
//
// Se cumple siempre que la fuente NO emita fases traslapadas entre sí (el
// caso normal: las etapas del Apple Watch son mutuamente excluyentes). Con
// fases traslapadas del mismo reloj los dos caminos resuelven el traslape
// distinto — los totales recortan proporcionalmente, los bloques recortan por
// orden — y divergen unos minutos. Medido en una noche patológica hecha a
// propósito: bloques 230/90/100 vs totales 222/111/87. Está documentado como
// limitación conocida, no corregido: unificar ambos caminos cambiaría los
// totales que ya alimentan el motor de sueño.
func test_segments_sum_matches_totals_on_clean_night() -> Bool {
    let spans = [
        SleepSpan(start: makeDate(2024, 4, 24, 23, 0), end: makeDate(2024, 4, 24, 23, 45), value: 3, sourceID: "apple"),
        SleepSpan(start: makeDate(2024, 4, 24, 23, 45), end: makeDate(2024, 4, 25, 1, 0), value: 4, sourceID: "apple"),
        SleepSpan(start: makeDate(2024, 4, 25, 1, 0), end: makeDate(2024, 4, 25, 2, 30), value: 5, sourceID: "apple"),
        SleepSpan(start: makeDate(2024, 4, 25, 2, 30), end: makeDate(2024, 4, 25, 2, 45), value: 2, sourceID: "apple"),
        SleepSpan(start: makeDate(2024, 4, 25, 2, 45), end: makeDate(2024, 4, 25, 6, 0), value: 3, sourceID: "apple"),
    ]
    let nights = SleepAggregator.buildNights(from: spans, iOS16OrLater: true)
    guard let e = entry(for: "2024-04-25", in: nights), let list = segs(e) else {
        report("test_segments_sum_matches_totals_on_clean_night", false, "no se emitio segments")
        return false
    }
    if let bad = segmentsContractHolds(list) {
        report("test_segments_sum_matches_totals_on_clean_night", false, bad)
        return false
    }
    var byStage: [String: Int] = [:]
    for seg in list {
        byStage[seg["st"] as! String, default: 0] += (seg["e"] as! Int) - (seg["s"] as! Int)
    }
    let ok = byStage["light"] == iVal(e, "light")
        && byStage["deep"] == iVal(e, "deep")
        && byStage["rem"] == iVal(e, "rem")
        && byStage["awake"] == 15
    report("test_segments_sum_matches_totals_on_clean_night", ok,
           "bloques \(byStage) vs totales light=\(iVal(e, "light")!) deep=\(iVal(e, "deep")!) rem=\(iVal(e, "rem")!)")
    return ok
}

// ─────────────────────────────────────────────────────────────────────────
// Entrada: @main en vez de código suelto a nivel de archivo — swiftc solo
// permite código top-level "suelto" en un archivo llamado main.swift cuando
// se compilan varios .swift juntos, y este arnés se compila junto a
// SleepAggregator.swift bajo su propio nombre (ver comando arriba).
// ─────────────────────────────────────────────────────────────────────────
@main
struct VerifySleepUnion {
    static func main() {
        _ = test_two_sources_overlap_not_summed()
        _ = test_no_impossible_efficiency()
        _ = test_stages_coherent()
        _ = test_single_source_unchanged()
        _ = test_night_split_by_gap()
        _ = test_interleaved_sources_do_not_bridge_gap()
        _ = test_inbed_fallback_uses_night_span_not_asleep()
        _ = test_source_tiebreak_is_deterministic()
        _ = test_empty_input()
        _ = test_segments_basic()
        _ = test_segments_no_overlap_after_clipping()
        _ = test_segments_absent_when_unstaged()
        _ = test_segments_excludes_inbed()
        _ = test_segments_merge_adjacent_same_stage()
        _ = test_segments_come_from_winning_source()
        _ = test_segments_same_start_is_deterministic()
        _ = test_segments_sum_matches_totals_on_clean_night()

        if failureCount > 0 {
            print("\n\(failureCount) test(s) FAILED")
            exit(1)
        } else {
            print("\nAll tests OK")
            exit(0)
        }
    }
}
