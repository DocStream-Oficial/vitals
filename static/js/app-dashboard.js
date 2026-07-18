
// ── CONSTANTS ──
var A = {
  green:'#30D158', indigo:'#5E5CE6', blue:'#0A84FF', cyan:'#64D2FF',
  red:'#FF375F', orange:'#FF9F0A', teal:'#40C8E0', purple:'#BF5AF2', purpLite:'#9D8DF5'
};

// ── UTILS ──
function hexA(h,a){var n=parseInt(h.slice(1),16);return 'rgba('+((n>>16)&255)+','+((n>>8)&255)+','+(n&255)+','+a+')';}
function clamp(v,a,b){return Math.max(a,Math.min(b,v));}

// Format minutes-from-midnight to 12h format (reused from premium template _hm logic)
function _hm(m){
  m=Math.round(((m%1440)+1440)%1440);
  var h=Math.floor(m/60),mm=m%60;
  var ap=h<12?'am':'pm';
  var h12=h%12;if(h12===0)h12=12;
  return h12+':'+(mm<10?'0':'')+mm+' '+ap;
}

// ── DATA STATE ──
var days = (DB && DB.days) || [];
var exercises = (DB && DB.exercises) || [];
var summary = (DB && DB.summary) || {};
var bodyage = summary.bodyage || {};
var sel = days.length > 0 ? days.length - 1 : 0;

// ── CARD ORDER ──
// ── CARD ORDER — generalizado a 2 scopes (Hoy + Tendencias) ──
var ORDER_SCOPES = {
  hoy: {
    container: '#screenHoy .cards',
    key: 'vitals-card-order',
    def: ['heroCard','coachCard','insightCards','planCard','sleepSummaryCard','stepsCard','healthCard','journalCard','bodyAgeCard','loadCard','ecgCard'],
    get names(){ return {
      insightCards: t('card_alerts'), heroCard: t('card_rings'), coachCard: t('card_coach'),
      journalCard: t('journal_card_lbl'), planCard: t('plan_card_lbl'),
      bodyAgeCard: t('card_bodyage'), healthCard: t('card_health'),
      stepsCard: t('card_steps'), sleepSummaryCard: t('card_bed'), loadCard: t('card_load'),
      ecgCard: t('card_ecg')
    }; },
    anchor: null
  },
  tend: {
    container: '#screenTend .cards',
    key: 'vitals-tend-order',
    def: ['tendRecStrainCard','tendBalanceCard','tendHrvCard','tendRhrCard','tendSpo2Card','tendTempCard','tendMetricsCard','tendWorkoutsCard','tendPalancasCard','tendJournalImpactCard','tendReportCard'],
    get names(){ return {
      tendRecStrainCard: t('card_rec_strain'), tendBalanceCard: t('card_balance'),
      tendHrvCard: t('card_hrv'), tendRhrCard: t('card_rhr'),
      tendSpo2Card: t('card_spo2'), tendTempCard: t('card_temp'),
      tendMetricsCard: t('card_metrics'), tendWorkoutsCard: t('card_workouts'),
      tendPalancasCard: t('card_palancas'),
      tendJournalImpactCard: t('journal_impact_title'), tendReportCard: t('report_card_lbl')
    }; },
    anchor: null
  }
};

// Backwards-compat aliases (used by legacy callers — NO eliminar)
var CARD_ORDER_DEFAULT = ORDER_SCOPES.hoy.def;
var CARD_NAMES = ORDER_SCOPES.hoy.names;

function getOrder(scope) {
  var cfg = ORDER_SCOPES[scope];
  if (!cfg) return [];
  try {
    var raw = localStorage.getItem(cfg.key);
    if (!raw) return cfg.def.slice();
    var saved = JSON.parse(raw);
    if (!Array.isArray(saved)) return cfg.def.slice();
    // Filter out ids that no longer exist in def
    var result = saved.filter(function(id) { return cfg.def.indexOf(id) !== -1; });
    // Append any ids from def missing in saved (new cards never disappear)
    cfg.def.forEach(function(id) { if (result.indexOf(id) === -1) result.push(id); });
    return result;
  } catch(e) {
    return cfg.def.slice();
  }
}

function saveOrder(scope, arr) {
  var cfg = ORDER_SCOPES[scope];
  if (!cfg) return;
  try { localStorage.setItem(cfg.key, JSON.stringify(arr)); } catch(e) {}
}

function applyOrder(scope) {
  var cfg = ORDER_SCOPES[scope];
  if (!cfg) return;
  var container = document.querySelector(cfg.container);
  if (!container) return;
  var order = getOrder(scope);
  if (cfg.anchor) {
    // Tend: insertBefore anchor to leave metrics/workouts/Palancas fixed
    var anchorEl = document.querySelector(cfg.anchor);
    if (!anchorEl) return;
    order.forEach(function(id) {
      var el = document.getElementById(id);
      if (el) container.insertBefore(el, anchorEl);
    });
  } else {
    // Hoy: appendChild — all cards are reorderable
    order.forEach(function(id) {
      var el = document.getElementById(id);
      if (el) container.appendChild(el);
    });
  }
  layoutMasonry(scope);
}

// ── MASONRY (Roadmap P3 Fase A) ──────────────────────────────────────────
// Balancea las 2 columnas de ≥900px respetando el orden del DOM (row-flow
// sparse, sin dense): cada card recibe grid-row-end:span N según su altura
// real medida, de modo que la siguiente card cae en la columna más corta sin
// dejar huecos verticales grandes. Por debajo de 900px es un no-op que
// además LIMPIA los spans inline para no romper el 1-col de CSS.
var MASONRY_UNIT = 8;   // debe matchear grid-auto-rows en CSS
var MASONRY_GAP = 14;   // debe matchear column-gap/gap de .cards en CSS
function layoutMasonry(scope) {
  var cfg = ORDER_SCOPES[scope];
  if (!cfg) return;
  var container = document.querySelector(cfg.container);
  if (!container) return;
  var cards = Array.prototype.filter.call(container.children, function(el) {
    return el.nodeType === 1;
  });
  if (window.innerWidth < 900) {
    cards.forEach(function(el) { el.style.gridRowEnd = ''; });
    return;
  }
  cards.forEach(function(el) {
    if (el.offsetParent === null) { el.style.gridRowEnd = ''; return; } // oculto: no medir (daría 0)
    var h = el.getBoundingClientRect().height;
    var span = Math.max(1, Math.ceil((h + MASONRY_GAP) / MASONRY_UNIT));
    el.style.gridRowEnd = 'span ' + span;
  });
}
function layoutMasonryAll() {
  Object.keys(ORDER_SCOPES).forEach(function(scope) { layoutMasonry(scope); });
}
// Debounced resize/ResizeObserver: recomputa ambos scopes al cruzar 900px o
// al cambiar el ancho disponible (rotación, redimensionar ventana). Debounce
// para no thrashear; no observamos las cards individuales (solo el
// contenedor), así que no hay loop de auto-disparo del propio ResizeObserver.
var _masonryDebounce = null;
function _scheduleLayoutMasonryAll() {
  if (_masonryDebounce) clearTimeout(_masonryDebounce);
  _masonryDebounce = setTimeout(function() {
    _masonryDebounce = null;
    layoutMasonryAll();
  }, 120);
}
window.addEventListener('resize', _scheduleLayoutMasonryAll);
if (typeof ResizeObserver !== 'undefined') {
  (function() {
    var hoyEl = document.querySelector(ORDER_SCOPES.hoy.container);
    var tendEl = document.querySelector(ORDER_SCOPES.tend.container);
    var ro = new ResizeObserver(function() { _scheduleLayoutMasonryAll(); });
    if (hoyEl) ro.observe(hoyEl);
    if (tendEl) ro.observe(tendEl);
  })();
}
// Red de seguridad: el propio cruce del breakpoint 900px (el trigger real del
// masonry) no siempre cambia el tamaño de .cards en sí (solo su
// grid-template-columns vía media query) — en ese caso ni 'resize' de window
// ni el ResizeObserver del contenedor garantizan disparo en todos los
// motores/entornos. matchMedia sobre el propio breakpoint es el evento
// correcto y explícito para ese cruce específico.
if (typeof window.matchMedia === 'function') {
  var _mqMasonry = window.matchMedia('(min-width:900px)');
  var _onMqMasonryChange = function(){ _scheduleLayoutMasonryAll(); };
  if (_mqMasonry.addEventListener) _mqMasonry.addEventListener('change', _onMqMasonryChange);
  else if (_mqMasonry.addListener) _mqMasonry.addListener(_onMqMasonryChange); // Safari viejo
}

function moveCard(scope, id, dir) {
  var order = getOrder(scope);
  var idx = order.indexOf(id);
  if (idx === -1) return;
  var newIdx = idx + dir;
  if (newIdx < 0 || newIdx >= order.length) return;
  var tmp = order[idx]; order[idx] = order[newIdx]; order[newIdx] = tmp;
  saveOrder(scope, order);
  applyOrder(scope);
  renderOrderList(scope);
}

function resetOrder(scope) {
  var cfg = ORDER_SCOPES[scope];
  if (!cfg) return;
  saveOrder(scope, cfg.def.slice());
  applyOrder(scope);
  renderOrderList(scope);
}

// Backwards-compat wrappers (callers inside renderMas/resetCardOrder etc.)
function getCardOrder() { return getOrder('hoy'); }
function saveCardOrder(arr) { saveOrder('hoy', arr); }
function applyCardOrder() { applyOrder('hoy'); }
function resetCardOrder() { resetOrder('hoy'); }
function renderCardOrderList() { renderOrderList('hoy'); }

// ── ORDER OVERLAY ──
var _orderActiveScope = 'hoy';

function openOrderPopup(scope) {
  _orderActiveScope = scope || 'hoy';
  _orderUpdateSeg();
  renderOrderList(_orderActiveScope);
  var overlay = document.getElementById('orderOverlay');
  if (overlay) overlay.classList.add('visible');
}

function closeOrder() {
  var overlay = document.getElementById('orderOverlay');
  if (overlay) overlay.classList.remove('visible');
}

function switchOrderScope(scope) {
  _orderActiveScope = scope;
  _orderUpdateSeg();
  renderOrderList(scope);
}

function _orderUpdateSeg() {
  var btnHoy = document.getElementById('orderSegHoy');
  var btnTend = document.getElementById('orderSegTend');
  if (btnHoy) btnHoy.className = 'order-seg-btn' + (_orderActiveScope === 'hoy' ? ' active' : '');
  if (btnTend) btnTend.className = 'order-seg-btn' + (_orderActiveScope === 'tend' ? ' active' : '');
}

function _orderResetActive() {
  resetOrder(_orderActiveScope);
}

function renderOrderList(scope) {
  var list = document.getElementById('orderList');
  if (!list) return;
  var cfg = ORDER_SCOPES[scope];
  if (!cfg) return;
  list.innerHTML = '';
  var order = getOrder(scope);

  order.forEach(function(id, idx) {
    var name = cfg.names[id] || id;
    var isFirst = idx === 0;
    var isLast = idx === order.length - 1;

    var row = document.createElement('div');
    row.className = 'mas-row order-row';
    row.setAttribute('data-card-id', id);
    row.style.cssText = 'display:flex;align-items:center;gap:10px;touch-action:none;cursor:default;user-select:none;';

    // Drag handle
    var handle = document.createElement('span');
    handle.className = 'order-handle';
    handle.innerHTML = '&#8942;&#8942;';
    handle.style.cssText = 'font-size:18px;color:var(--label3);cursor:grab;padding:0 4px;line-height:1;flex-shrink:0;touch-action:none;';
    handle.setAttribute('aria-label', 'Arrastrar');

    // Name
    var label = document.createElement('div');
    label.style.cssText = 'flex:1;font:500 14px -apple-system;color:var(--label);';
    label.textContent = name;

    // Up button
    var btnUp = document.createElement('button');
    btnUp.textContent = '↑';
    btnUp.disabled = isFirst;
    btnUp.style.cssText = 'width:32px;height:32px;border:none;border-radius:8px;background:var(--card2,var(--card));font-size:16px;color:' + (isFirst ? 'var(--label3)' : 'var(--label)') + ';cursor:' + (isFirst ? 'default' : 'pointer') + ';flex-shrink:0;';
    btnUp.setAttribute('aria-label', 'Subir ' + name);
    (function(cardId, sc){ btnUp.onclick = function(){ moveCard(sc, cardId, -1); }; })(id, scope);

    // Down button
    var btnDown = document.createElement('button');
    btnDown.textContent = '↓';
    btnDown.disabled = isLast;
    btnDown.style.cssText = 'width:32px;height:32px;border:none;border-radius:8px;background:var(--card2,var(--card));font-size:16px;color:' + (isLast ? 'var(--label3)' : 'var(--label)') + ';cursor:' + (isLast ? 'default' : 'pointer') + ';flex-shrink:0;';
    btnDown.setAttribute('aria-label', 'Bajar ' + name);
    (function(cardId, sc){ btnDown.onclick = function(){ moveCard(sc, cardId, 1); }; })(id, scope);

    row.appendChild(handle);
    row.appendChild(label);
    row.appendChild(btnUp);
    row.appendChild(btnDown);
    list.appendChild(row);

    // Attach drag handler to handle (uses scope-aware finishDrag)
    _attachOrderHandleDrag(handle, row, list, scope);
  });
}

// Drag handler for the popup order list (scope-aware)
function _attachOrderHandleDrag(handle, row, list, scope) {
  var dragState = null;

  handle.addEventListener('pointerdown', function(e) {
    e.preventDefault();
    handle.setPointerCapture(e.pointerId);
    var rows = Array.from(list.querySelectorAll('.order-row'));
    var rowRect = row.getBoundingClientRect();
    dragState = {
      pointerId: e.pointerId,
      startY: e.clientY,
      rowHeight: rowRect.height,
      origIdx: rows.indexOf(row),
      currentIdx: rows.indexOf(row)
    };
    row.style.opacity = '0.7';
    row.style.background = 'var(--card2,var(--card))';
    row.style.borderRadius = '10px';
  });

  handle.addEventListener('pointermove', function(e) {
    if (!dragState) return;
    e.preventDefault();
    var delta = e.clientY - dragState.startY;
    var steps = Math.round(delta / (dragState.rowHeight || 44));
    var targetIdx = dragState.origIdx + steps;
    var rows = Array.from(list.querySelectorAll('.order-row'));
    targetIdx = Math.max(0, Math.min(rows.length - 1, targetIdx));
    if (targetIdx !== dragState.currentIdx) {
      var movingRow = rows[dragState.currentIdx];
      if (targetIdx < dragState.currentIdx) {
        list.insertBefore(movingRow, rows[targetIdx]);
      } else {
        var after = rows[targetIdx].nextSibling;
        if (after) list.insertBefore(movingRow, after);
        else list.appendChild(movingRow);
      }
      dragState.currentIdx = targetIdx;
    }
  });

  function finishDrag() {
    if (!dragState) return;
    row.style.opacity = '';
    row.style.background = '';
    row.style.borderRadius = '';
    var rows = Array.from(list.querySelectorAll('.order-row'));
    var newOrder = rows.map(function(r) { return r.getAttribute('data-card-id'); });
    saveOrder(scope, newOrder);
    applyOrder(scope);
    renderOrderList(scope);
    dragState = null;
  }

  handle.addEventListener('pointerup', finishDrag);
  handle.addEventListener('pointercancel', finishDrag);
}

// ── THEME ──
function setMode(mode){
  localStorage.setItem('vitals-theme', mode);
  document.body.className = mode === 'light' ? 'light' : '';
  var dk=document.getElementById('themeIconDark'), lt=document.getElementById('themeIconLight');
  if(dk&&lt){ dk.style.display = mode==='light'?'none':'block'; lt.style.display = mode==='light'?'block':'none'; }
}
function toggleTheme(){
  var cur = localStorage.getItem('vitals-theme') || 'dark';
  setMode(cur==='light'?'dark':'light');
  // Roadmap P3 Fase A: el cambio de tema puede alterar line-heights/anchos de
  // texto → recompute (rAF para esperar el repaint del CSS).
  if (typeof layoutMasonryAll === 'function') requestAnimationFrame(function(){ layoutMasonryAll(); });
}
(function(){
  var saved = localStorage.getItem('vitals-theme') || 'dark';
  setMode(saved);
})();

// ── TABS ──
var TAB_MAP = {
  screenHoy:'tabHoy', screenSleep:'tabSleep', screenTend:'tabTend', screenCoach:'tabCoach', screenMas:'tabMas'
};
var currentTab = 'screenHoy';
function goTab(screenId){
  document.querySelectorAll('.screen').forEach(function(s){s.classList.remove('active');});
  document.getElementById(screenId).classList.add('active');
  currentTab = screenId;
  // Update tab bar icon colors
  var GREEN = '#30D158', DIM = 'var(--label2)';
  Object.keys(TAB_MAP).forEach(function(sid){
    var tabEl = document.getElementById(TAB_MAP[sid]);
    var isActive = sid === screenId;
    var col = isActive ? GREEN : DIM;
    tabEl.querySelectorAll('svg').forEach(function(s){
      s.setAttribute('stroke', col);
      // For grid icon (fill-based)
      if(s.querySelector('rect')){s.setAttribute('fill',col);s.removeAttribute('stroke');}
    });
    tabEl.querySelector('span').style.color = col;
    if(isActive){
      tabEl.querySelector('svg').setAttribute('stroke-width','2.2');
    }
  });
  // Lazy-render Tendencias on first visit and on each activation
  if(screenId === 'screenTend'){ renderTend(); }
  // Re-dispara las animaciones de las gráficas al entrar a Hoy (anillos + sparklines del monitor)
  else if(screenId === 'screenHoy'){ retriggerAnim('screenHoy'); layoutMasonry('hoy'); }
  // Roadmap P3 Fase A: medir un screen oculto da 0 — recomputar SIEMPRE al
  // volverse visible (cubre también Coach/Más, que pueden contener .cards
  // en scopes futuros; hoy/tend son los únicos con masonry real).
  layoutMasonryAll();
}
// Reinicia las animaciones CSS de los charts dentro de una pantalla, conservando sus delays inline
function retriggerAnim(rootId){
  var root = document.getElementById(rootId);
  if(!root) return;
  root.classList.add('anim-restart');
  void root.offsetWidth;            // fuerza reflow → quita las animaciones
  root.classList.remove('anim-restart'); // → reinician desde el inicio
}

// ── SVG HELPERS ──
function smooth(pts){
  if(pts.length<2)return pts.length?'M'+pts[0][0]+','+pts[0][1]:'';
  var d='M'+pts[0][0].toFixed(1)+','+pts[0][1].toFixed(1);
  for(var i=0;i<pts.length-1;i++){
    var p0=pts[i-1]||pts[i],p1=pts[i],p2=pts[i+1],p3=pts[i+2]||p2;
    var c1x=p1[0]+(p2[0]-p0[0])/6,c1y=p1[1]+(p2[1]-p0[1])/6;
    var c2x=p2[0]-(p3[0]-p1[0])/6,c2y=p2[1]-(p3[1]-p1[1])/6;
    d+=' C'+c1x.toFixed(1)+','+c1y.toFixed(1)+' '+c2x.toFixed(1)+','+c2y.toFixed(1)+' '+p2[0].toFixed(1)+','+p2[1].toFixed(1);
  }
  return d;
}

function spark(data, color){
  if(!data||!data.length) return '';
  var W=62,H=24,p=3;
  var min=Math.min.apply(null,data),max=Math.max.apply(null,data);
  var X=function(i){return p+i*(W-2*p)/(data.length-1);};
  var Y=function(v){return p+(1-(v-min)/((max-min)||1))*(H-2*p);};
  var pts=data.map(function(v,i){return [X(i),Y(v)];});
  var last=pts[pts.length-1];
  return '<svg viewBox="0 0 '+W+' '+H+'" width="'+W+'" height="'+H+'" style="display:block;overflow:visible">'+
    '<path class="an-line" pathLength="1" vector-effect="non-scaling-stroke" d="'+smooth(pts)+'" fill="none" stroke="'+color+'" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>'+
    '<circle class="an-dot" style="animation-delay:.6s" cx="'+last[0].toFixed(1)+'" cy="'+last[1].toFixed(1)+'" r="2.4" fill="'+color+'"/></svg>';
}

function ringsSVG(rec, sleepPct, strainFrac){
  function ring(r, frac, grad, col, delay){
    var c=2*Math.PI*r;
    var dash=Math.max(0.001,Math.min(1,frac))*c;
    return '<circle cx="75" cy="75" r="'+r+'" fill="none" style="stroke:var(--ring-track)" stroke-width="12.5"/>'+
      '<circle class="an-ring" cx="75" cy="75" r="'+r+'" fill="none" stroke="url(#'+grad+')" stroke-width="12.5" stroke-linecap="round"'+
      ' stroke-dasharray="'+dash.toFixed(2)+' '+c.toFixed(2)+'"'+
      ' transform="rotate(-90 75 75)" style="--dlen:'+dash.toFixed(2)+';animation-delay:'+delay+'s;filter:drop-shadow(0 0 5px '+hexA(col,0.55)+')">';
  }
  return '<svg viewBox="0 0 150 150" width="100%" style="display:block"><defs>'+
    '<linearGradient id="rg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#5BF07F"/><stop offset="1" stop-color="#28B84A"/></linearGradient>'+
    '<linearGradient id="rs" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#7C7AF5"/><stop offset="1" stop-color="#4A48D8"/></linearGradient>'+
    '<linearGradient id="rt" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#8FE0FF"/><stop offset="1" stop-color="#2AA4F0"/></linearGradient>'+
    '</defs>'+
    ring(58,rec/100,'rg',A.green,0)+'</circle>'+
    ring(43.5,sleepPct/100,'rs',A.indigo,0.12)+'</circle>'+
    ring(29,strainFrac,'rt',A.cyan,0.24)+'</circle>'+
    '</svg>';
}

function buildDetailChart(data, color, minVal, maxVal, yTicks){
  // Roadmap P3 Fase B2: W subido de 330 a 700 (alternativa mínima documentada
  // del roadmap) para acotar el factor de estiramiento no-uniforme de
  // preserveAspectRatio="none" a ≤1.6x en 2-col / ~1.5x full-width, en vez de
  // threadear el ancho real de render por todos los callers (demasiado
  // invasivo para el alcance de esta fase). padL/padR escalados en proporción
  // para conservar el look (antes 34/10 sobre 330).
  var W=700,H=200,padL=72,padR=21,padT=12,padB=20;
  var pw=W-padL-padR,ph=H-padT-padB,N=data.length;
  var X=function(i){return padL+(N<=1?pw/2:i*pw/(N-1));};
  var Y=function(v){return padT+(1-(v-minVal)/(maxVal-minVal))*ph;};
  var body='';
  (yTicks||[]).forEach(function(t){
    var y=Y(t);
    body+='<line x1="'+padL+'" y1="'+y.toFixed(1)+'" x2="'+(W-padR)+'" y2="'+y.toFixed(1)+'" style="stroke:var(--grid)" stroke-width="1" vector-effect="non-scaling-stroke"/>';
    body+='<text x="'+(padL-6)+'" y="'+(y+3).toFixed(1)+'" text-anchor="end" style="fill:var(--axis);font:500 9px -apple-system">'+t+'</text>';
  });
  var pts=data.map(function(v,i){return [X(i),Y(clamp(v,minVal,maxVal))];});
  var line=smooth(pts);
  var last=pts[pts.length-1];
  // fill
  var area=line+' L'+pts[pts.length-1][0].toFixed(1)+','+(padT+ph).toFixed(1)+' L'+pts[0][0].toFixed(1)+','+(padT+ph).toFixed(1)+' Z';
  body+='<defs><linearGradient id="dtf" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="'+color+'" stop-opacity=".28"/><stop offset="1" stop-color="'+color+'" stop-opacity="0"/></linearGradient></defs>';
  body+='<path class="an-area" d="'+area+'" fill="url(#dtf)"/>';
  body+='<path class="an-line" pathLength="1" vector-effect="non-scaling-stroke" d="'+line+'" fill="none" stroke="'+color+'" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" style="filter:drop-shadow(0 1px 4px '+hexA(color,0.4)+')" />';
  body+='<circle class="an-dot" style="animation-delay:.7s" cx="'+last[0].toFixed(1)+'" cy="'+last[1].toFixed(1)+'" r="3.5" fill="'+color+'"/>';
  return '<svg viewBox="0 0 '+W+' '+H+'" width="100%" height="'+H+'" preserveAspectRatio="none" style="overflow:visible;display:block">'+body+'</svg>';
}

// ── ICON SVG ──
var ICONS = {
  heart:'M12 20.5C7 17 3.5 13.8 3.5 10.2 3.5 7.6 5.5 6 7.7 6c1.6 0 2.9.9 3.6 2 .7-1.1 2-2 3.6-2 2.2 0 4.2 1.6 4.2 4.2 0 3.6-3.5 6.8-8.5 10.3z',
  wave:'M2 12h3l2-5 3 10 2.5-7 2 4h6.5',
  wind:'M3 9h11a3 3 0 1 0-3-3M3 15h8a2.5 2.5 0 1 1-2.5 2.5',
  drop:'M12 3.5s5.5 6 5.5 10.2a5.5 5.5 0 0 1-11 0C6.5 9.5 12 3.5 12 3.5z',
  thermo:'M10.5 13.2V5.5a1.7 1.7 0 1 1 3.4 0v7.7a3.6 3.6 0 1 1-3.4 0z',
};
function icon(name, color, s){
  s = s||18;
  return '<svg viewBox="0 0 24 24" width="'+s+'" height="'+s+'" fill="none" stroke="'+color+'" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" style="display:block"><path d="'+ICONS[name]+'"/></svg>';
}

// ── LOCALE DATE ──
// Legacy aliases — point to locale-aware helpers
var DIAS  = _getDias();
var MESES = _getMeses();
function fmtDateES(dateStr){
  if(!dateStr) return '';
  var p=dateStr.split('-');
  var d=new Date(+p[0],+p[1]-1,+p[2]);
  var dias = _getDias(); var meses = _getMeses();
  return dias[d.getDay()]+', '+d.getDate()+(t('date_de'))+meses[d.getMonth()];
}

// ── AVG HELPERS ──
function avgField(arr, field, n){
  var vals=arr.map(function(d){return d[field];}).filter(function(x){return x!=null;}).slice(-n);
  return vals.length ? vals.reduce(function(s,x){return s+x;},0)/vals.length : null;
}

// ── RENDER INSIGHTS ──
// Roadmap P0 paso 4: top-1 (mayor severidad, orden ya viene del server) +
// control "N más" que expande/colapsa el resto (toggle SOLO en memoria, no
// persiste entre renders/recargas). Dedup en cliente ANTES de render:
//   (a) recommendation idéntica (===, ya normalizada del server) → la card
//       repetida omite su .insight-rec (no se descarta la card completa,
//       solo el consejo duplicado — sigue sumando al conteo de "N más").
//   (b) ins.summary (trim) === headline del Coach (COACH.headline, trim) →
//       se salta ese insight por completo y NO cuenta para "N más" (ya se
//       está mostrando arriba, en la card del Coach).
var _insightsExpanded = false;
function renderInsights(){
  var container = document.getElementById('insightCards');
  if(!container) return;
  var list = (typeof INSIGHTS !== 'undefined' && Array.isArray(INSIGHTS)) ? INSIGHTS : [];

  // (b) descartar insights cuyo summary duplique el headline del Coach.
  var coachHeadline = ((typeof COACH !== 'undefined' && COACH && COACH.headline) || '').trim();
  if(coachHeadline){
    list = list.filter(function(ins){
      return (ins.summary || '').trim() !== coachHeadline;
    });
  }

  if(!list.length){
    container.innerHTML = '<div class="insight-all-ok">🙂 <span>'+escHtml(t('insights_all_ok'))+'</span></div>';
    return;
  }

  // (a) recommendation duplicada → se omite el .insight-rec de las repetidas
  // (no la card entera: title/summary/factors se siguen mostrando).
  var seenRecs = {};
  list.forEach(function(ins){
    var rec = (ins.recommendation || '').trim();
    if(rec){
      if(seenRecs[rec]) ins._recDup = true;
      seenRecs[rec] = true;
    }
  });

  var SEV_COLOR = {alert:'#FF375F', watch:'#FF9F0A', positive:'#30D158', info:'var(--label2)'};
  function cardHtml(ins){
    var factors = (ins.factors||[]).map(function(f){
      return '<span class="insight-factor">'+escHtml(f)+'</span>';
    }).join('');
    return '<div class="insight-card" data-sev="'+ins.severity+'">'
      + '<div class="insight-header">'
      +   '<span class="insight-icon">'+ins.icon+'</span>'
      +   '<span class="insight-title">'+escHtml(ins.title)+'</span>'
      +   '<span class="insight-sev-dot"></span>'
      + '</div>'
      + '<div class="insight-summary">'+escHtml(ins.summary)+'</div>'
      + (factors ? '<div class="insight-factors">'+factors+'</div>' : '')
      + (ins._recDup ? '' : '<div class="insight-rec">'+escHtml(ins.recommendation)+'</div>')
      + '</div>';
  }

  var top = list[0];
  var rest = list.slice(1);
  var html = cardHtml(top);
  if(rest.length){
    html += '<div class="insight-more-row" onclick="toggleInsightsExpanded()">'
      + (_insightsExpanded
          ? escHtml(t('insights_less'))
          : escHtml(t('insights_more_n').replace('{n}', rest.length)))
      + '</div>';
    if(_insightsExpanded){
      html += '<div id="insightCardsExtra">' + rest.map(cardHtml).join('') + '</div>';
    }
  }
  container.innerHTML = html;
}
function toggleInsightsExpanded(){
  _insightsExpanded = !_insightsExpanded;
  renderInsights();
  // Roadmap P3 fase A: expandir/colapsar "N más" cambia la altura de la
  // tarjeta de insights → recompute (rAF para esperar el reflow del innerHTML).
  if (typeof layoutMasonry === 'function') requestAnimationFrame(function(){ layoutMasonry('hoy'); });
}
function escHtml(s){
  return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// ── Roadmap P1 paso 1: helper "ⓘ detalles" reutilizable ─────────────────────
// Principio: plain-language primero, técnico a un tap — NUNCA se elimina el
// dato técnico, NUNCA se muestra por default. techDetail(id, techHtml) genera
// un trigger "ⓘ" + un cuerpo colapsado (ya escapado por el caller si aplica)
// que se expande in-place al tocarlo. `id` debe ser único en la card que lo usa.
var _techDetailSeq = 0;
function techDetail(idHint, techHtml){
  var id = 'td_' + (idHint || '') + '_' + (_techDetailSeq++);
  return '<span class="techdetail-trigger" onclick="toggleTechDetail(\''+id+'\')" role="button" aria-label="'+escHtml(t('techdetail_al'))+'">ⓘ</span>'
    + '<div class="techdetail-body" id="'+id+'">'+techHtml+'</div>';
}
function toggleTechDetail(id){
  var el = document.getElementById(id);
  if(!el) return;
  el.classList.toggle('open');
}

// ── C2 (Fase 8C AAA feel): toast de error con reintentar, reutilizable ──────
// showRetryToast(fn): pinta un toast flotante ("Error de red — Reintentar")
// cerca de la tab bar; al tocar "Reintentar" llama fn() y oculta el toast.
// Un solo toast activo a la vez (llamadas repetidas reemplazan el mensaje/fn
// anterior, no apilan). Se auto-oculta a los 8s si el usuario no interactúa.
var _retryToastTimer = null;
function showRetryToast(fn, message) {
  var host = document.getElementById('retryToastHost');
  if (!host) {
    host = document.createElement('div');
    host.id = 'retryToastHost';
    document.body.appendChild(host);
  }
  var msg = message || (typeof t === 'function' ? t('err_net') : 'Error de red');
  host.innerHTML = '<span class="retry-toast-msg">' + escHtml(msg) + '</span>'
    + '<span class="retry-toast-btn" id="retryToastBtn">' + escHtml(typeof t === 'function' ? t('retry_btn') : 'Reintentar') + '</span>';
  host.classList.add('visible');

  var btn = document.getElementById('retryToastBtn');
  if (btn) {
    btn.onclick = function () {
      hideRetryToast();
      if (typeof fn === 'function') fn();
    };
  }

  if (_retryToastTimer) clearTimeout(_retryToastTimer);
  _retryToastTimer = setTimeout(hideRetryToast, 8000);
}
function hideRetryToast() {
  var host = document.getElementById('retryToastHost');
  if (host) host.classList.remove('visible');
  if (_retryToastTimer) { clearTimeout(_retryToastTimer); _retryToastTimer = null; }
}

// ── FASE 7: CICLO (salud femenina, opt-in) ──────────────────────────────────

// Re-consulta GET /api/cycle (usado tras togglear en Más, sin recargar toda la app).
function fetchCycleState(){
  fetch('/api/cycle')
    .then(function(r){ return r.json(); })
    .then(function(data){
      CYCLE = data || {enabled:false};
      renderCycleCard();
    })
    .catch(function(){ /* sin conexión: deja CYCLE como estaba, no rompe la UI */ });
}

// Tarjeta compacta en "Hoy". Oculta por completo si CYCLE.enabled es falso
// (criterio #1: cero fuga con el toggle apagado — ni siquiera el contenedor
// se muestra, no solo el contenido).
function renderCycleCard(){
  var card = document.getElementById('cycleCard');
  var body = document.getElementById('cycleCardBody');
  if (!card || !body) return;

  if (!CYCLE || !CYCLE.enabled) {
    card.style.display = 'none';
    body.innerHTML = '';
    return;
  }
  card.style.display = '';

  if (CYCLE.cycle_day == null) {
    // Sin ningún periodo registrado todavía: invita a registrar.
    body.innerHTML = '<div class="mas-row-sub" style="margin-top:6px">' + t('cycle_card_no_data') + '</div>';
    return;
  }

  var phaseLabel = t('phase_' + CYCLE.phase) || CYCLE.phase || '—';
  var daysUntil = CYCLE.period && CYCLE.period.days_until;
  var html = '<div style="display:flex;align-items:baseline;gap:8px;margin-top:6px">'
    + '<span style="font:600 22px -apple-system;color:var(--label)">' + t('cycle_card_day').replace('{n}', CYCLE.cycle_day) + '</span>'
    + '<span style="font:500 13px -apple-system;color:var(--label3);text-transform:capitalize">' + escHtml(phaseLabel) + '</span>'
    + '</div>';

  if (daysUntil != null) {
    var countdownTxt = daysUntil <= 0
      ? t('cycle_card_period_now')
      : t('cycle_card_countdown').replace('{n}', daysUntil);
    html += '<div class="mas-row-sub" style="margin-top:4px">' + escHtml(countdownTxt) + '</div>';
  }

  if (CYCLE.fertile_window) {
    html += '<div style="margin-top:8px;display:inline-block;padding:4px 10px;border-radius:999px;background:rgba(255,55,95,.12);color:#FF375F;font:600 11px -apple-system">'
      + '🌸 ' + escHtml(t('cycle_card_fertile_chip')) + '</div>';
  }

  if (CYCLE.delay && CYCLE.delay.is_delayed) {
    html += '<div style="margin-top:8px;font:500 12px -apple-system;color:#FF9F0A">⏳ '
      + escHtml(t('cycle_delay_summary').replace('{n_days}', CYCLE.delay.days)) + '</div>';
  }

  body.innerHTML = html;
}

// Deep screen de ciclo (overlay, mismo patrón que sleep/fitness/vitals —
// Fase 3.5). Timeline, calendario de periodos, síntomas, predicciones, panel
// peri/meno y el DISCLAIMER siempre visible.
// Nota roadmap P2 paso 3: el grid 2-up de #deepMetricList es para los 3 drill-downs
// citados (Sueño/Fitness/Vitals), de valor+sparkline angostos. El de Ciclo (Fase 7,
// opt-in) comparte el mismo contenedor pero su contenido es texto largo (fechas,
// ventana fértil, formulario, disclaimer) — cada _deepCard aquí lleva spanAll:true
// para conservar 1 columna como antes (fuera de alcance de P2, cero regresión).
function _renderCycleDeep(){
  var wrap = document.getElementById('deepMetricList');
  if (!wrap) return;

  if (!CYCLE || !CYCLE.enabled) {
    wrap.innerHTML = '<div class="mas-row-sub">' + t('cycle_card_no_data') + '</div>';
    return;
  }

  var html = '';

  if (CYCLE.cycle_day != null) {
    html += _deepSection('cycle_section_estado');
    var phaseLabel = t('phase_' + CYCLE.phase) || CYCLE.phase || '—';
    html += _deepCard({
      lbl: t('cycle_card_day').replace('{n}', CYCLE.cycle_day),
      val: escHtml(phaseLabel), unit: '', sub: '', status: 'in_range', color: '#FF375F', sparkVals: null, spanAll:true
    });

    if (CYCLE.period) {
      var pconf = CYCLE.period.confidence || 'low';
      html += _deepCard({
        lbl: t('cycle_deep_next_period'), val: CYCLE.period.predicted_next || '—', unit: '',
        sub: t('cycle_deep_confidence') + ': ' + t('cycle_conf_' + pconf),
        status: 'in_range', color: '#FF9F0A', sparkVals: null, spanAll:true
      });
    }

    if (CYCLE.fertile_window) {
      html += _deepCard({
        lbl: t('cycle_deep_fertile_window'),
        val: CYCLE.fertile_window.start + ' → ' + CYCLE.fertile_window.end, unit: '',
        sub: t('cycle_deep_ovulation_est') + ': ' + (CYCLE.fertile_window.ovulation_est || '—'),
        status: 'in_range', color: '#BF5AF2', sparkVals: null, spanAll:true
      });
    }

    if (CYCLE.delay && CYCLE.delay.is_delayed) {
      html += _deepCard({
        lbl: t('cycle_delay_title'), val: String(CYCLE.delay.days), unit: t('du_days'),
        sub: '', status: 'high', color: '#FF9F0A', sparkVals: null, spanAll:true
      });
    }
  } else {
    html += '<div class="mas-row-sub deep-span-all" style="padding:8px 0">' + t('cycle_card_no_data') + '</div>';
  }

  // Peri/menopausia — solo se muestra si NO es insufficient_history (cero
  // falsos positivos / cero alarmismo con historial corto, criterio #6).
  if (CYCLE.menopause && CYCLE.menopause.stage && CYCLE.menopause.stage !== 'insufficient_history'
      && CYCLE.menopause.stage !== 'premenopausal') {
    html += _deepSection('cycle_section_meno');
    html += _deepCard({
      lbl: t(CYCLE.menopause.stage + '_title'), val: '', unit: '',
      sub: t(CYCLE.menopause.stage + '_summary'),
      status: 'high', color: '#FF9F0A', sparkVals: null, spanAll:true
    });
  }

  // Registrar periodo (formulario mínimo inline)
  html += _deepSection('cycle_section_log');
  html += '<div class="mas-sync-card deep-span-all" style="margin-top:6px">'
    + '<div class="mas-row-label" style="margin-bottom:8px">' + t('cycle_deep_log_period') + '</div>'
    + '<input type="date" id="cycleLogStart" style="width:100%;padding:10px;border-radius:10px;border:1px solid var(--sep);background:var(--bg2);color:var(--label);margin-bottom:8px">'
    + '<div id="cycleLogBtn" onclick="_submitCyclePeriod()" style="text-align:center;padding:10px;border-radius:10px;background:#FF375F;color:#fff;font:600 14px -apple-system;cursor:pointer">'
    + t('cycle_deep_save') + '</div>'
    + '</div>';

  // Disclaimer — SIEMPRE visible en la pantalla de detalle (criterio #7).
  html += '<div class="mas-row-sub deep-span-all" style="margin-top:16px;line-height:1.5;padding:0 4px">' + escHtml(CYCLE.disclaimer ? t('cycle_disclaimer') : t('cycle_disclaimer')) + '</div>';

  wrap.innerHTML = html;
}

function _submitCyclePeriod(){
  var input = document.getElementById('cycleLogStart');
  if (!input || !input.value) return;
  fetch('/api/cycle/period', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({start: input.value})
  })
    .then(function(r){ return r.json(); })
    .then(function(){
      fetchCycleState();
      _renderCycleDeep();
    })
    .catch(function(){ /* red caída: no rompe la UI, el usuario puede reintentar */ });
}

