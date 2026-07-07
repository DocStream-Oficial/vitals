/* Vitals PWA service worker — network-first con fallback de shell offline.
   No cachea datos de salud agresivamente: siempre intenta la red primero y
   solo cae al cache (shell + íconos) cuando no hay conexión.

   Fase 8C (paso C5, AAA feel): además de '/', ahora cachea también
   /api/data — la respuesta OK de CADA fetch se escribe a caché, y si la red
   falla, el fallback inyecta el header X-From-Cache para que el frontend
   pueda mostrar un banner "sin conexión — datos del <fecha>" con la fecha
   real del dataset cacheado (dataset.summary.updated / days[-1].date).

   Nota de privacidad (roadmap, riesgo #5): los datos de salud cacheados
   viven ÚNICAMENTE en el Cache Storage del navegador/dispositivo del propio
   usuario (no se replican a ningún servidor de terceros) — mismo modelo de
   confianza que localStorage/IndexedDB de cualquier PWA. */
const CACHE = 'vitals-shell-v2';
const SHELL = ['/', '/static/icons/icon-192.png', '/static/icons/icon-512.png'];

// Rutas de datos que además de shell, cacheamos por respuesta (no solo al
// instalar) — se sobreescriben en cada fetch exitoso.
const DATA_ROUTES = ['/api/data'];

self.addEventListener('install', (e) => {
  self.skipWaiting();
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL).catch(() => {})));
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

function _isDataRoute(url) {
  return DATA_ROUTES.some((p) => url.pathname === p);
}

self.addEventListener('fetch', (e) => {
  const req = e.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  const isData = _isDataRoute(url);

  e.respondWith(
    fetch(req)
      .then((res) => {
        // Guardar copia de navegaciones, imágenes/estáticos, Y de las rutas
        // de datos declaradas arriba — cada respuesta OK reemplaza la
        // anterior en caché (siempre el dataset más reciente disponible).
        if (res && res.ok && (req.mode === 'navigate' || req.destination === 'image' || req.url.includes('/static/') || isData)) {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put(req, copy)).catch(() => {});
        }
        return res;
      })
      .catch(() =>
        caches.match(req).then((m) => {
          if (m) {
            // Fallback servido desde caché: inyectamos X-From-Cache=1 para
            // que el frontend sepa que estos datos NO vienen de la red viva
            // (banner offline con la fecha del dataset cacheado).
            const headers = new Headers(m.headers);
            headers.set('X-From-Cache', '1');
            return m.blob().then((body) => new Response(body, {
              status: m.status, statusText: m.statusText, headers,
            }));
          }
          return req.mode === 'navigate' ? caches.match('/') : undefined;
        })
      )
  );
});
