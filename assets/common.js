// 검토 화면과 지도 화면이 같이 쓰는 도구
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

// 지도 배경: OpenStreetMap 타일을 회색으로 바래게 깔고(다크 모드면 밝기를 뒤집어 어둡게) 그래프 데이터를 그 위에 그린다
// 배경이 옅어야 건물 상태 색과 질문 답이 묻히지 않는다
const darkMode = () => matchMedia("(prefers-color-scheme: dark)").matches;
function baseStyle() {
  const dark = darkMode();
  return {
    version: 8, sources: {
      osm: { type: "raster", tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"], tileSize: 256, maxzoom: 19,
             attribution: "© OpenStreetMap contributors" },
    },
    layers: [
      { id: "bg", type: "background", paint: { "background-color": dark ? "#0e1116" : "#f2f3f5" } },
      // 밝기 최소값이 최대값보다 크면 밝기가 뒤집힌다 (다크 모드)
      { id: "osm", type: "raster", source: "osm", paint: dark
          ? { "raster-saturation": -1, "raster-brightness-min": 0.82, "raster-brightness-max": 0.06, "raster-contrast": -0.15 }
          : { "raster-saturation": -1, "raster-brightness-min": 0.42, "raster-brightness-max": 1, "raster-contrast": -0.25 } },
    ],
  };
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

function boundsOf(features) {
  const b = new maplibregl.LngLatBounds();
  const walk = c => (typeof c[0] === "number" ? b.extend(c) : c.forEach(walk));
  features.forEach(f => walk(f.geometry.coordinates));
  return b;
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