// ── RENDER HOY ──
function renderHoy(){
  var day = days[sel] || {};

  // Insight cards (solo se renderizan una vez; no dependen del día seleccionado)
  renderInsights();

  // Fase 7: tarjeta de ciclo (solo visible si CYCLE.enabled — toggle opt-in).
  renderCycleCard();

  // Fecha
  document.getElementById('todayDate').textContent = fmtDateES(day.date);
  document.getElementById('scrubDate').textContent = day.date || '—';
  document.getElementById('btnPrev').disabled = sel <= 0;
  document.getElementById('btnNext').disabled = sel >= days.length-1;

  // RINGS
  var rec = day.recovery != null ? day.recovery : 0;
  var sleepPct = day.sleep_perf != null ? day.sleep_perf : 0;
  var strain = day.strain != null ? day.strain : 0;
  var strainFrac = clamp(strain/21,0,1);

  document.getElementById('ringWrap').innerHTML = ringsSVG(rec, sleepPct, strainFrac);

  // recovery label
  document.getElementById('recVal').innerHTML = (day.recovery!=null ? day.recovery : '—') + '<span class="ring-sub" style="margin-left:1px">%</span>';

  // recovery_n: confianza de señales
  (function(){
    var rn = day.recovery_n;
    var confEl = document.getElementById('recConf');
    if(!confEl) return;
    if(rn == null){ confEl.textContent = ''; return; }
    var txt = rn >= 3 ? t('recovery_n_3') : (rn === 2 ? t('recovery_n_2') : t('recovery_n_hrv'));
    confEl.textContent = txt;
  })();

  // sleep label: show Xh Ym from asleep minutes
  var sleepMin = day.asleep;
  if(sleepMin != null){
    var sh=Math.floor(sleepMin/60), sm=sleepMin%60;
    var sleepStr=sh+'h '+(sm<10?'0':'')+sm+'m';
    document.getElementById('sleepVal').innerHTML = sleepStr + '<span class="ring-sub" id="sleepPctSpan" style="margin-left:5px">'+(sleepPct||'')+'%</span>';
  } else {
    document.getElementById('sleepVal').innerHTML = '—';
  }

  // strain
  document.getElementById('strainVal').innerHTML = (day.strain!=null?day.strain.toFixed(1):'—') + '<span class="ring-sub" style="margin-left:2px">/ 21</span>';

  // ── EDAD CORPORAL ──
  document.getElementById('fitnessAge').textContent = bodyage.fitness_age != null ? bodyage.fitness_age : '—';
  // Roadmap edad-corporal-estable: el número grande de Hoy pasa a ser el
  // ESTABLE (media rodante de 30 días cerrados, compute_body_age_stable en
  // app/bodyage.py) — deja de saltar a diario. Fallback al instantáneo si el
  // backend no lo pudo poblar (datasets viejos antes de este cambio, o el
  // best-effort de sync.py falló).
  var bodyAgeMain = bodyage.body_age_stable != null ? bodyage.body_age_stable : bodyage.body_age;
  document.getElementById('bodyAge').textContent = bodyAgeMain != null ? bodyAgeMain : '—';
  var ba = bodyage;

  // ── Ritmo de envejecimiento (pace) ──────────────────────────────────────
  // Reutiliza el `pace` ya calculado por app/healthspan.py (motor de
  // Tendencias, #tendHealthspanCard) — sync.py lo copia a summary.bodyage.pace.
  // Este bloque NO reinventa el motor, solo lo muestra también en Hoy junto
  // al número estable (roadmap edad-corporal-estable, paso 4). Si pace es
  // null (historial <120 días) el bloque se oculta limpio, nunca "—x" roto.
  (function(){
    var paceRow = document.getElementById('baPaceRow');
    if(!paceRow) return;
    var pace = ba.pace;
    if(pace == null){
      paceRow.style.display = 'none';
      return;
    }
    paceRow.style.display = '';
    document.getElementById('baPaceVal').textContent = pace.toFixed(2) + 'x';
    // _healthspanPaceNote vive en healthspan.js (mismos umbrales 0.9/1.1) —
    // se reutiliza tal cual para no duplicar el criterio en dos archivos.
    var note = (typeof _healthspanPaceNote === 'function') ? _healthspanPaceNote(pace) : '';
    document.getElementById('baPaceNote').textContent = note;
  })();
  // baDetail: base info + percentil + label — SOLO se emite un segmento si
  // tiene dato real (roadmap P0 paso 5: nunca "()" vacíos ni "—" sueltos
  // colgando de un join). Cada segmento se arma completo o se omite entero.
  (function(){
    var segs = [];
    if(ba.vo2max != null){
      segs.push(t('vo2max_lbl')+' <strong>'+ba.vo2max+'</strong>'+(ba.category ? ' ('+ba.category+')' : ''));
    }
    if(ba.age != null){
      segs.push(t('ba_detail_real')+' <strong>'+ba.age+'</strong>');
    }
    var rhrVal = ba.rhr || summary.rhr_base;
    if(rhrVal != null){
      segs.push(t('ba_detail_rhr')+' '+rhrVal);
    }
    var hrvVal = ba.hrv || summary.hrv_base;
    if(hrvVal != null){
      segs.push(t('ba_detail_hrv')+' '+hrvVal);
    }
    if(ba.sleep_h != null){
      segs.push(t('ba_detail_sleep')+' '+ba.sleep_h+'h');
    }
    if(ba.vo2max_percentile != null){
      segs.push(t('ba_detail_percentil')+' <strong>'+ba.vo2max_percentile+'</strong>'+(ba.vo2max_label ? ' ('+ba.vo2max_label+')' : ''));
    }
    document.getElementById('baDetail').innerHTML = segs.join(' · ');
  })();

  // badge de confianza en bodyAgeCard
  (function(){
    var badgeEl = document.getElementById('bodyAgeBadge');
    if(!badgeEl) return;
    var conf = ba.confidence && ba.confidence.level;
    if(!conf){ badgeEl.textContent=''; badgeEl.style.display='none'; return; }
    var colors = {high:'#30D158', medium:'#FF9F0A', low:'#FF375F'};
    var labels = {high:t('conf_high'), medium:t('conf_medium'), low:t('conf_low')};
    badgeEl.textContent = t('conf_prefix') + (labels[conf]||conf);
    badgeEl.style.color = colors[conf]||'#8E8E93';
    badgeEl.style.display = '';
  })();

  if(ba.penalty && ba.penalty > 0){
    document.getElementById('baPenalty').textContent = t('penalty_txt').replace('{n}', ba.penalty);
    document.getElementById('baPenalty').style.display = '';
  } else {
    document.getElementById('baPenalty').style.display = 'none';
  }

  // ── MONITOR DE SALUD ──
  renderHealthMonitor(day);

  // ── PASOS ──
  renderSteps(day);

  // ── RESUMEN DE SUEÑO (roadmap tab Sueño, paso 6) ──
  // bedCard/renderBedtime/renderSleepCoach se MOVIERON al bloque Bedtime
  // dentro de _buildSleepContent (ver _populateBedtimeSection); Hoy ahora
  // solo muestra la card-resumen compacta y tappable.
  renderSleepSummaryCard();

  // ── CARGA 7 DÍAS ──
  renderLoad();

  // ── ELECTROCARDIOGRAMAS (visor independiente, fetch propio — no viene en DB) ──
  refreshEcgCard();

  // Roadmap P3 Fase A: recomputa masonry — nuevos datos pueden cambiar
  // alturas de tarjetas (recovery/insights/etc). No-op si <900px.
  layoutMasonry('hoy');
}

function renderHealthMonitor(today){
  // 5 métricas: FCR, HRV, Resp, SpO2, Temp piel
  var daysSlice14 = days.slice(-14);
  var monitor = [
    {key:'rhr',   name:t('hm_rhr'),  unit:'',    field:'rhr',       col:A.orange, icon:'heart', betterDir:'low'},
    {key:'hrv',   name:t('hm_hrv'),  unit:'ms',  field:'hrv',       col:A.green,  icon:'wave',  betterDir:'high'},
    {key:'resp',  name:t('hm_resp'), unit:'rpm', field:'resp',      col:A.teal,   icon:'wind',  betterDir:'stable'},
    {key:'spo2',  name:t('hm_spo2'), unit:'%',   field:'spo2',      col:A.cyan,   icon:'drop',  betterDir:'high'},
    {key:'temp',  name:t('hm_temp'), unit:'°',   field:'skin_temp', col:A.orange, icon:'thermo',betterDir:'stable'},
  ];

  // bases del summary (si existen)
  var bases = {
    rhr: summary.rhr_base,
    hrv: summary.hrv_base,
    resp: null,
    spo2: null,
    temp: null,
  };

  var html = '';
  monitor.forEach(function(m){
    // 3d avg
    var recent3 = days.slice(-3).map(function(d){return d[m.field];}).filter(function(x){return x!=null;});
    var avg3 = recent3.length ? recent3.reduce(function(s,x){return s+x;},0)/recent3.length : null;
    // base 30d
    var base30 = avgField(days, m.field, 30);
    var base = bases[m.key] || base30;

    // sparkline data (last 14d)
    var sparkData = daysSlice14.map(function(d){return d[m.field];}).filter(function(x){return x!=null;});

    // value to display (today's value or 3d avg)
    var todayVal = today[m.field];
    var displayVal = todayVal != null ? todayVal : avg3;
    var valStr = displayVal != null ? (Number.isInteger(displayVal) ? displayVal : displayVal.toFixed(1)) : '—';

    // trend dot color — roadmap P0 paso 5: sin dato ES neutro gris, NUNCA
    // verde por default (antes: '#30D158' inicial se quedaba puesto si
    // avg3/base faltaban, dando falsa sensación de "todo bien").
    var trendColor = 'var(--label3)';
    if(displayVal == null){
      trendColor = 'var(--label3)'; // sin dato hoy/3d → neutro, explícito
    } else if(avg3 != null && base != null){
      var delta = avg3 - base;
      var pct = Math.abs(delta)/Math.max(Math.abs(base),0.1)*100;
      if(m.betterDir === 'low'){
        trendColor = delta < -2 ? '#30D158' : (delta > 3 ? '#FF375F' : '#FF9F0A');
      } else if(m.betterDir === 'high'){
        trendColor = delta > 2 ? '#30D158' : (delta < -3 ? '#FF375F' : '#FF9F0A');
      } else {
        // stable: within 5% is green
        trendColor = pct < 5 ? '#30D158' : (pct > 12 ? '#FF375F' : '#FF9F0A');
      }
    } else {
      // hay valor de hoy pero no hay base/promedio para comparar tendencia
      trendColor = 'var(--label3)';
    }

    var baseStr = base != null ? t('hm_base_prefix')+(typeof base==='number'?base.toFixed(base<10?2:1):base)+m.unit : '';

    // dot no-data: trendColor es una CSS var (no hex) → hexA() no aplica;
    // se estiliza vía clase .dot-nodata (sin glow, roadmap P0 paso 5).
    var isNoData = trendColor === 'var(--label3)';
    var dotHtml = isNoData
      ? '<span class="trend-dot dot-nodata"></span>'
      : '<span class="trend-dot" style="background:'+trendColor+';box-shadow:0 0 7px '+hexA(trendColor,.55)+'"></span>';

    html += '<div class="hm-row">'
      +'<div class="hm-icon" style="background:'+hexA(m.col,.16)+'">'+icon(m.icon, m.col, 18)+'</div>'
      +'<div class="hm-info"><div class="hm-name">'+m.name+'</div><div class="hm-base">'+baseStr+'</div></div>'
      +'<div class="hm-spark">'+spark(sparkData, m.col)+'</div>'
      +'<div class="hm-val-wrap"><span class="hm-val">'+valStr+'</span><span class="hm-unit">'+m.unit+'</span></div>'
      +dotHtml
      +'</div>';
  });
  document.getElementById('healthRows').innerHTML = html;
}

// Bedtime logic — exact port of _hm + renderBedtime from vitals_premium_template
function renderSteps(day){
  var valEl = document.getElementById('stepsVal');
  var fillEl = document.getElementById('stepsFill');
  var subEl = document.getElementById('stepsSub');
  var sparkEl = document.getElementById('stepsSpark');
  if(!valEl || !fillEl || !subEl) return; // guard: tarjeta oculta/no en DOM no rompe renderHoy

  var target = (PROFILE && PROFILE.steps_target) || 8000;
  var steps = day && day.steps != null ? day.steps : null;

  if(steps == null){
    valEl.textContent = '—';
    fillEl.style.width = '0%';
    fillEl.style.background = 'var(--label3)';
    subEl.textContent = t('steps_no_data');
  } else {
    var loc = (PROFILE && PROFILE.locale) || 'es';
    // useGrouping:'always' fuerza el separador de miles aun bajo 10.000 (el
    // default de V8 en 'es'/'es-ES' NO agrupa 4 dígitos → "7053"); conserva el
    // separador nativo de cada idioma (es "." · en "," · fr " " · pt ".").
    var nf; try { nf = new Intl.NumberFormat(loc, {useGrouping:'always', maximumFractionDigits:0}); } catch(e){ nf = null; }
    var fmt = nf ? function(n){ return nf.format(n); } : function(n){ return n.toLocaleString(loc); };
    valEl.textContent = fmt(steps);
    var pct = clamp(target > 0 ? (steps/target*100) : 0, 0, 100);
    fillEl.style.width = pct + '%';
    var met = steps >= target;
    fillEl.style.background = met ? A.green : A.orange;
    var pctRounded = Math.round(pct);
    var goalStr = t('steps_goal_fmt').replace('{n}', fmt(target));
    subEl.textContent = met ? (goalStr + ' · ' + t('steps_goal_met')) : (goalStr + ' · ' + pctRounded + '%');
  }

  if(sparkEl){
    var last7 = days.slice(-7).map(function(d){ return d.steps != null ? d.steps : null; });
    var vals = last7.filter(function(v){ return v != null; });
    sparkEl.innerHTML = vals.length >= 2 ? spark(vals, A.orange) : '';
  }
}

// ── ELECTROCARDIOGRAMAS ──────────────────────────────────────────────────────
// VISOR INDEPENDIENTE: solo lee GET/POST /api/ecg. Nunca toca `days`/`summary`/
// DB (el dataset del motor) — los voltajes viven aparte, aislados a propósito.

var ECG_CLASS_KEYS = {
  sinusRhythm: 'ecg_class_sinus',
  atrialFibrillation: 'ecg_class_afib',
  inconclusiveLowHeartRate: 'ecg_class_inconclusive',
  inconclusiveHighHeartRate: 'ecg_class_inconclusive',
  inconclusivePoorReading: 'ecg_class_inconclusive',
  inconclusiveOther: 'ecg_class_inconclusive',
  inconclusive: 'ecg_class_inconclusive',
  unreadable: 'ecg_class_unreadable',
  notSet: 'ecg_class_unreadable'
};

function ecgClassLabel(cls){
  var key = ECG_CLASS_KEYS[cls] || null;
  return key ? t(key) : (cls || t('ecg_class_unreadable'));
}

function ecgBadgeClass(cls){
  if(cls === 'sinusRhythm') return 'sinus';
  if(cls === 'atrialFibrillation') return 'afib';
  if(cls && cls.indexOf('inconclusive') === 0) return 'inconclusive';
  return 'other';
}

function ecgFmtDate(iso){
  if(!iso) return '—';
  try{
    var d = new Date(iso);
    if(isNaN(d.getTime())) return iso;
    var loc = (PROFILE && PROFILE.locale) || 'es';
    return d.toLocaleString(loc, {year:'numeric', month:'short', day:'numeric', hour:'2-digit', minute:'2-digit'});
  }catch(e){ return iso; }
}

var _ecgListCache = [];

function refreshEcgCard(){
  var card = document.getElementById('ecgCard');
  var body = document.getElementById('ecgCardBody');
  if(!card || !body) return;
  fetch('/api/ecg')
    .then(function(r){ return r.json(); })
    .then(function(list){
      _ecgListCache = Array.isArray(list) ? list : [];
      card.style.display = '';
      if(!_ecgListCache.length){
        body.innerHTML = '<div class="ecg-empty">' + t('ecg_no_readings') + '</div>';
        return;
      }
      var summary = t('ecg_count_fmt').replace('{n}', _ecgListCache.length);
      var mostRecent = _ecgListCache[0];
      body.innerHTML =
        '<div class="ecg-row" onclick="openEcgListModal()">'
        + '<div class="ecg-row-main">'
        +   '<div class="ecg-row-date">' + summary + '</div>'
        +   '<div class="ecg-row-sub">' + t('ecg_most_recent_prefix') + ' ' + ecgFmtDate(mostRecent.date) + '</div>'
        + '</div>'
        + '<span class="ecg-badge ' + ecgBadgeClass(mostRecent.classification) + '">' + ecgClassLabel(mostRecent.classification) + '</span>'
        + '</div>';
    })
    .catch(function(){
      // Sin conexión / endpoint no disponible: card oculta, nunca rota (elegante).
      card.style.display = 'none';
    });
}

