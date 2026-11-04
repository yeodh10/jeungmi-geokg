// 키 없는 질문 (묻기 탭 입력칸). 화면 없이 돌아가는 순수 함수만 둔다 (node tests/ask_check.mjs가 같은 파일을 쓴다).
// 자료는 빌드 때 만든 assets/data/ask.json이다 (src/graph/askforms.py): 질문 틀, 빈칸이 받을 수 있는 값,
// 빈칸 조합마다 사람이 쓴 쿼리를 공개 범위 그래프에서 실행한 결과.
//   1) 입력에서 빈칸 값을 찾는다. 수는 단위로(500m, 15층, 1980년, 2개, 3동, 1000세대), 이름은 사전으로(용도, 법정동, 길, 업종, 이름 낱말)
//   2) 찾은 값을 자리표 글자로 바꾸고, 틀의 말투들과 두 글자 조각 유사도(Dice)를 잰다
//   3) 가장 가까운 틀의 그 빈칸 조합 결과를 돌려준다
// 짐작하지 않는다: 점수가 낮으면 못 알아들었다고 하고, 틀이 받지 못하는 조건(다른 빈칸, '이하' 같은 반대 비교)이나
// 받을 수 없는 값(350m)이 있으면 답하지 않고 까닭과 고를 수 있는 값을 돌려준다. 빈칸을 말하지 않으면 틀의 기본값을 쓰고 그렇게 적는다.

export const THRESHOLD = 0.36;   // 이보다 낮으면 못 알아들은 것으로 본다 (tests/ask_cases.json으로 정함)

const TOKEN = { dist: "", floors: "", use: "", year: "", entrances: "", buildings: "",
                dong: "", road: "", cat: "", word: "", households: "" };
export const LABEL = { dist: "거리", floors: "지상 층수", use: "용도", year: "사용승인 연도", entrances: "출입구 수", buildings: "건물 수",
                       dong: "법정동", road: "길 이름", cat: "업종", word: "이름 낱말", households: "세대수" };
const UNIT = { dist: "m", floors: "층", year: "년", entrances: "개", buildings: "동", households: "세대" };
// 비교: 틀의 쿼리는 수 빈칸에 '이상'(>=), 연도에 '보다 이른'(<), 거리에 '안'을 쓴다
const MAXED = new Set(["floors", "entrances", "buildings"]);
const GE = new Set(["floors", "entrances", "buildings", "households"]);
const MORE = /^(초과|넘는|넘게|넘은|넘어|보다많|보다높|보다큰)/;
const LESS = /^(이하|미만|보다적|보다낮|보다작|아래|안되는|안넘는|밑)/;
const LATER = /^(이후|후에|후로|부터|보다늦|넘어서|뒤에|뒤로)/;
const UNTIL = /^(까지|이전까지|전까지)/;
const OUTSIDE = /^(밖|바깥|넘게떨어|이상떨어|넘어서)/;
const KNUM = { 한: 1, 두: 2, 세: 3, 네: 4, 다섯: 5, 여섯: 6, 일곱: 7, 여덟: 8, 아홉: 9, 열: 10 };

