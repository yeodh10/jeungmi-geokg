// 지도 앱의 뼈대 (명세 5.2절). 공개 사이트와 로컬 서버가 같은 파일을 쓰고 자료 입구(cfg.source)만 다르다.
// 뼈대를 세운 뒤 기능 모듈을 차례로 불러 init(ctx)를 부른다. 기능 파일을 더해도 이 파일은 고치지 않는다.
//   기능 모듈: export function init(ctx) 또는 export default { init }. 뺀 기능은 export const off = true.
// 같은 폴더의 뼈대 모듈도 ?v=빌드 값을 붙여 받는다 (재배포 직후 옛 파일과 섞이지 않게).

const cfg = window.APP_CONFIG || { mode: "public", root: "", app: ".", build: "", source: "./source-static.js", extra: [] };
const V = "?v=" + encodeURIComponent(cfg.build || "");
const FEATURES = ["search", "chips", "card", "popup", "nearby", "here", "ask", "datatab", "tour", "themes", "measure", "conditions"];
const SITE = "https://yeodh10.github.io/jeungmi-geokg";
const KIND = { building: "건물", "building-gis": "건물", station: "역", road: "길", "admin-dong": "행정동", "legal-dong": "법정동",
  "basic-zone": "우편구역", sensor: "센서", parcel: "필지", entrance: "입구", business: "상호", group: "단지" };
const STATUS = { "주소 미연결": "도로명주소 없는 건물" };
const nf = n => Number(n).toLocaleString("ko-KR");
const short = a => String(a || "").replace(/^서울특별시\s+/, "");
// 탭 아이콘: 사이트 바깥 /favicon.ico를 찾지 않게 직접 그린 점 셋을 넣는다
if (!document.querySelector('link[rel="icon"]')) {
  const ic = document.createElement("link");
  ic.rel = "icon";
  ic.href = "data:image/svg+xml," + encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><rect width="32" height="32" rx="8" fill="#0b2545"/><path d="M10 21 16 11 22 19" fill="none" stroke="#fff" stroke-width="2"/><circle cx="10" cy="21" r="3" fill="#fff"/><circle cx="16" cy="11" r="3" fill="#fff"/><circle cx="22" cy="19" r="3" fill="#f6a54b"/></svg>');
  document.head.appendChild(ic);
}

const [{ createState, normPath }, { createUI }, { createPanel }, { createSheet }, M] = await Promise.all(
  ["state", "ui", "panel", "sheet", "map"].map(n => import(`${cfg.app}/${n}.js${V}`)));

// ---- 이벤트 (명세 5.2절 표)
function createBus() {
  const m = new Map();
  return {
    on(name, fn) { if (!m.has(name)) m.set(name, []); m.get(name).push(fn); return () => this.off(name, fn); },
    off(name, fn) { const a = m.get(name); if (a) { const k = a.indexOf(fn); if (k >= 0) a.splice(k, 1); } },
    emit(name, payload) { for (const fn of (m.get(name) || []).slice()) { try { fn(payload); } catch (e) { console.error(e); } } },
  };
}

const loadedCss = new Set();
const ctx = {
  cfg, source: null, features: new Set(), map: null, mapApi: M.nullMapApi(), data: null,
  bus: createBus(), state: createState(), ui: createUI(),
  css(name) {
    if (loadedCss.has(name)) return;
    loadedCss.add(name);
    const l = document.createElement("link");
    l.rel = "stylesheet";
    l.href = `${cfg.app}/${name}${V}`;
    document.head.appendChild(l);
  },
};
const { bus, state, ui } = ctx;
window.__app = ctx;   // 확인용 (콘솔과 자동 시험에서 상태를 읽는다)


