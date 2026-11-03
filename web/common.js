// 검토 화면, 지도 앱, 공개 장소 페이지가 같이 쓰는 도구 (고전 스크립트, defer 없이 맨 앞에서 읽는다)

// 화면 밝기: 고른 값(localStorage jgk-theme: light, dark)을 첫 그림 전에 넣는다. 없으면 시스템을 따른다.
// 저장소가 막혀 있어도 오류를 내지 않는다 (기억만 못 한다)
const THEME_KEY = "jgk-theme";
(() => {
  try {
    const t = localStorage.getItem(THEME_KEY);
    if (t === "light" || t === "dark") document.documentElement.dataset.theme = t;
  } catch (e) { /* 저장소 막힘 */ }
})();

// 이 스크립트 주소에서 vendor 경로를 얻는다. 공개는 /jeungmi-geokg/assets/vendor/, 로컬은 /static/vendor/.
// 스크립트 주소를 알 수 없으면(모듈로 읽거나 시험 환경) 로컬 경로를 쓴다
const VENDOR = (() => {
  try { return location.origin + new URL("vendor/", document.currentScript.src).pathname; }
  catch (e) { return "/static/vendor/"; }
})();

const BASE = "https://yeodh10.github.io/jeungmi-geokg/id/";
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const css = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();