function openEcgListModal(){
  var modal = document.getElementById('ecgListModal');
  var listBody = document.getElementById('ecgListModalBody');
  if(!modal || !listBody) return;
  // El modal vive fuera del <script> que corrió applyI18n() en INIT (igual que
  // profileModal/onboardOverlay) -> re-aplicar aquí para que el título traduzca
  // la primera vez que se abre, sin depender de un refresh previo de idioma.
  applyI18n();
  if(!_ecgListCache.length){
    listBody.innerHTML = '<div class="ecg-empty">' + t('ecg_no_readings') + '</div>';
  } else {
    var html = '<div class="ecg-row-list">';
    _ecgListCache.forEach(function(item){
      var hr = item.avg_hr != null ? Math.round(item.avg_hr) + ' ' + t('ecg_bpm') : '';
      html += '<div class="ecg-row" onclick="openEcgViewer(\'' + String(item.uuid).replace(/'/g,"\\'") + '\')">'
        + '<div class="ecg-row-main">'
        +   '<div class="ecg-row-date">' + ecgFmtDate(item.date) + '</div>'
        +   '<div class="ecg-row-sub">' + hr + '</div>'
        + '</div>'
        + '<span class="ecg-badge ' + ecgBadgeClass(item.classification) + '">' + ecgClassLabel(item.classification) + '</span>'
        + '<svg class="ecg-chevron" viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 6l6 6-6 6"/></svg>'
        + '</div>';
    });
    html += '</div>';
    listBody.innerHTML = html;
  }
  modal.classList.remove('hidden');
}

function closeEcgListModal(){
  var modal = document.getElementById('ecgListModal');
  if(modal) modal.classList.add('hidden');
}

function closeEcgViewer(){
  var modal = document.getElementById('ecgViewerModal');
  if(modal) modal.classList.add('hidden');
}

function openEcgViewer(uuid){
  var modal = document.getElementById('ecgViewerModal');
  var header = document.getElementById('ecgViewerHeader');
  var stripWrap = document.getElementById('ecgViewerStripWrap');
  if(!modal || !header || !stripWrap) return;
  // Mismo motivo que openEcgListModal(): el back-button y el disclaimer estático
  // (data-i18n) viven fuera del <script> de INIT -> re-aplicar aquí.
  applyI18n();

  header.innerHTML = '<div class="ecg-vh-class">' + t('ecg_loading') + '</div>';
  stripWrap.innerHTML = '';
  modal.classList.remove('hidden');

  fetch('/api/ecg/' + encodeURIComponent(uuid))
    .then(function(r){
      if(!r.ok) throw new Error('not found');
      return r.json();
    })
    .then(function(meta){
      var freqStr = meta.sampling_frequency ? (meta.sampling_frequency + ' Hz') : '—';
      var hrStr = meta.avg_hr != null ? Math.round(meta.avg_hr) + ' ' + t('ecg_bpm') : '—';
      header.innerHTML =
        '<div class="ecg-vh-class">' + ecgClassLabel(meta.classification) + '</div>'
        + '<div class="ecg-vh-meta">' + ecgFmtDate(meta.date) + ' · ' + hrStr + ' · ' + t('ecg_sampling_prefix') + ' ' + freqStr + '</div>';
      stripWrap.innerHTML = renderEcgStrip(meta, meta.voltages);
    })
    .catch(function(){
      header.innerHTML = '<div class="ecg-vh-class">' + t('ecg_class_unreadable') + '</div>';
      stripWrap.innerHTML = '<div class="ecg-strip-empty">' + t('ecg_load_error') + '</div>';
    });
}

/**
 * renderEcgStrip(meta, voltages) — dibuja la tira de ECG como SVG.
 *
 * Escala médica estándar: 25 mm/s (tiempo) y 10 mm/mV (amplitud). Cuadrícula
 * milimetrada clásica: líneas finas cada 1mm, gruesas cada 5mm, rosa/rojo.
 * 1 "mm" de la tira = 4 px en pantalla (px_per_mm) — factor de escala visual,
 * NO cambia la relación 25mm/s·10mm/mV, solo el tamaño físico del render.
 *
 * None-safe: sin voltajes / sampling_frequency, o con <2 puntos útiles -> mensaje
 * amable (ecg-strip-empty), nunca un <path> roto ni "NaN" en el atributo `d`.
 */
function renderEcgStrip(meta, voltages){
  var PX_PER_MM = 4;
  var MM_PER_SEC = 25;   // escala de tiempo estándar
  var MM_PER_MV = 10;    // escala de amplitud estándar

  if(!Array.isArray(voltages) || voltages.length < 2){
    return '<div class="ecg-strip-empty">' + t('ecg_no_waveform') + '</div>';
  }

  var freq = Number(meta && meta.sampling_frequency);
  if(!freq || !isFinite(freq) || freq <= 0) freq = 512; // fallback razonable si el nativo no la mandó

  // Filtrar a números finitos; si TODO es basura, mensaje amable (no NaN en el path).
  var clean = voltages.map(function(v){
    var n = Number(v);
    return isFinite(n) ? n : null;
  });
  var finiteVals = clean.filter(function(v){ return v != null; });
  if(finiteVals.length < 2){
    return '<div class="ecg-strip-empty">' + t('ecg_no_waveform') + '</div>';
  }

  // µV -> mV
  var mv = clean.map(function(v){ return v == null ? null : v/1000; });

  var minV = Math.min.apply(null, finiteVals) / 1000;
  var maxV = Math.max.apply(null, finiteVals) / 1000;
  // Onda plana (min===max): dar un rango mínimo artificial para que la cuadrícula
  // no colapse a una línea y el trazo se vea centrado.
  if(maxV - minV < 0.05){
    var mid = (maxV + minV) / 2;
    minV = mid - 0.5;
    maxV = mid + 0.5;
  }

  var durationSec = voltages.length / freq;
  var widthMm = durationSec * MM_PER_SEC;
  var ampRangeMm = (maxV - minV) * MM_PER_MV;
  var paddingMm = 5;
  var heightMm = ampRangeMm + paddingMm * 2;

  var widthPx = Math.max(320, Math.round(widthMm * PX_PER_MM));
  var heightPx = Math.max(140, Math.round(heightMm * PX_PER_MM));

  // Coordenadas: x en mm*PX_PER_MM; y invertido (mV alto = arriba).
  function xAt(i){ return (i / freq) * MM_PER_SEC * PX_PER_MM; }
  function yAt(v){
    var vv = v == null ? (minV+maxV)/2 : v;
    var mmFromBottom = (vv - minV) * MM_PER_MV;
    return heightPx - paddingMm*PX_PER_MM - (mmFromBottom * PX_PER_MM);
  }

  // path del trazo — saltar (moveto) tras un hueco null, nunca interpolar NaN.
  var d = '';
  var drawing = false;
  for(var i=0;i<mv.length;i++){
    var v = mv[i];
    if(v == null){ drawing = false; continue; }
    var x = xAt(i), y = yAt(v);
    d += (drawing ? ' L ' : ' M ') + x.toFixed(2) + ' ' + y.toFixed(2);
    drawing = true;
  }

  // Cuadrícula: patrón de 1mm (fino) con líneas gruesas cada 5mm, clásico rosa ECG.
  var mmPx = PX_PER_MM;
  var gridId = 'ecgGrid' + Math.random().toString(36).slice(2,8);
  var gridSvg =
    '<defs>'
    + '<pattern id="' + gridId + 'Small" width="' + mmPx + '" height="' + mmPx + '" patternUnits="userSpaceOnUse">'
    +   '<path d="M ' + mmPx + ' 0 L 0 0 0 ' + mmPx + '" fill="none" stroke="#ff8fa3" stroke-opacity="0.25" stroke-width="0.5"/>'
    + '</pattern>'
    + '<pattern id="' + gridId + 'Big" width="' + (mmPx*5) + '" height="' + (mmPx*5) + '" patternUnits="userSpaceOnUse">'
    +   '<rect width="' + (mmPx*5) + '" height="' + (mmPx*5) + '" fill="url(#' + gridId + 'Small)"/>'
    +   '<path d="M ' + (mmPx*5) + ' 0 L 0 0 0 ' + (mmPx*5) + '" fill="none" stroke="#ff5c7a" stroke-opacity="0.45" stroke-width="1"/>'
    + '</pattern>'
    + '</defs>'
    + '<rect width="100%" height="100%" fill="url(#' + gridId + 'Big)"/>';

  var svg =
    '<svg viewBox="0 0 ' + widthPx + ' ' + heightPx + '" width="' + widthPx + '" height="' + heightPx + '" xmlns="http://www.w3.org/2000/svg">'
    + gridSvg
    + (d ? '<path d="' + d + '" fill="none" stroke="#e6edf3" stroke-width="1.6" stroke-linejoin="round" stroke-linecap="round"/>' : '')
    + '</svg>';

  return svg;
}

// "Tu bedtime reciente: X (delta)" — común a legacy y sleep-coach (roadmap P0 paso 2).
// Devuelve {actStr, gapTxt} usando el mismo promedio de bed_min de los últimos 7 días
// y comparándolo contra btCentered (bedtime recomendado, minutos desde medianoche,
// centrado alrededor de medianoche para que el delta tenga sentido).
function _bedRecentInfo(btCentered){
  var bm=days.filter(function(d){return d.bed_min!=null;}).slice(-7).map(function(d){return d.bed_min;});
  var act=bm.length?Math.round(bm.reduce(function(s,x){return s+x;},0)/bm.length):null;
  var actStr=act!=null?_hm(act):'–';
  var gap=act!=null?Math.round(act-btCentered):null;
  var gapTxt='';
  if(gap!=null){
    if(Math.abs(gap)>20){
      var gapKey = gap>0 ? 'bed_late' : 'bed_early';
      gapTxt=' <span style="color:#FF9F0A">'+t(gapKey).replace('{min}', Math.abs(gap))+'</span>';
    } else {
      gapTxt=' <span style="color:#30D158">'+t('bed_on_target')+'</span>';
    }
  }
  return {actStr:actStr, gapTxt:gapTxt};
}

// ── RESUMEN DE SUEÑO EN HOY (roadmap tab Sueño, paso 6) ─────────────────────
// Card compacta y tappable que reemplaza a bedCard: "Anoche · Xh Ym · score
// N%" (criterio 10 del roadmap). Usa el día seleccionado en Hoy (days[sel],
// mismo scrubber que el resto de las cards de Hoy) — None-safe: sin dato de
// sueño esa noche, muestra el empty state en vez de "—" sueltos.
function renderSleepSummaryCard(){
  var body = document.getElementById('sleepSummaryBody');
  if(!body) return; // guard: card oculta/no en DOM no rompe renderHoy
  var day = days[sel] || {};
  if(day.asleep == null){
    body.innerHTML = '<div class="bed-row">'+t('sleep_summary_empty')+'</div>';
    return;
  }
  var h = Math.floor(day.asleep/60), m = day.asleep%60;
  var durStr = h+'h '+(m<10?'0':'')+m+'m';
  var scoreStr = day.sleep_perf != null ? t('sleep_summary_score').replace('{pct}', day.sleep_perf) : '';
  body.innerHTML =
    '<div class="bed-time-row" style="margin-top:2px">'
      + '<div class="bed-time" style="font-size:34px">'+escHtml(durStr)+'</div>'
    + '</div>'
    + (scoreStr ? '<div class="bed-row" style="margin-top:6px">'+escHtml(scoreStr)+'</div>' : '');
}

// Render legacy completo (fallback si /api/sleep-coach no responde o sin datos).
// Roadmap tab Sueño paso 6: los IDs bedCardLbl/bedTimeRec/bedInfo ahora viven
// DENTRO de #screenSleep (bloque Bedtime de _buildSleepContent), que solo
// existe en el DOM tras la 1ª visita al tab (lazy-render) — None-safe: si el
// tab aún no se montó, esta función no hace nada (se re-popula sola cuando
// _buildSleepContent arma el bloque, ver _populateBedtimeSection).
function renderBedtime(){
  var bedTimeEl = document.getElementById('bedTimeRec');
  if(!bedTimeEl) return; // tab Sueño aún no montado
  var bedLbl = document.getElementById('bedCardLbl');
  if(bedLbl) bedLbl.setAttribute('data-i18n','bed_label'), bedLbl.textContent=t('bed_label');
  var wkDays = days.filter(function(d){return d.waketime;}).slice(-14);
  var wk = wkDays.map(function(d){var p=d.waketime.split(':');return +p[0]*60+ +p[1];});
  if(!wk.length){
    bedTimeEl.textContent='—';
    document.getElementById('bedInfo').innerHTML='<div class="bed-row">'+t('bed_no_data')+'</div>';
    return;
  }
  var wake=Math.round(wk.reduce(function(s,x){return s+x;},0)/wk.length);
  var rec=days.map(function(d){return d.recovery;}).filter(function(x){return x!=null;}).slice(-7);
  var ravg=rec.length?rec.reduce(function(s,x){return s+x;},0)/rec.length:60;
  var need=480+15+(ravg<50?30:0);
  var bt=((wake-need)%1440+1440)%1440;
  bt=Math.floor(bt/30)*30; // redondear a la media hora
  var btCentered=bt>720?bt-1440:bt;
  var recentInfo=_bedRecentInfo(btCentered);
  bedTimeEl.textContent=_hm(bt);
  var wakeExtra = ravg<50 ? t('bed_low_recovery') : '';
  document.getElementById('bedInfo').innerHTML=
    '<div class="bed-row">'+t('bed_wake').replace('{wake}',_hm(wake)).replace('{extra}',wakeExtra)+'</div>'+
    '<div class="bed-row" style="margin-top:6px">'+t('bed_recent')+'<strong>'+recentInfo.actStr+'</strong>'+recentInfo.gapTxt+'</div>';
}

// ── SLEEP COACH (Fase 8C, paso C4) ──────────────────────────────────────────
// Fusionado con bedCard (roadmap P0 paso 2): consume el motor PURO backend
// app/sleep_coach.py, que además considera strain/recovery de hoy y deuda de
// sueño 7d explícita. Progressive enhancement: si el fetch falla o no hay datos
// suficientes, bedCard se queda con el render legacy de renderBedtime() (ya
// pintado antes de este fetch, ver renderHoy()).
var _lastSleepCoachData = null; // cache del último /api/sleep-coach exitoso — re-aplicado
                                 // tras cada renderHoy()/scrub de día (ver renderHoy()),
                                 // pues fetchAndRenderSleepCoach() solo corre 1 vez al inicio.
function fetchAndRenderSleepCoach(){
  fetch('/api/sleep-coach')
    .then(function(r){ return r.json(); })
    .then(function(data){ _lastSleepCoachData = (data && data.available) ? data : null; renderSleepCoach(data); })
    .catch(function(){ /* sin conexión: deja bedCard con el render legacy */ });
}
function renderSleepCoach(data){
  if(!data || !data.available) return; // fallback: bedCard conserva el render legacy
  var bedTimeEl = document.getElementById('bedTimeRec');
  if(!bedTimeEl) return; // tab Sueño aún no montado (roadmap tab Sueño paso 6)
  var bedLbl = document.getElementById('bedCardLbl');
  if(bedLbl){ bedLbl.removeAttribute('data-i18n'); bedLbl.textContent = t('sleep_coach_lbl'); }
  // bedtime/wake_assumed vienen "HH:MM" 24h del backend — convertir a minutos y
  // formatear con _hm() para que use el MISMO formato 12h am/pm que el resto de la app.
  function hhmmToMin(s){
    if(!s || typeof s !== 'string' || s.indexOf(':') === -1) return null;
    var p = s.split(':'); var h = +p[0], m = +p[1];
    if(isNaN(h) || isNaN(m)) return null;
    return h*60+m;
  }
  var bedMin = hhmmToMin(data.bedtime);
  var wakeMin = hhmmToMin(data.wake_assumed);
  bedTimeEl.textContent = bedMin!=null ? _hm(bedMin) : '—';
  var driverTxt = (data.drivers || []).map(function(k){ return t(k); }).filter(Boolean).join(' · ');
  var wakeTxt = t('sleep_coach_wake').replace('{wake}', wakeMin!=null ? _hm(wakeMin) : '—');
  var btCentered = bedMin!=null ? (bedMin>720?bedMin-1440:bedMin) : 0;
  var recentInfo=_bedRecentInfo(btCentered);
  // Roadmap P1 F5 (paso 2): sub-línea "% de tu necesidad (Xh Ym)" — SOLO si
  // hay dato de anoche (today.asleep) y el backend calculó sleep_score. Sin
  // dato -> la línea no aparece (vista intacta, mismo criterio del roadmap).
  var scoreLine = '';
  var lastNight = days.length ? days[days.length-1] : null;
  if (data.sleep_score != null && lastNight && lastNight.asleep != null) {
    var asleepH = Math.floor(lastNight.asleep/60), asleepM = lastNight.asleep%60;
    var durStr = asleepH+'h'+(asleepM ? (asleepM<10?'0':'')+asleepM+'m' : '');
    scoreLine = '<div class="bed-row" style="margin-top:6px">'
      + t('sleep_score_pct_line').replace('{pct}', data.sleep_score).replace('{dur}', durStr)
      + '</div>';
  }
  document.getElementById('bedInfo').innerHTML =
    '<div class="bed-row">' + escHtml(wakeTxt) + '</div>' +
    (driverTxt ? '<div class="bed-row" style="margin-top:6px">' + escHtml(driverTxt) + '</div>' : '') +
    scoreLine +
    '<div class="bed-row" style="margin-top:6px">'+t('bed_recent')+'<strong>'+recentInfo.actStr+'</strong>'+recentInfo.gapTxt+'</div>';
}

// ── PLAN ACTIVO (Roadmap P1, F4, paso 8) ────────────────────────────────────
// Card "Día N de M — tarea de hoy" en el tab Hoy. Sin plan activo -> la card
// completa queda display:none (criterio del roadmap, gate estricto — nada de
// esqueleto/placeholder cuando no hay programa en curso).
function fetchAndRenderPlanCard(){
  fetch('/api/plan')
    .then(function(r){ return r.json(); })
    .then(function(data){ renderPlanCard(data); })
    .catch(function(){ /* sin conexión: la card se queda oculta */ });
}

function renderPlanCard(data){
  var card = document.getElementById('planCard');
  if(!card) return;
  if(!data || !data.active){
    card.style.display = 'none';
    return;
  }
  card.style.display = '';

  var titleEl = document.getElementById('planCardTitle');
  var bodyEl = document.getElementById('planCardBody');
  var actionsEl = document.getElementById('planCardActions');

  var catalogEntry = (window._plansCatalogCache || []).filter(function(p){ return p.id === data.program_id; })[0];
  var programName = catalogEntry ? catalogEntry.name : data.program_id;
  if(titleEl) titleEl.textContent = programName;

  if(data.is_completed){
    bodyEl.innerHTML = '<div class="mas-row-sub" style="margin-top:6px">' + escHtml(t('plan_completed_msg')) + '</div>';
    actionsEl.innerHTML = '';
    return;
  }

  var dayLine = t('plan_day_of').replace('{n}', data.day_number).replace('{m}', data.duration_days);
  var taskLine = '';
  var adaptedBadge = '';
  if(data.today_task){
    taskLine = escHtml(data.today_task.label);
    if(data.today_task.adapted){
      adaptedBadge = '<span style="display:inline-block;margin-left:6px;padding:2px 8px;border-radius:999px;'
        + 'background:rgba(255,159,10,.14);color:#FF9F0A;font:600 10px -apple-system">'
        + escHtml(t('plan_adapted_badge')) + '</span>';
    }
  }
  var adherenceLine = '';
  if(data.adherence_pct != null){
    adherenceLine = '<div class="mas-row-sub" style="margin-top:4px">'
      + escHtml(t('plan_adherence_lbl').replace('{pct}', data.adherence_pct)) + '</div>';
  }

  bodyEl.innerHTML =
    '<div style="font:600 16px/1.3 -apple-system;color:var(--label);margin-top:6px">' + escHtml(dayLine) + '</div>'
    + '<div style="font:500 14px/1.4 -apple-system;color:var(--label2);margin-top:4px">' + taskLine + adaptedBadge + '</div>'
    + adherenceLine;

  var todayStr = (days.length ? days[days.length-1].date : null) || new Date().toISOString().slice(0,10);
  actionsEl.innerHTML = '<button class="plan-done-btn" onclick="planMarkDone(event)" '
    + 'style="padding:8px 16px;border-radius:12px;border:none;background:#30D158;color:#000;'
    + 'font:600 13px -apple-system;cursor:pointer">' + escHtml(t('plan_done_btn')) + '</button>';
}

function planMarkDone(ev){
  if(ev) ev.stopPropagation();
  fetch('/api/plan/check', {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({}),
  })
    .then(function(r){ return r.json(); })
    .then(function(){ fetchAndRenderPlanCard(); })
    .catch(function(){ /* best-effort: sin conexión no se refleja el check */ });
}

function renderLoad(){
  var lastDay = days[sel] || (days.length ? days[days.length-1] : {});
  var lastDate = lastDay.date || '';
  var cutoff = '';
  if(lastDate){
    try{
      var d=new Date(lastDate+'T12:00');d.setDate(d.getDate()-7);
      cutoff=d.toISOString().slice(0,10);
    }catch(e){}
  }
  var rec = cutoff ? exercises.filter(function(e){return e.date>=cutoff;}) : exercises.slice(-20);
  var totMin = Math.round(rec.reduce(function(s,e){return s+(e.dur_min||0);},0));
  var strMin = Math.round(rec.filter(function(e){return /(weight|strength|fuerza)/i.test((e.type||'')+(e.name||''));}).reduce(function(s,e){return s+(e.dur_min||0);},0));
  var vigTotal = days.filter(function(d){return d.date>=cutoff;}).reduce(function(s,d){return s+(d.vigorous||0);},0);

  // ACWR tile
  var acwr = summary.acwr;
  var acwrZone = summary.acwr_zone;
  var acwrVal = acwr != null ? acwr.toFixed(2) : '—';
  var acwrColor = acwr != null ? acwrZoneColor(acwrZone) : 'var(--label3)';
  var acwrLbl = acwr != null ? acwrZoneLabel(acwrZone) : '—';

  // Roadmap P1 paso 1: "Vigorosos Z4–5" -> plain-language "Intensidad alta" con
  // "(zonas 4–5)" como secundario (no técnico-a-un-tap porque ya es corto y
  // el paréntesis es autoexplicativo, a diferencia de ACWR).
  var tiles = [
    {v:rec.length, u:'', l:t('load_sessions'), c:'var(--label)'},
    {v:totMin, u:'min', l:t('load_total'), c:'var(--label)'},
    {v:vigTotal, u:'min', l:t('load_intensity_lbl')+' <span style="font-size:10px;color:var(--label3)">'+t('load_intensity_zones')+'</span>', c:'var(--label)'},
    {v:strMin, u:'min', l:t('load_strength'), c:strMin>0?A.green:A.red},
  ];
  var gridHtml = tiles.map(function(tl){
    return '<div class="load-tile">'
      +'<div style="display:flex;align-items:baseline;gap:3px"><span class="load-val" style="color:'+tl.c+'">'+tl.v+'</span><span class="load-unit">'+tl.u+'</span></div>'
      +'<div class="load-lbl2">'+tl.l+'</div>'
      +'</div>';
  }).join('');
  // 5º tile: Balance de carga (ACWR) — plain-language primero, ACWR y su
  // explicación técnica quedan tras el helper ⓘ (roadmap P1 paso 1).
  var acwrTechHtml = acwr != null
    ? escHtml(t('load_balance_tech').replace('{v}', acwrVal))
    : escHtml(t('load_balance_tech').replace('{v}', '—'));
  gridHtml += '<div class="load-tile">'
    +'<div style="display:flex;align-items:baseline;gap:3px"><span class="load-val" style="color:'+acwrColor+'">'+acwrVal+'</span></div>'
    +'<div class="load-lbl2">'+t('load_balance_lbl')+' <span style="font-size:10px;color:'+acwrColor+'">'+acwrLbl+'</span>'+techDetail('acwr', acwrTechHtml)+'</div>'
    +'</div>';
  document.getElementById('loadGrid').innerHTML = gridHtml;

  // loadWarn: fuerza 0 y/o ACWR fuera de óptimo
  var warnParts = [];
  if(strMin === 0){
    warnParts.push('<div class="load-warn"><svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="#FF9F0A" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="flex:none;margin-top:1px"><path d="M12 9v4M12 17h.01M10.3 3.9 2.4 18a1.9 1.9 0 0 0 1.7 2.8h15.8a1.9 1.9 0 0 0 1.7-2.8L13.7 3.9a1.9 1.9 0 0 0-3.4 0Z"/></svg><div class="load-warn-txt">'+t('load_warn_strength')+'</div></div>');
  }
  if(acwr != null && acwrZone !== 'optimo'){
    var acwrWarnTxt = acwrZone === 'alto'
      ? t('load_acwr_high').replace('{v}', acwrVal)
      : acwrZone === 'precaucion'
        ? t('load_acwr_caution').replace('{v}', acwrVal)
        : t('load_acwr_other').replace('{v}', acwrVal).replace('{lbl}', acwrLbl);
    var acwrStroke = acwrZone === 'alto' ? '#FF375F' : '#FF9F0A';
    warnParts.push('<div class="load-warn"><svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="'+acwrStroke+'" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="flex:none;margin-top:1px"><path d="M12 9v4M12 17h.01M10.3 3.9 2.4 18a1.9 1.9 0 0 0 1.7 2.8h15.8a1.9 1.9 0 0 0 1.7-2.8L13.7 3.9a1.9 1.9 0 0 0-3.4 0Z"/></svg><div class="load-warn-txt">'+acwrWarnTxt+'</div></div>');
  }
  document.getElementById('loadWarn').innerHTML = warnParts.join('');
}

// ── COACH ──
function renderCoach(){
  // COACH is the parsed dict {chips:[...], bullets:[...]}
  var card = COACH || {};
  var chips = card.chips || [];
  var bullets = card.bullets || [];

  // Date of last day
  var lastDay = days[days.length-1] || {};
  var dateStr = lastDay.date || '';
  if(dateStr){
    var p=dateStr.split('-');
    var d=new Date(+p[0],+p[1]-1,+p[2]);
    document.getElementById('coachDate').textContent = t('today_prefix')+d.getDate()+' '+_getMeses()[d.getMonth()];
  }

  // Titular IA (Frescura de Alertas + Coach): va ARRIBA de los chips. Guard:
  // si COACH.headline es vacío/undefined, el nodo queda display:none (sin hueco).
  var headlineEl = document.getElementById('coachHeadline');
  var headline = (card.headline || '').trim();
  if(headline){
    headlineEl.textContent = headline;
    headlineEl.style.display = '';
  } else {
    headlineEl.textContent = '';
    headlineEl.style.display = 'none';
  }

  var chipsHtml = chips.map(function(c){
    return '<span class="chip" style="color:'+c.c+';background:'+c.bg+';border:1px solid '+c.bd+'">'+c.t+'</span>';
  }).join('');
  document.getElementById('coachChips').innerHTML = chipsHtml;

  var bulletsHtml = bullets.map(function(b){
    return '<p class="bullet"><strong>'+b.title+'</strong> '+b.body+'</p>';
  }).join('');
  document.getElementById('coachBullets').innerHTML = bulletsHtml || '<p class="bullet">'+t('coach_metrics_ok')+'</p>';
}

// ── AUTH BANNER (roadmap P2 pasos 4 y 5) ──
// STALE_BANNER_HOURS: umbral de frescura del dataset. Por debajo de esto, un
// token expirado se degrada a un chip discreto ("ya casi no importa, la data
// sigue viva"); por encima (o si no se pudo determinar la antigüedad), se
// mantiene el banner rojo de siempre (fail-safe: ante la duda, se avisa).
var STALE_BANNER_HOURS = 24;

function renderAuth(){
  var reconnectBanner = document.getElementById('reconnectBanner');
  var demoBanner = document.getElementById('demoBanner');
  var chip = document.getElementById('masReconnectChip');
  var dot = document.getElementById('tabMasDot');
  if(!AUTH) return;

  // Paso 5: modo demo — nunca el banner rojo de "acceso expirado" (el token
  // sintético del demo SIEMPRE reporta expired/no_token; roadmap: primera
  // impresión honesta para quien prueba el kit de GitHub, Fase 8A).
  if(AUTH.is_demo){
    if(demoBanner) demoBanner.classList.add('visible');
    return;
  }

  var expired = AUTH.status === 'expired' || AUTH.status === 'no_token';
  if(!expired) return;

  var ageH = AUTH.data_age_hours;
  var isFresh = typeof ageH === 'number' && ageH < STALE_BANNER_HOURS;

  if(isFresh){
    // Data fresca (<24h): suprimir el banner top, chip discreto en Más + dot en el tab.
    if(chip) chip.style.display = '';
    if(dot) dot.classList.add('visible');
  } else {
    // Stale de verdad (≥24h) o antigüedad desconocida: banner rojo sin cambios.
    if(reconnectBanner) reconnectBanner.classList.add('visible');
  }
}

// ── SYNC ──
function doSync(){
  var btn = document.getElementById('syncBtn');
  btn.style.pointerEvents = 'none';
  btn.innerHTML = '<span class="spin-icon">↻</span> ' + t('sync_doing');
  fetch('/api/sync',{method:'POST'}).then(function(r){return r.json();}).then(function(d){
    if(d && d.status==='ok'){btn.innerHTML=t('sync_done');location.reload();return;}
    btn.style.pointerEvents='';
    btn.innerHTML='<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M23 4v6h-6M1 20v-6h6"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg> ' + t('sync_btn');
    if(d && (d.status==='no_token'||d.status==='expired')){
      alert(t('sync_token_alert'));
    } else {
      alert(t('sync_error_alert').replace('{s}', (d&&d.status)||'error'));
    }
  }).catch(function(){
    btn.style.pointerEvents='';
    btn.innerHTML=t('sync_btn');
    alert(t('sync_net_alert'));
  });
}

// ── OFFLINE BANNER (Fase 8C, paso C5) ───────────────────────────────────────
// Detecta si la respuesta de /api/data vino del fallback de caché del service
// worker (header X-From-Cache inyectado en service-worker.js) y muestra un
// banner con la fecha del dataset cacheado. Progressive enhancement: si el
// fetch falla también (offline Y sin nada en caché), no se muestra nada
// distinto — el usuario ya ve los datos SSR de la última carga exitosa.
function _showOfflineBanner(dateStr){
  var banner = document.getElementById('offlineBanner');
  var textEl = document.getElementById('offlineBannerText');
  if(!banner || !textEl) return;
  textEl.textContent = t('offline_banner').replace('{date}', dateStr || '—');
  banner.classList.add('visible');
}
function _checkOfflineStatus(){
  if(!('serviceWorker' in navigator)) return;
  fetch('/api/data', {cache:'no-store'})
    .then(function(r){
      if(r.headers.get('X-From-Cache') === '1'){
        return r.json().then(function(data){
          var dateStr = (data && data.summary && data.summary.updated)
            || (data && data.days && data.days.length ? data.days[data.days.length-1].date : null);
          _showOfflineBanner(dateStr);
        });
      }
    })
    .catch(function(){ /* red y caché ambas fallaron: no hay nada nuevo que mostrar */ });
}
_checkOfflineStatus();

// ── AUTO-SYNC al abrir/recargar ──
// Cada carga del tablero dispara un sync en background; al terminar recarga con datos frescos.
// Guardia anti-loop: tras el sync ponemos un sello en sessionStorage; la recarga post-sync lo
// detecta (reciente) y se salta el sync, consumiendo el sello → la SIGUIENTE recarga vuelve a sync.
function _showSyncInd(show){
  var ind = document.getElementById('autoSyncInd');
  if(!ind){
    ind = document.createElement('div');
    ind.id = 'autoSyncInd';
    ind.style.cssText = 'position:fixed;left:50%;top:calc(env(safe-area-inset-top) + 8px);'+
      'transform:translateX(-50%) translateY(-12px);z-index:300;opacity:0;'+
      'transition:opacity .3s ease, transform .3s ease;pointer-events:none;display:flex;'+
      'align-items:center;gap:7px;padding:7px 14px;border-radius:999px;background:rgba(28,28,32,.85);'+
      'backdrop-filter:blur(20px);-webkit-backdrop-filter:blur(20px);border:1px solid rgba(255,255,255,.1);'+
      'color:rgba(235,235,245,.78);font:600 12px -apple-system';
    ind.innerHTML = '<span class="spin-icon" style="display:inline-block">↻</span> ' + t('sync_doing');
    document.body.appendChild(ind);
  }
  if(show){ ind.style.opacity='1'; ind.style.transform='translateX(-50%) translateY(0)'; }
  else { ind.style.opacity='0'; ind.style.transform='translateX(-50%) translateY(-12px)'; }
}
// Reintentos si otro sync ya está corriendo (típico: el auto-sync nativo de HealthKit
// en sceneDidBecomeActive dispara /api/ingest casi al mismo tiempo que este autoSync();
// con el single-flight lock del servidor, uno de los dos recibe 'already_running' de
// inmediato — sin reintentar, el indicador se apagaba en milisegundos, imperceptible).
// En vez de rendirse, esperamos a que la OTRA sync termine y recargamos con sus datos.
var AUTOSYNC_MAX_RETRIES = 10;
var AUTOSYNC_RETRY_MS = 4000;

function autoSync(){
  try {
    var stamp = sessionStorage.getItem('v_synced_at');
    if(stamp && (Date.now() - parseInt(stamp,10) < 8000)){ sessionStorage.removeItem('v_synced_at'); return; }
  } catch(e){}
  _showSyncInd(true);
  _trySync(0);
}
function _trySync(attempt){
  fetch('/api/sync',{method:'POST'}).then(function(r){return r.json();}).then(function(d){
    if(d && d.status==='ok'){
      try { sessionStorage.setItem('v_synced_at', String(Date.now())); } catch(e){}
      location.reload();
    } else if(d && d.status==='already_running' && attempt < AUTOSYNC_MAX_RETRIES){
      setTimeout(function(){ _trySync(attempt+1); }, AUTOSYNC_RETRY_MS);
    } else {
      _showSyncInd(false);   // token vencido/no_token → banner Reconectar ya sale; auto-sync no molesta
    }
  }).catch(function(){ _showSyncInd(false); });
}

// ── SCRUBBER ──
function scrubBy(delta){
  sel = clamp(sel+delta, 0, days.length-1);
  renderHoy();
}

// ── DETAIL OVERLAY ──
var DETAIL_CFG = {
  recovery:{
    get title(){return t('ring_recovery');}, unit:'%', accent:A.green,
    get chartLabel(){return t('detail_rec_chart');},
    dataFn:function(days){return days.map(function(d){return d.recovery;}).filter(function(v){return v!=null;});},
    dateFn:function(days){return days.filter(function(d){return d.recovery!=null;}).map(function(d){return d.date;});},
    valFn:function(day){return day.recovery!=null?day.recovery+'%':'—';},
    subFn:function(day){
      var rn = day.recovery_n;
      if(rn == null) return t('detail_rec_today')+'0–100%';
      var sig = rn >= 3 ? t('detail_rec_sub_3sig') : (rn === 2 ? t('detail_rec_sub_2sig') : t('detail_rec_sub_hrv'));
      return t('detail_rec_today') + sig;
    },
    minVal:0,maxVal:100,yTicks:[0,25,50,75,100],
    get ctx(){return [t('detail_rec_ctx1'),t('detail_rec_ctx2'),t('detail_rec_ctx3')];},
    get norms(){return t('detail_rec_norms');}
  },
  sleep:{
    get title(){return t('ring_sleep');}, unit:'', accent:A.indigo,
    get chartLabel(){return t('detail_sleep_chart');},
    dataFn:function(days){return days.map(function(d){return d.asleep!=null?Math.round(d.asleep/60*10)/10:null;}).filter(function(v){return v!=null;});},
    dateFn:function(days){return days.filter(function(d){return d.asleep!=null;}).map(function(d){return d.date;});},
    valFn:function(day){if(day.asleep==null)return '—'; var sh=Math.floor(day.asleep/60),sm=day.asleep%60; return sh+'h '+(sm<10?'0':'')+sm+'m';},
    subFn:function(day){return day.sleep_perf!=null?t('detail_sleep_sub').replace('{pct}',day.sleep_perf):'';},
    minVal:3,maxVal:10,yTicks:[4,6,8,10],
    get ctx(){return [t('detail_sleep_ctx1'),t('detail_sleep_ctx2'),t('detail_sleep_ctx3')];},
    get norms(){return t('detail_sleep_norms');}
  },
  strain:{
    get title(){return t('ring_strain');}, unit:'/ 21', accent:A.cyan,
    get chartLabel(){return t('detail_strain_chart');},
    dataFn:function(days){return days.map(function(d){return d.strain!=null?d.strain:null;}).filter(function(v){return v!=null;});},
    dateFn:function(days){return days.filter(function(d){return d.strain!=null;}).map(function(d){return d.date;});},
    valFn:function(day){return day.strain!=null?day.strain.toFixed(1):'—';},
    subFn:function(day){return day.strain!=null?(day.strain<8?t('detail_strain_light'):(day.strain<14?t('detail_strain_moderate'):(day.strain<18?t('detail_strain_high'):t('detail_strain_vhigh'))))+t('detail_strain_today'):'';},
    minVal:0,maxVal:21,yTicks:[0,7,14,21],
    get ctx(){return [t('detail_strain_ctx1'),t('detail_strain_ctx2'),t('detail_strain_ctx3')];},
    get norms(){return t('detail_strain_norms');}
  },
  bodyage:{
    get title(){return t('ba_label');}, unit:'', accent:A.purpLite,
    get chartLabel(){return t('detail_bodyage_chart');},
    dataFn:function(days){return days.map(function(d){return d.hrv!=null?d.hrv:null;}).filter(function(v){return v!=null;});},
    dateFn:function(days){return days.filter(function(d){return d.hrv!=null;}).map(function(d){return d.date;});},
    valFn:function(){return bodyage.body_age!=null?String(bodyage.body_age):'—';},
    subFn:function(){
      var pct = bodyage.vo2max_percentile;
      var lbl = bodyage.vo2max_label;
      var base = t('ba_fitness_lbl')+' '+bodyage.fitness_age+' · '+t('ba_detail_real')+' '+bodyage.age;
      return pct != null ? base + ' · '+t('ba_detail_percentil')+' '+pct+(lbl?' ('+lbl+')':'') : base;
    },
    minVal:30,maxVal:80,yTicks:[35,50,65,80],
    get ctx(){return [
      t('vo2max_lbl')+' '+(bodyage.vo2max||'—')+' ('+(bodyage.category||'')+') → '+t('ba_fitness_lbl')+' ~'+(bodyage.fitness_age||'—')+'.'
      + (bodyage.vo2max_percentile != null ? ' '+t('ba_detail_percentil')+' '+bodyage.vo2max_percentile+'.' : ''),
      'HRV & '+t('ba_detail_sleep')+': +'+(bodyage.penalty||0)+' '+t('ba_ctx_penalty_suffix'),
      t('detail_sleep_ctx3'),
    ];},
    get norms(){return t('detail_bodyage_norms');}
  },
};

// ── Deep screen helpers ──────────────────────────────────────────────────────

/** Build a mini sparkline SVG (7 values, no axes, just the line+dot). */
function _miniSparkline(vals, color){
  var W=180, H=42, pad=6;
  var clean = vals.map(function(v){ return typeof v === 'number' && isFinite(v) ? v : null; });
  var defined = clean.filter(function(v){ return v !== null; });
  if(!defined.length) return '';
  var minV = Math.min.apply(null, defined);
  var maxV = Math.max.apply(null, defined);
  var span = maxV - minV || 1;
  var pw = W - pad*2, ph = H - pad*2;
  var X = function(i){ return pad + (clean.length <= 1 ? pw/2 : i * pw / (clean.length - 1)); };
  var Y = function(v){ return pad + (1 - (v - minV) / span) * ph; };
  // Build path segments (skip nulls)
  var segs = [], cur = [];
  clean.forEach(function(v, i){
    if(v !== null){ cur.push([X(i), Y(v)]); }
    else { if(cur.length){ segs.push(cur); cur = []; } }
  });
  if(cur.length) segs.push(cur);
  var body = '';
  segs.forEach(function(pts){
    if(!pts.length) return;
    var d = pts.length === 1
      ? 'M'+pts[0][0].toFixed(1)+','+pts[0][1].toFixed(1)
      : smooth(pts);
    body += '<path d="'+d+'" fill="none" stroke="'+color+'" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" vector-effect="non-scaling-stroke"/>';
  });
  // Last dot
  var lastPt = null;
  for(var i = clean.length-1; i >= 0; i--){
    if(clean[i] !== null){ lastPt = [X(i), Y(clean[i])]; break; }
  }
  if(lastPt) body += '<circle cx="'+lastPt[0].toFixed(1)+'" cy="'+lastPt[1].toFixed(1)+'" r="2.5" fill="'+color+'"/>';
  // Roadmap P2 paso 3: sin width/height fijos — el sparkline debe estirarse al
  // 100% del ancho de su celda (.deep-mc-spark svg{width:100%}); el viewBox
  // preserva la relación de aspecto 180:42.
  return '<svg viewBox="0 0 '+W+' '+H+'" preserveAspectRatio="none" style="display:block;overflow:visible;width:100%;height:auto">'+body+'</svg>';
}

/** Status pill: returns {cls, txt} for a metric. */
function _pill(status){
  // status: 'on_target' | 'in_range' | 'low' | 'high' | 'no_data' | 'active'
  var map = {
    on_target: {cls:'green', txt: t('dp_on_target')},
    in_range:  {cls:'green', txt: t('dp_in_range')},
    low:       {cls:'amber', txt: t('dp_low')},
    high:      {cls:'amber', txt: t('dp_high')},
    no_data:   {cls:'neutral', txt: t('dp_no_data')},
    active:    {cls:'green', txt: t('dp_active')},
  };
  return map[status] || {cls:'neutral', txt: status};
}

/**
 * Render one deep metric card.
 * cfg: { lbl, val, unit, sub, status, color, sparkVals, spanAll }
 * val: string already formatted, or null → shows '—'
 * spanAll: true para las métricas "hero" del detail (Duración en Sueño,
 *   Readiness en Fitness — roadmap P2 paso 3), ocupan el ancho completo
 *   del grid 2-up en vez de media columna.
 */
function _deepCard(cfg){
  var p = _pill(cfg.status || (cfg.val != null ? 'in_range' : 'no_data'));
  var valStr = cfg.val != null ? cfg.val : '—';
  var sparkHtml = '';
  if(cfg.sparkVals && cfg.sparkVals.some(function(v){ return v != null; })){
    sparkHtml = '<div class="deep-mc-spark">'+_miniSparkline(cfg.sparkVals, cfg.color || A.green)+'</div>';
    sparkHtml += '<div style="font:500 10px/1 -apple-system;color:var(--label3);margin-top:4px">'+t('ds_sparkline_7d')+'</div>';
  }
  return '<div class="deep-mc'+(cfg.spanAll ? ' deep-span-all' : '')+'">'
    +'<div class="deep-mc-header">'
      +'<div class="deep-mc-lbl-row">'
        +'<div class="deep-mc-dot" style="background:'+(cfg.color||A.green)+'"></div>'
        +'<div class="deep-mc-lbl">'+cfg.lbl+'</div>'
      +'</div>'
      +'<div class="deep-mc-pill '+p.cls+'">'+p.txt+'</div>'
    +'</div>'
    +'<div class="deep-mc-val-row">'
      +'<span class="deep-mc-val" style="color:'+(cfg.color||A.green)+'">'+valStr+'</span>'
      +(cfg.unit ? '<span class="deep-mc-unit">'+cfg.unit+'</span>' : '')
    +'</div>'
    +(cfg.sub ? '<div class="deep-mc-sub">'+cfg.sub+'</div>' : '')
    +sparkHtml
    +'</div>';
}

/** Section label inside the deep overlay. Full-width en el grid 2-up. */
function _deepSection(labelKey){
  return '<div class="deep-section-lbl deep-span-all">'+t(labelKey)+'</div>';
}

/** Build bedtime mini-bars for the last 7 nights with bedtime+waketime data. */
function _bedtimeBars(){
  var recent = days.slice(-14).filter(function(d){ return d.bedtime && d.waketime; }).slice(-7);
  if(!recent.length) return '<div style="font:400 13px/1.4 -apple-system;color:var(--label3);padding:8px 0">'+t('ds_no_sched')+'</div>';

  // Parse "HH:MM" → minutes from midnight (bedtime may be negative = before midnight)
  function toMin(s){
    if(!s) return null;
    var p = s.split(':'); if(p.length < 2) return null;
    return parseInt(p[0],10)*60 + parseInt(p[1],10);
  }

  // Normalize bedtime: if it represents time after ~18:00 it's "before midnight", store as negative offset
  // bedtime field is already in HH:MM 24h. e.g. "23:30" or "00:30"
  // We treat bedtime minutes from midnight: if >= 18*60 → subtract 24h → negative
  function normBed(s){
    var m = toMin(s); if(m == null) return null;
    return m >= 18*60 ? m - 24*60 : m;
  }
  function normWake(s){
    return toMin(s);
  }

  var allStart = recent.map(function(d){ return normBed(d.bedtime); }).filter(function(v){ return v !== null; });
  var allEnd   = recent.map(function(d){ return normWake(d.waketime); }).filter(function(v){ return v !== null; });
  if(!allStart.length) return '<div style="font:400 13px/1.4 -apple-system;color:var(--label3);padding:8px 0">'+t('ds_no_sched')+'</div>';

  var minT = Math.min.apply(null, allStart) - 20;
  var maxT = Math.max.apply(null, allEnd.concat([allStart[0]+600])) + 20;
  var span = maxT - minT || 1;

  function pct(m){ return ((m - minT) / span * 100).toFixed(1)+'%'; }
  function barW(s,e){ var w = (e-s)/span*100; return Math.max(w,2).toFixed(1)+'%'; }

  var barsHtml = '<div style="display:flex;gap:4px;align-items:stretch;margin-top:8px">';
  recent.forEach(function(d){
    var s = normBed(d.bedtime), e = normWake(d.waketime);
    if(s == null || e == null){ barsHtml += '<div style="flex:1"></div>'; return; }
    var date = d.date ? d.date.slice(5) : '';
    // bar as a relative-width fill within a row
    barsHtml += '<div style="flex:1;display:flex;flex-direction:column;align-items:center;gap:3px">'
      +'<div style="width:100%;height:52px;position:relative;background:var(--card2);border-radius:6px;overflow:hidden">'
        +'<div style="position:absolute;top:0;bottom:0;left:'+pct(s)+';width:'+barW(s,e)+';background:'
          +A.indigo+';border-radius:4px;opacity:.85"></div>'
      +'</div>'
      +'<div style="font:500 9px/1 -apple-system;color:var(--label3);text-align:center">'+date+'</div>'
    +'</div>';
  });
  barsHtml += '</div>';
  barsHtml += '<div style="display:flex;gap:14px;margin-top:6px;font:400 11px/1 -apple-system;color:var(--label3)">'
    +'<span><span style="display:inline-block;width:9px;height:9px;background:'+A.indigo+';border-radius:3px;margin-right:3px;vertical-align:middle"></span>'+t('ds_bed_lbl')+'→'+t('ds_wake_lbl')+'</span>'
    +'</div>';
  return barsHtml;
}

// ── Heatmap calendario de regularidad de sueño (Roadmap P1, F6, paso 3) ─────
// Grid semanas×días (columnas=semanas, filas=lun-dom) de los últimos ~13
// semanas, coloreado por asleep/need del día. 100% frontend, sin librerías:
// los días ya están en `days` (var global), el need del día usa needMin (el
// mismo `need_min` que expone /api/sleep-coach para HOY — para días
// históricos es una APROXIMACIÓN con el need actual, documentado en el
// título del bloque, ver roadmap "Arquitectura F6"). Sin dato ese día -> celda
// vacía (transparente), nunca se inventa un nivel.
var _HEATMAP_WEEKS = 13;
var _HEATMAP_LEVELS = [
  {min: 0,    color: 'rgba(255,55,95,.55)'},   // <60% del need
  {min: 0.6,  color: 'rgba(255,159,10,.55)'},  // 60-80%
  {min: 0.8,  color: 'rgba(163,200,90,.65)'},  // 80-95%
  {min: 0.95, color: '#30D158'},               // >=95%
];

function _heatmapLevelColor(ratio){
  if(ratio == null) return null;
  var chosen = _HEATMAP_LEVELS[0].color;
  for(var i=0;i<_HEATMAP_LEVELS.length;i++){
    if(ratio >= _HEATMAP_LEVELS[i].min) chosen = _HEATMAP_LEVELS[i].color;
  }
  return chosen;
}

/** Grid heatmap de sueño de los últimos _HEATMAP_WEEKS semanas. needMin: la
 * necesidad de sueño vigente (need_min de /api/sleep-coach) para calcular
 * asleep/need por día. Con <14 días de historial -> '' (bloque no aparece). */
function _renderSleepHeatmap(daysArr, needMin){
  daysArr = daysArr || [];
  if(daysArr.length < 14 || !needMin) return '';

  // Indexar por fecha para lookup O(1) al recorrer el calendario real
  // (incluye huecos sin sync como celdas vacías, no los comprime).
  var byDate = {};
  daysArr.forEach(function(d){ if(d && d.date) byDate[d.date] = d; });

  var lastDate = new Date(daysArr[daysArr.length-1].date+'T12:00:00');
  if(isNaN(lastDate.getTime())) return '';

  // Alinear al domingo de la semana del último día (columna final = semana actual).
  var endDow = lastDate.getDay(); // 0=dom..6=sáb
  var endOfWeek = new Date(lastDate); endOfWeek.setDate(lastDate.getDate() + (6-((endDow+6)%7)));
  var totalDays = _HEATMAP_WEEKS*7;
  var startDate = new Date(endOfWeek); startDate.setDate(endOfWeek.getDate() - totalDays + 1);

  // Construir columnas (semanas) de 7 celdas (lun..dom), de izquierda a derecha.
  var cols = [];
  var cur = new Date(startDate);
  for(var w=0; w<_HEATMAP_WEEKS; w++){
    var col = [];
    for(var i=0; i<7; i++){
      var iso = cur.toISOString().slice(0,10);
      var dayRow = byDate[iso];
      var ratio = (dayRow && dayRow.asleep != null) ? dayRow.asleep/needMin : null;
      col.push({date: iso, ratio: ratio, asleep: dayRow ? dayRow.asleep : null});
      cur.setDate(cur.getDate()+1);
    }
    cols.push(col);
  }

  // Etiquetas de mes: una por columna donde cambia el mes vs la anterior.
  var monthLbls = '';
  var lastMonth = null;
  cols.forEach(function(col){
    var d = new Date(col[0].date+'T12:00:00');
    var mo = d.getMonth();
    var lbl = '';
    if(mo !== lastMonth){ lbl = d.toLocaleDateString(undefined, {month:'short'}); lastMonth = mo; }
    monthLbls += '<div class="sleep-heatmap-month-lbl" style="width:12px">'+escHtml(lbl)+'</div>';
  });

  var dayLbls = ['L','M','X','J','V','S','D'].map(function(l){
    return '<div class="sleep-heatmap-daylbl">'+l+'</div>';
  }).join('');

  var colsHtml = cols.map(function(col){
    var cellsHtml = col.map(function(cell){
      var color = _heatmapLevelColor(cell.ratio);
      var style = color ? 'background:'+color : '';
      var title = '';
      if(cell.asleep != null){
        var h = Math.floor(cell.asleep/60), m = cell.asleep%60;
        title = cell.date+' · '+h+'h'+(m<10?'0':'')+m+'m ('+Math.round((cell.ratio||0)*100)+'%)';
      } else {
        title = cell.date;
      }
      return '<div class="sleep-heatmap-cell" style="'+style+'" title="'+escHtml(title)+'"></div>';
    }).join('');
    return '<div class="sleep-heatmap-col">'+cellsHtml+'</div>';
  }).join('');

  var legendHtml = '<div class="sleep-heatmap-legend">'
    + '<span>'+t('ds_heatmap_legend_low')+'</span>'
    + _HEATMAP_LEVELS.map(function(lv){ return '<span class="sleep-heatmap-legend-cell" style="background:'+lv.color+'"></span>'; }).join('')
    + '<span>'+t('ds_heatmap_legend_high')+'</span>'
    + '</div>';

  return '<div class="sleep-heatmap-wrap">'
    + '<div class="sleep-heatmap-months">'+monthLbls+'</div>'
    + '<div class="sleep-heatmap-body">'
      + '<div class="sleep-heatmap-daylbls">'+dayLbls+'</div>'
      + colsHtml
    + '</div>'
    + '</div>'
    + legendHtml;
}

// ── Hipnograma (F2 roadmap P0) — SVG a mano, sin librería de charts ─────────
// 4 bandas de arriba a abajo: Despierto / REM / Ligero / Profundo (orden
// estándar de hipnograma). Colores tomados de la paleta ya usada por las
// _deepCard de REM/Deep/Light de esta misma pantalla (NO se inventan hex
// nuevos); 'awake' reusa el naranja ya usado para vigorous/otras métricas.
var _HYPNO_ROWS = ['awake', 'rem', 'light', 'deep'];
var _HYPNO_COLORS = { awake: '#FF9F0A', rem: '#BF5AF2', light: '#9D8DF5', deep: '#5E5CE6' };

function _hypnogramSVG(segments){
  var totalMin = 0;
  segments.forEach(function(s){ if(s.e > totalMin) totalMin = s.e; });
  if(totalMin <= 0) return '';

  var W = 600, H = 120, rowH = H / 4, pad = 1;
  var svg = '<svg viewBox="0 0 '+W+' '+H+'" preserveAspectRatio="none" style="width:100%;height:120px;display:block">';

  // Fondo de cada fila (pista tenue) para que se note el espacio de las 4
  // bandas incluso donde no hay segmento de esa etapa.
  _HYPNO_ROWS.forEach(function(stage, i){
    svg += '<rect x="0" y="'+(i*rowH+pad)+'" width="'+W+'" height="'+(rowH-2*pad)+'" rx="2" fill="var(--card2)"></rect>';
  });

  segments.forEach(function(seg){
    var rowIdx = _HYPNO_ROWS.indexOf(seg.st);
    if(rowIdx < 0) return;
    var x = (seg.s / totalMin) * W;
    var w = Math.max(((seg.e - seg.s) / totalMin) * W, 1);
    var y = rowIdx * rowH + pad;
    var h = rowH - 2 * pad;
    svg += '<rect x="'+x.toFixed(2)+'" y="'+y.toFixed(2)+'" width="'+w.toFixed(2)+'" height="'+h.toFixed(2)+'" rx="2" fill="'+_HYPNO_COLORS[seg.st]+'"></rect>';
  });

  svg += '</svg>';
  return svg;
}

/** Fila de etiquetas de hora (bedtime -> waketime, ticks cada ~2h) bajo el SVG. */
function _hypnogramAxis(totalMin, bedtime){
  var bedMin = 0;
  if(bedtime){
    var p = bedtime.split(':');
    if(p.length === 2) bedMin = parseInt(p[0],10)*60 + parseInt(p[1],10);
  }
  var nTicks = Math.max(2, Math.min(6, Math.round(totalMin / 120) + 1));
  var html = '<div style="display:flex;justify-content:space-between;margin-top:4px">';
  for(var i=0;i<nTicks;i++){
    var frac = i/(nTicks-1);
    var m = bedMin + frac*totalMin;
    html += '<span style="font:400 9px/1 -apple-system;color:var(--label3)">'+_hm(m)+'</span>';
  }
  html += '</div>';
  return html;
}

/** Bloque "Anoche" (hipnograma + stats). SIEMPRE presente (roadmap tab Sueño,
 * paso 2 — empty state honesto: antes el bloque desaparecía en silencio sin
 * `segments`, lo cual parecía que el feature "se había borrado"). Tres ramas
 * según qué trae la noche `today`:
 *  1. Con `segments` -> hipnograma SVG completo + stats (comportamiento
 *     idéntico al de antes de este paso).
 *  2. Sin `segments` pero con totales de fase (deep/rem/light no nulos) ->
 *     leyenda "sin desglose por bloques" + totales por fase igual (caso
 *     típico Apple Watch/HealthKit, que aún no empuja el timeline).
 *  3. Sin nada -> leyenda "aún no hay datos de sueño de esta noche". */
function _renderHypnogramBlock(today){
  var segments = today.segments;
  var totalMin = 0;
  if(Array.isArray(segments) && segments.length){
    segments.forEach(function(s){ if(s.e > totalMin) totalMin = s.e; });
  }

  if(!Array.isArray(segments) || !segments.length || totalMin <= 0){
    var hasTotals = today.deep != null || today.rem != null || today.light != null;
    var emptyHtml = _deepSection('ds_hypnogram_title');
    emptyHtml += '<div class="deep-mc deep-span-all" style="padding:16px">';
    if(hasTotals){
      // Rama 2: sin segments pero con totales por fase — se muestra la
      // leyenda explicando por qué no hay hipnograma + los totales igual,
      // para no perder la info que sí existe.
      emptyHtml += '<div style="font:400 13px/1.4 -apple-system;color:var(--label3)">'+t('ds_hypno_empty_no_stages')+'</div>';
      emptyHtml += '<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-top:14px">';
      var dDeep = today.deep != null ? String(today.deep) : null;
      var dRem = today.rem != null ? String(today.rem) : null;
      var dLight = today.light != null ? String(today.light) : null;
      emptyHtml += '<div><div style="font:600 11px/1 -apple-system;color:var(--label2);margin-bottom:3px">'+t('ds_hypno_deep')+'</div>'
        + '<div style="font:680 20px/1 -apple-system;color:'+_HYPNO_COLORS.deep+'">'+(dDeep != null ? dDeep+t('du_min_short') : '—')+'</div></div>';
      emptyHtml += '<div><div style="font:600 11px/1 -apple-system;color:var(--label2);margin-bottom:3px">'+t('ds_hypno_rem')+'</div>'
        + '<div style="font:680 20px/1 -apple-system;color:'+_HYPNO_COLORS.rem+'">'+(dRem != null ? dRem+t('du_min_short') : '—')+'</div></div>';
      emptyHtml += '<div><div style="font:600 11px/1 -apple-system;color:var(--label2);margin-bottom:3px">'+t('ds_hypno_light')+'</div>'
        + '<div style="font:680 20px/1 -apple-system;color:'+_HYPNO_COLORS.light+'">'+(dLight != null ? dLight+t('du_min_short') : '—')+'</div></div>';
      emptyHtml += '</div>';
    } else if(today.asleep != null && today.asleep > 0){
      // Rama 3: SÍ durmió (hay asleep) pero sin segments NI totales por fase —
      // p.ej. una noche donde el merge eligió una sesión sin desglose. Antes
      // caía en "aún no hay datos", INCOHERENTE cuando la duración sí existe (y
      // se muestra en la card de abajo). Ahora reconoce el sueño: muestra la
      // duración dormida + la leyenda de que falta el desglose por fases.
      emptyHtml += '<div style="font:400 13px/1.4 -apple-system;color:var(--label3)">'+t('ds_hypno_empty_no_stages')+'</div>';
      var _dH = Math.floor(today.asleep/60), _dM = today.asleep%60;
      emptyHtml += '<div style="margin-top:14px"><div style="font:600 11px/1 -apple-system;color:var(--label2);margin-bottom:3px">'+t('ds_duration')+'</div>'
        + '<div style="font:680 20px/1 -apple-system">'+_dH+'h '+(_dM<10?'0':'')+_dM+'m</div></div>';
    } else {
      // Rama 4: sin NADA de dato de sueño para esta noche (ni duración ni fases).
      emptyHtml += '<div style="font:400 13px/1.4 -apple-system;color:var(--label3)">'+t('ds_hypno_empty_no_data')+'</div>';
    }
    emptyHtml += '</div>'; // /deep-mc
    return emptyHtml;
  }

  // Despertares: espejo JS de app.sleep_segments.awakenings() — cuenta los
  // segmentos "awake" DESPUÉS del primer segmento no-awake.
  var asleepStarted = false, nAwakenings = 0;
  var minByStage = { awake: 0, rem: 0, light: 0, deep: 0 };
  segments.forEach(function(seg){
    var dur = seg.e - seg.s;
    if(minByStage.hasOwnProperty(seg.st)) minByStage[seg.st] += dur;
    if(!asleepStarted){
      if(seg.st !== 'awake') asleepStarted = true;
      return;
    }
    if(seg.st === 'awake') nAwakenings++;
  });
  var asleepMin = minByStage.rem + minByStage.light + minByStage.deep;
  function pctOf(stage){
    return asleepMin > 0 ? Math.round(minByStage[stage] / asleepMin * 100) : null;
  }

  var html = _deepSection('ds_hypnogram_title');
  html += '<div class="deep-mc deep-span-all" style="padding:16px">';
  html += _hypnogramSVG(segments);
  html += _hypnogramAxis(totalMin, today.bedtime);

  // Leyenda de etapas (color + nombre), mismo patrón visual que _bedtimeBars().
  html += '<div style="display:flex;gap:12px;flex-wrap:wrap;margin-top:10px">';
  _HYPNO_ROWS.forEach(function(stage){
    html += '<span style="font:400 11px/1 -apple-system;color:var(--label3)">'
      + '<span style="display:inline-block;width:9px;height:9px;background:'+_HYPNO_COLORS[stage]+';border-radius:3px;margin-right:4px;vertical-align:middle"></span>'
      + t('ds_hypno_'+stage) + '</span>';
  });
  html += '</div>';

  // Fila de stats: eficiencia + despertares (fila 1) y % por etapa
  // deep/rem/light sobre asleep (fila 2) — criterio 15 del roadmap.
  html += '<div style="display:grid;grid-template-columns:repeat(2,1fr);gap:10px;margin-top:14px">';
  var effVal = today.eff != null ? String(today.eff)+t('du_pct') : '—';
  html += '<div><div style="font:600 11px/1 -apple-system;color:var(--label2);margin-bottom:3px">'+t('ds_eff')+'</div>'
    + '<div style="font:680 20px/1 -apple-system;color:var(--label)">'+effVal+'</div></div>';
  html += '<div><div style="font:600 11px/1 -apple-system;color:var(--label2);margin-bottom:3px">'+t('ds_hypno_awakenings')+'</div>'
    + '<div style="font:680 20px/1 -apple-system;color:var(--label)">'+nAwakenings+'</div></div>';
  html += '</div>';
  var pctDeep = pctOf('deep'), pctRem = pctOf('rem'), pctLight = pctOf('light');
  html += '<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-top:10px">';
  html += '<div><div style="font:600 11px/1 -apple-system;color:var(--label2);margin-bottom:3px">'+t('ds_hypno_pct_deep')+'</div>'
    + '<div style="font:680 20px/1 -apple-system;color:'+_HYPNO_COLORS.deep+'">'+(pctDeep != null ? pctDeep+t('du_pct') : '—')+'</div></div>';
  html += '<div><div style="font:600 11px/1 -apple-system;color:var(--label2);margin-bottom:3px">'+t('ds_hypno_pct_rem')+'</div>'
    + '<div style="font:680 20px/1 -apple-system;color:'+_HYPNO_COLORS.rem+'">'+(pctRem != null ? pctRem+t('du_pct') : '—')+'</div></div>';
  html += '<div><div style="font:600 11px/1 -apple-system;color:var(--label2);margin-bottom:3px">'+t('ds_hypno_pct_light')+'</div>'
    + '<div style="font:680 20px/1 -apple-system;color:'+_HYPNO_COLORS.light+'">'+(pctLight != null ? pctLight+t('du_pct') : '—')+'</div></div>';
  html += '</div>';

  html += '</div>'; // /deep-mc
  return html;
}

/** Construye el HTML de contenido de sueño para la noche `nightIdx` (puro
 * string-building, sin I/O ni inyección al DOM). Extraído de _renderSleepDeep
 * (roadmap tab Sueño, paso 1) para poder reusarlo tanto en el overlay viejo
 * como en el nuevo tab #screenSleep sin duplicar lógica. */
function _buildSleepContent(nightIdx){
  var today = days[nightIdx] || {};
  var idx = nightIdx != null ? nightIdx : days.length - 1;
  // last7: mismo criterio que antes (últimos 7 días del histórico completo,
  // NO relativos a nightIdx) — las sparklines siempre muestran "los últimos
  // 7 días reales", igual que el resto de la app.
  var last7 = days.slice(-7);
  function spark(field){ return last7.map(function(d){ return d[field] != null ? d[field] : null; }); }

  // Hipnograma (F2 roadmap P0): bloque "Anoche" SIEMPRE presente (roadmap tab
  // Sueño, paso 2 — antes desaparecía sin `segments`; ahora _renderHypnogramBlock
  // ramifica en 3 estados y nunca retorna '').
  var html = _renderHypnogramBlock(today);
  html += _deepSection('deep_section_sleep_data');

  // Duration — sleep-goal-vs-need: la tarjeta de Hoy es consumidora del
  // OBJETIVO personal (sleep_goal_min), NO de la necesidad fisiológica: su
  // copy ya dice "meta {h}h" en los 4 locales y pf_sleep_goal_hint le promete
  // al usuario que fijar su meta "mueve tu tarjeta de Hoy". Misma cadena de
  // fallback que app/changes.py y app/coach_headline.py (goal -> target -> 480)
  // para que un PROFILE viejo sin sleep_goal_min se comporte igual que hoy.
  var sleepTargetMin = (PROFILE && (PROFILE.sleep_goal_min || PROFILE.sleep_target_min)) || 480;
  var sleepTargetH = Math.round(sleepTargetMin/60);
  var dur = today.asleep != null ? (Math.floor(today.asleep/60)+'h '+(today.asleep%60<10?'0':'')+(today.asleep%60)+'m') : null;
  var durStatus = today.asleep == null ? 'no_data' : today.asleep >= sleepTargetMin ? 'on_target' : 'low';
  var durSpark = last7.map(function(d){ return d.asleep != null ? Math.round(d.asleep/60*10)/10 : null; });
  html += _deepCard({lbl:t('ds_duration'), val:dur, unit:'', sub:t('ds_duration_sub').replace('{h}', sleepTargetH)+' ('+sleepTargetMin+' '+t('du_min_short')+')'+_viaSourceSuffix('sleep'), status:durStatus, color:A.indigo, sparkVals:durSpark, spanAll:true});

  // Sleep score
  var score = today.sleep_perf != null ? String(today.sleep_perf) : null;
  var scoreStatus = today.sleep_perf == null ? 'no_data' : today.sleep_perf >= 85 ? 'on_target' : today.sleep_perf >= 60 ? 'low' : 'high';
  scoreStatus = today.sleep_perf == null ? 'no_data' : today.sleep_perf >= 85 ? 'on_target' : 'low';
  html += _deepCard({lbl:t('ds_score'), val:score, unit:t('du_pct'), sub:'', status:scoreStatus, color:A.indigo, sparkVals:spark('sleep_perf')});

  // REM
  var rem = today.rem != null ? String(today.rem) : null;
  var remStatus = today.rem == null ? 'no_data' : today.rem >= 60 ? 'on_target' : 'low';
  html += _deepCard({lbl:t('ds_rem'), val:rem, unit:t('du_min_short'), sub:'', status:remStatus, color:'#BF5AF2', sparkVals:spark('rem')});

  // Deep
  var deep = today.deep != null ? String(today.deep) : null;
  var deepStatus = today.deep == null ? 'no_data' : today.deep >= 60 ? 'on_target' : 'low';
  html += _deepCard({lbl:t('ds_deep'), val:deep, unit:t('du_min_short'), sub:'', status:deepStatus, color:'#5E5CE6', sparkVals:spark('deep')});

  // Light
  var light = today.light != null ? String(today.light) : null;
  html += _deepCard({lbl:t('ds_light'), val:light, unit:t('du_min_short'), sub:'', status:today.light != null ? 'in_range' : 'no_data', color:A.purpLite, sparkVals:spark('light')});

  // Efficiency
  var eff = today.eff != null ? String(today.eff) : null;
  var effStatus = today.eff == null ? 'no_data' : today.eff >= 85 ? 'on_target' : 'low';
  html += _deepCard({lbl:t('ds_eff'), val:eff, unit:t('du_pct'), sub:t('ds_eff_sub'), status:effStatus, color:A.cyan, sparkVals:spark('eff')});

  // ── Scores (Roadmap P1 F5, paso 2): sleep score (% del need) + consistencia
  // 0-100. Consume el cache de /api/sleep-coach (_lastSleepCoachData) — SIN
  // fetch propio (ya se pidió al cargar Hoy). Sin dato -> el bloque completo
  // no aparece (gate None-safe, mismo criterio del roadmap).
  var sc = _lastSleepCoachData;
  if (sc && (sc.sleep_score != null || sc.consistency != null)) {
    html += _deepSection('deep_section_sleep_scores');
    if (sc.sleep_score != null) {
      var scoreStatusF5 = sc.sleep_score >= 85 ? 'on_target' : sc.sleep_score >= 60 ? 'low' : 'high';
      html += _deepCard({
        lbl: t('ds_sleep_score_need'), val: String(sc.sleep_score), unit: t('du_pct'),
        sub: sc.need_min != null ? t('ds_sleep_score_need_sub').replace('{need}', Math.floor(sc.need_min/60)+'h'+(sc.need_min%60 ? (sc.need_min%60)+'m' : '')) : '',
        status: scoreStatusF5, color: A.indigo, sparkVals: null,
      });
    }
    if (sc.consistency != null) {
      var consStatusF5 = sc.consistency >= 80 ? 'on_target' : sc.consistency >= 50 ? 'low' : 'high';
      html += _deepCard({
        lbl: t('ds_consistency_score'), val: String(sc.consistency), unit: t('du_pct'),
        sub: t('ds_consistency_score_sub'), status: consStatusF5, color: A.cyan, sparkVals: null,
      });
    }
  }

  // ── Bedtime coach (roadmap tab Sueño, paso 6) — MOVIDO desde bedCard/Hoy.
  // Hora recomendada de acostarse: renderBedtime()/renderSleepCoach() poblan
  // estos IDs (bedCardLbl/bedTimeRec/bedInfo) justo después de inyectar este
  // HTML al DOM (ver _renderSleepDeep/renderSleepTab) — mismo patrón que
  // antes (progressive enhancement: legacy primero, sleep-coach rico encima
  // si /api/sleep-coach respondió). Solo tiene sentido para la noche MÁS
  // RECIENTE (el coach recomienda la hora de HOY, no de noches pasadas) —
  // se omite si se está viendo una noche histórica.
  if (idx === days.length - 1) {
    html += _deepSection('bed_label');
    html += '<div class="deep-mc deep-span-all" style="padding:16px;position:relative;overflow:hidden">'
      + '<div class="bed-glow"></div>'
      + '<div class="bed-lbl-row" style="display:none"><span class="bed-lbl" id="bedCardLbl" data-i18n="bed_label">'+t('bed_label')+'</span></div>'
      + '<div class="bed-time-row">'
        + '<div class="bed-time" id="bedTimeRec">—</div>'
        + '<div class="bed-hint"><span data-i18n="bed_hint_line1">'+t('bed_hint_line1')+'</span><br><span data-i18n="bed_hint_line2">'+t('bed_hint_line2')+'</span></div>'
      + '</div>'
      + '<div class="bed-info" id="bedInfo"></div>'
      + '</div>';
  }

  // ── Heatmap calendario de regularidad de sueño (Roadmap P1, F6, paso 3) ──
  // needMin: usa el need vigente (sleep-coach de HOY) como aproximación para
  // todo el histórico — el need histórico exacto no está persistido (roadmap
  // "Arquitectura F6"). Con <14 días de dato, _renderSleepHeatmap ya degrada
  // a '' (bloque no aparece).
  var heatmapNeedMin = (sc && sc.need_min != null) ? sc.need_min : sleepTargetMin;
  var heatmapHtml = _renderSleepHeatmap(days, heatmapNeedMin);
  if (heatmapHtml) {
    html += _deepSection('deep_section_sleep_heatmap');
    html += '<div class="deep-mc deep-span-all" style="padding:16px">'
      + '<div class="deep-mc-sub" style="margin-bottom:10px">' + t('ds_heatmap_sub') + '</div>'
      + heatmapHtml
      + '</div>';
  }

  // Bedtime schedule — full-width: son 7 barras horizontales, no cabe en media columna
  html += _deepSection('deep_section_sleep_sched');
  html += '<div class="deep-mc deep-span-all" style="padding:16px">'+_bedtimeBars()+'</div>';

  return html;
}

/** Render the Sleep deep screen (overlay viejo) into #deepMetricList. Queda
 * como wrapper fino de _buildSleepContent — comportamiento idéntico al
 * previo al refactor (roadmap tab Sueño, paso 1). El overlay ya no se invoca
 * desde ningún path activo (openDetail('sleep') fue reemplazado por
 * goTab('screenSleep')) pero se deja intacto para no ampliar superficie. */
function _renderSleepDeep(){
  document.getElementById('deepMetricList').innerHTML = _buildSleepContent(sel);
  _populateBedtimeSection();
}

/** Popula el bloque Bedtime (bedTimeRec/bedInfo) recién inyectado al DOM —
 * mismo patrón progressive-enhancement de siempre: legacy primero, sleep-coach
 * rico encima si ya llegó (roadmap tab Sueño, paso 6). None-safe: si la
 * sección no está en el DOM (noche histórica, se omite a propósito — ver
 * _buildSleepContent), ambas funciones hacen no-op. */
function _populateBedtimeSection(){
  renderBedtime();
  if (_lastSleepCoachData) renderSleepCoach(_lastSleepCoachData);
}

// ── TAB SUEÑO (roadmap tab Sueño) ───────────────────────────────────────────
// Selector de noche PROPIO del tab — independiente de `sel` (el de Hoy), para
// que navegar noches acá no mueva el scrubber de Hoy y viceversa (roadmap
// criterio 3/arquitectura). Default: la ÚLTIMA noche con `asleep` no nulo,
// no la última calendario (que puede venir vacía — justo el caso que
// confundió al Doc). Se calcula 1 vez (lazy) al entrar por 1ª vez al tab.
var sleepSel = null;

/** Índice de la última noche en `days` con `asleep` no nulo, o la última
 * noche del arreglo si ninguna trae dato (None-safe: nunca revienta). */
function _lastNightWithSleepIdx(){
  for (var i = days.length - 1; i >= 0; i--) {
    if (days[i] && days[i].asleep != null) return i;
  }
  return days.length ? days.length - 1 : 0;
}

/** Arma el header (selector ‹fecha›) + cuerpo del tab Sueño. Reusa
 * _buildSleepContent (hipnograma/fases/scores/heatmap/bedtime) + agrega la
 * sección Tendencia (arquitectura + consistencia), movida acá desde
 * Tendencias en el paso 5. Se re-invoca completa en cada cambio de noche —
 * el costo es el mismo que abrir el overlay viejo (ya lo hacía así). */
function renderSleepTab(){
  if (sleepSel == null) sleepSel = _lastNightWithSleepIdx();
  sleepSel = clamp(sleepSel, 0, Math.max(0, days.length - 1));

  var night = days[sleepSel] || {};
  document.getElementById('sleepSelDate').textContent = night.date || '—';
  document.getElementById('sleepBtnPrev').disabled = sleepSel <= 0;
  document.getElementById('sleepBtnNext').disabled = sleepSel >= days.length - 1;

  var html = _buildSleepContent(sleepSel);
  html += _buildSleepTrendSection();
  document.getElementById('sleepBody').innerHTML = html;
  _populateBedtimeSection();

  // Roadmap P3 Fase A: recomputa masonry — no-op si el tab no usa masonry,
  // pero consistente con el resto de tabs que sí podrían tener .cards.
  if (typeof layoutMasonryAll === 'function') layoutMasonryAll();
}

/** Mueve el selector de noche del tab Sueño ±1, con clamp en los extremos
 * (roadmap criterio 4: flechas deshabilitadas, nunca sale de rango). */
function sleepScrubBy(delta){
  if (sleepSel == null) sleepSel = _lastNightWithSleepIdx();
  var next = clamp(sleepSel + delta, 0, Math.max(0, days.length - 1));
  if (next === sleepSel) return;
  sleepSel = next;
  renderSleepTab();
}

/** Sección "Tendencia" del tab Sueño — MOVIDA desde Tendencias (roadmap tab
 * Sueño, paso 5): arquitectura del sueño (stacked bars deep/rem/light/awake)
 * + consistencia de bedtime (scatter). Mismas funciones stackedBars/
 * scatterChart y mismos datos que antes (days[].{deep,rem,light,awake,
 * bed_min}, sleep_target_min), solo cambia dónde se ensambla. Usa el mismo
 * `tendPeriod` global que Tendencias (7/30/90/365d) para no introducir un
 * segundo selector de rango (fuera de alcance del roadmap). Devuelve HTML
 * puro — no toca el DOM directamente (se inyecta junto con
 * _buildSleepContent en renderSleepTab). */
function _buildSleepTrendSection(){
  var n = tendPeriod;
  var slice = days.slice(-n);
  if (!slice.length) return '';

  // La línea de referencia es el OBJETIVO (adherencia en el tiempo), no la necesidad:
  // misma cadena de fallback que la tarjeta de Hoy -> goal → target → 480, para que
  // un perfil viejo sin sleep_goal_min caiga a su necesidad y no a un 480 duro.
  var sleepGoalMinChart = (PROFILE && (PROFILE.sleep_goal_min || PROFILE.sleep_target_min)) || 480;
  var html = _deepSection('tend_sleep_arch');
  html += '<div class="deep-mc deep-span-all" style="padding:16px">';
  html += '<div class="deep-mc-sub" style="margin-bottom:8px">' + t('tend_sleep_sub').replace('{h}', Math.round(sleepGoalMinChart/60)) + '</div>';
  html += stackedBars(
    slice,
    ['deep','rem','light','awake'],
    ['#4B3FA8','#9D78FF','#2E7BE6','rgba(150,150,175,.6)'],
    sleepGoalMinChart,
    {H:160, padL:32, yTicks:[120,240,360,480,600]}
  );
  html += '<div class="tend-legend" style="margin-top:10px">'
    + '<span class="tend-legend-item"><span class="tend-legend-dot" style="background:#4B3FA8"></span><span>'+t('tend_sleep_legend_deep')+'</span></span>'
    + '<span class="tend-legend-item"><span class="tend-legend-dot" style="background:#9D78FF"></span><span>'+t('tend_sleep_legend_rem')+'</span></span>'
    + '<span class="tend-legend-item"><span class="tend-legend-dot" style="background:#2E7BE6"></span><span>'+t('tend_sleep_legend_light')+'</span></span>'
    + '<span class="tend-legend-item"><span class="tend-legend-dot" style="background:rgba(150,150,175,.6)"></span><span>'+t('tend_sleep_legend_wake')+'</span></span>'
    + '</div>';
  html += '</div>'; // /deep-mc

  var bedVals = slice.map(function(d){ return d.bed_min != null ? d.bed_min : null; });
  var bedValid = bedVals.filter(function(v){ return v != null; });
  var sd = stdev(bedValid);
  var sdRound = Math.round(sd);
  html += _deepSection('tend_consistency');
  html += '<div class="deep-mc deep-span-all" style="padding:16px">';
  html += '<div class="deep-mc-sub" style="margin-bottom:8px">' + t('tend_consist_sub') + '</div>';
  html += scatterChart(
    bedVals, '#9D78FF', 0,
    {
      H:150, padL:38,
      minV: bedValid.length ? Math.min(Math.min.apply(null,bedValid)-20, -130) : -130,
      maxV: bedValid.length ? Math.max(Math.max.apply(null,bedValid)+20, 200) : 200,
      yTicks: [-120,-60,0,60,120,180]
    }
  );
  if (bedValid.length >= 3) {
    var badgeColor = sd <= 30 ? '#30D158' : (sd <= 60 ? '#FF9F0A' : '#FF375F');
    var varSfx = sd > 60 ? t('tend_consist_var_hi') : (sd > 30 ? t('tend_consist_var_mid') : t('tend_consist_var_lo'));
    html += '<div class="tend-badge" style="margin-top:8px">' + t('tend_consist_badge') + '<strong style="color:'+badgeColor+'">±'+sdRound+' min</strong>' + varSfx + '</div>';
  }
  html += '</div>'; // /deep-mc

  return html;
}

/** Render the Fitness deep screen into #deepMetricList. */
function _renderFitnessDeep(){
  var today = days[sel] || {};
  var last7 = days.slice(-7);
  var summary = (DB && DB.summary) || {};
  var bodyage = (DB && DB.summary && DB.summary.bodyage) || {};
  function spark(field){ return last7.map(function(d){ return d[field] != null ? d[field] : null; }); }

  var html = _deepSection('deep_section_fitness_readiness');

  // Readiness (recovery)
  var rec = today.recovery != null ? String(today.recovery) : null;
  var recStatus = today.recovery == null ? 'no_data' : today.recovery >= 67 ? 'on_target' : today.recovery >= 34 ? 'low' : 'high';
  recStatus = today.recovery == null ? 'no_data' : today.recovery >= 67 ? 'on_target' : 'low';
  html += _deepCard({lbl:t('df_readiness'), val:rec, unit:t('du_pct'), sub:'', status:recStatus, color:A.green, sparkVals:spark('recovery'), spanAll:true});

  // RHR
  var rhrBase = summary.rhr_base_recent || summary.rhr_base;
  var rhrRange = summary.rhr_range || [null, null];
  var rhr = today.rhr != null ? String(today.rhr) : null;
  var rhrStatus = today.rhr == null ? 'no_data'
    : (rhrRange[0] != null && today.rhr <= rhrRange[1] && today.rhr >= rhrRange[0]) ? 'in_range' : 'high';
  html += _deepCard({lbl:t('df_rhr'), val:rhr, unit:t('du_bpm'),
    sub: rhrBase ? 'base '+rhrBase+' '+t('du_bpm') : '',
    status:rhrStatus, color:A.red, sparkVals:spark('rhr')});

  // HRV
  var hrvBase = summary.hrv_base_recent || summary.hrv_base;
  var hrvRange = summary.hrv_range || [null, null];
  var hrv = today.hrv != null ? String(today.hrv) : null;
  var hrvStatus = today.hrv == null ? 'no_data'
    : (hrvRange[0] != null && today.hrv >= hrvRange[0]) ? 'in_range' : 'low';
  html += _deepCard({lbl:t('df_hrv'), val:hrv, unit:t('du_ms'),
    sub: hrvBase ? 'base '+hrvBase+' '+t('du_ms') : '',
    status:hrvStatus, color:A.green, sparkVals:spark('hrv')});

  // VO2max
  if(bodyage && bodyage.vo2max != null){
    var pct = bodyage.vo2max_percentile;
    var vo2sub = bodyage.vo2max_label ? bodyage.vo2max_label : '';
    if(pct != null) vo2sub += (vo2sub ? ' · ' : '') + t('ba_detail_percentil')+' '+pct;
    html += _deepCard({lbl:t('df_vo2max'), val:String(bodyage.vo2max), unit:'mL/kg/min',
      sub:vo2sub, status:'in_range', color:'#30D158', sparkVals:null});
  }

  html += _deepSection('deep_section_fitness_cardio');

  // Cardio load (strain + ACWR zone)
  var strain = today.strain != null ? today.strain.toFixed(1) : null;
  var acwrZone = summary.acwr_zone;
  var strainSub = acwrZone ? 'ACWR '+acwrZoneLabel(acwrZone) : '';
  var strainStatus = today.strain == null ? 'no_data' : today.strain < 14 ? 'in_range' : 'high';
  html += _deepCard({lbl:t('df_cardio_load'), val:strain, unit:'/ 21',
    sub:strainSub, status:strainStatus, color:A.cyan, sparkVals:spark('strain')});

  html += _deepSection('deep_section_fitness_activity');

  // Steps
  var steps = today.steps != null ? today.steps.toLocaleString() : null;
  var stepsStatus = today.steps == null ? 'no_data' : today.steps >= 8000 ? 'on_target' : 'low';
  html += _deepCard({lbl:t('df_steps'), val:steps, unit:'',
    sub:t('df_steps_sub')+' '+t('du_steps'), status:stepsStatus, color:A.amber, sparkVals:spark('steps')});

  // Vigorous (AZM)
  var vig = today.vigorous != null ? String(today.vigorous) : null;
  var vigStatus = today.vigorous == null ? 'no_data' : today.vigorous >= 20 ? 'on_target' : 'low';
  html += _deepCard({lbl:t('df_vigorous'), val:vig, unit:t('du_azm'),
    sub:t('df_vigorous_sub')+' '+t('du_azm'), status:vigStatus, color:'#FF9F0A', sparkVals:spark('vigorous')});

  // Exercise days (from exercises list — last 7 days)
  var exDays = 0;
  var sevenAgo = days.length >= 7 ? days[days.length-7].date : (days.length ? days[0].date : '');
  var exercises = (DB && DB.exercises) || [];
  var exDaysSet = {};
  exercises.forEach(function(ex){ if(ex.date >= sevenAgo) exDaysSet[ex.date] = 1; });
  exDays = Object.keys(exDaysSet).length;
  html += _deepCard({lbl:t('df_ex_days'), val:String(exDays), unit:t('du_days')+'/7',
    sub:'', status:exDays >= 3 ? 'on_target' : exDays >= 1 ? 'low' : 'no_data', color:'#30D158', sparkVals:null});

  // Extra metrics: distance, energy, active_hours — only if field exists and non-null
  var extraHtml = '';
  if(today.distance_km != null){
    extraHtml += _deepCard({lbl:t('df_distance'), val:today.distance_km.toFixed(1), unit:t('du_km'),
      sub:'', status:'in_range', color:A.cyan, sparkVals:spark('distance_km')});
  }
  if(today.energy_kcal != null){
    extraHtml += _deepCard({lbl:t('df_energy'), val:String(today.energy_kcal), unit:t('du_kcal'),
      sub:'', status:'in_range', color:'#FF9F0A', sparkVals:spark('energy_kcal')});
  }
  if(today.active_hours != null){
    extraHtml += _deepCard({lbl:t('df_active_hours'), val:String(today.active_hours), unit:t('du_hrs_active'),
      sub:'', status:'in_range', color:A.green, sparkVals:spark('active_hours')});
  }
  if(extraHtml){
    html += _deepSection('deep_section_fitness_extra');
    html += extraHtml;
  }

  document.getElementById('deepMetricList').innerHTML = html;
}

/** Render the Vitals (health signs) deep screen into #deepMetricList. */
function _renderVitalsDeep(){
  var today = days[sel] || {};
  var last7 = days.slice(-7);
  var summary = (DB && DB.summary) || {};
  function spark(field){ return last7.map(function(d){ return d[field] != null ? d[field] : null; }); }

  var html = '';

  // ── Wellbeing score header ─────────────────────────────────────────────────
  var wb = today.wellbeing;
  var wbColor = wb == null ? 'var(--label3)' : wb >= 80 ? '#30D158' : wb >= 60 ? '#A3C85A' : '#FF9F0A';
  var wbLabel = wb == null ? t('dp_no_data')
    : wb >= 80 ? t('dv_wellbeing_optimal')
    : wb >= 60 ? t('dv_wellbeing_good')
    : t('dv_wellbeing_attention');

  // Donut + big number header
  var donutHtml = (wb != null && typeof donutSVG === 'function')
    ? '<div style="margin:0 auto 8px;width:88px">' + donutSVG(wb, wbColor) + '</div>'
    : '';

  html += '<div class="deep-span-all" style="text-align:center;padding:8px 0 20px">'
    + donutHtml
    + '<div style="font:700 42px/1 -apple-system;color:' + wbColor + ';letter-spacing:-1px">'
    + (wb != null ? wb : '—') + '</div>'
    + '<div style="font:600 13px/1.3 -apple-system;color:var(--label2);margin-top:4px">'
    + t('dv_wellbeing') + '</div>'
    + '<div style="font:500 12px/1 -apple-system;color:' + wbColor + ';margin-top:4px">'
    + wbLabel + '</div>'
    + '</div>';

  // ── 5 vital sign cards ─────────────────────────────────────────────────────
  html += _deepSection('deep_section_vitals_signs');

  var rhrBase = summary.rhr_base_recent || summary.rhr_base;
  var rhrRange = summary.rhr_range || [null, null];
  var hrvBase = summary.hrv_base_recent || summary.hrv_base;
  var hrvRange = summary.hrv_range || [null, null];

  // Respiración (resp, rpm) — estable es bueno
  var resp = today.resp;
  var respVal = resp != null ? resp.toFixed(1) : null;
  var respStatus = resp == null ? 'no_data'
    : (resp >= 12 && resp <= 20) ? 'in_range' : 'high';
  html += _deepCard({
    lbl: t('hm_resp'), val: respVal, unit: t('du_rpm'),
    sub: t('dv_resp_sub'),
    status: respStatus, color: '#5AC8FA', sparkVals: spark('resp')
  });

  // SpO₂ (%) — umbral ≥96
  var spo2 = today.spo2;
  var spo2Val = spo2 != null ? spo2.toFixed(1) : null;
  var spo2Status = spo2 == null ? 'no_data'
    : spo2 >= 96 ? 'in_range' : spo2 >= 93 ? 'low' : 'high';
  html += _deepCard({
    lbl: t('hm_spo2'), val: spo2Val, unit: t('du_pct'),
    sub: t('dv_spo2_sub'),
    status: spo2Status, color: '#30AAFF', sparkVals: spark('spo2')
  });

  // FC reposo (rhr, lpm)
  var rhr = today.rhr;
  var rhrVal = rhr != null ? String(rhr) : null;
  var rhrStatus = rhr == null ? 'no_data'
    : (rhrRange[0] != null && rhr <= rhrRange[1] && rhr >= rhrRange[0]) ? 'in_range' : 'high';
  var rhrSub = (rhrBase ? t('dv_rhr_sub').replace('{v}', rhrBase) : '') + _viaSourceSuffix('rhr');
  html += _deepCard({
    lbl: t('hm_rhr'), val: rhrVal, unit: t('du_bpm'),
    sub: rhrSub,
    status: rhrStatus, color: '#FF6B6B', sparkVals: spark('rhr')
  });

  // HRV (ms)
  var hrv = today.hrv;
  var hrvVal = hrv != null ? String(hrv) : null;
  var hrvStatus = hrv == null ? 'no_data'
    : (hrvRange[0] != null && hrv >= hrvRange[0]) ? 'in_range' : 'low';
  var hrvSub = (hrvBase ? t('dv_hrv_sub').replace('{v}', hrvBase) : '') + _viaSourceSuffix('hrv');
  html += _deepCard({
    lbl: t('hm_hrv'), val: hrvVal, unit: t('du_ms'),
    sub: hrvSub,
    status: hrvStatus, color: '#30D158', sparkVals: spark('hrv')
  });

  // Temp piel (skin_temp, °) — skin_temp es desviación, ~0 normal
  var skinTemp = today.skin_temp;
  var skinVal = skinTemp != null ? (skinTemp >= 0 ? '+' : '') + skinTemp.toFixed(2) : null;
  var skinStatus = skinTemp == null ? 'no_data'
    : Math.abs(skinTemp) <= 0.5 ? 'in_range'
    : Math.abs(skinTemp) <= 1.0 ? 'low' : 'high';
  html += _deepCard({
    lbl: t('hm_temp'), val: skinVal, unit: t('du_deg'),
    sub: t('dv_temp_sub'),
    status: skinStatus, color: '#FF9F0A', sparkVals: spark('skin_temp')
  });

  document.getElementById('deepMetricList').innerHTML = html;
}

// ── openDetail — routes sleep/fitness/vitals to rich renderer, others to simple ──

function openDetail(key){
  var overlay = document.getElementById('detailOverlay');
  var deepWrap = document.getElementById('deepScreenWrap');
  var detailValRow = document.getElementById('detailValRow');
  var detailSub = document.getElementById('detailSub');
  var detailChart = document.getElementById('detailChart');
  var detailCtxCard = document.getElementById('detailCtxCard');

  overlay.classList.add('visible');
  overlay.classList.add('detail-in');
  setTimeout(function(){overlay.classList.remove('detail-in');},300);

  if(key === 'sleep' || key === 'fitness' || key === 'vitals' || key === 'cycle'){
    // Rich deep screen
    var accent = key === 'sleep' ? A.indigo : key === 'vitals' ? '#FF375F' : key === 'cycle' ? '#FF375F' : A.green;
    document.getElementById('detailBack').style.color = accent;
    document.getElementById('detailTitle').textContent =
      key === 'sleep' ? t('ring_sleep')
      : key === 'vitals' ? t('detail_vitals_title')
      : key === 'cycle' ? t('cycle_card_lbl')
      : t('detail_fitness_title');

    // Hide simple layout elements
    detailValRow.style.display = 'none';
    detailSub.style.display = 'none';
    detailChart.style.display = 'none';
    detailCtxCard.style.display = 'none';

    deepWrap.classList.add('visible');
    if(key === 'sleep'){
      _renderSleepDeep();
    } else if(key === 'vitals'){
      _renderVitalsDeep();
    } else if(key === 'cycle'){
      _renderCycleDeep();
    } else {
      _renderFitnessDeep();
    }
    return;
  }

  // Simple layout (recovery, strain, bodyage)
  deepWrap.classList.remove('visible');
  detailValRow.style.display = '';
  detailSub.style.display = '';
  detailChart.style.display = '';
  detailCtxCard.style.display = '';

  var cfg = DETAIL_CFG[key];
  if(!cfg) return;

  // Back button color
  document.getElementById('detailBack').style.color = cfg.accent;
  document.getElementById('detailTitle').textContent = cfg.title;
  var today = days[sel]||{};
  document.getElementById('detailVal').textContent = cfg.valFn(today);
  document.getElementById('detailVal').style.color = cfg.accent;
  document.getElementById('detailUnit').textContent = cfg.unit;
  document.getElementById('detailSub').textContent = cfg.subFn(today);

  // Chart: last 30 days with data
  document.getElementById('detailChartLbl').textContent = cfg.chartLabel || t('detail_chart_lbl');
  var chartData = cfg.dataFn(days).slice(-30);
  document.getElementById('detailChartSvg').innerHTML = buildDetailChart(chartData, cfg.accent, cfg.minVal, cfg.maxVal, cfg.yTicks);

  // C1 (Fase 8C): overlay de interactividad (tooltip/scrub) sobre el chart
  // recién pintado — progressive enhancement, si falla el chart estático queda.
  if (typeof attachChartInteraction === 'function') {
    try {
      var chartDates = (cfg.dateFn ? cfg.dateFn(days) : []).slice(-30);
      attachChartInteraction(document.getElementById('detailChartSvg'), {
        values: chartData, dates: chartDates, color: cfg.accent,
        minVal: cfg.minVal, maxVal: cfg.maxVal, unit: cfg.unit || '',
      });
    } catch (e) { /* progressive enhancement: el chart estático sigue funcionando */ }
  }

  // Context
  var ctxHtml = (cfg.ctx||[]).map(function(line){
    return '<div class="ctx-item"><span class="ctx-dot" style="background:'+cfg.accent+'"></span><span class="ctx-txt">'+line+'</span></div>';
  }).join('');
  document.getElementById('detailCtx').innerHTML = ctxHtml;
  document.getElementById('detailNorms').textContent = cfg.norms||'';
}

function closeDetail(){
  document.getElementById('detailOverlay').classList.remove('visible');
  // C1 (Fase 8C): cleanup del overlay de interactividad del chart (listeners,
  // tooltip DOM) — evita listeners huérfanos al reabrir otro detalle.
  if (typeof detachChartInteraction === 'function') {
    try { detachChartInteraction(); } catch (e) { /* best-effort */ }
  }
  // Reset deep wrap on close so re-open of simple screens looks clean
  var deepWrap = document.getElementById('deepScreenWrap');
  if(deepWrap) deepWrap.classList.remove('visible');
  var detailValRow = document.getElementById('detailValRow');
  var detailSub = document.getElementById('detailSub');
  var detailChart = document.getElementById('detailChart');
  var detailCtxCard = document.getElementById('detailCtxCard');
  if(detailValRow) detailValRow.style.display = '';
  if(detailSub) detailSub.style.display = '';
  if(detailChart) detailChart.style.display = '';
  if(detailCtxCard) detailCtxCard.style.display = '';
}

// ─────────────────────────────────────────────────────────────
// ── TENDENCIAS ──
// ─────────────────────────────────────────────────────────────

var tendPeriod = 30;

function setTendPeriod(n){
  tendPeriod = n;
  document.querySelectorAll('.tend-period-btn').forEach(function(b){
    b.classList.toggle('active', +b.getAttribute('data-n') === n);
  });
  renderTend();
  // La sección "Tendencia" del tab Sueño (roadmap tab Sueño, paso 5) comparte
  // el mismo tendPeriod global — si el tab ya se montó, se refresca para no
  // quedar desincronizado (nunca se fuerza el render si aún no se visitó).
  if (typeof sleepTabRendered !== 'undefined' && sleepTabRendered) renderSleepTab();
}

// ── SVG helpers for Tendencias (generalized, responsive) ──

// Returns pixel coords for a value in [minV, maxV] mapped to [padT, padT+ph]
function _tY(v, minV, maxV, padT, ph){
  return padT + (1 - (v - minV) / ((maxV - minV) || 1)) * ph;
}
function _tX(i, n, padL, pw){
  return padL + (n <= 1 ? pw / 2 : i * pw / (n - 1));
}

/**
 * lineChart(series, opts) → SVG string
 * series: [{vals:[...], color:'#hex', dashed:bool, label:'', fill:bool}]
 * Each vals array may contain null (gaps).
 * opts: {W,H,padL,padR,padT,padB, minV,maxV, yTicks, refLines:[{v,color,dashed,label}]}
 */
function lineChart(series, opts){
  // Roadmap P3 Fase B2: W default subido de 320 a 700 (alternativa mínima
  // documentada) para acotar el estiramiento no-uniforme de
  // preserveAspectRatio="none". Los callers pasan padL/padR pensados para
  // W=320 (ej. padL:30 ≈ 9.4%) — se escalan por _wScale para conservar esa
  // proporción relativa en el nuevo W (si no, el padding quedaría demasiado
  // angosto y apretaría las etiquetas del eje Y).
  var W=opts.W||700;
  var _wScale = W/320, H=opts.H||160;
  var padL=(opts.padL!=null?opts.padL:30)*_wScale, padR=(opts.padR||10)*_wScale, padT=opts.padT||10, padB=opts.padB||20;
  var pw=W-padL-padR, ph=H-padT-padB;
  // Compute global min/max from all series (ignoring nulls) or use opts
  var allVals=[];
  series.forEach(function(s){ s.vals.forEach(function(v){ if(v!=null) allVals.push(v); }); });
  (opts.refLines||[]).forEach(function(r){ allVals.push(r.v); });
  var minV = opts.minV != null ? opts.minV : (allVals.length ? Math.min.apply(null,allVals) : 0);
  var maxV = opts.maxV != null ? opts.maxV : (allVals.length ? Math.max.apply(null,allVals) : 1);
  var span = maxV - minV || 1;
  // pad 5%
  if(opts.minV == null){ minV -= span*0.05; }
  if(opts.maxV == null){ maxV += span*0.05; }

  var body = '';
  // Y-axis grid + tick labels
  (opts.yTicks||[]).forEach(function(t){
    var y=_tY(t,minV,maxV,padT,ph);
    body+='<line x1="'+padL+'" y1="'+y.toFixed(1)+'" x2="'+(W-padR)+'" y2="'+y.toFixed(1)+'" stroke="var(--grid)" stroke-width="1" vector-effect="non-scaling-stroke"/>';
    body+='<text x="'+(padL-5)+'" y="'+(y+3.5).toFixed(1)+'" text-anchor="end" fill="var(--axis)" style="font:500 9px -apple-system">'+t+'</text>';
  });
  // reference lines (horizontal thresholds)
  (opts.refLines||[]).forEach(function(r){
    var y=_tY(r.v,minV,maxV,padT,ph);
    var dash = r.dashed ? 'stroke-dasharray="4 3"' : '';
    body+='<line x1="'+padL+'" y1="'+y.toFixed(1)+'" x2="'+(W-padR)+'" y2="'+y.toFixed(1)+'" stroke="'+(r.color||'#fff')+'" stroke-width="1.2" vector-effect="non-scaling-stroke" '+dash+' opacity="0.6"/>';
    if(r.label){
      body+='<text x="'+(W-padR-2)+'" y="'+(y-3).toFixed(1)+'" text-anchor="end" fill="'+(r.color||'#fff')+'" style="font:500 9px -apple-system" opacity="0.7">'+r.label+'</text>';
    }
  });
  // each series
  series.forEach(function(s){
    var n = s.vals.length;
    if(!n) return;
    var dashAttr = s.dashed ? 'stroke-dasharray="5 3"' : '';
    // split into segments at nulls
    var segs=[], cur=[];
    s.vals.forEach(function(v,i){
      if(v != null){
        cur.push([_tX(i,n,padL,pw), _tY(v,minV,maxV,padT,ph)]);
      } else {
        if(cur.length>0){ segs.push(cur); cur=[]; }
      }
    });
    if(cur.length) segs.push(cur);
    // optional fill gradient (only first segment used for area)
    if(s.fill && segs.length){
      var gid='tg'+Math.random().toString(36).slice(2,7);
      body+='<defs><linearGradient id="'+gid+'" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="'+s.color+'" stop-opacity=".22"/><stop offset="1" stop-color="'+s.color+'" stop-opacity="0"/></linearGradient></defs>';
      segs.forEach(function(pts){
        if(pts.length<2) return;
        var lpath=smooth(pts);
        var area=lpath+' L'+pts[pts.length-1][0].toFixed(1)+','+(padT+ph).toFixed(1)+' L'+pts[0][0].toFixed(1)+','+(padT+ph).toFixed(1)+' Z';
        body+='<path class="an-area" d="'+area+'" fill="url(#'+gid+')" />';
      });
    }
    var lineCls = s.dashed ? 'an-fade' : 'an-line';
    var plAttr  = s.dashed ? '' : 'pathLength="1"';
    segs.forEach(function(pts){
      if(!pts.length) return;
      var lpath = pts.length===1
        ? 'M'+pts[0][0].toFixed(1)+','+pts[0][1].toFixed(1)
        : smooth(pts);
      body+='<path class="'+lineCls+'" '+plAttr+' vector-effect="non-scaling-stroke" d="'+lpath+'" fill="none" stroke="'+s.color+'" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" '+dashAttr+'/>';
      // dot at last point of last seg
      if(pts === segs[segs.length-1]){
        var last=pts[pts.length-1];
        body+='<circle class="an-dot" style="animation-delay:.7s" cx="'+last[0].toFixed(1)+'" cy="'+last[1].toFixed(1)+'" r="3" fill="'+s.color+'"/>';
      }
    });
  });
  return '<svg viewBox="0 0 '+W+' '+H+'" width="100%" height="'+H+'" preserveAspectRatio="none" style="display:block;overflow:visible">'+body+'</svg>';
}

/**
 * bandChart(vals, color, bandLo, bandHi, baseV, opts) → SVG string
 * Draws a shaded band between bandLo and bandHi, plus a line for vals, optional base line.
 */
function bandChart(vals, color, bandLo, bandHi, baseV, opts){
  // Roadmap P3 Fase B2: ver nota en lineChart — W default 320→700, padL/padR
  // de opts escalados por _wScale para conservar la proporción del diseño.
  var W=opts.W||700;
  var _wScale = W/320, H=opts.H||160;
  var padL=(opts.padL!=null?opts.padL:30)*_wScale, padR=(opts.padR||10)*_wScale, padT=opts.padT||10, padB=opts.padB||20;
  var pw=W-padL-padR, ph=H-padT-padB;
  var valid=vals.filter(function(v){return v!=null;});
  var allV=[].concat(valid,[bandLo,bandHi,baseV!=null?baseV:bandLo]);
  var minV = opts.minV != null ? opts.minV : Math.min.apply(null,allV) - 2;
  var maxV = opts.maxV != null ? opts.maxV : Math.max.apply(null,allV) + 2;

  var body='';
  (opts.yTicks||[]).forEach(function(t){
    var y=_tY(t,minV,maxV,padT,ph);
    body+='<line x1="'+padL+'" y1="'+y.toFixed(1)+'" x2="'+(W-padR)+'" y2="'+y.toFixed(1)+'" stroke="var(--grid)" stroke-width="1" vector-effect="non-scaling-stroke"/>';
    body+='<text x="'+(padL-5)+'" y="'+(y+3.5).toFixed(1)+'" text-anchor="end" fill="var(--axis)" style="font:500 9px -apple-system">'+t+'</text>';
  });
  // Band
  var n=vals.length;
  var yLo=_tY(bandLo,minV,maxV,padT,ph), yHi=_tY(bandHi,minV,maxV,padT,ph);
  body+='<rect x="'+padL+'" y="'+yHi.toFixed(1)+'" width="'+pw+'" height="'+(yLo-yHi).toFixed(1)+'" fill="'+color+'" opacity="0.12" rx="2"/>';
  // Base dashed line
  if(baseV!=null){
    var yBase=_tY(baseV,minV,maxV,padT,ph);
    body+='<line x1="'+padL+'" y1="'+yBase.toFixed(1)+'" x2="'+(W-padR)+'" y2="'+yBase.toFixed(1)+'" stroke="'+color+'" stroke-width="1" stroke-dasharray="4 3" opacity="0.5" vector-effect="non-scaling-stroke"/>';
  }
  // Series line (with gap support)
  var segs=[], cur=[];
  vals.forEach(function(v,i){
    if(v!=null){
      cur.push([_tX(i,n,padL,pw), _tY(v,minV,maxV,padT,ph)]);
    } else {
      if(cur.length){ segs.push(cur); cur=[]; }
    }
  });
  if(cur.length) segs.push(cur);
  segs.forEach(function(pts){
    if(!pts.length) return;
    body+='<path class="an-line" pathLength="1" vector-effect="non-scaling-stroke" d="'+smooth(pts)+'" fill="none" stroke="'+color+'" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/>';
  });
  if(segs.length){
    var last=segs[segs.length-1];
    var lp=last[last.length-1];
    body+='<circle class="an-dot" style="animation-delay:.7s" cx="'+lp[0].toFixed(1)+'" cy="'+lp[1].toFixed(1)+'" r="3" fill="'+color+'"/>';
  }
  return '<svg viewBox="0 0 '+W+' '+H+'" width="100%" height="'+H+'" preserveAspectRatio="none" style="display:block;overflow:visible">'+body+'</svg>';
}

/**
 * stackedBars(days, fieldGroups, colors, opts) → SVG string
 * fieldGroups: ['deep','rem','light','awake']  — stacked bottom-up
 * Also draws an optional refLine (8h=480min)
 */
function stackedBars(daysSlice, fieldGroups, colors, refMinutes, opts){
  // Roadmap P3 Fase B2: ver nota en lineChart — W default 320→700, padL/padR
  // de opts escalados por _wScale para conservar la proporción del diseño.
  // Este es el builder de "Arquitectura del sueño" (chart más ancho de la
  // app), ancla explícita del roadmap para verificar nitidez a 1280px.
  var W=opts.W||700;
  var _wScale = W/320, H=opts.H||160;
  var padL=(opts.padL!=null?opts.padL:30)*_wScale, padR=(opts.padR||8)*_wScale, padT=opts.padT||10, padB=opts.padB||20;
  var pw=W-padL-padR, ph=H-padT-padB;
  var n=daysSlice.length; if(!n) return '';
  // Max total for scale — roadmap P0 paso 8: SIEMPRE incluir el yTick más alto
  // en el cálculo. Antes solo se consideraban totals+refMinutes, así que un
  // yTick fijo más alto que ambos (ej. 600 con datos bajos y ref=480) caía
  // FUERA del viewBox (y negativa) y, por overflow:visible del <svg>, el label
  // flotaba encima del subtítulo de la card (overlap visual a 375px).
  var totals=daysSlice.map(function(d){
    return fieldGroups.reduce(function(s,f){return s+(d[f]||0);},0);
  });
  var tickMax = (opts.yTicks && opts.yTicks.length) ? Math.max.apply(null,opts.yTicks) : 0;
  var maxV = opts.maxV != null ? opts.maxV : Math.max.apply(null,totals.concat([refMinutes||0, tickMax]));
  if(!maxV) maxV=1;
  var barW=Math.max(2, pw/n*0.72);
  var gap=(pw/n)*(1-0.72)/2;
  var body='';
  (opts.yTicks||[]).forEach(function(t){
    var y=_tY(t,0,maxV,padT,ph);
    body+='<line x1="'+padL+'" y1="'+y.toFixed(1)+'" x2="'+(W-padR)+'" y2="'+y.toFixed(1)+'" stroke="var(--grid)" stroke-width="1" vector-effect="non-scaling-stroke"/>';
    body+='<text x="'+(padL-5)+'" y="'+(y+3.5).toFixed(1)+'" text-anchor="end" fill="var(--axis)" style="font:500 9px -apple-system">'+t+'</text>';
  });
  // Reference line (8h)
  if(refMinutes!=null){
    var yRef=_tY(refMinutes,0,maxV,padT,ph);
    body+='<line x1="'+padL+'" y1="'+yRef.toFixed(1)+'" x2="'+(W-padR)+'" y2="'+yRef.toFixed(1)+'" stroke="var(--label)" stroke-width="1.5" opacity="0.55" vector-effect="non-scaling-stroke"/>';
  }
  daysSlice.forEach(function(d,i){
    var x=padL+i*(pw/n)+gap;
    var base=padT+ph;
    var col='';
    fieldGroups.forEach(function(f,fi){
      var v=d[f]||0;
      if(!v) return;
      var barH=v/maxV*ph;
      var y=base-barH;
      col+='<rect x="'+x.toFixed(1)+'" y="'+y.toFixed(1)+'" width="'+barW.toFixed(1)+'" height="'+barH.toFixed(1)+'" fill="'+colors[fi]+'" rx="1.5"/>';
      base=y;
    });
    if(col){
      var dly=(i/n*0.4).toFixed(3);
      body+='<g class="an-bar" style="animation-delay:'+dly+'s">'+col+'</g>';
    }
  });
  return '<svg viewBox="0 0 '+W+' '+H+'" width="100%" height="'+H+'" preserveAspectRatio="none" style="display:block;overflow:visible">'+body+'</svg>';
}

/**
 * scatterChart(xSeries, color, refV, opts) → SVG string
 * xSeries: array of values (may be null). refV = y reference line.
 * bed_min can be negative (before midnight). We display as-is; wrap at ±720.
 */
function scatterChart(vals, color, refV, opts){
  // Roadmap P3 Fase B2: ver nota en lineChart — W default 320→700, padL/padR
  // de opts escalados por _wScale para conservar la proporción del diseño.
  var W=opts.W||700;
  var _wScale = W/320, H=opts.H||140;
  var padL=(opts.padL!=null?opts.padL:30)*_wScale, padR=(opts.padR||10)*_wScale, padT=opts.padT||10, padB=opts.padB||20;
  var pw=W-padL-padR, ph=H-padT-padB;
  var valid=vals.filter(function(v){return v!=null;});
  if(!valid.length) return '';
  var allV=valid.concat([refV!=null?refV:0]);
  var minV=opts.minV!=null?opts.minV:Math.min.apply(null,allV)-30;
  var maxV=opts.maxV!=null?opts.maxV:Math.max.apply(null,allV)+30;
  var n=vals.length;
  var body='';
  (opts.yTicks||[]).forEach(function(t){
    var y=_tY(t,minV,maxV,padT,ph);
    var lab=t>=0?_hm(t):'-'+_hm(-t);
    body+='<line x1="'+padL+'" y1="'+y.toFixed(1)+'" x2="'+(W-padR)+'" y2="'+y.toFixed(1)+'" stroke="var(--grid)" stroke-width="1" vector-effect="non-scaling-stroke"/>';
    body+='<text x="'+(padL-5)+'" y="'+(y+3.5).toFixed(1)+'" text-anchor="end" fill="var(--axis)" style="font:500 9px -apple-system">'+lab+'</text>';
  });
  if(refV!=null){
    var yRef=_tY(refV,minV,maxV,padT,ph);
    body+='<line x1="'+padL+'" y1="'+yRef.toFixed(1)+'" x2="'+(W-padR)+'" y2="'+yRef.toFixed(1)+'" stroke="#FF9F0A" stroke-width="1.2" stroke-dasharray="4 3" opacity="0.65" vector-effect="non-scaling-stroke"/>';
  }
  vals.forEach(function(v,i){
    if(v==null) return;
    var cx=_tX(i,n,padL,pw);
    var cy=_tY(v,minV,maxV,padT,ph);
    body+='<circle class="an-dot" style="animation-delay:'+(i/n*0.4).toFixed(3)+'s" cx="'+cx.toFixed(1)+'" cy="'+cy.toFixed(1)+'" r="3.2" fill="'+hexA(color,0.82)+'"/>';
  });
  return '<svg viewBox="0 0 '+W+' '+H+'" width="100%" height="'+H+'" preserveAspectRatio="none" style="display:block;overflow:visible">'+body+'</svg>';
}

/**
 * donutSVG(pct, color) → SVG string for balance donut
 */
function donutSVG(pct, color){
  var R=55, W=130, CX=65, CY=65;
  var C=2*Math.PI*R;
  var fill=Math.max(0,Math.min(1,pct/100))*C;
  return '<svg viewBox="0 0 '+W+' '+W+'" width="100%" style="display:block">'
    +'<circle cx="'+CX+'" cy="'+CY+'" r="'+R+'" fill="none" stroke="var(--ring-track)" stroke-width="13"/>'
    +'<circle class="an-ring" cx="'+CX+'" cy="'+CY+'" r="'+R+'" fill="none" stroke="'+color+'" stroke-width="13" stroke-linecap="round"'
    +' stroke-dasharray="'+fill.toFixed(2)+' '+C.toFixed(2)+'" transform="rotate(-90 '+CX+' '+CY+')"'
    +' style="--dlen:'+fill.toFixed(2)+';filter:drop-shadow(0 0 5px '+hexA(color,0.5)+')" />'
    +'</svg>';
}

// ── stdev helper ──
function stdev(arr){
  if(arr.length<2) return 0;
  var m=arr.reduce(function(s,x){return s+x;},0)/arr.length;
  var v=arr.reduce(function(s,x){return s+(x-m)*(x-m);},0)/(arr.length-1);
  return Math.sqrt(v);
}

// ── avg with null-filter ──
function avgOf(arr){
  var v=arr.filter(function(x){return x!=null;});
  return v.length ? v.reduce(function(s,x){return s+x;},0)/v.length : null;
}

// ── Comparativa semanal (Ronda 4) — siempre 7d-vs-7d, independiente del selector
// de periodo de Tendencias. Cada métrica compara sus PROPIOS últimos 7 días CON
// DATO (no calendario fijo) vs los 7 previos también con dato — así una métrica
// con huecos no arrastra Nones a la comparación de otra métrica.
// Regex de fuerza: espeja app/load.py STRENGTH_RE (Ronda 3) — no se puede import
// compartir Python<->JS, así que se duplica literal aquí con el mismo patrón.
var WCMP_STRENGTH_RE = /(weight|strength|fuerza|pesas|gym|resistance|musculac)/i;

function _wcmpLastNWithData(allDays, field, n){
  // Últimos n días (recorriendo desde el final) que tengan `field` no-null,
  // en orden cronológico. No exige contigüidad de fechas.
  var out = [];
  for(var i=allDays.length-1; i>=0 && out.length<n; i--){
    if(allDays[i] && allDays[i][field] != null) out.unshift(allDays[i]);
  }
  return out;
}

function _wcmpAvg(arr, field){
  var vals = arr.map(function(d){return d[field];}).filter(function(v){return v!=null;});
  if(!vals.length) return null;
  return vals.reduce(function(s,x){return s+x;},0)/vals.length;
}

function _wcmpStrengthMinutes(allExercises, dateSet){
  var total = 0;
  (allExercises||[]).forEach(function(e){
    if(!dateSet[e.date]) return;
    var hay = String(e.type||'') + ' ' + String(e.name||'');
    if(WCMP_STRENGTH_RE.test(hay)) total += (e.dur_min||0);
  });
  return total;
}

function weeklyCompare(allDays, allExercises){
  // Para recovery/asleep/strain/steps: tomar los últimos 14 días CON dato de esa
  // métrica, partirlos en curr=últimos 7 / prev=7 anteriores a esos.
  var result = {sufficient: true, metrics: {}};
  var fieldsSimple = [
    {key:'recovery', field:'recovery'},
    {key:'sleep',    field:'asleep'},
    {key:'strain',   field:'strain'},
    {key:'steps',    field:'steps'}
  ];
  fieldsSimple.forEach(function(f){
    var withData = (allDays||[]).filter(function(d){ return d && d[f.field] != null; });
    if(withData.length < 14){
      result.metrics[f.key] = {insufficient: true};
      return;
    }
    var last14 = withData.slice(-14);
    var prev7 = last14.slice(0,7), curr7 = last14.slice(7,14);
    var curAvg = _wcmpAvg(curr7, f.field), prevAvg = _wcmpAvg(prev7, f.field);
    result.metrics[f.key] = {cur: curAvg, prev: prevAvg,
      delta: (curAvg!=null && prevAvg!=null) ? (curAvg-prevAvg) : null};
  });

  // Fuerza: minutos por ventana de fechas (últimos 7 días de calendario vs los 7 previos),
  // ya que 0 min es un valor válido (no "sin dato") a diferencia de las métricas de arriba.
  var datesAll = (allDays||[]).map(function(d){return d.date;}).filter(Boolean);
  if(datesAll.length >= 14){
    var last14dates = datesAll.slice(-14);
    var prevDates = last14dates.slice(0,7), currDates = last14dates.slice(7,14);
    var prevSet = {}, currSet = {};
    prevDates.forEach(function(d){prevSet[d]=true;});
    currDates.forEach(function(d){currSet[d]=true;});
    var curMin = _wcmpStrengthMinutes(allExercises, currSet);
    var prevMin = _wcmpStrengthMinutes(allExercises, prevSet);
    result.metrics.strength = {cur: curMin, prev: prevMin, delta: curMin - prevMin};
  } else {
    result.metrics.strength = {insufficient: true};
  }

  // "Insuficiente" global: si TODAS las métricas simples son insuficientes (menos
  // de 14 días de datos en general), mostramos el estado único de la tarjeta.
  var anySufficient = fieldsSimple.some(function(f){ return !result.metrics[f.key].insufficient; })
    || !result.metrics.strength.insufficient;
  result.sufficient = anySufficient;
  return result;
}

// Roadmap P1 paso 1: cada delta lleva SIEMPRE una unidad explícita
// (deltaUnit se antepone con espacio; puede ser distinta al unit del valor
// actual/previo — ej. pasos: el valor absoluto no lleva unidad visible pero
// el delta sí, para que nunca se lea un número desnudo tipo "Pasos +27.6").
// deltaNd: decimales del delta (pasos se ve mejor entero que las demás métricas).
function _wcmpDeltaHtml(delta, higherIsBetter, deltaUnit, deltaNd){
  if(delta == null) return '';
  var cls = 'flat', arrow = '→';
  if(Math.abs(delta) >= 0.05){
    var up = delta > 0;
    cls = higherIsBetter ? (up ? 'up' : 'down') : (up ? 'down' : 'up');
    arrow = up ? '↑' : '↓';
  }
  // strain es neutral: se muestra el delta sin juicio de color (roadmap) — se
  // fuerza a 'flat' cuando higherIsBetter es null.
  if(higherIsBetter === null) cls = 'flat';
  var unit = deltaUnit || '';
  var nd = deltaNd != null ? deltaNd : 1;
  return '<span class="wcmp-delta '+cls+'">'+arrow+' '+(delta>0?'+':'')+delta.toFixed(nd)+unit+'</span>';
}

function renderWeeklyCompare(){
  var el = document.getElementById('tendWeeklyCompareBody');
  if(!el) return;
  var cmp = weeklyCompare(days, exercises);
  if(!cmp.sufficient){
    el.innerHTML = '<div class="wcmp-insufficient">'+t('tend_wcompare_insufficient')+'</div>';
    return;
  }
  // Roadmap P1 paso 1: unidad explícita por métrica en el delta.
  // recovery -> pts, sueño -> h, esfuerzo -> pts, pasos -> unidad "pasos"
  // (el dataset trae un CONTEO crudo, no un porcentaje — ver nota de
  // tend_wcompare_steps_unit en i18n; roadmap asumía "%", se documenta la
  // desviación en el informe), fuerza -> min.
  var rows = [
    {key:'recovery',  lbl:t('tend_wcompare_recovery'), unit:'%',   nd:0, higherBetter:true,  deltaUnit:' pts',                       deltaNd:1},
    {key:'sleep',     lbl:t('tend_wcompare_sleep'),     unit:'h',   nd:1, higherBetter:true,  div:60, deltaUnit:'h',                  deltaNd:1},
    {key:'strain',    lbl:t('tend_wcompare_strain'),    unit:'',    nd:1, higherBetter:null,  deltaUnit:' pts',                       deltaNd:1},
    {key:'steps',     lbl:t('tend_wcompare_steps'),     unit:'',    nd:0, higherBetter:true,  deltaUnit:t('tend_wcompare_steps_unit'), deltaNd:0},
    {key:'strength',  lbl:t('tend_wcompare_strength'),  unit:'min', nd:0, higherBetter:true,  deltaUnit:' min',                       deltaNd:0}
  ];
  var html = '';
  rows.forEach(function(r){
    var m = cmp.metrics[r.key];
    if(!m || m.insufficient){
      html += '<div class="wcmp-row"><div class="wcmp-lbl">'+r.lbl+'</div>'
        + '<div class="wcmp-insufficient" style="padding:0">'+t('tend_wcompare_insufficient')+'</div></div>';
      return;
    }
    var div = r.div || 1;
    var curDisp = m.cur != null ? (m.cur/div).toFixed(r.nd) : '—';
    var prevDisp = m.prev != null ? (m.prev/div).toFixed(r.nd) : '—';
    var deltaScaled = m.delta != null ? m.delta/div : null;
    html += '<div class="wcmp-row">'
      + '<div class="wcmp-lbl">'+r.lbl+'</div>'
      + '<div class="wcmp-vals">'+curDisp+r.unit
      + ' <span class="wcmp-prev">('+prevDisp+r.unit+')</span> '
      + _wcmpDeltaHtml(deltaScaled, r.higherBetter, r.deltaUnit, r.deltaNd) + '</div>'
      + '</div>';
  });
  el.innerHTML = html;
}

// ── Palancas (drivers) ──
// Roadmap P1 paso 1: factorizado fuera de renderTend() para poder refrescarse
// independientemente al cambiar de idioma (setLocale) — DRIVERS es un
// snapshot embebido server-side en el load inicial (placeholder de driver
// data en app/render.py) que NUNCA se refrescaba al cambiar de locale (gap
// preexistente, no introducido por P1, pero visible ahora porque Palancas es
// una de las superficies que este roadmap toca). fetchAndRenderDrivers() usa
// el endpoint /api/drivers ya existente, mismo patrón que
// fetchAndRenderJournalImpact para Impacto de hábitos.
function renderPalancas(){
  var palancasEl = document.getElementById('tendPalancas');
  if(!palancasEl) return;
  var drvs = DRIVERS || [];
  var drvHtml = '';
  if(drvs.length === 0){
    drvHtml = '<p style="font-size:13px;color:var(--label3);margin:6px 0">'+t('tend_palancas_empty')+'</p>';
  } else {
    // Roadmap P1 paso 1: el headline humano queda visible por default; ρ/n/p
    // (antes embebidos en el propio headline server-side) viven tras el
    // helper ⓘ, usando los campos crudos que ya trae cada finding.
    drvHtml = drvs.map(function(d){
      var statLine = t('impact_stat_line')
        .replace('{rho}', d.rho != null ? d.rho.toFixed(2) : '—')
        .replace('{n}', d.n != null ? d.n : '—')
        .replace('{p}', d.p != null ? d.p.toFixed(3) : '—');
      return '<p class="bullet" style="margin:5px 0;font-size:13px;line-height:1.4"><strong style="color:var(--label)">'+d.headline+'</strong>'+techDetail('drv', escHtml(statLine))+'</p>';
    }).join('');
  }
  palancasEl.innerHTML = '<div style="font-size:11px;color:var(--label3);margin-bottom:8px">'+t('tend_palancas_sub')+'</div>' + drvHtml;
}
function fetchAndRenderDrivers(){
  fetch('/api/drivers')
    .then(function(r){ return r.json(); })
    .then(function(data){ DRIVERS = Array.isArray(data) ? data : []; renderPalancas(); })
    .catch(function(){ /* sin conexión: deja lo que había (mismo patrón que journal.js) */ });
}

// ── renderTend ──
function renderTend(){
  // Comparativa semanal: independiente del selector de periodo (roadmap) — se
  // renderiza ANTES del early-return de abajo para no depender de `slice`.
  renderWeeklyCompare();

  var n = tendPeriod;
  var slice = days.slice(-n);
  if(!slice.length) return;

  // ── 1. Recuperación vs Esfuerzo ──
  var recVals   = slice.map(function(d){ return d.recovery != null ? d.recovery : null; });
  var strainVals= slice.map(function(d){ return d.strain   != null ? d.strain/21*100 : null; });
  document.getElementById('tendChartRecStrain').innerHTML = lineChart(
    [
      {vals: recVals,    color:'#30D158', fill: true},
      {vals: strainVals, color:'#0A84FF', dashed: true}
    ],
    {H:150, padL:30, minV:0, maxV:100, yTicks:[0,25,50,75,100]}
  );

  // ── 2. Balance donut ──
  var recValid = recVals.filter(function(v){return v!=null;});
  var avgRec = recValid.length ? Math.round(recValid.reduce(function(s,x){return s+x;},0)/recValid.length) : 0;
  document.getElementById('tendDonutBalance').innerHTML = donutSVG(avgRec, '#30D158');
  document.getElementById('tendBalanceVal').innerHTML = avgRec+'<span style="font-size:16px">%</span>';
  var balanceMsg = avgRec >= 70
    ? t('tend_balance_desc_hi')
    : avgRec >= 50
      ? t('tend_balance_desc_mid')
      : t('tend_balance_desc_lo');
  document.getElementById('tendBalanceDesc').textContent = balanceMsg;

  // ── 3. HRV ──
  var hrvBase = summary.hrv_base || null;
  var hrvRange = summary.hrv_range || [null, null];
  var hrvVals = slice.map(function(d){ return d.hrv != null ? d.hrv : null; });
  document.getElementById('tendHrvSub').textContent =
    t('tend_hrv_sub').replace('{base}', hrvBase != null ? hrvBase.toFixed(1) : '—');
  document.getElementById('tendChartHrv').innerHTML = bandChart(
    hrvVals, '#30D158',
    hrvRange[0] || 0, hrvRange[1] || 100, hrvBase,
    {H:150, padL:30, yTicks: hrvBase ? [Math.round(hrvRange[0]||0), Math.round(hrvBase), Math.round(hrvRange[1]||100)] : []}
  );

  // ── 4. FC en reposo ──
  var rhrBase = summary.rhr_base || null;
  var rhrVals = slice.map(function(d){ return d.rhr != null ? d.rhr : null; });
  document.getElementById('tendRhrSub').textContent =
    t('tend_rhr_sub').replace('{base}', rhrBase != null ? rhrBase.toFixed(0) : '—');
  var rhrValid = rhrVals.filter(function(v){return v!=null;});
  var rhrMin = rhrValid.length ? Math.min.apply(null,rhrValid) : 40;
  var rhrMax = rhrValid.length ? Math.max.apply(null,rhrValid) : 80;
  document.getElementById('tendChartRhr').innerHTML = lineChart(
    [{vals: rhrVals, color:'#FF9F0A', fill: true}],
    {
      H:150, padL:30,
      minV: Math.min(rhrMin-3, rhrBase ? rhrBase-3 : rhrMin-3),
      maxV: rhrMax+3,
      refLines: rhrBase ? [{v: rhrBase, color:'#FF9F0A', dashed:true}] : [],
      yTicks: rhrBase ? [Math.round(rhrMin), Math.round(rhrBase), Math.round(rhrMax)] : []
    }
  );

  // ── 5/6. Arquitectura del sueño + Consistencia de sueño — MOVIDAS al tab
  // Sueño (roadmap tab Sueño, paso 5). Ver _buildSleepTrendSection().

  // ── 7. SpO₂ ──
  var spo2Vals = slice.map(function(d){ return d.spo2 != null ? d.spo2 : null; });
  var spo2Valid = spo2Vals.filter(function(v){return v!=null;});
  document.getElementById('tendChartSpo2').innerHTML = lineChart(
    [{vals: spo2Vals, color:'#64D2FF', fill: true}],
    {
      H:140, padL:32,
      minV: spo2Valid.length ? Math.min(Math.min.apply(null,spo2Valid)-1, 88) : 88,
      maxV: spo2Valid.length ? Math.max(Math.max.apply(null,spo2Valid)+0.5, 100) : 100,
      refLines: [{v:90, color:'#FF375F', dashed:true, label:'90%'}],
      yTicks: [88,90,92,94,96,98,100]
    }
  );

  // ── 8. Temp. de piel ──
  // Compute mean of valid temps as dynamic base
  var tempVals = slice.map(function(d){ return d.skin_temp != null ? d.skin_temp : null; });
  var tempValid = tempVals.filter(function(v){return v!=null;});
  var tempBase = tempValid.length ? avgOf(tempValid) : null;
  var tempDevVals = tempBase != null
    ? tempVals.map(function(v){ return v != null ? +(v - tempBase).toFixed(2) : null; })
    : tempVals;
  document.getElementById('tendChartTemp').innerHTML = lineChart(
    [{vals: tempDevVals, color:'#FF9F0A', fill: true}],
    {
      H:130, padL:30,
      refLines: tempBase != null ? [{v:0, color:'#FF9F0A', dashed:true}] : [],
      yTicks: []
    }
  );

  // ── Métricas clave ──
  var avgHrv  = avgOf(slice.map(function(d){return d.hrv;}));
  var avgRhr  = avgOf(slice.map(function(d){return d.rhr;}));
  var avgResp = avgOf(slice.map(function(d){return d.resp;}));
  var avgSpo2 = avgOf(slice.map(function(d){return d.spo2;}));
  var avgTemp = avgOf(slice.map(function(d){return d.skin_temp;}));
  var avgSteps= avgOf(slice.map(function(d){return d.steps;}));
  var avgAsleep = avgOf(slice.map(function(d){return d.asleep;})); // minutes
  var daysWithSleep = slice.filter(function(d){return d.deep||d.rem||d.light;});
  var avgDeepPct = avgOf(daysWithSleep.map(function(d){
    var tot=(d.deep||0)+(d.rem||0)+(d.light||0)+(d.awake||0);
    return tot>0?d.deep/tot*100:null;
  }));
  var avgRemPct  = avgOf(daysWithSleep.map(function(d){
    var tot=(d.deep||0)+(d.rem||0)+(d.light||0)+(d.awake||0);
    return tot>0?d.rem/tot*100:null;
  }));
  var avgEff   = avgOf(slice.map(function(d){return d.eff;}));
  var avgStrain= avgOf(slice.map(function(d){return d.strain;}));
  var avgRecovery = avgOf(slice.map(function(d){return d.recovery;}));

  function fmt(v, dec){ return v!=null ? v.toFixed(dec!=null?dec:1) : '—'; }
  function fmtInt(v){ return v!=null ? Math.round(v).toString() : '—'; }

  var metrics = [
    {dot:'#30D158', l:t('met_recovery'), v: fmtInt(avgRecovery), u:'%',  s:t('met_sub_avg'), tk:'recovery'},
    {dot:'#30D158', l:t('met_hrv'),      v: fmt(avgHrv,1),        u:'ms', s:t('met_sub_base_hrv').replace('{v}',fmt(summary.hrv_base,1)), tk:'hrv'},
    {dot:'#FF9F0A', l:t('met_rhr'),      v: fmt(avgRhr,1),        u:'lpm',s:t('met_sub_base_rhr').replace('{v}',fmt(summary.rhr_base,1)), tk:'rhr'},
    {dot:'#40C8E0', l:t('met_resp'),     v: fmt(avgResp,1),       u:'rpm',s:t('met_sub_avg')},
    {dot:'#64D2FF', l:t('met_spo2'),     v: fmt(avgSpo2,1),       u:'%',  s:t('met_sub_spo2')},
    {dot:'#FF9F0A', l:t('met_temp'),     v: fmt(avgTemp,2),       u:'°C', s:t('met_sub_avg')},
    {dot:'#0A84FF', l:t('met_steps'),    v: fmtInt(avgSteps),     u:'',   s:t('met_sub_steps')},
    {dot:'#5E5CE6', l:t('met_sleep'),    v: avgAsleep!=null?fmt(avgAsleep/60,1):'—', u:'h', s:t('met_sub_sleep'), tk:'asleep'},
    {dot:'#4B3FA8', l:t('met_deep'),     v: fmt(avgDeepPct,1),    u:'%',  s:t('met_sub_deep')},
    {dot:'#9D78FF', l:t('met_rem'),      v: fmt(avgRemPct,1),     u:'%',  s:t('met_sub_deep')},
    {dot:'#2E7BE6', l:t('met_eff'),      v: fmt(avgEff,1),        u:'%',  s:t('met_sub_eff')},
    {dot:'#64D2FF', l:t('met_strain'),   v: fmt(avgStrain,1),     u:'/21',s:t('met_sub_avg')},
  ];

  document.getElementById('tendMetricsGrid').innerHTML = metrics.map(function(m){
    var badge = m.tk ? trendBadge(m.tk) : '';
    return '<div class="tend-metric">'
      +'<div class="tend-metric-lbl-row"><span class="tend-metric-dot" style="background:'+m.dot+'"></span>'
      +'<span class="tend-metric-lbl">'+m.l+'</span>'+badge+'</div>'
      +'<div class="tend-metric-val-row"><span class="tend-metric-val">'+m.v+'</span>'
      +'<span class="tend-metric-unit">'+m.u+'</span></div>'
      +'<div class="tend-metric-sub">'+m.s+'</div>'
      +'</div>';
  }).join('');

  // ── Entrenamientos recientes ──
  // Filter exercises within period
  var cutDate = slice.length ? slice[0].date : '';
  var filtEx = exercises.filter(function(e){ return !cutDate || e.date >= cutDate; })
    .slice().sort(function(a,b){ return b.date > a.date ? 1 : -1; })
    .slice(0,10);
  if(!filtEx.length && exercises.length){
    filtEx = exercises.slice().sort(function(a,b){ return b.date>a.date?1:-1; }).slice(0,6);
  }
  // roadmap P0 paso 5: si TODA una columna (min/kcal/bpm) es null en el
  // período mostrado, se omite la columna completa (no una racha de "—" por
  // fila, que no aporta nada y ensucia la lista).
  var hasDur  = filtEx.some(function(e){ return e.dur_min != null; });
  var hasKcal = filtEx.some(function(e){ return e.kcal != null; });
  var hasHr   = filtEx.some(function(e){ return e.avg_hr != null; });
  document.getElementById('tendWorkoutsList').innerHTML = filtEx.length
    ? filtEx.map(function(e){
        return '<div class="tend-workout-row">'
          +'<div class="tend-workout-info"><div class="tend-workout-name">'+(e.name||e.type||t('tend_workout_default'))+'</div>'
          +'<div class="tend-workout-date">'+fmtDateES(e.date)+'</div></div>'
          +(hasDur  ? '<div class="tend-workout-min">'+(e.dur_min!=null?e.dur_min:'—')+'m</div>' : '')
          +(hasKcal ? '<div class="tend-workout-kcal">'+(e.kcal!=null?Math.round(e.kcal)+' kcal':'—')+'</div>' : '')
          +(hasHr   ? '<div class="tend-workout-bpm">'+(e.avg_hr!=null?Math.round(e.avg_hr)+' bpm':'—')+'</div>' : '')
          +'</div>';
      }).join('')
    : '<div style="padding:12px 10px;font:400 13px -apple-system;color:var(--label3)">'+t('tend_workouts_empty')+'</div>';

  renderPalancas();

  // Nota Fase 8B: Impacto de hábitos + Informe narrativo (tendJournalImpact /
  // reportPreview) se cargan desde el patch de goTab() al final de
  // static/js/journal.js (fetchAndRenderJournalImpact + fetchAndRenderReportPreview,
  // 1ra vez que se entra a Tendencias) — no se duplica aquí para evitar doble
  // fetch en cada render de esta función.

  // Roadmap P3 Fase A: recomputa masonry tras redibujar todos los charts de
  // Tendencias (cambio de periodo incluido). No-op si <900px.
  layoutMasonry('tend');
}

// ── COACH TAB ──

// COACH_SUGGESTIONS is now locale-driven via t('coach_suggestions')
var COACH_SUGGESTIONS = [];  // populated by applyI18n / renderCoachTab

var coachRendered = false;

// Conversación activa (persistida en localStorage — al reabrir la app vuelve
// al último chat). null = todavía no se sabe / sin conversaciones.
var activeConvId = localStorage.getItem('vitals_coach_conv') || null;
var _coachConvListCache = []; // metadata ligera [{id,title,updated,message_count}]

function _setActiveConvId(id) {
  activeConvId = id || null;
  if (activeConvId) {
    localStorage.setItem('vitals_coach_conv', activeConvId);
  } else {
    localStorage.removeItem('vitals_coach_conv');
  }
}

function _paintHistoryBubbles(msgs) {
  // Pinta burbujas de historial persistido ANTES de la sesión actual.
  // XSS: SIEMPRE textContent (nunca innerHTML) — el contenido viene de texto
  // libre del usuario/coach (mismo patrón que el visor de ECG).
  var thread = document.getElementById('coachThread');
  var lbl = document.getElementById('coachThreadLbl');
  if (!thread) return;
  thread.innerHTML = '';
  if (!msgs || !msgs.length) { if (lbl) lbl.style.display = 'none'; return; }
  if (lbl) lbl.style.display = '';
  // F1 roadmap P0: con una conversación activa que YA tiene mensajes, las
  // sugerencias de arranque no aplican — se ocultan (criterio 2: desaparecen
  // mientras la conversación avanza).
  if (typeof _setCoachSuggestionsHidden === 'function') _setCoachSuggestionsHidden(true);
  msgs.forEach(function(msg) {
    var role = msg.role === 'user' ? 'user' : 'assistant';
    var wrap = document.createElement('div');
    wrap.className = 'bubble-wrap ' + role;
    var bubble = document.createElement('div');
    bubble.className = 'bubble ' + role;
    bubble.textContent = msg.content || '';
    wrap.appendChild(bubble);
    thread.appendChild(wrap);
  });
  // Scroll al final
  thread.scrollIntoView({ behavior: 'instant', block: 'end' });
}

function _updateConvSwitcherLabel() {
  var el = document.getElementById('coachConvSwitcherTitle');
  if (!el) return;
  var conv = _coachConvListCache.find(function(c) { return c.id === activeConvId; });
  el.textContent = conv ? conv.title : t('coach_new_conv');
}

// Roadmap P1 paso 4: cola de "listo" del tab Coach — permite que
// goCoachWithPlanPrompt() espere a que el historial (async) termine de
// pintarse antes de mandar el prompt, sin competir con el auto-render que
// el patch de goTab() ya dispara en la primera visita (evita doble-fetch).
var _coachTabReadyQueue = [];
function _onCoachTabReady(fn) {
  if (coachRendered) { fn(); } else { _coachTabReadyQueue.push(fn); }
}

// ── F1 roadmap P0: preguntas sugeridas (chips) del tab Coach ────────────────
// fetch a /api/coach/suggestions (motor de insights, backend). Al tocar una
// chip se envía por el MISMO camino que el submit manual (sendCoach), y el
// contenedor se oculta apenas arranca la conversación activa (mismo
// comportamiento que WHOOP/Oura: son atajos, no botones fijos).

function _setCoachSuggestionsHidden(hidden) {
  // Chips + su label viven juntos: ocultar solo las chips dejaría el label
  // "Pregúntale al coach" flotando sobre un contenedor vacío.
  var sugEl = document.getElementById('coachSuggestions');
  var lblEl = document.querySelector('.coach-suggestions-lbl');
  if (sugEl) sugEl.style.display = hidden ? 'none' : '';
  if (lblEl) lblEl.style.display = hidden ? 'none' : '';
}

function _renderCoachSuggestionChips(questions) {
  // DOM API (no innerHTML con texto interpolado): el texto de la pregunta va
  // por textContent — XSS-safe (patrón _appendBubble) y sin el problema de
  // quoting de JSON.stringify dentro de un atributo onclick.
  var sugEl = document.getElementById('coachSuggestions');
  if (!sugEl) return;
  sugEl.innerHTML = '';
  var qs = Array.isArray(questions) ? questions.filter(function(q) { return q && q.text; }) : [];
  if (!qs.length) { _setCoachSuggestionsHidden(true); return; }
  _setCoachSuggestionsHidden(false);
  qs.forEach(function(q) {
    var chip = document.createElement('div');
    chip.className = 'coach-suggestion';
    var span = document.createElement('span');
    span.textContent = q.text;
    chip.appendChild(span);
    chip.insertAdjacentHTML('beforeend',
      '<svg class="coach-suggestion-chevron" viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 6l6 6-6 6"/></svg>');
    chip.onclick = function() { _onCoachSuggestionClick(q.text); };
    sugEl.appendChild(chip);
  });
}

function _onCoachSuggestionClick(text) {
  // Las chips son atajos de arranque, no botones fijos (mismo comportamiento
  // que WHOOP/Oura): al tocar una, el set entero desaparece y la pregunta
  // entra por el MISMO camino que el submit manual.
  _setCoachSuggestionsHidden(true);
  sendCoach(text);
}

function _loadCoachSuggestions() {
  fetch('/api/coach/suggestions')
    .then(function(r) { return r.json(); })
    .then(function(data) {
      _renderCoachSuggestionChips(data && data.questions);
    })
    .catch(function() {
      // Fallback silencioso: contenedor oculto, cero errores en consola.
      _setCoachSuggestionsHidden(true);
    });
}

function renderCoachTab() {
  // Brief (reusa los datos del COACH del tab Hoy)
  var card = COACH || {};
  var chips = card.chips || [];
  var bullets = card.bullets || [];

  var lastDay = days[days.length - 1] || {};
  var dateStr = lastDay.date || '';
  if (dateStr) {
    var p = dateStr.split('-');
    var d = new Date(+p[0], +p[1] - 1, +p[2]);
    var el = document.getElementById('coachBriefDate');
    if (el) el.textContent = t('today_prefix') + d.getDate() + ' ' + _getMeses()[d.getMonth()];
  }

  var chipsEl = document.getElementById('coachBriefChips');
  if (chipsEl) {
    chipsEl.innerHTML = chips.map(function(c) {
      return '<span class="chip" style="color:' + c.c + ';background:' + c.bg + ';border:1px solid ' + c.bd + '">' + c.t + '</span>';
    }).join('');
  }

  var bulletsEl = document.getElementById('coachBriefBullets');
  if (bulletsEl) {
    bulletsEl.innerHTML = bullets.map(function(b) {
      return '<p class="bullet"><strong>' + b.title + '</strong> ' + b.body + '</p>';
    }).join('') || '<p class="bullet">'+t('coach_metrics_ok')+'</p>';
  }

  // Sugerencias — F1 roadmap P0: chips contextuales desde /api/coach/suggestions
  // (motor de insights), con fallback silencioso a [] si el fetch falla.
  _loadCoachSuggestions();

  _loadConversationList(function() {
    // Sin conversation activa explícita: caer a la más reciente si existe alguna.
    if (!activeConvId && _coachConvListCache.length) {
      _setActiveConvId(_coachConvListCache[0].id);
    }
    _updateConvSwitcherLabel();
    _updateMasterCloseVisibility();
    if (activeConvId) {
      _loadActiveConversationThread();
    } else {
      _paintHistoryBubbles([]);
    }
    // Roadmap P1 paso 4: "Ver plan del día" necesita esperar a que el historial
    // (async) termine de pintarse ANTES de mandar el prompt — si no, la carga
    // de historial (_paintHistoryBubbles) borra la burbuja del usuario recién
    // agregada por sendCoach (misma condición de carrera latente que ya existía
    // para un tap ultra-rápido en una sugerencia del empty state). Se resuelve
    // con una cola de "onReady" en vez de un parámetro para no competir con el
    // auto-call de renderCoachTab() que ya hace el patch de goTab() (evita
    // doble-render/doble-fetch en la primera visita al tab Coach).
    var queued = _coachTabReadyQueue.slice();
    _coachTabReadyQueue.length = 0;
    queued.forEach(function(fn){ fn(); });
  });

  coachRendered = true;
}

function _loadConversationList(cb) {
  fetch('/api/coach/conversations')
    .then(function(r) { return r.json(); })
    .then(function(list) {
      _coachConvListCache = Array.isArray(list) ? list : [];
      if (cb) cb();
    })
    .catch(function() {
      _coachConvListCache = [];
      if (cb) cb();
    });
}

function _loadActiveConversationThread() {
  if (!activeConvId) { _paintHistoryBubbles([]); return; }
  fetch('/api/coach/conversations/' + encodeURIComponent(activeConvId))
    .then(function(r) {
      if (!r.ok) throw new Error('not found');
      return r.json();
    })
    .then(function(conv) {
      _paintHistoryBubbles((conv && conv.messages) || []);
    })
    .catch(function() {
      // Conversación activa ya no existe (borrada en otra pestaña, etc.) — no romper.
      _setActiveConvId(null);
      _paintHistoryBubbles([]);
    });
}

function autoResize(el) {
  el.style.height = 'auto';
  el.style.height = Math.min(el.scrollHeight, 120) + 'px';
}

function coachInputKeydown(e) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    sendCoach();
  }
}

