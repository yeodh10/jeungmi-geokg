// 지도와 지도 층, mapApi (명세 3.4절, 5.2절 층 이름)
// 배경은 common.js의 baseStyle(). 우리 층은 건물, 덮개, 범위, 역, 글자 단계, 고른 장소, 번짐, 번호 표식, 입구, 역 점선이다.
// 다른 묶음은 층을 직접 지우거나 바꾸지 않고 여기의 api를 쓴다.

const EMPTY = { type: "FeatureCollection", features: [] };
const fc = features => ({ type: "FeatureCollection", features });
const pointF = (pt, props = {}) => ({ type: "Feature", geometry: { type: "Point", coordinates: pt }, properties: props });
const reduced = () => matchMedia("(prefers-reduced-motion: reduce)").matches;
export const dur = ms => (reduced() ? 0 : ms);

// 글자 단계별 배율 (3.4절). 고른 장소의 글자는 따로 늘 보인다
const TIERS = [[1, 14, 13], [2, 15.5, 12.5], [3, 16.5, 12]];
const LABEL_LAYERS = ["label-1", "label-2", "label-3"];
const BEFORE = "base-road-label";

// 지도를 만든다. WebGL을 못 쓰면 예외를 던진다 (부르는 곳이 안내를 띄운다)
export function createMap(container, view) {
  if (typeof maplibregl === "undefined") throw new Error("지도 라이브러리를 받지 못했습니다");
  const map = new maplibregl.Map(mapOptions({
    container, style: baseStyle(), center: view.center, zoom: view.zoom,
    attributionControl: false, dragRotate: false, pitchWithRotate: true, touchPitch: false, maxPitch: 72,   // 끌어서 돌리기, 기울이기는 입체 보기에서만 연다 (themes.js). 키 돌리기(Q, E)는 평면에서도 된다 (motion.js)
    fadeDuration: reduced() ? 0 : 200,
  }));
  map.touchZoomRotate.disableRotation();
  map.keyboard.disableRotation();
  map.addControl(new maplibregl.AttributionControl({ compact: true }), "bottom-right");
  map.addControl(new maplibregl.ScaleControl({ maxWidth: 90, unit: "metric" }), "bottom-right");
  return map;
}