// ---- 찾기창 틀과 메뉴. 찾기 동작은 search 모듈이 #q에 붙인다
const siteBase = cfg.mode === "public" ? cfg.root : SITE;
const searchbar = document.getElementById("searchbar");
searchbar.innerHTML = `<div class="sb" role="search">
  <button type="button" class="sb-btn" id="menu-btn" aria-expanded="false" aria-controls="menu" aria-label="메뉴">${ui.icon("menu")}</button>
  <label class="sr-only" for="q">장소, 주소, 지번, 공식 번호 찾기</label>
  <input id="q" class="sb-input" type="text" placeholder="장소, 주소, 지번 찾기" autocomplete="off" spellcheck="false" enterkeyhint="search">
  <button type="button" class="sb-btn sb-collapse" aria-controls="panel" aria-expanded="true" aria-label="장소 정보 패널 접기">${ui.icon("up")}</button>
</div>
<div id="menu" class="menu" hidden>
  <nav aria-label="사이트 메뉴"><ul>
    <li><a href="${siteBase}/about/">소개</a></li>
    <li><a href="${siteBase}/id/building/">건물 목록</a></li>
    <li><a href="${siteBase}/def/">어휘</a></li>
    <li><a href="https://github.com/yeodh10/jeungmi-geokg" target="_blank" rel="noopener">소스 코드 ${ui.icon("external", 14)}</a></li>
    ${cfg.mode === "local" ? '<li><a href="/">변경 제안 검토</a></li>' : ""}
  </ul></nav>
  <fieldset class="theme-pick"><legend>화면 밝기</legend>
    <label><input type="radio" name="theme-pick" value="system">${ui.icon("system", 18)}시스템</label>
    <label><input type="radio" name="theme-pick" value="light">${ui.icon("sun", 18)}밝게</label>
    <label><input type="radio" name="theme-pick" value="dark">${ui.icon("moon", 18)}어둡게</label>
  </fieldset>
</div>`;
const menuBtn = document.getElementById("menu-btn"), menu = document.getElementById("menu");
const syncTheme = () => { for (const r of menu.querySelectorAll('input[name="theme-pick"]')) r.checked = r.value === themeChoice(); };
function openMenu(on) {
  menu.hidden = !on;
  menuBtn.setAttribute("aria-expanded", String(on));
  if (on) { syncTheme(); (menu.querySelector("a") || menu).focus(); }
}
menuBtn.addEventListener("click", () => openMenu(menu.hidden));
menu.addEventListener("keydown", e => { if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); openMenu(false); menuBtn.focus(); } });
menu.addEventListener("change", e => { if (e.target.name === "theme-pick") setTheme(e.target.value); });
document.addEventListener("pointerdown", e => { if (!menu.hidden && !menu.contains(e.target) && !menuBtn.contains(e.target)) openMenu(false); });
menu.addEventListener("focusout", e => { if (!menu.hidden && e.relatedTarget && !menu.contains(e.relatedTarget) && e.relatedTarget !== menuBtn) openMenu(false); });

// 키보드 순서: 찾기창, 칩, 패널을 지도보다 앞에 둔다 (화면 배치는 그대로, 모두 고정 위치)
for (const id of ["searchbar", "map-top", "panel"]) document.body.insertBefore(document.getElementById(id), document.getElementById("map"));

// ---- 패널, 시트
ctx.panel = createPanel(ctx);
ctx.sheet = createSheet(ctx, ctx.panel.el);
const { panel, sheet } = ctx;
const collapseBtn = searchbar.querySelector(".sb-collapse");
collapseBtn.addEventListener("click", () => panel.collapse());
panel.onCollapse(on => {
  collapseBtn.setAttribute("aria-expanded", String(!on));
  collapseBtn.setAttribute("aria-label", on ? "장소 정보 패널 펴기" : "장소 정보 패널 접기");
  collapseBtn.innerHTML = ui.icon(on ? "down" : "up");
  ui.live(on ? "패널을 접었습니다" : "패널을 폈습니다");
  ctx.mapApi.setPadding(padding());
});

// 지도에서 패널과 시트가 가리는 곳을 뺀다 (명세 3.2, 3.3절)
function padding() {
  if (sheet.active()) {
    const H = sheet.heights();
    const bar = searchbar.getBoundingClientRect().bottom;
    const chips = document.getElementById("map-top");
    const top = Math.round(chips && chips.offsetParent && chips.childElementCount ? chips.getBoundingClientRect().bottom + 8 : bar + 8);
    return { left: 0, right: 0, top, bottom: sheet.current() === "full" ? H.half : sheet.height() };
  }
  return { left: panel.isCollapsed() ? 0 : 404, right: 0, top: 0, bottom: 0 };
}
sheet.onChange(() => ctx.mapApi.setPadding(padding()));