// 표준 어휘(어휘 파일 밖) 속성과 값의 한국어 이름
const EXT = {
  "22-rdf-syntax-ns#type": "종류", "rdf-schema#label": "이름", "terms/source": "출처", "terms/description": "설명",
  "terms/created": "만든 시각", "geosparql#hasGeometry": "모양", "geosparql#hasDefaultGeometry": "기본 모양",
  "geosparql#asWKT": "WKT", "owl#sameAs": "같은 객체", "prov#wasInfluencedBy": "반영된 변경 제안",
  "geosparql#Feature": "공간 객체", "geosparql#Geometry": "모양", "prov#Entity": "기록",
};
const ext = u => { for (const k in EXT) if (String(u).endsWith(k)) return EXT[k]; return null; };
const localName = u => String(u).replace(/^.*[#/]/, "");
const ACTION = { create: "신규", update: "변경", retire: "폐지", link: "주소 연결" };
const SOURCE = { "rule-candidate": "규칙 후보", "data-diff": "자료 비교", "llm-notice": "LLM 고시 추출", "notice-table": "고시 첨부표" };
const STATUS = { pending: "대기", approved: "승인", rejected: "반려" };

// 화면 밝기: darkMode()는 지금 어두운 화면인지 (고른 값이 먼저, 없으면 시스템). themeChoice()는 "light", "dark", "system"
const themeChoice = () => document.documentElement.dataset.theme || "system";
const darkMode = () => {
  const t = document.documentElement.dataset.theme;
  return t ? t === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
};
// 화면 밝기를 바꾸고 기억한다. 바뀌면 "jgk-theme" 이벤트를 보낸다 (지도 색을 바꾸는 곳이 받는다)
function setTheme(choice) {
  const root = document.documentElement;
  const fixed = choice === "light" || choice === "dark";
  if (fixed) root.dataset.theme = choice;
  else delete root.dataset.theme;
  try {
    if (fixed) localStorage.setItem(THEME_KEY, choice);
    else localStorage.removeItem(THEME_KEY);
  } catch (e) { /* 저장소 막힘: 이번 화면에만 적용 */ }
  dispatchEvent(new CustomEvent("jgk-theme", { detail: { choice: fixed ? choice : "system", dark: darkMode() } }));
}
// 밝기가 바뀌면 fn(어두운지)을 부른다. 고른 값이 없을 때는 시스템 설정이 바뀌어도 부른다
function onThemeChange(fn) {
  addEventListener("jgk-theme", () => fn(darkMode()));
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => { if (!document.documentElement.dataset.theme) fn(darkMode()); });
}

// 배경 범위: 증미역 중심에서 동서남북 2.5km 사각형 [서, 남, 동, 북]. src/graph/basetiles.py의 bounds()와 같다
const MAP_BOUNDS = [126.832902, 37.535157, 126.889575, 37.580275];
const MAP_CENTER = [126.86125, 37.55772];
// 지도 글자: 한글은 기기 글꼴로 그리고, 라틴 글자와 기호는 구운 글꼴(MapMedium)을 쓴다
const MAP_FONT = "'Apple SD Gothic Neo','Malgun Gothic','Noto Sans KR',sans-serif";
// MapLibre 기본 영어 문구를 한국어로 (키는 maplibre-gl 4.7.1의 defaultLocale에서 확인)
const MAP_LOCALE = {
  "AttributionControl.ToggleAttribution": "지도 자료 표기 펴고 접기", "AttributionControl.MapFeedback": "지도 의견 보내기",
  "FullscreenControl.Enter": "전체 화면", "FullscreenControl.Exit": "전체 화면 끝내기",
  "GeolocateControl.FindMyLocation": "내 위치 찾기", "GeolocateControl.LocationNotAvailable": "위치를 알 수 없습니다",
  "LogoControl.Title": "MapLibre 로고", "Map.Title": "지도", "Marker.Title": "지도 표식",
  "NavigationControl.ResetBearing": "북쪽이 위로 오게", "NavigationControl.ZoomIn": "확대", "NavigationControl.ZoomOut": "축소",
  "Popup.Close": "말풍선 닫기", "ScaleControl.Feet": "ft", "ScaleControl.Meters": "m", "ScaleControl.Kilometers": "km",
  "ScaleControl.Miles": "mi", "ScaleControl.NauticalMiles": "nm",
  "TerrainControl.Enable": "지형 켜기", "TerrainControl.Disable": "지형 끄기",
  "CooperativeGesturesHandler.WindowsHelpText": "Ctrl 키를 누른 채 휠을 굴리면 지도가 커지고 작아집니다",
  "CooperativeGesturesHandler.MacHelpText": "⌘ 키를 누른 채 휠을 굴리면 지도가 커지고 작아집니다",
  "CooperativeGesturesHandler.MobileHelpText": "두 손가락으로 지도를 움직입니다",
};
const MAP_ATTRIBUTION = '<a href="https://openfreemap.org" target="_blank" rel="noopener">OpenFreeMap</a> '
  + '<a href="https://www.openmaptiles.org/" target="_blank" rel="noopener">© OpenMapTiles</a> '
  + '<a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">© OpenStreetMap 기여자</a>';
// new maplibregl.Map에 같이 넘길 값: 범위, 배율, 한국어 문구, 한글 글꼴
const mapOptions = (extra = {}) => ({ minZoom: 13, maxZoom: 19, maxBounds: MAP_BOUNDS, locale: MAP_LOCALE,
  localIdeographFontFamily: MAP_FONT, ...extra });

// 지도 배경: 저장소에 구운 벡터 타일(vendor/base, z13~14)로 도로, 물, 공원, 철도, 지명, 도로 이름만 그린다.
// 타일에 건물, 상호(poi), 번지(housenumber) 층은 없다. 색은 style.css의 --map-* 토큰이라 화면 밝기를 따른다.
// 우리 층(건물 등)을 글자 아래에 넣으려면 beforeId로 "base-road-label"을 준다
function baseStyle() {
  const name = ["coalesce", ["get", "name:ko"], ["get", "name"]];
  const road = (id, cls, color, w) => ({ id, type: "line", source: "base", "source-layer": "transportation",
    filter: ["in", ["get", "class"], ["literal", cls]], layout: { "line-cap": "round", "line-join": "round" },
    paint: { "line-color": css(color), "line-width": ["interpolate", ["exponential", 1.5], ["zoom"], 12, w[0], 18, w[1]] } });
  const label = (id, layer, color, size, more = {}) => ({ id, type: "symbol", source: "base", "source-layer": layer, ...more,
    layout: { "text-field": name, "text-font": ["MapMedium"], "text-size": size, ...(more.layout || {}) },
    paint: { "text-color": css(color), "text-halo-color": css("--map-halo"), "text-halo-width": 1.5 } });
  return {
    version: 8, glyphs: VENDOR + "glyphs/{fontstack}/{range}.pbf",
    sources: { base: { type: "vector", tiles: [VENDOR + "base/{z}/{x}/{y}.pbf"], minzoom: 13, maxzoom: 14, bounds: MAP_BOUNDS,
      attribution: MAP_ATTRIBUTION } },
    layers: [
      { id: "base-bg", type: "background", paint: { "background-color": css("--map-land") } },
      { id: "base-landuse", type: "fill", source: "base", "source-layer": "landuse",
        filter: ["in", ["get", "class"], ["literal", ["residential", "suburb", "neighbourhood"]]], paint: { "fill-color": css("--map-residential") } },
      { id: "base-landcover", type: "fill", source: "base", "source-layer": "landcover",
        filter: ["in", ["get", "class"], ["literal", ["grass", "wood"]]], paint: { "fill-color": css("--map-wood"), "fill-opacity": 0.8 } },
      { id: "base-park", type: "fill", source: "base", "source-layer": "park", paint: { "fill-color": css("--map-park") } },
      { id: "base-water", type: "fill", source: "base", "source-layer": "water", paint: { "fill-color": css("--map-water") } },
      { id: "base-waterway", type: "line", source: "base", "source-layer": "waterway", paint: { "line-color": css("--map-water"), "line-width": 2 } },
      road("base-minor-case", ["minor", "service", "track", "path"], "--map-road-line", [0.6, 11]),
      road("base-road2-case", ["tertiary", "secondary"], "--map-road-line", [2, 19]),
      road("base-road1-case", ["primary", "trunk"], "--map-road-1-line", [3, 27]),
      road("base-motorway-case", ["motorway"], "--map-motorway-line", [3.5, 31]),
      road("base-minor", ["minor", "service", "track"], "--map-road", [0.3, 8.5]),
      road("base-road2", ["tertiary", "secondary"], "--map-road-2", [1.3, 16]),
      road("base-road1", ["primary", "trunk"], "--map-road-1", [2, 23]),
      road("base-motorway", ["motorway"], "--map-motorway", [2.5, 27]),
      { ...road("base-rail", ["rail", "transit"], "--map-rail", [0.7, 2.4]),
        paint: { "line-color": css("--map-rail"), "line-width": 1.6, "line-dasharray": [3, 2] } },
      label("base-road-label", "transportation_name", "--map-text-2", 12, { minzoom: 14, layout: { "symbol-placement": "line" } }),
      label("base-water-label", "water_name", "--map-water-text", 13),
      label("base-place-label", "place", "--map-text", 13),
    ],
  };
}
// 화면 밝기가 바뀌면 배경 층의 색만 바꾼다 (우리 층은 지우지 않는다)
function restyleBase(map) {
  for (const layer of baseStyle().layers) {
    if (!map.getLayer(layer.id)) continue;
    for (const [k, v] of Object.entries(layer.paint || {})) map.setPaintProperty(layer.id, k, v);
  }
}

// 위 막대의 로고 (지도 핀 안에 그래프 점 셋)
const BRAND_MARK = `<svg class="mark" viewBox="0 0 32 32" aria-hidden="true"><rect width="32" height="32" rx="8" fill="var(--brand)"/>
<path d="M10 21 L16 11 L22 19" fill="none" stroke="var(--panel)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" opacity=".85"/>
<circle cx="10" cy="21" r="3" fill="var(--panel)"/><circle cx="16" cy="11" r="3" fill="var(--panel)"/><circle cx="22" cy="19" r="3" fill="#f6a54b"/></svg>`;
document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll(".brand").forEach(b => b.insertAdjacentHTML("afterbegin", BRAND_MARK));
});

