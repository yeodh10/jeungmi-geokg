// 그래프에서 찾아 답하기 (키 없는 질문의 두 번째 단계). 모델도 API도 쓰지 않는 검색 증강 답하기다.
// 질문 틀(ask-match.js)에 맞지 않는 질문을 받으면
//   1) 찾기 색인(search-core.js)으로 질문 속 장소를 찾는다 (최대 둘)
//   2) 무엇을 묻는지(층수, 사용승인일, 주소, 노선, 행정동, 입구, 출처, 거리 등) 낱말 목록으로 정한다
//   3) 그 장소의 카드 자료(card.json, 공개 범위 그래프에서 빌드 때 만든 것)에서 근거 칸을 고르고, 사람이 쓴 문장 틀에 넣는다
// 근거 칸이 없으면 지어내지 않고 없다고 답한다. 무엇을 묻는지 모르면 질문과 글자 조각이 가장 많이 겹치는 칸을 근거로 보여 준다.
// 화면 없는 순수 함수만 둔다 (node tests/rag_check.mjs가 같은 파일을 쓴다). 카드는 부르는 쪽이 받아 넘긴다.

// ---- 무엇을 묻는가. keys는 카드 facts의 칸 이름, rels는 links의 관계 이름, field는 카드의 다른 칸
export const ASPECTS = [
  { id: "missing", re: null },   // 아래 ABSENT로 따로 본다
  { id: "source", label: "출처", re: /출처|근거|어디서\s*(나온|온|가져)|무슨\s*자료|어느\s*자료/, field: "sources" },
  { id: "dist", label: "거리", re: /거리|멀[어어요다]|먼가|가까[워운]|몇\s*m|몇\s*미터|얼마나\s*(떨어|가)|떨어져/ },
  { id: "line", label: "노선", re: /노선|호선/, keys: ["노선"] },
  { id: "adjacent", label: "옆 역", re: /옆\s*역|다음\s*역|이전\s*역|이웃\s*역|인접/, rels: ["옆 역"] },
  { id: "height", label: "높이", re: /높이|몇\s*m\s*(높|짜리)|얼마나\s*높/, keys: ["높이"] },
  { id: "floors", label: "층수", re: /몇\s*층|층수|층짜리|층\s*(이야|인가|이에요|입니까|건물)|지하|지상/, keys: ["지상 층수", "지하 층수", "층"] },
  { id: "year", label: "사용승인일", re: /언제|지어|지은|준공|사용\s*승인|승인일|몇\s*년|연식|오래된|건축\s*연도/, keys: ["사용승인일"] },
  { id: "use", label: "용도", re: /용도|무슨\s*건물|어떤\s*건물|뭐\s*하는\s*건물|뭐하는\s*건물|쓰임/, keys: ["용도", "단지 주용도"] },
  { id: "struct", label: "구조", re: /구조|철근|콘크리트|벽돌/, keys: ["구조"] },
  { id: "area", label: "면적", re: /면적|넓이|몇\s*평|크기/, keys: ["연면적", "대지면적"] },
  { id: "households", label: "세대수", re: /세대/, keys: ["세대수"] },
  { id: "cat", label: "업종", re: /업종|뭐\s*파는|무엇을\s*파는|무슨\s*가게|어떤\s*가게|뭐\s*하는\s*곳|뭐하는\s*곳|무슨\s*업/, keys: ["업종"] },
  { id: "jibun", label: "지번", re: /지번|번지/, field: "jibun" },
  { id: "no", label: "공식 번호", re: /관리\s*번호|식별\s*번호|번호가|번호는|번호\s*알|pnu|코드/i, field: "officialNo" },
  { id: "addr", label: "주소", re: /주소|어디\s*(있|야|에|인|쯤|$)|어디야|위치|찾아\s*가|가는\s*길|어느\s*쪽/, field: "addr" },
  { id: "dong", label: "동", re: /무슨\s*동|어느\s*동|몇\s*동이|행정동|법정동|동네/, rels: ["행정동", "법정동"] },
  { id: "zip", label: "우편구역", re: /우편/, rels: ["우편구역"] },
  { id: "road", label: "길", re: /무슨\s*길|어느\s*길|무슨\s*도로|어느\s*도로|길\s*이름/, rels: ["길"] },
  { id: "entrance", label: "입구", re: /입구|출입구|들어가는\s*곳/, rels: ["입구"], count: true },
  { id: "parcel", label: "필지", re: /필지|무슨\s*땅|어느\s*땅|땅\s*번호/, rels: ["필지"] },
  { id: "building", label: "건물", re: /무슨\s*건물에|어느\s*건물|들어\s*있는\s*건물|어디\s*건물|위에/, rels: ["들어 있는 건물", "소속 건물", "위에 선 건물", "역사 건물", "설치 주소의 건물"] },
  { id: "group", label: "단지", re: /단지/, rels: ["같은 단지"] },
  { id: "status", label: "상태", re: /상태|폐지|없어졌|아직\s*있/, keys: ["상태"], field: "status" },
];
// 이 자료에 아예 없는 것 (카드의 missing과 같은 이름). 물으면 지어내지 않고 없다고 답한다
export const ABSENT = [
  ["영업시간", /영업\s*시간|몇\s*시(에|까지|부터)|문\s*(열|닫)|여는\s*시간|닫는\s*시간/],
  ["전화번호", /전화|연락처/], ["리뷰", /리뷰|후기|평점|별점|맛있/], ["사진", /사진|이미지/],
  ["시간표", /시간표|첫차|막차|배차/], ["출구 번호", /출구/], ["도착 정보", /도착|언제\s*와/], ["가격", /가격|얼마야|비싸|싸/],
];
const ASK_WORDS = /^(몇|언제|어디|어디야|어디에|뭐|뭐야|무슨|어느|어떤|얼마나|알려줘|알려|줘|좀|이야|인가요|인가|이에요|예요|야|있어|있나요|해줘|말해줘|궁금해|정도|대략|약|혹시|그|이|저|거기|무엇|무엇인가요)$/;
// 장소 이름이 될 수 없는 흔한 낱말 (이것만으로 된 묶음은 장소로 찾지 않는다)
const GENERIC = /^(건물|곳|장소|가게|상가|위|아래|안|밖|근처|주변|둘레|부근|옆|앞|뒤|층|동|길|거리|주소|역|번호|정보|자료|이름|여기|거기|저기|어디쯤)$/;
const NEAR = /근처|주변|둘레|부근|가까운\s*곳|가까이|인근/;
const JOSA = ["에서부터", "으로부터", "에서는", "이랑", "에서", "까지", "부터", "으로", "하고", "에는", "은", "는", "이", "가", "을", "를", "의", "에", "와", "과", "랑", "로", "도", "만"];
const ENDINGS = ["이에요", "인가요", "입니까", "이야", "예요", "인가", "야"];

