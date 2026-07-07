# ============================================================================
# Vitals — Deploy en Windows + NSSM + Tailscale serve
# Correr en PowerShell COMO ADMINISTRADOR desde la carpeta de la app.
#
# GOTCHA: en algunos boxes Windows `python3` NO existe (stub de la Microsoft
#         Store). Este script usa `py -3`. Ajusta si tu instalación es distinta.
# ============================================================================

$ErrorActionPreference = "Stop"
$AppDir = "C:\path\to\vitals-app"
$Port   = 8700

Write-Host "== 1. Verificando Python (py -3) ==" -ForegroundColor Cyan
py -3 --version
if ($LASTEXITCODE -ne 0) { throw "py -3 no responde. Instala Python 3 (python.org), NO el de la Store." }

Write-Host "== 2. Creando venv e instalando dependencias ==" -ForegroundColor Cyan
Set-Location $AppDir
py -3 -m venv .venv
& "$AppDir\.venv\Scripts\python.exe" -m pip install --upgrade pip
& "$AppDir\.venv\Scripts\python.exe" -m pip install -r requirements.txt

Write-Host "== 3. Checando .env ==" -ForegroundColor Cyan
if (-not (Test-Path "$AppDir\.env")) {
  throw "Falta $AppDir\.env — copia .env.example a .env, rellena CLIENT_ID, CLIENT_SECRET y REDIRECT_URI HTTPS."
}
if (-not (Select-String -Path "$AppDir\.env" -Pattern "ts.net" -Quiet)) {
  Write-Warning "El REDIRECT_URI del .env no parece el HTTPS de Tailscale. Revísalo."
}

Write-Host "== 4. Smoke local (arranca 6s y prueba /) ==" -ForegroundColor Cyan
$p = Start-Process -FilePath "$AppDir\.venv\Scripts\python.exe" `
  -ArgumentList "-m","uvicorn","main:app","--host","127.0.0.1","--port","$Port" `
  -PassThru -WindowStyle Hidden
Start-Sleep -Seconds 6
try {
  $code = (Invoke-WebRequest -Uri "http://127.0.0.1:$Port/" -UseBasicParsing).StatusCode
  Write-Host "   GET / -> $code" -ForegroundColor Green
} catch { Write-Warning "Smoke falló: $_" }
Stop-Process -Id $p.Id -Force

Write-Host "== 5. Tailscale serve (HTTPS al tailnet) ==" -ForegroundColor Cyan
Write-Host "   Requiere HTTPS habilitado en la tailnet (admin console > DNS > HTTPS Certificates)."
tailscale serve --bg https / "http://127.0.0.1:$Port"
tailscale serve status

Write-Host "== 6. Servicio NSSM 'Vitals' ==" -ForegroundColor Cyan
$nssm = (Get-Command nssm -ErrorAction SilentlyContinue)
if (-not $nssm) { throw "nssm no está en PATH. Instálalo (choco install nssm) o usa la ruta completa." }
# Si ya existe, lo recreamos limpio
cmd /c "nssm stop Vitals 2>nul"
cmd /c "nssm remove Vitals confirm 2>nul"
nssm install Vitals "$AppDir\.venv\Scripts\python.exe" "-m uvicorn main:app --host 127.0.0.1 --port $Port"
nssm set Vitals AppDirectory $AppDir
nssm set Vitals Start SERVICE_AUTO_START
nssm set Vitals AppStdout "$AppDir\data\vitals_service.log"
nssm set Vitals AppStderr "$AppDir\data\vitals_service.log"
nssm start Vitals
Start-Sleep -Seconds 5
nssm status Vitals

Write-Host "`n== LISTO ==" -ForegroundColor Green
Write-Host "Abre https://your-box.your-tailnet.ts.net desde tu laptop/cel (Tailscale activo)."
Write-Host "Primera vez: entra a /auth/login para autenticar con Google."
Write-Host "ANTES de /auth/login: registra el redirect HTTPS en Google Cloud Console (paso manual)."
