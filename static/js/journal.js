// journal.js — Diario de hábitos + Impacto de hábitos (Fase 8B, paso B5).
//
// Depende de globals definidos en el <script> inline del template: t() (i18n),
// escHtml(), A (paleta de acentos), days/DB. Se carga vía <script src> DESPUÉS
// de esos globals (ver templates/vitals_ios.html, cierre de </body>).
//
// Estado propio del módulo:
//   JOURNAL_CATALOG    — catálogo [{key,category,label,custom}] del último GET /api/journal
//   JOURNAL_ENTRY      — entry {key:bool} del día seleccionado actualmente (dentro del sheet)
//   journalDayOffset   — 0 = hoy, 1 = ayer (selector dentro del sheet de detalle)
//   JOURNAL_TODAY_ENTRY — entry de HOY, independiente del selector — alimenta
//                         el resumen colapsado de la card "Diario" en Hoy
//                         (roadmap P0 paso 3: card resumen + tap abre sheet).

var JOURNAL_CATALOG = [];
var JOURNAL_ENTRY = {};
var journalDayOffset = 0;
var JOURNAL_WEEK_ENTRIES = {}; // cache local de entries de los últimos 7 días (para el contador)
var JOURNAL_TODAY_ENTRY = {}; // cache de la entry de HOY (independiente de journalDayOffset)
                               // — alimenta el resumen colapsado de la card (roadmap P0 paso 3),
                               // que SIEMPRE debe mostrar los chips marcados de hoy sin importar
                               // qué día esté seleccionado dentro del sheet de detalle.
var JOURNAL_SUMMARY_MAX_CHIPS = 6;

function _journalDateForOffset(offset) {
  var d = new Date();
  d.setDate(d.getDate() - offset);
  var y = d.getFullYear(), m = ('0' + (d.getMonth() + 1)).slice(-2), day = ('0' + d.getDate()).slice(-2);
  return y + '-' + m + '-' + day;
}

function _journalCategoryOrder() {
  return ['supplements', 'consumption', 'recovery_mind', 'sleep_routine', 'context', 'custom'];
}

// Carga catálogo + entry del día seleccionado, luego pinta el sheet de detalle
// (si está abierto) y el resumen colapsado de la card (roadmap P0 paso 3).
// El contador semanal viene YA calculado por el backend en la misma respuesta
// (campo week_count: días con entry en la semana ISO lun-dom actual) — cero
// requests extra.
function fetchJournalState() {
  var date = _journalDateForOffset(journalDayOffset);
  fetch('/api/journal?date=' + encodeURIComponent(date))
    .then(function(r) { return r.json(); })
    .then(function(data) {
      JOURNAL_CATALOG = data.catalog || [];
      JOURNAL_ENTRY = data.entry || {};
      if (journalDayOffset === 0) JOURNAL_TODAY_ENTRY = JOURNAL_ENTRY;
      renderJournalCard();
      renderJournalSummary();
      _setJournalWeekCount(data.week_count);
    })
    .catch(function() {
      // Sin conexión: deja el estado anterior. Si es la carga INICIAL (catálogo
      // aún vacío), ofrece reintentar en vez de dejar la card sin explicación
      // (C2, Fase 8C).
      if (!JOURNAL_CATALOG.length && typeof showRetryToast === 'function') {
        showRetryToast(function() { fetchJournalState(); });
      }
    });
  // El resumen de la card SIEMPRE es de "hoy" — si el sheet está mostrando
  // "ayer" (journalDayOffset===1), la respuesta de arriba no trae la entry de
  // hoy, así que se pide aparte (ligero, mismo endpoint con date=hoy).
  if (journalDayOffset !== 0) {
    fetch('/api/journal?date=' + encodeURIComponent(_journalDateForOffset(0)))
      .then(function(r) { return r.json(); })
      .then(function(data) {
        JOURNAL_TODAY_ENTRY = data.entry || {};
        renderJournalSummary();
      })
      .catch(function() {});
  }
}

function _setJournalWeekCount(n) {
  n = (typeof n === 'number' && n >= 0) ? n : 0;
  var txt = t('journal_week_count').replace('{n}', n);
  var el1 = document.getElementById('journalWeekCount');
  var el2 = document.getElementById('journalWeekCountInline');
  if (el1) el1.textContent = txt;
  if (el2) el2.textContent = n > 0 ? txt : '';
}

