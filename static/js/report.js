// report.js — Informe narrativo semanal/mensual (Fase 8B, paso B5/B6).
//
// Depende de globals del <script> inline: t() (i18n), escHtml(), fmtDateES()
// si está disponible. Cargado vía <script src> después de esos globals.

var reportPeriod = 'weekly'; // 'weekly' | 'monthly'
var REPORT_CACHE = { weekly: null, monthly: null };

function setReportPeriod(period) {
  reportPeriod = period;
  document.querySelectorAll('.report-period-btn').forEach(function(el) {
    el.classList.toggle('active', el.getAttribute('data-period') === period);
  });
  renderReportPreview();
  fetchReport(period);
}

function fetchReport(period) {
  period = period || reportPeriod;
  return fetch('/api/report?period=' + encodeURIComponent(period))
    .then(function(r) { return r.json(); })
    .then(function(data) {
      REPORT_CACHE[period] = data;
      if (period === reportPeriod) renderReportPreview();
      return data;
    })
    .catch(function() {
      // Sin conexión: deja el cache anterior. Si es la carga INICIAL (sin
      // cache todavía), ofrece reintentar — un fallo silencioso ahí dejaría
      // la card vacía sin ninguna pista de qué pasó (C2, Fase 8C).
      if (!REPORT_CACHE[period] && typeof showRetryToast === 'function') {
        showRetryToast(function() { fetchReport(period); });
      }
    });
}

// Roadmap P2 paso 6: cuando el backend YA tiene números del período pero
// todavía no corrió la narrativa del coach IA (has_narrative === false),
// usamos un copy propio del cliente (report_narrative_pending) que explica
// el cuándo y no suena a error — en vez del texto server-side genérico
// (report_no_narrative en app/i18n.py, fuera del scope de archivos de P2).
// Si el cache es de un shape viejo sin has_narrative, cae al comportamiento
// anterior (texto que trae el propio backend).
function _reportNarrativeText(data) {
  if (data.has_narrative === false) return t('report_narrative_pending');
  return data.narrative || t('report_no_data');
}

function renderReportPreview() {
  var el = document.getElementById('reportPreview');
  if (!el) return;
  var data = REPORT_CACHE[reportPeriod];
  if (!data) {
    el.textContent = '';
    return;
  }
  el.textContent = _reportNarrativeText(data);
}

function _reportMetricRows(data) {
  var means = (data && data.data && data.data.means) || {};
  var deltas = (data && data.data && data.data.deltas) || {};
  var defs = [
    { key: 'recovery', lbl: 'report_metric_recovery', unit: '%', fmt: function(v) { return Math.round(v); } },
    { key: 'hrv', lbl: 'report_metric_hrv', unit: ' ms', fmt: function(v) { return Math.round(v); } },
    { key: 'strain', lbl: 'report_metric_strain', unit: '/21', fmt: function(v) { return v.toFixed(1); } },
    { key: 'asleep', lbl: 'report_metric_sleep', unit: 'h', fmt: function(v) { return (v / 60).toFixed(1); } },
    { key: 'sleep_perf', lbl: 'report_metric_sleep_perf', unit: '%', fmt: function(v) { return Math.round(v); } },
  ];

  return defs.filter(function(d) { return means[d.key] != null; }).map(function(d) {
    var v = means[d.key];
    var delta = deltas[d.key];
    var deltaHtml = '';
    if (delta != null) {
      var good = d.key === 'strain' ? delta < 0 : delta > 0;
      var deltaCls = delta === 0 ? '' : (good ? 'good' : 'bad');
      var sign = delta > 0 ? '+' : '';
      deltaHtml = '<div class="report-metric-delta ' + deltaCls + '">' + sign + delta + '</div>';
    }
    return '<div class="report-metric-tile">'
      + '<div class="report-metric-val">' + d.fmt(v) + '<span style="font-size:13px">' + d.unit + '</span></div>'
      + '<div class="report-metric-lbl">' + escHtml(t(d.lbl)) + '</div>'
      + deltaHtml
      + '</div>';
  }).join('');
}

// Números clave adicionales del período (roadmap B6): mejor/peor día,
// adherencia al diario y hallazgo destacado. Reusa las claves i18n ya
// definidas en STRINGS (report_best_day / report_worst_day /
// report_adherence_lbl / report_top_insight_lbl / report_range).
function _reportExtraRows(data) {
  var d = data && data.data;
  if (!d) return '';
  var rows = [];
  if (d.start && d.end) {
    rows.push('<div class="report-extra-row report-extra-range">'
      + escHtml(t('report_range').replace('{start}', d.start).replace('{end}', d.end)) + '</div>');
  }
  if (d.best_day) {
    rows.push('<div class="report-extra-row"><span>' + escHtml(t('report_best_day'))
      + '</span><span>' + escHtml(d.best_day) + '</span></div>');
  }
  if (d.worst_day) {
    rows.push('<div class="report-extra-row"><span>' + escHtml(t('report_worst_day'))
      + '</span><span>' + escHtml(d.worst_day) + '</span></div>');
  }
  if (d.adherence && d.adherence.days_total) {
    rows.push('<div class="report-extra-row"><span>' + escHtml(t('report_adherence_lbl'))
      + '</span><span>' + d.adherence.days_logged + '/' + d.adherence.days_total
      + ' (' + d.adherence.pct + '%)</span></div>');
  }
  if (d.top_insight && d.top_insight.headline) {
    rows.push('<div class="report-extra-row report-extra-insight"><span>' + escHtml(t('report_top_insight_lbl'))
      + '</span><span>' + escHtml(d.top_insight.headline) + '</span></div>');
  }
  if (!rows.length) return '';
  return '<div class="report-extras">' + rows.join('') + '</div>';
}