const strip = s => String(s || "").normalize("NFC").replace(/[?？!.,~"'“”‘’()[\]]/g, " ").replace(/\s+/g, " ").trim();
function bare(tok) {
  let t = tok;
  for (const e of ENDINGS) if (t.length > e.length + 1 && t.endsWith(e)) { t = t.slice(0, -e.length); break; }
  for (const j of JOSA) if (t.length > j.length + 1 && t.endsWith(j)) { t = t.slice(0, -j.length); break; }
  return t;
}

// ---- 받침에 맞는 조사
const DIGIT_JONG = { 0: 1, 1: 1, 2: 0, 3: 1, 4: 0, 5: 0, 6: 1, 7: 1, 8: 1, 9: 0 };
function hasJong(w) {
  const s = String(w || "").replace(/[\s)\]'"’”]+$/, "");
  const c = s[s.length - 1];
  if (!c) return false;
  const k = c.charCodeAt(0) - 0xac00;
  if (k >= 0 && k < 11172) return k % 28 !== 0;
  if (/\d/.test(c)) return !!DIGIT_JONG[c];
  return /[lmnr]$/i.test(c);
}
export const josa = (w, pair) => { const [a, b] = pair.split("/"); return w + (hasJong(w) ? a : b); };   // josa('증미역', '은/는')

// ---- 글자 조각 유사도 (두 글자). 무엇을 묻는지 모를 때 근거 칸 고르기에 쓴다
const grams = s => { const t = String(s || "").replace(/\s+/g, ""); const g = new Set(); for (let i = 0; i < t.length - 1; i++) g.add(t.slice(i, i + 2)); return g; };
function overlap(q, s) {
  const A = grams(q), B = grams(s);
  if (!A.size || !B.size) return 0;
  let n = 0; for (const x of A) if (B.has(x)) n++;
  return n / A.size;
}

// 거리 (m). search-core.meters와 같은 평면 근사
function meters(a, b) {
  const k = Math.cos(((a[1] + b[1]) / 2) * Math.PI / 180);
  return Math.round(Math.hypot((a[0] - b[0]) * 111320 * k, (a[1] - b[1]) * 110540));
}
const nf = n => Number(n).toLocaleString("ko-KR");

// ---- 1) 질문 읽기: 장소와 묻는 것
// core: search-core.js 모듈, index: 찾기 색인, basis: 거리 기준점({pt, n}) 또는 null
export function read(core, index, text, { basis = null } = {}) {
  const q = strip(text);
  const toks = q.split(" ").filter(Boolean);
  const words = toks.map(bare);
  let aspects = ASPECTS.filter(a => a.re && a.re.test(q));
  if (aspects.some(a => a.id === "building")) aspects = aspects.filter(a => a.id !== "use");   // '위에 무슨 건물'은 용도가 아니라 건물을 묻는다
  const absent = ABSENT.filter(([, re]) => re.test(q)).map(([n]) => n);
  // 장소 찾기: 이어진 낱말 묶음(긴 것부터)마다 찾기를 돌려 이름이 제대로 맞는 것만 받는다
  const cands = [];
  for (let len = Math.min(4, words.length); len >= 1; len--) {
    for (let i = 0; i + len <= words.length; i++) {
      const span = words.slice(i, i + len);
      if (span.some(w => ASK_WORDS.test(w) || w.length < 2 && !/\d/.test(w)) || span.every(w => GENERIC.test(w))) continue;
      const s = span.join(" ");
      if (ASPECTS.some(a => a.re && a.re.test(s) && a.re.source.length > 0 && s.replace(a.re, "").trim().length < 2)) continue;   // 묻는 말 자체 ('몇 층', '주소')
      const r = core.search(index, s, { limit: 3 });
      const top = r.items[0];
      if (!top || !top.path) continue;
      // 제대로 맞은 것만: 이름은 찾기말이 이름의 40% 넘게 덮을 때(센서 -> 'S-DoT 센서 V02...'는 아님),
      // 주소는 길 이름과 번호가, 지번은 동 이름과 번호가 함께 있을 때. 화면이 지은 '이름 없는 건물' 이름은 받지 않는다
      if (/^이름 없는/.test(top.name || "")) continue;
      const cover = core.compact(s).length / Math.max(1, core.compact(top.name).length);
      const strong = top.field === "number"
        || (top.field === "name" && top.score >= 75 && cover >= 0.4)
        // 초성은 상호 감점(-25) 때문에 점수가 낮다. 세 글자 넘게 이름 전체가 맞고 다음 후보와 뚜렷이 다르면 받는다 ('ㅈㅇㅅㅌㄹ' -> 지오스토리)
        || (top.field === "name" && core.hasJamo(s) && core.compact(s).length >= 3 && cover >= 0.99 && top.score >= 65 && !(r.items[1] && r.items[1].score > top.score - 10))
        || (top.field === "addr" && top.score >= 80 && span.some(w => /(로|길)$/.test(w)) && span.some(w => /\d/.test(w)))
        || (top.field === "jibun" && top.score >= 70 && span.some(w => /(동|가)$/.test(w)) && span.some(w => /\d/.test(w)));
      // 여러 낱말이 함께 맞으면 앞세운다 ('등촌동 666-40'이 '등촌동'보다 먼저)
      if (strong) cands.push({ i, len, item: top, typo: r.typo || null, score: top.score + len * 15 });
    }
  }
  cands.sort((a, b) => b.score - a.score || b.len - a.len);
  const ents = [];
  for (const c of cands) {
    if (ents.length >= 2) break;
    if (ents.some(e => !(c.i + c.len <= e.i || e.i + e.len <= c.i) || e.item.path === c.item.path)) continue;
    ents.push(c);
  }
  ents.sort((a, b) => a.i - b.i);   // 질문에 나온 차례 ('A에서 B까지')
  // 'OO 근처 약국': 장소를 뺀 낱말 가운데 업종이나 칩 이름으로 맞는 것을 찾는다
  let near = null;
  if (ents.length === 1 && NEAR.test(q)) {
    const e = ents[0];
    const m = q.match(/(\d{2,4})\s*(m|미터)/);
    const radius = m ? Math.min(1000, Math.max(50, Number(m[1]))) : 300;
    for (let k = 0; k < words.length && !near; k++) {
      const w = words[k];
      if (k >= e.i && k < e.i + e.len) continue;
      if (ASK_WORDS.test(w) || GENERIC.test(w) || NEAR.test(w) || /^(가까운|가까이|가깝|먼|멀리)/.test(w) || w.length < 2 || /^\d/.test(w)) continue;
      const r = core.search(index, w);
      const items = r.items.filter(x => x.pt && x.path !== e.item.path && (x.field === "cat" || x.field === "name") && x.score >= 50);
      if (items.length) near = { word: w, radius, items };
    }
  }
  // '어디에 있어'를 물으면 가장 가까운 역까지 함께 알려 준다 (찾기 색인의 역 목록)
  const where = /어디|위치|찾아\s*가|가는\s*길|어느\s*쪽/.test(q);
  const stations = where ? (index.docs || []).filter(d => d.type === "station" && d.pt).map(d => ({ name: d.name, pt: d.pt, path: d.path })) : [];
  const here = basis && basis.pt && /여기|기준|고른\s*곳/.test(q) ? { name: basis.n || "거리 기준", pt: basis.pt, basis: true } : null;
  return { q, aspects, absent, near, ents: ents.map(e => ({ path: e.item.path, name: e.item.name, type: e.item.type, pt: e.item.pt, group: e.item.group ?? null, score: e.item.score, field: e.item.field, said: words.slice(e.i, e.i + e.len).join(" "),
    building: e.item.building || null, floor: e.item.floor || "", src: e.item.src || null })), here, basis, where, stations };
}

// 질문 틀보다 먼저 답할까: 장소를 찾았고, 그 장소에 대해 무엇을 묻는지가 분명할 때.
// 질문 틀은 증미역 둘레나 전체 건물을 묻는 것이라, 다른 장소의 층수나 주소를 물으면 엉뚱한 틀에 걸린다 ('올리브영 주소' -> 증미역 주소)
const AREA = new Set(["road", "dong", "zone"]);   // 길, 동네, 우편구역을 묻는 것은 대개 '거기 있는 건물들'이라 질문 틀이 맡는다
export function prefer(plan, centerPath) {
  if (!plan.ents.length || plan.ents.every(e => AREA.has(e.type))) return false;
  const specific = plan.aspects.length || plan.absent.length || plan.ents.length === 2;
  if (plan.near) return plan.ents[0].path !== centerPath;   // 증미역 둘레 업종은 질문 틀이 표와 쿼리까지 보여 준다
  return !!specific;
}

// 장소 카드에 없는 칸은 그 장소가 들어 있는 건물의 카드에서 찾는다 (역 -> 역사 건물, 상호 -> 들어 있는 건물).
// 부르는 쪽은 장소 카드를 받은 뒤 이 경로들의 카드도 받아 cards에 넣는다
const VIA = ["역사 건물", "들어 있는 건물", "설치 주소의 건물"];
export const follow = card => (card && card.links || []).filter(l => VIA.includes(l.rel) && l.path).map(l => l.path);
// 상호 카드 없이 들어 있는 건물로 가는 장소 (사람이 넣은 장소). 카드는 건물 카드다
const inside = e => e.type === "business" && !!e.building && e.path === e.building;
const floorText = f => (/^\d+$/.test(f) ? `${f}층` : /^B(\d+)$/.test(f) ? `지하 ${f.slice(1)}층` : f === "지" ? "지하" : f);
const viaRel = (card, via) => ((card.links || []).find(l => l.path === via.path) || {}).rel || "건물";

// ---- 2) 답 만들기. cards: 장소 경로 -> card.json (없으면 null)
// 결과: { ok, sentences: [], evidence: [{label, value, note, path}], paths: [], how }
export function answer(plan, cards) {
  const out = { ok: false, sentences: [], evidence: [], paths: [], how: "" };
  const { ents, aspects, absent } = plan;
  if (!ents.length && !(plan.here && aspects.some(a => a.id === "dist"))) return out;
  out.ok = true;
  // 장소 둘레의 업종: 찾기 결과 가운데 그 장소에서 반경 안인 곳을 가까운 차례로
  if (plan.near && ents[0] && ents[0].pt) {
    const e = ents[0], { word, radius } = plan.near;
    const list = plan.near.items.map(x => ({ x, d: meters(e.pt, x.pt) })).filter(o => o.d <= radius).sort((a, b) => a.d - b.d);
    out.sentences.push(list.length ? `${e.name}에서 직선 ${nf(radius)}m 안에 '${word}'에 맞는 곳은 ${nf(list.length)}곳입니다. 가까운 곳부터 ${list.slice(0, 5).map(o => `${o.x.name}(${nf(o.d)}m)`).join(", ")}${list.length > 5 ? " 등" : ""}입니다.`
      : `${e.name}에서 직선 ${nf(radius)}m 안에는 '${word}'에 맞는 곳이 없습니다. 거리를 넓혀(예: 500m) 다시 물어 주세요.`);
    list.slice(0, 20).forEach(o => out.evidence.push({ label: `${nf(o.d)}m`, value: o.x.name + (o.x.cat ? ` (${o.x.cat})` : ""), path: o.x.path, link: true }));
    out.paths = [e.path, ...list.map(o => o.x.path)].filter(Boolean);
    out.how = `질문에서 ${e.name}과 '${word}'를 찾았습니다. 찾기 색인에서 '${word}'에 맞는 곳(업종이나 이름)을 모아 ${e.name}의 좌표에서 직선 ${nf(radius)}m 안인 곳만 남겼습니다.`;
    return out;
  }
  const askDist = aspects.some(a => a.id === "dist");
  // 거리: 두 장소, 또는 한 장소와 거리 기준점
  const pts = ents.filter(e => e.pt).map(e => ({ name: e.name, pt: e.pt, path: e.path }));
  if (askDist || (ents.length === 2 && !aspects.length && !absent.length)) {
    let a = null, b = null;
    if (pts.length >= 2) [a, b] = pts;
    else if (pts.length === 1 && (plan.here || plan.basis)) { a = plan.here || { name: plan.basis.n || "거리 기준", pt: plan.basis.pt }; b = pts[0]; }
    if (a && b) {
      const d = meters(a.pt, b.pt);
      out.sentences.push(`${a.name}에서 ${b.name}까지 직선 거리는 약 ${nf(d)}m입니다.`);
      out.sentences.push("걷는 길의 거리나 걸리는 시간은 이 자료에 없습니다.");
      out.evidence.push({ label: `${a.name} 좌표`, value: a.pt.map(x => x.toFixed(6)).join(", "), path: a.path || null });
      out.evidence.push({ label: `${b.name} 좌표`, value: b.pt.map(x => x.toFixed(6)).join(", "), path: b.path || null });
      out.paths = pts.map(p => p.path).filter(Boolean);
      out.how = "두 곳의 좌표(공개 범위 그래프의 대표점)로 직선 거리를 쟀습니다.";
      return out;
    }
    if (askDist && pts.length === 1) {
      out.sentences.push(`${josa(pts[0].name, "과/와")} 어디 사이의 거리인지 알 수 없습니다. 두 곳의 이름을 함께 적거나, 카드의 '거리 기준'으로 기준점을 고른 뒤 다시 물어 주세요.`);
      out.paths = [pts[0].path];
      out.how = "질문에서 장소를 하나만 찾았습니다.";
      return out;
    }
  }
  // 한 장소의 칸들 (둘을 찾았으면 둘 다 같은 것을 답한다)
  for (const e of ents) {
    const card = cards[e.path];
    out.paths.push(e.path);
    if (!card) { out.sentences.push(`${e.name}의 카드 자료를 받지 못했습니다.`); continue; }
    const ins = inside(e);
    const name = e.type === "group" ? e.name : ins ? `${josa(e.name, "이/가")} 들어 있는 ${card.name}` : card.name || e.name;
    if (e.type === "group") out.sentences.push(`${josa(e.name, "은/는")} 여러 동이 모인 단지라, 대표 동(${card.name})의 카드로 답합니다.`);
    const facts = card.facts || [], links = card.links || [], missing = new Set(card.missing || []);
    const src = (card.sources && card.sources.object || []).join(", ");
    const fact = k => facts.find(f => f.k === k);
    let said = 0;
    // 자료에 아예 없는 것
    for (const n of absent) {
      out.sentences.push(missing.has(n) ? `${name}의 ${josa(n, "은/는")} 이 자료에 없습니다.` : `${josa(n, "은/는")} 이 지도의 자료에 없는 정보입니다.`);
      out.evidence.push({ label: "없는 정보", value: [...missing].join(", ") || n, path: e.path });
      said++;
    }
    for (const a of aspects) {
      if (a.id === "dist") continue;
      if (ins && a.id === "floors") {
        out.sentences.push(e.floor ? `${josa(e.name, "은/는")} ${floorText(e.floor)}에 있습니다.` : `${josa(e.name, "이/가")} 몇 층에 있는지는 이 자료에 없습니다.`);
        if (e.floor) out.evidence.push({ label: "층", value: floorText(e.floor), path: e.path });
      }
      if (a.keys) {
        const hit = a.keys.map(fact).filter(Boolean);
        if (hit.length) {
          out.sentences.push(`${name}의 ${hit.map(f => `${josa(f.k, "은/는")} ${f.v}`).join(", ")}입니다.`);
          hit.forEach(f => out.evidence.push({ label: f.k, value: f.v, note: f.note || "", path: e.path, src }));
          hit.filter(f => f.note).forEach(f => out.sentences.push(`(${f.k}: ${f.note})`));
          said++; continue;
        }
        const via = follow(card).map(p => cards[p]).find(c => c && a.keys.some(k => (c.facts || []).some(f => f.k === k)));
        if (via) {
          const vh = a.keys.map(k => (via.facts || []).find(f => f.k === k)).filter(Boolean);
          out.sentences.push(`${name}의 ${viaRel(card, via)} ${via.name}의 ${vh.map(f => `${josa(f.k, "은/는")} ${f.v}`).join(", ")}입니다.`);
          vh.forEach(f => out.evidence.push({ label: `${f.k} (${via.name})`, value: f.v, note: f.note || "", path: via.path }));
          said++; continue;
        }
        const gone = a.keys.filter(k => missing.has(k));
        if (gone.length) { out.sentences.push(`${name}의 ${josa(gone.join(", "), "은/는")} 자료에 값이 없습니다.`); out.evidence.push({ label: "없는 정보", value: gone.join(", "), path: e.path }); said++; continue; }
      }
      if (a.rels) {
        let hit = links.filter(l => a.rels.includes(l.rel));
        let viaName = "";
        if (!hit.length) {
          const via = follow(card).map(p => cards[p]).find(c => c && (c.links || []).some(l => a.rels.includes(l.rel)));
          if (via) { hit = via.links.filter(l => a.rels.includes(l.rel)); viaName = `${viaRel(card, via)} ${via.name}`; }
        }
        if (hit.length) {
          if (a.count) out.sentences.push(`${name}의 ${josa(a.label, "은/는")} ${nf(hit.length)}곳입니다.`);
          else {
            const by = new Map(); hit.forEach(l => { if (!by.has(l.rel)) by.set(l.rel, []); by.get(l.rel).push(l.name); });
            const subj = rel => (/^들어 있는/.test(rel) ? `${josa(name, "이/가")} ${rel}` : /^위에 선/.test(rel) ? `${name} ${rel}` : `${name}의 ${rel}`);
            out.sentences.push([...by].map(([rel, ns]) => `${josa(subj(rel), "은/는")} ${ns.slice(0, 6).join(", ")}${ns.length > 6 ? ` 외 ${nf(ns.length - 6)}곳` : ""}입니다.`).join(" "));
          }
          if (viaName) out.sentences.push(`(${name}의 ${viaName} 카드에서 찾았습니다.)`);
          hit.slice(0, 8).forEach(l => out.evidence.push({ label: viaName ? `${l.rel} (${viaName})` : l.rel, value: l.name, path: l.unit === "group" ? null : l.path, link: true }));
          said++; continue;
        }
        if (a.id === "building" && (e.type === "place" || e.type === "group") && (e.field === "jibun" || e.field === "addr")) {
          out.sentences.push(`${e.said}에 있는 건물은 ${name}입니다.`); out.evidence.push({ label: e.field === "jibun" ? "지번" : "주소", value: e.field === "jibun" ? card.jibun : card.addr, path: e.path }); said++; continue;
        }
        if (!a.field) { out.sentences.push(`${name}에는 이어진 ${josa(a.label, "이/가")} 없습니다.`); said++; continue; }
      }
      if (a.field === "addr") {
        if (ins) {
          out.sentences.push(`${josa(e.name, "은/는")} ${card.name}${e.floor ? ` ${floorText(e.floor)}` : ""}에 있습니다.${card.addr ? ` 주소는 ${card.addr}${card.jibun ? `, 지번은 ${card.jibun}` : ""}입니다.` : ""}`);
          out.evidence.push({ label: "들어 있는 건물", value: card.name, path: e.path, link: true });
          if (card.addr) out.evidence.push({ label: "도로명주소", value: card.addr, path: e.path, src });
          if (card.jibun) out.evidence.push({ label: "지번", value: card.jibun, path: e.path, src });
          if (e.src && e.src.note) out.evidence.push({ label: `${e.name} 출처`, value: e.src.note + (e.src.checked ? ` (${e.src.checked} 확인)` : ""), path: null });
        } else if (card.addr) { out.sentences.push(`${name}의 주소는 ${card.addr}입니다.${card.jibun ? ` 지번은 ${card.jibun}입니다.` : ""}`); out.evidence.push({ label: "도로명주소", value: card.addr, path: e.path, src }); if (card.jibun) out.evidence.push({ label: "지번", value: card.jibun, path: e.path, src }); }
        else if (card.jibun) { out.sentences.push(`${name}에는 도로명주소가 없고, 지번은 ${card.jibun}입니다.`); out.evidence.push({ label: "지번", value: card.jibun, path: e.path, src }); }
        else out.sentences.push(`${name}의 주소는 자료에 없습니다.`);
        // 상호는 들어 있는 건물도 함께 (상호 카드의 '들어 있는 건물')
        const inB = !ins && e.type === "business" && links.find(l => l.rel === "들어 있는 건물");
        if (inB) { out.sentences.push(`${josa(card.name || e.name, "이/가")} 들어 있는 건물은 ${inB.name}입니다.`); out.evidence.push({ label: "들어 있는 건물", value: inB.name, path: inB.path, link: true }); }
        // '어디에 있어'에는 가장 가까운 역도 (그 장소가 역이면 빼고)
        const st = plan.where && e.pt && e.type !== "station" && (plan.stations || []).map(x => ({ ...x, d: meters(e.pt, x.pt) })).sort((x, y) => x.d - y.d)[0];
        if (st) { out.sentences.push(`가장 가까운 역은 ${st.name}으로, 직선 약 ${nf(st.d)}m입니다.`); out.evidence.push({ label: "가까운 역", value: `${st.name} ${nf(st.d)}m`, path: st.path, link: true }); out.paths.push(st.path); }
        said++; continue;
      }
      if (a.field === "jibun") {
        out.sentences.push(card.jibun ? `${name}의 지번은 ${card.jibun}입니다.` : `${name}의 지번은 자료에 없습니다.`);
        if (card.jibun) out.evidence.push({ label: "지번", value: card.jibun, path: e.path, src });
        said++; continue;
      }
      if (a.field === "officialNo") {
        const no = card.officialNo;
        out.sentences.push(no ? `${name}의 ${josa(no.label, "은/는")} ${no.value}입니다. 이 번호를 그대로 고유 주소에 씁니다.` : `${name}에는 공식 번호가 없습니다.`);
        if (no) out.evidence.push({ label: no.label, value: no.value, path: e.path });
        if (card.uri) out.evidence.push({ label: "고유 주소", value: card.uri, path: e.path });
        said++; continue;
      }
      if (a.field === "sources") {
        const s = card.sources || {};
        const all = [...new Set([...(s.object || []), ...(s.shape || [])])];
        if (ins && e.src && e.src.note) {
          out.sentences.push(`${josa(e.name, "은/는")} 공공 자료가 아니라 직접 넣은 장소이고, 근거는 ${e.src.note.replace(/^직접 넣은 장소,?\s*(출처:\s*)?/, "")}${e.src.checked ? `(${e.src.checked} 확인)` : ""}입니다.`);
          out.evidence.push({ label: `${e.name} 출처`, value: e.src.note + (e.src.url ? ` ${e.src.url}` : ""), path: null });
        }
        out.sentences.push(all.length ? `${name}의 자료는 ${all.join(", ")}에서 왔습니다.` : `${name}의 출처는 카드에 적혀 있지 않습니다.`);
        all.forEach(x => out.evidence.push({ label: "출처", value: x, path: e.path }));
        said++; continue;
      }
      if (a.field === "status" && card.status) { out.sentences.push(`${name}의 상태는 ${card.status}입니다.`); out.evidence.push({ label: "상태", value: card.status, path: e.path }); said++; continue; }
    }
    const unanswered = aspects.filter(a => a.id !== "dist");
    if (!said && unanswered.length) out.sentences.push(`${name}의 카드에는 ${josa(unanswered.map(a => a.label).join(", "), "이/가")} 없습니다.`);
    if (!said) {
      // 무엇을 묻는지 모르면 질문과 가장 많이 겹치는 칸을 근거로 고른다. 그것도 없으면 카드 요약
      const rest = plan.q.replace(e.said, " ");
      const pass = [
        ...facts.map(f => ({ label: f.k, value: f.v, note: f.note || "", text: `${f.k} ${f.v}` })),
        ...(card.addr ? [{ label: "도로명주소", value: card.addr, text: `주소 ${card.addr}` }] : []),
        ...links.map(l => ({ label: l.rel, value: l.name, text: `${l.rel} ${l.name}`, link: true, lp: l.path })),
      ].map(p => ({ ...p, s: overlap(rest, p.text) })).sort((x, y) => y.s - x.s);
      const good = pass.filter(p => p.s >= 0.34).slice(0, 3);
      const pick = good.length ? good : pass.filter(p => !p.link).slice(0, 4);
      out.sentences.push(good.length ? `${name}에서 질문과 가장 가까운 칸입니다: ${good.map(p => `${p.label} ${p.value}`).join(", ")}.`
        : unanswered.length ? `대신 카드의 주요 칸입니다: ${pick.map(p => `${p.label} ${p.value}`).join(", ")}.`
        : `${josa(name, "을/를")} 찾았지만 무엇을 묻는지 알아듣지 못해 카드의 주요 칸을 보여 드립니다: ${pick.map(p => `${p.label} ${p.value}`).join(", ")}.`);
      pick.forEach(p => out.evidence.push({ label: p.label, value: p.value, note: p.note || "", path: p.link ? p.lp : e.path, link: !!p.link }));
    }
  }
  const found = ents.map(e => `'${e.said}'에서 ${e.name}(${e.field === "number" ? "공식 번호" : `찾기 점수 ${Math.round(e.score)}`})`).join(", ");
  const asked = [...aspects.filter(a => a.id !== "dist").map(a => a.label), ...absent].join(", ");
  out.how = `질문에서 ${found}을 찾았고, ${asked ? `'${asked}'을 묻는 것으로 읽었습니다` : "무엇을 묻는지는 정하지 못했습니다"}. 그 장소의 카드 자료(공개 범위 그래프에서 빌드 때 만든 card.json)에서 근거 칸을 골라 정해 둔 문장 틀에 넣었습니다.`;
  return out;
}
