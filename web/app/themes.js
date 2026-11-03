// 지도 보기 (명세 4.2절 있으면 좋음 1, 5, 9, 7.2절 색): 건물 종류, 층수, 지은 때, 행정동 소속 주제와 범례, 연도 슬라이더, 층수 입체 보기.
// 켜면 범례가 함께 열리고 자료 없음은 회색이다. 끄면 기본 색(옅은 용도별 색)으로 돌아간다. 주소창 theme은 덮어쓴다.
// 건물 색은 mapApi.setBuildingPaint로만 바꾸고 다른 묶음의 층은 건드리지 않는다. 입체는 이 묶음의 층 bldg-3d다.

const nf = n => Number(n).toLocaleString("ko-KR");
const GRAY = "#98a2b3";
// 7.2절 표. 건물 종류는 밝고 어두운 화면이 같다
const USE = { home: "#e69f00", shop: "#d55e00", office: "#0072b2", edu: "#009e73", factory: "#56b4e9", car: "#cc79a7" };
const FLOORS = { light: ["#7fa9d8", "#5f90c8", "#4577b4", "#2d5e9c", "#174a83"], dark: ["#2d5e9c", "#4577b4", "#5f90c8", "#7fa9d8", "#a9c8ea"] };
const FLOOR_LABEL = ["1~2층", "3~5층", "6~10층", "11~20층", "21층 이상"];
const YEARS = [2010, 2000, 1990, 1980, 1970, 1960];
const YEAR_C = { light: ["#5fb8a3", "#46a48e", "#30907a", "#1f7b67", "#106654", "#065142"], dark: ["#075748", "#106c5a", "#20836e", "#359a83", "#4fae97", "#6fc2ad"] };

