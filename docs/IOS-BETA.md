# Vitals iOS — Haz tu propia beta (TestFlight)

Esta app es un **shell nativo delgado** (Capacitor + WKWebView) que carga **tu propia instancia**
self-hosted de Vitals. No redistribuimos binarios: tú compilas tu propio build con tu cuenta
Apple Developer. Toma ~30 min la primera vez.

## Requisitos
- Una Mac con **Xcode** (incluye los componentes de plataforma iOS — Xcode > Settings > Components).
- **Node.js** y este repo clonado.
- Una **cuenta Apple Developer de pago** ($99/año) para subir a TestFlight.
  *(Con un Apple ID gratis puedes correrla en tu iPhone con firma de 7 días, pero NO TestFlight.)*
- Tu instancia de Vitals corriendo y accesible por HTTPS (tu Tailscale, p. ej.).

## 1. Preparar el proyecto
```bash
npm install
npx cap sync ios
```

## 2. Abrir en Xcode
```bash
open ios/App/App.xcodeproj
```

## 3. Firmar (Signing & Capabilities)
1. Selecciona el target **App** → pestaña **Signing & Capabilities**.
2. **Team**: elige tu equipo de Apple Developer.
3. **Bundle Identifier**: cámbialo a uno tuyo único (ej. `com.tunombre.vitals`). El default
   `tv.docstream.vitals` probablemente esté tomado — usa el tuyo.

## 4. (Opcional) Probar en tu iPhone o simulador
- Conecta tu iPhone (o elige un simulador) y pulsa **Run** (▶).
- Al abrir verás la pantalla **"Conecta tu Vitals"**: pega la URL de tu instancia
  (tu Tailscale HTTPS) y toca **Conectar**. La app la recuerda.
  *(El escaneo de QR llega en una versión próxima — ver "Pendiente".)*

## 5. Subir a TestFlight
1. En Xcode: selecciona destino **Any iOS Device (arm64)**.
2. **Product > Archive**. Espera a que termine.
3. En el Organizer: **Distribute App > App Store Connect > Upload**.
4. En [App Store Connect](https://appstoreconnect.apple.com) → tu app → **TestFlight**:
   - Internal testers (tú y hasta 100 del equipo): disponible casi al instante.
   - External testers (link público hasta 10k): requiere un **Beta App Review** ligero.
5. Instala **TestFlight** desde la App Store en tu iPhone, acepta la invitación, instala Vitals.

> ⚠️ Los builds de TestFlight **expiran a los 90 días** — sube uno nuevo cada trimestre.

## 6. Conectar la app a tu instancia
Al abrir la app por primera vez, pega tu URL HTTPS y toca **Conectar**.
La app la recuerda y carga tu Vitals como si fuera nativa.

## Pendiente (mejoras futuras)
- **Escaneo de QR**: el plugin de cámara que probamos (`@capacitor-mlkit/barcode-scanning`) no es
  compatible con el modo SPM de Capacitor 7. Volverá cuando se migre a CocoaPods o a un scanner
  compatible con SPM. El shell ya tiene el handler listo (reaparece solo cuando haya scanner) y la
  web app generará el QR en **Más → Conectar app móvil**.
- **Botón "cambiar instancia"** persistente. Por ahora, para apuntar a otra instancia: borra y
  reinstala la app.

## Troubleshooting
- **"iOS XX.X is not installed" / simulador desactualizado**: Xcode > Settings > Components →
  descarga la plataforma iOS y el simulador. Reinicia la Mac si persiste.
- **Falla la firma**: confirma Team + un Bundle ID único tuyo (no el default).
- **La cámara no abre**: revisa que aceptaste el permiso; el escaneo usa `NSCameraUsageDescription`
  (ya incluido). Si no, usa la entrada manual de URL.
- **Ícono**: el repo trae un placeholder. Reemplaza `assets/icon.png` (1024×1024, sin transparencia)
  por el tuyo y corre `npx @capacitor/assets generate --ios`.
