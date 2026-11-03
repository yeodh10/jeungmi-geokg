// 재기 (명세 4.2절 있으면 좋음 2): 거리와 반경. 반경 안 건물 수와 용도별 수를 보여 준다.
// 지도를 눌러 점을 찍는다. 백스페이스는 한 점 취소, Esc나 오른쪽 단추는 끝내기, '지우기'는 처음부터. 시간은 내지 않는다(근거가 없다).
// 재는 동안 지도 누르기는 장소 고르기 대신 점 찍기가 된다 (지도 칸의 click을 먼저 받아 멈춘다). 층 이름은 measure-*다.

const nf = n => Number(n).toLocaleString("ko-KR");
const EMPTY = { type: "FeatureCollection", features: [] };
const fmt = m => (m >= 1000 ? `${(m / 1000).toFixed(2)}km` : `${nf(Math.round(m))}m`);
function dist(a, b) {
  const k = Math.cos(((a[1] + b[1]) / 2) * Math.PI / 180);
  return Math.hypot((a[0] - b[0]) * 111320 * k, (a[1] - b[1]) * 110540);
}
// 반경 원 (64각형)
function circle(c, r) {
  const k = Math.cos(c[1] * Math.PI / 180), ring = [];
  for (let j = 0; j <= 64; j++) {
    const t = (j / 64) * 2 * Math.PI;
    ring.push([c[0] + (r * Math.cos(t)) / (111320 * k), c[1] + (r * Math.sin(t)) / 110540]);
  }
  return ring;
}