// Refresca SOLO el contador semanal (tras un PUT): un único GET ligero.
function _fetchJournalWeekCount() {
  fetch('/api/journal?date=' + encodeURIComponent(_journalDateForOffset(0)))
    .then(function(r) { return r.json(); })
    .then(function(d) { _setJournalWeekCount(d.week_count); })
    .catch(function() {});
}

function journalSelectDay(offset) {
  journalDayOffset = offset;
  document.querySelectorAll('.journal-daysel-btn').forEach(function(el) {
    el.classList.toggle('active', String(offset) === el.getAttribute('data-off'));
  });
  fetchJournalState();
}

// ── Resumen colapsado (roadmap P0 paso 3) ───────────────────────────────────
// Card "Diario" en Hoy: ≤1 línea con los chips marcados HOY (máx
// JOURNAL_SUMMARY_MAX_CHIPS + "+n"), o un hint si no hay ninguno marcado.
// Independiente de journalDayOffset (ese solo aplica al sheet de detalle).
function renderJournalSummary() {
  var line = document.getElementById('journalSummaryLine');
  if (!line) return;

  if (!JOURNAL_CATALOG.length) {
    line.innerHTML = '';
    return;
  }

  var markedLabels = JOURNAL_CATALOG
    .filter(function(h) { return !!JOURNAL_TODAY_ENTRY[h.key]; })
    .map(function(h) { return h.label; });

  if (!markedLabels.length) {
    line.innerHTML = '<span class="journal-summary-hint">' + escHtml(t('journal_hint_empty')) + '</span>';
    return;
  }

  var shown = markedLabels.slice(0, JOURNAL_SUMMARY_MAX_CHIPS);
  var extra = markedLabels.length - shown.length;
  var html = shown.map(function(lbl) {
    return '<span class="journal-summary-chip">' + escHtml(lbl) + '</span>';
  }).join('');
  if (extra > 0) {
    html += '<span class="journal-summary-more">' + escHtml(t('journal_more_n').replace('{n}', extra)) + '</span>';
  }
  line.innerHTML = html;
}

// ── Sheet de detalle (roadmap P0 paso 3) ────────────────────────────────────
// Abre/cierra el modal con el contenido íntegro (selector Hoy/Ayer, 6
// categorías, toggle, agregar hábito) — mismo patrón que #ecgViewerModal /
// #reportModal. Al cerrar, el resumen ya está actualizado (cada toggle re-
// pinta renderJournalSummary si el día activo del sheet es "hoy").
function openJournalDetail() {
  var modal = document.getElementById('journalDetailModal');
  if (!modal) return;
  modal.classList.remove('hidden');
}

function closeJournalDetail() {
  var modal = document.getElementById('journalDetailModal');
  if (modal) modal.classList.add('hidden');
}