// 'load'와 isStyleLoaded()는 배경지도 타일을 다 받을 때까지 기다려서, 타일 서버가 느리면 우리 데이터가 한참 안 보인다.
// 스타일(레이어 목록)만 준비되면 바로 우리 레이어를 얹는다 ('style.load'는 타일을 기다리지 않는다)
function whenStyleReady(map, cb) {
  if (map.style && map.style._loaded) { cb(); return; }
  let done = false;
  const once = () => { if (!done) { done = true; cb(); } };
  map.once("style.load", once);
  // 이벤트를 놓친 경우를 위한 확인 (스타일이 이미 준비됐는데 이벤트가 지나갔을 때)
  const poll = () => { if (done) return; if (map.style && map.style._loaded) once(); else setTimeout(poll, 50); };
  poll();
}

// 지도에 놓을 수 있는 [경도, 위도]인지. 범위 밖 값은 maplibre가 예외를 던져 화면 스크립트가 멈춘다
const okLngLat = c => Array.isArray(c) && Number.isFinite(c[0]) && Number.isFinite(c[1]) && Math.abs(c[0]) <= 180 && Math.abs(c[1]) <= 90;

// 지도에 놓을 수 있는 좌표만 모은다. GeometryCollection, 모양 없는 객체, 범위 밖 좌표는 건너뛴다
function boundsOf(features) {
  const b = new maplibregl.LngLatBounds();
  const walk = c => {
    if (!Array.isArray(c)) return;
    if (typeof c[0] === "number") { if (okLngLat(c)) b.extend(c); }
    else c.forEach(walk);
  };
  const visit = g => {
    if (!g) return;
    if (g.type === "GeometryCollection") (g.geometries || []).forEach(visit);
    else walk(g.coordinates);
  };
  features.forEach(f => visit(f && f.geometry));
  return b;
}

