// api_keys.js — API pública de solo lectura (Roadmap P2, F10, paso 3).
//
// Depende de globals definidos en el <script> inline del template: t() (i18n),
// escHtml(), goTab. Se carga vía <script src> DESPUÉS de esos globals (ver
// templates/vitals_ios.html, cierre de </body>).
//
// Estado propio del módulo:
//   API_KEYS_LIST — [{id,label,created,last_used,revoked}] del último GET /api/keys
//   _apiKeysNewRawValue — la clave CRUDA recién generada, solo en memoria del
//                         cliente para el botón "Copiar" del modal (nunca se
//                         vuelve a pedir al servidor: no se puede recuperar).

var API_KEYS_LIST = [];
var _apiKeysNewRawValue = '';

function apiKeysFetchAndRender() {
  var wrap = document.getElementById('apiKeysList');
  if (!wrap) return;
  fetch('/api/keys')
    .then(function(r) { return r.json(); })
    .then(function(data) {
      API_KEYS_LIST = (data && data.keys) || [];
      renderApiKeysList();
    })
    .catch(function() {
      // Sin conexión: deja el estado anterior (mismo patrón best-effort que
      // journal.js/household.js — no hay retry toast aquí, la sección no es
      // crítica para el resto de la app).
    });
}

function _apiKeyFmtDate(iso) {
  if (!iso) return '';
  try {
    var d = new Date(iso);
    return d.getFullYear() + '-' + ('0' + (d.getMonth() + 1)).slice(-2) + '-' + ('0' + d.getDate()).slice(-2);
  } catch (e) {
    return iso;
  }
}

function renderApiKeysList() {
  var wrap = document.getElementById('apiKeysList');
  var genBtn = document.getElementById('apiKeysGenBtn');
  if (!wrap) return;

  if (!API_KEYS_LIST.length) {
    wrap.innerHTML = '<div class="api-key-empty">' + escHtml(t('mas_api_empty')) + '</div>';
  } else {
    wrap.innerHTML = API_KEYS_LIST.map(function(k) {
      var lastUsed = k.last_used ? _apiKeyFmtDate(k.last_used) : t('mas_api_last_used_never');
      var metaLine = t('mas_api_created_lbl') + ' ' + _apiKeyFmtDate(k.created) +
        ' · ' + t('mas_api_last_used_lbl') + ': ' + lastUsed;
      var label = k.label ? escHtml(k.label) : ('#' + escHtml(String(k.id || '').slice(0, 8)));
      var actionHtml = k.revoked
        ? '<span class="api-key-revoked-pill">' + escHtml(t('mas_api_revoked_lbl')) + '</span>'
        : '<span class="api-key-revoke-btn" onclick="apiKeysRevoke(\'' + escHtml(k.id).replace(/'/g, "\\'") + '\')">'
          + escHtml(t('mas_api_revoke_btn')) + '</span>';
      return '<div class="api-key-row' + (k.revoked ? ' revoked' : '') + '">'
        + '<div><div class="api-key-label">' + label + '</div>'
        + '<div class="api-key-meta">' + escHtml(metaLine) + '</div></div>'
        + actionHtml
        + '</div>';
    }).join('');
  }

  // Tope de 10 claves (criterio F10 #2): deshabilita el botón "Generar" en
  // vez de dejar que el usuario choque con el 422 sin explicación previa.
  var activeCount = API_KEYS_LIST.filter(function(k) { return !k.revoked; }).length;
  if (genBtn) {
    var atLimit = API_KEYS_LIST.length >= 10;
    genBtn.style.opacity = atLimit ? '.4' : '';
    genBtn.style.pointerEvents = atLimit ? 'none' : '';
  }
}

function apiKeysGenerate() {
  fetch('/api/keys', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ label: '' })
  })
    .then(function(r) { return r.json().then(function(body) { return { status: r.status, body: body }; }); })
    .then(function(res) {
      if (res.status !== 200 || (res.body && res.body.status === 'error')) {
        alert((res.body && res.body.message) || t('mas_api_limit_reached'));
        return;
      }
      _apiKeysNewRawValue = res.body.key;
      apiKeysOpenNewModal(res.body.key);
      apiKeysFetchAndRender();
    })
    .catch(function() {
      if (typeof showRetryToast === 'function') {
        showRetryToast(function() { apiKeysGenerate(); });
      }
    });
}

function apiKeysRevoke(keyId) {
  if (!window.confirm(t('mas_api_confirm_revoke'))) return;
  fetch('/api/keys/' + encodeURIComponent(keyId), { method: 'DELETE' })
    .then(function(r) { return r.json(); })
    .then(function() { apiKeysFetchAndRender(); })
    .catch(function() { apiKeysFetchAndRender(); });
}

// ── Modal de clave nueva ─────────────────────────────────────────────────────

function apiKeysOpenNewModal(rawKey) {
  var modal = document.getElementById('apiKeyNewModal');
  var valueEl = document.getElementById('apiKeyNewValue');
  if (!modal) return;
  if (valueEl) valueEl.textContent = rawKey;
  modal.classList.remove('hidden');
}

function apiKeysCloseNewModal() {
  var modal = document.getElementById('apiKeyNewModal');
  if (modal) modal.classList.add('hidden');
}

function apiKeysCopyNewValue() {
  if (!_apiKeysNewRawValue) return;
  try {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(_apiKeysNewRawValue);
    } else {
      var valueEl = document.getElementById('apiKeyNewValue');
      if (valueEl) {
        var range = document.createRange();
        range.selectNode(valueEl);
        window.getSelection().removeAllRanges();
        window.getSelection().addRange(range);
        document.execCommand('copy');
        window.getSelection().removeAllRanges();
      }
    }
  } catch (e) { /* sin clipboard API: el usuario copia manualmente el texto visible */ }
}

// ── hook de inicialización ───────────────────────────────────────────────────
// La sección "API" vive en Más -> se carga la primera vez que el usuario
// entra a esa tab (mismo patrón que journal.js/household.js).
var _apiKeysMasLoaded = false;
if (typeof goTab === 'function') {
  var _apiKeysOrigGoTab = goTab;
  goTab = function(screenId) {
    _apiKeysOrigGoTab(screenId);
    if (screenId === 'screenMas' && !_apiKeysMasLoaded) {
      _apiKeysMasLoaded = true;
      apiKeysFetchAndRender();
    }
  };
}
