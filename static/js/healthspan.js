// healthspan.js — Healthspan / Pace of Aging (Fase 8D, paso D2).
//
// Depende de globals definidos en el <script> inline del template: t() (i18n),
// escHtml(), buildDetailChart(), attachChartInteraction()/detachChartInteraction()
// (chart-interact.js), A (paleta de acentos). Se carga vía <script src> DESPUÉS
// de esos globals (ver templates/vitals_ios.html, cierre de </body>).

var HEALTHSPAN_DATA = null;

function fetchAndRenderHealthspan() {
  fetch('/api/healthspan')
    .then(function(r) { return r.json(); })
    .then(function(data) {
      HEALTHSPAN_DATA = data;
      renderHealthspanCard();
    })
    .catch(function() {
      if (typeof showRetryToast === 'function') {
        showRetryToast(function() { fetchAndRenderHealthspan(); });
      }
    });
}

function _healthspanPaceNote(pace) {
  if (pace == null) return '';
  if (pace < 0.9) return t('healthspan_pace_note_slow');
  if (pace > 1.1) return t('healthspan_pace_note_fast');
  return t('healthspan_pace_note_pace');
}

function renderHealthspanCard() {
  var card = document.getElementById('tendHealthspanCard');
  if (!card) return;
  var data = HEALTHSPAN_DATA;

  // Roadmap P1 paso 1: "pace of aging" queda como término técnico secundario
  // (plain-language ya vive en healthspan_sub) — helper ⓘ, nunca se elimina.
  var subTechEl = document.getElementById('healthspanSubTech');
  if (subTechEl && typeof techDetail === 'function') {
    subTechEl.innerHTML = techDetail('healthspan', escHtml(t('healthspan_sub_tech')));
  }

  if (!data || !data.available) {
    card.style.display = '';
    document.getElementById('healthspanPaceRow').style.display = 'none';
    document.getElementById('healthspanPaceNote').style.display = 'none';
    document.getElementById('healthspanChartSvg').style.display = 'none';
    document.getElementById('healthspanDeltaRow').style.display = 'none';
    document.getElementById('healthspanEmpty').style.display = '';
    return;
  }

  card.style.display = '';
  document.getElementById('healthspanPaceRow').style.display = '';
  document.getElementById('healthspanPaceNote').style.display = '';
  document.getElementById('healthspanChartSvg').style.display = '';
  document.getElementById('healthspanDeltaRow').style.display = '';
  document.getElementById('healthspanEmpty').style.display = 'none';

  var pace = data.pace;
  document.getElementById('healthspanPaceVal').textContent = (pace != null) ? pace.toFixed(2) : '—';
  document.getElementById('healthspanPaceNote').textContent = _healthspanPaceNote(pace);

  var series = data.series || [];
  var bodyAges = series.map(function(pt) { return pt.body_age; });
  var chartWrap = document.getElementById('healthspanChartSvg');
  if (bodyAges.length && typeof buildDetailChart === 'function') {
    var minVal = Math.min.apply(null, bodyAges) - 2;
    var maxVal = Math.max.apply(null, bodyAges) + 2;
    var yTicks = [Math.round(minVal), Math.round((minVal + maxVal) / 2), Math.round(maxVal)];
    chartWrap.innerHTML = buildDetailChart(bodyAges, A.purpLite || '#9D8DF5', minVal, maxVal, yTicks);

    if (typeof attachChartInteraction === 'function') {
      try {
        var dates = series.map(function(pt) { return pt.date; });
        attachChartInteraction(chartWrap, {
          values: bodyAges, dates: dates, color: A.purpLite || '#9D8DF5',
          minVal: minVal, maxVal: maxVal, unit: '',
        });
      } catch (e) { /* progressive enhancement: el chart estático sigue funcionando */ }
    }
  } else {
    chartWrap.innerHTML = '';
  }

  var deltaEl = document.getElementById('healthspanDeltaRow');
  if (data.delta_quarter != null) {
    var d = data.delta_quarter;
    var cls = d < 0 ? 'good' : (d > 0 ? 'bad' : '');
    var sign = d > 0 ? '+' : '';
    var txt = t('healthspan_delta_quarter').replace('{delta}', sign + d);
    deltaEl.innerHTML = cls ? txt.replace(sign + d, '<span class="' + cls + '">' + sign + d + '</span>') : txt;
  } else {
    deltaEl.textContent = '';
  }
}

// ── hook de inicialización ───────────────────────────────────────────────────
// Healthspan vive en Tendencias -> se carga la primera vez que el usuario
// entra a esa tab (mismo patrón que journal.js: goTab hookeado en cadena).
var _healthspanTendLoaded = false;
if (typeof goTab === 'function') {
  var _healthspanOrigGoTab = goTab;
  goTab = function(screenId) {
    _healthspanOrigGoTab(screenId);
    if (screenId === 'screenTend' && !_healthspanTendLoaded) {
      _healthspanTendLoaded = true;
      fetchAndRenderHealthspan();
    }
  };
}