// ---- 지도. WebGL을 못 쓰면 안내만 띄우고 찾기, 목록, 장소 정보는 그대로 쓴다 (명세 3.11절)
const mapEl = document.getElementById("map");
const urlView = viewFrom(state.get("z"), state.get("c"));
let mapReady = null;
try {
  const start = urlView || { center: MAP_CENTER, zoom: sheet.active() ? 15 : 14.6 };
  ctx.map = M.createMap(mapEl, start);
  ctx.map.setPadding(padding());
  const made = M.createMapApi(ctx, ctx.map);
  ctx.mapApi = made.api;
  mapReady = made;
  ctx.map.on("webglcontextlost", () => {
    const box = document.createElement("div");
    box.className = "map-fallback";
    box.innerHTML = `<p>지도 그림이 멈췄습니다.</p><button type="button" class="btn">${ui.icon("refresh", 16)}다시 불러오기</button>`;
    box.querySelector("button").onclick = () => location.reload();
    mapEl.appendChild(box);
  });
} catch (e) {
  console.warn("지도를 그리지 못했습니다:", e && e.message);
  mapEl.innerHTML = `<div class="map-fallback"><p>이 브라우저에서는 지도를 그릴 수 없습니다 (WebGL을 쓸 수 없음).</p>
    <p class="small">찾기, 결과 목록, 장소 정보, 묻기, 자료 탭은 그대로 쓸 수 있습니다.</p></div>`;
  document.body.classList.add("no-map");
}

// 도구 줄: 확대, 축소, 역, 내 위치 (지도 보기와 재기, 링크는 다른 묶음이 더한다)
if (ctx.map) {
  const m = ctx.map;
  ui.toolButton({ id: "tool-zoom-in", icon: "plus", label: "확대", group: "zoom", order: 0, onClick: () => m.zoomIn({ duration: M.dur(250) }) });
  ui.toolButton({ id: "tool-zoom-out", icon: "minus", label: "축소", group: "zoom", order: 1, onClick: () => m.zoomOut({ duration: M.dur(250) }) });
  ui.toolButton({ id: "tool-station", icon: "station", label: "증미역으로", group: "place", order: 0,
    onClick: () => m.easeTo({ center: (ctx.data && ctx.data.station && ctx.data.station.pt) || MAP_CENTER, zoom: Math.max(m.getZoom(), 16), duration: M.dur(400) }) });
  ui.toolButton({ id: "tool-locate", icon: "locate", label: "내 위치", group: "place", order: 1, onClick: locate });
}

// 내 위치: 눌렀을 때만 권한을 묻는다. 거부, 실패, 범위 밖은 한 줄 알림만 내고 지도는 움직이지 않는다
function locate() {
  if (!navigator.geolocation) { ui.toast("이 브라우저는 위치를 알려 주지 않습니다."); return; }
  ui.toast("내 위치를 찾는 중입니다");
  navigator.geolocation.getCurrentPosition(pos => {
    const pt = [pos.coords.longitude, pos.coords.latitude];
    const [w, s, e, n] = MAP_BOUNDS;
    if (!(pt[0] >= w && pt[0] <= e && pt[1] >= s && pt[1] <= n)) { ui.toast("지금 위치가 이 지도의 범위(증미역 둘레 2.5km) 밖입니다."); return; }
    ctx.mapApi.setMe(pt);
    ctx.map.easeTo({ center: pt, zoom: Math.max(ctx.map.getZoom(), 16), duration: M.dur(400) });
    ui.live("지도를 내 위치로 옮겼습니다");
  }, err => {
    ui.toast(err && err.code === 1 ? "위치 권한이 없어 내 위치를 볼 수 없습니다." : "내 위치를 알 수 없습니다. 잠시 뒤에 다시 해 보세요.");
  }, { enableHighAccuracy: true, timeout: 10000, maximumAge: 60000 });
}

