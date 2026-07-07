# Vitals iOS — HealthKit nativo (Fase 5D-B)

Guía manual para activar la sincronización de Apple Watch / HealthKit en la app
nativa. El código ya está escrito (plugin `VitalsHealth` + `HealthSyncManager`);
esto son los pasos que solo se pueden hacer en Xcode, con el Doc al volante.

## 0. Qué hace esto

La app nativa (Capacitor, shell delgado) gana un plugin Swift propio que:
- Lee ~13 tipos de HealthKit (HRV, RHR, respiración, SpO2, temperatura de
  muñeca, pasos, VO2max, distancia, energía, sueño, entrenamientos) de los
  últimos 45 días.
- Agrega por **día local** del iPhone (no UTC).
- Arma el mismo payload que ya consume `app/sources/healthkit.py`
  (Fase 5D-A, backend ya desplegado).
- Hace `POST {tu-instancia}/api/ingest` con el header `X-Vitals-Token`.
- Se dispara solo (auto-sync) cada vez que la app vuelve a primer plano —
  **solo si configuraste un token**. Sin token, cero llamadas (los usuarios
  que no usan HealthKit/Apple Watch no se enteran de que esto existe).

## 1. Activar la capability HealthKit en Xcode

1. Abre el proyecto: `open ios/App/App.xcodeproj` (o vía `npx cap open ios`).
2. Selecciona el target **App** → pestaña **Signing & Capabilities**.
3. Click **+ Capability** → busca **HealthKit** → agrégala.
   - Esto genera (o actualiza) `ios/App/App/App.entitlements` automáticamente.
   - **Importante**: el entitlement de HealthKit suele requerir una cuenta
     **Apple Developer Program de pago** ($99/año). Con un **Personal Team**
     gratuito, Xcode puede rechazar el build o quitar la capability al firmar.
     Si te pasa esto, anótalo — no es un bug del código, es una limitación de
     la cuenta. El código queda listo para cuando enroles en el programa de pago.
4. Dentro de la capability HealthKit, no hace falta marcar "Clinical Health
   Records" — solo lectura de los tipos estándar que el código ya pide.

## 2. Confirmar el texto de permiso (ya incluido)

`ios/App/App/Info.plist` ya trae:
```xml
<key>NSHealthShareUsageDescription</key>
<string>Vitals lee tus metricas de salud (frecuencia cardiaca, HRV, sueno,
oxigeno, temperatura, pasos y entrenamientos) para calcular tu recuperacion,
sueno y edad corporal. Tus datos solo se envian a tu propio servidor.</string>
```
No necesitas tocarlo. Solo lectura → no hace falta `NSHealthUpdateUsageDescription`.

## 3. Build & Run en tu iPhone

1. Conecta tu iPhone, selecciónalo como destino en Xcode.
2. Si tu Xcode estable no soporta tu versión de iOS, usa **Xcode-beta**
   (mismo flujo que ya usas para el resto de la app — `DEVELOPER_DIR` apuntando
   al beta si lo invocas por CLI).
3. **Product → Run** (▶). Espera `** BUILD SUCCEEDED **`.
   - Si el build falla por el entitlement (ver paso 1), confirma Team +
     capability antes de seguir.

## 4. Conceder el permiso de HealthKit

1. La primera vez que la app intente sincronizar (ver paso 5), iOS muestra la
   hoja estándar de HealthKit con la lista de tipos. Actívalos todos (o los
   que quieras compartir — los que no actives simplemente no llegan datos de
   esa métrica, el backend los tolera ausentes).
2. Si la niegas por accidente: **Ajustes → Privacidad y seguridad → Salud →
   Vitals** en el iPhone, y actívalos ahí.

## 5. Probar el flujo completo

1. **Fase 8C (paso C6): el token ya NO es opcional ni hay que inventarlo a
   mano.** Si `INGEST_TOKEN` no está en el `.env` del box, el backend lo
   autogenera al arrancar y lo persiste en `data/ingest_token.json` — copia
   el valor vigente desde la pestaña **Más → Conectar app móvil → Token de
   HealthKit/ECG** (botón "Copiar"), o si prefieres fijarlo tú mismo:
   ```
   INGEST_TOKEN=elige-un-token-largo-y-aleatorio
   ```
   en el `.env` y reinicia el servicio (`Restart-Service Vitals` en Windows,
   o el equivalente en tu plataforma) — esto tiene prioridad sobre el
   autogenerado.
   - `/api/ingest` y `/api/ecg` responden **401 SIEMPRE** que falte el header
     `X-Vitals-Token` o no coincida — ya no existe el modo "sin auth" de
     versiones anteriores.