export async function init(ctx) {
  const { bus, state, ui, data, mapApi } = ctx;
  if (!ctx.map) return;   // 지도를 못 그리면 지도 보기 입구도 두지 않는다
  ctx.css("tools.css");
  const map = ctx.map;
  const places = data.places;
  const useLabel = Object.fromEntries((data.useGroups || []).map(g => [g.key, g.label]));
  const adminDongs = (data.dongs || []).filter(d => d.kind === "admin");
  const yearOf = p => (p.ad ? Number(String(p.ad).slice(0, 4)) : null);
  const years = places.map(yearOf).filter(Boolean);
  const yMin = Math.min(...years), yMax = Math.max(...years);

  let key = null;          // use, floors, year, dong:{이름}
  let upto = yMax;         // 연도 슬라이더
  let threeD = false;

  // 장소 번호(i)를 묶음별로 모아 match 식을 만든다. 면과 점 모두 i를 가진다
  function matchExpr(classOf, colorOf, fallback) {
    const groups = new Map();
    for (const p of places) {
      const c = classOf(p);
      if (c == null) continue;
      if (!groups.has(c)) groups.set(c, []);
      groups.get(c).push(p.i);
    }
    const parts = [];
    for (const [c, list] of groups) parts.push(list, colorOf(c));
    return parts.length ? ["match", ["get", "i"], ...parts, fallback] : fallback;
  }
  const dark = () => (darkMode() ? "dark" : "light");
  const floorClass = p => { const f = p.fa || 0; return f <= 0 ? null : f <= 2 ? 0 : f <= 5 ? 1 : f <= 10 ? 2 : f <= 20 ? 3 : 4; };
  const yearClass = p => { const y = yearOf(p); if (!y) return null; const k = YEARS.findIndex(d => y >= d); return k < 0 ? YEARS.length - 1 : k; };

  // 주제마다 {color, opacity, legend:[{color, label, n, faint}], title, note}
  function themeSpec(k) {
    const count = f => { const m = new Map(); for (const p of places) { const c = f(p); m.set(c, (m.get(c) || 0) + 1); } return m; };
    if (k === "use") {
      const n = count(p => p.ug || "none");
      const legend = Object.keys(USE).map(u => ({ color: USE[u], label: useLabel[u] || u, n: n.get(u) || 0 }));
      legend.push({ color: GRAY, label: "그 외", n: n.get("other") || 0 }, { color: GRAY, label: "자료 없음", n: n.get("none") || 0, faint: true });
      return { title: "건물 종류", legend, color: matchExpr(p => (USE[p.ug] ? p.ug : null), u => USE[u], GRAY),
        opacity: matchExpr(p => (p.ug && p.ug !== "none" ? null : "none"), () => 0.45, 0.92) };
    }
    if (k === "floors") {
      const n = count(floorClass), pal = FLOORS[dark()];
      const legend = FLOOR_LABEL.map((l, j) => ({ color: pal[j], label: `지상 ${l}`, n: n.get(j) || 0 }));
      legend.push({ color: GRAY, label: "층수 자료 없음", n: n.get(null) || 0, faint: true });
      return { title: "층수", legend, color: matchExpr(floorClass, j => pal[j], GRAY), opacity: matchExpr(p => (floorClass(p) == null ? 0 : null), () => 0.45, 0.92),
        note: "층수 0은 값이 없는 것으로 보았습니다." };
    }
    if (k === "year") {
      const n = count(yearClass), pal = YEAR_C[dark()];
      const legend = YEARS.map((d, j) => ({ color: pal[j], label: j === YEARS.length - 1 ? `${d}년대 이전` : `${d}년대`, n: n.get(j) || 0 }));
      legend.push({ color: GRAY, label: "사용승인일 자료 없음", n: n.get(null) || 0, faint: true });
      const op = matchExpr(p => { const y = yearOf(p); return !y ? "none" : y > upto ? "later" : null; }, c => (c === "none" ? 0.45 : 0.1), 0.92);
      return { title: "지은 때 (사용승인 연도)", legend, color: matchExpr(yearClass, j => pal[j], GRAY), opacity: op,
        note: "사용승인일은 2018년까지의 자료입니다." };
    }
    if (k && k.startsWith("dong:")) {
      const name = k.slice(5);
      const on = dark() === "dark" ? "#6f9bff" : "#0072b2", off = dark() === "dark" ? "#3a3f47" : "#d5d9e0";
      const n = count(p => (!p.dong ? "none" : p.dong === name ? "in" : "out"));
      return { title: "행정동 소속", legend: [{ color: on, label: `${name} 소속`, n: n.get("in") || 0 }, { color: off, label: "다른 행정동", n: n.get("out") || 0 },
        { color: GRAY, label: "행정동 값 없음 (도로명주소 없는 건물)", n: n.get("none") || 0, faint: true }],
        color: matchExpr(p => (!p.dong ? null : p.dong === name ? "in" : "out"), c => (c === "in" ? on : off), GRAY),
        opacity: matchExpr(p => (p.dong ? null : "none"), () => 0.45, 0.92), note: "행정동 경계선은 공개 자료가 없어 그리지 않습니다." };
    }
    return null;
  }

  // ---- 그리기
  function paint() {
    const spec = key ? themeSpec(key) : null;
    if (!spec) { mapApi.resetBuildingPaint(); mapApi.setBuildingPaint({ "circle-opacity": 1 }); }
    else mapApi.setBuildingPaint({ "fill-color": spec.color, "circle-color": spec.color, "fill-opacity": spec.opacity, "circle-opacity": spec.opacity });
    if (map.getLayer("bldg-3d")) map.setPaintProperty("bldg-3d", "fill-extrusion-color", spec ? spec.color : mapApi.defaultBuildingColor());
    renderLegend(spec);
    renderPop();
  }

  function apply(k, { write = true, announce = true } = {}) {
    if (k && k.startsWith("dong:") && !adminDongs.some(d => d.n === k.slice(5))) k = null;
    if (k && !["use", "floors", "year"].includes(k) && !k.startsWith("dong:")) k = null;
    key = k;
    if (key !== "year") upto = yMax;
    paint();
    btn.classList.toggle("on", !!key || threeD);
    if (write) state.set({ theme: key }, { silent: true });
    bus.emit("theme:change", { key });
    if (announce) ui.live(key ? `지도 보기: ${themeSpec(key).title}. 범례를 열었습니다` : "지도 보기를 기본 색으로 돌렸습니다");
  }

  // ---- 층수 입체 보기 (면 건물만)
  function setThreeD(on) {
    threeD = !!on;
    if (threeD && !map.getLayer("bldg-3d")) {
      map.addLayer({ id: "bldg-3d", type: "fill-extrusion", source: "bldg", minzoom: 14,
        paint: { "fill-extrusion-color": key ? themeSpec(key).color : mapApi.defaultBuildingColor(),
          "fill-extrusion-height": ["*", ["max", ["coalesce", ["get", "fa"], 0], 0], 3], "fill-extrusion-base": 0, "fill-extrusion-opacity": 0.85 } },
      map.getLayer("spread-fill") ? "spread-fill" : undefined);
    }
    if (!threeD && map.getLayer("bldg-3d")) map.removeLayer("bldg-3d");
    const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
    map.easeTo({ pitch: threeD ? 50 : 0, duration: reduce ? 0 : 400 });
    btn.classList.toggle("on", !!key || threeD);
    renderPop();
    ui.live(threeD ? "층수 입체 보기를 켰습니다. 층수로 만든 모형이고 실제 높이가 아닙니다" : "층수 입체 보기를 껐습니다");
  }

  // ---- 도구 단추와 고르는 창
  const btn = ui.toolButton({ id: "tool-view", icon: "layers", label: "지도 보기", group: "view", order: 0, onClick: () => openPop(pop.hidden) });
  btn.setAttribute("aria-expanded", "false");
  btn.setAttribute("aria-controls", "theme-pop");
  const pop = document.createElement("div");
  pop.id = "theme-pop";
  pop.className = "tool-pop theme-pop";
  pop.hidden = true;
  pop.setAttribute("role", "dialog");
  pop.setAttribute("aria-label", "지도 보기");
  document.body.appendChild(pop);
  const legendBox = document.createElement("div");
  legendBox.id = "theme-legend";
  legendBox.className = "theme-legend";
  legendBox.hidden = true;
  legendBox.setAttribute("role", "region");
  legendBox.setAttribute("aria-label", "지도 보기 범례");
  document.body.appendChild(legendBox);

  const legendRows = spec => `<ul class="legend">${spec.legend.map(r => `<li class="legend-row"><span class="sw${r.faint ? " faint" : ""}" style="background:${r.color}"></span>${esc(r.label)} <span class="muted">${nf(r.n)}동</span></li>`).join("")}</ul>`;
  const baseLegend = () => `<ul class="legend">${(data.useGroups || []).map(g => `<li class="legend-row"><span class="sw" style="background:var(${esc(g.color)})"></span>${esc(g.label)} <span class="muted">${nf(g.n)}동</span></li>`).join("")}</ul>`;

  function renderPop() {
    const opt = (v, label) => `<label class="pick"><input type="radio" name="theme-k" value="${v}"${(key || "") === v || (v === "dong" && key && key.startsWith("dong:")) ? " checked" : ""}>${label}</label>`;
    const dongSel = key && key.startsWith("dong:") ? key.slice(5) : (adminDongs[0] && adminDongs[0].n);
    const spec = key ? themeSpec(key) : null;
    pop.innerHTML = `<div class="pop-head"><h2 class="pop-title" tabindex="-1">지도 보기</h2>
      <button type="button" class="sb-btn pop-close" aria-label="지도 보기 닫기">${ui.icon("close", 18)}</button></div>
      <fieldset class="pick-set"><legend class="sr-only">건물 색</legend>
        ${opt("", "기본 (옅은 용도별 색)")}${opt("use", "건물 종류")}${opt("floors", "층수")}${opt("year", "지은 때")}${adminDongs.length ? opt("dong", "행정동 소속") : ""}
      </fieldset>
      ${adminDongs.length ? `<div class="dong-pick"${key && key.startsWith("dong:") ? "" : " hidden"}><label for="theme-dong">행정동</label>
        <select id="theme-dong">${adminDongs.map(d => `<option${d.n === dongSel ? " selected" : ""}>${esc(d.n)}</option>`).join("")}</select></div>` : ""}
      ${key === "year" ? `<div class="year-pick"><label for="theme-year">이 해까지 사용승인된 건물만 진하게: <b>${upto}년</b></label>
        <input id="theme-year" type="range" min="${yMin}" max="${yMax}" step="1" value="${upto}">
        <p class="small muted">그해 실제 있던 건물이 아닙니다. 철거 자료가 없습니다.</p></div>` : ""}
      <div class="pop-legend">${spec ? legendRows(spec) + (spec.note ? `<p class="small muted">${esc(spec.note)}</p>` : "") : baseLegend()}</div>
      <label class="pick pick-3d"><input type="checkbox" id="theme-3d"${threeD ? " checked" : ""}>층수 입체 보기</label>
      ${threeD ? `<p class="small muted">층수로 만든 모형이고 실제 높이가 아닙니다. 점만 있는 건물과 층수 값이 없는 건물은 세우지 않습니다.</p>` : ""}`;
    pop.querySelector(".pop-close").onclick = () => { openPop(false); btn.focus(); };
    pop.querySelectorAll('input[name="theme-k"]').forEach(r => r.onchange = () => {
      const v = r.value === "dong" ? "dong:" + pop.querySelector("#theme-dong").value : r.value || null;
      apply(v);
      const again = pop.querySelector(`input[name="theme-k"][value="${r.value}"]`);
      if (again) again.focus();
    });
    const ds = pop.querySelector("#theme-dong");
    if (ds) ds.onchange = () => { apply("dong:" + ds.value); const s = pop.querySelector("#theme-dong"); if (s) s.focus(); };
    const yr = pop.querySelector("#theme-year");
    if (yr) yr.oninput = () => {
      upto = Number(yr.value);
      const spec2 = themeSpec("year");
      mapApi.setBuildingPaint({ "fill-opacity": spec2.opacity, "circle-opacity": spec2.opacity });
      const b = yr.parentElement.querySelector("b");
      if (b) b.textContent = `${upto}년`;
    };
    const d3 = pop.querySelector("#theme-3d");
    d3.onchange = () => { setThreeD(d3.checked); const x = pop.querySelector("#theme-3d"); if (x) x.focus(); };
  }

  function renderLegend(spec) {
    legendBox.hidden = !spec;
    if (!spec) { legendBox.innerHTML = ""; return; }
    legendBox.innerHTML = `<div class="pop-head"><h2 class="legend-title">${esc(spec.title)}</h2>
      <button type="button" class="btn ghost mini legend-off">기본 색으로</button></div>${legendRows(spec)}`;
    legendBox.querySelector(".legend-off").onclick = () => apply(null);
  }

  function openPop(on) {
    pop.hidden = !on;
    btn.setAttribute("aria-expanded", String(on));
    if (on) { renderPop(); pop.querySelector(".pop-title").focus(); }
  }
  pop.addEventListener("keydown", e => { if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); openPop(false); btn.focus(); } });
  document.addEventListener("pointerdown", e => { if (!pop.hidden && !pop.contains(e.target) && !btn.contains(e.target)) openPop(false); });

  mapApi.onTheme(() => { if (key) paint(); else renderPop(); });
  state.onChange((changed, s, source) => { if (source === "pop" && changed.includes("theme")) apply(s.theme || null, { write: false, announce: false }); });
  const t0 = state.get("theme");
  if (t0) mapApi.whenReady(() => apply(t0, { write: true, announce: false }));
  else renderPop();
}
