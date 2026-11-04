// 로컬 서버의 자료 입구 (명세 5.3절). 공개판 source-static.js와 같은 함수를 같은 모양으로 주고, 로컬만의 함수를 더한다.
// 자료는 src/review/app.py의 /api/... 가 전체 뷰로 만든다. 공개 빌드에는 이 폴더(web/local/)를 복사하지 않는다.
// 받은 결과는 기억해 두고, 실패하면 기억을 지워 다시 시도할 수 있게 한다.

const memo = new Map();
function once(key, url) {
  if (!memo.has(key)) {
    const p = fetchJSON(url).catch(e => { memo.delete(key); throw e; });
    memo.set(key, p);
  }
  return memo.get(key);
}

// 키 값은 받지 않고 있음, 없음만 받는다. 받지 못하면 로컬 기능을 끈 채로 연다
let caps = { ask: false, key: false, layers: [], history: false, review: false, route: false };
try { caps = { ...caps, ...(await fetchJSON("/api/capabilities")) }; }
catch (e) { console.warn("로컬 기능 목록을 받지 못했습니다:", e && e.message); }

export default {
  caps,
  places: () => once("places", "/api/places"),
  shapes: () => once("shapes", "/api/shapes"),
  entrances: () => once("entrances", "/api/entrances"),
  area: () => once("area", "/api/area"),
  questions: () => once("questions", "/api/questions"),
  sources: () => once("sources", "/api/sources"),
  businesses: () => once("businesses", "/api/businesses"),
  card: path => {
    const clean = String(path || "").replace(/^\/+|\/+$/g, "");
    if (!/^[a-z-]+\/[0-9A-Za-z_.-]+$/.test(clean)) return Promise.reject(Object.assign(new Error("장소 경로가 맞지 않습니다"), { status: 400 }));
    return once("card:" + clean, "/api/card?path=" + encodeURIComponent(clean));
  },
  // ---- 로컬만
  ask: text => fetchJSON("/api/ask", { json: { question: String(text || "") } }),
  layer: name => once("layer:" + name, "/api/layers/" + encodeURIComponent(name)),
  changes: (since, until) => fetchJSON("/api/editions/changes" + (since || until
    ? "?" + new URLSearchParams({ ...(since ? { since } : {}), ...(until ? { until } : {}) }) : "")),
  asOf: (uri, at) => fetchJSON(`/api/editions/as-of?uri=${encodeURIComponent(uri)}&at=${encodeURIComponent(at)}`),
  feature: uri => fetchJSON("/api/feature?uri=" + encodeURIComponent(uri)),
  route: () => Promise.reject(new Error("길찾기는 이 화면에 없습니다")),
};