function renderJournalCard() {
  var wrap = document.getElementById('journalChipsWrap');
  if (!wrap) return;

  if (!JOURNAL_CATALOG.length) {
    wrap.innerHTML = '';
    return;
  }

  var byCat = {};
  JOURNAL_CATALOG.forEach(function(h) {
    if (!byCat[h.category]) byCat[h.category] = [];
    byCat[h.category].push(h);
  });

  var html = '';
  _journalCategoryOrder().forEach(function(cat) {
    var items = byCat[cat];
    if (!items || !items.length) return;
    html += '<div class="journal-cat-group">';
    html += '<div class="journal-cat-lbl">' + escHtml(t('journal_cat_' + cat)) + '</div>';
    html += '<div class="journal-chips">';
    items.forEach(function(h) {
      // Roadmap P2 (F9, paso 9): los 3 hábitos con `quantity` (alcohol/
      // meditation/breathwork) pintan un stepper numérico en vez del chip
      // toggle — el resto (~30 hábitos) sin ningún cambio visual.
      if (h.quantity) {
        html += _renderJournalQtyStepper(h);
      } else {
        var on = !!JOURNAL_ENTRY[h.key];
        html += '<div class="journal-chip' + (on ? ' on' : '') + (h.custom ? ' custom' : '') +
          '" data-key="' + escHtml(h.key) + '" onclick="journalToggleHabit(\'' + h.key.replace(/'/g, "\\'") + '\')">' +
          escHtml(h.label) + '</div>';
      }
    });
    html += '</div></div>';
  });

  html += '<div class="journal-addbtn" onclick="openJournalAddModal()">' + escHtml(t('journal_add_custom')) + '</div>';

  wrap.innerHTML = html;
}

// Valor actual de un hábito cuantificable en la entry del día seleccionado —
// tolera legacy bool (True->1, False->0) para que el stepper muestre algo
// coherente incluso si el journal viejo tenía ese hábito en booleano.
function _journalQtyValue(key) {
  var v = JOURNAL_ENTRY[key];
  if (v === true) return 1;
  if (v === false || v == null) return 0;
  var n = Number(v);
  return isNaN(n) ? 0 : n;
}

function _renderJournalQtyStepper(h) {
  var val = _journalQtyValue(h.key);
  var unit = (h.quantity && h.quantity.unit) || '';
  var keyEsc = escHtml(h.key).replace(/'/g, "\\'");
  return '<div class="journal-qty' + (val > 0 ? ' active' : '') + '" data-key="' + escHtml(h.key) + '">'
    + '<span class="journal-qty-lbl">' + escHtml(h.label) + '</span>'
    + '<div class="journal-qty-ctrl">'
    + '<span class="journal-qty-btn" onclick="journalStepQuantity(\'' + keyEsc + '\', -1)">−</span>'
    + '<span class="journal-qty-val" id="journalQtyVal_' + escHtml(h.key) + '">' + val + '</span>'
    + '<span class="journal-qty-unit">' + escHtml(unit) + '</span>'
    + '<span class="journal-qty-btn" onclick="journalStepQuantity(\'' + keyEsc + '\', 1)">+</span>'
    + '</div></div>';
}

// Step +/-1 sobre el valor actual (optimista, mismo patrón que
// journalToggleHabit) — el paso es 1 unidad (copa o minuto) por tap; para
// minutos, mantener presionado repite el tap nativo del navegador, suficiente
// para un input de bajo volumen como este (sin inventar long-press custom).
function journalStepQuantity(key, delta) {
  var current = _journalQtyValue(key);
  var next = current + delta;
  if (next < 0) next = 0;
  _journalCommitQuantity(key, next);
}

function _journalCommitQuantity(key, value) {
  JOURNAL_ENTRY[key] = value;
  renderJournalCard();
  if (journalDayOffset === 0) { JOURNAL_TODAY_ENTRY = JOURNAL_ENTRY; renderJournalSummary(); }

  var date = _journalDateForOffset(journalDayOffset);
  var habits = {};
  habits[key] = value;

  fetch('/api/journal/' + date, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ habits: habits })
  })
    .then(function(r) { return r.json(); })
    .then(function(data) {
      if (data && data.status === 'ok' && data.entry) {
        JOURNAL_ENTRY = data.entry;  // valor clamped que devolvió el backend
        renderJournalCard();
        if (journalDayOffset === 0) { JOURNAL_TODAY_ENTRY = JOURNAL_ENTRY; renderJournalSummary(); }
        _fetchJournalWeekCount();
      }
    })
    .catch(function() {
      // Sin conexión: no revertimos a un valor previo específico (a
      // diferencia del toggle binario, no guardamos "wasValue" aquí) — el
      // próximo fetchJournalState() al reabrir el sheet re-sincroniza desde
      // el servidor, mismo principio best-effort que el resto del módulo.
    });
}

// Toggle optimista: pinta el chip de inmediato, PUT en background (merge en
// el backend), re-sincroniza contador semanal. Si falla la red, revierte.
// Si el día activo del sheet es "hoy", el resumen colapsado de la card
// (roadmap P0 paso 3) se mantiene en sync con el mismo objeto JOURNAL_ENTRY.
function journalToggleHabit(key) {
  var wasOn = !!JOURNAL_ENTRY[key];
  JOURNAL_ENTRY[key] = !wasOn;
  renderJournalCard();
  if (journalDayOffset === 0) { JOURNAL_TODAY_ENTRY = JOURNAL_ENTRY; renderJournalSummary(); }

  var date = _journalDateForOffset(journalDayOffset);
  var habits = {};
  habits[key] = JOURNAL_ENTRY[key];

  fetch('/api/journal/' + date, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ habits: habits })
  })
    .then(function(r) { return r.json(); })
    .then(function(data) {
      if (data && data.status === 'ok' && data.entry) {
        JOURNAL_ENTRY = data.entry;
        renderJournalCard();
        if (journalDayOffset === 0) { JOURNAL_TODAY_ENTRY = JOURNAL_ENTRY; renderJournalSummary(); }
        _fetchJournalWeekCount();
      }
    })
    .catch(function() {
      // Sin conexión: revierte el toggle optimista.
      JOURNAL_ENTRY[key] = wasOn;
      renderJournalCard();
      if (journalDayOffset === 0) { JOURNAL_TODAY_ENTRY = JOURNAL_ENTRY; renderJournalSummary(); }
    });
}

