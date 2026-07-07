// labs.js — Laboratorios de sangre manuales (Fase 8D, paso D1).
//
// Depende de globals definidos en el <script> inline del template: t() (i18n),
// escHtml(), _miniSparkline(), A (paleta de acentos). Se carga vía <script src>
// DESPUÉS de esos globals (ver templates/vitals_ios.html, cierre de </body>).

var LABS_CATALOG = [];   // [{key,label,unit,ref_low,ref_high}]
var LABS_SERIES = {};    // {marker: [{id,date,marker,value,unit,ref_low,ref_high,note,out_of_range}]}
var labsCurrentMarker = null;

// ── carga de estado ──────────────────────────────────────────────────────────

function fetchLabsState() {
  return fetch('/api/labs')
    .then(function(r) { return r.json(); })
    .then(function(data) {
      LABS_CATALOG = data.catalog || [];
      LABS_SERIES = data.series || {};
      _populateLabsMarkerSelect();
      renderLabsMarkerList();
      _updateLabsSummary();
    })
    .catch(function() {
      if (typeof showRetryToast === 'function') {
        showRetryToast(function() { fetchLabsState(); });
      }
    });
}

function _updateLabsSummary() {
  var el = document.getElementById('masLabsSummary');
  if (!el) return;
  var markersWithData = Object.keys(LABS_SERIES).filter(function(k) { return (LABS_SERIES[k] || []).length; });
  if (!markersWithData.length) return; // deja el texto por defecto (i18n)
  var oob = markersWithData.filter(function(k) {
    var lst = LABS_SERIES[k] || [];
    var last = lst[lst.length - 1];
    return last && last.out_of_range;
  }).length;
  var txt = t('mas_labs_summary_count').replace('{n}', markersWithData.length);
  if (oob > 0) txt += ' · ' + t('mas_labs_summary_oob').replace('{n}', oob);
  el.textContent = txt;
}

function _populateLabsMarkerSelect() {
  var sel = document.getElementById('labsFormMarker');
  if (!sel) return;
  sel.innerHTML = LABS_CATALOG.map(function(m) {
    return '<option value="' + escHtml(m.key) + '">' + escHtml(m.label) + '</option>';
  }).join('');
}

// ── modal open/close/tabs ────────────────────────────────────────────────────

function openLabsModal() {
  var modal = document.getElementById('labsModal');
  if (!modal) return;
  modal.classList.remove('hidden');
  labsSwitchTab('list');
  fetchLabsState();
  var dateInput = document.getElementById('labsFormDate');
  if (dateInput && !dateInput.value) {
    var d = new Date();
    var y = d.getFullYear(), m = ('0' + (d.getMonth() + 1)).slice(-2), day = ('0' + d.getDate()).slice(-2);
    dateInput.value = y + '-' + m + '-' + day;
  }
}

function closeLabsModal() {
  var modal = document.getElementById('labsModal');
  if (modal) modal.classList.add('hidden');
}

function labsSwitchTab(tab) {
  document.querySelectorAll('.labs-tab-btn').forEach(function(el) {
    el.classList.toggle('active', el.getAttribute('data-labs-tab') === tab);
  });
  document.getElementById('labsPaneList').style.display = (tab === 'list') ? '' : 'none';
  document.getElementById('labsPaneDetail').style.display = 'none';
  document.getElementById('labsPaneAdd').style.display = (tab === 'add') ? '' : 'none';
  document.getElementById('labsPaneImport').style.display = (tab === 'import') ? '' : 'none';
  document.getElementById('labsTabs').style.display = '';
}

function labsShowList() {
  document.getElementById('labsTabs').style.display = '';
  document.getElementById('labsPaneList').style.display = '';
  document.getElementById('labsPaneDetail').style.display = 'none';
  document.getElementById('labsPaneAdd').style.display = 'none';
  document.getElementById('labsPaneImport').style.display = 'none';
  document.querySelectorAll('.labs-tab-btn').forEach(function(el) {
    el.classList.toggle('active', el.getAttribute('data-labs-tab') === 'list');
  });
}

// ── lista de marcadores ──────────────────────────────────────────────────────