// ---- 자료
function indexData(places, shapes) {
  const d = { ...places, shapes };
  d.byIndex = [];
  for (const p of places.places) d.byIndex[p.i] = p;
  d.byPath = new Map();
  for (const p of places.places) d.byPath.set(p.p, p);
  d.groupById = new Map(places.groups.map(g => [g.g, g]));
  d.groupOf = i => { const p = d.byIndex[i]; return p && p.g != null ? d.groupById.get(p.g) || null : null; };
  for (const list of [places.roads, places.dongs, places.zones, places.sensors]) for (const x of list || []) d.byPath.set(x.p, x);
  if (places.station) d.byPath.set(places.station.p, places.station);
  // 필지: 건물 행의 pnu와 지번에서 만든다. 위에 건물이 없는 필지는 parcels에 따로 있다 (좌표 없음)
  const parcels = new Map();
  for (const p of places.places) {
    if (!p.pnu) continue;
    const key = "parcel/" + p.pnu;
    if (!parcels.has(key)) parcels.set(key, { p: key, pnu: p.pnu, j: p.j, m: [] });
    parcels.get(key).m.push(p.i);
  }
  for (const x of places.parcels || []) if (!parcels.has(x.p)) parcels.set(x.p, { ...x, pnu: x.p.split("/")[1], m: [] });
  for (const [k, v] of parcels) if (!d.byPath.has(k)) d.byPath.set(k, v);
  d.kindOf = path => String(path).split("/")[0];
  d.nameOf = path => { const x = d.byPath.get(path); return x ? x.n || x.j || x.p : null; };
  return d;
}

async function loadData() {
  try {
    const [places, shapes, area] = await Promise.all([ctx.source.places(), ctx.source.shapes(), ctx.source.area().catch(() => null)]);
    return { data: indexData(places, shapes), area };
  } catch (e) {
    await new Promise(resolve => {
      ui.retryBar("장소 자료를 받지 못했습니다. 배경 지도만 보입니다.", resolve);
    });
    return loadData();
  }
}

// ---- 장소 고르기 (select, deselect)
let selected = null;
const placeCardKinds = /^(entrance|business|parcel|observation)\//;

function resolveSel(sel) {
  const d = ctx.data;
  let path = sel.path ? normPath(sel.path) : null;
  let group = null;
  if (sel.group != null && sel.group !== "") group = d.groupById.get(Number(sel.group)) || null;
  if (!path && group) path = d.byIndex[group.m[0]].p;
  const place = path ? d.byPath.get(path) : null;
  const card = sel.card || null;
  if (!place && !card) return null;
  const kind = group ? "group" : d.kindOf(path);
  let paths = place ? [path] : [], pt = (place && place.pt) || (card && card.pt) || null, name;
  if (group) { paths = group.m.map(i => d.byIndex[i].p); pt = group.pt; name = group.n; }
  else if (kind === "parcel" && place) { paths = place.m.map(i => d.byIndex[i].p); pt = place.m.length ? d.byIndex[place.m[0]].pt : null; name = place.j || place.p; }
  else name = (place && (place.n || place.j)) || (card && card.name) || path;
  // 점이 없는 유형(길, 동네)은 지도에 그릴 것이 없다
  if (["road", "admin-dong", "legal-dong", "basic-zone"].includes(kind)) { paths = []; pt = null; }
  return { path, group, place, card, kind, kindLabel: KIND[kind] || (card && card.kindLabel) || "장소", name, pt, paths };
}

