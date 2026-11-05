// 주변 찾기 (명세 3.6절 행동 줄, WP5): 기준은 고른 장소, 지도 가운데, 역. 반경 100, 300, 500m.
// 종류별 건수를 누르면 목록과 번호 표식이 나온다. 거리는 언제나 '직선 거리'라고 적는다 (걷는 거리를 내지 않는다).
// 지도에는 우리 층 nearby-fill, nearby-line으로 반경 원을 그린다.

const RADII = [100, 300, 500];
const PAGE = 15;
const nf = n => Number(n).toLocaleString("ko-KR");
const meters = (a, b) => {
  const R = 6371008.8, r = Math.PI / 180;
  const dLat = (b[1] - a[1]) * r, dLng = (b[0] - a[0]) * r;
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(a[1] * r) * Math.cos(b[1] * r) * Math.sin(dLng / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(h));
};
function circle(c, r, n = 64) {
  const pts = [];
  const dLat = r / 111320, dLng = r / (111320 * Math.cos(c[1] * Math.PI / 180));
  for (let k = 0; k <= n; k++) { const a = (k / n) * 2 * Math.PI; pts.push([c[0] + dLng * Math.cos(a), c[1] + dLat * Math.sin(a)]); }
  return { type: "Feature", properties: {}, geometry: { type: "Polygon", coordinates: [pts] } };
}
// 상호 칩 조건 (places.json chips의 kind: biz)
function bizMatch(chip, x) {
  if (chip.exclude_middle && chip.exclude_middle.includes(x.m)) return false;
  return !!((chip.small && chip.small.includes(x.c)) || (chip.middle && chip.middle.includes(x.m)) || (chip.large && chip.large.includes(x.l)));
}