function renderLabsMarkerList() {
  var wrap = document.getElementById('labsMarkerList');
  if (!wrap) return;

  var withData = LABS_CATALOG.filter(function(m) { return (LABS_SERIES[m.key] || []).length; });

  if (!withData.length) {
    wrap.innerHTML = '<div class="labs-empty">' + escHtml(t('labs_empty')) + '</div>';
    return;
  }

  var html = withData.map(function(m) {
    var series = LABS_SERIES[m.key] || [];
    var last = series[series.length - 1];
    var vals = series.map(function(e) { return e.value; });
    var spark = (typeof _miniSparkline === 'function' && vals.length > 1) ? _miniSparkline(vals, A.blue || '#0A84FF') : '';
    var sparkHtml = spark ? '<svg viewBox="0 0 180 42" class="labs-marker-spark" preserveAspectRatio="none">' + spark + '</svg>' : '';
    var latestTxt = last ? (last.value + ' ' + (last.unit || '')) : '—';
    var oob = last && last.out_of_range;

    return '<div class="labs-marker-row" onclick="labsShowDetail(\'' + m.key.replace(/'/g, "\\'") + '\')">'
      + '<div class="labs-marker-info">'
      + '<div class="labs-marker-name">' + escHtml(m.label) + '</div>'
      + '<div class="labs-marker-latest' + (oob ? ' oor' : '') + '"><span class="' + (oob ? 'oor' : '') + '">' + escHtml(latestTxt) + '</span></div>'
      + '</div>'
      + sparkHtml
      + (oob ? '<span class="labs-oob-badge">' + escHtml(t('labs_oob_badge')) + '</span>' : '')
      + '</div>';
  }).join('');

  wrap.innerHTML = html;
}

// ── detalle de un marcador ───────────────────────────────────────────────────

function labsShowDetail(markerKey) {
  labsCurrentMarker = markerKey;
  var m = LABS_CATALOG.find(function(x) { return x.key === markerKey; });
  if (!m) return;

  document.getElementById('labsTabs').style.display = 'none';
  document.getElementById('labsPaneList').style.display = 'none';
  document.getElementById('labsPaneAdd').style.display = 'none';
  document.getElementById('labsPaneImport').style.display = 'none';
  document.getElementById('labsPaneDetail').style.display = '';

  document.getElementById('labsDetailTitle').textContent = m.label;
  var rangeTxt = (m.ref_low != null || m.ref_high != null)
    ? t('labs_ref_range').replace('{low}', m.ref_low != null ? m.ref_low : '—').replace('{high}', m.ref_high != null ? m.ref_high : '—').replace('{unit}', m.unit || '')
    : '';
  document.getElementById('labsDetailRange').textContent = rangeTxt;

  var series = LABS_SERIES[markerKey] || [];
  var vals = series.map(function(e) { return e.value; });
  var sparkEl = document.getElementById('labsDetailSpark');
  if (sparkEl) {
    var spark = (typeof _miniSparkline === 'function' && vals.length > 1) ? _miniSparkline(vals, A.blue || '#0A84FF') : '';
    sparkEl.innerHTML = spark ? '<svg viewBox="0 0 180 42" width="100%" height="60" preserveAspectRatio="none">' + spark + '</svg>' : '';
  }

  var histHtml = series.slice().reverse().map(function(e) {
    var valTxt = e.value + ' ' + (e.unit || '');
    return '<div class="labs-history-row">'
      + '<span>' + escHtml(e.date) + '</span>'
      + '<span class="' + (e.out_of_range ? 'oor' : '') + '">' + escHtml(valTxt) + '</span>'
      + '<span class="labs-history-del" onclick="labsDeleteEntry(\'' + e.id + '\')">'
      + '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2m2 0v14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2V6h12z"/></svg>'
      + '</span>'
      + '</div>';
  }).join('');
  document.getElementById('labsDetailHistory').innerHTML = histHtml || '<div class="labs-empty">' + escHtml(t('labs_empty')) + '</div>';
}

function labsDeleteEntry(entryId) {
  fetch('/api/labs/' + encodeURIComponent(entryId), { method: 'DELETE' })
    .then(function(r) { return r.json(); })
    .then(function() {
      return fetchLabsState();
    })
    .then(function() {
      if (labsCurrentMarker) labsShowDetail(labsCurrentMarker);
    })
    .catch(function() {});
}

// ── alta manual ──────────────────────────────────────────────────────────────

function labsSubmitEntry() {
  var date = document.getElementById('labsFormDate').value;
  var marker = document.getElementById('labsFormMarker').value;
  var valueRaw = document.getElementById('labsFormValue').value;
  var note = document.getElementById('labsFormNote').value;
  var err = document.getElementById('labsAddErr');

  var value = parseFloat(valueRaw);
  if (!date || !marker || isNaN(value)) {
    if (err) { err.textContent = t('labs_form_err_required'); err.style.display = ''; }
    return;
  }

  fetch('/api/labs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ date: date, marker: marker, value: value, note: note || null })
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
      if (err) err.style.display = 'none';
      document.getElementById('labsFormValue').value = '';
      document.getElementById('labsFormNote').value = '';
      fetchLabsState().then(function() { labsSwitchTab('list'); });
    })
    .catch(function() {
      if (err) { err.textContent = t('err_net'); err.style.display = ''; }
    });
}

// ── import CSV ───────────────────────────────────────────────────────────────

function labsSubmitImport() {
  var fileInput = document.getElementById('labsImportFile');
  var resultEl = document.getElementById('labsImportResult');
  var file = fileInput && fileInput.files && fileInput.files[0];
  if (!file) {
    if (resultEl) resultEl.textContent = t('labs_import_err_no_file');
    return;
  }

  file.text().then(function(text) {
    return fetch('/api/labs/import', {
      method: 'POST',
      headers: { 'Content-Type': 'text/csv' },
      body: text
    });
  })
    .then(function(r) { return r.json(); })
    .then(function(data) {
      var imported = (data.imported || []).length;
      var rejected = data.rejected || [];
      var txt = t('labs_import_result').replace('{imported}', imported).replace('{rejected}', rejected.length);
      if (rejected.length) {
        txt += '\n' + rejected.map(function(r) { return '#' + r.row + ': ' + r.reason; }).join('\n');
      }
      if (resultEl) resultEl.textContent = txt;
      fetchLabsState();
    })
    .catch(function() {
      if (resultEl) resultEl.textContent = t('err_net');
    });
}

// ── hooks de inicialización ──────────────────────────────────────────────────
// Card de labs vive en Más (carga bajo demanda, no en el arranque de la app) —
// se carga la primera vez que se abre el modal (openLabsModal -> fetchLabsState).
// Best-effort: refresca el summary de Más cuando la tab Más se abre, si ya
// hubo un fetch previo (evita 1 request extra si el usuario nunca abrió labs).
var _labsMasHooked = false;
if (typeof goTab === 'function' && !_labsMasHooked) {
  _labsMasHooked = true;
  var _labsOrigGoTab = goTab;
  goTab = function(screenId) {
    _labsOrigGoTab(screenId);
    if (screenId === 'screenMas' && Object.keys(LABS_SERIES).length) {
      _updateLabsSummary();
    }
  };
}