// 객체들이 보이게 지도를 옮긴다. 놓을 좌표가 하나도 없으면 그대로 둔다 (빈 범위로 옮기면 예외가 난다)
function fitTo(map, features, options) {
  const b = boundsOf(features);
  if (!b.isEmpty()) map.fitBounds(b, options);
}

// 주소창의 z, c를 지도의 처음 위치로 읽는다. 지도가 받을 수 없는 값이면 null
function viewFrom(z, c) {
  const zoom = parseFloat(z), center = String(c ?? "").split(",").map(s => s.trim()).filter(Boolean).map(Number);
  return Number.isFinite(zoom) && zoom >= 0 && zoom <= 24 && center.length === 2 && okLngLat(center) ? { zoom, center } : null;
}

// 키로 묶는다. 프로토타입이 없는 객체라 키가 constructor나 __proto__여도 된다
function groupBy(items, key) {
  const out = Object.create(null);
  for (const x of items) (out[key(x)] ||= []).push(x);
  return out;
}

// API를 부르고 JSON을 받는다. 실패하면 화면에 그대로 보일 한국어 문장을 담은 Error를 던진다
//   opts.json이 있으면 POST로 JSON 본문을 보낸다. 나머지 opts(signal 등)는 fetch에 그대로 넘긴다
//   err.status는 HTTP 상태 코드 (서버에 닿지 못하면 0). 요청을 끊으면 AbortError를 그대로 던진다
async function fetchJSON(url, opts = {}) {
  const { json, ...init } = opts;
  if (json !== undefined) {
    init.method = init.method || "POST";
    init.headers = { "Content-Type": "application/json", ...(init.headers || {}) };
    init.body = JSON.stringify(json);
  }
  const fail = (status, message) => Object.assign(new Error(message), { status });
  let r;
  try { r = await fetch(url, init); }
  catch (e) {
    if (e && e.name === "AbortError") throw e;
    throw fail(0, "서버에 연결하지 못했습니다");
  }
  let body = null, parsed = true;
  try { body = await r.json(); }
  catch (e) {
    if (e && e.name === "AbortError") throw e;
    parsed = false;
  }
  if (r.ok && parsed) return body;
  const d = body && body.detail;
  throw fail(r.status, typeof d === "string" ? d : Array.isArray(d) ? "요청 형식이 맞지 않습니다" : `서버 오류 (상태 코드 ${r.status})`);
}

async function pendingCount() {
  try {
    const n = (await (await fetch("/api/proposals?status=pending")).json()).length;
    const el = document.getElementById("pending-count");
    if (el) { el.textContent = n; el.style.display = n ? "inline-block" : "none"; }
  } catch (e) { /* 표시만 못 할 뿐 */ }
}