// ── Modal de alta de hábito custom ──────────────────────────────────────────

function openJournalAddModal() {
  var modal = document.getElementById('journalAddModal');
  var input = document.getElementById('journalAddInput');
  var err = document.getElementById('journalAddErr');
  if (!modal) return;
  if (input) input.value = '';
  if (err) { err.style.display = 'none'; err.textContent = ''; }
  modal.classList.remove('hidden');
  if (input) setTimeout(function() { input.focus(); }, 50);
}

function closeJournalAddModal() {
  var modal = document.getElementById('journalAddModal');
  if (modal) modal.classList.add('hidden');
}

function journalSubmitCustom() {
  var input = document.getElementById('journalAddInput');
  var err = document.getElementById('journalAddErr');
  var label = input ? input.value.trim() : '';
  if (!label) return;

  fetch('/api/journal/custom', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ label: label })
  })
    .then(function(r) { return r.json().then(function(body) { return { status: r.status, body: body }; }); })
    .then(function(res) {
      if (res.status !== 200 || (res.body && res.body.status === 'error')) {
        if (err) {
          // Nota i18n: res.body.message viene del backend en español fijo
          // (mismo patrón que TODO main.py — mensajes de validación de error
          // no localizados en el repo, ver informe del implementador). Con
          // fallback SÍ localizado (err_net) si el backend no manda message.
          err.textContent = (res.body && res.body.message) || t('err_net');
          err.style.display = '';
        }
        return;
      }
      closeJournalAddModal();
      fetchJournalState();
    })
    .catch(function() {
      if (err) { err.textContent = t('err_net'); err.style.display = ''; }
    });
}

// ── Impacto de hábitos (Tendencias) ─────────────────────────────────────────

function fetchAndRenderJournalImpact() {
  var container = document.getElementById('tendJournalImpact');
  if (!container) return;
  fetch('/api/journal/impact')
    .then(function(r) { return r.json(); })
    .then(function(findings) { renderJournalImpact(findings || []); })
    .catch(function() {
      // Sin conexión: deja lo que había. Ofrece reintentar (C2, Fase 8C).
      if (typeof showRetryToast === 'function') {
        showRetryToast(function() { fetchAndRenderJournalImpact(); });
      }
    });
}

// Regla de "mejora vs empeora" (verde/rojo) — punto dudoso, documentado
// explícitamente en el informe final del implementador: `delta` viene YA
// calculado por el backend como media(outcome|hábito=sí) − media(outcome|
// hábito=no). Los 3 outcomes evaluados (recovery, hrv, sleep_perf) son
// SIEMPRE "más alto = mejor" fisiológicamente, así que la UI colorea
// delta>0 = verde (mejora), delta<0 = rojo (empeora) de forma UNIFORME para
// los 3, SIN voltear el signo según si el hábito "suena bueno o malo"
// (alcohol, meditación, etc.). El color describe el efecto sobre el
// outcome, no un juicio moral sobre el hábito — el headline ya deja explícito
// si el efecto es "recuperación más alta" o "más baja".
function renderJournalImpact(findings) {
  var container = document.getElementById('tendJournalImpact');
  if (!container) return;

  if (!findings || !findings.length) {
    container.innerHTML = '<div class="jimpact-empty">' + escHtml(t('impact_empty')) + '</div>';
    return;
  }

  // Roadmap P1 paso 1: unidad explícita por outcome del finding (recovery/
  // sleep_perf -> "pts", hrv -> "ms" — mapeado en cliente, ver roadmap paso 1).
  // El badge "significativo" se reemplaza por copy humano; ρ/n/p viven tras
  // el helper ⓘ (nunca se elimina el dato técnico, nunca se muestra por default).
  var UNIT_BY_OUTCOME = {
    recovery: 'impact_unit_recovery',
    hrv: 'impact_unit_hrv',
    sleep_perf: 'impact_unit_sleep_perf',
  };

  var html = findings.map(function(f) {
    var good = f.delta != null && f.delta > 0;
    var deltaClass = f.delta == null ? '' : (good ? 'good' : 'bad');
    var unitKey = UNIT_BY_OUTCOME[f.outcome];
    var unitTxt = unitKey ? ' ' + t(unitKey) : '';
    var deltaTxt = f.delta == null ? '—' : ((f.delta > 0 ? '+' : '') + f.delta + unitTxt);
    var statLine = t('impact_stat_line')
      .replace('{rho}', f.rho != null ? f.rho.toFixed(2) : '—')
      .replace('{n}', (f.n_yes != null && f.n_no != null) ? (f.n_yes + f.n_no) : '—')
      .replace('{p}', f.p != null ? f.p.toFixed(3) : '—');

    return '<div class="jimpact-row">'
      + '<div class="jimpact-headline">' + escHtml(f.headline || '') + '</div>'
      + '<div class="jimpact-sub">'
      + '<span class="jimpact-delta ' + deltaClass + '">' + escHtml(deltaTxt) + '</span>'
      + (f.significant ? '<span class="jimpact-badge">' + escHtml(t('impact_significant_plain')) + '</span>' : '')
      + (typeof techDetail === 'function' ? techDetail('jimpact', escHtml(statLine)) : '')
      + '</div>'
      + '</div>';
  }).join('');

  container.innerHTML = html;
}

