// chart-interact.js — C1 (Fase 8C AAA feel): interactividad SIN librería sobre
// el SVG estático que ya pinta buildDetailChart() en el modal de detalle.
//
// Progressive enhancement: si algo aquí falla, el <svg> estático de
// buildDetailChart queda intacto y visible — este módulo SOLO añade una capa
// encima (overlay <div> con listeners de puntero), nunca reemplaza el chart.
//
// Depende de globals ya definidos en el <script> inline del template: t()
// (i18n), fmtDateES() (fecha localizada), clamp(). Se carga DESPUÉS de esos
// globals (ver templates/vitals_ios.html, cierre de </body>).
//
// Geometría: DEBE coincidir con buildDetailChart() (templates/vitals_ios.html,
// ~línea 4388): W=330,H=200,padL=34,padR=10,padT=12,padB=20. Si esa función
// cambia sus paddings, esta debe actualizarse en paralelo (documentado aquí
// a propósito para el próximo dev que la toque).
var _CHART_GEOM = { W: 330, H: 200, padL: 34, padR: 10, padT: 12, padB: 20 };

// Estado del listener activo (para poder hacer cleanup al cerrar el modal).
var _chartInteractState = null;

function _chartX(i, n, geom) {
  var pw = geom.W - geom.padL - geom.padR;
  return geom.padL + (n <= 1 ? pw / 2 : i * pw / (n - 1));
}

function _chartY(v, minVal, maxVal, geom) {
  var ph = geom.H - geom.padT - geom.padB;
  var range = (maxVal - minVal) || 1;
  return geom.padT + (1 - (v - minVal) / range) * ph;
}

// Localiza el índice del punto más cercano en X dado un offset en px dentro
// del viewBox (0..W).
function _nearestIndex(xPx, n, geom) {
  if (n <= 1) return 0;
  var pw = geom.W - geom.padL - geom.padR;
  var frac = (xPx - geom.padL) / pw;
  var idx = Math.round(frac * (n - 1));
  return Math.max(0, Math.min(n - 1, idx));
}

// Convierte un evento de puntero (mouse o touch) a coordenadas del viewBox
// del SVG (0..W, 0..H), independientemente del tamaño real renderizado.
function _pointerToViewBox(evt, svgEl, geom) {
  var rect = svgEl.getBoundingClientRect();
  var clientX = evt.clientX, clientY = evt.clientY;
  if (clientX == null && evt.touches && evt.touches.length) {
    clientX = evt.touches[0].clientX;
    clientY = evt.touches[0].clientY;
  }
  if (clientX == null && evt.changedTouches && evt.changedTouches.length) {
    clientX = evt.changedTouches[0].clientX;
    clientY = evt.changedTouches[0].clientY;
  }
  var relX = (clientX - rect.left) / (rect.width || 1) * geom.W;
  var relY = (clientY - rect.top) / (rect.height || 1) * geom.H;
  return { x: relX, y: relY };
}

function _fmtChartDate(dateStr) {
  try {
    if (typeof fmtDateES === 'function' && dateStr) return fmtDateES(dateStr);
  } catch (e) { /* fallback abajo */ }
  return dateStr || '';
}

function _fmtChartValue(v, unit) {
  if (v == null) return '—';
  var rounded = (Math.round(v * 10) / 10);
  return rounded + (unit || '');
}

// Elimina el listener/overlay previo, si existe. Se llama SIEMPRE antes de
// attach (re-render de otro detail) y desde closeDetail() (cierre de modal).
function detachChartInteraction() {
  if (!_chartInteractState) return;
  var st = _chartInteractState;
  try {
    if (st.container && st.handlers) {
      st.container.removeEventListener('pointermove', st.handlers.move);
      st.container.removeEventListener('pointerdown', st.handlers.down);
      st.container.removeEventListener('pointerup', st.handlers.up);
      st.container.removeEventListener('pointerleave', st.handlers.leave);
      st.container.removeEventListener('touchmove', st.handlers.touchmove);
    }
    if (st.overlayEl && st.overlayEl.parentNode) {
      st.overlayEl.parentNode.removeChild(st.overlayEl);
    }
    if (st.tipEl && st.tipEl.parentNode) {
      st.tipEl.parentNode.removeChild(st.tipEl);
    }
  } catch (e) { /* best-effort cleanup */ }
  _chartInteractState = null;
}

