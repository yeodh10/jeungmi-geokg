// 주소창 상태 (명세 3.1절). 공개와 로컬이 같은 값을 쓴다.
//   focus 고른 장소 경로, unit 단지 카드(group), q 찾기말, cat 분류, tab 탭(ask, data), theme 주제 보기, z 배율, c 경도,위도
// 쌓는 값(set의 push: true)은 pushState, 덮어쓰는 값은 replaceState로 적는다. 뒤로 가기는 popstate로 받아 알린다.
// URLSearchParams.toString()은 '/'를 %2F로 바꾸므로 값을 직접 잇는다.

const KEYS = ["focus", "unit", "q", "cat", "tab", "theme", "z", "c"];
const enc = v => encodeURIComponent(String(v)).replace(/%2F/gi, "/").replace(/%2C/gi, ",").replace(/%3A/gi, ":");

function read() {
  const sp = new URLSearchParams(location.search);
  const s = {};
  for (const k of KEYS) {
    const v = sp.get(k);
    if (v !== null && v !== "") s[k] = v;
  }
  // 옛 지도 링크 /map/?u=building/... 는 focus로 읽는다
  if (!s.focus && sp.get("u")) s.focus = sp.get("u");
  // 꼴이 맞지 않는 값은 읽지 않는다 (주소창에도 남기지 않는다). 있는 칩과 탭인지는 칩, 패널 모듈이 다시 본다
  if (s.unit && s.unit !== "group") delete s.unit;
  if (s.cat && !/^[a-z0-9][a-z0-9_-]{0,39}$/.test(s.cat)) delete s.cat;
  if (s.tab && !/^[a-z][a-z0-9-]{0,23}$/.test(s.tab)) delete s.tab;
  return s;
}

function urlOf(s) {
  const parts = [];
  for (const k of KEYS) if (s[k] != null && s[k] !== "") parts.push(k + "=" + enc(s[k]));
  return location.pathname + (parts.length ? "?" + parts.join("&") : "") + location.hash;
}

// 쌓은 깊이를 history.state에 둔다. 0이면 이 화면이 처음 연 항목이다
const depth = () => (history.state && Number.isFinite(history.state.jgk) ? history.state.jgk : 0);

export function createState() {
  let cur = read();
  const fns = [];
  const notify = (changed, source) => {
    if (!changed.length) return;
    for (const fn of fns.slice()) {
      try { fn(changed, { ...cur }, source); } catch (e) { console.error(e); }
    }
  };
  // 옛 u= 값을 focus로 바꾸어 적고, 쌓은 깊이를 기록한다
  try { history.replaceState({ ...(history.state || {}), jgk: depth() }, "", urlOf(cur)); } catch (e) { /* 주소창을 못 바꿔도 화면은 쓴다 */ }

  addEventListener("popstate", () => {
    const next = read();
    const changed = KEYS.filter(k => (cur[k] ?? null) !== (next[k] ?? null));
    cur = next;
    notify(changed, "pop");
  });

  return {
    get: key => (key ? cur[key] ?? null : { ...cur }),
    // set({key: value}, {push, silent}). 값이 null이나 빈 문자열이면 지운다. 바뀐 키가 없으면 아무것도 하지 않는다
    set(values, { push = false, silent = false } = {}) {
      const next = { ...cur };
      for (const [k, v] of Object.entries(values)) {
        if (!KEYS.includes(k)) continue;
        if (v == null || v === "") delete next[k];
        else next[k] = String(v);
      }
      const changed = KEYS.filter(k => (cur[k] ?? null) !== (next[k] ?? null));
      if (!changed.length) return false;
      cur = next;
      try {
        if (push) history.pushState({ jgk: depth() + 1 }, "", urlOf(cur));
        else history.replaceState({ ...(history.state || {}), jgk: depth() }, "", urlOf(cur));
      } catch (e) { /* Safari는 replaceState가 너무 잦으면 예외를 낸다. 값은 메모리에 남는다 */ }
      if (!silent) notify(changed, "set");
      return true;
    },
    // fn(바뀐 키 목록, 지금 상태, "set" | "pop")
    onChange(fn) { fns.push(fn); return () => { const i = fns.indexOf(fn); if (i >= 0) fns.splice(i, 1); }; },
    canBack: () => depth() > 0,
    url: (values = {}) => {
      const s = { ...cur };
      for (const [k, v] of Object.entries(values)) { if (v == null || v === "") delete s[k]; else s[k] = String(v); }
      return location.origin + urlOf(s);
    },
  };
}

// 경로를 고른다: 전체 URI(로컬 옛 링크), /id/ 주소, 앞뒤 '/'를 모두 받아 'building/1150...' 꼴로 만든다
export function normPath(value) {
  let v = String(value || "").trim();
  const m = v.match(/\/id\/(.+)$/);
  if (m) v = m[1];
  return v.replace(/[?#].*$/, "").replace(/^\/+|\/+$/g, "");
}
