// 로컬 서버만의 지도 층과 로컬 표시 (명세 10절 WP6). 공개 빌드에는 이 폴더를 복사하지 않는다.
// 층: 공공시설, 도로 구간, 법정동 경계, 국가기본도 건물, 수치지형도 건물. 모두 공개하지 않는 자료라 로컬 화면에서만 그린다.
// 지도 왼쪽 아래의 '로컬 서버' 단추가 층 목록과 범례를 연다. 층의 점이나 선을 누르면 그 객체의 카드를 연다.
// 공공시설은 places의 facilities로 와서 byPath에 넣는다 (주소창 focus로도 열린다). 나머지는 처음 켤 때 /api/layers/{이름}을 받는다.

const LAYERS = [
  { key: "facility", label: "공공시설", color: "--facility", kind: "point",
    note: "민원행정기관 전자지도의 공공시설입니다. 누르면 카드가 열립니다." },
  { key: "road", label: "도로 구간", color: "--road", kind: "line",
    note: "도로명주소 전자지도의 도로 구간입니다. 출입구는 같은 길 이름의 가장 가까운 구간에 이어져 있습니다." },
  { key: "legaldong", label: "법정동 경계", color: "--legaldong", kind: "dash",
    note: "도로명주소 전자지도의 법정동 경계입니다. 공개 사이트는 소속 관계만 싣고 경계 모양은 싣지 않습니다." },
  { key: "basemap", label: "국가기본도 건물", color: "--basemap", kind: "area",
    note: "국토지리정보원 국가기본도의 건물 외곽입니다. 우리 건물과 모양이 겹치면 서로 이어져 있습니다." },
  { key: "digitalmap", label: "수치지형도 건물", color: "--digitalmap", kind: "areadash",
    note: "국토지리정보원 수치지형도 Ver2.0의 건물 외곽입니다." },
];
const CSS = `
.local-badge { position: fixed; z-index: 16; left: 416px; bottom: 12px; display: inline-flex; align-items: center; gap: 8px; min-height: 44px;
  padding: 0 14px; border-radius: 999px; border: 1px solid var(--warn); background: var(--warn-soft); color: var(--warn); font-weight: 600;
  font-size: 13px; box-shadow: var(--shadow-2); }
body.panel-collapsed .local-badge { left: 12px; }
.local-badge .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--warn); }
.local-pop { position: fixed; z-index: 40; left: 416px; bottom: 64px; width: min(340px, calc(100vw - 24px)); max-height: calc(100dvh - 140px);
  overflow: auto; padding: 14px 16px; border-radius: var(--radius-lg); border: 1px solid var(--line); background: var(--panel); color: var(--text);
  box-shadow: var(--shadow-3); }
body.panel-collapsed .local-pop { left: 12px; }
.local-pop h2 { font-size: 15px; margin: 0 0 4px; }
.local-pop .lp-warn { font-size: 12px; color: var(--warn); margin: 0 0 10px; }
.local-pop ul { list-style: none; margin: 0; padding: 0; display: grid; gap: 4px; }
.local-pop .lp-row { display: flex; align-items: center; gap: 10px; min-height: 44px; cursor: pointer; }
.local-pop .lp-row input { width: 18px; height: 18px; margin: 0; accent-color: var(--accent); }
.local-pop .lp-n { margin-left: auto; color: var(--muted); font-size: 12px; font-variant-numeric: tabular-nums; }
.local-pop .lp-note { margin: -2px 0 6px 28px; font-size: 12px; color: var(--text-2); }
.local-pop .sw { flex: none; width: 18px; height: 12px; border-radius: 3px; }
.local-pop .sw.point { width: 12px; height: 12px; border-radius: 50%; margin: 0 3px; }
.local-pop .sw.line { height: 4px; border-radius: 2px; }
.local-pop .sw.dash { height: 0; border-top: 3px dashed; border-radius: 0; }
.local-pop .sw.area { background: transparent !important; border: 2px solid; }
.local-pop .sw.areadash { background: transparent !important; border: 2px dashed; }
.local-pop .lp-fac { margin: 0 0 8px 28px; max-height: 220px; overflow: auto; border-left: 2px solid var(--line); padding-left: 8px; }
.local-pop .lp-fac button { min-height: 36px; }
.local-pop .lp-close { position: absolute; top: 6px; right: 6px; width: 44px; height: 44px; border: 0; background: none; color: var(--text-2); }
@media (max-width: 900px) {
  .local-badge, body.panel-collapsed .local-badge { left: 8px; bottom: calc(var(--sheet-h) + 36px); min-height: 40px; transition: bottom .18s; }
  body.sheet-full .local-badge { visibility: hidden; }
  .local-pop, body.panel-collapsed .local-pop { left: 8px; right: 8px; width: auto; bottom: calc(var(--sheet-h) + 84px); max-height: calc(100dvh - var(--sheet-h) - 200px); }
}
`;