function _appendBubble(role, text) {
  var thread = document.getElementById('coachThread');
  var lbl = document.getElementById('coachThreadLbl');
  if (!thread) return;
  if (lbl) lbl.style.display = '';
  var wrap = document.createElement('div');
  wrap.className = 'bubble-wrap ' + role;
  var bubble = document.createElement('div');
  bubble.className = 'bubble ' + role;
  bubble.textContent = text;
  wrap.appendChild(bubble);
  thread.appendChild(wrap);
  thread.scrollIntoView({ behavior: 'smooth', block: 'end' });
  return wrap;
}

function _appendThinking() {
  var thread = document.getElementById('coachThread');
  if (!thread) return null;
  var wrap = document.createElement('div');
  wrap.className = 'bubble-wrap assistant';
  var bubble = document.createElement('div');
  bubble.className = 'bubble thinking';
  bubble.textContent = t('coach_thinking');
  wrap.appendChild(bubble);
  thread.appendChild(wrap);
  thread.scrollIntoView({ behavior: 'smooth', block: 'end' });
  return wrap;
}

// Roadmap P1 paso 4: "Ver plan del día" ahora entrega un plan real — navega
// al Coach, precarga el prompt i18n en el input y lo ENVÍA (mismo mecanismo
// que las sugerencias del empty state del Coach, que ya maneja loading/error
// vía sendCoach). Si el coach no responde, sendCoach ya deja una burbuja de
// error graceful (coach_net_error) y el input queda listo para reintentar —
// no se re-implementa un fallback distinto al que el resto de la app usa.
// _onCoachTabReady evita la condición de carrera de la 1ra visita (ver su
// comentario) sin duplicar el fetch de historial que el patch de goTab() ya dispara.
function goCoachWithPlanPrompt() {
  var prompt = t('coach_plan_prompt');
  goTab('screenCoach'); // dispara el auto-render (patch de goTab) si es la 1ra visita
  var input = document.getElementById('coachInput');
  if (input) input.value = prompt;
  _onCoachTabReady(function(){ sendCoach(prompt); });
}

