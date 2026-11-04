// 키 없는 질문 시험. 화면 없이 web/app/ask-match.js를 돌린다.
// 실행: node tests/ask_check.mjs [자료 폴더]   (기본: data/processed/site/assets/data, appdata가 만든 공개 자료)
// 1) 빈칸 기본값의 답이 questions.json(미리 실행한 답)과 칸마다 같다
// 2) 틀마다 말투(forms)에 기본값을 넣은 글이 그 틀로 읽힌다
// 3) tests/ask_cases.json: forms에 없는 말투가 맞는 틀과 빈칸 값으로 읽히고, 답하면 안 되는 질문은 답하지 않는다
import { readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { isDeepStrictEqual } from "node:util";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const dataDir = resolve(process.argv[2] || join(root, "data/processed/site/assets/data"));
const M = await import(pathToFileURL(join(root, "web/app/ask-match.js")).href);
const read = name => JSON.parse(readFileSync(join(dataDir, name), "utf8"));
const cases = JSON.parse(readFileSync(join(root, "tests/ask_cases.json"), "utf8"));

const ask = read("ask.json");
const places = read("places.json").places;
const questions = Object.fromEntries(read("questions.json").items.map(x => [x.id, x]));
const idx = M.prepare(ask);
let fail = 0;
const bad = msg => { fail++; console.log("  틀림  " + msg); };

console.log(`틀 ${ask.items.length}개, 결과 ${ask.results.length}개, 빈칸 조합 ${ask.items.reduce((n, it) => n + Object.keys(it.combos).length, 0).toLocaleString()}개`);
if (ask.built !== read("places.json").built) bad(`ask.json 빌드 값 ${ask.built}이 places.json과 다르다`);

// 1) 기본값의 답 = questions.json
for (const it of ask.items) {
  const q = questions[it.id];
  const key = it.slots.map(k => String(it.defaults[k])).join("|");
  const combo = it.combos[key];
  if (!q || !Array.isArray(combo)) { bad(`${it.id}: 기본값 조합 ${key}가 없다`); continue; }
  const a = M.answerOf(idx, { item: it, values: it.defaults, combo }, places);
  for (const k of ["q", "answer", "n", "cols", "rows", "map", "sparql"]) {
    if (!isDeepStrictEqual(a[k], q[k])) bad(`${it.id}: 기본값의 ${k}가 questions.json과 다르다`);
  }
}
console.log("기본값 답 대조 끝");

// 2) 말투에 기본값을 넣은 글
const UNIT = { dist: "m", floors: "층", year: "년", entrances: "개", buildings: "동", households: "세대" };
let forms = 0;
for (const it of ask.items) {
  for (const f of it.forms) {
    const text = f.replace(/\$\{([a-z]+)\}/g, (m, k) => String(it.defaults[k]) + (UNIT[k] || ""));
    const r = M.match(idx, text);
    forms++;
    if (!r.ok || r.item.id !== it.id) bad(`말투 '${text}' -> ${r.ok ? r.item.id : r.why} (기대 ${it.id})`);
  }
}
console.log(`말투 ${forms}개 확인`);

// 3) 새 말투와 답하면 안 되는 질문
let n = 0;
for (const c of cases.answer) {
  const r = M.match(idx, c.q);
  const want = { ...(ask.items.find(x => x.id === c.id) || {}).defaults, ...(c.values || {}) };
  const ok = r.ok && r.item.id === c.id && isDeepStrictEqual(r.values, want);
  if (ok) n++;
  else bad(`'${c.q}' -> ${r.ok ? `${r.item.id} ${JSON.stringify(r.values)}` : `${r.why}: ${r.message}`} (기대 ${c.id} ${JSON.stringify(want)}, 점수 ${(r.score || 0).toFixed(2)})`);
}
console.log(`새 말투 ${n}/${cases.answer.length} 맞음`);
let m = 0;
for (const c of cases.refuse) {
  const r = M.match(idx, c.q);
  if (!r.ok && r.why === c.why) m++;
  else bad(`'${c.q}' -> ${r.ok ? `답함 ${r.item.id}` : r.why} (기대 ${c.why}, 점수 ${(r.score || 0).toFixed(2)})`);
}
console.log(`답하지 않을 질문 ${m}/${cases.refuse.length} 맞음`);

if (fail) { console.log(`\n실패 ${fail}건`); process.exit(1); }
console.log("\n키 없는 질문 확인 통과");