function onSelect(sel) {
  if (!ctx.data) return;
  const r = resolveSel(sel);
  if (!r) { notFound(); return; }
  selected = { ...sel, ...r };
  if (!sel.noPush) state.set({ focus: r.path, unit: r.group ? "group" : null }, { push: true, silent: true });
  const labelPt = r.pt && r.kind !== "station" ? { pt: r.pt, text: r.name } : null;
  if (r.paths.length) ctx.mapApi.highlight(r.paths, { style: "select", label: labelPt });
  else if (r.pt) ctx.mapApi.highlight([], { style: "select", label: labelPt, pt: r.pt });
  else ctx.mapApi.clearHighlight("select");
  // 고른 장소와 역 사이 점선 (점이 없는 유형과 역 자신은 긋지 않는다)
  const st = ctx.data.station;
  ctx.mapApi.setLine(r.pt && st && st.pt && r.path !== st.p && r.kind !== "group" ? st.pt : null, r.pt);
  if (sel.fit && ctx.map) {
    if (r.paths.length && ctx.mapApi.fitPlaces(r.paths)) { /* 옮김 */ }
    else if (r.pt) ctx.map.easeTo({ center: r.pt, zoom: Math.max(ctx.map.getZoom(), 17), duration: M.dur(400) });
  }
  const fromMap = sel.from === "map";
  panel.show({ id: "place", title: r.name, onBack: closePlace, focus: !fromMap && sel.from !== "link",
    render: el => (panel.placeRenderer || defaultPlace)(el, selected, ctx) });
  if (fromMap || sel.from === "link") ui.live(`${r.kindLabel} ${r.name} 정보를 열었습니다`);
  if (sheet.active() && !fromMap && sheet.current() === "peek") sheet.snap("half");
}

function onDeselect() {
  selected = null;
  ctx.mapApi.clearHighlight("select");
  ctx.mapApi.setLine(null);
  ctx.mapApi.setEntrances([]);
  panel.remove("place");
}

// 장소 화면 닫기: 주소창에서 focus를 지우는 것도 한 걸음으로 쌓는다 (뒤로 가기로 다시 열 수 있다)
function closePlace() {
  state.set({ focus: null, unit: null }, { push: true, silent: true });
  bus.emit("deselect");
}

function notFound() {
  ui.toast("이 장소를 찾지 못했습니다");
  ui.live("이 장소를 찾지 못했습니다. 처음 화면으로 갑니다");
  state.set({ focus: null, unit: null }, { silent: true });
  if (selected) bus.emit("deselect");
  else panel.remove("place");
}

// 주소창의 focus를 연다. places.json에 없는 유형(입구, 상호)은 카드를 받아 본다. 없거나 공개하지 않는 장소는 같은 말로 알린다
async function openFocus(focus, unit, { fit }) {
  const path = normPath(focus);
  const d = ctx.data;
  if (path !== focus) state.set({ focus: path }, { silent: true });   // 전체 URI로 온 옛 링크는 경로로 고쳐 적는다
  const place = d.byPath.get(path);
  // 필지 색인은 건물 행의 pnu로 만든 것이라, 필지 연결이 없는 건물의 번호 앞자리에서 온 값(그래프에 없는 필지)이 섞인다.
  // 그래서 주소창으로 온 필지는 카드를 받아 있는지 확인한다
  if (place && d.kindOf(path) === "parcel") {
    try {
      const card = await ctx.source.card(path);
      bus.emit("select", { path, card, from: "link", fit, noPush: true });
    } catch (e) { notFound(); }
    return;
  }
  if (place) {
    const g = unit === "group" && typeof place.i === "number" ? d.groupOf(place.i) : null;
    bus.emit("select", { path, group: g ? g.g : undefined, from: "link", fit, noPush: true });
    return;
  }
  if (placeCardKinds.test(path)) {
    try {
      const card = await ctx.source.card(path);
      if (card && (card.pt || card.name)) { bus.emit("select", { path, card, from: "link", fit, noPush: true }); return; }
    } catch (e) { /* 아래에서 알린다 */ }
  }
  notFound();
}

bus.on("select", onSelect);
bus.on("deselect", onDeselect);

// 뒤로 가기, 앞으로 가기
state.onChange((changed, s, source) => {
  if (source !== "pop" || !ctx.data) return;
  if (changed.includes("focus") || changed.includes("unit")) {
    if (s.focus) openFocus(s.focus, s.unit, { fit: true });
    else if (selected) bus.emit("deselect");
  }
  if (changed.includes("tab")) panel.selectTab(s.tab || "search");
});