function sendCoach(text) {
  var input = document.getElementById('coachInput');
  var question = (text || (input && input.value.trim()) || '').trim();
  if (!question) return;

  // Clear input
  if (input) { input.value = ''; input.style.height = 'auto'; }

  // La conversación arranca: las sugerencias son atajos de arranque, no
  // botones fijos (mismo comportamiento que WHOOP/Oura) — se ocultan.
  _setCoachSuggestionsHidden(true);

  // Paint user bubble
  _appendBubble('user', question);

  // Disable send btn while waiting
  var btn = document.getElementById('coachSendBtn');
  if (btn) btn.disabled = true;

  // Thinking indicator
  var thinkWrap = _appendThinking();

  fetch('/api/coach', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question: question, conversation_id: activeConvId }),
  })
    .then(function(r) { return r.json(); })
    .then(function(data) {
      // Remove thinking bubble
      if (thinkWrap && thinkWrap.parentNode) thinkWrap.parentNode.removeChild(thinkWrap);
      var answer = (data && data.answer) || t('coach_no_answer');
      _appendBubble('assistant', answer);
      // Si no había conversación activa (o cambió), adoptar la que devolvió el backend.
      if (data && data.conversation_id && data.conversation_id !== activeConvId) {
        _setActiveConvId(data.conversation_id);
      }
      // Refrescar metadata (título/updated/message_count) en segundo plano.
      _loadConversationList(_updateConvSwitcherLabel);
    })
    .catch(function() {
      if (thinkWrap && thinkWrap.parentNode) thinkWrap.parentNode.removeChild(thinkWrap);
      _appendBubble('assistant', t('coach_net_error'));
    })
    .finally(function() {
      if (btn) btn.disabled = false;
      if (input) input.focus();
    });
}