const strip = s => String(s || "").normalize("NFC").toLowerCase().replace(/[\s.,!?~'"“”‘’()\[\]{}:;\u00b7/\\-]+/g, "");

// 받침에 따라 조사를 고른다 ('거리를', '업종을', '500m를', '15층을', '300m로', '15층으로'). 영문 단위 m은 '미터'로 읽는다
const DIGIT_END = { 0: "ㅇ", 1: "ㄹ", 2: "", 3: "ㅁ", 4: "", 5: "", 6: "ㄱ", 7: "ㄹ", 8: "ㄹ", 9: "" };
function final(word) {
  const c = String(word).trim().slice(-1);
  if (/[0-9]/.test(c)) return DIGIT_END[c];
  if (/[a-z]/i.test(c)) return "";
  const code = c.charCodeAt(0) - 0xac00;
  if (code < 0 || code > 11171) return "";
  const j = code % 28;
  return j === 0 ? "" : j === 8 ? "ㄹ" : "ㅇ";
}
export function josa(word, pair) {
  const f = final(word);
  if (pair === "으로") return word + (f && f !== "ㄹ" ? "으로" : "로");
  const [withF, without] = { 을: ["을", "를"], 이: ["이", "가"], 은: ["은", "는"] }[pair];
  return word + (f ? withF : without);
}
export const UNITS = { dist: "m", floors: "층", year: "년", entrances: "개", buildings: "동", households: "세대" };
export const valueText = (kind, v) => fmt(kind, v) + (UNITS[kind] || "");

export function fmt(kind, v) {
  return kind === "households" && typeof v === "number" ? v.toLocaleString("ko-KR") : String(v);
}
export function fillText(text, values) {
  return String(text).replace(/\$\{([a-z]+)\}/g, (m, k) => (k in values ? fmt(k, values[k]) : m));
}
// 말투를 화면에 보일 때: 빈칸 자리에 단위를 붙인 값을 넣는다 ('${dist} 안 ${cat}' -> '300m 안 편의점')
export function fillForm(text, values) {
  return String(text).replace(/\$\{([a-z]+)\}/g, (m, k) => (k in values ? valueText(k, values[k]) : m));
}
export function fillQuery(text, values) {
  return String(text).replace(/\$\{([a-z]+)\}/g, (m, k) => (k in values ? String(values[k]) : m));
}

// 사전: 이름 빈칸 값과 그 별칭. 업종은 '김밥/만두/분식'처럼 묶은 이름이라 조각('분식')도 받되, 두 업종에 걸치는 조각은 버린다
function dictionary(slots) {
  const out = new Map();   // 붙여 쓴 글 -> [{kind, value}]
  const add = (text, kind, value) => {
    const k = strip(text);
    if (k.length < 2) return;
    const list = out.get(k) || [];
    if (!list.some(x => x.kind === kind && x.value === value)) list.push({ kind, value });
    out.set(k, list);
  };
  for (const kind of ["use", "dong", "road", "cat", "word"]) for (const v of (slots[kind] || {}).values || []) add(v, kind, v);
  const parts = new Map();
  for (const v of (slots.cat || {}).values || []) {
    for (const part of String(v).split(/[\/,]\s*/)) {
      const p = part.trim();
      if (p.length < 2 || p === v) continue;
      const set = parts.get(p) || new Set();
      set.add(v);
      parts.set(p, set);
    }
  }
  for (const [p, set] of parts) if (set.size === 1) add(p, "cat", [...set][0]);
  return out;
}

export function prepare(ask) {
  const dict = dictionary(ask.slots || {});
  const byFirst = new Map();
  for (const key of dict.keys()) {
    const list = byFirst.get(key[0]) || [];
    list.push(key);
    byFirst.set(key[0], list);
  }
  for (const list of byFirst.values()) list.sort((a, b) => b.length - a.length);
  const items = ask.items.map(it => {
    const shapes = it.forms.map(f => readForm(f));
    return { ...it, kinds: new Set(it.slots), shapes, literal: shapes.join("|") };
  });
  // 두 글자 조각의 무게: 여러 틀에 두루 나오는 조각('증미', '건물')은 가볍게, 한 틀에만 나오는 조각('우편')은 무겁게.
  // 어느 틀에도 없는 조각(입력에만 있는 '날씨')은 가장 무겁게 센다
  const df = new Map();
  for (const it of items) for (const g of new Set(it.shapes.flatMap(s => [...bigrams(s).keys()]))) df.set(g, (df.get(g) || 0) + 1);
  const N = items.length;
  const weight = g => Math.log(1 + N / (df.get(g) || 1));
  return { ask, dict, byFirst, items, weight };
}

// 말투 하나를 자리표 글자로: ${dist} -> 자리표, 나머지는 붙여 쓴 글
function readForm(form) {
  let s = "";
  let last = 0;
  String(form).replace(/\$\{([a-z]+)\}/g, (m, k, at) => {
    s += strip(form.slice(last, at)) + (TOKEN[k] || "");
    last = at + m.length;
    return m;
  });
  return s + strip(form.slice(last));
}

// 입력에서 수 빈칸 찾기: 단위로 종류를 정하고, 바로 뒤의 비교 말을 읽는다
const NUM_RE = /(?<![0-9가-힣a-z])([0-9][0-9,]*(?:\.[0-9]+)?|한|두|세|네|다섯|여섯|일곱|여덟|아홉|열)\s*(km|킬로미터|킬로|m|미터|메터|층|년도|년|개|동|세대)(?![a-z])/gi;
const YEAR_RE = /(?<![0-9])((?:19|20)[0-9]{2})(?=\s*(?:이전|전에|전|보다|까지))/g;
const BARE_RE = /(?<![0-9가-힣a-z.])([0-9][0-9,]*)(?![0-9.,])/gi;
const BEFORE = [[/세대(수)?(가|는|이)?$/, "households"], [/(층수|층)(가|는|이)?$/, "floors"], [/(출입구|입구)(가|는|수가|수)?$/, "entrances"],
                [/(반경|거리)(가|는)?$/, "dist"], [/(사용승인|준공|사용승인일)(이|은|는|가)?$/, "year"]];

function numbers(text) {
  const hits = [];
  const s = String(text).normalize("NFC");
  for (const m of s.matchAll(NUM_RE)) {
    const raw = m[1], unit = m[2].toLowerCase();
    let v = raw in KNUM ? KNUM[raw] : Number(raw.replace(/,/g, ""));
    if (!Number.isFinite(v)) continue;
    let kind;
    if (/^(km|킬로미터|킬로)$/.test(unit)) { kind = "dist"; v *= 1000; }
    else if (/^(m|미터|메터)$/.test(unit)) kind = "dist";
    else if (unit === "층") kind = "floors";
    else if (unit === "년" || unit === "년도") kind = "year";
    else if (unit === "개") kind = "entrances";
    else if (unit === "동") kind = "buildings";
    else if (unit === "세대") kind = "households";
    if (raw in KNUM && kind !== "entrances" && kind !== "buildings" && kind !== "floors") continue;
    hits.push({ kind, value: Math.round(v * 1000) / 1000, start: m.index, end: m.index + m[0].length, text: m[0] });
  }
  for (const m of s.matchAll(YEAR_RE)) {
    if (hits.some(h => m.index >= h.start && m.index < h.end)) continue;
    hits.push({ kind: "year", value: Number(m[1]), start: m.index, end: m.index + m[1].length, text: m[1] });
  }
  // 단위 없는 수: 바로 앞 낱말로 종류를 정한다 ('세대수가 1000 이상', '출입구 3 이상'). 앞 낱말이 없으면 빈칸으로 읽지 않는다
  for (const m of s.matchAll(BARE_RE)) {
    if (hits.some(h => m.index < h.end && m.index + m[0].length > h.start)) continue;
    const before = strip(s.slice(Math.max(0, m.index - 10), m.index));
    const hit = BEFORE.find(([re]) => re.test(before));
    if (!hit) continue;
    hits.push({ kind: hit[1], value: Number(m[1].replace(/,/g, "")), start: m.index, end: m.index + m[0].length, text: m[0] });
  }
  hits.sort((a, b) => a.start - b.start);
  for (const h of hits) h.after = strip(s.slice(h.end, h.end + 12));
  return { s, hits };
}

// 입력 읽기: 수 빈칸과 이름 빈칸을 찾아 [글자 또는 {hit}] 줄로 만든다
export function read(idx, text) {
  const { s, hits } = numbers(text);
  const parts = [];
  let last = 0;
  for (const h of hits) {
    parts.push(...strip(s.slice(last, h.start)));
    parts.push({ hit: { kinds: [{ kind: h.kind, value: h.value }], text: h.text, after: h.after } });
    last = h.end;
  }
  parts.push(...strip(s.slice(last)));
  // 이름 빈칸: 가장 긴 사전 낱말부터, 겹치지 않게
  const out = [];
  for (let i = 0; i < parts.length;) {
    const c = parts[i];
    if (typeof c !== "string") { out.push(c); i++; continue; }
    let found = null;
    for (const key of idx.byFirst.get(c) || []) {
      if (i + key.length > parts.length) continue;
      let ok = true;
      for (let j = 0; j < key.length; j++) if (parts[i + j] !== key[j]) { ok = false; break; }
      if (ok) { found = key; break; }
    }
    if (found) {
      out.push({ hit: { kinds: idx.dict.get(found), text: found } });
      i += found.length;
    } else { out.push(c); i++; }
  }
  return out;
}

function bigrams(s) {
  const a = Array.from("^" + s + "$");
  const m = new Map();
  for (let i = 0; i < a.length - 1; i++) {
    const g = a[i] + a[i + 1];
    m.set(g, (m.get(g) || 0) + 1);
  }
  return m;
}
export function dice(a, b, weight = () => 1) {
  const x = bigrams(a), y = bigrams(b);
  let common = 0, n = 0;
  for (const [g, c] of x) { const w = weight(g); n += c * w; if (y.has(g)) common += Math.min(c, y.get(g)) * w; }
  for (const [g, c] of y) n += c * weight(g);
  return n ? (2 * common) / n : 0;
}

// 틀 하나에 맞춰 읽은 입력: 빈칸마다 그 틀이 받는 종류를 고른다
function against(item, parts) {
  let s = "";
  const used = [];
  for (const c of parts) {
    if (typeof c === "string") { s += c; continue; }
    const pick = c.hit.kinds.find(k => item.kinds.has(k.kind)) || c.hit.kinds[0];
    // 이 틀이 받지 않는 종류의 이름인데 틀의 말투에 글자 그대로 있으면('아파트 단지') 빈칸이 아니라 글로 읽는다
    if (!item.kinds.has(pick.kind) && !("after" in c.hit) && item.literal.includes(c.hit.text)) { s += c.hit.text; continue; }
    s += TOKEN[pick.kind];
    used.push({ ...pick, text: c.hit.text, after: c.hit.after, fits: item.kinds.has(pick.kind) });
  }
  return { s, used };
}

function score(idx, item, parts) {
  const { s, used } = against(item, parts);
  let best = 0, form = item.forms[0];
  item.shapes.forEach((f, j) => { const d = dice(s, f, idx.weight); if (d > best) { best = d; form = item.forms[j]; } });
  const kinds = new Set(used.filter(u => u.fits).map(u => u.kind));
  const missing = item.slots.filter(k => !kinds.has(k)).length;
  const extra = used.filter(u => !u.fits).length;
  const bonus = 0.08 * kinds.size - 0.04 * missing - 0.12 * extra;
  return { item, raw: best, score: best + bonus, form, used };
}

const nearest = (list, v) => {
  const lo = [...list].reverse().find(x => x < v), hi = list.find(x => x > v);
  return [lo, hi].filter(x => x != null);
};

// 질문 하나 읽기. 돌려주는 것:
//   {ok:true, item, values, combo, notes, score, form}           답할 수 있음 (combo는 [결과 번호, 답 문장] 또는 {big})
//   {ok:false, why, message, similar:[{item, values, q}]}       답하지 않음 (why: empty, low, extra, op, range, many)
export function match(idx, text) {
  const parts = read(idx, text);
  const plain = parts.filter(c => typeof c === "string").join("");
  if (!parts.length) return { ok: false, why: "empty", message: "질문을 적어 주세요.", similar: [] };
  let ranked = idx.items.map(it => score(idx, it, parts)).sort((a, b) => b.score - a.score || b.raw - a.raw);
  const similar = ranked.slice(0, 3).map(r => suggestion(idx, r));
  // 빈칸 값만 적은 질문('편의점', '허준로')은 그 값을 모두 받는 틀이 하나뿐일 때만 그 틀로 읽는다
  const hits = parts.filter(c => typeof c !== "string");
  if (plain.length <= 1 && hits.length) {
    const fit = ranked.filter(r => r.used.length === hits.length && r.used.every(u => u.fits));
    if (fit.length === 1) ranked = [{ ...fit[0], score: Math.max(fit[0].score, THRESHOLD) }];
  }
  let top = ranked[0];
  if (!top || top.score < THRESHOLD) {
    // 조건이 많아 점수가 깎인 경우: 빈칸 감점 없이는 넘는 틀이 있으면 '넣을 수 없는 조건'으로 알린다
    const near = ranked.filter(r => r.used.some(u => !u.fits)).sort((a, b) => b.raw - a.raw)[0];
    if (near && near.raw + 0.08 * near.used.filter(u => u.fits).length >= THRESHOLD) top = near;
    else return { ok: false, why: "low", message: "질문을 알아듣지 못했습니다. 아래 비슷한 질문 가운데 하나를 고르거나, 다르게 적어 주세요.", similar, score: top && top.score };
  }
  const extra = top.used.filter(u => !u.fits);
  if (extra.length) {
    const what = extra.map(u => `${LABEL[u.kind]} '${u.text}'`).join(", ");
    return { ok: false, why: "extra", message: `가장 비슷한 질문 틀('${fillText(top.item.q, top.item.defaults)}')에는 ${what} 조건을 넣을 수 없어 답하지 않았습니다. 조건을 빼거나 아래 질문을 골라 주세요.`, similar, score: top.score };
  }
  const values = { ...top.item.defaults };
  const notes = [];
  const seen = new Set();
  for (const u of top.used) {
    if (seen.has(u.kind)) {
      return { ok: false, why: "many", message: `${josa(LABEL[u.kind], "이")} 둘 이상 적혀 있습니다. 하나만 적어 주세요.`, similar, score: top.score };
    }
    seen.add(u.kind);
    let v = u.value;
    const after = u.after || "";
    if (GE.has(u.kind)) {
      if (LESS.test(after)) return { ok: false, why: "op", message: `'${u.text}' 이하나 미만을 묻는 질문 틀은 없습니다. ${josa(LABEL[u.kind], "은")} '${valueText(u.kind, v)} 이상'처럼 이상으로만 찾을 수 있습니다.`, similar, score: top.score };
      if (MORE.test(after)) { v += 1; notes.push(`'${u.text}'보다 큰 값을 물어 '${valueText(u.kind, v)} 이상'으로 찾았습니다.`); }
    } else if (u.kind === "year") {
      if (LATER.test(after)) return { ok: false, why: "op", message: `'${u.text}' 이후를 묻는 질문 틀은 없습니다. 사용승인 연도는 '${valueText(u.kind, v)} 이전'처럼 이전으로만 찾을 수 있습니다.`, similar, score: top.score };
      if (UNTIL.test(after)) { v += 1; notes.push(`'${u.text}까지'를 물어 '${v}년 1월 1일보다 이른'으로 찾았습니다.`); }
    } else if (u.kind === "dist") {
      if (OUTSIDE.test(after)) return { ok: false, why: "op", message: `'${u.text}' 밖을 묻는 질문 틀은 없습니다. 거리는 증미역에서 '${valueText(u.kind, v)} 안'처럼 안쪽으로만 찾을 수 있습니다.`, similar, score: top.score };
    }
    const allowed = ((idx.ask.slots[u.kind] || {}).values) || [];
    // 층수, 출입구 수, 필지의 건물 수는 그래프의 가장 큰 값까지 모두 실행해 두었다. 그보다 크면 결과가 없다고 말할 수 있다
    if (MAXED.has(u.kind) && allowed.length && v > allowed[allowed.length - 1]) {
      const top1 = allowed[allowed.length - 1];
      return { ok: false, why: "range", message: `이 범위에서 ${LABEL[u.kind]}의 가장 큰 값은 ${valueText(u.kind, top1)}입니다. ${valueText(u.kind, v)} 이상은 없습니다.`,
               similar: [{ item: top.item, values: { ...values, [u.kind]: top1 } }].map(s => ({ ...s, q: fillText(s.item.q, s.values) })), score: top.score };
    }
    if (!allowed.includes(v)) {
      const near = typeof v === "number" ? nearest(allowed, v) : [];
      const choices = near.length ? ` 가까운 값은 ${near.map(x => fmt(u.kind, x) + (UNIT[u.kind] || "")).join(", ")}입니다.` : "";
      const range = typeof v === "number" && allowed.length ? ` 고를 수 있는 값은 ${fmt(u.kind, allowed[0])}${UNIT[u.kind] || ""}부터 ${fmt(u.kind, allowed[allowed.length - 1])}${UNIT[u.kind] || ""}까지${u.kind === "dist" ? " 100m 단위" : u.kind === "households" ? " 100세대 단위" : ""}입니다.` : "";
      return { ok: false, why: "range", message: `${LABEL[u.kind]} ${josa(valueText(u.kind, v), "은")} 미리 실행해 두지 않았습니다.${range}${choices}`,
               similar: near.map(x => ({ item: top.item, values: { ...values, ...Object.fromEntries(top.used.filter(w => w.fits && w.kind !== u.kind).map(w => [w.kind, w.value])), [u.kind]: x } }))
                 .map(s => ({ ...s, q: fillText(s.item.q, s.values) })), score: top.score };
    }
    values[u.kind] = v;
  }
  for (const k of top.item.slots) {
    if (!seen.has(k)) notes.push(`${josa(LABEL[k], "을")} 적지 않아 기본값 ${josa(valueText(k, values[k]), "으로")} 찾았습니다.`);
  }
  const key = top.item.slots.map(k => String(values[k])).join("|");
  const combo = top.item.combos[key];
  if (!combo) return { ok: false, why: "range", message: "이 조합은 미리 실행해 두지 않았습니다.", similar, score: top.score };
  return { ok: true, item: top.item, values, combo, notes, score: top.score, raw: top.raw, form: top.form };
}

function suggestion(idx, r) {
  const values = { ...r.item.defaults };
  for (const u of r.used) if (u.fits && ((idx.ask.slots[u.kind] || {}).values || []).includes(u.value)) values[u.kind] = u.value;
  return { item: r.item, values, q: fillText(r.item.q, values) };
}

// 결과 하나를 묻기 탭의 답 모양으로 (questions.json의 항목과 같은 칸). places는 places.json의 건물 행 (번호 i 순서)
export function answerOf(idx, m, places) {
  const it = m.item;
  const [ri, answer] = m.combo;
  const r = idx.ask.results[ri];
  const refOf = c => (typeof c === "number" ? (places[c] ? { p: places[c].p, n: places[c].n } : null) : c);
  return {
    id: it.id, q: fillText(it.q, m.values), answer, n: r.n,
    cols: it.cols.map(c => ({ key: c.key, label: c.label })),
    rows: r.rows.map(row => row.map((c, j) => (it.cols[j].ref ? refOf(c) : c))),
    map: r.map.map(c => (typeof c === "number" ? (places[c] || {}).p : c)).filter(Boolean),
    sparql: fillQuery(it.sparql, m.values), ...(it.from ? { from: it.from } : {}),
  };
}