// ---- 카드 모듈이 없을 때의 기본 표시 (이름과 주소)
function defaultPlace(el, s) {
  const p = (s.kind === "group" ? {} : s.place) || {}, c = s.card || {};
  const addr = p.a || c.addr || (s.kind === "station" ? p.a : "");
  const rows = [];
  if (addr) rows.push(`<p class="pl-addr">${esc(short(addr))}</p>`);
  if (p.j && s.kind !== "parcel") rows.push(`<p class="pl-sub">지번 ${esc(p.j)}</p>`);
  if (Number.isFinite(p.d) && p.d > 0) rows.push(`<p class="pl-sub">역에서 직선 ${nf(p.d)}m</p>`);
  if (s.group) {
    const g = s.group;
    rows.push(`<p class="pl-sub">건물 ${nf(g.m.length)}동${g.hh ? `, 세대수 ${nf(g.hh)}` : ""}${g.part && g.part.length ? " (일부 필지만 값이 있습니다)" : ""}</p>`);
  }
  if (s.kind === "road" && p.b != null) rows.push(`<p class="pl-sub">이 길을 주소로 쓰는 건물 ${nf(p.b)}동${p.side ? `, 곁길까지 ${nf(p.b + p.side)}동` : ""}</p>`);
  if (["admin-dong", "legal-dong", "basic-zone"].includes(s.kind) && p.b != null) rows.push(`<p class="pl-sub">속한 건물 ${nf(p.b)}동</p>`);
  if (s.kind === "parcel" && p.m) rows.push(`<p class="pl-sub">위에 선 건물 ${nf(p.m.length)}동</p>`);
  if (p.use) rows.push(`<p class="pl-sub">용도 ${esc(p.use)}</p>`);
  const status = p.s ? `<span class="chip type">${esc(STATUS[p.s] || p.s)}</span>` : "";
  const others = (s.others || []).filter(o => o.path !== s.path || o.group !== s.group);
  const page = cfg.mode === "public" && s.kind !== "group" && s.path ? `${cfg.root}/id/${s.path}/` : null;
  el.innerHTML = `<div class="pl">
    <p class="pl-kind"><span class="chip type">${esc(s.kindLabel)}</span>${status}</p>
    <h2 class="pl-name" tabindex="-1">${esc(s.name)}</h2>
    ${rows.join("")}
    ${others.length ? `<h3>여기 있는 다른 곳</h3><ul class="pl-others">${others.map(o => {
      const g = o.group != null ? ctx.data.groupById.get(Number(o.group)) : null;
      const name = g ? g.n : ctx.data.nameOf(o.path) || o.path;
      return `<li><button type="button" class="linkish" data-path="${esc(o.path)}" data-group="${g ? g.g : ""}">${esc(name)}</button></li>`;
    }).join("")}</ul>` : ""}
    ${page ? `<p class="pl-more"><a href="${esc(page)}">장소 페이지 열기</a></p>` : ""}
  </div>`;
  el.querySelectorAll(".pl-others button").forEach(b => b.addEventListener("click", () =>
    bus.emit("select", { path: b.dataset.path, group: b.dataset.group === "" ? undefined : Number(b.dataset.group), from: "list", fit: true })));
}

// ---- 홈 모듈이 없을 때의 기본 홈 (범위 요약과 건물 색)
function defaultHome(el) {
  const d = ctx.data;
  const legend = d.useGroups.map(g =>
    `<li class="legend-row"><span class="sw" style="background:var(${esc(g.color)});box-shadow:inset 0 0 0 1px var(--map-bldg-line)"></span>${esc(g.label)} <span class="muted">${nf(g.n)}동</span></li>`).join("");
  el.innerHTML = `<div class="home">
    <p class="home-sum">증미역 반경 1km, 건물 ${nf(d.count.buildings)}동, 장소 ${nf(d.count.places)}곳</p>
    <p class="muted small">건물을 누르면 이름과 주소가 나옵니다.</p>
    <h3>건물 색 (용도)</h3><ul class="legend">${legend}</ul>
    <p class="small"><a href="${siteBase}/id/building/">건물 목록</a>에서도 같은 장소를 볼 수 있습니다.</p>
  </div>`;
}