// ── COACH: switcher + modal de lista de conversaciones (patrón ecgListModal) ──

function newConversation() {
  closeCoachConvModal();
  fetch('/api/coach/conversations', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({}),
  })
    .then(function(r) { return r.json(); })
    .then(function(data) {
      if (data && data.id) {
        _setActiveConvId(data.id);
        _loadConversationList(function(){ _updateConvSwitcherLabel(); _updateMasterCloseVisibility(); });
        _paintHistoryBubbles([]);
        // Hilo vacío de nuevo: recuperar las sugerencias de arranque.
        _loadCoachSuggestions();
      }
    })
    .catch(function() {
      // Silencioso: si falla, el usuario simplemente sigue en la conversación actual.
    });
}

function openConversation(id) {
  _setActiveConvId(id);
  closeCoachConvModal();
  _updateConvSwitcherLabel();
  _updateMasterCloseVisibility();
  _loadActiveConversationThread();
}

// ── Coach Deportivo: Sesión Master (roadmap coach-mental, Paso 5) ──────────

function _updateMasterCloseVisibility() {
  // El botón de cierre solo aparece cuando la conversación ACTIVA es una
  // Sesión Master (kind=mental_master, ya viene en la metadata ligera de
  // _coachConvListCache desde el Paso 2 de coach_store.py).
  var btn = document.getElementById('masterCloseBtn');
  if (!btn) return;
  var conv = _coachConvListCache.find(function(c) { return c.id === activeConvId; });
  btn.style.display = (conv && conv.kind === 'mental_master') ? '' : 'none';
}

