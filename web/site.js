// 공개 URI 사이트(src/graph/publish.py가 만든 페이지)의 동작: URI 복사, 목록 거르기, 전체 찾기, 객체 지도
(() => {
  const $ = id => document.getElementById(id);
  // 지도 색 (따뜻한 동네). 고도화 명세 7.1절의 값이고, 화면을 새로 만들 때 공통 토큰으로 옮긴다
  const COLOR = {
    light: { land: "#f6f1e7", home: "#f3ecdd", park: "#c4e6b0", wood: "#b4dca0", water: "#a3d6f0", road: "#ffffff", casing: "#e2d6c2",
             road2: "#fff3c4", road1: "#ffdc7a", road1Line: "#eeb94a", motorway: "#f8ad66", motorwayLine: "#dd8a3c", rail: "#8e99a6",
             text: "#2b2a27", text2: "#5b5e66", waterText: "#2f7fb0", halo: "#ffffff", bline: "#c9b896",
             b: { home: "#f1dfc2", shop: "#f9d0b0", office: "#cfdcf5", edu: "#c9ead0", factory: "#e2d8ec", other: "#ebe3d4" } },
    dark: { land: "#16191e", home: "#191c21", park: "#1c3324", wood: "#1a2f21", water: "#12324a", road: "#2a2f38", casing: "#3a404b",
            road2: "#3a3a2e", road1: "#6b5a2a", road1Line: "#8a7333", motorway: "#7a5230", motorwayLine: "#94633a", rail: "#5a6472",
            text: "#e3e1dc", text2: "#a4acb8", waterText: "#7fc1e8", halo: "#16191e", bline: "#4a4438",
            b: { home: "#3a3226", shop: "#4a3226", office: "#27324a", edu: "#213a2a", factory: "#332b40", other: "#2c2a26" } },
  };
  const palette = () => COLOR[darkMode() ? "dark" : "light"];
  const USES = [
    ["주거", "home", ["공동주택", "단독주택", "주택"]],
    ["상가", "shop", ["제1종근린생활시설", "제2종근린생활시설", "근린생활시설", "판매시설", "숙박시설", "유통시설"]],
    ["업무", "office", ["업무시설"]],
    ["교육, 문화, 의료", "edu", ["교육연구시설", "교육및복지시설", "노유자시설", "종교시설", "운동시설", "의료시설", "문화/관광/레저시설"]],
    ["공장, 창고, 자동차", "factory", ["공장", "창고시설", "공장/창고시설", "자동차관련시설", "위험물저장및처리시설", "여객(화물)운송시설", "운수시설", "발전시설", "환경정화시설", "보안/위험시설"]],
  ];
  const useColor = p => ["match", ["get", "use"], ...USES.flatMap(([, k, names]) => [names, p.b[k]]), p.b.other];
  function colorStyle() {
    const p = palette();
    const name = ["coalesce", ["get", "name:ko"], ["get", "name"]];
    const road = (id, cls, color, w) => ({ id, type: "line", source: "base", "source-layer": "transportation",
      filter: ["in", ["get", "class"], ["literal", cls]], layout: { "line-cap": "round", "line-join": "round" },
      paint: { "line-color": color, "line-width": ["interpolate", ["exponential", 1.5], ["zoom"], 12, w[0], 18, w[1]] } });
    return {
      version: 8, glyphs: "https://tiles.openfreemap.org/fonts/{fontstack}/{range}.pbf",
      sources: { base: { type: "vector", url: "https://tiles.openfreemap.org/planet",
        attribution: '<a href="https://openfreemap.org" target="_blank" rel="noopener">OpenFreeMap</a> © <a href="https://www.openmaptiles.org/" target="_blank" rel="noopener">OpenMapTiles</a> © <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a> 기여자' } },
      layers: [
        { id: "bg", type: "background", paint: { "background-color": p.land } },
        { id: "landuse", type: "fill", source: "base", "source-layer": "landuse", filter: ["in", ["get", "class"], ["literal", ["residential", "suburb", "neighbourhood"]]], paint: { "fill-color": p.home } },
        { id: "landcover", type: "fill", source: "base", "source-layer": "landcover", filter: ["in", ["get", "class"], ["literal", ["grass", "wood"]]], paint: { "fill-color": p.wood, "fill-opacity": 0.8 } },
        { id: "park", type: "fill", source: "base", "source-layer": "park", paint: { "fill-color": p.park } },
        { id: "water", type: "fill", source: "base", "source-layer": "water", paint: { "fill-color": p.water } },
        { id: "waterway", type: "line", source: "base", "source-layer": "waterway", paint: { "line-color": p.water, "line-width": 2 } },
        road("c-minor", ["minor", "service", "track", "path"], p.casing, [0.6, 11]),
        road("c-road2", ["tertiary", "secondary"], p.casing, [2, 19]),
        road("c-road1", ["primary", "trunk"], p.road1Line, [3, 27]),
        road("c-motorway", ["motorway"], p.motorwayLine, [3.5, 31]),
        road("r-minor", ["minor", "service", "track"], p.road, [0.3, 8.5]),
        road("r-road2", ["tertiary", "secondary"], p.road2, [1.3, 16]),
        road("r-road1", ["primary", "trunk"], p.road1, [2, 23]),
        road("r-motorway", ["motorway"], p.motorway, [2.5, 27]),
        { ...road("rail", ["rail", "transit"], p.rail, [0.7, 2.4]), paint: { "line-color": p.rail, "line-width": 1.6, "line-dasharray": [3, 2] } },
        { id: "road-name", type: "symbol", source: "base", "source-layer": "transportation_name", minzoom: 14,
          layout: { "symbol-placement": "line", "text-field": name, "text-font": ["Noto Sans Regular"], "text-size": 12 },
          paint: { "text-color": p.text2, "text-halo-color": p.halo, "text-halo-width": 1.5 } },
        { id: "water-name", type: "symbol", source: "base", "source-layer": "water_name",
          layout: { "text-field": name, "text-font": ["Noto Sans Regular"], "text-size": 14 },
          paint: { "text-color": p.waterText, "text-halo-color": p.halo, "text-halo-width": 1 } },
        { id: "place", type: "symbol", source: "base", "source-layer": "place",
          layout: { "text-field": name, "text-font": ["Noto Sans Regular"], "text-size": 14 },
          paint: { "text-color": p.text, "text-halo-color": p.halo, "text-halo-width": 2 } },
      ],
    };
  }
  const KO_FONT = "'Malgun Gothic','Apple SD Gothic Neo','Noto Sans KR',sans-serif";
  // 글자로 쓸 이름인가. 주소, 번호로 부르는 건물과 '가동', '1동'처럼 그것만으로 알아볼 수 없는 이름은 뺀다
  const nameable = n => n && !/^건물 \(|서울특별시|\d{10,}/.test(n) && !/^([가-하]|[A-Za-z]|\d+)(동|호)?$/.test(n.replace(/\s/g, "")) && n !== "증미역 9호선";
  const centerOf = g => {
    const pts = g.type === "Point" ? [g.coordinates] : g.type === "Polygon" ? g.coordinates[0] : g.coordinates.flatMap(r => r[0]);
    const xs = pts.map(c => c[0]), ys = pts.map(c => c[1]);
    return [(Math.min(...xs) + Math.max(...xs)) / 2, (Math.min(...ys) + Math.max(...ys)) / 2];
  };
  function nameLabels(buildings) {
    const groups = new Map();
    for (const f of buildings.features) {
      const n = f.properties.n;
      if (!nameable(n)) continue;
      const [x, y] = centerOf(f.geometry);
      (groups.get(n) || groups.set(n, []).get(n)).push([x, y]);
    }
    const features = [];
    for (const [n, list] of groups) {
      // 같은 이름이 멀리 떨어져 있으면(약 300m) 다른 곳이므로 따로 쓴다
      const rest = [...list];
      while (rest.length) {
        const seed = rest[0], near = rest.filter(([x, y]) => Math.hypot((x - seed[0]) * 0.79, y - seed[1]) < 0.0027);
        near.forEach(c => rest.splice(rest.indexOf(c), 1));
        const c = [near.reduce((a, q) => a + q[0], 0) / near.length, near.reduce((a, q) => a + q[1], 0) / near.length];
        features.push({ type: "Feature", geometry: { type: "Point", coordinates: c }, properties: { n, c: near.length } });
      }
    }
    return { type: "FeatureCollection", features };
  }

  const root = (document.querySelector('link[href$="/assets/site.css"]').getAttribute("href") || "").replace(/\/assets\/site\.css$/, "");

  // URI 복사
  document.querySelectorAll("button.copy").forEach(b => b.onclick = async () => {
    const old = b.innerHTML;
    try { await navigator.clipboard.writeText(b.dataset.copy); b.textContent = "복사했습니다"; }
    catch (e) { b.textContent = "복사하지 못했습니다"; }
    setTimeout(() => { b.innerHTML = old; }, 1400);
  });

  // 목록 거르기 (공백 무시)
  const tight = s => String(s).toLowerCase().replace(/\s+/g, "");
  const filter = $("filter");
  if (filter) {
    const rows = [...document.querySelectorAll("#listing tbody tr")].map(tr => [tr, tight(tr.textContent)]);
    filter.oninput = () => {
      const q = tight(filter.value);
      let shown = 0;
      for (const [tr, t] of rows) { const on = !q || t.includes(q); tr.hidden = !on; shown += on; }
      $("nohit").hidden = shown > 0;
    };
  }

  // 전체 찾기: 처음 입력할 때 목록(assets/search.json)을 한 번 받는다
  const q = $("site-q"), box = $("site-hits");
  if (q && box) {
    let all = null, sel = -1;
    const load = async () => all || (all = await (await fetch(root + "/assets/search.json")).json());
    const esc = s => String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
    const render = async () => {
      const t = tight(q.value);
      if (t.length < 2) { box.innerHTML = ""; return; }
      const list = await load();
      const hits = [];
      for (const [label, path, kind] of list) {
        const a = tight(label), b = path.toLowerCase();
        const score = a === t ? 3 : a.startsWith(t) ? 2 : a.includes(t) || b.includes(t) ? 1 : 0;
        if (score) hits.push([score, label, path, kind]);
        if (hits.length > 400) break;
      }
      hits.sort((x, y) => y[0] - x[0] || x[1].length - y[1].length);
      sel = -1;
      box.innerHTML = hits.length
        ? hits.slice(0, 12).map(([, label, path, kind]) => `<a href="${root}/id/${esc(path)}/"><span>${esc(label)}</span><span class="k">${esc(kind)}</span></a>`).join("")
        : '<div class="none">맞는 객체가 없습니다.</div>';
    };
    q.oninput = render;
    q.onkeydown = ev => {
      const links = [...box.querySelectorAll("a")];
      if (!links.length) return;
      if (ev.key === "ArrowDown" || ev.key === "ArrowUp") {
        ev.preventDefault();
        sel = (sel + (ev.key === "ArrowDown" ? 1 : -1) + links.length) % links.length;
        links.forEach((a, i) => a.classList.toggle("on", i === sel));
      } else if (ev.key === "Enter") {
        (links[sel] || links[0]).click();
      } else if (ev.key === "Escape") { box.innerHTML = ""; }
    };
    document.addEventListener("click", ev => { if (!ev.target.closest(".find")) box.innerHTML = ""; });
  }

  // 객체 지도: 페이지에 실린 모양(GeoJSON)을 색 있는 배경지도 위에 그린다
  const el = $("objmap");
  if (el && window.maplibregl) {
    const geom = JSON.parse(el.dataset.geom);
    const feature = { type: "Feature", geometry: geom, properties: {} };
    const map = new maplibregl.Map({ container: el, style: colorStyle(), localIdeographFontFamily: KO_FONT, attributionControl: { compact: true }, interactive: true,
      cooperativeGestures: true, center: [126.8612, 37.5577], zoom: 16 });
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    whenStyleReady(map, () => {
      const accent = css("--accent");
      map.addSource("obj", { type: "geojson", data: feature });
      map.addLayer({ id: "obj-fill", type: "fill", source: "obj", filter: ["in", ["geometry-type"], ["literal", ["Polygon", "MultiPolygon"]]], paint: { "fill-color": accent, "fill-opacity": 0.25 } });
      map.addLayer({ id: "obj-line", type: "line", source: "obj", filter: ["!=", ["geometry-type"], "Point"], paint: { "line-color": accent, "line-width": 2.5 } });
      map.addLayer({ id: "obj-pt", type: "circle", source: "obj", filter: ["==", ["geometry-type"], "Point"],
        paint: { "circle-radius": 8, "circle-color": accent, "circle-stroke-width": 3, "circle-stroke-color": "#fff" } });
      const b = boundsOf([feature]);
      if (geom.type === "Point") map.jumpTo({ center: b.getCenter(), zoom: 17 });
      else map.fitBounds(b, { padding: 40, maxZoom: 18, duration: 0 });
    });
  }
  // 전체 지도: assets/map/의 레이어를 그리고, 누른 객체의 요약과 URI 페이지 링크를 보여 준다
  const sm = $("sitemap");
  if (sm && window.maplibregl) {
    const esc = s => String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
    const KIND = { building: "건물", "building-gis": "건물 (주소 미연결)", entrance: "건물 출입구", station: "역", sensor: "센서" };
    const map = new maplibregl.Map({ container: sm, style: colorStyle(), localIdeographFontFamily: KO_FONT, attributionControl: { compact: true },
      center: [126.8612, 37.5577], zoom: 14.6, maxZoom: 19 });
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "bottom-right");
    map.addControl(new maplibregl.ScaleControl({ unit: "metric" }), "bottom-left");
    const get = async name => (await fetch(`${root}/assets/map/${name}.geojson`)).json();
    const data = {};
    const show = f => {
      const p = f.properties, kind = KIND[p.u.split("/")[0]] || "객체";
      const rows = [["상태", p.s], ["용도", p.use], ["도로명주소", p.addr], ["URI", `<code>${esc(p.u)}</code>`]]
        .filter(r => r[1]).map(([k, v]) => `<tr><td class="k">${k}</td><td>${k === "URI" ? v : esc(v)}</td></tr>`).join("");
      $("sm-info").innerHTML = `<span class="chip type">${esc(kind)}</span><h3>${esc(p.n)}</h3><table class="kv">${rows}</table>
        <a class="btn mini" href="${root}/id/${esc(p.u)}/">URI 페이지 열기</a>`;
      map.getSource("sel").setData({ type: "FeatureCollection", features: [f] });
    };
    const focus = f => {
      show(f);
      const b = boundsOf([f]);
      if (f.geometry.type === "Point") map.flyTo({ center: b.getCenter(), zoom: 18 });
      else map.fitBounds(b, { padding: { top: 60, bottom: 60, left: innerWidth > 700 ? 360 : 40, right: 60 }, maxZoom: 18 });
    };
    whenStyleReady(map, async () => {
      const [area, building, entrance, station, sensor] = await Promise.all(["area", "building", "entrance", "station", "sensor"].map(n => get(n).catch(() => null)));
      Object.assign(data, { building, entrance, station, sensor });
      const pal = palette(), buildingColor = useColor(pal);
      if (area) {
        map.addSource("area", { type: "geojson", data: area });
        map.addLayer({ id: "area", type: "line", source: "area", paint: { "line-color": css("--text-2"), "line-width": 1.5, "line-dasharray": [3, 2] } });
        map.fitBounds(area.bbox, { padding: { top: 30, bottom: 30, left: innerWidth > 700 ? 360 : 30, right: 30 }, duration: 0 });
      }
      if (building) {
        map.addSource("building", { type: "geojson", data: building });
        map.addLayer({ id: "building", type: "fill", source: "building", paint: { "fill-color": buildingColor, "fill-opacity": 0.95 } });
        map.addLayer({ id: "building-line", type: "line", source: "building", paint: { "line-color": pal.bline, "line-width": ["interpolate", ["linear"], ["zoom"], 14, 0.3, 18, 1] } });
        // 모양이 점뿐인 건물(내비 중심점만 있는 것)은 작은 점으로
        map.addLayer({ id: "building-pt", type: "circle", source: "building", filter: ["==", ["geometry-type"], "Point"],
          paint: { "circle-radius": ["interpolate", ["linear"], ["zoom"], 14, 2, 18, 6], "circle-color": buildingColor,
                   "circle-stroke-width": 1, "circle-stroke-color": pal.bline } });
        // 글자는 건물마다 쓰지 않는다. 같은 이름(한 단지의 여러 동)은 가운데에 한 번만 쓰고, 많은 동을 가진 단지부터 보여 준다
        map.addSource("label", { type: "geojson", data: nameLabels(building) });
        const nameLayout = { "text-field": ["get", "n"], "text-font": ["Noto Sans Regular"], "text-size": 12, "text-max-width": 8, "symbol-sort-key": ["-", 0, ["get", "c"]] };
        const namePaint = { "text-color": pal.text, "text-halo-color": pal.halo, "text-halo-width": 1.6 };
        map.addLayer({ id: "building-name-big", type: "symbol", source: "label", minzoom: 14.5, filter: [">=", ["get", "c"], 5], layout: nameLayout, paint: namePaint });
        map.addLayer({ id: "building-name", type: "symbol", source: "label", minzoom: 16, filter: ["<", ["get", "c"], 5], layout: nameLayout, paint: namePaint });
      }
      if (entrance) {
        map.addSource("entrance", { type: "geojson", data: entrance });
        map.addLayer({ id: "entrance", type: "circle", source: "entrance", layout: { visibility: "none" },
          paint: { "circle-radius": ["interpolate", ["linear"], ["zoom"], 14, 1.5, 18, 5], "circle-color": css("--accent"),
                   "circle-stroke-width": 1, "circle-stroke-color": "#fff" } });
      }
      if (sensor) {
        map.addSource("sensor", { type: "geojson", data: sensor });
        map.addLayer({ id: "sensor", type: "circle", source: "sensor",
          paint: { "circle-radius": 6.5, "circle-color": css("--sensor"), "circle-stroke-width": 2, "circle-stroke-color": "#fff" } });
      }
      if (station) {
        map.addSource("station", { type: "geojson", data: station });
        map.addLayer({ id: "station", type: "circle", source: "station",
          paint: { "circle-radius": 9, "circle-color": css("--station"), "circle-stroke-width": 3, "circle-stroke-color": "#fff" } });
        map.addLayer({ id: "station-name", type: "symbol", source: "station",
          layout: { "text-field": "증미역", "text-font": ["Noto Sans Regular"], "text-size": 15, "text-offset": [0, 1.4], "text-anchor": "top" },
          paint: { "text-color": pal.text, "text-halo-color": pal.halo, "text-halo-width": 2 } });
      }
      map.addSource("sel", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
      map.addLayer({ id: "sel-line", type: "line", source: "sel", filter: ["!=", ["geometry-type"], "Point"], paint: { "line-color": css("--select"), "line-width": 3 } });
      map.addLayer({ id: "sel-pt", type: "circle", source: "sel", filter: ["==", ["geometry-type"], "Point"],
        paint: { "circle-radius": 10, "circle-color": "transparent", "circle-stroke-width": 3, "circle-stroke-color": css("--select") } });

      // 건물 종류 범례 (개수)
      const groupOf = u => (USES.find(([, , names]) => names.includes(u)) || [, "other"])[1];
      const cnt = {};
      for (const f of building ? building.features : []) { const g = groupOf(f.properties.use); cnt[g] = (cnt[g] || 0) + 1; }
      $("sm-legend").previousElementSibling.textContent = "건물 종류";
      const swatch = document.querySelector('input[data-layer="building"] ~ .sw');
      if (swatch) swatch.style.background = pal.b.home;
      $("sm-legend").innerHTML = [...USES, ["그 외, 자료 없음", "other"]].filter(([, k]) => cnt[k]).map(([n, k]) =>
        `<div><i class="sw" style="background:${pal.b[k]}"></i>${n}<span class="n">${cnt[k].toLocaleString()}</span></div>`).join("");

      // 레이어 켜고 끄기
      const ids = { building: ["building", "building-line", "building-pt", "building-name", "building-name-big"], entrance: ["entrance"], station: ["station", "station-name"], sensor: ["sensor"] };
      document.querySelectorAll("input[data-layer]").forEach(cb => {
        const apply = () => (ids[cb.dataset.layer] || []).forEach(id => map.getLayer(id) && map.setLayoutProperty(id, "visibility", cb.checked ? "visible" : "none"));
        cb.onchange = apply; apply();
      });

      // 누르기와 가리키기 (점이 위에 있으면 점 먼저)
      const clickable = ["station", "sensor", "entrance", "building-pt", "building"];
      map.on("click", ev => {
        const hit = map.queryRenderedFeatures(ev.point, { layers: clickable.filter(id => map.getLayer(id)) })[0];
        if (!hit) return;
        const src = data[hit.layer.id.split("-")[0]].features.find(f => f.properties.u === hit.properties.u);
        if (src) show(src);
      });
      map.on("mousemove", ev => {
        const n = map.queryRenderedFeatures(ev.point, { layers: clickable.filter(id => map.getLayer(id)) }).length;
        map.getCanvas().style.cursor = n ? "pointer" : "";
      });

      // 지도에서 찾기
      const q = $("sm-q"), hits = $("sm-hits");
      const all = ["station", "sensor", "building", "entrance"].flatMap(k => (data[k] ? data[k].features : []));
      q.oninput = () => {
        const t = tight(q.value);
        if (t.length < 2) { hits.innerHTML = ""; return; }
        const found = all.filter(f => tight(f.properties.n + " " + f.properties.addr).includes(t)).slice(0, 10);
        hits.innerHTML = found.length
          ? found.map((f, i) => `<button type="button" data-i="${i}"><span>${esc(f.properties.n)}</span><span class="k">${esc(KIND[f.properties.u.split("/")[0]] || "")}</span></button>`).join("")
          : '<div class="none">맞는 객체가 없습니다.</div>';
        hits.querySelectorAll("button").forEach(b => b.onclick = () => { focus(found[+b.dataset.i]); hits.innerHTML = ""; });
      };
      const want = new URLSearchParams(location.search).get("u");
      const start = want && all.find(f => f.properties.u === want);
      if (start) focus(start);
    });
  }
})();
