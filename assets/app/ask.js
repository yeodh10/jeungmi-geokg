// 묻기 탭 (명세 3.7절): 빌드 때 공개 범위 그래프에서 미리 실행한 답을 보여 준다.
// 질문을 누르면 한 문장 답, 지도 보라색 번짐, 쉬운 열 이름의 표(처음 50행과 더 보기), 접힌 '어떻게 찾았나요'가 나온다.
// 탭 맨 위의 빈 칸은 ask:ready로 알린다 (로컬 서버의 자유 입력칸이 들어갈 자리).

const ROWS = 50;
const nf = n => Number(n).toLocaleString("ko-KR");

export function init(ctx) {
  ctx.css("graph.css");
  const { bus, ui, panel, source } = ctx;
  let qsP = null;
  let pane = null;
  let openId = null;
  const load = () => (qsP = qsP || source.questions().catch(e => { qsP = null; throw e; }));

  function openPath(path) {
    if (ctx.openPath) return ctx.openPath(path, { from: "list" });
    bus.emit("select", { path, from: "list", fit: true });
  }

  function cell(v) {
    if (v && typeof v === "object" && v.p) return `<button type="button" class="linkish" data-path="${esc(v.p)}">${esc(v.n || v.p)}</button>`;
    if (typeof v === "number") return esc(nf(v));
    return esc(v == null ? "" : v);
  }

  function listHtml(items) {
    const one = q => `<li><button type="button" class="ask-q" data-id="${esc(q.id)}"><span>${esc(q.q)}</span><span class="n">${nf(q.n)}</span></button></li>`;
    const featured = items.filter(q => q.featured), rest = items.filter(q => !q.featured);
    return `<h3 class="ask-h">대표 질문</h3><ul class="ask-list">${featured.map(one).join("")}</ul>
      ${rest.length ? `<h3 class="ask-h">다른 질문</h3><ul class="ask-list">${rest.map(one).join("")}</ul>` : ""}`;
  }

  function render(el) {
    pane = el;
    el.innerHTML = `<div class="ask-slot" data-slot="ask"></div><p class="ask-note">불러오는 중입니다.</p><div class="ask-body">${ui.skeleton(6)}</div>`;
    bus.emit("ask:ready", { slot: el.querySelector(".ask-slot") });
    load().then(qs => {
      el.querySelector(".ask-note").textContent = qs.note || "미리 실행해 둔 답입니다. 질문을 직접 입력하려면 이 저장소의 로컬 서버를 실행해 주세요.";
      const body = el.querySelector(".ask-body");
      body.innerHTML = `<div class="ask-ans"></div><div class="ask-qs">${listHtml(qs.items)}</div>`;
      body.querySelectorAll(".ask-q").forEach(b => b.addEventListener("click", () => answer(b.dataset.id, { focus: true })));
      if (openId) answer(openId, { focus: false });
    }).catch(() => {
      el.querySelector(".ask-note").textContent = "미리 실행해 둔 답입니다. 질문을 직접 입력하려면 이 저장소의 로컬 서버를 실행해 주세요.";
      const body = el.querySelector(".ask-body");
      body.innerHTML = `<div class="cd-fail" role="alert"><p>질문 자료를 받지 못했습니다.</p><button type="button" class="btn mini">${ui.icon("refresh", 14)}다시 시도</button></div>`;
      body.querySelector("button").addEventListener("click", () => render(el));
    });
  }

  // 질문 하나의 답
  async function answer(id, { focus = true } = {}) {
    openId = id;
    let qs;
    try { qs = await load(); } catch (e) { return; }
    const q = qs.items.find(x => x.id === id);
    if (!q || !pane) return;
    const box = pane.querySelector(".ask-ans");
    if (!box) return;
    let shown = Math.min(ROWS, q.rows.length);
    const head = `<tr>${q.cols.map(c => `<th scope="col">${esc(c.label)}</th>`).join("")}</tr>`;
    const body = () => q.rows.slice(0, shown).map(r => `<tr>${r.map(v => `<td>${cell(v)}</td>`).join("")}</tr>`).join("");
    const mapN = (q.map || []).length;
    box.innerHTML = `<section class="ask-answer" aria-live="polite"><h2 tabindex="-1">${esc(q.q)}</h2><p>${esc(q.answer)}</p>
        <div class="ask-acts">${mapN ? `<button type="button" class="btn ghost mini ask-show">지도에 ${nf(mapN)}곳 표시</button>` : ""}
        <button type="button" class="btn ghost mini ask-close">답 닫기</button></div></section>
      ${q.rows.length ? `<div class="ask-table-wrap"><table class="ask-table"><caption class="sr-only">${esc(q.q)} 표, ${nf(q.rows.length)}행</caption>
        <thead>${head}</thead><tbody>${body()}</tbody></table></div>
        ${q.rows.length > shown ? `<p class="small muted ask-more-wrap">${nf(q.rows.length)}행 가운데 ${nf(shown)}행을 보여 줍니다. 지도에는 모두 표시합니다.
          <button type="button" class="btn ghost mini ask-more">더 보기</button></p>` : ""}` : `<p class="muted">표로 보일 행이 없습니다.</p>`}
      <details class="ask-sparql"><summary>어떻게 찾았나요 (실행한 SPARQL 쿼리)</summary>
        <pre>${esc(q.sparql || "")}</pre>
        <p>공개 범위 그래프에서 빌드 때 실행했습니다.${/\{\{NEAR:/.test(q.sparql || "") ? " {{NEAR:장소:거리}} 자리는 그 장소를 중심으로 주어진 거리만큼의 원으로 바꿔 실행했습니다." : ""}${q.from ? ` 평가 질문 ${esc(q.from)}의 정답 쿼리를 바탕으로 했습니다.` : ""}</p></details>`;
    const bind = () => box.querySelectorAll(".ask-table [data-path]").forEach(b => b.addEventListener("click", () => openPath(b.dataset.path)));
    bind();
    const more = box.querySelector(".ask-more");
    if (more) more.addEventListener("click", () => {
      shown = Math.min(q.rows.length, shown + ROWS);
      box.querySelector(".ask-table tbody").innerHTML = body();
      bind();
      if (shown >= q.rows.length) box.querySelector(".ask-more-wrap").remove();
      else box.querySelector(".ask-more-wrap").firstChild.textContent = `${nf(q.rows.length)}행 가운데 ${nf(shown)}행을 보여 줍니다. 지도에는 모두 표시합니다. `;
    });
    const show = () => {
      if (!mapN) { bus.emit("spread:clear"); return; }
      bus.emit("spread", { paths: q.map, label: q.q, owner: "ask" });
      ctx.mapApi.fitPlaces(q.map, { maxZoom: 17 });
    };
    const sb = box.querySelector(".ask-show");
    if (sb) sb.addEventListener("click", show);
    box.querySelector(".ask-close").addEventListener("click", () => {
      openId = null;
      box.innerHTML = "";
      bus.emit("spread:clear");
      const b = pane.querySelector(`.ask-q[data-id="${CSS.escape(id)}"]`);
      if (b) b.focus();
    });
    show();
    pane.closest(".panel-body") && (pane.closest(".panel-body").scrollTop = 0);
    if (focus) box.querySelector("h2").focus({ preventScroll: true });
  }

  panel.addTab({ id: "ask", label: "묻기", order: 10, render });

  // 다른 곳(홈, 둘러보기)에서 질문 열기: 묻기 탭으로 가서 답을 연다
  bus.on("ask:open", ({ id, push = true, focus = true } = {}) => {
    openId = id;
    panel.selectTab("ask", { push });
    if (ctx.sheet && ctx.sheet.active() && ctx.sheet.current() === "peek") ctx.sheet.snap("half");
    if (pane && pane.querySelector(".ask-ans")) answer(id, { focus });
  });
  ctx.questions = load;
}