function startMasterSession() {
  // Misma cola de "listo" que goCoachWithPlanPrompt() (ver su comentario):
  // evita competir con el auto-render de la primera visita al tab, que
  // también pinta el historial de forma asíncrona.
  _onCoachTabReady(function() {
    fetch('/api/coach/mental/session', { method: 'POST' })
      .then(function(r) { return r.json(); })
      .then(function(data) {
        if (!data || !data.conversation_id) return;
        _setActiveConvId(data.conversation_id);
        _setCoachSuggestionsHidden(true);
        _loadConversationList(function() {
          _updateConvSwitcherLabel();
          _updateMasterCloseVisibility();
          _loadActiveConversationThread();
        });
      })
      .catch(function() {
        // Silencioso: si falla, el usuario sigue en la conversación actual.
      });
  });
}

function closeMasterSession() {
  if (!activeConvId) return;
  fetch('/api/coach/mental/session/' + encodeURIComponent(activeConvId) + '/close', { method: 'POST' })
    .then(function(r) { return r.json(); })
    .then(function(data) {
      var focos = (data && data.focos) || [];
      var msg = focos.length
        ? (t('mental_closed_with_focos') + ' ' + focos.join(' · '))
        : t('mental_closed_no_focos');
      _appendBubble('assistant', msg);
    })
    .catch(function() {
      _appendBubble('assistant', t('coach_net_error'));
    });
}

// ── COACH: notas de voz asíncronas (roadmap coach-voz, Paso 4) ─────────────
// Botón #coachMicBtn junto a #coachSendBtn: graba con MediaRecorder, sube el
// blob como raw body a POST /api/coach/voice, pinta transcript + respuesta
// reusando _appendBubble (mismo helper que sendCoach — sin duplicar pintado).

var _voiceRecorder = null;
var _voiceChunks = [];
var _voiceMimeType = 'audio/webm';
var _voiceState = 'idle'; // idle | recording | uploading

function _voiceSupported() {
  return !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia && window.MediaRecorder);
}

function _initVoiceMicButton() {
  // Feature-detect: el botón vive oculto en el template (style="display:none")
  // y solo se muestra si el navegador soporta grabación (criterio 7 del roadmap).
  var btn = document.getElementById('coachMicBtn');
  if (!btn) return;
  btn.style.display = _voiceSupported() ? '' : 'none';
}

function _setVoiceState(state) {
  _voiceState = state;
  var btn = document.getElementById('coachMicBtn');
  var input = document.getElementById('coachInput');
  var sendBtn = document.getElementById('coachSendBtn');
  if (btn) {
    btn.classList.toggle('recording', state === 'recording');
    btn.disabled = (state === 'uploading');
  }
  if (sendBtn) sendBtn.disabled = (state !== 'idle');
  if (input) {
    if (state === 'recording') input.placeholder = t('voice_recording_ph');
    else if (state === 'uploading') input.placeholder = t('voice_uploading');
    else input.placeholder = t('coach_placeholder');
  }
}

function toggleVoiceRecording() {
  if (_voiceState === 'recording') { _stopVoiceRecording(); return; }
  if (_voiceState !== 'idle') return; // subiendo — ignora doble tap
  _startVoiceRecording();
}

function _startVoiceRecording() {
  if (!_voiceSupported()) return;
  navigator.mediaDevices.getUserMedia({ audio: true })
    .then(function(stream) {
      var mimeType = '';
      if (window.MediaRecorder.isTypeSupported) {
        if (window.MediaRecorder.isTypeSupported('audio/mp4')) mimeType = 'audio/mp4';
        else if (window.MediaRecorder.isTypeSupported('audio/webm')) mimeType = 'audio/webm';
      }
      _voiceMimeType = mimeType || 'audio/webm';
      _voiceChunks = [];
      try {
        _voiceRecorder = mimeType ? new MediaRecorder(stream, { mimeType: mimeType }) : new MediaRecorder(stream);
      } catch (e) {
        _voiceRecorder = new MediaRecorder(stream);
      }
      _voiceRecorder.ondataavailable = function(e) {
        if (e.data && e.data.size > 0) _voiceChunks.push(e.data);
      };
      _voiceRecorder.onstop = function() {
        stream.getTracks().forEach(function(tr) { tr.stop(); }); // libera el mic
        var blob = new Blob(_voiceChunks, { type: _voiceMimeType });
        _uploadVoiceNote(blob);
      };
      _voiceRecorder.start();
      _setVoiceState('recording');
    })
    .catch(function() {
      // Permiso denegado (o mic ocupado/ausente) — burbuja i18n, botón a idle.
      _setVoiceState('idle');
      _appendBubble('assistant', t('voice_mic_denied'));
    });
}

function _stopVoiceRecording() {
  _setVoiceState('uploading');
  if (_voiceRecorder && _voiceRecorder.state !== 'inactive') {
    _voiceRecorder.stop(); // dispara onstop -> _uploadVoiceNote
  }
}

function _uploadVoiceNote(blob) {
  _setCoachSuggestionsHidden(true);
  var url = '/api/coach/voice' + (activeConvId ? ('?conversation_id=' + encodeURIComponent(activeConvId)) : '');
  fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': _voiceMimeType },
    body: blob,
  })
    .then(function(r) { return r.json(); })
    .then(function(data) {
      if (data && data.error_key) {
        _appendBubble('assistant', data.message || t('coach_net_error'));
        return;
      }
      if (!data || !data.answer) {
        _appendBubble('assistant', t('coach_no_answer'));
        return;
      }
      _appendBubble('user', '🎤 ' + (data.transcript || ''));
      var answerWrap = _appendBubble('assistant', data.answer);
      if (data.audio_id && answerWrap) {
        var audio = document.createElement('audio');
        audio.controls = true;
        audio.style.marginTop = '6px';
        audio.style.width = '100%';
        audio.src = '/api/coach/voice/audio/' + encodeURIComponent(data.audio_id);
        var bubbleEl = answerWrap.querySelector('.bubble');
        if (bubbleEl) bubbleEl.appendChild(audio);
        // Autoplay puede fallar (política del navegador) — no es un error.
        try { var p = audio.play(); if (p && p.catch) p.catch(function() {}); } catch (e) {}
      }
      if (data.conversation_id && data.conversation_id !== activeConvId) {
        _setActiveConvId(data.conversation_id);
      }
      _loadConversationList(_updateConvSwitcherLabel);
    })
    .catch(function() {
      _appendBubble('assistant', t('coach_net_error'));
    })
    .finally(function() {
      _setVoiceState('idle');
    });
}

function deleteConversation(id, evt) {
  if (evt) evt.stopPropagation();
  if (!confirm(t('coach_delete_conv_confirm'))) return;
  fetch('/api/coach/conversations/' + encodeURIComponent(id), { method: 'DELETE' })
    .then(function() {
      var wasActive = (id === activeConvId);
      _loadConversationList(function() {
        _renderCoachConvModalList();
        if (wasActive) {
          // La activa se borró: caer a la más reciente restante, o a ninguna.
          var next = _coachConvListCache.length ? _coachConvListCache[0].id : null;
          _setActiveConvId(next);
          _updateConvSwitcherLabel();
          _loadActiveConversationThread();
        }
      });
    })
    .catch(function() {
      // Nunca romper la UI si el DELETE falla en red.
    });
}

function _renderCoachConvModalList() {
  var body = document.getElementById('coachConvModalBody');
  if (!body) return;
  if (!_coachConvListCache.length) {
    body.innerHTML = '';
    var empty = document.createElement('div');
    empty.className = 'coach-conv-empty';
    empty.textContent = t('coach_no_conversations');
    body.appendChild(empty);
    return;
  }
  body.innerHTML = '';
  _coachConvListCache.forEach(function(conv) {
    var row = document.createElement('div');
    row.className = 'coach-conv-row' + (conv.id === activeConvId ? ' active' : '');
    row.onclick = function() { openConversation(conv.id); };

    var main = document.createElement('div');
    main.className = 'coach-conv-row-main';

    var titleEl = document.createElement('div');
    titleEl.className = 'coach-conv-row-title';
    // XSS: título viene del texto del usuario -> textContent SIEMPRE (nunca innerHTML).
    titleEl.textContent = conv.title || t('coach_default_title');
    main.appendChild(titleEl);

    var subEl = document.createElement('div');
    subEl.className = 'coach-conv-row-sub';
    subEl.textContent = t('coach_msg_count_fmt').replace('{n}', conv.message_count || 0);
    main.appendChild(subEl);

    row.appendChild(main);

    var delBtn = document.createElement('button');
    delBtn.className = 'coach-conv-row-del';
    delBtn.setAttribute('aria-label', t('coach_delete_conv'));
    delBtn.innerHTML = '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2m3 0-1 14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2L4 6"/></svg>';
    delBtn.onclick = function(evt) { deleteConversation(conv.id, evt); };
    row.appendChild(delBtn);

    body.appendChild(row);
  });
}

function openCoachConvModal() {
  var modal = document.getElementById('coachConvModal');
  if (!modal) return;
  // El modal vive fuera del <script> que corrió applyI18n() en INIT (igual que
  // profileModal/ecgListModal) -> re-aplicar aquí (patrón ECG/perfil).
  applyI18n();
  _loadConversationList(_renderCoachConvModalList);
  modal.classList.remove('hidden');
}

function closeCoachConvModal() {
  var modal = document.getElementById('coachConvModal');
  if (modal) modal.classList.add('hidden');
}

// ── CARD ORDER UI — moved to renderOrderList / _attachOrderHandleDrag above ──

// ── MÁS TAB ──

function renderMas() {
  // QR de emparejamiento: codigo de la URL publica actual (lo que ve el navegador)
  var qb = document.getElementById('qrBox');
  if (qb && !qb.dataset.done) {
    qb.innerHTML = '<img alt="QR" width="190" height="190" src="/api/qr?data=' +
                   encodeURIComponent(window.location.origin) + '">';
    qb.dataset.done = '1';
  }

  // Fase 8C (paso C6): token de HealthKit/ECG visible + embebido en el QR.
  _fetchAndRenderIngestToken();

  // Roadmap P1 (F4, paso 8): sección Programas — catálogo + iniciar/abandonar.
  if (typeof renderProgramsSection === 'function') { renderProgramsSection(); }

  // Theme toggle mirror
  var toggle = document.getElementById('masThemeToggle');
  if (toggle) {
    var cur = localStorage.getItem('vitals-theme') || 'dark';
    toggle.checked = (cur === 'dark');
  }

  // Fase 7: toggle de seguimiento de ciclo — refleja PROFILE.cycle_tracking.
  // Inclusivo: funciona con cualquier sex; solo se muestra un HINT si sex==='F'
  // (sugerido, nunca forzado — ver roadmap "toggle opt-in inclusivo").
  (function(){
    var cycleToggle = document.getElementById('masCycleToggle');
    var subEl = document.getElementById('masCycleToggleSub');
    var detailRow = document.getElementById('masCycleDetailRow');
    if (!cycleToggle) return;
    var enabled = !!(PROFILE && PROFILE.cycle_tracking);
    cycleToggle.checked = enabled;
    if (detailRow) detailRow.style.display = enabled ? '' : 'none';
    if (subEl) {
      if (!enabled && PROFILE && PROFILE.sex === 'F') {
        subEl.textContent = t('mas_cycle_toggle_sub_hint_f');
      } else {
        subEl.textContent = t('mas_cycle_toggle_sub');
      }
    }
  })();

  // Fase 6B: lista de las 4 fuentes conocidas, cada una con su propio estado/acción.
  renderSourcesList();

  // Last sync date
  var lastSyncEl = document.getElementById('masLastSync');
  var updated = summary && summary.updated;
  if (lastSyncEl) {
    if (updated) {
      // Format: "26 de junio, 2026 · 09:14"
      try {
        var dt = new Date(updated);
        var dateStr = dt.getDate() + t('date_de') + _getMeses()[dt.getMonth()] + ', ' + dt.getFullYear()
          + ' · ' + dt.getHours() + ':' + (dt.getMinutes() < 10 ? '0' : '') + dt.getMinutes();
        lastSyncEl.textContent = dateStr;
      } catch(e) {
        lastSyncEl.textContent = updated;
      }
    } else {
      lastSyncEl.textContent = t('mas_no_sync');
    }
  }

  // Card order: inline list removed (now in popup via openOrderPopup)
}

// Fase 6B: nombres de display de cada fuente conocida (reusa claves i18n existentes).
var _SOURCE_LABELS = {
  google_health: 'mas_google',
  oura: 'mas_source_oura',
  whoop: 'mas_source_whoop',
  healthkit: 'mas_source_healthkit'
};
var _SOURCES_ORDER = ['google_health', 'oura', 'whoop', 'healthkit'];

// ── Roadmap P1, F7 (paso 11): procedencia por fuente ────────────────────────
// Etiquetas i18n de cada métrica (clave del by_metric -> clave i18n legible).
// Reusa el vocabulario ya existente en la app donde aplica (hm_rhr, hm_hrv,
// etc.) para no duplicar traducciones.
var _METRIC_DISPLAY_KEYS = {
  rhr: 'hm_rhr', hrv: 'hm_hrv', resp: 'hm_resp', vo2: 'df_vo2max', spo2: 'hm_spo2',
  skin: 'hm_temp', steps: 'df_steps', distance_km: 'df_distance', energy_kcal: 'df_energy',
  sleep: 'ring_sleep', exercises: 'tend_workouts_lbl',
};

var _MODE_LABEL_KEYS = {
  avg: 'source_mode_avg', canonical: 'source_mode_canonical',
  max: 'source_mode_max', 'per-night': 'source_mode_per_night', dedup: 'source_mode_dedup',
};

/** Invierte summary.merge_info.by_metric: {source -> [claves de métrica]}.
 * Con merge de 1 sola fuente o sin merge_info -> {} (gate del roadmap: CERO
 * badges/matriz cuando no hay nada honesto que mostrar — nada inventado). */
function _metricsBySourceFromMergeInfo() {
  var mergeInfo = (summary && summary.merge_info) || null;
  if (!mergeInfo || !mergeInfo.by_metric || (mergeInfo.n_sources || 0) < 2) return {};
  var out = {};
  Object.keys(mergeInfo.by_metric).forEach(function(metricKey) {
    var entry = mergeInfo.by_metric[metricKey];
    var srcs = entry.source ? [entry.source] : (entry.sources || []);
    srcs.forEach(function(src) {
      out[src] = out[src] || [];
      out[src].push(metricKey);
    });
  });
  return out;
}

/** Línea "Sueño (por noche), HRV (canónico)" para una fuente conectada, o ''
 * si no aportó nada al último merge conocido (o solo hay 1 fuente — gate del
 * roadmap). Cada métrica incluye su modo de fusión entre paréntesis (criterio
 * del roadmap: "labels de modo promedio/canónico/max/por-noche" visibles). */
function _sourceContributionLine(sourceName) {
  var mergeInfo = (summary && summary.merge_info) || null;
  var byMetric = (mergeInfo && mergeInfo.by_metric) || {};
  var contributed = _metricsBySourceFromMergeInfo();
  var metrics = contributed[sourceName];
  if (!metrics || !metrics.length) return '';
  var labels = metrics.map(function(k) {
    var modeKey = byMetric[k] ? _MODE_LABEL_KEYS[byMetric[k].mode] : null;
    var modeLbl = modeKey ? t(modeKey) : '';
    return t(_METRIC_DISPLAY_KEYS[k] || k) + (modeLbl ? ' (' + modeLbl + ')' : '');
  });
  return t('mas_source_contributed_lbl').replace('{metrics}', labels.join(', '));
}

/** Badge discreto "· via <fuente>" para una métrica en las pantallas deep de
 * HRV/RHR/Sueño (roadmap P1 F7, paso 11). Gate: SOLO si by_metric identifica
 * UNA fuente clara para esa métrica (mode='canonical' -> siempre 1 fuente; o
 * mode='avg'/'per-night'/'max'/'dedup' -> solo si, de hecho, una única fuente
 * contribuyó, ej. las demás no tenían dato ese periodo) Y hay >1 fuente en el
 * merge total — con 1 sola fuente conectada o sin merge_info -> '' (cero
 * badges inventados, mismo gate que _sourceContributionLine). El nombre de
 * la fuente reusa _SOURCE_LABELS. */
function _viaSourceSuffix(metricKey) {
  var mergeInfo = (summary && summary.merge_info) || null;
  if (!mergeInfo || !mergeInfo.by_metric || (mergeInfo.n_sources || 0) < 2) return '';
  var entry = mergeInfo.by_metric[metricKey];
  if (!entry) return '';
  var srcName = entry.source || ((entry.sources && entry.sources.length === 1) ? entry.sources[0] : null);
  if (!srcName) return '';
  var label = t(_SOURCE_LABELS[srcName] || srcName);
  return ' · ' + t('mas_source_via_lbl').replace('{source}', label);
}

function renderSourcesList() {
  var container = document.getElementById('masSourcesList');
  if (!container) return;
  fetch('/api/sources')
    .then(function(r) { return r.json(); })
    .then(function(data) {
      _renderSourcesRows(container, data || {});
    })
    .catch(function() {
      // Sin conexión / error de red: deja la lista como estaba (no rompe la pantalla).
    });
}

function _renderSourcesRows(container, sources) {
  var html = '';
  for (var i = 0; i < _SOURCES_ORDER.length; i++) {
    var name = _SOURCES_ORDER[i];
    var info = sources[name] || { connected: false, status: 'no_token' };
    html += _sourceRowHtml(name, info);
  }
  container.innerHTML = html;
}

function _sourceRowHtml(name, info) {
  var label = t(_SOURCE_LABELS[name] || name);
  var isHealthKit = name === 'healthkit';
  var connected = !!info.connected;
  var status = info.status;

  var badgeClass = 'off';
  var badgeText = t('mas_source_not_connected');
  var sub = '';
  var actionsHtml = '';

  if (isHealthKit) {
    // HealthKit: badge y hint PROPIOS, nunca comparte el label "Expirado" de OAuth
    // (ese fue el bug reportado — HealthKit no tiene concepto de expiración).
    if (connected && status === 'active') {
      badgeClass = 'ok';
      badgeText = t('mas_healthkit_status_active');
      sub = t('mas_source_healthkit_sync_hint');
      actionsHtml = _disconnectBtnHtml(name, label);
    } else {
      badgeClass = 'off';
      badgeText = t('mas_healthkit_status_inactive');
      sub = t('mas_source_healthkit_sync_hint');
      // En la app nativa iOS SÍ se puede pedir el permiso + sincronizar desde
      // aquí (botón que dispara el plugin). En web pura no hay forma de invocar
      // el permiso nativo -> sin botón (solo el hint informativo).
      actionsHtml = _isNativeApp()
        ? '<button type="button" id="hkConnectBtn" class="mas-source-btn" onclick="healthkitConnect()">' + t('mas_healthkit_connect_btn') + '</button>'
        : '';
    }
  } else {
    // Fuentes OAuth (google_health / oura / whoop)
    // 'expiring' = token vigente pero por caducar pronto (ver Source.auth_state() en
    // app/sources/base.py) — SIGUE conectado y funcionando, no es lo mismo que 'expired'.
    if (connected && (status === 'active' || status === 'expiring')) {
      badgeClass = 'ok';
      badgeText = t('mas_source_connected');
      actionsHtml = _disconnectBtnHtml(name, label);
    } else if (connected && (status === 'expired' || status === 'no_token' || status === 'error')) {
      badgeClass = 'warn';
      badgeText = t('mas_badge_expired');
      actionsHtml = _reconnectBtnHtml(name) + _disconnectBtnHtml(name, label);
    } else {
      badgeClass = 'off';
      badgeText = t('mas_source_not_connected');
      actionsHtml = _connectBtnHtml(name);
    }
  }

  // Roadmap P1, F7 (paso 11): qué métricas aportó esta fuente al último
  // merge — SOLO si conectada y con >1 fuente en el merge (gate honesto,
  // ver _sourceContributionLine). Con 1 sola fuente o sin merge_info -> '',
  // la fila queda IDÉNTICA a como estaba antes de este paso.
  var contribLine = connected ? _sourceContributionLine(name) : '';

  return '<div class="mas-source-row">'
    + '<div>'
    + '<div class="mas-source-name">' + label + '</div>'
    + (sub ? '<div class="mas-source-sub">' + sub + '</div>' : '')
    + (contribLine ? '<div class="mas-source-sub">' + escHtml(contribLine) + '</div>' : '')
    + '</div>'
    + '<div class="mas-source-actions">'
    + '<span class="mas-source-status ' + badgeClass + '">' + badgeText + '</span>'
    + actionsHtml
    + '</div>'
    + '</div>';
}

function _connectBtnHtml(name) {
  return '<button type="button" class="mas-source-btn connect" onclick="sourceConnect(\'' + name + '\')">'
    + t('mas_source_connect_btn') + '</button>';
}

function _reconnectBtnHtml(name) {
  return '<button type="button" class="mas-source-btn connect" onclick="sourceConnect(\'' + name + '\')">'
    + t('mas_source_reconnect_btn') + '</button>';
}

function _disconnectBtnHtml(name, label) {
  return '<button type="button" class="mas-source-btn disconnect" onclick="sourceDisconnect(\'' + name + '\',\'' + label.replace(/'/g, "\\'") + '\')">'
    + t('mas_source_disconnect_btn') + '</button>';
}

function sourceConnect(name) {
  location.href = '/auth/login?source=' + encodeURIComponent(name);
}

// ── HealthKit: pedir el permiso nativo + sincronizar DESDE LA UI ─────────────
// El permiso de HealthKit solo se puede pedir desde la app nativa iOS (plugin
// VitalsHealth). Antes SOLO se disparaba en sceneDidBecomeActive, así que un
// usuario nuevo que se configuraba dentro de la misma sesión NUNCA veía la hoja
// de permiso (el "become active" ya había pasado y no se re-dispara). Este botón
// lo invoca explícitamente — mismo método que ya usa el auto-sync, expuesto al
// WebView por el plugin.
function _isNativeApp() {
  return !!(window.Capacitor && typeof window.Capacitor.isNativePlatform === 'function'
    && window.Capacitor.isNativePlatform());
}
function _vitalsHealthPlugin() {
  return (window.Capacitor && window.Capacitor.Plugins && window.Capacitor.Plugins.VitalsHealth) || null;
}
// Sync con reintento ante "already_running": el sync global es single-flight
// (_SYNC_LOCK); al abrir la app, el autoSync de Google suele tener el lock y el
// ingest de HealthKit choca. En vez de fallar, esperamos ~4s y reintentamos
// (hasta `left` veces, ~40s) — mismo patrón que autoSync/_trySync del dashboard.
function _hkSyncWithRetry(plugin, left) {
  return plugin.sync().then(function (syncRes) {
    var status = syncRes && syncRes.status;
    if (status === 'already_running' && left > 0) {
      return new Promise(function (resolve) { setTimeout(resolve, 4000); })
        .then(function () { return _hkSyncWithRetry(plugin, left - 1); });
    }
    if (status && status !== 'ok') { throw new Error(status); }
    return syncRes;
  });
}
function healthkitConnect() {
  var plugin = _vitalsHealthPlugin();
  // Defensa: el botón solo se renderiza en la app nativa, pero si por algo se
  // invoca sin plugin (web pura), avisamos en vez de romper.
  if (!plugin) { showRetryToast(null, t('healthkit_connect_err')); return; }
  var btn = document.getElementById('hkConnectBtn');
  if (btn) { btn.disabled = true; btn.textContent = t('healthkit_connecting'); }
  function fail(msg) {
    showRetryToast(healthkitConnect, msg);
    if (btn) { btn.disabled = false; btn.textContent = t('mas_healthkit_connect_btn'); }
  }
  // 1) CONFIGURAR el plugin. El plugin nativo necesita url+token para saber a
  //    dónde empujar (hasConfig). Al reinstalar la app se pierde su config
  //    (UserDefaults/Keychain) y el shell nativo puede no haberla repuesto ->
  //    sync() devolvía "no_config" en silencio. Lo resolvemos aquí: el dashboard
  //    SÍ conoce su propio origin y puede pedir el token de ingest. En household,
  //    HOUSEHOLD_ACTIVE trae el usuario correcto para el header X-Vitals-User.
  // 0) REGISTRAR healthkit como fuente conectada del perfil. El guard de
  //    /api/ingest (app/routes/sync.py) rechaza con "wrong_source" si healthkit
  //    no está en profile.sources. El POST es idempotente (no duplica, no
  //    desconecta las otras fuentes). Sin esto el push llega pero se rechaza.
  fetch('/api/sources/healthkit', { method: 'POST' }).then(function () {
    return fetch('/api/ingest-token');
  }).then(function (r) { return r.json(); }).then(function (data) {
    var token = (data && data.token) || '';
    if (!token) { throw new Error('no_token'); }
    var cfg = { url: window.location.origin, token: token };
    if (typeof HOUSEHOLD_ACTIVE !== 'undefined' && HOUSEHOLD_ACTIVE) { cfg.user = HOUSEHOLD_ACTIVE; }
    return plugin.setConfig(cfg);
  }).then(function () {
    // 2) Permiso: presenta la hoja nativa (idempotente, no-op si ya concedido).
    return plugin.requestAuthorization();
  }).then(function (res) {
    if (!res || !res.granted) { throw new Error('denied'); }
    // 3) Sync con reintento ante "already_running" (otro sync tiene el lock);
    //    _hkSyncWithRetry valida el status real y lanza si no es "ok".
    return _hkSyncWithRetry(plugin, 10);
  }).then(function () {
    renderSourcesList(); // badge -> activo = feedback visual de éxito
  }).catch(function (err) {
    var m = (err && err.message) || 'error';
    // Diagnóstico: el motivo exacto (no_token / no_config / error / status del
    // sync) va entre paréntesis para poder cazarlo desde el dispositivo.
    var msg = (m === 'denied') ? t('healthkit_connect_denied')
      : (t('healthkit_connect_err') + ' (' + m + ')');
    fail(msg);
  });
}

function sourceDisconnect(name, label) {
  var msg = t('mas_disconnect_confirm').replace('{source}', label);
  if (!confirm(msg)) return;
  fetch('/api/sources/' + encodeURIComponent(name), { method: 'DELETE' })
    .then(function() { renderSourcesList(); })
    .catch(function() { renderSourcesList(); });
}

function masToggleTheme(checkbox) {
  setMode(checkbox.checked ? 'dark' : 'light');
  // Keep Más toggle in sync
  var toggle = document.getElementById('masThemeToggle');
  if (toggle) toggle.checked = checkbox.checked;
}

// Fase 7: toggle opt-in del módulo de salud femenina/ciclo. PUT /api/profile
// {cycle_tracking}; en error de red revertimos el checkbox visualmente (no
// dejamos la UI mintiendo sobre un estado que no se guardó).
// ── Notificaciones (Fase 8C, paso C3) ── PUT parcial: solo el subcampo tocado.
function masSaveNotifyField(field, value) {
  var payload = {};
  payload[field] = value;
  fetch('/api/profile', {
    method: 'PUT',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({notifications: payload})
  })
    .then(function(r){ return r.json(); })
    .then(function(data){
      if (PROFILE && data && data.notifications) PROFILE.notifications = data.notifications;
      _updateMasNotifyConfigHint();
    })
    .catch(function(){
      if (typeof showRetryToast === 'function') {
        showRetryToast(function(){ masSaveNotifyField(field, value); });
      }
    });
}

// Roadmap P1 paso 2: si el usuario prende un toggle (Brief/Alertas) sin haber
// configurado ntfy NI Telegram todavía, mostrar un hint señalando Avanzado
// (dependencia explícita del roadmap — los toggles quedaron visibles fuera
// de Avanzado, pero su config técnica vive adentro).
function _updateMasNotifyConfigHint() {
  var hint = document.getElementById('masNotifyConfigHint');
  if (!hint || !PROFILE) return;
  var n = PROFILE.notifications || {};
  var anyToggleOn = n.morning_brief !== false || n.alerts !== false;
  var hasConfig = !!(n.ntfy_url || n.telegram_bot_token);
  hint.style.display = (anyToggleOn && !hasConfig) ? '' : 'none';
}

function _renderMasNotifyFields() {
  if (!PROFILE) return;
  var n = PROFILE.notifications || {};
  var ntfy = document.getElementById('masNotifyNtfyUrl');
  var tgToken = document.getElementById('masNotifyTgToken');
  var tgChat = document.getElementById('masNotifyTgChat');
  var brief = document.getElementById('masNotifyBrief');
  var alerts = document.getElementById('masNotifyAlerts');
  if (ntfy && document.activeElement !== ntfy) ntfy.value = n.ntfy_url || '';
  if (tgToken && document.activeElement !== tgToken) tgToken.value = n.telegram_bot_token || '';
  if (tgChat && document.activeElement !== tgChat) tgChat.value = n.telegram_chat_id || '';
  if (brief) brief.checked = n.morning_brief !== false;
  if (alerts) alerts.checked = n.alerts !== false;
  _updateMasNotifyConfigHint();
}

// ── Roadmap P1 paso 2: sección "Avanzado" colapsable (chevron rotatorio) ──
// Toggle SOLO en memoria (mismo patrón que _insightsExpanded, roadmap P0
// paso 4) — no persiste entre recargas.
var _masAdvancedExpanded = false;
function toggleMasAdvanced() {
  _masAdvancedExpanded = !_masAdvancedExpanded;
  var body = document.getElementById('masAdvancedBody');
  var chevron = document.getElementById('masAdvancedChevron');
  var toggleRow = chevron ? chevron.closest('.mas-advanced-toggle') : null;
  if (body) body.classList.toggle('open', _masAdvancedExpanded);
  if (chevron) chevron.classList.toggle('open', _masAdvancedExpanded);
  if (toggleRow) toggleRow.setAttribute('aria-expanded', _masAdvancedExpanded ? 'true' : 'false');
}

// Token HealthKit/ECG: enmascarado por default (type=password), botón 👁
// revela/oculta alternando el type del input (roadmap P1 paso 2).
var _masTokenRevealed = false;
function masToggleIngestTokenVisibility() {
  _masTokenRevealed = !_masTokenRevealed;
  var input = document.getElementById('masIngestToken');
  var btn = document.getElementById('masTokenRevealBtn');
  if (input) input.type = _masTokenRevealed ? 'text' : 'password';
  if (btn) {
    btn.textContent = _masTokenRevealed ? '🙈' : '👁';
    var alKey = _masTokenRevealed ? 'mas_token_hide_al' : 'mas_token_reveal_al';
    btn.setAttribute('data-i18n-al', alKey);
    btn.setAttribute('aria-label', t(alKey));
  }
}

// ── Token de ingest (Fase 8C, paso C6) ──────────────────────────────────────
// Fetch 1x (cacheado en un data-attr) — el token no cambia en caliente salvo
// que se edite ingest_token.json a mano, así que no hace falta re-consultarlo
// en cada render de Más.
function _fetchAndRenderIngestToken() {
  var input = document.getElementById('masIngestToken');
  if (!input || input.dataset.loaded) return;
  fetch('/api/ingest-token')
    .then(function(r) { return r.json(); })
    .then(function(data) {
      var token = (data && data.token) || '';
      input.value = token;
      input.dataset.loaded = '1';
      // Re-pinta el QR con el token embebido como query param — la pantalla
      // nativa "Conecta tu Vitals" hoy pide pegar el token a mano (ver
      // docs/IOS-HEALTHKIT.md); dejamos la URL lista por si un futuro lector
      // de QR nativo lo consume directo, sin romper el flujo actual (un
      // query param extra no afecta abrir la PWA en el navegador).
      //
      // Fase 8D (paso D3, household): si hay un usuario ACTIVO de household
      // (HOUSEHOLD_ACTIVE, poblado por household.js al entrar a Más), se
      // embebe también ?vitals_user= — así el emparejamiento desde iOS ya
      // trae el usuario correcto sin que el Doc tenga que configurarlo a
      // mano por dispositivo. Instalaciones sin household (single-user,
      // caso de hoy) nunca definen HOUSEHOLD_ACTIVE -> el QR queda IDÉNTICO
      // al de antes de esta fase.
      var qb = document.getElementById('qrBox');
      if (qb && token) {
        var urlWithToken = window.location.origin + '/?ingest_token=' + encodeURIComponent(token);
        if (typeof HOUSEHOLD_ACTIVE !== 'undefined' && HOUSEHOLD_ACTIVE) {
          urlWithToken += '&vitals_user=' + encodeURIComponent(HOUSEHOLD_ACTIVE);
        }
        qb.innerHTML = '<img alt="QR" width="190" height="190" src="/api/qr?data=' +
                       encodeURIComponent(urlWithToken) + '">';
      }
    })
    .catch(function() { /* sin conexión: el campo queda vacío, reintenta en el próximo render de Más */ });
}

function masCopyIngestToken() {
  var input = document.getElementById('masIngestToken');
  if (!input || !input.value) return;
  try {
    input.select();
    input.setSelectionRange(0, 99999);
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(input.value);
    } else {
      document.execCommand('copy');
    }
  } catch (e) { /* best-effort: si falla el copiado, el usuario aún puede seleccionar a mano */ }
}

