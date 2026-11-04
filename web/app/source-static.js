// 공개 사이트의 자료 입구 (명세 5.3절). 빌드 때 만든 정적 파일을 받는다.
// 앱 자료와 카드는 ?v=빌드 값을 붙여 받는다. 재배포 직후 옛 파일과 새 파일이 섞이면 장소 번호(i)가 어긋나기 때문이다.
// 받은 결과는 기억해 두고, 실패하면 기억을 지워 다시 시도할 수 있게 한다.

const cfg = window.APP_CONFIG || {};
const V = "?v=" + encodeURIComponent(cfg.build || "");
const DATA = (cfg.root || "") + "/assets/data/";
const memo = new Map();

function once(key, url) {
  if (!memo.has(key)) {
    const p = fetchJSON(url).catch(e => { memo.delete(key); throw e; });
    memo.set(key, p);
  }
  return memo.get(key);
}

export default {
  caps: { ask: false, layers: [], history: false, review: false, route: false },
  places: () => once("places", DATA + "places.json" + V),
  shapes: () => once("shapes", DATA + "shapes.geojson" + V),
  entrances: () => once("entrances", DATA + "entrances.json" + V),
  area: () => once("area", DATA + "area.geojson" + V),
  questions: () => once("questions", DATA + "questions.json" + V),
  sources: () => once("sources", DATA + "sources.json" + V),
  // 키 없는 질문의 틀과 미리 실행한 결과. 묻기 탭 입력칸을 처음 쓸 때 받는다 (첫 화면 자료에 넣지 않는다)
  askForms: () => once("ask", DATA + "ask.json" + V),
  // 상호는 첫 화면 자료에 넣지 않고 찾기창에 처음 입력할 때 받는다 (명세 0-1절)
  businesses: () => once("businesses", DATA + "businesses.json" + V),
  // 장소 하나의 카드. 경로는 'building/1150...' 꼴이다. 공개하지 않거나 없는 장소는 404라 실패로 끝난다
  card: path => {
    const clean = String(path || "").replace(/^\/+|\/+$/g, "");
    if (!/^[a-z-]+\/[0-9A-Za-z_.-]+$/.test(clean)) return Promise.reject(Object.assign(new Error("장소 경로가 맞지 않습니다"), { status: 400 }));
    return once("card:" + clean, `${cfg.root || ""}/id/${clean}/card.json${V}`);
  },
};
