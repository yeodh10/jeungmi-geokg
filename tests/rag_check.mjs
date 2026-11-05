// 그래프에서 찾아 답하기 시험 (web/app/ask-rag.js). 화면 없이 공개 자료와 카드 파일로 돌린다. 모델과 API는 쓰지 않는다.
// 실행: node tests/rag_check.mjs [사이트 폴더]   (기본: data/processed/site, appdata가 만든 공개 사이트)
import { readFileSync, existsSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const site = resolve(process.argv[2] || join(root, "data/processed/site"));
const core = await import(pathToFileURL(join(root, "web/app/search-core.js")).href);
const rag = await import(pathToFileURL(join(root, "web/app/ask-rag.js")).href);
const read = f => JSON.parse(readFileSync(f, "utf8"));
const index = core.createIndex(read(join(site, "assets/data/places.json")));
core.addBusinesses(index, read(join(site, "assets/data/businesses.json")));
const card = p => { const f = join(site, "id", p, "card.json"); return existsSync(f) ? read(f) : null; };

function ask(q, basis = null) {
  const plan = rag.read(core, index, q, { basis });
  const cards = Object.fromEntries(plan.ents.map(e => [e.path, card(e.path)]));
  for (const c of Object.values(cards)) for (const p of rag.follow(c)) cards[p] = card(p);
  return { plan, a: rag.answer(plan, cards) };
}

const cases = JSON.parse(readFileSync(join(root, "tests/rag_cases.json"), "utf8")).cases;
let fail = 0;
for (const c of cases) {
  const { plan, a } = ask(c.q, c.basis || null);
  const text = a.sentences.join(" ");
  let ok = true;
  if (c.none) ok = !a.ok;
  else {
    ok = a.ok;
    if (ok && c.ents) ok = JSON.stringify(plan.ents.map(e => e.name)) === JSON.stringify(c.ents);
    if (ok && c.has) ok = c.has.every(h => text.includes(h));
    if (ok && c.not) ok = c.not.every(h => !text.includes(h));
    if (ok && c.evidence) ok = a.evidence.length > 0;
  }
  if (!ok) fail++;
  console.log(`${ok ? "맞음" : "틀림"}  '${c.q}' -> [${plan.ents.map(e => e.name).join(", ")}] ${text.slice(0, 110)}`);
}
console.log(fail ? `\n실패 ${fail}건` : "\n통과");
process.exit(fail ? 1 : 0);
