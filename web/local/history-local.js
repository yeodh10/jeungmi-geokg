// 로컬 서버의 기록 탭 (명세 3.1절 '로컬은 기록 탭이 더해집니다', 10절 WP6).
// 공개 사이트는 변경 기록을 싣지 않는다 (지금까지의 승인이 공개하지 않는 자료를 근거로 해서다). 로컬 서버에서만 본다.
//   고른 장소의 기록: 그 장소를 바꾼 승인 제안과 '승인 바로 전 값과 견주기'(시점별 판, /api/editions/as-of)
//   시점 비교: 고른 장소의 아무 날짜 값을 지금 값과 견준다
//   최근 변경: /api/editions/changes를 제안별로 묶어 최신 순으로. 지도에서 보기, 검토 화면에서 보기
// 검토 화면의 '제안 노드' 링크(/map?focus=…/proposal/…)는 이 탭에서 그 제안을 보여 준다.

const ACTION_KO = { create: "신규", update: "변경", retire: "폐지", link: "주소 연결", withdraw: "승인 거둠" };
const BASE = "https://yeodh10.github.io/jeungmi-geokg/id/";
const LIST = 30;
const CSS = `
.hl h3 { font-size: 14px; margin: 18px 0 6px; }
.hl h3:first-child { margin-top: 4px; }
.hl .hl-note { font-size: 12px; color: var(--text-2); margin: 0 0 8px; }
.hl ul.hl-list { list-style: none; margin: 0; padding: 0; display: grid; gap: 8px; }
.hl .hl-item { border: 1px solid var(--line); border-radius: var(--radius); padding: 10px 12px; background: var(--panel); }
.hl .hl-item.on { border-color: var(--graph); box-shadow: 0 0 0 2px var(--graph-line); }
.hl .hl-top { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; font-size: 12px; color: var(--muted); }
.hl .hl-sum { margin: 4px 0; font-size: 14px; }
.hl .hl-acts { display: flex; gap: 6px; flex-wrap: wrap; }
.hl .hl-acts .btn, .hl .hl-acts a.btn { min-height: 36px; }
.hl table.hl-diff { width: 100%; border-collapse: collapse; font-size: 12px; margin-top: 8px; }
.hl table.hl-diff th, .hl table.hl-diff td { text-align: left; vertical-align: top; padding: 4px 6px; border-top: 1px solid var(--line); overflow-wrap: anywhere; }
.hl table.hl-diff th { color: var(--muted); font-weight: 600; }
.hl .hl-when { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.hl .hl-when input { min-height: 40px; padding: 0 8px; border: 1px solid var(--line-2); border-radius: 8px; background: var(--panel); color: var(--text); }
.hl .chip.act { background: var(--graph-soft); color: var(--graph); }
`;