// ---- 기능 모듈 불러오기
async function loadFeatures() {
  const list = [...FEATURES.map(name => ({ name, url: `${cfg.app}/${name}.js${V}` })),
    ...(cfg.extra || []).map(u => ({ name: String(u).replace(/^.*\//, "").replace(/\.js$/, ""), url: u + V }))];
  const got = await Promise.allSettled(list.map(x => import(x.url)));
  const mods = [];
  got.forEach((r, k) => {
    if (r.status !== "fulfilled") { console.warn(`기능 파일을 건너뜁니다: ${list[k].name}`); return; }
    const m = r.value;
    const f = typeof m.init === "function" ? m : (m.default && typeof m.default.init === "function" ? m.default : null);
    if (!f || m.off || f.off) return;
    mods.push([list[k].name, f]);
  });
  ctx.features = new Set(mods.map(x => x[0]));
  for (const [name, f] of mods) {
    try { await f.init(ctx); }
    catch (e) { console.error(`${name} 기능을 켜지 못했습니다`, e); ctx.features.delete(name); }
  }
}

// 찾기 모듈이 없으면 찾기창이 아무 일도 안 하는 것처럼 보이지 않게 알린다
function searchFallback() {
  if (ctx.features.has("search")) return;
  document.getElementById("q").addEventListener("keydown", e => {
    if (e.key === "Enter") ui.toast("찾기 기능을 불러오지 못했습니다. 메뉴의 건물 목록에서 찾을 수 있습니다.");
  });
}

// 지도 아래 기준 표기 (출처 기준일)
async function basisControl() {
  if (!ctx.map) return;
  try {
    const s = await ctx.source.sources();
    if (!s || !s.basis) return;
    ctx.map.addControl({
      onAdd() { const div = document.createElement("div"); div.className = "maplibregl-ctrl map-basis"; div.textContent = s.basis; this._el = div; return div; },
      onRemove() { this._el.remove(); },
    }, "bottom-right");
  } catch (e) { /* 표기만 빠진다 */ }
}

// 키보드: '/'는 찾기창으로, Esc는 메뉴를 닫는다
document.addEventListener("keydown", e => {
  if (e.key === "/" && !e.ctrlKey && !e.metaKey && !e.altKey && !/^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName) && !e.target.isContentEditable) {
    e.preventDefault();
    document.getElementById("q").focus();
  }
  if (e.key === "Escape" && !menu.hidden) { openMenu(false); menuBtn.focus(); }
});

// ---- 시작
async function start() {
  ctx.source = (await import(cfg.source + V)).default;
  const [{ data, area }] = await Promise.all([loadData(), new Promise(r => (ctx.map ? whenStyleReady(ctx.map, r) : r()))]);
  ctx.data = data;
  if (mapReady) mapReady.addLayers(data, area);
  await loadFeatures();
  // 홈을 넣는 기능 모듈이 없으면 기본 홈을 쓴다. 장소 표시도 카드 모듈이 없으면 defaultPlace를 쓴다
  if (!panel.hasHome()) panel.setHome(defaultHome);
  searchFallback();
  basisControl();
  const s = state.get();
  if (s.tab) panel.selectTab(s.tab);
  // 첫 화면: 주소창 위치가 없으면 PC는 범위 원 전체를 패널 오른쪽에, 휴대폰은 역 중심 배율 15
  if (ctx.map && !urlView && !s.focus) {
    const ring = area && area.features && area.features[0] && area.features[0].geometry.coordinates[0];
    if (ring && !sheet.active()) {
      const b = new maplibregl.LngLatBounds(ring[0], ring[0]);
      ring.forEach(c => b.extend(c));
      ctx.map.fitBounds(b, { padding: 16, duration: 0 });
    } else if (data.station && data.station.pt) ctx.map.jumpTo({ center: data.station.pt, zoom: 15 });
  }
  if (s.focus) await openFocus(s.focus, s.unit, { fit: !urlView });
  document.body.classList.add("app-ready");
  bus.emit("app:ready", {});
}

start().catch(e => { console.error(e); ui.toast("화면을 여는 중에 문제가 생겼습니다. 새로 고쳐 주세요."); });
