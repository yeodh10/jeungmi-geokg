// 찾기 1순위 시험 (명세 WP4 완료 기준 1, 11절). 화면 없이 web/app/search-core.js를 돌린다.
// 실행: node tests/search_check.mjs [자료 폴더]   (기본: data/processed/site/assets/data, appdata가 만든 공개 자료)
// 설계 27개(design)는 모두, 새 검색어 20개(new)는 16개 이상 맞아야 통과한다. 틀린 항목은 모두 적는다.
import { readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const dataDir = resolve(process.argv[2] || join(root, "data/processed/site/assets/data"));
const core = await import(pathToFileURL(join(root, "web/app/search-core.js")).href);
const read = name => JSON.parse(readFileSync(join(dataDir, name), "utf8"));
const cases = JSON.parse(readFileSync(join(root, "tests/search_cases.json"), "utf8"));

const places = read("places.json");
const index = core.createIndex(places);
core.addBusinesses(index, read("businesses.json"));

const show = it => (it ? `${it.name} (${it.type}${it.path ? " " + it.path : ""})` : "결과 없음");
function check(c) {
  const r = core.search(index, c.q);
  const top = r.items[0];
  const e = c.expect;
  let ok = !!top;
  if (ok && e.path) ok = top.path === e.path;
  if (ok && e.group) ok = top.type === "group" && top.name === e.group;
  if (ok && e.name) ok = top.name === e.name;
  if (ok && e.type) ok = top.type === e.type;
  if (ok && e.cat) ok = top.cat === e.cat;
  if (ok && e.nameIncludes) ok = top.name.includes(e.nameIncludes);
  return { q: c.q, ok, got: show(top), want: JSON.stringify(e) };
}

let fail = false;
const report = (title, list, need) => {
  const res = list.map(check);
  const n = res.filter(x => x.ok).length;
  console.log(`\n${title}: ${n}/${list.length} 맞음 (기준 ${need}개 이상)`);
  for (const x of res) if (!x.ok) console.log(`  틀림  '${x.q}' -> ${x.got}, 기대 ${x.want}`);
  if (n < need) fail = true;
  return n;
};
report("설계 27개", cases.design, cases.design.length);
report("새 검색어 20개", cases.new, 16);

// 안내 종류 (3.5절 빈 결과와 오해할 낱말)
console.log("\n안내 낱말");
for (const c of cases.notes || []) {
  const r = core.search(index, c.q);
  const got = r.note ? r.note.kind : "없음";
  const ok = got === c.kind;
  if (!ok) fail = true;
  console.log(`  ${ok ? "맞음" : "틀림"}  '${c.q}' -> ${got} (결과 ${r.items.length}줄)`);
}

// 칩 건수는 places.json에 적힌 n과 같아야 한다 (같은 규칙인지 확인)
console.log("\n칩 건수");
for (const ch of places.chips) {
  const items = core.chipItems(index, ch);
  const ok = items && items.length === ch.n;
  if (!ok) fail = true;
  console.log(`  ${ok ? "맞음" : "틀림"}  ${ch.label}: ${items ? items.length : "-"} (자료 ${ch.n})`);
}

// 그 밖의 약속: '양천로'에서 센서가 맨 앞이 아님, '백석초'는 한 줄
const yc = core.search(index, "양천로").items;
const bs = core.search(index, "백석초").items.filter(x => x.name.includes("백석초"));
const extra = [
  ["'양천로' 1위가 센서가 아님", yc[0] && yc[0].type !== "sensor"],
  ["'백석초'가 한 줄 (17동 묶음)", bs.length === 1 && bs[0].size === 17],
  ["공식 번호는 바로 가기", core.search(index, "1150010200106660040000001").direct === true],
];
// 초성 찾기와 한영 자판 오타
const top = q => core.search(index, q).items[0] || { name: "" };
const zm = core.search(index, "wmdaldur");
extra.push(
  ["초성 'ㅈㅁㅇ' 1위가 증미역", top("ㅈㅁㅇ").path === "station/1150010200106660040000001"],
  ["초성 섞인 '올ㄹㅂ' 1위가 '올리'로 시작", top("올ㄹㅂ").name.startsWith("올리")],
  ["치는 중인 '올립'도 '올리'로 시작하는 곳", top("올립").name.startsWith("올리")],
  ["자음 하나 'ㅇ'은 찾지 않음", core.search(index, "ㅇ").items.length === 0],
  ["'wmdaldur'는 증미역으로 바꿔 찾음", zm.typo && zm.typo.to === "증미역" && zm.items[0].path === "station/1150010200106660040000001"],
  ["'dhfflqmdud'는 올리브영", top("dhfflqmdud").name.includes("올리브영")],
  ["'gs25'는 바꾸지 않음", !core.search(index, "gs25").typo && top("gs25").name.startsWith("GS25")],
  ["자판 바꾸기: 겹받침이 다음 글자로 넘어감 (ekfrrhrl -> 닭고기)", core.engToKor("ekfrrhrl") === "닭고기"],
  ["자판 바꾸기: 겹모음 (rhkdtj -> 광서)", core.engToKor("rhkdtj") === "광서"],
  ["'olive'는 한글로 보지 않음", core.typoKor("olive") === null],
  ["초성 자리 표시 (ㅈㅁㅇ -> 증미역 앞 세 글자)", JSON.stringify(core.markRange("증미역", "ㅈㅁㅇ")) === "[0,3]"],
);
console.log("\n그 밖의 약속");
for (const [t, ok] of extra) { if (!ok) fail = true; console.log(`  ${ok ? "맞음" : "틀림"}  ${t}`); }

console.log(fail ? "\n실패" : "\n통과");
process.exit(fail ? 1 : 0);