export function createMapApi(ctx, map) {
  const { bus } = ctx;
  const themeFns = [];
  let customPaint = null;      // 주제 보기가 바꾼 건물 색
  let markers = [];            // setMarkers로 받은 항목
  let activeNo = null;
  let ready = false;
  const pending = [];          // 층이 준비되기 전에 온 호출
  const whenReady = fn => (ready ? fn() : pending.push(fn));
  let data = null;

  // ---- 색 (토큰에서 읽어 밝기마다 다시 넣는다)
  const ugColor = () => ["match", ["get", "ug"], ...(data ? data.useGroups.flatMap(g => [g.key, css(g.color)]) : ["home", css("--b-home")]), css("--b-other")];
  const halo = () => css("--map-halo");
  function paints() {
    return {
      "bldg-fill": { "fill-color": ugColor(), "fill-opacity": 0.95 },
      "bldg-line": { "line-color": css("--map-bldg-line"), "line-width": ["interpolate", ["linear"], ["zoom"], 14, 0.3, 18, 1] },
      "bldg-pt": { "circle-color": ugColor(), "circle-radius": ["interpolate", ["linear"], ["zoom"], 14, 2.2, 18, 4.5],
        "circle-stroke-color": css("--map-bldg-line"), "circle-stroke-width": 0.8 },
      "spread-fill": { "fill-color": css("--graph"), "fill-opacity": 0.38, "fill-outline-color": css("--graph") },
      "spread-pt": { "circle-color": css("--graph"), "circle-radius": 5, "circle-stroke-color": halo(), "circle-stroke-width": 1.5 },
      "sel-fill": { "fill-color": css("--select"), "fill-opacity": 0.16 },
      "sel-line": { "line-color": css("--select"), "line-width": 3 },
      "mask": { "fill-color": css("--map-mask") },
      "area-line": { "line-color": css("--map-text-2"), "line-width": 1.5, "line-dasharray": [2, 2], "line-opacity": 0.7 },
      "entrance": { "circle-color": css("--panel"), "circle-radius": 4.5, "circle-stroke-color": css("--select"), "circle-stroke-width": 2.5 },
      "bldg-no": { "text-color": css("--map-text-2"), "text-halo-color": halo(), "text-halo-width": 1.5 },
      "label-3": { "text-color": css("--map-text"), "text-halo-color": halo(), "text-halo-width": 1.5 },
      "label-2": { "text-color": css("--map-text"), "text-halo-color": halo(), "text-halo-width": 1.5 },
      "label-1": { "text-color": css("--map-text"), "text-halo-color": halo(), "text-halo-width": 1.5 },
      "sel-pt": { "circle-color": css("--panel"), "circle-radius": 7, "circle-stroke-color": css("--select"), "circle-stroke-width": 3 },
      "station": { "circle-color": css("--station-mark"), "circle-radius": ["interpolate", ["linear"], ["zoom"], 13, 7, 17, 11],
        "circle-stroke-color": halo(), "circle-stroke-width": 2.5 },
      "station-no": { "text-color": css("--on-station") },
      "station-label": { "text-color": css("--map-text"), "text-halo-color": halo(), "text-halo-width": 2 },
      "entrance-label": { "text-color": css("--map-text-2"), "text-halo-color": halo(), "text-halo-width": 1.5 },
      "sel-label": { "text-color": css("--select"), "text-halo-color": halo(), "text-halo-width": 2 },
      "me": { "circle-color": css("--accent"), "circle-radius": 7, "circle-stroke-color": halo(), "circle-stroke-width": 3 },
      "marker": { "circle-color": css("--accent"), "circle-stroke-color": halo(), "circle-stroke-width": 2,
        "circle-radius": ["case", ["boolean", ["get", "on"], false], 15, 11] },
      "marker-text": { "text-color": css("--on-accent") },
    };
  }

  function restyle() {
    restyleBase(map);
    const p = paints();
    for (const [id, props] of Object.entries(p)) {
      if (!map.getLayer(id)) continue;
      for (const [k, v] of Object.entries(props)) {
        if (customPaint && (id === "bldg-fill" || id === "bldg-pt") && k in customPaint) continue;
        map.setPaintProperty(id, k, v);
      }
    }
    for (const fn of themeFns) { try { fn(darkMode()); } catch (e) { console.error(e); } }
  }
  onThemeChange(() => { if (map.style && map.style._loaded) restyle(); });

  // ---- 자료에서 지도 모양을 만든다
  const shapeByI = new Map();
  function featuresFor(paths, { label = null, pt = null } = {}) {
    const out = [];
    for (const path of paths) {
      const p = data.byPath.get(path);
      if (!p) continue;
      if (typeof p.i === "number" && p.sh === 1 && shapeByI.has(p.i)) out.push({ ...shapeByI.get(p.i), properties: { i: p.i, path } });
      else if (p.pt) out.push(pointF(p.pt, { path }));
    }
    if (pt && okLngLat(pt)) out.push(pointF(pt, {}));   // places.json에 없는 장소(입구, 상호)의 점
    if (label && label.pt) out.push(pointF(label.pt, { lbl: label.text }));
    return out;
  }

  function addLayers(d, area) {
    data = d;
    for (const f of d.shapes.features) shapeByI.set(f.properties.i, { type: "Feature", geometry: f.geometry });
    const ptBuildings = fc(d.places.filter(p => p.sh === 0 && p.pt).map(p => pointF(p.pt, { i: p.i, ug: p.ug || "none" })));
    // 글자: 묶음은 묶음 이름 하나, 묶이지 않은 장소는 자기 이름. 단계(t)가 없는 장소는 글자를 두지 않는다
    const labels = [];
    for (const g of d.groups) if (g.t && g.pt) labels.push(pointF(g.pt, { n: g.n, t: g.t, k: "g", g: g.g, path: d.places[g.m[0]].p, s: -g.m.length }));
    const stations = [d.station, ...(d.near_stations || [])].filter(s => s && s.pt);   // 중심 역과 범위 안의 옆 역
    const stationBuildings = new Set(stations.map(s => s.b).filter(Boolean));   // 역사 건물 이름은 역 표식 글자와 겹치므로 뺀다
    for (const p of d.places) if (p.t && p.g == null && p.pt && !stationBuildings.has(p.p)) labels.push(pointF(p.pt, { n: p.n, t: p.t, k: "p", i: p.i, path: p.p, s: 0 }));
    const numbers = fc(d.places.filter(p => p.no && p.pt).map(p => pointF(p.pt, { no: p.no, i: p.i, path: p.p })));

    map.addSource("bldg", { type: "geojson", data: d.shapes, buffer: 64, tolerance: 0.2 });
    map.addSource("bldg-pts", { type: "geojson", data: ptBuildings });
    map.addSource("labels", { type: "geojson", data: fc(labels) });
    map.addSource("bno", { type: "geojson", data: numbers });
    for (const id of ["sel", "spread", "entrances", "markers", "me"]) map.addSource(id, { type: "geojson", data: EMPTY });
    map.addSource("station", { type: "geojson", data: fc(stations.map(s => pointF(s.pt, { path: s.p, n: s.n }))) });
    // 원 밖 덮개: 범위 사각형보다 조금 큰 바깥 고리에 범위 원을 구멍으로 낸다
    const ring = area && area.features && area.features[0] && area.features[0].geometry.coordinates[0];
    map.addSource("area", { type: "geojson", data: area || EMPTY });
    if (ring) {
      const [w, s, e, n] = MAP_BOUNDS, m = 0.05;
      const outer = [[w - m, s - m], [e + m, s - m], [e + m, n + m], [w - m, n + m], [w - m, s - m]];
      const signed = r => r.reduce((a, c, k) => { const q = r[(k + 1) % r.length]; return a + (q[0] - c[0]) * (q[1] + c[1]); }, 0);
      const hole = Math.sign(signed(outer)) === Math.sign(signed(ring)) ? ring.slice().reverse() : ring;
      map.addSource("mask", { type: "geojson", data: { type: "Feature", properties: {}, geometry: { type: "Polygon", coordinates: [outer, hole] } } });
    }

    const P = paints();
    const add = (layer, before) => map.addLayer({ ...layer, paint: P[layer.id] }, before && map.getLayer(before) ? before : undefined);
    const POLY = ["==", ["geometry-type"], "Polygon"], POINT = ["==", ["geometry-type"], "Point"];
    // 배경 도로 이름 아래: 건물, 번짐, 고른 면
    add({ id: "bldg-fill", type: "fill", source: "bldg" }, BEFORE);
    add({ id: "bldg-line", type: "line", source: "bldg" }, BEFORE);
    add({ id: "bldg-pt", type: "circle", source: "bldg-pts", minzoom: 14 }, BEFORE);
    add({ id: "spread-fill", type: "fill", source: "spread", filter: POLY }, BEFORE);
    add({ id: "sel-fill", type: "fill", source: "sel", filter: POLY }, BEFORE);
    add({ id: "sel-line", type: "line", source: "sel", filter: POLY }, BEFORE);
    // 배경 글자 위: 덮개, 범위, 점선, 점, 글자, 표식
    if (map.getSource("mask")) add({ id: "mask", type: "fill", source: "mask" });
    add({ id: "area-line", type: "line", source: "area" });
    add({ id: "spread-pt", type: "circle", source: "spread", filter: POINT });
    add({ id: "entrance", type: "circle", source: "entrances" });
    const sym = (size, more = {}) => ({ "text-font": ["MapMedium"], "text-size": size, "text-max-width": 7, "text-padding": 2, ...more });
    add({ id: "bldg-no", type: "symbol", source: "bno", minzoom: 17.5, layout: sym(11, { "text-field": ["get", "no"] }) });
    for (const [t, minzoom, size] of TIERS.slice().reverse()) {
      add({ id: `label-${t}`, type: "symbol", source: "labels", minzoom, filter: ["==", ["get", "t"], t],
        layout: sym(size, { "text-field": ["get", "n"], "symbol-sort-key": ["get", "s"] }) });
    }
    add({ id: "sel-pt", type: "circle", source: "sel", filter: ["all", POINT, ["!", ["has", "lbl"]]] });
    add({ id: "me", type: "circle", source: "me" });
    add({ id: "station", type: "circle", source: "station" });
    add({ id: "station-no", type: "symbol", source: "station",
      layout: sym(11, { "text-field": "9", "text-allow-overlap": true, "text-ignore-placement": true }) });
    add({ id: "station-label", type: "symbol", source: "station",
      layout: sym(14, { "text-field": ["get", "n"], "text-anchor": "top", "text-offset": [0, 1.1], "text-allow-overlap": true }) });
    add({ id: "entrance-label", type: "symbol", source: "entrances", minzoom: 17,
      layout: sym(11, { "text-field": ["get", "kind"], "text-anchor": "top", "text-offset": [0, 0.7], "text-optional": true }) });
    add({ id: "sel-label", type: "symbol", source: "sel", filter: ["has", "lbl"],
      layout: sym(13, { "text-field": ["get", "lbl"], "text-allow-overlap": true, "text-anchor": "top", "text-offset": [0, 0.9] }) });
    add({ id: "marker", type: "circle", source: "markers" });
    add({ id: "marker-text", type: "symbol", source: "markers",
      layout: sym(12, { "text-field": ["get", "no"], "text-allow-overlap": true, "text-ignore-placement": true, "symbol-sort-key": ["get", "no"] }) });

    bindClicks();
    ready = true;
    pending.splice(0).forEach(fn => fn());
  }

  // ---- 누르기 규칙 (3.4절). 위에서부터 먼저 맞는 것을 쓴다
  const has = ids => ids.filter(id => map.getLayer(id));
  const box = (p, r) => [[p.x - r, p.y - r], [p.x + r, p.y + r]];
  const pick = f => {
    const pr = f.properties || {};
    if (f.layer.id === "marker" || f.layer.id === "marker-text") {
      const m = markers.find(x => String(x.no) === String(pr.no));
      if (!m) return null;
      const g = m.group != null ? data.groupById.get(m.group) : null;
      return { path: m.path || (g ? data.places[g.m[0]].p : null), group: m.group, no: m.no };
    }
    if (pr.k === "g") return { path: pr.path, group: pr.g };
    if (pr.path) return { path: pr.path };
    if (typeof pr.i === "number" && data.byIndex[pr.i]) return { path: data.byIndex[pr.i].p };
    return null;
  };
  const NEAR = ["marker", "marker-text", ...LABEL_LAYERS, "station", "station-label", "bldg-pt", "sel-pt", "entrance", "spread-pt", "bldg-no"];
  function hitTest(point) {
    const q = (ids, where) => map.queryRenderedFeatures(where, { layers: has(ids) });
    const seen = new Set(), cands = [];
    const push = c => { if (c && c.path && !seen.has(c.path + "|" + (c.group ?? ""))) { seen.add(c.path + "|" + (c.group ?? "")); cands.push(c); } };
    // 0. 번호 표식은 언제나 먼저
    q(["marker", "marker-text"], box(point, 4)).forEach(f => push(pick(f)));
    // 1. 배율 16 미만이면 누른 자리의 글자를 먼저 본다
    if (!cands.length && map.getZoom() < 16) q([...LABEL_LAYERS, "station-label", "station"], box(point, 4)).forEach(f => push(pick(f)));
    // 2. 누른 자리의 건물 면
    if (!cands.length) q(["bldg-3d", "bldg-fill"], point).forEach(f => push(pick(f)));   // 입체 보기면 세운 건물을 먼저 본다
    // 3. 12px 안에서 가장 가까운 점이나 글자
    const near = q(NEAR, box(point, 12)).map(f => {
      const c = f.geometry.type === "Point" ? map.project(f.geometry.coordinates) : point;
      return { f, d: Math.hypot(c.x - point.x, c.y - point.y) };
    }).sort((a, b) => a.d - b.d);
    near.forEach(x => push(pick(x.f)));
    return cands;
  }

  function bindClicks() {
    map.on("click", e => {
      if (ctx.sheet && ctx.sheet.active()) ctx.sheet.snap("peek");   // 휴대폰: 지도를 누르면 시트가 접힘으로 내려간다
      const cands = hitTest(e.point);
      // 빈 땅을 누르면 Esc와 같다: 말풍선과 고른 장소를 닫는다 (app.js dismiss). '여기' 말풍선은 우클릭과 길게 누르기로 연다
      if (!cands.length) { if (!document.body.classList.contains("measuring")) bus.emit("map:dismiss", { from: "map" }); return; }
      const [first, ...more] = cands;
      // 단지를 골랐으면 그 단지의 동은 '여기 있는 다른 곳'에 넣지 않는다
      const members = first.group != null && data.groupById.get(first.group) ? new Set(data.groupById.get(first.group).m.map(i => data.byIndex[i].p)) : null;
      const rest = members ? more.filter(c => c.group != null || !members.has(c.path)) : more;
      if (first.no != null) bus.emit("results:active", { no: first.no, from: "map" });
      bus.emit("select", { path: first.path, group: first.group, from: "map", fit: false,
        others: rest.slice(0, 6).map(c => ({ path: c.path, group: c.group })) });
    });
    map.on("contextmenu", e => bus.emit("map:blank", { lngLat: e.lngLat, point: e.point, context: true }));
    // 누를 수 있는 곳에서는 손가락 모양
    let raf = 0;
    map.on("mousemove", e => {
      if (raf) return;
      raf = requestAnimationFrame(() => {
        raf = 0;
        const hit = map.queryRenderedFeatures(box(e.point, 3), { layers: has(["marker", "bldg-3d", "bldg-fill", "bldg-pt", ...LABEL_LAYERS, "station", "station-label"]) }).length;
        map.getCanvas().style.cursor = hit ? "pointer" : "";
        hover(e);
      });
    });
    map.on("mouseout", () => tip.remove());
    map.on("movestart", () => tip.remove());
    let moveTimer = 0;
    map.on("moveend", () => {
      const b = map.getBounds(), c = map.getCenter(), z = map.getZoom();
      bus.emit("map:moved", { bounds: b, center: [c.lng, c.lat], zoom: z });
      clearTimeout(moveTimer);   // 주소창 배율과 위치는 움직임이 멎고 300ms 뒤에 한 번 덮어쓴다
      moveTimer = setTimeout(() => ctx.state.set({ z: z.toFixed(2), c: `${c.lng.toFixed(6)},${c.lat.toFixed(6)}` }, { silent: true }), 300);
    });
  }

  // ---- 마우스를 올린 건물의 말풍선: 누르지 않아도 이름, 종류, 층수를 본다. 마우스가 있는 화면에서만 쓴다
  const tip = new maplibregl.Popup({ closeButton: false, closeOnClick: false, offset: 14, maxWidth: "260px", className: "hover-tip" });
  const canHover = matchMedia("(hover: hover) and (pointer: fine)").matches;
  let tipPath = null;
  function hover(e) {
    if (!canHover || !data) return;
    const f = map.queryRenderedFeatures(box(e.point, 2), { layers: has(["bldg-3d", "bldg-fill", "bldg-pt"]) })[0];
    const p = f && typeof f.properties.i === "number" ? data.byIndex[f.properties.i] : null;
    if (!p) { tip.remove(); tipPath = null; return; }
    if (tipPath !== p.p) {
      tipPath = p.p;
      const use = (data.useGroups || []).find(g => g.key === p.ug);
      const bits = [use ? use.label : null, p.fa ? `지상 ${p.fa}층` : null, { "주소 미연결": "도로명주소 없는 건물", "폐지": "폐지된 건물" }[p.s] || null].filter(Boolean);
      tip.setHTML(`<b>${esc(p.n)}</b>${bits.length ? `<span>${esc(bits.join(", "))}</span>` : ""}`);
    }
    tip.setLngLat(e.lngLat);
    if (!tip.isOpen()) tip.addTo(map);
  }

  // 고른 장소의 글자(sel-label)를 그릴 때 그 장소의 평소 글자를 숨긴다. 묶음은 묶음 글자(k=g), 장소는 자기 글자(k=p)
  function hideOwnLabel(label) {
    let not = null;
    if (label && label.group != null) not = ["!", ["all", ["==", ["get", "k"], "g"], ["==", ["get", "g"], Number(label.group)]]];
    else if (label && label.path) not = ["!", ["all", ["==", ["get", "k"], "p"], ["==", ["get", "path"], String(label.path)]]];
    for (const [t] of TIERS) {
      const id = `label-${t}`;
      if (!map.getLayer(id)) continue;
      const base = ["==", ["get", "t"], t];
      map.setFilter(id, not ? ["all", base, not] : base);
    }
  }

  // ---- 화면에서 쓰는 api (명세 5.2절)
  const setData = (id, value) => whenReady(() => { const s = map.getSource(id); if (s) s.setData(value); });
  const pts = paths => {
    const out = [];
    for (const path of paths) {
      const p = data && data.byPath.get(path);
      if (!p) continue;
      if (typeof p.i === "number" && p.sh === 1 && shapeByI.has(p.i)) {
        const walk = c => (typeof c[0] === "number" ? out.push(c) : c.forEach(walk));
        walk(shapeByI.get(p.i).geometry.coordinates);
      } else if (p.pt) out.push(p.pt);
      else if (Array.isArray(p.m)) p.m.forEach(i => data.byIndex[i] && data.byIndex[i].pt && out.push(data.byIndex[i].pt));
    }
    return out;
  };

  // 바라는 여백. 지도가 움직이는 동안 map.setPadding을 부르면 지도 라이브러리가 그 움직임을 멈춘다(jumpTo가 stop을 부른다).
  // 그래서 장소로 가는 움직임(fitPlaces)은 최종 여백까지 함께 움직이고, 가는 중에 여백이 바뀌면(패널이 펴지거나 시트가 오르면)
  // 같은 목적지로 새 여백을 넣어 이어 간다. 다른 움직임(끌기, 키) 중에 바뀐 여백은 그 움직임이 멎은 뒤 맞춘다
  let wantPad = null;
  let placeMove = null;   // 지금 가는 장소 움직임 {b, one, maxZoom, minZoom, until}
  const SIDES = ["top", "right", "bottom", "left"];
  const samePad = (a, b) => SIDES.every(k => Math.round(a[k] || 0) === Math.round(b[k] || 0));
  let starting = false;   // 새 장소 움직임을 거는 중. 그때 끊긴 앞 움직임의 moveend로 여백을 따로 맞추지 않는다
  map.on("moveend", () => {
    if (starting || map.isMoving()) return;
    if (placeMove && performance.now() >= placeMove.until - 20) placeMove = null;
    if (!wantPad) return;
    if (samePad(map.getPadding(), wantPad)) { wantPad = null; return; }
    map.easeTo({ padding: wantPad, duration: dur(200) });
  });
  // 장소 움직임의 목적지: 여백 pad를 뺀 화면 가운데에 범위 가운데가 오고, 범위가 가장자리 48px 안에 다 들어가는 배율
  function placeCamera(m, pad) {
    const cur = map.getPadding();
    const sw = maplibregl.MercatorCoordinate.fromLngLat(m.b.getSouthWest()), ne = maplibregl.MercatorCoordinate.fromLngLat(m.b.getNorthEast());
    const center = new maplibregl.MercatorCoordinate((sw.x + ne.x) / 2, (sw.y + ne.y) / 2).toLngLat();
    if (m.zoom != null) return { center, zoom: m.zoom };
    if (m.one) return { center, zoom: Math.max(map.getZoom(), m.atLeast) };
    // cameraForBounds는 지금 여백에 주어진 여백을 더해 배율을 잰다. 바랄 여백과의 차이를 더해 준다
    const edge = m.edge ?? 48;
    const extra = {}; for (const k of SIDES) extra[k] = edge + (pad[k] || 0) - (cur[k] || 0);
    const cam = map.cameraForBounds(m.b, { padding: extra, bearing: map.getBearing(), maxZoom: m.maxZoom });
    return { center, zoom: cam && Number.isFinite(cam.zoom) ? cam.zoom : map.getZoom() };
  }
  function goPlace(m, ms) {
    const pad = wantPad || map.getPadding();
    const cam = placeCamera(m, pad);
    m.until = performance.now() + ms;
    placeMove = m;
    // 입체 보기에서 돌려 둔 방위각과 기울기는 그대로 둔다
    starting = true;
    try { map.easeTo({ center: cam.center, zoom: cam.zoom, padding: pad, duration: ms, essential: true }); } finally { starting = false; }
    if (!ms) placeMove = null;
  }

  const api = {
    get ready() { return ready; },
    whenReady,
    setPadding(padding, { animate = true } = {}) {
      const cur = map.getPadding();
      const next = { top: 0, right: 0, bottom: 0, left: 0, ...(wantPad || cur), ...padding };
      wantPad = next;
      if (samePad(cur, next)) return;
      if (map.isMoving()) {
        // 장소로 가는 중이면 같은 목적지로 새 여백을 넣어 남은 시간 동안 이어 간다
        if (placeMove) goPlace(placeMove, Math.max(dur(250), placeMove.until - performance.now()));
        return;   // 다른 움직임은 멎은 뒤 moveend에서 맞춘다
      }
      if (animate) map.easeTo({ padding: next, duration: dur(180) });
      else map.setPadding(next);
    },
    // 장소들이 다 보이게 옮긴다. 점 하나면 그 점으로, 여럿이면 범위로. 바로 미끄러져 간다(멀어졌다 가까워지는 날아가기는 쓰지 않는다)
    fitPlaces(paths, { maxZoom = 17.5, minZoom = 16 } = {}) {
      const c = pts(paths);
      if (!c.length) return false;
      const b = new maplibregl.LngLatBounds(c[0], c[0]);
      c.forEach(x => b.extend(x));
      const one = b.getNorthEast().distanceTo(b.getSouthWest()) < 60;
      goPlace({ b, one, maxZoom, atLeast: minZoom + 1 }, dur(450));
      return true;
    },
    // 한 점으로 옮긴다. zoom을 주면 그 배율로, atLeast를 주면 지금 배율과 그것 가운데 큰 쪽으로. 패널과 시트의 여백까지 한 번에 맞춘다
    goTo(pt, { zoom = null, atLeast = 16, ms = 450 } = {}) {
      if (!pt) return false;
      goPlace({ b: new maplibregl.LngLatBounds(pt, pt), one: true, zoom, atLeast }, dur(ms));
      return true;
    },
    // 범위가 다 보이게 옮긴다 (가장자리 edge px)
    fitBounds(b, { maxZoom = 17.5, edge = 48, ms = 450 } = {}) {
      const bb = maplibregl.LngLatBounds.convert(b);
      const one = bb.getNorthEast().distanceTo(bb.getSouthWest()) < 60;
      goPlace({ b: bb, one, maxZoom, atLeast: 17, edge }, dur(ms));
      return true;
    },
    // style: select(파랑, 고른 장소) 또는 graph(보라, 번짐). label이 {pt, text}면 고른 장소 글자를 늘 보이게 한다.
    // pt는 places.json에 없는 장소(입구, 상호)를 점으로 그릴 때 준다
    // label.path, label.group을 주면 같은 장소의 평소 글자(label-1~3)를 숨겨 이름이 두 번 그려지지 않게 한다
    highlight(paths, { style = "select", label = null, pt = null } = {}) {
      whenReady(() => {
        map.getSource(style === "graph" ? "spread" : "sel").setData(fc(featuresFor(paths, { label, pt })));
        if (style !== "graph") hideOwnLabel(label);
      });
    },
    clearHighlight(style = "select") {
      setData(style === "graph" ? "spread" : "sel", EMPTY);
      if (style !== "graph") whenReady(() => hideOwnLabel(null));
    },
    setMarkers(items) {
      markers = (items || []).filter(x => x && x.pt);
      activeNo = null;
      setData("markers", fc(markers.map(x => pointF(x.pt, { no: x.no, on: false }))));
    },
    clearMarkers() { markers = []; activeNo = null; setData("markers", EMPTY); },
    setActiveMarker(no) {
      activeNo = no;
      setData("markers", fc(markers.map(x => pointF(x.pt, { no: x.no, on: String(x.no) === String(no) }))));
    },
    setEntrances(list) {
      setData("entrances", fc((list || []).filter(x => Array.isArray(x) && okLngLat(x)).map(x => pointF([x[0], x[1]], { kind: x[2] || "", no: x[3] || "" }))));
    },
    setMe(pt) { setData("me", pt ? fc([pointF(pt)]) : EMPTY); },
    onTheme(fn) { themeFns.push(fn); },
    // 주제 보기: {"fill-color": ...} 같은 칠 값을 건물 면과 점에 넣는다. color 키는 둘 다에 넣는다
    setBuildingPaint(paint) {
      whenReady(() => {
        customPaint = { ...paint };
        for (const [k, v] of Object.entries(paint)) {
          if (k === "color") { map.setPaintProperty("bldg-fill", "fill-color", v); map.setPaintProperty("bldg-pt", "circle-color", v); customPaint["fill-color"] = customPaint["circle-color"] = v; }
          else if (k.startsWith("fill-")) map.setPaintProperty("bldg-fill", k, v);
          else if (k.startsWith("circle-")) map.setPaintProperty("bldg-pt", k, v);
          else if (k.startsWith("line-")) map.setPaintProperty("bldg-line", k, v);
        }
      });
    },
    resetBuildingPaint() {
      whenReady(() => {
        customPaint = null;
        const p = paints();
        for (const id of ["bldg-fill", "bldg-pt", "bldg-line"]) for (const [k, v] of Object.entries(p[id])) map.setPaintProperty(id, k, v);
      });
    },
    // 기본 건물 색 식 (주제 보기가 '자료 없음'을 회색으로 칠할 때 견줄 수 있게)
    defaultBuildingColor: () => ugColor(),
    hitTest,
    restyle,
  };

  bus.on("results:show", ({ items } = {}) => api.setMarkers(items));
  bus.on("results:clear", () => api.clearMarkers());
  bus.on("results:active", ({ no } = {}) => api.setActiveMarker(no));
  bus.on("spread", ({ paths } = {}) => api.highlight(paths || [], { style: "graph" }));
  bus.on("spread:clear", () => api.clearHighlight("graph"));

  return { api, addLayers };
}

// WebGL이 없을 때 쓰는 빈 api. 다른 모듈이 map을 확인하지 않고 불러도 오류가 나지 않는다
export function nullMapApi() {
  const noop = () => {};
  return { ready: false, whenReady: noop, setPadding: noop, fitPlaces: () => false, goTo: () => false, fitBounds: () => false, highlight: noop, clearHighlight: noop,
    setMarkers: noop, clearMarkers: noop, setActiveMarker: noop, setEntrances: noop, setMe: noop, onTheme: noop,
    setBuildingPaint: noop, resetBuildingPaint: noop, defaultBuildingColor: () => null, hitTest: () => [], restyle: noop };
}