// Card del arquetipo de sueño mensual (Roadmap P2, F8, paso 5). Solo aparece
// cuando reportPeriod === 'monthly' Y el backend tiene suficiente dato (>=14
// noches ese mes) — sin dato suficiente, la sección está AUSENTE (no un
// placeholder vacío, criterio 13). 2-3 métricas clave con su percentil vs el
// propio histórico del usuario (nunca contra población).
function _reportArchetypeCard(data) {
  if (reportPeriod !== 'monthly') return '';
  var a = data && data.data && data.data.sleep_archetype;
  if (!a) return '';

  var m = a.metrics || {};
  var p = a.percentiles || {};
  var rows = [];

  if (m.mean_asleep_min != null) {
    var hrs = (m.mean_asleep_min / 60).toFixed(1);
    var pctTxt = p.mean_asleep_min != null ? ' · p' + p.mean_asleep_min : '';
    rows.push('<div class="archetype-metric-row"><span>' + escHtml(t('archetype_metric_duration'))
      + '</span><span>' + hrs + 'h' + escHtml(pctTxt) + '</span></div>');
  }
  if (m.consistency_score != null) {
    rows.push('<div class="archetype-metric-row"><span>' + escHtml(t('archetype_metric_consistency'))
      + '</span><span>' + m.consistency_score + '/100</span></div>');
  }
  if (m.mean_efficiency_pct != null) {
    var effPctTxt = p.mean_efficiency_pct != null ? ' · p' + p.mean_efficiency_pct : '';
    rows.push('<div class="archetype-metric-row"><span>' + escHtml(t('archetype_metric_efficiency'))
      + '</span><span>' + m.mean_efficiency_pct.toFixed(0) + '%' + escHtml(effPctTxt) + '</span></div>');
  }

  return '<div class="archetype-card">'
    + '<div class="archetype-hdr"><span class="archetype-badge">' + escHtml(t('archetype_badge')) + '</span>'
    + '<span class="archetype-name">' + escHtml(a.name) + '</span></div>'
    + '<div class="archetype-desc">' + escHtml(a.description) + '</div>'
    + '<div class="archetype-metrics">' + rows.join('') + '</div>'
    + '</div>';
}

function openReportModal() {
  var modal = document.getElementById('reportModal');
  var title = document.getElementById('reportModalTitle');
  var narrativeEl = document.getElementById('reportModalNarrative');
  var metricsEl = document.getElementById('reportModalMetrics');
  var archetypeEl = document.getElementById('reportModalArchetype');
  if (!modal) return;

  if (title) {
    title.textContent = reportPeriod === 'monthly' ? t('report_period_monthly') + ' — ' + t('report_modal_title')
      : t('report_period_weekly') + ' — ' + t('report_modal_title');
  }

  var data = REPORT_CACHE[reportPeriod];
  if (narrativeEl) narrativeEl.textContent = data ? _reportNarrativeText(data) : t('report_no_data');
  if (metricsEl) metricsEl.innerHTML = data ? (_reportMetricRows(data) + _reportExtraRows(data)) : '';
  if (archetypeEl) archetypeEl.innerHTML = data ? _reportArchetypeCard(data) : '';

  modal.classList.remove('hidden');

  // Re-fetch fresco en background (progressive enhancement: el usuario ya ve
  // lo que había en cache mientras llega lo nuevo).
  fetchReport(reportPeriod).then(function(fresh) {
    if (!fresh) return;
    if (narrativeEl) narrativeEl.textContent = _reportNarrativeText(fresh);
    if (metricsEl) metricsEl.innerHTML = _reportMetricRows(fresh) + _reportExtraRows(fresh);
    if (archetypeEl) archetypeEl.innerHTML = _reportArchetypeCard(fresh);
  });
}

function closeReportModal() {
  var modal = document.getElementById('reportModal');
  if (modal) modal.classList.add('hidden');
}

// Llamado desde renderTend() al entrar a la tab Tendencias — carga el
// preview de la card sin esperar a que el usuario abra el modal.
function fetchAndRenderReportPreview() {
  fetchReport('weekly');
  fetchReport('monthly');
}
