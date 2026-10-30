// 공개 URI 사이트(src/graph/publish.py가 만든 페이지)의 동작: URI 복사, 목록 거르기, 전체 찾기, 객체 지도
(() => {
  const $ = id => document.getElementById(id);
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

  // 객체 지도: 페이지에 실린 모양(GeoJSON)을 회색 배경지도 위에 그린다
  const el = $("objmap");
  if (el && window.maplibregl) {
    const geom = JSON.parse(el.dataset.geom);
    const feature = { type: "Feature", geometry: geom, properties: {} };
    const map = new maplibregl.Map({ container: el, style: baseStyle(), attributionControl: { compact: true }, interactive: true,
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
    const STATUS = [["사용", "--active"], ["주소 연결됨", "--linked"], ["주소 미연결", "--unlinked"], ["폐지", "--retired"]];
    const KIND = { building: "건물", "building-gis": "건물 (주소 미연결)", entrance: "건물 출입구", station: "역", sensor: "센서" };
    const map = new maplibregl.Map({ container: sm, style: baseStyle(), attributionControl: { compact: true },
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
      const statusColor = ["match", ["get", "s"], ...STATUS.flatMap(([n, v]) => [n, css(v)]), css("--active")];
      if (area) {
        map.addSource("area", { type: "geojson", data: area });
        map.addLayer({ id: "area", type: "line", source: "area", paint: { "line-color": css("--text-2"), "line-width": 1.5, "line-dasharray": [3, 2] } });
        map.fitBounds(area.bbox, { padding: { top: 30, bottom: 30, left: innerWidth > 700 ? 360 : 30, right: 30 }, duration: 0 });
      }
      if (building) {
        map.addSource("building", { type: "geojson", data: building });
        map.addLayer({ id: "building", type: "fill", source: "building", paint: { "fill-color": statusColor, "fill-opacity": 0.55 } });
        map.addLayer({ id: "building-line", type: "line", source: "building", paint: { "line-color": statusColor, "line-width": 0.8 } });
        // 모양이 점뿐인 건물(내비 중심점만 있는 것)은 작은 점으로
        map.addLayer({ id: "building-pt", type: "circle", source: "building", filter: ["==", ["geometry-type"], "Point"],
          paint: { "circle-radius": ["interpolate", ["linear"], ["zoom"], 14, 2, 18, 6], "circle-color": statusColor,
                   "circle-stroke-width": 1, "circle-stroke-color": "#fff" } });
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
      }
      map.addSource("sel", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
      map.addLayer({ id: "sel-line", type: "line", source: "sel", filter: ["!=", ["geometry-type"], "Point"], paint: { "line-color": css("--select"), "line-width": 3 } });
      map.addLayer({ id: "sel-pt", type: "circle", source: "sel", filter: ["==", ["geometry-type"], "Point"],
        paint: { "circle-radius": 10, "circle-color": "transparent", "circle-stroke-width": 3, "circle-stroke-color": css("--select") } });

      // 건물 상태 범례 (개수)
      const cnt = {};
      for (const f of building ? building.features : []) cnt[f.properties.s] = (cnt[f.properties.s] || 0) + 1;
      $("sm-legend").innerHTML = STATUS.filter(([n]) => cnt[n]).map(([n, v]) =>
        `<div><i class="sw" style="background:var(${v})"></i>${n}<span class="n">${cnt[n].toLocaleString()}</span></div>`).join("");

      // 레이어 켜고 끄기
      const ids = { building: ["building", "building-line", "building-pt"], entrance: ["entrance"], station: ["station"], sensor: ["sensor"] };
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