// ── Dosis-respuesta (Roadmap P2, F9, paso 9) ────────────────────────────────
// Findings de "¿la CANTIDAD del hábito importa?" — endpoint NUEVO y
// separado (/api/journal/dose-response, ver nota de desviación en main.py).
// Se muestran junto a los de Palancas/Impacto, con badge distintivo
// (criterio 20) para no confundirlos con el gate sí/no de arriba.
function fetchAndRenderJournalDoseResponse() {
  var container = document.getElementById('tendJournalDoseResponse');
  if (!container) return;
  fetch('/api/journal/dose-response')
    .then(function(r) { return r.json(); })
    .then(function(findings) { renderJournalDoseResponse(findings || []); })
    .catch(function() {
      if (typeof showRetryToast === 'function') {
        showRetryToast(function() { fetchAndRenderJournalDoseResponse(); });
      }
    });
}

function renderJournalDoseResponse(findings) {
  var container = document.getElementById('tendJournalDoseResponse');
  if (!container) return;

  // Sección AUSENTE si no hay hallazgos — no un placeholder vacío (mismo
  // principio que el arquetipo de sueño, criterio 13 de F8).
  if (!findings || !findings.length) {
    container.innerHTML = '';
    return;
  }

  var html = findings.map(function(f) {
    var statLine = t('impact_stat_line')
      .replace('{rho}', f.rho != null ? f.rho.toFixed(2) : '—')
      .replace('{n}', f.n != null ? f.n : '—')
      .replace('{p}', f.p != null ? f.p.toFixed(3) : '—');

    return '<div class="jimpact-row">'
      + '<div class="jimpact-headline">' + escHtml(f.headline || '') + '</div>'
      + '<div class="jimpact-sub">'
      + '<span class="jimpact-badge dose">' + escHtml(t('dose_response_badge')) + '</span>'
      + (typeof techDetail === 'function' ? techDetail('jimpact', escHtml(statLine)) : '')
      + '</div>'
      + '</div>';
  }).join('');

  container.innerHTML = '<div class="jimpact-section-lbl">' + escHtml(t('dose_response_section_lbl')) + '</div>' + html;
}

// ── hooks de inicialización ──────────────────────────────────────────────────
// La card "Diario" es de la tab Hoy (visible de entrada) -> carga inmediata.
fetchJournalState();

// Impacto de hábitos + dosis-respuesta + Informe (informe vive en
// static/js/report.js, cargado después de este archivo) son de la tab
// Tendencias -> se cargan la PRIMERA vez que el usuario entra a esa tab
// (patrón renderCoachTab/renderMas ya usado en el <script> inline principal
// para goTab).
var _journalTendLoaded = false;
var _journalOrigGoTab = goTab;
goTab = function(screenId) {
  _journalOrigGoTab(screenId);
  if (screenId === 'screenTend' && !_journalTendLoaded) {
    _journalTendLoaded = true;
    fetchAndRenderJournalDoseResponse();
    fetchAndRenderJournalImpact();
    if (typeof fetchAndRenderReportPreview === 'function') fetchAndRenderReportPreview();
  }
};