export function init(ctx) {
  const { bus, ui, panel, state, source, data } = ctx;
  if (ctx.cfg.mode !== "local" || !(source.caps && source.caps.history)) return;
  const style = document.createElement("style");
  style.textContent = CSS;
  document.head.appendChild(style);
  const nf = n => Number(n).toLocaleString("ko-KR");
  const day = s => String(s || "").slice(0, 10);
  const toPath = u => (String(u || "").startsWith(BASE) ? String(u).slice(BASE.length) : null);
  let pane = null;
  let place = null;          // 지금 고른 장소 {path, name}
  let wantProposal = null;   // 검토 화면에서 온 제안 id
  let changesP = null;

  // 검토 화면의 제안 노드 링크: 제안은 지도 장소가 아니라 이 탭에서 연다 (뼈대가 '찾지 못함'을 내기 전에 바꾼다)
  const f0 = state.get("focus");
  const m0 = f0 && String(f0).match(/(?:^|\/id\/)proposal\/([A-Za-z0-9_.:-]+)\/?$/);
  if (m0) {
    wantProposal = m0[1];
    state.set({ focus: null, unit: null, tab: "history" }, { silent: true });
  }

  // 고른 장소가 바뀌면 탭이 보일 때 다시 그린다 (안 보이는 동안은 받지 않는다)
  let dirty = false;
  const refresh = () => { if (pane && !pane.hidden) { dirty = false; drawPlace(); } else dirty = true; };
  bus.on("select", sel => {
    if (!sel || !sel.path) return;
    place = { path: sel.path, name: sel.name || data.nameOf(sel.path) || sel.path, group: sel.group != null };
    refresh();
  });
  bus.on("deselect", () => { place = null; refresh(); });

  function render(el) {
    pane = el;
    new MutationObserver(() => { if (!el.hidden && dirty) { dirty = false; drawPlace(); } }).observe(el, { attributes: true, attributeFilter: ["hidden"] });
    el.innerHTML = `<div class="hl">
      <p class="hl-note">변경 제안을 승인하면 그래프가 바뀌고 그 기록이 남습니다. 공개 사이트에는 싣지 않고 이 로컬 서버에서만 봅니다.
        승인과 반려는 <a href="/">검토 화면</a>에서 합니다.</p>
      <section class="hl-place"></section>
      <section class="hl-recent"><h3>최근 변경</h3>${ui.skeleton(4)}</section></div>`;
    drawPlace();
    drawRecent();
  }

  function valueText(names, v) {
    const n = names && names[v];
    if (n && n.label) return n.label;
    if (typeof v === "string" && /^https?:\/\//.test(v)) return (typeof ext === "function" && ext(v)) || localName(v);
    const s = String(v);
    return s.length > 160 ? s.slice(0, 157) + "..." : s;
  }
  function diffTable(r) {
    if (!r.diff.length) return `<p class="hl-note">그 시점과 지금 값이 같습니다.</p>`;
    const cellOf = vs => (vs.length ? vs.map(v => esc(valueText(r.names, v))).join("<br>") : '<span class="muted">없음</span>');
    return `<table class="hl-diff"><thead><tr><th scope="col">속성</th><th scope="col">그때</th><th scope="col">지금</th></tr></thead><tbody>${
      r.diff.map(d => `<tr><td>${esc(d.p.split(" ").map(p => valueText(r.names, p)).join(" "))}</td><td>${cellOf(d.before)}</td><td>${cellOf(d.after)}</td></tr>`).join("")}</tbody></table>
      <p class="hl-note">승인과 정정의 사건 기록으로 ${esc(new Date(r.at).toLocaleString("ko-KR", { timeZone: "Asia/Seoul" }))} 시점의 값을 되살렸습니다.</p>`;
  }
  async function compare(out, uri, at) {
    out.innerHTML = `<p class="hl-note">되살리는 중입니다.</p>`;
    try { out.innerHTML = diffTable(await source.asOf(uri, at)); }
    catch (e) { out.innerHTML = `<p class="hl-note" role="alert">${esc(e.message)}</p>`; }
  }

  // ---- 고른 장소의 기록
  async function drawPlace() {
    const box = pane && pane.querySelector(".hl-place");
    if (!box) return;
    if (!place || place.group) {
      box.innerHTML = `<h3>고른 장소의 기록</h3><p class="hl-note">${place && place.group ? "단지는 동마다 기록이 있습니다. 동 하나를 골라 주세요." : "지도나 찾기에서 장소를 고르면 그 장소를 바꾼 승인과 그 전 값을 여기서 견줘 봅니다."}</p>`;
      return;
    }
    const uri = BASE + place.path;
    const me = place;
    box.innerHTML = `<h3>${esc(place.name)}의 기록</h3>${ui.skeleton(3)}`;
    let f;
    try { f = await source.feature(uri); }
    catch (e) { if (me === place) box.innerHTML = `<h3>${esc(me.name)}의 기록</h3><p class="hl-note" role="alert">${esc(e.message)}</p>`; return; }
    if (me !== place) return;
    const hist = f.history || [];
    box.innerHTML = `<h3>${esc(me.name)}의 기록</h3>
      ${hist.length ? `<ul class="hl-list">${hist.map((h, k) => `<li class="hl-item">
        <div class="hl-top"><span>${esc(day(h.decided_at || h.created))}</span><span class="chip act">${esc(ACTION_KO[h.action] || h.action || "변경")}</span>
          ${h.decided_by ? `<span>승인 ${esc(h.decided_by)}</span>` : ""}</div>
        <p class="hl-sum">${esc(h.summary || h.id)}</p>
        <div class="hl-acts">${h.decided_at ? `<button type="button" class="btn ghost mini" data-k="${k}">승인 바로 전 값과 견주기</button>` : ""}
          <a class="btn ghost mini" href="/?status=approved&id=${encodeURIComponent(h.id)}">검토 화면에서 보기</a></div>
        <div class="hl-out"></div></li>`).join("")}</ul>`
        : `<p class="hl-note">이 장소를 바꾼 승인은 없습니다. 원천 자료 값 그대로입니다.</p>`}
      <h3>시점 비교</h3>
      <form class="hl-when"><label for="hl-at">이날 값과 지금 값</label><input id="hl-at" type="date" value="2026-10-01" min="2026-01-01">
        <button type="submit" class="btn ghost mini">견주기</button></form><div class="hl-out hl-at-out"></div>`;
    box.querySelectorAll("button[data-k]").forEach(b => b.addEventListener("click", () => {
      const h = hist[Number(b.dataset.k)];
      const at = new Date(Date.parse(h.decided_at) - 1000).toISOString();
      compare(b.closest("li").querySelector(".hl-out"), uri, at);
    }));
    box.querySelector(".hl-when").addEventListener("submit", e => {
      e.preventDefault();
      const v = box.querySelector("#hl-at").value;
      if (!/^\d{4}-\d{2}-\d{2}$/.test(v)) return;
      compare(box.querySelector(".hl-at-out"), uri, v + "T23:59:59+09:00");
    });
  }

  // ---- 최근 변경 (제안별로 묶는다)
  async function drawRecent() {
    const box = pane && pane.querySelector(".hl-recent");
    if (!box) return;
    let r;
    try { r = await (changesP = changesP || source.changes().catch(e => { changesP = null; throw e; })); }
    catch (e) {
      box.innerHTML = `<h3>최근 변경</h3><p class="hl-note" role="alert">변경 기록을 받지 못했습니다. ${esc(e.message)}</p>
        <button type="button" class="btn ghost mini">${ui.icon("refresh", 14)}다시 시도</button>`;
      box.querySelector("button").addEventListener("click", drawRecent);
      return;
    }
    const by = new Map();
    for (const c of r.changes || []) {
      const key = c.proposal + "|" + c.decidedAt + "|" + c.action;
      if (!by.has(key)) by.set(key, { proposal: c.proposal, at: c.decidedAt, action: c.action, target: c.target, n: 0 });
      by.get(key).n++;
    }
    const all = [...by.values()].sort((a, b) => String(b.at).localeCompare(String(a.at)));
    let n = LIST;
    const idOf = g => String(g.proposal || "").replace(/^.*\/proposal\//, "");
    if (wantProposal) {
      const k = all.findIndex(g => idOf(g) === wantProposal);
      if (k >= n) n = k + 1;
    }
    const draw = () => {
      box.innerHTML = `<h3>최근 변경</h3><p class="hl-note">변경 ${nf(all.length)}건 (승인과 승인 거둠). 최신 순입니다.</p>
        <ul class="hl-list">${all.slice(0, n).map((g, k) => {
          const id = idOf(g), p = toPath(g.target);
          const name = (p && data.nameOf(p)) || valueText(r.names, g.target);
          return `<li class="hl-item${id === wantProposal ? " on" : ""}" data-id="${esc(id)}">
            <div class="hl-top"><span>${esc(day(g.at))}</span><span class="chip act">${esc(ACTION_KO[g.action] || g.action || "변경")}</span>
              <span>트리플 ${nf(g.n)}개</span></div>
            <p class="hl-sum">${esc(name)}</p>
            <div class="hl-acts">${p ? `<button type="button" class="btn ghost mini" data-path="${esc(p)}">지도에서 보기</button>` : ""}
              <a class="btn ghost mini" href="/?status=&id=${encodeURIComponent(id)}">검토 화면에서 보기</a></div></li>`;
        }).join("")}</ul>
        ${all.length > n ? `<button type="button" class="btn ghost mini hl-more">더 보기</button>` : ""}`;
      box.querySelectorAll("button[data-path]").forEach(b => b.addEventListener("click", () => openPath(b.dataset.path)));
      const more = box.querySelector(".hl-more");
      if (more) more.addEventListener("click", () => { n += LIST; draw(); });
      const on = box.querySelector(".hl-item.on");
      if (on && wantProposal) { on.scrollIntoView({ block: "center" }); wantProposal = null; }
    };
    draw();
  }

  async function openPath(path) {
    if (data.byPath.has(path)) { bus.emit("select", { path, from: "list", fit: true }); return; }
    try { const card = await source.card(path); bus.emit("select", { path, card, from: "list", fit: true }); }
    catch (e) { ui.toast("이 장소를 지금 그래프에서 찾지 못했습니다 (승인을 거뒀거나 지운 객체일 수 있습니다)"); }
  }

  panel.addTab({ id: "history", label: "기록", order: 30, render });
  // 제안 링크로 왔으면 기록 탭을 연다 (뼈대가 주소창의 tab을 읽어 연다)
}
