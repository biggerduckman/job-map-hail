/* Storm overlays for the job map (copy only): hail swaths + wind reports, last 12 months.
   Data built by update_hail.py into storms/index.json, storms/hail.geojson, storms/wind.geojson.
   Nothing is downloaded until a layer is switched on. */
(function(){
  const HAIL_COLORS = ['#FACC15', '#FB923C', '#EF4444', '#B91C1C', '#7E22CE'];
  const HAIL_LABELS = ['0.5–0.99"', '1.0–1.49"', '1.5–1.99"', '2.0–2.49"', '2.5"+'];
  const BASE = 'storms/';
  let map, idx = null, hailData = null, windData = null, hailLayer = null, windLayer = null;
  let hailOn = false, windOn = false, days = 365, minCls = 0, hailRenderer = null;

  const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  function fmtDate(d){ const [y, m, dd] = d.split('-').map(Number);
    return new Date(y, m - 1, dd).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' }); }
  function since(){ const a = new Date(); a.setDate(a.getDate() - days);
    return a.getFullYear() + '-' + String(a.getMonth() + 1).padStart(2, '0') + '-' + String(a.getDate()).padStart(2, '0'); }
  const getJSON = f => fetch(BASE + f, { cache: 'no-cache' }).then(r => { if (!r.ok) throw new Error(f); return r.json(); });
  async function getIndex(){ if (!idx) idx = await getJSON('index.json'); return idx; }
  function windColor(p){ return p.mph == null ? '#64748B' : p.mph >= 70 ? '#DC2626' : p.mph >= 58 ? '#EA580C' : '#0EA5E9'; }

  function setStatus(){
    const el = document.getElementById('storm-status'), bits = [];
    if (hailOn && hailLayer) bits.push(hailLayer._n + ' hail day' + (hailLayer._n === 1 ? '' : 's'));
    if (windOn && windLayer) bits.push(windLayer._n + ' wind report' + (windLayer._n === 1 ? '' : 's'));
    el.textContent = bits.length ? bits.join(' · ') + ' shown.' + (hailOn ? ' Tap the map to see hail dates for that spot.' : '') : '';
  }

  async function drawHail(){
    if (hailLayer){ map.removeLayer(hailLayer); hailLayer = null; }
    if (!hailOn) return setStatus();
    document.getElementById('storm-status').textContent = 'Loading hail data…';
    try {
      if (!hailData) hailData = await getJSON('hail.geojson');
      const a = since();
      const feats = hailData.features.filter(f => f.properties.day >= a && f.properties.cls >= minCls)
        .sort((x, y) => x.properties.cls - y.properties.cls);
      if (!hailOn) return;
      hailLayer = L.geoJSON({ type: 'FeatureCollection', features: feats }, {
        renderer: (hailRenderer = hailRenderer || L.canvas({ padding: 0.3 })), interactive: false,
        style: f => ({ color: HAIL_COLORS[f.properties.cls], weight: 0.6, opacity: 0.7, fillColor: HAIL_COLORS[f.properties.cls], fillOpacity: 0.28 })
      }).addTo(map);
      hailLayer._feats = feats; hailLayer._n = new Set(feats.map(f => f.properties.day)).size;
    } catch (e) { document.getElementById('storm-status').textContent = 'Could not load hail data.'; return; }
    setStatus();
  }

  function windPopup(p){
    const mag = p.mph != null ? p.mph + ' mph' + (p.kts != null ? ' (' + p.kts + ' kt)' : '') + (p.mag_type ? ', ' + p.mag_type : '') : 'Speed not given';
    return '<div class="addr">' + esc(p.type) + '</div>' +
      '<div class="ln"><b>' + esc(fmtDate(p.date)) + '</b> ' + esc(p.time_et) + ' ET</div>' +
      '<div class="ln">Wind: ' + esc(mag) + '</div>' +
      '<div class="ln">' + esc([p.place, p.county ? p.county + ' Co.' : ''].filter(Boolean).join(', ')) + '</div>' +
      (p.damage ? '<div class="ln">Property damage: $' + esc(p.damage) + '</div>' : '') +
      (p.note ? '<div class="storm-note">' + esc(p.note) + '</div>' : '') +
      '<div class="dist">' + esc(p.src) + (p.prelim ? ' (not yet in the official database)' : '') + '</div>';
  }
  async function drawWind(){
    if (windLayer){ map.removeLayer(windLayer); windLayer = null; }
    document.getElementById('storm-zone').hidden = true;
    if (!windOn) return setStatus();
    document.getElementById('storm-status').textContent = 'Loading wind reports…';
    try {
      if (!windData) windData = await getJSON('wind.geojson');
      const a = since(), feats = windData.features.filter(f => f.properties.date >= a);
      if (!windOn) return;
      windLayer = L.geoJSON({ type: 'FeatureCollection', features: feats }, {
        pointToLayer: (f, ll) => L.circleMarker(ll, { radius: 8, color: '#fff', weight: 2, dashArray: f.properties.prelim ? '3 3' : null,
          fillColor: windColor(f.properties), fillOpacity: 0.9 }),
        onEachFeature: (f, l) => l.bindPopup(windPopup(f.properties), { maxWidth: 270 })
      }).addTo(map);
      windLayer._n = feats.length;
      const ix = await getIndex().catch(() => null), z = ix ? ix.wind.zone_high_wind.filter(p => p.date >= a) : [];
      const zel = document.getElementById('storm-zone');
      zel.innerHTML = z.length ? '<b>Zone-wide high wind</b> (no exact spot, not on map):' + z.map(p =>
        '<br>' + esc(fmtDate(p.date)) + ' – ' + esc(p.county) + (p.mph != null ? ', ' + p.mph + ' mph' : '')).join('') : '';
      zel.hidden = !z.length;
    } catch (e) { document.getElementById('storm-status').textContent = 'Could not load wind data.'; return; }
    setStatus();
  }

  // point-in-polygon (ray casting) for "which hail days hit this spot"
  function inRing(x, y, r){ let ins = false; for (let i = 0, j = r.length - 1; i < r.length; j = i++){
      const xi = r[i][0], yi = r[i][1], xj = r[j][0], yj = r[j][1];
      if (((yi > y) !== (yj > y)) && (x < (xj - xi) * (y - yi) / (yj - yi) + xi)) ins = !ins; } return ins; }
  function inPoly(x, y, p){ if (!inRing(x, y, p[0])) return false; for (let k = 1; k < p.length; k++) if (inRing(x, y, p[k])) return false; return true; }
  function onMapClick(e){
    if (!hailOn || !hailLayer) return;
    const x = e.latlng.lng, y = e.latlng.lat;
    const h = hailLayer._feats.filter(f => { const g = f.geometry;
      return g.type === 'Polygon' ? inPoly(x, y, g.coordinates) : g.type === 'MultiPolygon' ? g.coordinates.some(p => inPoly(x, y, p)) : false; })
      .sort((p, q) => q.properties.day.localeCompare(p.properties.day));
    if (!h.length) return;
    const rows = h.slice(0, 12).map(f => { const p = f.properties;
      return '<div class="storm-row"><span class="storm-sw" style="background:' + HAIL_COLORS[p.cls] + '"></span><b>' + esc(fmtDate(p.local_date)) + '</b>' +
        (p.time_et ? ' <span class="storm-t">' + esc(p.time_et) + ' ET</span>' : '') + '<br>Hail here: ' + esc(p.size) + '</div>'; }).join('');
    L.popup({ maxWidth: 260 }).setLatLng(e.latlng).setContent('<div class="addr">Hail at this spot</div>' + rows +
      (h.length > 12 ? '<div class="dist">+' + (h.length - 12) + ' more</div>' : '') +
      '<div class="dist">Radar estimate (NOAA MRMS, ~1 km), not a roof inspection.</div>').openOn(map);
  }

  function buildControl(){
    const C = L.Control.extend({ options: { position: 'topright' }, onAdd: function(){
      const d = L.DomUtil.create('div', 'storm-ctl');
      d.innerHTML =
        '<button type="button" id="storm-btn" aria-expanded="false" aria-controls="storm-panel">Storms <span id="storm-state">OFF</span></button>' +
        '<div id="storm-panel" hidden>' +
          '<label class="storm-chk"><input type="checkbox" id="hail-on"> Hail swaths</label>' +
          '<label class="storm-chk"><input type="checkbox" id="wind-on"> Wind reports</label>' +
          '<label class="storm-lbl">Dates <select id="storm-period"><option value="365">Last 12 months</option><option value="180">Last 6 months</option><option value="90">Last 90 days</option><option value="30">Last 30 days</option></select></label>' +
          '<label class="storm-lbl">Hail size <select id="hail-min"><option value="0">All sizes (0.5"+)</option><option value="1">1" and up</option><option value="2">1.5" and up</option></select></label>' +
          '<div class="storm-h">Hail (largest size that fell there)</div><div class="storm-legend">' + HAIL_LABELS.map((t, i) =>
            '<div><span class="storm-sw" style="background:' + HAIL_COLORS[i] + '"></span>' + t + '</div>').join('') + '</div>' +
          '<div class="storm-h">Wind</div><div class="storm-legend">' +
            [['#DC2626', '70+ mph'], ['#EA580C', '58–69 mph'], ['#0EA5E9', 'under 58 mph'], ['#64748B', 'damage, no speed']].map(c =>
            '<div><span class="storm-sw round" style="background:' + c[0] + '"></span>' + c[1] + '</div>').join('') + '</div>' +
          '<div class="storm-note">Dashed ring = preliminary report.</div>' +
          '<div id="storm-status" class="storm-note"></div>' +
          '<div id="storm-zone" class="storm-note" hidden></div>' +
          '<div class="storm-note" id="storm-src">Free NOAA data: hail from the <a href="https://www.ncei.noaa.gov/products/event-footprint-catalog" target="_blank" rel="noopener">Event Footprint Catalog</a>, wind from the <a href="https://www.ncei.noaa.gov/stormevents/" target="_blank" rel="noopener">Storm Events Database</a> and SPC reports.</div>' +
        '</div>';
      L.DomEvent.disableClickPropagation(d); L.DomEvent.disableScrollPropagation(d);
      return d; } });
    new C().addTo(map);
    const btn = document.getElementById('storm-btn'), panel = document.getElementById('storm-panel');
    btn.addEventListener('click', () => { panel.hidden = !panel.hidden; btn.setAttribute('aria-expanded', String(!panel.hidden)); });
    const sync = () => { const any = hailOn || windOn; document.getElementById('storm-state').textContent = any ? 'ON' : 'OFF'; btn.classList.toggle('on', any); };
    document.getElementById('hail-on').addEventListener('change', e => { hailOn = e.target.checked; sync(); drawHail(); });
    document.getElementById('wind-on').addEventListener('change', e => { windOn = e.target.checked; sync(); drawWind(); });
    document.getElementById('storm-period').addEventListener('change', e => { days = +e.target.value; drawHail(); drawWind(); });
    document.getElementById('hail-min').addEventListener('change', e => { minCls = +e.target.value; drawHail(); });
    getIndex().then(ix => { document.getElementById('storm-src').insertAdjacentHTML('beforeend',
      ' Hail through ' + esc(fmtDate(ix.hail.last_file)) + '; official wind reports through ' + esc(fmtDate(ix.wind.storm_events_through)) +
      ', preliminary after that. Updated ' + esc(ix.built) + '.'); }).catch(() => {});
  }

  window.initHail = function(m){ map = m; buildControl(); map.on('click', onMapClick); };
})();