export function init(ctx) {
  ctx.css("graph.css");
  const { bus, ui, panel, data, map } = ctx;
  let st = null;          // {origin:{pt,label}, base:"place"|"center"|"basis", radius, view, list}. basis는 사용자가 고른 거리 기준점
  let ownMarkers = false;
  let bizP = null;

  // ---- 반경 원 층
  function ring(pt, r) {
    if (!map) return;
    ctx.mapApi.whenReady(() => {
      const f = pt ? { type: "FeatureCollection", features: [circle(pt, r)] } : { type: "FeatureCollection", features: [] };
      if (!map.getSource("nearby")) {
        map.addSource("nearby", { type: "geojson", data: f });
        const before = map.getLayer("station-link") ? "station-link" : undefined;
        map.addLayer({ id: "nearby-fill", type: "fill", source: "nearby", paint: { "fill-color": css("--accent"), "fill-opacity": 0.06 } }, before);
        map.addLayer({ id: "nearby-line", type: "line", source: "nearby", paint: { "line-color": css("--accent"), "line-width": 2, "line-dasharray": [3, 2] } }, before);
        ctx.mapApi.onTheme(() => {
          if (map.getLayer("nearby-fill")) map.setPaintProperty("nearby-fill", "fill-color", css("--accent"));
          if (map.getLayer("nearby-line")) map.setPaintProperty("nearby-line", "line-color", css("--accent"));
        });
      } else map.getSource("nearby").setData(f);
    });
  }

  function basePt() {
    if (!st) return null;
    if (st.base === "basis") { const b = ctx.basis.get(); if (b) return { pt: b.pt, label: b.n }; st.base = "center"; }
    if (st.base === "center") {
      if (map) { const c = map.getCenter(); return { pt: [c.lng, c.lat], label: "지도 가운데" }; }
      return { pt: data.center, label: "지도 가운데" };
    }
    return st.origin;
  }

  // ---- 반경 안 장소 세기
  function within(pt, r) {
    const out = [];
    for (const p of data.places) {
      if (!p.pt) continue;
      const d = meters(pt, p.pt);
      if (d <= r) out.push({ p, d });
    }
    return out.sort((a, b) => a.d - b.d);
  }

  function rows(pt, r, biz) {
    const inside = within(pt, r);
    const cats = [];
    cats.push({ key: "all", label: "건물 전체", color: null, items: inside.map(x => ({ path: x.p.p, name: x.p.n, sub: x.p.use || "", d: x.d, pt: x.p.pt })) });
    for (const g of data.useGroups) {
      const items = inside.filter(x => (x.p.ug || "none") === g.key).map(x => ({ path: x.p.p, name: x.p.n, sub: x.p.use || "용도 값 없음", d: x.d, pt: x.p.pt }));
      cats.push({ key: "ug:" + g.key, label: g.label, color: g.color, items });
    }
    const sensors = (data.sensors || []).map(s => ({ s, d: meters(pt, s.pt) })).filter(x => x.d <= r)
      .map(x => ({ path: x.s.p, name: x.s.n, sub: "센서", d: x.d, pt: x.s.pt }));
    cats.push({ key: "sensor", label: "센서", color: null, items: sensors });
    const bizCats = [];
    if (biz) {
      const near = biz.items.filter(x => x.pt && x.p).map(x => ({ x, d: meters(pt, x.pt) })).filter(y => y.d <= r).sort((a, b) => a.d - b.d);
      const toItem = y => ({ path: y.x.p, mark: y.x.b, name: y.x.n, sub: [y.x.c, y.x.f ? `${y.x.f}층` : ""].filter(Boolean).join(", "), d: y.d, pt: y.x.pt });
      bizCats.push({ key: "biz:all", label: "상호 전체", items: near.map(toItem) });
      for (const c of (data.chips || []).filter(c => c.kind === "biz")) bizCats.push({ key: "biz:" + c.key, label: c.label, items: near.filter(y => bizMatch(c, y.x)).map(toItem) });
    }
    return { cats, bizCats };
  }

  // ---- 화면
  function open({ center, label, radius = 300 } = {}) {
    const origin = center ? { pt: center, label: label || "고른 곳" } : null;
    st = { origin, base: origin ? "place" : "center", radius: RADII.includes(radius) ? radius : 300 };
    if (ctx.sheet && ctx.sheet.active() && ctx.sheet.current() === "peek") ctx.sheet.snap("half");
    // 장소 카드 위에 주변 화면을 쌓으면 목록에서 장소를 열 때 패널이 주변 화면까지 내린다 (같은 id 'place'를 다시 쓰므로).
    // 그래서 장소 카드를 닫는 걸음(뒤로 가기로 다시 열 수 있음)을 먼저 하고 주변 화면을 연다. 반경 원이 고른 곳을 보여 준다
    if (panel.has("place")) {
      ctx.state.set({ focus: null, unit: null }, { push: true, silent: true });
      bus.emit("deselect");
    }
    const content = panel.show({ id: "nearby", title: "주변 찾기", focus: true, onBack: closeAll,
      render: el => { el.innerHTML = `<h2 class="cd-name" tabindex="-1">주변 찾기</h2><div class="nb-body"></div>`; } });
    st.view = content;
    draw();
    if (!bizP) {
      const p = (ctx.source.businesses ? ctx.source.businesses() : Promise.reject(new Error("상호 자료 입구가 없습니다")))
        .then(v => { p.__v = v; return v; }).catch(e => { if (bizP === p) bizP = null; throw e; });
      bizP = p;
    }
    if (!bizP.__v) bizP.then(() => { if (st && st.view === content && !st.list) draw(); }).catch(() => {
      if (st && st.view === content) { const m = content.querySelector(".nb-biz-wait"); if (m) m.textContent = "상호 자료를 받지 못해 건물 수만 보여 줍니다."; }
    });
  }

  function closeAll() {
    cleanup();
    panel.remove("nearby");
  }
  function cleanup() {
    st = null;
    ring(null);
    if (ownMarkers) { ownMarkers = false; bus.emit("results:clear"); }
  }

  function draw() {
    if (!st || !st.view) return;
    const el = st.view.querySelector(".nb-body");
    const b = basePt();
    const r = st.radius;
    const biz = bizP && bizP.__v;
    ring(b.pt, r);
    if (map) {
      const c = circle(b.pt, r).geometry.coordinates[0];
      const bb = new maplibregl.LngLatBounds(c[0], c[0]);
      c.forEach(x => bb.extend(x));
      map.fitBounds(bb, { padding: 32, maxZoom: 18, duration: matchMedia("(prefers-reduced-motion: reduce)").matches ? 0 : 400 });
    }
    if (ownMarkers) { ownMarkers = false; bus.emit("results:clear"); }
    st.list = null;
    const { cats, bizCats } = rows(b.pt, r, biz);
    const all = cats[0].items.length;
    const btn = c => `<li><button type="button" data-key="${c.key}" ${c.items.length ? "" : "disabled"}>
        <span class="sw" style="${c.color ? `background:var(${c.color});box-shadow:inset 0 0 0 1px var(--map-bldg-line)` : "background:transparent"}"></span>
        <span>${esc(c.label)}</span><span class="n">${nf(c.items.length)}${c.key.startsWith("biz") ? "곳" : c.key === "sensor" ? "대" : "동"}</span></button></li>`;
    const seg = (name, opts, cur) => opts.map(([v, t, dis]) => `<label><input type="radio" name="${name}" value="${v}" ${String(cur) === String(v) ? "checked" : ""} ${dis ? "disabled" : ""}>${esc(t)}</label>`).join("");
    el.innerHTML = `
      <fieldset class="nb-pick"><legend>기준</legend><div class="nb-seg">${seg("nb-base", [
        ["place", st.origin ? st.origin.label : "고른 장소", !st.origin], ["center", "지도 가운데"],
        ...(ctx.basis.get() ? [["basis", `거리 기준(${ctx.basis.get().n})`]] : [])], st.base)}</div></fieldset>
      <fieldset class="nb-pick"><legend>반경</legend><div class="nb-seg">${seg("nb-r", RADII.map(x => [x, `${x}m`]), r)}</div></fieldset>
      <p class="nb-basis" aria-live="polite">${esc(b.label)}에서 <b>직선 거리 ${r}m</b> 안. 걷는 거리가 아니라 건물 대표점까지의 직선 거리입니다.</p>
      ${all ? `<h3 class="cd-h">종류별 건수</h3><ul class="nb-counts">${cats.map(btn).join("")}</ul>`
        : `<div class="nb-empty" role="status"><p>이 반경 안에는 건물 자료가 없습니다.</p>
          ${r < 500 ? `<button type="button" class="btn mini nb-wider">반경 500m로 넓히기</button>` : ""}</div>`}
      ${biz ? `<h3 class="cd-h">상호 <span class="muted">(공공데이터에 등록된 곳만)</span></h3><ul class="nb-counts">${bizCats.map(btn).join("")}</ul>`
        : `<p class="muted small nb-biz-wait">상호 건수를 불러오는 중입니다.</p>`}`;
    el.querySelectorAll('input[name="nb-base"]').forEach(x => x.addEventListener("change", () => { st.base = x.value; draw(); }));
    el.querySelectorAll('input[name="nb-r"]').forEach(x => x.addEventListener("change", () => { st.radius = Number(x.value); draw(); }));
    const wider = el.querySelector(".nb-wider");
    if (wider) wider.addEventListener("click", () => { st.radius = 500; draw(); });
    const byKey = new Map([...cats, ...bizCats].map(c => [c.key, c]));
    el.querySelectorAll(".nb-counts button[data-key]").forEach(x => x.addEventListener("click", () => list(byKey.get(x.dataset.key), b, r)));
    ui.live(`${b.label}에서 직선 거리 ${r}m 안 건물 ${nf(all)}동`);
  }

  // 종류 하나의 목록과 번호 표식
  function list(cat, b, r) {
    if (!cat || !st) return;
    st.list = cat;
    let shown = Math.min(PAGE, cat.items.length);
    const el = st.view.querySelector(".nb-body");
    const markers = () => {
      const items = cat.items.slice(0, shown).map((x, k) => ({ no: k + 1, path: x.mark || x.path, pt: x.pt }));
      bus.emit("results:show", { items, title: `${b.label} 주변 ${cat.label}` });
      ownMarkers = true;
    };
    const rowsHtml = () => cat.items.slice(0, shown).map((x, k) => `<li><button type="button" data-k="${k}">
        <span class="nb-no">${k + 1}</span><span class="nb-nm">${esc(x.name)}<span class="nb-sub">${esc(x.sub || "")}</span></span>
        <span class="nb-d">직선 ${nf(Math.round(x.d))}m</span></button></li>`).join("");
    el.innerHTML = `<p class="nb-basis"><button type="button" class="linkish nb-back">${ui.icon("back", 16)}종류별 건수로</button></p>
      <h3 class="cd-h">${esc(cat.label)} <span class="muted">${nf(cat.items.length)}곳, ${esc(b.label)}에서 직선 거리 ${r}m 안</span></h3>
      <ul class="nb-list">${rowsHtml()}</ul>
      ${cat.items.length > shown ? `<button type="button" class="btn ghost mini nb-more">${PAGE}곳 더 보기</button>` : ""}
      <p class="muted small">거리는 걷는 거리가 아니라 직선 거리입니다.</p>`;
    const bind = () => {
      el.querySelectorAll(".nb-list button").forEach(x => x.addEventListener("click", () => {
        const it = cat.items[Number(x.dataset.k)];
        el.querySelectorAll(".nb-list button.on").forEach(y => y.classList.remove("on"));
        x.classList.add("on");
        bus.emit("results:active", { no: Number(x.dataset.k) + 1, from: "list" });
        if (ctx.openPath) ctx.openPath(it.path, { from: "list" });
        else bus.emit("select", { path: it.path, from: "list", fit: true });
      }));
    };
    bind();
    const more = el.querySelector(".nb-more");
    if (more) more.addEventListener("click", () => {
      shown = Math.min(cat.items.length, shown + PAGE);
      el.querySelector(".nb-list").innerHTML = rowsHtml();
      bind();
      markers();
      if (shown >= cat.items.length) more.remove();
    });
    el.querySelector(".nb-back").addEventListener("click", () => draw());
    markers();
    const h = el.querySelector(".cd-h");
    if (h) { h.tabIndex = -1; h.focus({ preventScroll: true }); }
  }

  // 표식을 지도에서 누르면 목록의 그 행을 강조하고 스크롤한다
  bus.on("results:active", ({ no, from } = {}) => {
    if (!ownMarkers || from !== "map" || !st || !st.view) return;
    const b = st.view.querySelector(`.nb-list button[data-k="${no - 1}"]`);
    if (!b) return;
    st.view.querySelectorAll(".nb-list button.on").forEach(y => y.classList.remove("on"));
    b.classList.add("on");
    b.scrollIntoView({ block: "nearest" });
  });
  // 다른 모듈이 표식을 바꾸면 우리 것이 아니다
  bus.on("results:show", ({ title } = {}) => { if (!st || !st.list || !String(title || "").includes(st.list.label)) ownMarkers = false; });
  // 주변 화면이 다른 화면에 밀려 사라졌으면 원과 표식을 치운다
  const check = () => setTimeout(() => { if (st && st.view && !st.view.isConnected) cleanup(); }, 0);
  bus.on("select", check);
  bus.on("deselect", check);

  bus.on("nearby:open", open);
}