export async function init(ctx) {
  const { bus, ui, data } = ctx;
  if (!ctx.map) return;
  ctx.css("tools.css");
  const map = ctx.map;
  let mode = null;        // distance, radius
  let pts = [];
  let done = false;
  let radius = 0;

  // ---- 층
  function ensureLayers() {
    if (map.getSource("measure")) return;
    map.addSource("measure", { type: "geojson", data: EMPTY });
    const before = map.getLayer("marker") ? "marker" : undefined;
    map.addLayer({ id: "measure-fill", type: "fill", source: "measure", filter: ["==", ["geometry-type"], "Polygon"],
      paint: { "fill-color": css("--accent"), "fill-opacity": 0.08 } }, before);
    map.addLayer({ id: "measure-line", type: "line", source: "measure", filter: ["!=", ["geometry-type"], "Point"],
      paint: { "line-color": css("--accent"), "line-width": 2.5, "line-dasharray": [2, 1.5] } }, before);
    map.addLayer({ id: "measure-pt", type: "circle", source: "measure", filter: ["==", ["geometry-type"], "Point"],
      paint: { "circle-radius": 5, "circle-color": css("--panel"), "circle-stroke-color": css("--accent"), "circle-stroke-width": 2.5 } }, before);
    map.addLayer({ id: "measure-label", type: "symbol", source: "measure", filter: ["all", ["==", ["geometry-type"], "Point"], ["has", "t"]],
      layout: { "text-field": ["get", "t"], "text-font": ["MapMedium"], "text-size": 12, "text-anchor": "bottom", "text-offset": [0, -0.8], "text-allow-overlap": true },
      paint: { "text-color": css("--text"), "text-halo-color": css("--map-halo"), "text-halo-width": 2 } }, before);
  }
  ctx.mapApi.onTheme(() => {
    if (!map.getLayer("measure-line")) return;
    map.setPaintProperty("measure-fill", "fill-color", css("--accent"));
    map.setPaintProperty("measure-line", "line-color", css("--accent"));
    map.setPaintProperty("measure-pt", "circle-color", css("--panel"));
    map.setPaintProperty("measure-pt", "circle-stroke-color", css("--accent"));
    map.setPaintProperty("measure-label", "text-color", css("--text"));
    map.setPaintProperty("measure-label", "text-halo-color", css("--map-halo"));
  });

  const total = () => pts.reduce((a, p, k) => (k ? a + dist(pts[k - 1], p) : 0), 0);
  function draw(hover) {
    ensureLayers();
    const f = [];
    const pt = (c, t) => ({ type: "Feature", geometry: { type: "Point", coordinates: c }, properties: t ? { t } : {} });
    if (mode === "distance") {
      const line = hover && !done && pts.length ? [...pts, hover] : pts;
      if (line.length > 1) f.push({ type: "Feature", geometry: { type: "LineString", coordinates: line }, properties: {} });
      let acc = 0;
      pts.forEach((c, k) => { if (k) acc += dist(pts[k - 1], c); f.push(pt(c, k ? fmt(acc) : "출발")); });
    } else if (mode === "radius" && pts[0]) {
      const r = done ? radius : hover ? dist(pts[0], hover) : radius;
      if (r > 0) {
        f.push({ type: "Feature", geometry: { type: "Polygon", coordinates: [circle(pts[0], r)] }, properties: {} });
        f.push({ type: "Feature", geometry: { type: "LineString", coordinates: [pts[0], circle(pts[0], r)[0]] }, properties: {} });
        f.push(pt(circle(pts[0], r)[0], `반경 ${fmt(r)}`));
      }
      f.push(pt(pts[0]));
    }
    map.getSource("measure").setData({ type: "FeatureCollection", features: f });
  }

  // ---- 안내 상자
  const box = document.createElement("div");
  box.className = "measure-box";
  box.hidden = true;
  box.setAttribute("role", "region");
  box.setAttribute("aria-label", "재기");
  document.body.appendChild(box);

  function inside() {
    if (mode !== "radius" || !pts[0] || !(radius > 0)) return null;
    const hit = data.places.filter(p => p.pt && dist(pts[0], p.pt) <= radius);
    const by = new Map();
    for (const p of hit) by.set(p.ug || "none", (by.get(p.ug || "none") || 0) + 1);
    return { n: hit.length, by };
  }

  function render() {
    const guide = mode === "distance"
      ? (done ? "다 쟀습니다." : pts.length ? "다음 점을 누르세요. 백스페이스는 한 점 취소, Esc나 오른쪽 단추는 끝내기입니다." : "지도에서 출발점을 누르세요.")
      : (done ? "다 쟀습니다." : pts.length ? "반경 끝점을 누르세요. 아래 단추로 반경을 바로 고를 수도 있습니다." : "지도에서 가운데 점을 누르세요.");
    let body = "";
    if (mode === "distance") body = `<p class="measure-val">직선 거리 합계 <b>${fmt(total())}</b>${pts.length > 2 ? ` <span class="muted">(점 ${pts.length}개)</span>` : ""}</p>`;
    else {
      const r = inside();
      body = `<div class="measure-presets" role="group" aria-label="반경 고르기">${[100, 300, 500].map(m =>
        `<button type="button" class="chip-btn" data-r="${m}" aria-pressed="${Math.round(radius) === m}"${pts[0] ? "" : " disabled"}>${m}m</button>`).join("")}</div>`;
      if (r) {
        const rows = (data.useGroups || []).filter(g => r.by.get(g.key)).map(g => `<li>${esc(g.label)} ${nf(r.by.get(g.key))}동</li>`).join("");
        body += `<p class="measure-val">반경 <b>${fmt(radius)}</b> 안 건물 <b>${nf(r.n)}동</b></p>${rows ? `<ul class="measure-by">${rows}</ul>` : ""}
          ${r.n ? "" : `<p class="small">맞는 건물이 없습니다. 반경을 넓혀 보세요.</p>`}`;
      }
    }
    box.innerHTML = `<div class="pop-head"><h2 class="pop-title" tabindex="-1">${mode === "distance" ? "거리 재기" : "반경 재기"}</h2>
      <button type="button" class="sb-btn measure-close" aria-label="재기 닫기">${ui.icon("close", 18)}</button></div>
      <p class="small muted measure-guide" aria-live="polite">${guide}</p>${body}
      <p class="small muted">직선 거리입니다. 걷는 길이나 걸리는 시간이 아닙니다.</p>
      <div class="measure-acts">
        ${!done && pts.length ? `<button type="button" class="btn ghost mini" data-a="undo">한 점 취소</button><button type="button" class="btn ghost mini" data-a="end">끝내기</button>` : ""}
        <button type="button" class="btn ghost mini" data-a="clear"${pts.length ? "" : " disabled"}>지우기</button>
        <button type="button" class="btn ghost mini" data-a="switch">${mode === "distance" ? "반경 재기로" : "거리 재기로"}</button>
      </div>`;
    box.querySelector(".measure-close").onclick = () => stop();
    box.querySelectorAll("[data-a]").forEach(b => b.onclick = () => {
      const a = b.dataset.a;
      if (a === "undo") undo();
      else if (a === "end") finish();
      else if (a === "clear") clear();
      else if (a === "switch") start(mode === "distance" ? "radius" : "distance");
    });
    box.querySelectorAll("[data-r]").forEach(b => b.onclick = () => { radius = Number(b.dataset.r); done = true; arm(false); draw(); render(); announce(); });
  }
  function announce() {
    if (mode === "distance") ui.live(`직선 거리 합계 ${fmt(total())}`);
    else { const r = inside(); if (r) ui.live(`반경 ${fmt(radius)} 안 건물 ${nf(r.n)}동`); }
  }

  // ---- 지도 누르기 가로채기 (재는 동안만)
  const canvasBox = map.getCanvasContainer();
  let down = null;
  const lngLatOf = e => { const r = canvasBox.getBoundingClientRect(); return map.unproject([e.clientX - r.left, e.clientY - r.top]).toArray(); };
  function onDown(e) { if (canvasBox.contains(e.target)) down = [e.clientX, e.clientY]; }
  function onClick(e) {
    if (!canvasBox.contains(e.target)) return;
    e.stopPropagation();
    e.preventDefault();
    if (down && Math.hypot(e.clientX - down[0], e.clientY - down[1]) > 5) return;   // 끌기는 점이 아니다
    addPoint(lngLatOf(e));
  }
  function onContext(e) {
    if (!canvasBox.contains(e.target)) return;
    e.stopPropagation();
    e.preventDefault();
    finish();
  }
  let raf = 0;
  function onMove(e) {
    if (done || !pts.length || !canvasBox.contains(e.target)) return;
    if (raf) return;
    raf = requestAnimationFrame(() => { raf = 0; draw(lngLatOf(e)); });
  }
  function onKey(e) {
    if (/^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName) || e.target.isContentEditable) return;
    if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); if (!done && pts.length) finish(); else stop(); }
    else if (e.key === "Backspace" && !done && pts.length) { e.preventDefault(); undo(); }
  }
  let armed = false;
  function arm(on) {
    if (on === armed) return;
    armed = on;
    const f = on ? "addEventListener" : "removeEventListener";
    document[f]("pointerdown", onDown, true);
    document[f]("click", onClick, true);
    document[f]("contextmenu", onContext, true);
    document[f]("mousemove", onMove, true);
    document.body.classList.toggle("measuring", on);
    if (on) map.doubleClickZoom.disable(); else map.doubleClickZoom.enable();
  }
  document.addEventListener("keydown", e => { if (mode) onKey(e); }, true);

  function addPoint(c) {
    if (done) { pts = []; done = false; radius = 0; }
    if (mode === "radius" && pts.length === 1) { radius = dist(pts[0], c); done = true; arm(false); }
    else pts.push(c);
    draw();
    render();
    announce();
  }
  function undo() {
    if (done && mode === "radius") { done = false; radius = 0; arm(true); }
    else pts.pop();
    draw();
    render();
  }
  function finish() {
    if (mode === "radius" && !(radius > 0)) { stop(); return; }
    done = true;
    arm(false);
    draw();
    render();
    announce();
    box.querySelector(".pop-title").focus();
  }
  function clear() { pts = []; done = false; radius = 0; arm(true); draw(); render(); }

  let opener = null;
  function start(m, from) {
    if (!opener || !document.contains(opener)) opener = document.activeElement;
    mode = m === "radius" ? "radius" : "distance";
    pts = from ? [from] : [];
    done = false;
    radius = 0;
    box.hidden = false;
    btn.classList.add("on");
    btn.setAttribute("aria-expanded", "true");
    if (ctx.sheet && ctx.sheet.active()) ctx.sheet.snap("peek");
    arm(true);
    draw();
    render();
    box.querySelector(".pop-title").focus();
    ui.live(`${mode === "distance" ? "거리" : "반경"} 재기를 시작했습니다. 지도를 눌러 점을 찍습니다`);
  }
  function stop() {
    arm(false);
    mode = null;
    pts = [];
    box.hidden = true;
    btn.classList.remove("on");
    btn.setAttribute("aria-expanded", "false");
    if (map.getSource("measure")) map.getSource("measure").setData(EMPTY);
    ui.live("재기를 마쳤습니다");
    const o = opener;
    opener = null;
    if (o && document.contains(o) && o !== document.body) o.focus();
  }

  // ---- 도구 단추: 누르면 거리와 반경 중 고르는 작은 창
  const btn = ui.toolButton({ id: "tool-measure", icon: "ruler", label: "재기", group: "measure", order: 0, onClick: () => (mode ? stop() : openPick(menu.hidden)) });
  btn.setAttribute("aria-expanded", "false");
  const menu = document.createElement("div");
  menu.className = "tool-pop measure-pick";
  menu.hidden = true;
  menu.setAttribute("role", "group");
  menu.setAttribute("aria-label", "재기 고르기");
  menu.innerHTML = `<button type="button" class="btn ghost" data-m="distance">${ui.icon("ruler", 18)}거리 재기</button>
    <button type="button" class="btn ghost" data-m="radius">${ui.icon("locate", 18)}반경 재기 (반경 안 건물 수)</button>`;
  document.body.appendChild(menu);
  function openPick(on) {
    menu.hidden = !on;
    btn.setAttribute("aria-expanded", String(on));
    if (on) menu.querySelector("button").focus();
  }
  menu.addEventListener("click", e => { const b = e.target.closest("[data-m]"); if (b) { openPick(false); opener = btn; start(b.dataset.m); } });
  menu.addEventListener("keydown", e => { if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); openPick(false); btn.focus(); } });
  document.addEventListener("pointerdown", e => { if (!menu.hidden && !menu.contains(e.target) && !btn.contains(e.target)) openPick(false); });

  // 다른 곳(여기 보기의 '여기서 재기')에서 시작
  bus.on("measure:start", ({ mode: m, from } = {}) => start(m || "distance", Array.isArray(from) ? from : null));
}
