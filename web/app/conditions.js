// 조건 찾기 (명세 4.2절 있으면 좋음 3): 용도, 층수, 사용승인 연도, 거리 기준점에서의 거리(기준점을 고른 뒤에만). 적용하기 전에 건수를 보여 준다.
// 결과는 찾기와 같은 목록(ctx.results)과 번호 표식으로 나온다. 입구는 칩 줄의 더보기에 있다 (chips.js가 ctx.features를 보고 그린다).

const V = "?v=" + encodeURIComponent((window.APP_CONFIG || {}).build || "");
const app = (window.APP_CONFIG || {}).app || ".";
const core = await import(`${app}/search-core.js${V}`);
const nf = n => Number(n).toLocaleString("ko-KR");
const FLOORS = [[0, "전체"], [2, "2층 이상"], [5, "5층 이상"], [10, "10층 이상"], [20, "20층 이상"]];
const DIST = [[0, "전체 (반경 1km)"], [300, "300m 안"], [500, "500m 안"], [700, "700m 안"]];

export async function init(ctx) {
  const { bus, panel, ui, data } = ctx;
  if (!ctx.results) return;
  ctx.css("tools.css");
  const years = data.places.map(p => (p.ad ? Number(String(p.ad).slice(0, 4)) : null)).filter(Boolean);
  const yMin = Math.min(...years), yMax = Math.max(...years);
  const decades = [];
  for (let d = Math.floor(yMin / 10) * 10; d <= yMax; d += 10) decades.push(d);
  let cond = { ug: [], minFloors: 0, yearFrom: 0, yearTo: 0, maxD: 0 };

  const basisPt = () => (ctx.basis.get() ? ctx.basis.get().pt : null);
  const count = c => core.conditionItems(ctx.results.index(), { ...c, basisPt: basisPt() });
  const label = c => {
    const parts = [];
    if (c.ug.length) parts.push(c.ug.map(k => (data.useGroups.find(g => g.key === k) || { label: k }).label).join(", "));
    if (c.minFloors) parts.push(`지상 ${c.minFloors}층 이상`);
    if (c.yearFrom || c.yearTo) parts.push(`${c.yearFrom || yMin}~${c.yearTo || yMax}년 사용승인`);
    if (c.maxD && ctx.basis.get()) parts.push(`${ctx.basis.get().n}에서 ${c.maxD}m 안`);
    return parts.join(", ") || "조건 없음";
  };

  function render(el) {
    const ugBoxes = (data.useGroups || []).map(g => `<label class="pick"><input type="checkbox" name="cond-ug" value="${esc(g.key)}"${cond.ug.includes(g.key) ? " checked" : ""}>
      <span class="sw" style="background:var(${esc(g.color)})"></span>${esc(g.label)} <span class="muted">${nf(g.n)}동</span></label>`).join("");
    const yOpt = (sel, first) => `<option value="0">${first}</option>${decades.map(d => `<option value="${d}"${sel === d ? " selected" : ""}>${d}년</option>`).join("")}`;
    el.innerHTML = `<div class="cond">
      <h2 class="res-title" tabindex="-1">조건 찾기</h2>
      <p class="small muted">건물 자료에서 고릅니다. 단지는 한 줄로 셉니다.</p>
      <fieldset class="pick-set"><legend>용도 (고르지 않으면 전체)</legend>${ugBoxes}</fieldset>
      <div class="cond-row"><label for="cond-floor">지상 층수</label><select id="cond-floor">${FLOORS.map(([v, t]) => `<option value="${v}"${cond.minFloors === v ? " selected" : ""}>${t}</option>`).join("")}</select></div>
      <div class="cond-row"><label for="cond-yf">사용승인 연도</label><span class="cond-years"><select id="cond-yf" aria-label="시작 연도">${yOpt(cond.yearFrom, "처음부터")}</select>
        <span aria-hidden="true">~</span><select id="cond-yt" aria-label="끝 연도">${yOpt(cond.yearTo ? Math.floor(cond.yearTo / 10) * 10 : 0, "지금까지")}</select></span></div>
      <p class="small muted">사용승인일은 2018년까지의 자료입니다. 연도를 고르면 사용승인일이 없는 건물은 빠집니다.</p>
      ${ctx.basis.get() ? `<div class="cond-row"><label for="cond-d">${esc(ctx.basis.get().n)}에서 직선 거리</label><select id="cond-d">${DIST.map(([v, t]) => `<option value="${v}"${cond.maxD === v ? " selected" : ""}>${t}</option>`).join("")}</select></div>`
        : `<p class="small muted">거리 조건을 쓰려면 먼저 장소 카드나 지도에서 '거리 기준'을 고르세요.</p>`}
      <p class="cond-count" aria-live="polite"></p>
      <div class="res-acts"><button type="button" class="btn cond-apply">적용</button><button type="button" class="btn ghost cond-reset">조건 지우기</button></div>
    </div>`;
    const read = () => {
      cond.ug = [...el.querySelectorAll('input[name="cond-ug"]:checked')].map(x => x.value);
      cond.minFloors = Number(el.querySelector("#cond-floor").value);
      cond.yearFrom = Number(el.querySelector("#cond-yf").value);
      const yt = Number(el.querySelector("#cond-yt").value);
      cond.yearTo = yt ? yt + 9 : 0;
      const dSel = el.querySelector("#cond-d");
      cond.maxD = dSel ? Number(dSel.value) : 0;
    };
    const update = () => {
      read();
      const r = count(cond);
      el.querySelector(".cond-count").innerHTML = `적용하면 <b>${nf(r.items.length)}곳</b> (건물 ${nf(r.buildings)}동)`;
      el.querySelector(".cond-apply").disabled = !r.items.length;
    };
    el.addEventListener("change", update);
    update();
    el.querySelector(".cond-reset").onclick = () => { cond = { ug: [], minFloors: 0, yearFrom: 0, yearTo: 0, maxD: 0 }; render(el); el.querySelector(".res-title").focus(); };
    el.querySelector(".cond-apply").onclick = () => {
      read();
      const r = count(cond);
      const c = { ...cond, ug: cond.ug.slice() };
      ctx.results.show({ kind: "cond", key: "cond", title: "조건 찾기 결과", items: r.items, sort: "near", focus: true,
        extraNote: `${label(c)}. 조건에 맞는 건물은 ${nf(r.buildings)}동입니다.`,
        empty: `<p>맞는 건물이 없습니다.</p><div class="res-acts"><button type="button" class="btn" data-cond-back>조건 바꾸기</button></div>` });
      ui.live(`조건에 맞는 곳 ${nf(r.items.length)}곳을 목록과 지도에 표시했습니다`);
    };
  }

  function open() {
    panel.show({ id: "conditions", title: "조건 찾기", focus: true, render: el => render(el) });
    if (ctx.sheet && ctx.sheet.active() && ctx.sheet.current() === "peek") ctx.sheet.snap("half");
  }
  bus.on("conditions:open", open);
  document.addEventListener("click", e => { if (e.target.closest("[data-cond-back]")) panel.remove("results"); });
}
