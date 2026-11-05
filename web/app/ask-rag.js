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
  ["전화번호", /전화|연락처/], ["리뷰", /리뷰|후기|평점|별점|맛있/], ["사진", /사진(?!관|촬영|스튜디오|인화)|이미지/],
  ["시간표", /시간표|첫차|막차|배차/], ["출구 번호", /출구/], ["도착 정보", /도착|언제\s*와/], ["가격", /가격|얼마야|비싸|싸[요다게니]/],
];
const ASK_WORDS = /^(가장|제일|젤|있는|있을까|있나|있니|어딨어|어딨|찾아줘|추천|추천해줘|먹을|중|중에|중에서|몇|언제|어디|어디야|어디에|뭐|뭐야|무슨|어느|어떤|얼마나|알려줘|알려|줘|좀|이야|인가요|인가|이에요|예요|야|있어|있나요|해줘|말해줘|궁금해|정도|대략|약|혹시|그|이|저|거기|무엇|무엇인가요)$/;
// 장소 이름이 될 수 없는 흔한 낱말 (이것만으로 된 묶음은 장소로 찾지 않는다)
const GENERIC = /^(?:집|데|곳|것|거|군데|개|건물|장소|가게|상가|위|아래|안|밖|근처|주변|둘레|부근|옆|앞|뒤|층|동|길|거리|주소|역|번호|정보|자료|이름|여기|거기|저기|어디쯤)(?:은|는|이|가|을|를|에|도|이야|야|의)?$/;
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
export const josa = (w, pair) => {
  const [a, b] = pair.split("/");
  // '으로/로'는 ㄹ 받침 뒤에서 '로' ('오니테이블로')
  if (pair === "으로/로") { const c = String(w || "").replace(/[\s)\]'"’”]+$/, "").slice(-1).charCodeAt(0) - 0xac00; if (c >= 0 && c < 11172 && c % 28 === 8) return w + b; }
  return w + (hasJong(w) ? a : b);
};   // josa('증미역', '은/는')

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

// ---- 업종이나 종류로 찾기 ('가장 가까운 닭한마리 집', '국밥집 어디 있어', '근처 맛집')
// 있는 곳을 없다고 하면 안 되므로 넓게 찾는다: 상호 이름, 업종(대, 중, 소 분류), 바로가기 묶음(칩), 건물과 단지 이름.
// 공백을 빼고 견주며, '국밥집', '닭한마리 집'의 '집'처럼 가게를 뜻하는 꼬리는 떼고도 찾는다. '@이름'은 칩 묶음이다
const KIND_TAIL = /(전문점|매장|가게|집|들)$/;
const SYN = {
  맛집: ["@음식점"], 식당: ["@음식점"], 밥집: ["@음식점"], 음식점: ["@음식점"], 먹을곳: ["@음식점"], 먹을데: ["@음식점"], 레스토랑: ["@음식점"],
  커피: ["@카페", "커피"], 커피숍: ["@카페", "커피"], 커피집: ["@카페", "커피"], 찻집: ["@카페"], 카페: ["@카페"], 디저트: ["디저트", "@카페"],
  술집: ["주점", "호프", "포차", "이자카야"], 호프집: ["호프", "주점"], 포차: ["포차", "주점"],
  햄버거: ["버거"], 햄버거집: ["버거"], 버거집: ["버거"],
  통닭: ["치킨", "통닭"], 치킨: ["치킨", "통닭"], 치킨집: ["치킨", "통닭"],
  빵집: ["빵", "베이커리", "제과"], 베이커리: ["빵", "베이커리", "제과"], 제과점: ["빵", "베이커리", "제과"],
  미용실: ["미용", "헤어"], 미장원: ["미용", "헤어"], 헤어샵: ["미용", "헤어"], 이발소: ["이용", "이발"], 이발관: ["이용", "이발"],
  세탁소: ["세탁"], 빨래방: ["세탁", "빨래"], 꽃집: ["꽃", "플라워"],
  마트: ["마트", "슈퍼"], 슈퍼: ["슈퍼", "마트"], 슈퍼마켓: ["슈퍼", "마트"],
  병원: ["@병원", "병원", "의원"], 의원: ["@병원"], 약국: ["@약국"], 편의점: ["@편의점"], 학원: ["@학원"],
  학교: ["@학교"], 아파트: ["@아파트 단지", "아파트"], 오피스텔: ["@오피스텔"],
  교회: ["@종교", "교회"], 성당: ["@종교", "성당"], 절: ["@종교", "사찰"], 종교시설: ["@종교"],
  헬스: ["헬스", "피트니스"], 헬스장: ["헬스", "피트니스"], 체육관: ["체육", "스포츠"],
  숙소: ["@숙박"], 모텔: ["모텔", "@숙박"], 호텔: ["호텔", "@숙박"], 주차장: ["주차"],
  은행: ["은행", "농협", "신협", "새마을금고"],
  부동산: ["부동산", "공인중개"], 중개사무소: ["공인중개", "부동산"], 공인중개사: ["공인중개", "부동산"],
};
// 음식 이름은 업종 소분류와 이어 둔다. 상호 이름이나 업종에 그 말이 없어도 이 업종인 곳은 '그 음식을 팔 수도 있는 곳'으로 따로 보여 준다
// (메뉴는 자료에 없으므로 판다고 말하지 않는다). 업종 이름은 상가정보 소분류 그대로다
const RELATED = [
  [/짜장|자장|짬뽕|탕수육|중국|중식|딤섬/, ["중국집", "마라탕/훠궈"]], [/마라|훠궈/, ["마라탕/훠궈"]],
  [/초밥|스시|사시미|횟집|^회$|회집|일식/, ["일식 회/초밥", "횟집"]], [/라멘|우동|소바/, ["일식 면 요리"]],
  [/돈가스|돈까스|카레|덮밥|규동/, ["일식 카레/돈가스/덮밥", "경양식"]], [/파스타|스테이크|양식|레스토랑/, ["파스타/스테이크", "경양식", "기타 서양식 음식점"]],
  [/피자/, ["피자"]], [/버거/, ["버거"]], [/쌀국수|베트남|분짜|반미|팟타이|태국|동남아/, ["베트남식 전문", "기타 동남아식 전문"]],
  [/떡볶이|김밥|만두|분식|라면|순대(?!국)/, ["김밥/만두/분식"]], [/삼겹|돼지|갈비|고기|고깃|목살|숯불/, ["돼지고기 구이/찜", "소고기 구이/찜"]],
  [/소고기|한우|등심|차돌/, ["소고기 구이/찜"]], [/닭|오리|백숙|삼계탕/, ["닭/오리고기 구이/찜", "치킨"]], [/족발|보쌈/, ["족발/보쌈"]],
  [/국밥|해장|순대국|순댓국|감자탕|찌개|설렁탕|곰탕|갈비탕|육개장|추어탕|뼈다귀/, ["국/탕/찌개류"]], [/칼국수|국수|수제비/, ["국수/칼국수"]],
  [/냉면|밀면/, ["냉면/밀면"]], [/곱창|막창|대창/, ["곱창 전골/구이"]], [/백반|한정식|가정식|한식/, ["백반/한정식", "기타 한식 음식점"]],
  [/샌드위치|토스트|샐러드/, ["토스트/샌드위치/샐러드"]], [/빙수|아이스크림/, ["아이스크림/빙수"]], [/떡집|한과/, ["떡/한과"]],
  [/맥주|호프|치맥/, ["생맥주 전문", "요리 주점"]], [/해산물|조개|대게|킹크랩|생선|아구|아귀|낙지|쭈꾸미|주꾸미/, ["해산물 구이/찜", "횟집"]],
  [/빵|도넛|베이글|케이크/, ["빵/도넛"]], [/뷔페/, ["뷔페"]], [/전집|부침개|파전|빈대떡/, ["전/부침개"]],
];
// 상호 이름이 자료마다 다르게 적힌 상표 (공공 상가정보는 'GS25'를 '지에스25'로, 'CU'를 '씨유'로도 적는다). 정규식은 이름 전체에 견준다
const BRAND = [
  [["gs25", "지에스25", "지에스편의점"], ["gs25", "지에스25"]], [["cu", "씨유"], ["씨유", "cu"]],
  [["세븐일레븐", "7-11", "711", "세븐"], ["세븐일레븐", "7-11", "코리아세븐", /^세븐.*점\s*코리아$/]], [["이마트24", "emart24"], ["이마트24", "emart24"]],
  [["배스킨라빈스", "베스킨라빈스", "배라"], ["배스킨라빈스", "베스킨라빈스"]], [["서브웨이", "써브웨이"], ["서브웨이", "써브웨이"]],
  [["bhc", "비에이치씨"], ["bhc", "비에이치씨"]], [["bbq", "비비큐"], ["bbq", "비비큐"]], [["메가커피", "메가엠지씨커피", "메가mgc커피", "메가"], ["메가엠지씨커피", "메가커피", "메가mgc"]],
  [["맥도날드", "맥도널드", "맥날"], ["맥도날드", "맥도널드"]], [["파리바게뜨", "파리바게트", "파바"], ["파리바게뜨", "파리바게트"]],
  [["투썸", "투썸플레이스"], ["투썸"]], [["스벅", "스타벅스"], ["스타벅스"]], [["버거킹"], ["버거킹"]], [["교촌", "교촌치킨"], ["교촌"]],
];
for (const [keys, terms] of BRAND) for (const k of keys) SYN[k] = [...(SYN[k] || []), ...terms];
const FOODISH = /음식|한식|중식|일식|양식|분식|치킨|주점|빵|국|탕|면|밥|고기|닭|회|초밥|피자|버거|떡|족발|보쌈|찌개|식당|짜장|짬뽕|마라|갈비|삼겹|곱창|냉면|국수|김밥|라멘|우동|돈가스|돈까스|파스타|쌀국수|해장/;
// 상가정보에 아예 없는 갈래. 이름으로 찾은 곳은 보이되, 없다고 말하지 않고 알 수 없다고 한다
const NOT_COVERED = [
  [/은행|금융|atm|현금인출|신협|새마을금고|증권|보험/i, "은행 같은 금융업"], [/우체국|주민센터|행정복지센터|구청|경찰|파출소|지구대|소방|보건소|세무서|관공서/, "관공서"],
  [/정류장|버스|화장실|공원|놀이터/, "정류장, 화장실, 공원 같은 시설"], [/주차/, "주차장"],
];
// 이름에 그 말이 들어 있어도 뜻이 다른 이름 (은행나무화실은 은행이 아니다)
const NAME_NOT = { 은행: /은행나무/, 학교: /교습소|수학교|학교앞|학교종/ };
// 찾은 곳 목록: [{name, path, pt, cat, addr, by}] (같은 곳은 한 번만, 좌표 없는 곳은 뺀다)
export function kindMatch(core, index, word) {
  const w0 = core.compact(word);
  if (!w0) return { terms: [], items: [] };
  const base = w0.length > 2 ? w0.replace(KIND_TAIL, "") : w0;
  const syn = [...(SYN[w0] || []), ...(SYN[base] || [])];
  let terms = [...new Set([w0, base, ...syn])];
  const isChip = t => typeof t === "string" && t.startsWith("@");
  const out = new Map();
  const notName = NAME_NOT[base] || NAME_NOT[w0] || null;
  const near = new Map();   // 같은 이름이 50m 안에 또 있으면 한 곳으로 (단지의 여러 동, 묶음과 건물이 겹칠 때)
  const put = (key, o) => {
    if (!o.pt || out.has(key) || (notName && o.by !== "업종" && notName.test(o.name))) return;
    const nm = core.compact(o.name), prev = near.get(nm) || [];
    if (prev.some(p => meters(p, o.pt) < 50)) return;
    near.set(nm, [...prev, o.pt]); out.set(key, o);
  };
  const scan = list => { for (const t of list) {
    if (t instanceof RegExp) {
      for (const d of index.docs) if (d.type === "business" && t.test(d.name)) put(d.path || d.building || d.name, { name: d.name, path: d.path || d.building, pt: d.pt, cat: d.cat || "", addr: d.addr || "", floor: d.floor || "", by: "이름" });
      continue;
    }
    if (isChip(t)) {
      const chip = (index.chips || []).find(c => c.label === t.slice(1));
      for (const it of (chip && core.chipItems(index, chip)) || []) put(it.path || it.name, { name: it.name, path: it.path, pt: it.pt, cat: it.cat || "", addr: it.addr || "", by: `묶음 '${chip.label}'` });
      continue;
    }
    const c = core.compact(t);
    if (c.length < 2 && !syn.includes(t)) continue;   // 한 글자는 비슷한 말 목록에 적은 것만
    for (const d of index.docs) {
      if (d.type === "business") {
        const byName = (d.nc || "").includes(c), byCat = (d.cc || "").includes(c) || (d.mc || "").includes(c);
        if (byName || byCat) put(d.path || d.building || d.name, { name: d.name, path: d.path || d.building, pt: d.pt, cat: d.cat || "", addr: d.addr || "", floor: d.floor || "", by: byName ? "이름" : "업종" });
      } else if ((d.type === "place" || d.type === "group") && !/^이름 없는/.test(d.name) && (d.nc || "").includes(c)) {
        put(d.g != null ? "g" + d.g : d.path, { name: d.name, path: d.path, pt: d.pt, cat: d.use || "", addr: d.addr || "", by: "건물 이름" });
      }
    }
  } };
  scan(terms);
  // 업종 이름은 꼬리를 떼고도 견준다 ('사진관' -> 업종 '사진 촬영업', '노래방' -> '노래연습장 운영업'). 업종 이름은 정해진 목록이라 이름보다 덜 헷갈린다
  const cs = w0.length > 2 ? w0.replace(/(전문점|매장|가게|관|점|집|소|방|장|샵|숍)$/, "") : "";
  if (cs.length >= 2 && cs !== w0) for (const d of index.docs) {
    if (d.type === "business" && ((d.cc || "").includes(cs) || (d.mc || "").includes(cs))) put(d.path || d.building || d.name, { name: d.name, path: d.path || d.building, pt: d.pt, cat: d.cat || "", addr: d.addr || "", floor: d.floor || "", by: "업종" });
  }
  // 그대로는 없으면 가게를 뜻하는 꼬리를 더 떼어 본다 ('안경점' -> 안경, '피아노학원' -> 피아노)
  let stem = w0;
  if (!out.size && w0.length > 2) {
    stem = w0.replace(/(점|샵|숍|학원|교습소|병원|의원|센터|클럽)$/, "");
    if (stem !== w0 && stem.length >= 2) { const more = [stem, ...(SYN[stem] || [])].filter(t => !terms.includes(t)); terms = [...terms, ...more]; scan(more); }
  }
  // 이어 둔 업종 (음식 이름일 때). 위에서 찾은 곳은 빼고
  const byCat = [...out.values()].some(o => o.by === "업종");
  const rel = byCat ? [] : [...new Set(RELATED.filter(([re]) => re.test(w0)).flatMap(([, cs]) => cs))].filter(c => !core.compact(c).includes(base));
  const related = [];
  if (rel.length) {
    const seen = new Set(out.keys());
    for (const d of index.docs) if (d.type === "business" && d.pt && rel.includes(d.cat) && !seen.has(d.path || d.building || d.name)) related.push({ name: d.name, path: d.path || d.building, pt: d.pt, cat: d.cat, addr: d.addr || "", by: "업종" });
  }
  const nc = NOT_COVERED.find(([re]) => re.test(w0));
  // 보여 줄 말: 적은 말 그대로 맞은 곳이 있으면 그대로('중국집'), 아니면 꼬리를 뗀 말('국밥집' -> 국밥)
  const said = [w0, base, stem].find(x => [...out.values()].some(o => core.compact(o.name).includes(x) || core.compact(o.cat).includes(x))) || base;
  return { word: said, byCat, terms, items: [...out.values()], rel, related, notCovered: nc ? nc[1] : null };
}
// 글자 하나둘이 다른 이름 (오타 짐작). 찾은 곳이 없을 때 '혹시 이것인가요'로만 쓴다
function lev(a, b) {
  const m = a.length, n = b.length; if (Math.abs(m - n) > 2) return 9;
  let prev = Array.from({ length: n + 1 }, (_, j) => j);
  for (let i = 1; i <= m; i++) { const cur = [i]; for (let j = 1; j <= n; j++) cur[j] = Math.min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1)); prev = cur; }
  return prev[n];
}
export function nearNames(core, index, word) {
  const w = core.compact(word);
  if (w.length < 3) return [];   // 두 글자는 한 글자만 달라도 뜻이 달라 짐작하지 않는다
  const lim = w.length >= 5 ? 2 : 1, seen = new Set(), out = [];
  for (const d of index.docs) {
    if (!d.nc || !d.pt || /^이름 없는/.test(d.name) || seen.has(d.name)) continue;
    // 이름 전체, 네 글자 넘으면 이름의 앞쪽도 ('올리브엉' -> 올리브영증미역점)
    const full = lev(w, d.nc), k = w.length >= 4 ? Math.min(full, lev(w, d.nc.slice(0, w.length))) : full;
    if (k <= lim) { seen.add(d.name); out.push({ name: d.name, path: d.path || d.building, k, full, type: d.type }); }
  }
  return out.sort((a, b) => a.k - b.k || a.full - b.full || (a.type === "business") - (b.type === "business")).slice(0, 3);
}
// 종류 낱말인가 ('카페', '주차장'): 이런 낱말은 장소 이름이 아니라 종류로 읽는다
export const isKindWord = (core, w) => !!SYN[core.compact(w)];
// 가게 종류를 묻는 말인가 (찾은 곳이 없어도 '없다'고 답할 만큼 분명한가)
const kindish = (core, w) => { const c = core.compact(w); return (/(집|가게|식당|전문점|매장|점|샵|숍|학원|방|소)$/.test(c) && c.length >= 2) || !!SYN[c]; };
const NEARW = /^(가까운|가까이|가깝|가장가까운|근처|주변|둘레|부근|인근|근방|쪽|앞|옆|건너편|맞은편|걸어갈|걸어서)/;
const SUPER = /(가장|제일|젤)\s*(가까|근처)|가까운\s*(데|곳)\s*(하나|한\s*곳)/;
const KIND_ASK = /어디|있어|있나|있니|있을까|몇\s*(곳|개|군데)|알려|추천|찾아|위치|목록|어딨|주소/;

