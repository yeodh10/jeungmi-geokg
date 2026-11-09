// 묻기 탭 대량 시험. 화면 없이 묻기 탭의 차례(근거 찾기 먼저 -> 질문 틀 -> 틀이 못 읽으면 근거 찾기)를 그대로 돌린다.
// 실행: node tests/rag_sweep.mjs [사이트 폴더]   (기본: data/processed/site, appdata가 만든 공개 사이트)
// 1) 종류 질문: 기준 장소 7곳 x 종류 낱말 120개 x 말투 16개 = 1,920문항
//    - 답을 못 하면 실패
//    - '들어간 곳이 없습니다'라고 했는데 공개 자료의 상호 이름, 업종, 건물 이름에 그 말이 있으면 실패 (있는 곳을 없다고 하면 안 된다)
//    - 기준 장소를 잘못 읽으면 실패, 질문 틀이 다른 업종의 답을 내면 실패
// 2) 장소 질문: 상호 60곳, 이름 있는 건물 60곳, 단지 20곳 x 말투 6개. 물은 장소를 찾아 답해야 한다
// 3) 건물 안 질문: 건물 40곳의 '뭐 있어'와 '안 (업종)'. 같은 필지의 건물에 이어진 상호를 하나도 빠뜨리면 안 된다
// 4) 순위 질문: '가장 높은 건물' 같은 질문의 1위가 자료에서 직접 고른 1위와 같아야 한다
// 5) 조건 질문: 동네, 길, 층 조건과 두 곳 사이, 자료에 없는 조건(24시, 주차 등)을 밝히는지
import { readFileSync, existsSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const site = resolve(process.argv[2] || join(root, "data/processed/site"));
const imp = f => import(pathToFileURL(join(root, f)).href);
const core = await imp("web/app/search-core.js"), rag = await imp("web/app/ask-rag.js"), AM = await imp("web/app/ask-match.js");
const read = f => JSON.parse(readFileSync(f, "utf8"));
const places = read(join(site, "assets/data/places.json"));
const biz = read(join(site, "assets/data/businesses.json")).items;
const index = core.createIndex(places);
core.addBusinesses(index, { items: biz });
const idx = AM.prepare(read(join(site, "assets/data/ask.json")));
const card = p => { const f = join(site, "id", p, "card.json"); return existsSync(f) ? read(f) : null; };
const center = places.station.p;

function flow(q) {
  const plan = rag.read(core, index, q, { basis: null });
  const cards = Object.fromEntries(plan.ents.map(e => [e.path, card(e.path)]));
  for (const c of Object.values(cards)) for (const p of rag.follow(c)) cards[p] = card(p);
  const a = rag.answer(plan, cards);
  const m = AM.match(idx, q);
  if (rag.ragFirst(plan, m, center) && a.ok) return { by: "rag", text: a.sentences.join(" "), plan };
  if (m.ok) return { by: "틀", text: AM.answerOf(idx, m, places.places).answer, plan };
  if (rag.ragAfter(plan) && a.ok) return { by: "rag", text: a.sentences.join(" "), plan };
  return { by: "없음", text: m.message || "", plan };
}

const C = s => String(s || "").toLowerCase().replace(/[\s()[\]{}"'·.]/g, "");
const TAIL = /(전문점|매장|가게|관|점|집|소|방|장|샵|숍)$/;
const hay = [...biz.map(b => ({ n: C(b.n), c: C(b.c) + "|" + C(b.m) })),
  ...places.places.filter(p => p.n && !/^이름 없는/.test(p.n)).map(p => ({ n: C(p.n), c: "" })), ...(places.groups || []).map(g => ({ n: C(g.n), c: "" }))];

// 1) 종류 질문
const anchors = ["증미역", "가양역", "등촌역", "지오스토리", "강서아이티밸리", "올리브영 증미역점", "염창동우성아파트"];
const kinds = ("카페,커피숍,편의점,약국,병원,치과,한의원,소아과,피부과,안과,내과,이비인후과,미용실,네일,세탁소,빨래방,꽃집,빵집,베이커리,마트,슈퍼,정육점,반찬가게,과일가게,문구점,서점,안경점,휴대폰 가게,부동산,학원,태권도,피아노학원,헬스장,필라테스,요가,PC방,노래방,당구장,모텔,호텔,주유소,세차장,카센터,동물병원,애견미용,철물점,인쇄소,사진관,"
  + "국밥집,닭한마리 집,짜장면,짬뽕,중국집,마라탕,초밥,스시,횟집,라멘,돈가스,파스타,피자,햄버거,쌀국수,떡볶이,김밥,분식,삼겹살,고깃집,족발,보쌈,감자탕,순대국,칼국수,냉면,곱창,백반,샐러드,빙수,술집,호프,이자카야,치킨,통닭,맛집,식당,"
  + "은행,ATM,우체국,주민센터,경찰서,소방서,도서관,학교,초등학교,어린이집,유치원,교회,성당,아파트,오피스텔,공원,주차장,다이소,스타벅스,올리브영,맥도날드,버거킹,이마트,홈플러스,배스킨라빈스,파리바게뜨,던킨,gs25,cu,세븐일레븐,서브웨이,메가커피,교촌치킨,bhc,롯데리아").split(",");
const forms = [
  (a, k) => `${a}에서 가장 가까운 ${k} 어디야`, (a, k) => `${a} 근처 ${k}`, (a, k) => `${a} 주변에 ${k} 있어?`, (a, k) => `${a}에서 제일 가까운 ${k}`,
  (a, k) => `${a} 500m 안 ${k}`, (a, k) => `${k} 어디 있어`, (a, k) => `${a} 가까운 ${k} 알려줘`, (a, k) => `${a} 근처에 ${k} 있나요`,
  (a, k) => `${a}랑 제일 가까운 ${k}는?`, (a, k) => `${k} 중에 ${a}에서 제일 가까운 곳`, (a, k) => `${a} 쪽에 ${k} 있어`, (a, k) => `${a} 1km 이내 ${k}`,
  (a, k) => `${k} 몇 개 있어`, (a, k) => `${a} 주변 ${k} 추천해줘`, (a, k) => `${a}에서 걸어갈 만한 ${k}`, (a, k) => `${a} 인근 ${k} 위치`,
];
const bad = [];
let n1 = 0, i = 0;
for (const k of kinds) for (const f of forms) {
  const a = anchors[i++ % anchors.length], q = f(a, k);
  n1++;
  const r = flow(q);
  if (r.by === "없음") { bad.push(["답 없음", q, r.text.slice(0, 60)]); continue; }
  if (/들어간 곳이 없습니다/.test(r.text)) {
    const base = C(k).replace(/(전문점|매장|가게|집)$/, "") || C(k), cs = C(k).length > 2 ? C(k).replace(TAIL, "") : "";
    const there = hay.filter(h => h.n.includes(base) || (cs.length >= 2 && h.c.includes(cs)));
    if (there.length) bad.push(["있는데 없다고 함", q, there.slice(0, 3).map(h => h.n).join(", ")]);
  }
  if (!q.startsWith(k) && r.by === "rag" && !(r.plan.ents[0] && C(r.plan.ents[0].name).includes(C(a).slice(0, 4)))) bad.push(["기준 장소를 잘못 읽음", q, JSON.stringify(r.plan.ents.map(e => e.name))]);
  if (r.by === "틀" && !C(r.text).includes((C(k).replace(TAIL, "") || C(k)).slice(0, 2))) bad.push(["질문 틀이 다른 답", q, r.text.slice(0, 80)]);
}
console.log(`종류 질문 ${n1}개: 문제 ${bad.length}개`);

// 2) 장소 질문. 쉼표가 든 이름과 '카페'처럼 종류 낱말과 같은 이름은 장소가 아니라 종류로 읽는 것이 맞아 뺀다 (뺀 수를 적는다)
const pick = (arr, n, seed) => Array.from({ length: n }, (_, k) => arr[(k * 7919 + seed) % arr.length]);
const okName = n => n && n.length >= 3 && !/[,]/.test(n) && !rag.isKindWord(core, n);
const pool = [...pick(biz.filter(b => b.n && b.n.length >= 3), 60, 11).map(b => ({ n: b.n, p: b.p || b.b })),
  ...pick(places.places.filter(p => p.n && !/^이름 없는/.test(p.n) && p.n.length >= 3 && p.g == null), 60, 5).map(p => ({ n: p.n, p: p.p })),
  ...(places.groups || []).slice(0, 20).map(g => ({ n: g.n, p: null }))];
const targets = pool.filter(t => okName(t.n));
const pforms = [n => `${n} 어디야`, n => `${n} 위치 알려줘`, n => `${n} 주소`, n => `${n}는 어디에 있어?`, n => `${n} 근처 카페`, n => `${n}에서 증미역까지 거리`];
const bad2 = [];
for (const t of targets) for (const f of pforms) {
  const q = f(t.n), r = flow(q), e = r.plan.ents[0];
  const ok = r.by !== "없음" && e && (C(e.name) === C(t.n) || (t.p && e.path === t.p) || C(e.name).includes(C(t.n)) || C(t.n).includes(C(e.name)));
  if (!ok) bad2.push(["장소를 못 찾음", q, `${e ? e.name : "(없음)"} | ${r.text.slice(0, 60)}`]);
}
console.log(`장소 질문 ${targets.length * pforms.length}개 (이름 ${pool.length - targets.length}개는 종류 낱말이거나 쉼표가 있어 뺌): 문제 ${bad2.length}개`);
// 3) 건물 안 질문: 상호가 이어진 이름 있는 건물 40곳. 같은 필지의 건물에 이어진 상호를 직접 세어 답과 견준다
const rowsByPnu = new Map();
for (const r of places.places) if (r.pnu) { if (!rowsByPnu.has(r.pnu)) rowsByPnu.set(r.pnu, []); rowsByPnu.get(r.pnu).push(r.p); }
const rowOf = new Map(places.places.map(r => [r.p, r]));
const bizIn = new Map(); for (const b of biz) if (b.b) { if (!bizIn.has(b.b)) bizIn.set(b.b, []); bizIn.get(b.b).push(b); }
const plotBiz = r => [...new Set((rowsByPnu.get(r.pnu) || [r.p]).flatMap(p => bizIn.get(p) || []))];
const hosts = places.places.filter(r => r.n && !/^이름 없는/.test(r.n) && r.n.length >= 3 && !/[,]/.test(r.n) && !rag.isKindWord(core, r.n) && plotBiz(r).length && r.g == null);
const bad3 = [];
let n3 = 0;
for (const r of pick(hosts, 40, 3)) {
  const want = plotBiz(r);
  const q1 = `${r.n}에 뭐 있어`, a1 = flow(q1); n3++;
  const m = a1.text.match(/상호는 ([\d,]+)곳/);
  if (!m || Number(m[1].replace(/,/g, "")) < want.length) bad3.push(["건물 안 상호 수가 모자람", q1, `답 ${m ? m[1] : "없음"}, 자료 ${want.length}곳`]);
  const c = want.find(b => b.c && b.c.length >= 2);
  if (c) {
    const q2 = `${r.n} 안 ${c.c}`, a2 = flow(q2); n3++;
    if (!a2.text.includes(c.n)) bad3.push(["건물 안 업종을 빠뜨림", q2, `${c.n}이 답에 없음 | ${a2.text.slice(0, 60)}`]);
  }
}
console.log(`건물 안 질문 ${n3}개: 문제 ${bad3.length}개`);

// 4) 순위 질문: 답의 1위가 자료에서 직접 고른 1위와 같아야 한다
const bad4 = [];
const meters = (a, b) => { const k = Math.cos(((a[1] + b[1]) / 2) * Math.PI / 180); return Math.hypot((a[0] - b[0]) * 111320 * k, (a[1] - b[1]) * 110540); };
const st = places.station.pt;
const top = (rows, key, dir) => rows.filter(x => x[key] != null && x[key] !== "").sort((a, b) => (a[key] < b[key] ? -1 : a[key] > b[key] ? 1 : 0) * dir)[0];
const rankCases = [
  ["가장 높은 건물", top(places.places, "h", -1)], ["제일 오래된 건물", top(places.places, "ad", 1)], ["층수가 가장 많은 건물", top(places.places, "fa", -1)],
  ["증미역 근처 가장 높은 건물", top(places.places.filter(x => meters(x.pt, st) <= 300), "h", -1)],
  ["세대수 가장 많은 아파트", top((places.groups || []).filter(g => /아파트/.test(g.n)), "hh", -1)],
];
for (const [q, want] of rankCases) {
  const r = flow(q), gn = want && want.g != null ? (places.groups.find(g => g.g === want.g) || {}).n : null;
  const name = want ? (gn || want.n) : "";
  if (!want || !r.text.split("그다음")[0].includes(name)) bad4.push(["순위 1위가 다름", q, `자료 1위 ${name} | ${r.text.slice(0, 80)}`]);
}
console.log(`순위 질문 ${rankCases.length}개: 문제 ${bad4.length}개`);
// 5) 조건 질문: 동네, 길, 층 조건은 자료에서 직접 센 수보다 적게 답하면 안 되고(이름이나 업종에 그 말이 든 곳만 센다),
//    자료에 없는 조건(24시, 주차, 걸어서 5분 등)은 뺐다고 밝혀야 한다
const bad5 = [];
let n5 = 0;
const legal = places.legalDong || {}, roadName = new Map((places.roads || []).map(r => [r.p.split("/")[1], r.n]));
const rowP = new Map(places.places.map(r => [r.p, r]));
const ckinds = ["카페", "편의점", "약국", "미용실", "치킨", "부동산", "학원", "세탁소", "중국집", "국밥"];
const countIn = (k, test) => biz.filter(b => { const r = rowP.get(b.b); return r && test(r) && (C(b.n).includes(C(k)) || C(b.c).includes(C(k)) || C(b.m).includes(C(k))); }).length;
const numOf = t => { const m = t.match(/([\d,]+)곳입니다/); return m ? Number(m[1].replace(/,/g, "")) : (/없습니다/.test(t) ? 0 : -1); };
for (const dn of Object.values(legal)) for (const k of ckinds) {
  const q = `${dn}에 ${k} 몇 개야`, r = flow(q), want = countIn(k, row => legal[row.ld] === dn), got = numOf(r.text); n5++;
  if (got < want) bad5.push(["동네 조건 수가 모자람", q, `답 ${got}, 자료 ${want}`]);
}
for (const rn of ["양천로", "공항대로", "화곡로", "등촌로", "허준로"]) for (const k of ckinds) {
  const q = `${rn}에 있는 ${k}`, r = flow(q), want = countIn(k, row => roadName.get(row.road) === rn), got = numOf(r.text); n5++;
  if (got < want) bad5.push(["길 조건 수가 모자람", q, `답 ${got}, 자료 ${want}`]);
}
for (const f of ["1", "2", "3"]) for (const k of ckinds) {
  const q = `${f}층에 있는 ${k}`, r = flow(q), got = numOf(r.text);
  const want = biz.filter(b => b.f === f && (C(b.n).includes(C(k)) || C(b.c).includes(C(k)) || C(b.m).includes(C(k)))).length; n5++;
  if (got < want) bad5.push(["층 조건 수가 모자람", q, `답 ${got}, 자료 ${want}`]);
}
for (const mq of ["증미역 근처 24시 편의점", "가양역 근처 주차되는 카페", "등촌역에서 걸어서 5분 거리 약국", "증미역 근처 맛있는 국밥집", "증미역 근처 배달되는 치킨", "가양역 근처 조용한 카페", "증미역 근처 싼 미용실", "등촌역 근처 카페 추천해줘"]) {
  const r = flow(mq); n5++;
  if (!/이 자료에 없어 그 조건은 빼고 찾았습니다/.test(r.text)) bad5.push(["없는 조건을 밝히지 않음", mq, r.text.slice(0, 80)]);
}
for (const [a, b2] of [["증미역", "가양역"], ["증미역", "등촌역"], ["가양역", "강서아이티밸리"]]) for (const k of ["카페", "편의점", "약국"]) {
  const q = `${a}과 ${b2} 사이 ${k}`, r = flow(q); n5++;
  if (!/사이\(두 곳을 잇는 직선에서/.test(r.text)) bad5.push(["두 곳 사이를 읽지 못함", q, r.text.slice(0, 80)]);
}
console.log(`조건 질문 ${n5}개: 문제 ${bad5.length}개`);
for (const b of [...bad, ...bad2, ...bad3, ...bad4, ...bad5].slice(0, 80)) console.log("  " + b.join("  |  "));
const fail = bad.length + bad2.length + bad3.length + bad4.length + bad5.length;
console.log(fail ? `\n실패 ${fail}건` : "\n통과");
process.exit(fail ? 1 : 0);
