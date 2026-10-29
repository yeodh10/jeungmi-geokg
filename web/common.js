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

// 지도 배경: OpenStreetMap 타일을 옅게 깔고 그래프 데이터를 그 위에 그린다
function baseStyle() {
  return {
    version: 8, sources: {
      osm: { type: "raster", tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"], tileSize: 256, maxzoom: 19,
             attribution: "© OpenStreetMap contributors" },
    },
    layers: [
      { id: "bg", type: "background", paint: { "background-color": "#eef1f4" } },
      { id: "osm", type: "raster", source: "osm", paint: { "raster-saturation": -0.6, "raster-opacity": 0.9 } },
    ],
  };
}

// 'load'는 배경지도 타일을 다 받을 때까지 기다려서 늦다. 스타일만 준비되면 바로 우리 레이어를 얹는다
function whenStyleReady(map, cb) {
  if (map.isStyleLoaded()) cb();
  else setTimeout(() => whenStyleReady(map, cb), 50);
}

function boundsOf(features) {
  const b = new maplibregl.LngLatBounds();
  const walk = c => (typeof c[0] === "number" ? b.extend(c) : c.forEach(walk));
  features.forEach(f => walk(f.geometry.coordinates));
  return b;
}

async function pendingCount() {
  try {
    const n = (await (await fetch("/api/proposals?status=pending")).json()).length;
    const el = document.getElementById("pending-count");
    if (el) { el.textContent = n; el.style.display = n ? "inline-block" : "none"; }
  } catch (e) { /* 표시만 못 할 뿐 */ }
}