2. En la app web (pestaña **Más**), cambia tu fuente (`source`) a `healthkit`
   en tu perfil — así el backend sabe que tus datos vienen del push nativo
   y no intenta jalar de Google/Oura/WHOOP.
3. En la app nativa **(pantalla "Conecta tu Vitals")**: pega la URL de tu
   instancia y, en el campo **"Token de sincronización (HealthKit)"** (ya
   NO es opcional — sin él, todo push recibe 401), pega el token que copiaste
   en el paso 1. Toca **Conectar**.
   - Esto guarda el token en `UserDefaults` nativo (vía `VitalsHealth.setConfig`)
     y en `localStorage` del shell.
   - El QR de "Más → Conectar app móvil" ya embebe el token como query param
     (`?ingest_token=...`) en la URL codificada — hoy la pantalla nativa
     todavía pide pegarlo a mano (no hay lector de QR propio en la app), pero
     queda ahí listo para un futuro flujo de auto-config por QR.
4. Sal de la app a Home y vuelve a abrirla (foreground) — eso dispara
   `SceneDelegate.sceneDidBecomeActive` → `HealthSyncManager.shared.autoSyncIfConfigured()`.
   - La primera vez te pedirá el permiso de HealthKit (paso 4).
5. Verifica:
   - En el box: revisa `data/healthkit_ingest.json` — debería tener el
     payload crudo recién recibido (con `_ingested_at` actualizado).
   - En la web app: `GET /api/data` (o la pestaña **Hoy/Tendencias**) debería
     mostrar los días recientes leídos del Apple Watch.

## 6. Troubleshooting

- **`window.Capacitor.Plugins.VitalsHealth` es `undefined`**: el plugin no
  cargó. Confirma que `ios/App/App/capacitor.config.json` (la copia dentro del
  target, NO solo la de la raíz del repo) tiene `"packageClassList": ["VitalsHealth"]`,
  y que corriste `npx cap sync ios` después de cualquier cambio en
  `capacitor.config.json` de la raíz (sync copia/regenera la del target).
- **No llega nada al backend / `n_days: 0`**: revisa que concediste el
  permiso de HealthKit (paso 4) y que tu Apple Watch realmente tiene datos en
  los últimos 45 días para esos tipos (algunos, como `vo2Max` o
  `appleSleepingWristTemperature`, requieren un Apple Watch compatible y
  cierto uso — son opcionales y se omiten si no hay datos).
- **401 en el POST**: el token de la app no coincide con el `INGEST_TOKEN`
  vigente del box (de `.env` o el autogenerado en `data/ingest_token.json`).
  Re-conecta con el token correcto — cópialo de nuevo desde **Más → Conectar
  app móvil**. Desde Fase 8C (paso C6) esto responde 401 SIEMPRE sin un token
  válido, ya no hay modo "sin auth".
- **El build falla por entitlement/firma**: ver nota del paso 1 — cuenta
  Personal Team gratuita puede no soportar HealthKit. Revisa el mensaje exacto
  de Xcode; si es de provisioning, ese es el límite (no el código).

## Notas

- El auto-sync es **solo en foreground** (v1). No hay `HKObserverQuery` en
  background todavía — para refrescar, basta con volver a abrir la app.
- El botón "Sincronizar HealthKit" dentro del dashboard remoto
  (`templates/vitals_ios.html`) queda **diferido** a un follow-up — por ahora,
  relanzar la app nativa = sincronizar.
- Rollback: si algo se rompe, quita `"VitalsHealth"` de ambos
  `capacitor.config.json` (raíz y `ios/App/App/`) y vuelve a `npx cap sync ios`
  — el resto de la app sigue funcionando igual, el campo de token en `www`
  queda inerte con valor vacío.