export function init(ctx) {
  const { bus, ui, data, source } = ctx;
  if (ctx.cfg.mode !== "local") return;
  const style = document.createElement("style");
  style.textContent = CSS;
  document.head.appendChild(style);
  document.body.classList.add("local-mode");

  // 공공시설을 장소 색인에 넣는다 (주소창 focus, 목록, 지도 누르기가 같은 경로로 연다)
  const facilities = (data.facilities || []).filter(f => f && f.p);
  for (const f of facilities) if (!data.byPath.has(f.p)) data.byPath.set(f.p, { ...f });

  const map = ctx.map;
  const on = new Set();
  const counts = { facility: facilities.length };
  const SRC = k => "local-" + k;
  const fc = features => ({ type: "FeatureCollection", features });
  const asPath = uri => String(uri || "").replace(/^.*\/id\//, "");

  function paint(k) {
    const c = css(LAYERS.find(x => x.key === k).color);
    if (k === "facility") return { "circle-color": c, "circle-radius": ["interpolate", ["linear"], ["zoom"], 14, 5, 18, 8], "circle-stroke-color": css("--map-halo"), "circle-stroke-width": 2 };
    if (k === "road") return { "line-color": c, "line-width": ["interpolate", ["linear"], ["zoom"], 14, 1, 18, 4], "line-opacity": 0.75 };
    if (k === "legaldong") return { "line-color": c, "line-width": 2, "line-dasharray": [4, 2] };
    if (k === "basemap") return { "line-color": c, "line-width": 1.6 };
    return { "line-color": c, "line-width": 1.2, "line-dasharray": [2, 1] };
  }
  const layerId = k => "local-" + k;
  const before = () => ["entrance", "station-link", "spread-pt"].find(id => map.getLayer(id));

  async function addLayer(k) {
    if (!map || map.getLayer(layerId(k))) return;
    let gj;
    if (k === "facility") gj = fc(facilities.filter(f => f.pt).map(f => ({ type: "Feature", geometry: { type: "Point", coordinates: f.pt }, properties: { path: f.p, n: f.n } })));
    else {
      const got = await source.layer(k);
      gj = fc((got.features || []).map(f => ({ ...f, properties: { path: asPath(f.properties && f.properties.uri), n: (f.properties && f.properties.label) || "" } })));
      counts[k] = gj.features.length;
    }
    if (!on.has(k) || map.getLayer(layerId(k))) return;
    if (!map.getSource(SRC(k))) map.addSource(SRC(k), { type: "geojson", data: gj });
    const type = k === "facility" ? "circle" : "line";
    map.addLayer({ id: layerId(k), type, source: SRC(k), paint: paint(k) }, type === "circle" ? undefined : before());
  }

  async function toggle(k, want) {
    if (want) on.add(k); else on.delete(k);
    if (!map) return;
    if (!want) {
      if (map.getLayer(layerId(k))) map.setLayoutProperty(layerId(k), "visibility", "none");
      return;
    }
    if (map.getLayer(layerId(k))) { map.setLayoutProperty(layerId(k), "visibility", "visible"); return; }
    try {
      await new Promise(r => ctx.mapApi.whenReady(r));
      await addLayer(k);
      render();
      ui.live(`${LAYERS.find(x => x.key === k).label} 층을 켰습니다`);
    } catch (e) {
      on.delete(k);
      ui.toast(`${LAYERS.find(x => x.key === k).label} 층을 받지 못했습니다. 잠시 뒤에 다시 켜 보세요.`);
      render();
    }
  }
  if (map) ctx.mapApi.onTheme(() => { for (const k of on) if (map.getLayer(layerId(k))) for (const [p, v] of Object.entries(paint(k))) map.setPaintProperty(layerId(k), p, v); });

  // ---- 로컬 표시 단추와 층 목록
  const badge = document.createElement("button");
  badge.type = "button";
  badge.className = "local-badge";
  badge.setAttribute("aria-expanded", "false");
  badge.setAttribute("aria-controls", "local-pop");
  badge.innerHTML = `<span class="dot" aria-hidden="true"></span>로컬 서버, 보류 자료 층`;
  const pop = document.createElement("div");
  pop.className = "local-pop";
  pop.id = "local-pop";
  pop.setAttribute("role", "dialog");
  pop.setAttribute("aria-label", "로컬 서버에서만 보이는 층");
  pop.hidden = true;
  document.body.appendChild(badge);
  document.body.appendChild(pop);

  function render() {
    const nf = n => Number(n).toLocaleString("ko-KR");
    pop.innerHTML = `<button type="button" class="lp-close" aria-label="층 목록 닫기">${ui.icon("close", 18)}</button>
      <h2>로컬 서버에서만 보이는 층</h2>
      <p class="lp-warn">공개 사이트에 싣지 않는 자료입니다. 이 화면을 찍어 바깥에 올리지 않습니다.</p>
      <ul>${LAYERS.map(L => `<li><label class="lp-row"><input type="checkbox" data-k="${L.key}"${on.has(L.key) ? " checked" : ""}>
        <span class="sw ${L.kind}" style="background:var(${L.color});border-color:var(${L.color});color:var(${L.color})"></span>${esc(L.label)}
        <span class="lp-n">${counts[L.key] != null ? nf(counts[L.key]) + (L.kind === "point" ? "곳" : "개") : ""}</span></label>
        ${on.has(L.key) ? `<p class="lp-note">${esc(L.note)}</p>` : ""}
        ${L.key === "facility" && on.has("facility") ? `<ul class="lp-fac">${facilities.map(f =>
          `<li><button type="button" class="linkish" data-path="${esc(f.p)}">${esc(f.n || f.p)}</button></li>`).join("")}</ul>` : ""}</li>`).join("")}</ul>`;
    pop.querySelector(".lp-close").addEventListener("click", () => openPop(false));
    pop.querySelectorAll("input[data-k]").forEach(cb => cb.addEventListener("change", () => toggle(cb.dataset.k, cb.checked).then(render)));
    pop.querySelectorAll(".lp-fac button").forEach(b => b.addEventListener("click", () =>
      bus.emit("select", { path: b.dataset.path, from: "list", fit: true })));
  }
  function openPop(open) {
    pop.hidden = !open;
    badge.setAttribute("aria-expanded", String(open));
    if (open) { render(); (pop.querySelector("input") || pop).focus(); }
  }
  badge.addEventListener("click", () => openPop(pop.hidden));
  pop.addEventListener("keydown", e => { if (e.key === "Escape") { e.preventDefault(); openPop(false); badge.focus(); } });
  document.addEventListener("pointerdown", e => { if (!pop.hidden && !pop.contains(e.target) && !badge.contains(e.target)) openPop(false); });

  // ---- 층의 점이나 선 누르기: 뼈대의 누르기보다 먼저 받아 그 객체 카드를 연다 (붙잡는 단계에서 받는다)
  if (map) {
    map.getContainer().addEventListener("click", async e => {
      const ids = [...on].map(layerId).filter(id => map.getLayer(id));
      if (!ids.length) return;
      const r = map.getCanvas().getBoundingClientRect();
      const x = e.clientX - r.left, y = e.clientY - r.top;
      if (x < 0 || y < 0 || x > r.width || y > r.height) return;
      const hit = map.queryRenderedFeatures([[x - 6, y - 6], [x + 6, y + 6]], { layers: ids });
      const f = hit.find(h => h.layer.id === layerId("facility")) || hit[0];
      const path = f && f.properties && f.properties.path;
      if (!path) return;
      e.stopPropagation();
      if (ctx.sheet && ctx.sheet.active()) ctx.sheet.snap("peek");
      if (data.byPath.has(path)) { bus.emit("select", { path, from: "map", fit: false }); return; }
      try {
        const card = await source.card(path);
        bus.emit("select", { path, card, from: "map", fit: false });
      } catch (err) { ui.toast("이 객체의 카드를 받지 못했습니다"); }
    }, true);
  }

  // 주소창이나 검토 화면 링크로 보류 자료 객체가 열리면 그 층도 켠다 (어디 있는지, 모양이 어떤지 보이게)
  const LAYER_OF = { "public-facility": "facility", "road-segment": "road", "basemap": "basemap", "digital-map": "digitalmap" };
  bus.on("select", sel => {
    const k = LAYER_OF[String((sel && sel.path) || "").split("/")[0]];
    if (k && !on.has(k)) toggle(k, true).then(render);
  });
}