// ---- 1) 질문 읽기: 장소와 묻는 것
// core: search-core.js 모듈, index: 찾기 색인, basis: 거리 기준점({pt, n}) 또는 null
export function read(core, index, text, { basis = null } = {}) {
  const q = strip(text);
  const toks = q.split(" ").filter(Boolean);
  // '공항대로 521'의 '로'는 조사가 아니다 (뒤에 번호가 오는 길 이름은 그대로 둔다)
  const words = toks.map((t, k) => (/(로|길)$/.test(t) && /^\d/.test(toks[k + 1] || "") ? t : bare(t)));
  let aspects = ASPECTS.filter(a => a.re && a.re.test(q));
  if (aspects.some(a => a.id === "building")) aspects = aspects.filter(a => a.id !== "use");   // '위에 무슨 건물'은 용도가 아니라 건물을 묻는다
  const absent = ABSENT.filter(([, re]) => re.test(q)).map(([n]) => n);
  // 장소 찾기: 이어진 낱말 묶음(긴 것부터)마다 찾기를 돌려 이름이 제대로 맞는 것만 받는다
  const cands = [];
  // '가까운', '근처' 같은 말이 있으면 기준 장소는 그 말 앞에서만 찾는다 (뒤는 찾는 종류: '증미역 근처 올리브영')
  const nearAt = words.findIndex(w => NEARW.test(w));
  const before = nearAt >= 0 ? nearAt : words.length;
  for (let len = Math.min(4, before); len >= 1; len--) {
    for (let i = 0; i + len <= before; i++) {
      const span = words.slice(i, i + len);
      if (span.some(w => ASK_WORDS.test(w) || w.length < 2 && !/\d/.test(w)) || span.every(w => GENERIC.test(w))) continue;
      const s0 = span.join(" ");
      if (ASPECTS.some(a => a.re && a.re.test(s0) && a.re.source.length > 0 && s0.replace(a.re, "").trim().length < 2)) continue;   // 묻는 말 자체 ('몇 층', '주소')
      if (len === 1 && SYN[core.compact(s0)] && !/(역|점)$/.test(s0)) continue;   // '주차장', '카페' 같은 종류 낱말은 장소 이름으로 받지 않는다 (같은 이름의 동이 있어도)
      // 'IT'는 자료에 '아이티'로 적힌 이름이 많다 ('강서IT밸리' -> 강서아이티밸리). 그대로 먼저 찾고, 맞지 않으면 바꿔 찾는다
      for (const s of /it/i.test(s0) ? [s0, s0.replace(/it/gi, "아이티")] : [s0]) {
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
      // 길 이름과 번호를 적었으면 그 번호가 그대로 맞아야 한다 ('양천로 58'이 '양천로 583'에 붙지 않게)
      const rn = s.match(/([가-힣\d]+(?:로|길))\s*(\d+(?:-\d+)?)/);
      const rt = top.field === "addr" ? top.addr : top.field === "name" ? top.name : null;
      if (strong && rn && rt && !new RegExp(core.compact(rn[1] + rn[2]) + "(?![0-9-])").test(core.compact(rt))) continue;
      // 여러 낱말이 함께 맞으면 앞세운다 ('등촌동 666-40'이 '등촌동'보다 먼저)
      if (strong) { cands.push({ i, len, item: top, typo: r.typo || null, score: top.score + len * 15 + (rn ? 30 : 0) }); break; }
      }
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
  // 종류로 찾기: 기준 장소(있으면 하나)를 뺀 낱말 가운데 업종, 이름, 묶음으로 맞는 것을 찾는다
  const nearMode = nearAt >= 0 || SUPER.test(q) || /\d+(?:\.\d+)?\s*(km|킬로|m|미터)\s*(안|이내|내|반경|거리)|반경\s*\d/i.test(q);
  let kind = null;
  {
    const tail = e => toks[e.i + e.len - 1] || "";
    const anchor = nearMode ? ents.slice().sort((a, b) => (/(에서|부터|기준)$/.test(tail(b)) ? 1 : 0) - (/(에서|부터|기준)$/.test(tail(a)) ? 1 : 0) || a.i - b.i)[0] || null : (ents.length === 1 ? ents[0] : null);
    const used = new Set(); if (anchor) for (let k = anchor.i; k < anchor.i + anchor.len; k++) used.add(k);
    const left = words.map((w, k) => ({ w, k })).filter(({ w, k }) => !used.has(k) && w && !ASK_WORDS.test(w) && !GENERIC.test(w) && !NEARW.test(w)
      && !/^\d/.test(w) && !/^(m|km|미터|킬로|킬로미터)$/i.test(w) && !ASPECTS.some(a => a.re && a.re.test(w)) && !ABSENT.some(([, re]) => re.test(w)));
    // 이웃한 두 낱말을 붙인 것('치킨 집' -> 치킨집)과 낱말 하나씩. 앞의 것부터 처음 맞는 것을 쓴다
    const tries = [];
    for (let k = 0; k + 1 < left.length; k++) if (left[k + 1].k === left[k].k + 1) tries.push({ w: left[k].w + left[k + 1].w, pair: true });
    left.forEach(x => tries.push({ w: x.w }));
    const askKind = nearMode || KIND_ASK.test(q);
    const onlyWhere = aspects.every(a => a.id === "addr" || a.id === "dist");
    if (askKind && tries.length && (nearMode || !ents.length || (ents.length === 1 && onlyWhere))) {
      let hit = null, miss = null;
      for (const { w, pair } of tries) {
        const m = kindMatch(core, index, w);
        if (pair && !m.items.length) continue;   // 붙인 말은 그대로 맞는 곳이 있을 때만 ('쪽에 짬뽕' -> '쪽에짬뽕'은 버린다)
        const items = anchor ? m.items.filter(x => x.path !== anchor.item.path) : m.items;
        const related = anchor ? m.related.filter(x => x.path !== anchor.item.path) : m.related;
        const got = { word: m.word || w, said: w, byCat: m.byCat, items, terms: m.terms, rel: m.rel, related, notCovered: m.notCovered };
        if (items.length || related.length) { hit = got; break; }
        if (!miss && (kindish(core, w) || m.notCovered || (nearMode && anchor) || (!ents.length && /어디|있어|있나|있니|있을까|어딨|주소/.test(q)))) miss = got;
      }
      const r = q.match(/(\d+(?:\.\d+)?)\s*(km|킬로|m|미터)/i);
      const pick = hit || miss;
      if (pick) {
        const nearest = SUPER.test(q);
        if (!pick.items.length && !pick.related.length) pick.suggest = nearNames(core, index, pick.said || pick.word);
        kind = { ...pick, nearest, radius: r ? Math.min(2000, Math.max(50, Math.round(Number(r[1]) * (/km|킬로/i.test(r[2]) ? 1000 : 1)))) : (nearAt >= 0 && !nearest ? 300 : null), near: nearMode, food: FOODISH.test(pick.word) && !SYN[core.compact(pick.word)] && !pick.byCat };
        ents.splice(0, ents.length, ...(anchor ? [anchor] : []));
      }
    }
  }
  // 길 이름과 번호를 물었는데 그 번호의 건물을 못 찾았다 ('양천로 58'): 길 카드로 답하지 않고 없다고 밝힌다
  const rq = q.match(/([가-힣\d]+(?:로|길))\s*(\d+(?:-\d+)?)/);
  const addrMiss = rq && !kind && !ents.some(e => !AREA.has(e.item.type)) ? `${rq[1]} ${rq[2]}` : null;
  if (addrMiss) ents.splice(0, ents.length);
  // '어디에 있어'를 물으면 가장 가까운 역까지 함께 알려 준다 (찾기 색인의 역 목록)
  const where = /어디|위치|찾아\s*가|가는\s*길|어느\s*쪽/.test(q);
  const stations = where ? (index.docs || []).filter(d => d.type === "station" && d.pt).map(d => ({ name: d.name, pt: d.pt, path: d.path })) : [];
  const here = basis && basis.pt && /여기|기준|고른\s*곳/.test(q) ? { name: basis.n || "거리 기준", pt: basis.pt, basis: true } : null;
  return { q, aspects, absent, kind, addrMiss, ents: ents.map(e => ({ path: e.item.path, name: e.item.name, type: e.item.type, pt: e.item.pt, group: e.item.group ?? null, score: e.item.score, field: e.item.field, said: words.slice(e.i, e.i + e.len).join(" "),
    building: e.item.building || null, floor: e.item.floor || "", src: e.item.src || null })), here, basis, where: where && !kind, stations };
}

// 질문 틀보다 먼저 답할까: 장소를 찾았고, 그 장소에 대해 무엇을 묻는지가 분명할 때.
// 질문 틀은 증미역 둘레나 전체 건물을 묻는 것이라, 다른 장소의 층수나 주소를 물으면 엉뚱한 틀에 걸린다 ('올리브영 주소' -> 증미역 주소)
const AREA = new Set(["road", "dong", "zone"]);   // 길, 동네, 우편구역을 묻는 것은 대개 '거기 있는 건물들'이라 질문 틀이 맡는다
export function prefer(plan, centerPath) {
  if (plan.addrMiss) return true;
  // 증미역 둘레 업종은 질문 틀이 표와 쿼리까지 보여 준다 (틀이 못 읽으면 부르는 쪽이 이 답으로 넘어온다)
  if (plan.kind) return !(plan.ents[0] && plan.ents[0].path === centerPath && !plan.kind.nearest && plan.kind.items.length);
  if (!plan.ents.length || plan.ents.every(e => AREA.has(e.type))) return false;
  const specific = plan.aspects.length || plan.absent.length || plan.ents.length === 2;
  return !!specific;
}

// 질문 틀이 이 종류를 제대로 읽었나. 틀은 모르는 업종을 기본값('카페')으로 채워 답하므로('증미역 근처 국밥집' -> 카페 33곳),
// 틀의 빈칸 값에 물은 종류가 들어 있을 때만 틀에 맡긴다
export function templateFits(plan, m, centerPath) {
  if (!plan || !m || !m.ok) return true;
  // 틀은 모두 증미역을 기준으로 한다. 다른 장소를 기준으로 물으면 틀에 맡기지 않는다
  if (centerPath && plan.ents.some(e => e.path !== centerPath) && /증미역/.test(m.item.q || "")) return false;
  if (!plan.kind) return true;
  const w = plan.kind.word.replace(/\s+/g, "");
  return Object.values(m.values || {}).some(v => { const c = typeof v === "string" ? v.replace(/\s+/g, "") : ""; return c && (c.includes(w) || (c.length >= 2 && w.startsWith(c) && /^(집|가게|전문점|매장|점|샵|숍)?$/.test(w.slice(c.length)))); });
}

// 묻기 탭의 차례 (ask.js와 시험이 같이 쓴다). first: 질문 틀보다 먼저 이 답을 낼까, after: 틀이 못 읽었을 때 이 답을 낼까
export const ragFirst = (plan, m, centerPath) => !!plan && (prefer(plan, centerPath) || !templateFits(plan, m, centerPath));
export const ragAfter = plan => !!plan && !!(plan.ents.length || plan.kind || plan.addrMiss);

// 장소 카드에 없는 칸은 그 장소가 들어 있는 건물의 카드에서 찾는다 (역 -> 역사 건물, 상호 -> 들어 있는 건물).
// 부르는 쪽은 장소 카드를 받은 뒤 이 경로들의 카드도 받아 cards에 넣는다
const VIA = ["역사 건물", "들어 있는 건물", "설치 주소의 건물"];
export const follow = card => (card && card.links || []).filter(l => VIA.includes(l.rel) && l.path).map(l => l.path);
// 상호 카드 없이 들어 있는 건물로 가는 장소 (사람이 넣은 장소). 카드는 건물 카드다
const inside = e => e.type === "business" && !!e.building && e.path === e.building;
const floorText = f => (/^\d+$/.test(f) ? `${f}층` : /^B(\d+)$/.test(f) ? `지하 ${f.slice(1)}층` : f === "지" ? "지하" : f);
const viaRel = (card, via) => ((card.links || []).find(l => l.path === via.path) || {}).rel || "건물";

// ---- 종류로 찾은 답. 기준 장소(또는 사용자가 고른 거리 기준점)가 있으면 가까운 차례로, 없으면 목록으로.
// 찾은 곳이 없으면 무엇을 어디까지 찾아봤는지 밝히고 없다고 답한다
export const SCOPE = "이 지도의 자료(증미역 둘레 1km 안의 건물과 상가정보 2026년 9월 기준)";
function kindAnswer(plan, out) {
  const k = plan.kind, e = plan.ents[0] || null;
  const from = e && e.pt ? { name: e.name, pt: e.pt, path: e.path }
    : plan.basis && plan.basis.pt && k.near ? { name: plan.basis.n || "거리 기준", pt: plan.basis.pt, path: null } : null;
  const what = `'${k.word}'`;
  const words = k.terms.filter(t => typeof t === "string" && !t.startsWith("@"));
  const chips = k.terms.filter(t => typeof t === "string" && t.startsWith("@")).map(t => `'${t.slice(1)}'`);
  const relLabel = (k.rel || []).map(c => `'${c}'`).join(", ");
  out.how = `질문에서 ${from ? `${from.name}${from.path ? "" : "(고른 거리 기준점)"}과 ` : ""}${what}를 읽었습니다. 찾기 색인 전체(상호 이름과 업종, 건물과 단지 이름)에서 ${words.map(w => `'${w}'`).join(", ")}${josa(words.length ? `'${words[words.length - 1]}'` : "", "이/가")} 들어간 곳${chips.length ? `과 ${chips.join(", ")} 묶음` : ""}을 공백을 빼고 모두 모았습니다.`
    + (relLabel ? ` 메뉴는 자료에 없어서, 업종이 ${relLabel}인 곳은 따로 모았습니다.` : "")
    + (from ? ` 그다음 ${from.name}의 좌표에서 직선 거리를 재어 가까운 차례로 늘어놓았습니다.` : "");
  const order = list => from ? list.map(x => ({ x, d: meters(from.pt, x.pt) })).sort((a, b) => a.d - b.d)
    : list.slice().sort((a, b) => a.name.localeCompare(b.name, "ko")).map(x => ({ x, d: null }));
  const fmt = o => (o.d == null ? o.x.name + (o.x.addr ? `(${o.x.addr})` : "") : `${o.x.name}(${nf(o.d)}m)`);
  const show = (l, n) => l.slice(0, n).map(fmt).join(", ") + (l.length > n ? " 등" : "");
  const main = order(k.items), rel = order(k.related || []);
  const unknownMenu = `메뉴는 이 자료에 없어서 ${josa(what, "을/를")} 파는지는 알 수 없습니다.`;
  let shown = [];
  if (!main.length) {
    out.sentences.push(k.notCovered
      ? `${SCOPE}에는 이름에 ${josa(what, "이/가")} 들어간 곳이 없습니다. 다만 상가정보에는 ${josa(k.notCovered, "이/가")} 들어 있지 않아, 실제로 없는지는 이 자료로 알 수 없습니다.`
      : `${SCOPE}에는 이름이나 업종에 ${josa(what, "이/가")} 들어간 곳이 없습니다.`);
    if (rel.length && from && k.radius && !rel.some(o => o.d <= k.radius)) {
      out.sentences.push(`업종이 ${relLabel}인 곳도 ${from.name}에서 직선 ${nf(k.radius)}m 안에는 없고, 가장 가까운 곳은 ${fmt(rel[0])}입니다. ${unknownMenu}`);
    } else if (rel.length) {
      const r2 = from && k.radius ? rel.filter(o => o.d <= k.radius) : rel;
      out.sentences.push(`대신 업종이 ${relLabel}인 곳은 ${from && k.radius ? `${from.name}에서 직선 ${nf(k.radius)}m 안에 ` : ""}${nf(r2.length)}곳 있습니다${from ? `. 가까운 곳부터` : ""}: ${show(r2, 5)}. ${unknownMenu}`);
    } else out.evidence.push({ label: "찾아본 말", value: [...words, ...chips.map(c => `${c} 묶음`)].join(", "), path: null });
    if (!rel.length && k.suggest && k.suggest.length) {
      out.sentences.push(`글자가 비슷한 이름으로는 ${k.suggest.map(x => `'${x.name}'`).join(", ")}${josa(`'${k.suggest[k.suggest.length - 1].name}'`, "이/가").slice(-1)} 있습니다. 이것을 찾으셨다면 그 이름으로 다시 물어 주세요.`);
      k.suggest.forEach(x => out.evidence.push({ label: "비슷한 이름", value: x.name, path: x.path, link: true }));
    }
  } else if (from && k.nearest) {
    const [f, ...rest] = main;
    out.sentences.push(`${from.name}에서 가장 가까운 ${what}에 맞는 곳은 ${josa(f.x.name, "으로/로")}, 직선 약 ${nf(f.d)}m입니다.${f.x.addr ? ` 주소는 ${f.x.addr}입니다.` : ""}`);
    if (rest.length) out.sentences.push(`그다음은 ${show(rest, 4)}이고, 이 자료에서 모두 ${nf(main.length)}곳입니다.`);
    shown = main.slice(0, 10);
  } else if (from && k.radius) {
    const within = main.filter(o => o.d <= k.radius);
    if (within.length) {
      out.sentences.push(`${from.name}에서 직선 ${nf(k.radius)}m 안에 ${what}에 맞는 곳은 ${nf(within.length)}곳입니다. 가까운 곳부터 ${show(within, 5)}입니다.`);
      shown = within;
    } else {
      out.sentences.push(`${from.name}에서 직선 ${nf(k.radius)}m 안에는 ${what}에 맞는 곳이 없습니다. 가장 가까운 곳은 ${josa(main[0].x.name, "으로/로")}, 직선 약 ${nf(main[0].d)}m이고, 이 자료에서 모두 ${nf(main.length)}곳입니다.`);
      shown = main.slice(0, 5);
    }
  } else if (from) {
    out.sentences.push(`${from.name}에서 가까운 차례로 ${what}에 맞는 곳은 ${show(main, 5)}이고, 이 자료에서 모두 ${nf(main.length)}곳입니다.`);
    shown = main;
  } else {
    out.sentences.push(`${SCOPE}에서 ${what}에 맞는 곳은 ${nf(main.length)}곳입니다: ${show(main, 6)}.`);
    if (k.near) out.sentences.push("어디에서 가까운 곳인지 장소 이름을 함께 적거나, 카드의 '거리 기준'으로 기준점을 고르면 가까운 차례로 답합니다.");
    shown = main;
  }
  if (main.length) {
    if (k.notCovered) out.sentences.push(`상가정보에는 ${josa(k.notCovered, "이/가")} 들어 있지 않아, 위는 이름으로 찾은 건물과 상호만 모은 것입니다.`);
    if (rel.length) out.sentences.push(`이름이나 업종에 ${josa(what, "이/가")} 들어 있지는 않지만 업종이 ${relLabel}인 곳도 ${nf(rel.length)}곳 더 있습니다${from ? `(가장 가까운 곳은 ${fmt(rel[0])})` : ""}. ${unknownMenu}`);
    else if (k.food) out.sentences.push("메뉴는 이 자료에 없어서, 이름이나 업종에 드러나지 않고 그 음식을 파는 가게까지는 알 수 없습니다.");
  }
  const ev = (o, extra) => out.evidence.push({ label: o.d == null ? o.x.by : `${nf(o.d)}m`, value: o.x.name + (o.x.cat ? ` (${o.x.cat})` : ""), note: extra || `${o.x.by}으로 맞음`, path: o.x.path, link: true });
  shown.slice(0, 20).forEach(o => ev(o));
  rel.slice(0, main.length ? 5 : 20).forEach(o => ev(o, `업종 ${o.x.cat}, 메뉴는 알 수 없음`));
  out.paths = [from && from.path, ...shown.slice(0, 50).map(o => o.x.path), ...rel.slice(0, main.length ? 5 : 30).map(o => o.x.path)].filter(Boolean);
  return out;
}

// ---- 2) 답 만들기. cards: 장소 경로 -> card.json (없으면 null)
// 결과: { ok, sentences: [], evidence: [{label, value, note, path}], paths: [], how }
export function answer(plan, cards) {
  const out = { ok: false, sentences: [], evidence: [], paths: [], how: "" };
  const { ents, aspects, absent } = plan;
  if (plan.kind) { out.ok = true; return kindAnswer(plan, out); }
  if (plan.addrMiss) {
    out.ok = true;
    out.sentences.push(`${SCOPE}에는 도로명주소가 '${plan.addrMiss}'인 건물이 없습니다. 범위 밖이거나 번호가 다를 수 있으니, 찾기 탭에서 길 이름만 적어 그 길의 건물 번호를 확인해 주세요.`);
    out.how = `질문에서 길 이름과 건물 번호 '${plan.addrMiss}'를 읽고, 찾기 색인의 건물과 상호 주소에서 번호까지 똑같은 곳을 찾았지만 없었습니다.`;
    return out;
  }
  if (!ents.length && !(plan.here && aspects.some(a => a.id === "dist"))) return out;
  out.ok = true;
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