function masToggleCycle(checkbox) {
  var desired = checkbox.checked;
  fetch('/api/profile', {
    method: 'PUT',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({cycle_tracking: desired})
  })
    .then(function(r){ return r.json(); })
    .then(function(data){
      if (PROFILE) PROFILE.cycle_tracking = !!(data && data.cycle_tracking);
      checkbox.checked = !!(data && data.cycle_tracking);
      var detailRow = document.getElementById('masCycleDetailRow');
      if (detailRow) detailRow.style.display = checkbox.checked ? '' : 'none';
      // Refrescar la tarjeta de Hoy sin recargar toda la app.
      fetchCycleState();
    })
    .catch(function(){
      checkbox.checked = !desired; // revertir en error de red
    });
}

// ── PROGRAMAS (Roadmap P1, F4, paso 8) — sección "Más" ──────────────────────
// Catálogo de las 4 plantillas + botón iniciar/abandonar según haya o no un
// plan activo ya en curso. Se re-renderiza cada vez que se abre Más (mismo
// patrón que renderMas() en general — barato, sin caché de estado propio).
function renderProgramsSection(){
  var list = document.getElementById('masProgramsList');
  if(!list) return;

  Promise.all([
    fetch('/api/programs').then(function(r){ return r.json(); }),
    fetch('/api/plan').then(function(r){ return r.json(); }),
  ]).then(function(results){
    var catalog = Array.isArray(results[0]) ? results[0] : [];
    var planStatus = results[1] || {};
    window._plansCatalogCache = catalog; // reusado por renderPlanCard() para el nombre localizado
    _renderProgramsList(list, catalog, planStatus);
  }).catch(function(){
    list.innerHTML = '';
  });
}

function _renderProgramsList(list, catalog, planStatus){
  var activeId = planStatus && planStatus.active ? planStatus.program_id : null;
  var html = catalog.map(function(p){
    var isActive = p.id === activeId;
    var actionBtn = isActive
      ? '<button onclick="programAbandon(event)" style="padding:6px 14px;border-radius:10px;border:1px solid rgba(255,55,95,.3);'
        + 'background:rgba(255,55,95,.1);color:#FF375F;font:600 12px -apple-system;cursor:pointer">'
        + escHtml(t('plan_abandon_btn')) + '</button>'
      : '<button onclick="programStart(event,\''+p.id+'\')" '
        + (activeId ? 'disabled style="opacity:.4;pointer-events:none;' : 'style="')
        + 'padding:6px 14px;border-radius:10px;border:none;background:#0A84FF;color:#fff;'
        + 'font:600 12px -apple-system;cursor:pointer">'
        + escHtml(t('plan_start_btn')) + '</button>';
    return '<div class="mas-row" style="align-items:flex-start">'
      + '<div style="flex:1">'
        + '<div class="mas-row-label">' + escHtml(p.name) + (isActive ? ' <span style="color:#30D158">●</span>' : '') + '</div>'
        + '<div class="mas-row-sub">' + escHtml(p.description) + ' · ' + p.duration_days + ' ' + escHtml(t('plan_days_unit')) + '</div>'
      + '</div>'
      + actionBtn
    + '</div>';
  }).join('');
  list.innerHTML = html;
}

function programStart(ev, programId){
  if(ev) ev.stopPropagation();
  fetch('/api/plan', {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({program_id: programId}),
  })
    .then(function(r){ return r.json(); })
    .then(function(){ renderProgramsSection(); fetchAndRenderPlanCard(); })
    .catch(function(){});
}

function programAbandon(ev){
  if(ev) ev.stopPropagation();
  if(!window.confirm(t('plan_abandon_confirm'))) return;
  fetch('/api/plan', {method: 'DELETE'})
    .then(function(r){ return r.json(); })
    .then(function(){ renderProgramsSection(); fetchAndRenderPlanCard(); })
    .catch(function(){});
}

// ── PATCH goTab to call renderCoachTab / renderMas / renderSleepTab ──
var _origGoTab = goTab;
var sleepTabRendered = false; // lazy-render: solo se arma el tab Sueño la 1ª vez que se visita
goTab = function(screenId) {
  _origGoTab(screenId);
  if (screenId === 'screenCoach' && !coachRendered) { renderCoachTab(); }
  if (screenId === 'screenMas') { renderMas(); }
  if (screenId === 'screenSleep' && !sleepTabRendered) { sleepTabRendered = true; renderSleepTab(); }
};

// ── INIT ──
renderCoach();
renderAuth();
renderHoy();
applyOrder('hoy');
applyOrder('tend');
applyI18n();
autoSync();   // auto-actualiza al abrir/recargar (background + recarga con datos frescos)

// Fase 8B: Journal (card "Diario" en Hoy) — 1 sola carga al inicio (no en
// cada scrubBy/renderHoy, que se dispara mucho más seguido). setLocale()
// también la refresca para que las labels salgan en el idioma nuevo.
if (typeof fetchJournalState === 'function') { fetchJournalState(); }

// Fase 8C (paso C4): Sleep Coach — 1 sola carga al inicio, misma cadencia que Journal.
if (typeof fetchAndRenderSleepCoach === 'function') { fetchAndRenderSleepCoach(); }

// Roadmap P1 (F4, paso 8): Plan activo (planCard en Hoy) + catálogo (para
// resolver el nombre localizado del programa activo) — 1 sola carga al inicio.
if (typeof fetchAndRenderPlanCard === 'function') {
  fetch('/api/programs').then(function(r){ return r.json(); }).then(function(cat){
    window._plansCatalogCache = Array.isArray(cat) ? cat : [];
    fetchAndRenderPlanCard();
  }).catch(function(){ fetchAndRenderPlanCard(); });
}

// ── PROFILE HELPERS ──
function _profileInitial(name) {
  if (!name || !name.trim()) return '?';
  return name.trim()[0].toUpperCase();
}

function _updateAvatars() {
  var initial = _profileInitial(PROFILE && PROFILE.name);
  var ha = document.getElementById('headerAvatar');
  if (ha) ha.childNodes[0].textContent = initial;
  var ma = document.getElementById('masAvatarEl');
  if (ma) ma.textContent = initial;
}

// ── renderMas — update to use PROFILE data ──
var _origRenderMas = renderMas;
renderMas = function() {
  _origRenderMas();
  if (!PROFILE) return;
  var nameEl = document.getElementById('masNameEl');
  var emailEl = document.getElementById('masEmailEl');
  var ageEl = document.getElementById('masAgeVal');
  var heightEl = document.getElementById('masHeightVal');
  var weightEl = document.getElementById('masWeightVal');
  var avatarEl = document.getElementById('masAvatarEl');

  if (nameEl) nameEl.textContent = PROFILE.name || '—';
  if (emailEl) emailEl.textContent = PROFILE.email || '';
  if (ageEl) ageEl.textContent = PROFILE.age || '—';
  // Unit-aware display
  if (heightEl) heightEl.textContent = PROFILE.height_cm ? fmtHeight(PROFILE.height_cm) : '—';
  if (weightEl) weightEl.textContent = PROFILE.weight_kg ? fmtWeight(PROFILE.weight_kg) : '—';
  if (avatarEl) avatarEl.textContent = _profileInitial(PROFILE.name);
  renderMasLocaleControls();
  _renderMasNotifyFields();
};

// Render the locale + units rows inside #masLocaleArea (injected into HTML)
function renderMasLocaleControls() {
  var el = document.getElementById('masLocaleArea');
  if (!el) return;
  var loc = PROFILE && PROFILE.locale || 'es';
  var units = PROFILE && PROFILE.units || 'metric';
  var langs = [
    {code:'es', label:'Español'}, {code:'en', label:'English'},
    {code:'fr', label:'Français'}, {code:'pt', label:'Português'}
  ];
  var langBtns = langs.map(function(l){
    var active = loc === l.code ? ' active' : '';
    return '<button class="ob-seg-btn'+active+'" onclick="setLocale(\''+l.code+'\')">'+l.label+'</button>';
  }).join('');
  var unitBtns =
    '<button class="ob-seg-btn'+(units==='metric'?' active':'')+'" onclick="setUnits(\'metric\')">'+t('mas_metric')+'</button>'+
    '<button class="ob-seg-btn'+(units==='imperial'?' active':'')+'" onclick="setUnits(\'imperial\')">'+t('mas_imperial')+'</button>';
  el.innerHTML =
    '<div class="mas-section-lbl">'+t('mas_lang_lbl')+'</div>'+
    '<div class="mas-row" style="padding:12px 16px"><div class="ob-seg" style="flex:1;margin:0">'+langBtns+'</div></div>'+
    '<div class="mas-section-lbl">'+t('mas_units_lbl')+'</div>'+
    '<div class="mas-row" style="padding:12px 16px"><div class="ob-seg" style="flex:1;margin:0">'+unitBtns+'</div></div>';
  // Also update height/weight stat label to show unit
  var htLbl = document.getElementById('masHeightLbl');
  var wtLbl = document.getElementById('masWeightLbl');
  if (htLbl) htLbl.textContent = units === 'imperial' ? 'ft/in' : 'cm';
  if (wtLbl) wtLbl.textContent = units === 'imperial' ? 'lb' : 'kg';
}

// ── ONBOARDING / PROFILE FORM ──
// Reutiliza el mismo form para onboarding y edición de perfil.
// isOnboarding=true → overlay full-screen; false → modal sheet.

function openProfileForm(isOnboarding) {
  var p = PROFILE || {};
  var imperial = p.units === 'imperial';
  // Display values in current units
  var waistDisp = p.waist_cm ? (imperial ? Math.round(p.waist_cm/2.54) : Math.round(p.waist_cm)) : '';
  var heightDisp = p.height_cm ? (imperial ? fmtHeight(p.height_cm) : Math.round(p.height_cm)) : '';
  var weightDisp = p.weight_kg ? (imperial ? Math.round(p.weight_kg*2.20462) : Math.round(p.weight_kg)) : '';
  var waistUnit = _waistUnit();
  var weightUnit = _weightUnit();
  // Height: for imperial use ft/in via text input; for metric use number
  var heightInputHtml = imperial
    ? '<input class="ob-input" id="pf-height" type="text" placeholder="5\'10&quot;" value="' + _esc(p.height_cm ? fmtHeight(p.height_cm) : '') + '">'
    : '<input class="ob-input" id="pf-height" type="number" min="100" max="250" placeholder="170" value="' + (p.height_cm ? Math.round(p.height_cm) : '') + '">';
  // Build form HTML
  var html = '<div class="ob-form" id="profileFormInner">'
    // Language selector in onboarding
    + (isOnboarding ? '<div class="ob-field"><label class="ob-label">'+t('ob_lang')+'</label>'
      + '<div class="ob-seg">'
      + ['es','en','fr','pt'].map(function(lc){var lbl={es:'Español',en:'English',fr:'Français',pt:'Português'}[lc]; return '<button class="ob-seg-btn'+(( p.locale||'es')===lc?' active':'')+'" onclick="pfLocaleSel(\''+lc+'\')">'+lbl+'</button>';}).join('')
      + '</div></div>'
      + '<div class="ob-field"><label class="ob-label">'+t('ob_units')+'</label>'
      + '<div class="ob-seg">'
      + '<button class="ob-seg-btn'+(!imperial?' active':'')+'" onclick="pfUnitsSel(\'metric\')">'+t('mas_metric')+'</button>'
      + '<button class="ob-seg-btn'+(imperial?' active':'')+'" onclick="pfUnitsSel(\'imperial\')">'+t('mas_imperial')+'</button>'
      + '</div></div>'
      : '')
    // Source selector: visible en onboarding Y en edición de perfil (Más).
    // Fase 6A/6B: profile.sources puede traer VARIAS fuentes conectadas a la vez (fusión) —
    // se resaltan TODAS las conectadas, no solo una (antes solo marcaba profile.source,
    // el campo legado singular, aunque hubiera 2+ fuentes activas fusionándose).
    + (function(){
        var connectedSources = (p.sources && p.sources.length) ? p.sources : [p.source || 'google_health'];
        var pendingAdd = _pfSourceVal; // fuente recién clickeada, se conecta al Guardar (puede ser una nueva)
        return '<div class="ob-field"><label class="ob-label">'+t('ob_source')+'</label>'
          + '<div class="ob-seg">'
          + ['google_health','oura','whoop','healthkit'].map(function(sc){
              var lbl = sc==='google_health' ? t('ob_source_google')
                : (sc==='oura' ? t('ob_source_oura')
                : (sc==='whoop' ? t('ob_source_whoop') : t('ob_source_healthkit')));
              var active = connectedSources.indexOf(sc) !== -1 || pendingAdd === sc;
              return '<button class="ob-seg-btn'+(active?' active':'')+'" onclick="pfSourceSel(\''+sc+'\')">'+lbl+'</button>';
            }).join('')
          + '</div>'
          + ((connectedSources.indexOf('healthkit') !== -1 || pendingAdd === 'healthkit')
              ? '<span class="ob-hint">'+t('ob_source_healthkit_hint')+'</span>'
                // En la app nativa, botón para pedir el permiso de HealthKit aquí
                // mismo (sin depender de re-abrir la app). En web pura solo el hint.
                + (_isNativeApp() ? '<button type="button" class="ob-seg-btn" style="margin-top:8px;width:100%" onclick="healthkitConnect()">'+t('mas_healthkit_connect_btn')+'</button>' : '')
              : '')
          + '</div>';
      })()
    + '<div class="ob-field"><label class="ob-label">'+t('ob_name')+'</label>'
    + '<input class="ob-input" id="pf-name" type="text" placeholder="'+t('ob_name')+'" value="'
    + _esc(p.name||'') + '"></div>'
    + '<div class="ob-field"><label class="ob-label">'+t('ob_email')+'</label>'
    + '<input class="ob-input" id="pf-email" type="email" placeholder="email@example.com" value="'
    + _esc(p.email||'') + '"></div>'
    + '<div class="ob-field"><label class="ob-label">'+t('ob_birthdate')+'</label>'
    + '<input class="ob-input" id="pf-birthdate" type="date" value="'
    + _esc(p.birthdate||'') + '"></div>'
    + '<div class="ob-field"><label class="ob-label">'+t('ob_sex')+'</label>'
    + '<div class="ob-seg">'
    + '<button class="ob-seg-btn' + (p.sex==='M'?' active':'') + '" id="pf-sex-M" onclick="pfSexSel(\'M\')">'+t('ob_male')+'</button>'
    + '<button class="ob-seg-btn' + (p.sex==='F'?' active':'') + '" id="pf-sex-F" onclick="pfSexSel(\'F\')">'+t('ob_female')+'</button>'
    + '</div></div>'
    + '<div class="ob-field"><label class="ob-label">'+t('ob_waist').replace('{unit}',waistUnit)+'</label>'
    + '<input class="ob-input" id="pf-waist" type="number" min="1" max="500" placeholder="" value="'
    + waistDisp + '">'
    + '<span class="ob-hint">'+t('ob_waist_hint')+'</span></div>'
    // Sleep-goal-vs-need: sección "Tus metas" — separa el OBJETIVO personal
    // (sleep_goal_min, editable libre) de la NECESIDAD fisiológica
    // (sleep_target_min, avanzado, dispara confirm() al cambiar — ver
    // submitProfileForm). Meta de pasos MOVIDA aquí desde su ubicación
    // anterior (junto a cintura).
    + '<div class="ob-section-lbl">'+t('pf_goals_lbl')+'</div>'
    + '<div class="ob-section-sub">'+t('pf_goals_sub')+'</div>'
    // El usuario ve y edita HORAS (5–10, paso 0.5), no minutos crudos: el
    // backend guarda minutos (300–600) y submitProfileForm convierte h→min.
    // Math.round(min/60*100)/100 da horas EXACTAS para cualquier valor legado
    // (múltiplo de 15 min -> múltiplo de 0.25 h), sin decimales infinitos.
    + '<div class="ob-field"><label class="ob-label">'+t('pf_sleep_goal')+'</label>'
    + '<input class="ob-input" id="pf-sleep-goal" type="number" min="5" max="10" step="0.5" placeholder="8" value="'
    + (p.sleep_goal_min != null ? (Math.round(p.sleep_goal_min/60*100)/100) : '') + '">'
    + '<span class="ob-hint">'+t('pf_sleep_goal_hint')+'</span>'
    + (function(){
        var med = _sleepMedianHint();
        return med == null ? '' : '<span class="ob-hint" id="pf-sleep-goal-median">'+t('pf_sleep_goal_median').replace('{h}', med)+'</span>';
      })()
    + '</div>'
    + '<div class="ob-field"><label class="ob-label">'+t('pf_sleep_need')+'</label>'
    + '<input class="ob-input" id="pf-sleep-target" type="number" min="5" max="10" step="0.5" placeholder="8" value="'
    + (p.sleep_target_min != null ? (Math.round(p.sleep_target_min/60*100)/100) : '') + '">'
    + '<span class="ob-hint">'+t('pf_sleep_need_hint')+'</span></div>'
    + '<div class="ob-field"><label class="ob-label">'+t('ob_steps_target')+'</label>'
    + '<input class="ob-input" id="pf-steps-target" type="number" min="1000" max="50000" step="100" placeholder="8000" value="'
    + (p.steps_target != null ? p.steps_target : '') + '">'
    + '<span class="ob-hint">'+t('ob_steps_target_hint')+'</span></div>'
    + '<div class="ob-field"><label class="ob-label">'+t('ob_height').replace('{unit}', imperial?'ft/in':'cm')+'</label>'
    + heightInputHtml + '</div>'
    + '<div class="ob-field"><label class="ob-label">'+t('ob_weight').replace('{unit}',weightUnit)+'</label>'
    + '<input class="ob-input" id="pf-weight" type="number" min="1" max="1000" step="0.1" placeholder="" value="'
    + weightDisp + '"></div>'
    // Intake clínico (Ronda 4): metas/lesiones/condiciones/medicamentos — 1 item por línea.
    // Contenido de las listas escapado con _esc() (mismo estándar anti-XSS del resto del
    // form) antes de interpolarse dentro del textarea.
    + '<div class="ob-section-lbl">'+t('ob_clinical_lbl')+'</div>'
    + '<div class="ob-section-sub">'+t('ob_clinical_sub')+'</div>'
    + '<div class="ob-field"><label class="ob-label">'+t('ob_goals')+'</label>'
    + '<textarea class="ob-textarea" id="pf-goals" rows="3">' + _esc((p.goals||[]).join('\n')) + '</textarea>'
    + '<span class="ob-hint">'+t('ob_goals_hint')+'</span></div>'
    + '<div class="ob-field"><label class="ob-label">'+t('ob_injuries')+'</label>'
    + '<textarea class="ob-textarea" id="pf-injuries" rows="2">' + _esc((p.injuries||[]).join('\n')) + '</textarea>'
    + '<span class="ob-hint">'+t('ob_injuries_hint')+'</span></div>'
    + '<div class="ob-field"><label class="ob-label">'+t('ob_conditions')+'</label>'
    + '<textarea class="ob-textarea" id="pf-conditions" rows="2">' + _esc((p.conditions||[]).join('\n')) + '</textarea>'
    + '<span class="ob-hint">'+t('ob_conditions_hint')+'</span></div>'
    + '<div class="ob-field"><label class="ob-label">'+t('ob_medications')+'</label>'
    + '<textarea class="ob-textarea" id="pf-medications" rows="2">' + _esc((p.medications||[]).join('\n')) + '</textarea>'
    + '<span class="ob-hint">'+t('ob_medications_hint')+'</span></div>'
    + '<div class="ob-error" id="pf-error"></div>'
    + '<button class="ob-submit" id="pf-submit" onclick="submitProfileForm(' + (isOnboarding?'true':'false') + ')">'
    + (isOnboarding ? t('ob_start') : t('ob_save')) + '</button>'
    + '</div>';

  if (isOnboarding) {
    var ov = document.getElementById('onboardOverlay');
    if (!ov) return;
    document.getElementById('obFormArea').innerHTML = html;
    // Pre-fill sex default M if not set
    if (!p.sex) pfSexSel('M');
    ov.classList.remove('hidden');
  } else {
    var modal = document.getElementById('profileModal');
    if (!modal) return;
    document.getElementById('profileModalForm').innerHTML = html;
    if (!p.sex) pfSexSel('M');
    modal.classList.remove('hidden');
  }
}

// Sleep-goal-vs-need: hint DESCRIPTIVO (no prescriptivo) bajo Meta de sueño —
// mediana real de 'asleep' de las últimas 90 noches del global `days` (línea
// ~22). null si hay <14 noches con dato (onboarding / usuario nuevo con
// `days` vacío) -> el llamador omite el hint en vez de renderizar "NaNh".
// Prohibido sugerir un óptimo: solo reporta lo que YA pasó.
function _sleepMedianHint() {
  var recent = (days || []).slice(-90)
    .map(function(d){ return d && d.asleep; })
    .filter(function(v){ return v != null && !isNaN(v); });
  if (recent.length < 14) return null;
  var sorted = recent.slice().sort(function(a,b){ return a - b; });
  var mid = Math.floor(sorted.length / 2);
  var medianMin = sorted.length % 2 === 0 ? (sorted[mid-1] + sorted[mid]) / 2 : sorted[mid];
  return (medianMin / 60).toFixed(1) + 'h';
}

function _esc(s) {
  return String(s).replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/</g,'&lt;');
}

var _pfSexVal = 'M';
function pfSexSel(val) {
  _pfSexVal = val;
  ['M','F'].forEach(function(v){
    var btn = document.getElementById('pf-sex-'+v);
    if (btn) btn.className = 'ob-seg-btn' + (v===val?' active':'');
  });
}

var _pfLocaleVal = null; // null = use PROFILE.locale
function pfLocaleSel(val) {
  _pfLocaleVal = val;
  // Update locale immediately so labels refresh
  if (PROFILE) PROFILE.locale = val;
  applyI18n();
  // Rebuild form to reflect new language
  var isOnboarding = !document.getElementById('profileModal') || document.getElementById('profileModal').classList.contains('hidden');
  openProfileForm(isOnboarding);
}

var _pfUnitsVal = null; // null = use PROFILE.units
function pfUnitsSel(val) {
  _pfUnitsVal = val;
  if (PROFILE) PROFILE.units = val;
  // Rebuild form to refresh unit labels/conversions
  var isOnboarding = !document.getElementById('profileModal') || document.getElementById('profileModal').classList.contains('hidden');
  openProfileForm(isOnboarding);
}

var _pfSourceVal = null; // null = use PROFILE.source
function pfSourceSel(val) {
  _pfSourceVal = val;
  // Rebuild form to refresh the active segmented button (no rebuild side-effects needed
  // beyond visual state — el connect/reconnect flow vive en la sección Más).
  var isOnboarding = !document.getElementById('profileModal') || document.getElementById('profileModal').classList.contains('hidden');
  openProfileForm(isOnboarding);
}

function closeProfileModal() {
  var modal = document.getElementById('profileModal');
  if (modal) modal.classList.add('hidden');
}

function submitProfileForm(isOnboarding) {
  var errEl = document.getElementById('pf-error');
  var btn = document.getElementById('pf-submit');
  if (errEl) errEl.textContent = '';

  var name = (document.getElementById('pf-name')||{}).value||'';
  var email = (document.getElementById('pf-email')||{}).value||'';
  var birthdate = (document.getElementById('pf-birthdate')||{}).value||'';
  var waistRaw = (document.getElementById('pf-waist')||{}).value||'';
  var stepsTargetRaw = (document.getElementById('pf-steps-target')||{}).value||'';
  var sleepGoalRaw = (document.getElementById('pf-sleep-goal')||{}).value||'';
  var sleepTargetRaw = (document.getElementById('pf-sleep-target')||{}).value||'';
  var heightRaw = (document.getElementById('pf-height')||{}).value||'';
  var weightRaw = (document.getElementById('pf-weight')||{}).value||'';
  var imperial = PROFILE && PROFILE.units === 'imperial';

  // Intake clínico (Ronda 4): 1 item por línea -> lista de strings. El trim/filtro
  // final de vacíos y el cap de 10x120 chars lo hace el backend (_clean_str_list en
  // main.py) — aquí solo separamos por línea, sin duplicar esa lógica de validación.
  var _textareaLines = function(id) {
    var el = document.getElementById(id);
    if (!el || !el.value) return [];
    return el.value.split('\n');
  };
  var goalsList = _textareaLines('pf-goals');
  var injuriesList = _textareaLines('pf-injuries');
  var conditionsList = _textareaLines('pf-conditions');
  var medicationsList = _textareaLines('pf-medications');

  // Validation
  var errors = [];
  if (!name.trim()) errors.push(t('err_name_required'));
  if (!birthdate) errors.push(t('err_birthdate_required'));
  var waistInput = parseFloat(waistRaw);
  if (!waistRaw || isNaN(waistInput) || waistInput <= 0) errors.push(t('err_waist_invalid'));

  if (errors.length) {
    if (errEl) errEl.textContent = errors.join(' ');
    return;
  }

  // Convert imperial → metric before PUT
  var waist_cm = imperial ? waistInput * 2.54 : waistInput;

  var payload = {
    name: name.trim(),
    email: email.trim() || null,
    birthdate: birthdate,
    sex: _pfSexVal,
    waist_cm: waist_cm,
    onboarded: true,
    goals: goalsList,
    injuries: injuriesList,
    conditions: conditionsList,
    medications: medicationsList
  };

  // Height: imperial accepts "5'10" string or plain inches
  if (heightRaw) {
    var height_cm;
    if (imperial) {
      var ftInMatch = String(heightRaw).match(/^(\d+)['\s]+(\d+)/);
      if (ftInMatch) {
        height_cm = _ftInToCm(ftInMatch[1], ftInMatch[2]);
      } else {
        // assume inches
        height_cm = parseFloat(heightRaw) * 2.54;
      }
    } else {
      height_cm = parseFloat(heightRaw);
    }
    if (height_cm && !isNaN(height_cm)) payload.height_cm = height_cm;
  }

  if (weightRaw) {
    var weight_kg = imperial ? parseFloat(weightRaw) / 2.20462 : parseFloat(weightRaw);
    if (!isNaN(weight_kg)) payload.weight_kg = weight_kg;
  }

  if (stepsTargetRaw) {
    var stepsTargetVal = parseInt(stepsTargetRaw, 10);
    // Clamp defensivo en el front (el backend valida igual, esto solo evita
    // un PUT rechazado por un valor fuera de rango tecleado a mano).
    if (!isNaN(stepsTargetVal)) payload.steps_target = clamp(stepsTargetVal, 1000, 50000);
  }

  // Sleep-goal-vs-need: OBJETIVO (sleep_goal_min) se manda libre, sin aviso —
  // editarlo no toca el motor. NECESIDAD (sleep_target_min) sí re-califica el
  // histórico -> se detecta el cambio para el confirm() de abajo.
  // Los inputs vienen en HORAS (5–10); el backend guarda minutos (300–600).
  // h -> min: parseFloat * 60 redondeado a entero, luego clamp. 8 -> 480,
  // 7.5 -> 450, 6 -> 360 (exactos, no disparan un confirm falso más abajo).
  if (sleepGoalRaw) {
    var sleepGoalVal = parseFloat(sleepGoalRaw);
    if (!isNaN(sleepGoalVal)) payload.sleep_goal_min = clamp(Math.round(sleepGoalVal * 60), 300, 600);
  }
  var sleepTargetChanged = false;
  if (sleepTargetRaw) {
    var sleepTargetVal = parseFloat(sleepTargetRaw);
    if (!isNaN(sleepTargetVal)) {
      var sleepTargetClamped = clamp(Math.round(sleepTargetVal * 60), 300, 600);
      payload.sleep_target_min = sleepTargetClamped;
      sleepTargetChanged = sleepTargetClamped !== (PROFILE && PROFILE.sleep_target_min);
    }
  }

  // Criterio 10: cambiar la NECESIDAD recalcula recovery/edad corporal
  // históricas en el próximo sync -> avisar y proceder. Cambiar SOLO el
  // objetivo (o solo pasos) NO dispara este confirm. Si el usuario cancela,
  // no se manda el PUT (return antes de tocar el botón/fetch).
  if (sleepTargetChanged && !confirm(t('pf_need_change_confirm'))) {
    return;
  }

  // Include locale/units if changed in onboarding form
  if (_pfLocaleVal) payload.locale = _pfLocaleVal;
  if (_pfUnitsVal) payload.units = _pfUnitsVal;
  // Fase 6A: la fuente elegida en el selector se CONECTA vía POST /api/sources/{name}
  // (no PUT /api/profile) — esto AÑADE la fuente sin desconectar las demás, así un
  // usuario que ya tenía 2+ fuentes conectadas por API no pierde la segunda solo por
  // tocar este selector viejo. Limitación temporal conocida: no hay forma de
  // DESCONECTAR desde esta UI hasta 6B (picker real de gestión de conexiones).
  var sourceChanged = _pfSourceVal && _pfSourceVal !== (PROFILE && PROFILE.source);

  if (btn) { btn.disabled = true; btn.textContent = t('ob_saving'); }

  var _sourceConnect = sourceChanged
    ? fetch('/api/sources/' + encodeURIComponent(_pfSourceVal), {method: 'POST'}).catch(function(){})
    : Promise.resolve();

  _sourceConnect.then(function(){
    return fetch('/api/profile', {
      method: 'PUT',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify(payload)
    });
  })
  .then(function(r){ return r.json(); })
  .then(function(p) {
    if (p.status === 'error') {
      var msg = (p.errors||[p.message||t('err_unknown')]).join(', ');
      if (errEl) errEl.textContent = msg;
      if (btn) { btn.disabled = false; btn.textContent = isOnboarding ? t('ob_start') : t('ob_save'); }
      return;
    }
    PROFILE = p;
    _pfLocaleVal = null; _pfUnitsVal = null; _pfSourceVal = null;
    applyI18n();
    _updateAvatars();
    if (isOnboarding) {
      // Disparar sync y recargar
      var ov = document.getElementById('onboardOverlay');
      if (ov) ov.classList.add('hidden');
      if (btn) btn.textContent = t('ob_syncing');
      fetch('/api/sync', {method:'POST'})
        .then(function(r){ return r.json(); })
        .then(function(d){
          if (d && (d.status === 'no_token' || d.status === 'expired')) {
            location.href = '/auth/login';
            return;
          }
          location.reload();
        })
        .catch(function(){ location.reload(); });
    } else {
      closeProfileModal();
      renderMas();
      if (sourceChanged) location.reload(); // refresca AUTH/banner para la nueva fuente
    }
  })
  .catch(function(e) {
    if (errEl) errEl.textContent = t('err_net');
    if (btn) { btn.disabled = false; btn.textContent = isOnboarding ? t('ob_start') : t('ob_save'); }
  });
}

// ── INIT ONBOARDING CHECK ──
// Diferido a DOMContentLoaded: este <script src> se carga ANTES del
// <div id="onboardOverlay"> del template (script en ~821, overlay en ~824), así
// que corre durante el parseo cuando el overlay aún no existe. Sin el defer,
// openProfileForm(true) hacía getElementById('onboardOverlay')===null y salía
// temprano → el onboarding no se auto-abría nunca (bug de orden pre-existente).
document.addEventListener('DOMContentLoaded', function() {
  _updateAvatars();
  if (PROFILE && PROFILE.onboarded === false) {
    openProfileForm(true);
  }
  _initVoiceMicButton(); // roadmap coach-voz: feature-detect del botón de mic
});