// attachChartInteraction(svgContainer, cfg)
//   svgContainer: el <div id="detailChartSvg"> que YA contiene el <svg> de
//                 buildDetailChart() (innerHTML ya asignado por el caller).
//   cfg: { values: number[], dates: string[] (paralelo a values, mismo largo),
//          color, minVal, maxVal, unit }
//
// Pinta una línea guía vertical + dot resaltado + tooltip HTML flotante que
// sigue al puntero (mouse hover en desktop, drag/tap en touch). No usa
// ninguna librería — solo SVG/DOM nativos.
function attachChartInteraction(svgContainer, cfg) {
  detachChartInteraction(); // un solo overlay activo a la vez

  if (!svgContainer) return;
  var svgEl = svgContainer.querySelector('svg');
  if (!svgEl) return;

  var values = (cfg && cfg.values) || [];
  var dates = (cfg && cfg.dates) || [];
  var n = values.length;
  if (n < 1) return; // nada que interactuar

  var geom = _CHART_GEOM;
  var color = (cfg && cfg.color) || '#30D158';
  var minVal = (cfg && cfg.minVal != null) ? cfg.minVal : Math.min.apply(null, values);
  var maxVal = (cfg && cfg.maxVal != null) ? cfg.maxVal : Math.max.apply(null, values);
  var unit = (cfg && cfg.unit) || '';

  // Namespace SVG para crear los elementos guía dentro del propio <svg>
  // (así heredan el mismo viewBox/escala que la línea, sin cálculos de CSS px).
  var svgNS = 'http://www.w3.org/2000/svg';

  var guideLine = document.createElementNS(svgNS, 'line');
  guideLine.setAttribute('stroke', 'var(--label3, #8E8E93)');
  guideLine.setAttribute('stroke-width', '1');
  guideLine.setAttribute('stroke-dasharray', '3,3');
  guideLine.setAttribute('opacity', '0');
  guideLine.setAttribute('y1', String(geom.padT));
  guideLine.setAttribute('y2', String(geom.H - geom.padB));

  var guideDot = document.createElementNS(svgNS, 'circle');
  guideDot.setAttribute('r', '5');
  guideDot.setAttribute('fill', color);
  guideDot.setAttribute('stroke', 'var(--bg, #07090e)');
  guideDot.setAttribute('stroke-width', '2');
  guideDot.setAttribute('opacity', '0');

  svgEl.appendChild(guideLine);
  svgEl.appendChild(guideDot);

  // Tooltip HTML flotante (fuera del SVG, position:absolute dentro del container).
  var tipEl = document.createElement('div');
  tipEl.className = 'chart-tip';
  tipEl.style.cssText = [
    'position:absolute', 'pointer-events:none', 'opacity:0',
    'transition:opacity .12s ease', 'z-index:5',
    'background:var(--card2, #1c1c1e)', 'border:1px solid var(--card-border, rgba(255,255,255,.12))',
    'border-radius:10px', 'padding:6px 10px', 'font:600 12px -apple-system',
    'color:var(--label, #fff)', 'white-space:nowrap', 'box-shadow:0 4px 14px rgba(0,0,0,.35)',
    'left:0', 'top:0',
  ].join(';');
  if (getComputedStyle(svgContainer).position === 'static') {
    svgContainer.style.position = 'relative';
  }
  svgContainer.appendChild(tipEl);

  function showAt(idx) {
    idx = Math.max(0, Math.min(n - 1, idx));
    var v = values[idx];
    var d = dates[idx];
    var xPx = _chartX(idx, n, geom);
    var yPx = _chartY(clamp(v, minVal, maxVal), minVal, maxVal, geom);

    guideLine.setAttribute('x1', String(xPx));
    guideLine.setAttribute('x2', String(xPx));
    guideLine.setAttribute('opacity', '1');
    guideDot.setAttribute('cx', String(xPx));
    guideDot.setAttribute('cy', String(yPx));
    guideDot.setAttribute('opacity', '1');

    var rect = svgEl.getBoundingClientRect();
    var scaleX = rect.width / geom.W;
    var scaleY = rect.height / geom.H;
    var leftPx = xPx * scaleX;
    var topPx = yPx * scaleY;

    tipEl.innerHTML = '<div>' + _fmtChartValue(v, unit) + '</div>'
      + '<div style="font-weight:500;opacity:.7;font-size:10px">' + _fmtChartDate(d) + '</div>';
    tipEl.style.opacity = '1';

    // Posiciona el tooltip cerca del dot, evitando que se salga del contenedor.
    var containerRect = svgContainer.getBoundingClientRect();
    var tipW = tipEl.offsetWidth || 90;
    var left = leftPx - tipW / 2;
    left = Math.max(4, Math.min((containerRect.width || geom.W) - tipW - 4, left));
    var top = topPx - 44;
    if (top < 0) top = topPx + 14;

    tipEl.style.left = left + 'px';
    tipEl.style.top = top + 'px';
  }

  function hide() {
    guideLine.setAttribute('opacity', '0');
    guideDot.setAttribute('opacity', '0');
    tipEl.style.opacity = '0';
  }

  var dragging = false;

  function onMove(evt) {
    var pt = _pointerToViewBox(evt, svgEl, geom);
    var idx = _nearestIndex(pt.x, n, geom);
    showAt(idx);
  }

  function onDown(evt) {
    dragging = true;
    onMove(evt);
  }

  function onUp() {
    dragging = false;
  }

  function onLeave() {
    if (!dragging) hide();
  }

  // Touch: permitir scrub con drag incluso si el navegador no dispara
  // pointermove de forma continua durante touch (algunos móviles solo
  // disparan touchmove); se maneja ambos sin duplicar lógica.
  function onTouchMove(evt) {
    if (evt.cancelable) evt.preventDefault();
    onMove(evt);
  }

  svgContainer.addEventListener('pointermove', onMove);
  svgContainer.addEventListener('pointerdown', onDown);
  svgContainer.addEventListener('pointerup', onUp);
  svgContainer.addEventListener('pointerleave', onLeave);
  svgContainer.addEventListener('touchmove', onTouchMove, { passive: false });

  // Mostrar el último punto por defecto (mismo punto que ya resalta el dot
  // estático de buildDetailChart) para dar una pista visual de que es interactivo.
  showAt(n - 1);
  setTimeout(hide, 900);

  _chartInteractState = {
    container: svgContainer,
    overlayEl: null,
    tipEl: tipEl,
    handlers: { move: onMove, down: onDown, up: onUp, leave: onLeave, touchmove: onTouchMove },
  };
}
