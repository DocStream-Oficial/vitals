// household.js — Switcher de usuarios (Fase 8D, paso D3, household).
//
// Depende de globals del <script> inline: t() (i18n), escHtml(). Se carga
// vía <script src> DESPUÉS de esos globals.
//
// Modelo: GET /api/users -> {users:[{id,name,color}], active: uid|null}.
// active=null significa "instalación sin household" (data/users/ no existe
// todavía) -> la sección entera se oculta, cero cambio visual para el caso
// de hoy (single-user, la inmensa mayoría de instalaciones existentes).
//
// Selección: al elegir un usuario en la lista, se guarda la cookie
// vitals_user (el backend ya la lee en cascada header>cookie>único>default)
// y se recarga la página para que TODA la UI (perfil, journal, coach, etc.)
// se re-renderice con los datos de ese usuario.

var HOUSEHOLD_USERS = [];
var HOUSEHOLD_ACTIVE = null;

function fetchHouseholdState() {
  fetch('/api/users')
    .then(function(r) { return r.json(); })
    .then(function(data) {
      HOUSEHOLD_USERS = data.users || [];
      HOUSEHOLD_ACTIVE = data.active;
      renderHouseholdSection();
    })
    .catch(function() {
      // Sin conexión: deja la sección oculta si no había datos previos.
    });
}

function renderHouseholdSection() {
  var section = document.getElementById('masHouseholdSection');
  var list = document.getElementById('masHouseholdList');
  if (!section || !list) return;

  // Sin household (active=null, instalación single-user de siempre) ->
  // sección oculta por completo, cero cambio visual.
  if (HOUSEHOLD_ACTIVE === null || !HOUSEHOLD_USERS.length) {
    section.style.display = 'none';
    return;
  }

  section.style.display = '';
  var html = HOUSEHOLD_USERS.map(function(u) {
    var initial = (u.name || '?').trim().charAt(0).toUpperCase();
    var isActive = u.id === HOUSEHOLD_ACTIVE;
    return '<div class="household-row" onclick="householdSwitchTo(\'' + u.id.replace(/'/g, "\\'") + '\')">'
      + '<div class="household-avatar" style="background:' + escHtml(u.color || '#0A84FF') + '">' + escHtml(initial) + '</div>'
      + '<div class="household-name">' + escHtml(u.name) + '</div>'
      + (isActive ? '<span class="household-active-badge" data-i18n="household_active_badge">' + escHtml(t('household_active_badge')) + '</span>' : '')
      + '<span class="household-del" onclick="event.stopPropagation();householdDelete(\'' + u.id.replace(/'/g, "\\'") + '\')">'
      + '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2m2 0v14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2V6h12z"/></svg>'
      + '</span>'
      + '</div>';
  }).join('');
  list.innerHTML = html;
}

function householdSwitchTo(uid) {
  if (uid === HOUSEHOLD_ACTIVE) return;
  document.cookie = 'vitals_user=' + encodeURIComponent(uid) + ';path=/;max-age=31536000;samesite=lax';
  window.location.reload();
}

function householdDelete(uid) {
  // Confirmación simple (patrón nativo, consistente con el resto de la app
  // para acciones destructivas de un solo paso) — el backend además exige
  // confirm=true en la querystring como segunda capa de protección.
  if (!window.confirm(t('household_delete_confirm'))) return;
  fetch('/api/users/' + encodeURIComponent(uid) + '?confirm=true', { method: 'DELETE' })
    .then(function() { return fetchHouseholdState(); })
    .catch(function() {});
}

// ── Modal de alta ────────────────────────────────────────────────────────────

function openHouseholdAddModal() {
  var modal = document.getElementById('householdAddModal');
  var input = document.getElementById('householdAddInput');
  var err = document.getElementById('householdAddErr');
  if (!modal) return;
  if (input) input.value = '';
  if (err) { err.style.display = 'none'; err.textContent = ''; }
  modal.classList.remove('hidden');
  if (input) setTimeout(function() { input.focus(); }, 50);
}

function closeHouseholdAddModal() {
  var modal = document.getElementById('householdAddModal');
  if (modal) modal.classList.add('hidden');
}

function householdSubmitAdd() {
  var input = document.getElementById('householdAddInput');
  var err = document.getElementById('householdAddErr');
  var name = input ? input.value.trim() : '';
  if (!name) return;

  fetch('/api/users', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name: name })
  })
    .then(function(r) { return r.json().then(function(body) { return { status: r.status, body: body }; }); })
    .then(function(res) {
      if (res.status !== 200 || (res.body && res.body.status === 'error')) {
        if (err) {
          err.textContent = (res.body && res.body.message) || t('err_net');
          err.style.display = '';
        }
        return;
      }
      closeHouseholdAddModal();
      // El primer usuario creado en una instalación fresh activa household
      // para todo el sistema — recargar para que el resto de la UI (que ya
      // pudo haber cacheado "sin household") se re-sincronice de cero.
      window.location.reload();
    })
    .catch(function() {
      if (err) { err.textContent = t('err_net'); err.style.display = ''; }
    });
}

// ── hook de inicialización ───────────────────────────────────────────────────
// La sección de household vive en Más -> se carga la primera vez que el
// usuario entra a esa tab (mismo patrón que journal.js/healthspan.js).
var _householdMasLoaded = false;
if (typeof goTab === 'function') {
  var _householdOrigGoTab = goTab;
  goTab = function(screenId) {
    _householdOrigGoTab(screenId);
    if (screenId === 'screenMas' && !_householdMasLoaded) {
      _householdMasLoaded = true;
      fetchHouseholdState();
    }
  };
}
