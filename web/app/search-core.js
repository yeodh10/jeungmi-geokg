// 찾기의 화면 없는 부분 (명세 3.5절, 9절): 정규화, 점수, 묶기, 칩과 조건 고르기.
// 브라우저(search.js, chips.js, conditions.js)와 node 시험(tests/search_check.mjs)이 같은 파일을 쓴다. DOM을 쓰지 않는다.
// 낱말(줄임말, 없는 자료 낱말, 범위 밖 낱말 등)은 이 파일에 적지 않고 places.json의 words(config/search_words.yaml)에서만 받는다.

const SEOUL = /^(서울특별시|서울시|서울)$/;
const MIN = 45;   // 이 점수보다 낮은 후보는 버린다

// ---- 글자 다루기
// 견줄 꼴: 소문자, 공백과 괄호를 뺀다. 숫자, '-', ','는 남긴다 (등촌주공7,8단지, 666-40)
export const compact = s => String(s || "").normalize("NFC").toLowerCase().replace(/[\s()[\]{}"'\u00b7.]/g, "");
const tokensOf = s => String(s || "").normalize("NFC").trim().split(/\s+/).filter(Boolean);
const hasDigit = s => /\d/.test(s);
const onlyNum = s => /^[\d-]+$/.test(s);

// 주소 꼴: '서울특별시'와 빼는 낱말('지하', '번지')을 지운 뒤 붙인다. 구 이름을 뺀 꼴도 만든다
function addrForms(a, drop) {
  const t = tokensOf(a).filter(x => !SEOUL.test(x) && !drop.has(x));
  const full = compact(t.join(""));
  const noGu = compact(t.filter(x => !/구$/.test(x)).join(""));
  return [full, noGu];
}

// hay가 needle로 끝나고, 앞 글자가 숫자에 붙어 있지 않을 때만 참 ('1666-40'이 '666-40'으로 잡히지 않게)
function endsAt(hay, needle) {
  if (!hay || !needle || !hay.endsWith(needle)) return false;
  if (hay.length === needle.length) return true;
  return !(/\d/.test(needle[0]) && /\d/.test(hay[hay.length - needle.length - 1]));
}

// 거리 (m). 1km 안이라 평면 근사로 충분하다 (src/graph/places.py와 같은 식)
export function meters(a, b) {
  if (!a || !b) return Infinity;
  const k = Math.cos(((a[1] + b[1]) / 2) * Math.PI / 180);
  const dx = (a[0] - b[0]) * 111320 * k, dy = (a[1] - b[1]) * 110540;
  return Math.round(Math.hypot(dx, dy));
}

// ---- 색인
// places: places.json. 상호는 처음 찾을 때 받아 addBusinesses로 더한다 (첫 화면 크기 기준)
export function createIndex(places) {
  const w = places.words || {};
  const words = {
    aliases: w.aliases || {}, suffix: w.suffix || {}, drop: new Set(w.drop || []),
    absent: (w.absent || []).map(compact), partial: (w.partial || []).map(compact), outside: (w.outside || []).map(compact),
    notes: w.notes || {},
  };
  const station = places.station || null;
  const st = station && station.pt;
  const docs = [];
  const byI = [];
  for (const p of places.places) byI[p.i] = p;
  const groupById = new Map((places.groups || []).map(g => [g.g, g]));
  // 동네 이름 앞말 (가양, 등촌 ...). '가양이마트'처럼 동네 이름 뒤에 붙은 이름을 앞에서 맞은 것으로 본다
  const areas = new Set();
  for (const n of Object.values(places.legalDong || {})) areas.add(compact(n).replace(/동$/, ""));
  for (const x of places.dongs || []) areas.add(compact(x.n).replace(/제?\d*동$/, ""));
  areas.delete("");

  const nameDoc = (o, name) => {
    o.name = name;
    o.nc = compact(name);
    o.toks = tokensOf(name).map(compact);
    return o;
  };
  const withAddr = (o, a) => { if (a) { o.addr = a; [o.ac, o.ac2] = addrForms(a, words.drop); } return o; };

  // 장소 (건물 행). 묶음에 든 건물은 이름으로 맞으면 묶음 한 줄로 합친다
  for (const p of places.places) {
    const o = nameDoc({ type: "place", path: p.p, i: p.i, g: p.g ?? null, pt: p.pt, d: p.d, bonus: p.nf === 0 ? 0 : -6, num: p.p.split("/")[1] }, p.n);
    withAddr(o, p.a);
    if (p.j) { o.j = p.j; o.jc = compact(p.j); }
    o.use = p.use || "";
    docs.push(o);
  }
  for (const g of places.groups || []) {
    const o = nameDoc({ type: "group", g: g.g, path: byI[g.m[0]] && byI[g.m[0]].p, pt: g.pt, d: meters(g.pt, st), bonus: 1, size: g.m.length }, g.n);
    const rep = byI[g.m[0]];
    if (rep && rep.a) withAddr(o, rep.a);
    docs.push(o);
  }
  if (station) {
    const o = nameDoc({ type: "station", path: station.p, pt: station.pt, d: 0, bonus: 8 }, station.n);
    withAddr(o, station.a);
    docs.push(o);
  }
  // 범위 안의 옆 역 (가양역, 등촌역). d는 중심 역에서 잰 직선 거리
  for (const s of places.near_stations || []) docs.push(withAddr(nameDoc({ type: "station", path: s.p, pt: s.pt, d: s.d ?? meters(s.pt, st), bonus: 7 }, s.n), s.a));
  for (const r of places.roads || []) docs.push(nameDoc({ type: "road", path: r.p, b: r.b || 0, bonus: 5 + Math.min(1, (r.b || 0) / 1000), num: r.p.split("/")[1] }, r.n));
  for (const x of places.dongs || []) docs.push(nameDoc({ type: "dong", path: x.p, kind: x.kind, b: x.b || 0, bonus: x.kind === "legal" ? 3 : 2 }, x.n));
  for (const z of places.zones || []) docs.push(nameDoc({ type: "zone", path: z.p, b: z.b || 0, bonus: 0, num: z.p.split("/")[1], numOnly: true }, `우편구역 ${z.n}`));
  for (const s of places.sensors || []) docs.push(withAddr(nameDoc({ type: "sensor", path: s.p, pt: s.pt, d: meters(s.pt, st), bonus: -10 }, s.n), s.a));
  // 필지: 건물 행의 pnu와 지번에서 만든다 (지번과 PNU로만 찾는다)
  const parcels = new Map();
  for (const p of places.places) {
    if (!p.pnu) continue;
    if (!parcels.has(p.pnu)) parcels.set(p.pnu, { type: "parcel", path: "parcel/" + p.pnu, num: p.pnu, name: p.j || p.pnu, j: p.j, jc: compact(p.j), m: [], pt: p.pt, d: p.d, bonus: -5, numOnly: false, jibunOnly: true });
    parcels.get(p.pnu).m.push(p.i);
  }
  for (const x of places.parcels || []) {
    const pnu = x.p.split("/")[1];
    if (!parcels.has(pnu)) parcels.set(pnu, { type: "parcel", path: x.p, num: pnu, name: x.j || pnu, j: x.j, jc: compact(x.j), m: [], pt: null, d: null, bonus: -5, jibunOnly: true });
  }
  for (const v of parcels.values()) docs.push(v);

  const placeDoc = new Map(), groupDoc = new Map();
  for (const d of docs) { if (d.type === "place") placeDoc.set(d.i, d); else if (d.type === "group") groupDoc.set(d.g, d); }
  return { places, words, docs, byI, groupById, placeDoc, groupDoc, station, areas, parcels, biz: null, chips: places.chips || [] };
}

// 상호 자료 (businesses.json)를 색인에 더한다. 지오스토리처럼 사람이 넣은 장소(src)는 경로가 없어 들어 있는 건물로 잇는다
export function addBusinesses(index, biz) {
  if (index.biz) return index;
  index.biz = biz;
  const st = index.station && index.station.pt;
  for (const b of biz.items || []) {
    const o = {
      type: "business", path: b.p || null, building: b.b || null, lot: b.lot || null, pt: b.pt, d: meters(b.pt, st), bonus: -25,
      cat: b.c || "", mid: b.m || "", big: b.l || "", cc: compact(b.c), mc: compact(b.m), lc: compact(b.l), floor: b.f || "", rows: b.r || 0,
      src: b.src || null, num: b.p ? b.p.split("/")[1] : null, row: b,
    };
    o.name = b.n; o.nc = compact(b.n); o.toks = tokensOf(b.n).map(compact);
    if (b.a) { o.addr = b.a; [o.ac, o.ac2] = addrForms(b.a, index.words.drop); }
    index.docs.push(o);
  }
  return index;
}

// ---- 찾기말 풀기
export function parseQuery(index, raw) {
  const { words } = index;
  const text = String(raw || "").normalize("NFC").trim();
  const toks = tokensOf(text).filter(t => !SEOUL.test(t));
  const kept = toks.filter(t => !words.drop.has(t));
  const qc = compact(kept.join(""));
  const alts = [];
  // 찾기말 전체의 다른 표기 (등촌1동 -> 등촌제1동, 증미 -> 증미역)
  for (const [k, v] of Object.entries(words.aliases)) if (compact(k) === qc) alts.push(compact(v));
  // 끝 줄임말 (백석초 -> 백석초등학교). 마지막 낱말이 그 글자로 끝날 때
  for (const [k, v] of Object.entries(words.suffix)) {
    const kc = compact(k);
    if (qc.length > kc.length && qc.endsWith(kc)) alts.push(qc + compact(v).slice(kc.length));
  }
  const num = text.replace(/[\s-]/g, "");
  return { raw: text, qc, alts, toks: kept.map(compact).filter(Boolean), num };
}

// 이름 점수. s가 이름에 어떻게 들어 있는지에 따라
function nameScore(index, doc, s) {
  if (!s || !doc.nc) return 0;
  if (doc.nc === s) return 100;
  if (doc.type === "dong" && doc.nc.replace(/동$/, "") === s) return 92;   // '가양'으로 가양동
  if (doc.toks && doc.toks.length > 1 && doc.toks.includes(s)) return 88;
  if (doc.nc.startsWith(s)) return 80;
  if (s.length >= 2) {
    for (const a of index.areas) if (doc.nc.startsWith(a + s)) return 78;
    if (doc.toks && doc.toks.some(t => t.startsWith(s))) return 75;
    if (doc.nc.includes(s)) return 60;
  }
  return 0;
}

function addrScore(doc, s) {
  if (!doc.ac || !hasDigit(s)) return 0;
  if (doc.ac === s || doc.ac2 === s) return 92;
  if (onlyNum(s)) return 0;   // 숫자만으로는 주소를 고르지 않는다
  if (endsAt(doc.ac, s)) return 86;
  if (s.length >= 3 && (doc.ac.startsWith(s) || doc.ac2.startsWith(s))) return 46;
  return 0;
}

function jibunScore(doc, s) {
  if (!doc.jc || !hasDigit(s)) return 0;
  if (doc.jc === s) return 90;
  if (endsAt(doc.jc, s) && !onlyNum(s.replace(/^산/, ""))) return 72;
  if (endsAt(doc.jc, s) && s.includes("-")) return 66;
  if (s.length >= 3 && doc.jc.startsWith(s)) return 46;
  return 0;
}

// 상호 업종 점수 (소, 중, 대분류)
function catScore(doc, s) {
  if (doc.type !== "business" || !s) return 0;
  if (doc.cc === s) return 75;
  if (doc.mc === s || doc.mc.split(",").includes(s)) return 70;
  if (doc.lc === s || doc.lc.split(",").includes(s)) return 62;
  if (s.length >= 2 && doc.cc.includes(s)) return 50;
  return 0;
}

// 한 장소의 가장 좋은 점수와 맞은 칸
function scoreDoc(index, doc, Q) {
  let best = 0, field = "name";
  const tryS = (s, minus) => {
    if (!doc.jibunOnly && !doc.numOnly) {
      const n = nameScore(index, doc, s);
      if (n && n - minus > best) { best = n - minus; field = "name"; }
      const c = catScore(doc, s);
      if (c && c - minus > best) { best = c - minus; field = "cat"; }
    }
    if (!doc.numOnly) {
      const a = addrScore(doc, s);
      if (a && a - minus > best) { best = a - minus; field = "addr"; }
      const j = jibunScore(doc, s);
      if (j && j - minus > best) { best = j - minus; field = "jibun"; }
    }
  };
  tryS(Q.qc, 0);
  for (const a of Q.alts) tryS(a, 2);
  // 여러 낱말이 모두 이름에 들면 (가양 이마트)
  if (!best && Q.toks.length > 1 && doc.nc && !doc.jibunOnly && !doc.numOnly && Q.toks.every(t => doc.nc.includes(t))) { best = 55; field = "name"; }
  if (!best) return null;
  // 상호의 이름 점수는 장소보다 낮게 둔다 (이마트24보다 가양이마트 매장동이 먼저). 업종 점수는 그대로
  const bonus = field === "cat" ? 0 : doc.bonus || 0;
  return { score: best + bonus, raw: best, field };
}

// 공식 번호로 바로 가기 (PNU 19, 건물관리번호 25, GIS 28, 도로명코드 12, 기초구역 5자리, 상가업소번호)
function numberHit(index, Q) {
  const n = Q.num;
  let path = null;
  if (/^\d{19}$/.test(n)) path = "parcel/" + n;
  else if (/^\d{25}$/.test(n)) path = "building/" + n;
  else if (/^\d{28}$/.test(n)) path = "building-gis/" + n;
  else if (/^\d{12}$/.test(n)) path = "road/" + n;
  else if (/^\d{5}$/.test(n)) path = "basic-zone/" + n;
  else if (/^MA[0-9A-Z]{10,}$/i.test(n)) path = "business/" + n.toUpperCase();
  if (!path) return null;
  const doc = index.docs.find(d => d.path === path && d.type !== "group");
  return doc ? { doc, score: 200, field: "number" } : null;
}

const TAB = { road: "road", dong: "dong", zone: "dong" };
// 화면 줄로 바꾼다. tab: place(장소), addr(주소), road(길), dong(동네)
function toItem(index, h) {
  const d = h.doc;
  const it = { type: d.type, path: d.path || null, name: d.name, score: h.score, field: h.field, pt: d.pt || null, d: d.d ?? null };
  if (d.type === "group") { it.group = d.g; it.size = d.size; }
  if (d.type === "business") {
    it.building = d.building; it.cat = d.cat; it.floor = d.floor; it.rows = d.rows; it.src = d.src; it.addr = d.addr;
    if (!it.path && d.building) it.path = d.building;   // 사람이 넣은 장소는 들어 있는 건물로 간다
  }
  if (d.type === "place") { it.addr = d.addr; it.j = d.j; it.use = d.use; it.i = d.i; }
  if (d.type === "parcel") { it.m = d.m; }
  if (d.type === "road" || d.type === "dong" || d.type === "zone") it.b = d.b;
  if (d.type === "dong") it.kind = d.kind;
  it.tab = TAB[d.type] || (["addr", "jibun", "number"].includes(h.field) && d.type !== "business" ? "addr" : "place");
  return it;
}

const cmpHit = (a, b) => b.score - a.score || (a.doc.d ?? 9e9) - (b.doc.d ?? 9e9) || (b.doc.b || 0) - (a.doc.b || 0) || a.doc.name.length - b.doc.name.length;

// 찾기. 결과: {items, note, direct}. note.kind: absent(없는 자료 낱말), partial(일부만 찾는 낱말), outside(범위 밖), none(없음)
export function search(index, raw, { limit = 400 } = {}) {
  const Q = parseQuery(index, raw);
  const out = { query: Q.raw, items: [], note: null, direct: null, total: 0 };
  if (!Q.qc) return out;
  const num = numberHit(index, Q);
  let hits = [];
  if (num) { hits.push(num); out.direct = true; }
  else {
    for (const doc of index.docs) {
      const r = scoreDoc(index, doc, Q);
      if (r && r.raw >= MIN) hits.push({ doc, ...r });   // 버릴지는 덧붙이기 전 점수로 정한다 (씨제이올리브영가양역점도 '올리브영'에 나온다)
    }
  }
  // 찾기말이 칩 이름과 같으면 (병원, 학원, 음식점) 그 칩의 장소도 결과에 넣는다. 이미 더 높은 점수로 맞은 곳은 그대로 둔다
  if (!num) {
    const chip = index.chips.find(c => compact(c.label) === Q.qc);
    const items = chip ? chipDocs(index, chip) : null;
    if (items) {
      const have = new Map(hits.map(h => [h.doc, h]));
      for (const doc of items) {
        const h = have.get(doc);
        if (!h) hits.push({ doc, score: 74 + (doc.type === "business" ? 0 : 1), field: doc.type === "business" ? "cat" : "name" });
        else if (h.score < 74) { h.score = 74; h.field = doc.type === "business" ? "cat" : "name"; }
      }
    }
  }
  // 같은 묶음은 한 줄: 이름으로 맞았거나 둘 이상 맞은 묶음의 건물은 묶음 줄로 합친다
  const byG = new Map();
  for (const h of hits) if (h.doc.type === "place" && h.doc.g != null && !num) {
    if (!byG.has(h.doc.g)) byG.set(h.doc.g, []);
    byG.get(h.doc.g).push(h);
  }
  const gHit = new Map(hits.filter(h => h.doc.type === "group").map(h => [h.doc.g, h]));
  const drop = new Set();
  for (const [g, list] of byG) {
    const groupDoc = index.groupDoc.get(g);
    const nameHit = list.some(h => h.field === "name");
    if (!groupDoc || (!gHit.has(g) && list.length < 2 && !nameHit)) continue;
    const best = list.reduce((a, b) => (b.score > a.score ? b : a));
    const cur = gHit.get(g);
    if (!cur || best.score > cur.score) gHit.set(g, { doc: groupDoc, score: Math.max(best.score, cur ? cur.score : 0), field: best.field });
    list.forEach(h => drop.add(h));
  }
  hits = hits.filter(h => !drop.has(h) && h.doc.type !== "group").concat([...gHit.values()]);
  // 필지 줄은 같은 지번의 건물 줄이 있으면 뒤로 (지번 찾기는 건물이 먼저)
  hits.sort(cmpHit);
  // 없는 자료 낱말(버스, 영업시간)은 이름이 그 낱말로 시작하는 곳만 남긴다 ('콜럼버스컴퍼니'가 '버스' 답이 되지 않게)
  if (index.words.absent.includes(Q.qc)) hits = hits.filter(h => (h.raw ?? h.score) >= 80);
  out.items = hits.slice(0, limit).map(h => toItem(index, h));
  out.total = hits.length;

  const { words } = index;
  const qc = Q.qc;
  if (words.absent.includes(qc)) out.note = { kind: "absent", text: words.notes.absent_note };
  else if (!out.items.length && words.outside.some(w => qc.includes(w))) out.note = { kind: "outside", text: words.notes.outside_note };
  else if (!out.items.length) out.note = { kind: "none", text: words.notes.none_note };
  else if (words.partial.some(w => qc.includes(w))) out.note = { kind: "partial", text: words.notes.partial_note };
  return out;
}

// 자동완성 후보 (첫 8개)
export function suggest(index, raw, n = 8) {
  const r = search(index, raw, { limit: n });
  return { ...r, items: r.items.slice(0, n) };
}

// 이름에서 찾기말이 맞은 자리 [시작, 끝) (공백을 건너 견준다). 없으면 null
export function markRange(name, raw) {
  const s = compact(raw);
  if (!s) return null;
  const map = [];
  let c = "";
  const src = String(name || "");
  for (let k = 0; k < src.length; k++) {
    const ch = compact(src[k]);
    if (!ch) continue;
    for (const x of ch) { c += x; map.push(k); }
  }
  const at = c.indexOf(s);
  if (at < 0) return null;
  return [map[at], map[at + s.length - 1] + 1];
}

// ---- 칩 (명세 9절, 0-1절). places.json의 chips 규칙을 그대로 쓴다. 묶음은 한 곳으로 센다
// 칩에 드는 장소 (색인 항목). 상호 칩인데 상호 자료가 아직 없으면 null
function chipDocs(index, chip) {
  const { byI } = index;
  const out = [];
  const has = (name, ws) => (ws || []).some(w => String(name).includes(w));
  if (chip.kind === "biz") {
    if (!index.biz) return null;
    for (const d of index.docs) {
      if (d.type !== "business" || d.src) continue;
      if ((chip.exclude_middle || []).includes(d.mid)) continue;
      if ((chip.small || []).includes(d.cat) || (chip.middle || []).includes(d.mid) || (chip.large || []).includes(d.big)) out.push(d);
    }
    return out;
  }
  for (const d of index.docs) {
    let name, members;
    if (d.type === "group") { name = d.name; members = (index.groupById.get(d.g) || { m: [] }).m; }
    else if (d.type === "place" && d.g == null) { name = d.name; members = [d.i]; }
    else continue;
    const hit = chip.kind === "name" ? has(name, chip.words)
      : members.some(i => byI[i] && (chip.uses || []).includes(byI[i].use)) && !has(name, chip.exclude_words);
    if (hit) out.push(d);
  }
  return out;
}

export function chipItems(index, chip) {
  const docs = chipDocs(index, chip);
  if (!docs) return null;
  const items = docs.map(doc => toItem(index, { doc, score: 0, field: doc.type === "business" ? "cat" : "name" }));
  items.sort((a, b) => (a.d ?? 9e9) - (b.d ?? 9e9));
  return items;
}

// ---- 조건 찾기 (용도 묶음, 층수, 사용승인 연도, 역 거리). 건물 행을 고른 뒤 묶음은 한 줄로 합친다
// cond: {ug: Set|array, minFloors, yearFrom, yearTo, maxD}
export function conditionItems(index, cond) {
  const ug = cond.ug && (cond.ug.size || cond.ug.length) ? new Set(cond.ug) : null;
  const pick = p => {
    if (ug && !ug.has(p.ug || "none")) return false;
    if (cond.minFloors && !((p.fa || 0) >= cond.minFloors)) return false;
    const y = p.ad ? Number(String(p.ad).slice(0, 4)) : null;
    if ((cond.yearFrom || cond.yearTo) && !y) return false;
    if (cond.yearFrom && y < cond.yearFrom) return false;
    if (cond.yearTo && y > cond.yearTo) return false;
    if (cond.maxD && !(p.d != null && p.d <= cond.maxD)) return false;
    return true;
  };
  const rows = index.places.places.filter(pick);
  const groups = new Map(), items = [];
  for (const p of rows) {
    if (p.g != null) { if (!groups.has(p.g)) groups.set(p.g, []); groups.get(p.g).push(p); }
    else items.push(toItem(index, { doc: index.placeDoc.get(p.i), score: 0, field: "name" }));
  }
  for (const [g, list] of groups) {
    const doc = index.groupDoc.get(g);
    if (list.length === 1 || !doc) items.push(toItem(index, { doc: index.placeDoc.get(list[0].i), score: 0, field: "name" }));
    else { const it = toItem(index, { doc, score: 0, field: "name" }); it.hit = list.length; items.push(it); }
  }
  items.sort((a, b) => (a.d ?? 9e9) - (b.d ?? 9e9));
  return { items, buildings: rows.length };
}

// 결과 정렬: score(맞는 순), station(역에서 가까운 순), center(지도 가운데서 가까운 순)
export function sortItems(items, by, center) {
  const a = items.slice();
  if (by === "station") a.sort((x, y) => (x.d ?? 9e9) - (y.d ?? 9e9));
  else if (by === "center" && center) a.sort((x, y) => meters(x.pt, center) - meters(y.pt, center));
  return a;
}

// 지도 범위 안의 줄만 (다시 찾기). bounds: [서, 남, 동, 북]
export function within(items, bounds) {
  const [w, s, e, n] = bounds;
  return items.filter(x => x.pt && x.pt[0] >= w && x.pt[0] <= e && x.pt[1] >= s && x.pt[1] <= n);
}
