// 묻기 탭 (명세 3.7절): 빌드 때 공개 범위 그래프에서 미리 실행한 답을 보여 준다.
// 질문을 누르면 한 문장 답, 지도 보라색 번짐, 쉬운 열 이름의 표(처음 50행과 더 보기), 접힌 '어떻게 찾았나요'가 나온다.
// 탭 맨 위의 빈 칸은 ask:ready로 알린다 (로컬 서버의 자유 입력칸이 들어갈 자리).
// 자료 입구에 askForms가 있으면(공개 사이트) 키 없는 질문 입력칸을 둔다: 입력을 질문 틀에 맞춰 읽고(ask-match.js),
// 빈칸 조합마다 빌드 때 실행해 둔 결과를 보여 준다. 한 장소의 층수, 주소, 거리처럼 틀이 맡지 않는 질문은
// 그래프에서 찾아 답한다(ask-rag.js): 찾기 색인으로 장소를 찾고, 그 장소의 카드 자료에서 근거 칸을 골라 문장 틀에 넣는다.
// AI 호출도 쿼리 생성도 없다.

const V = "?v=" + encodeURIComponent((window.APP_CONFIG || {}).build || "");
const app = (window.APP_CONFIG || {}).app || ".";
const AM = await import(`${app}/ask-match.js${V}`);
const RAG = await import(`${app}/ask-rag.js${V}`);
const CORE = await import(`${app}/search-core.js${V}`);

const ROWS = 50;
const nf = n => Number(n).toLocaleString("ko-KR");
const EXAMPLES = ["증미역 300m 안 편의점", "지상 15층 넘는 건물", "강서아이티밸리 몇 층이야", "증미역에서 가양역까지 거리"];

export function init(ctx) {
  ctx.css("graph.css");
  const { bus, ui, panel, source } = ctx;
  let qsP = null, formsP = null;
  let pane = null;
  let openId = null;
  const load = () => (qsP = qsP || source.questions().catch(e => { qsP = null; throw e; }));
  // 키 없는 질문 자료는 입력칸을 처음 쓸 때 받는다 (첫 화면 자료에 넣지 않는다)
  const loadForms = () => (formsP = formsP || source.askForms().then(a => AM.prepare(a)).catch(e => { formsP = null; throw e; }));

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

  function typedHtml() {
    return `<form class="ak" novalidate role="search">
      <label class="ak-h" for="ak-q">질문 입력</label>
      <div class="ak-row"><input id="ak-q" class="field" type="search" maxlength="200" autocomplete="off" enterkeyhint="search"
        placeholder="예: ${esc(EXAMPLES[0])}" aria-describedby="ak-help"><button type="submit" class="btn">묻기</button></div>
      <div class="ak-eg" role="group" aria-label="질문 예시">${EXAMPLES.map(t => `<button type="button" data-eg="${esc(t)}">${esc(t)}</button>`).join("")}</div>
      <p class="ak-help" id="ak-help">미리 만든 질문 틀에 맞춰 읽습니다. 거리, 층수, 용도, 법정동, 길 이름, 업종 같은 빈칸 값을 바꿔 물을 수 있고,
        답은 사람이 쓴 쿼리를 빌드 때 실행해 둔 결과입니다. '강서아이티밸리 몇 층이야', '증미역에서 가양역까지 거리'처럼 한 장소를 물으면
        그 장소의 카드 자료에서 근거를 찾아 답하고, '증미역에서 가장 가까운 약국', '강서아이티밸리 안 카페', '가장 높은 건물'처럼 물으면 이름과 업종, 건물 값으로 모두 찾아 답합니다. AI는 쓰지 않습니다.</p>
      <div class="ak-msg" aria-live="polite"></div></form>`;
  }

  function render(el) {
    pane = el;
    el.innerHTML = `<div class="ask-slot" data-slot="ask"></div>${source.askForms ? typedHtml() : ""}<p class="ask-note">불러오는 중입니다.</p><div class="ask-body">${ui.skeleton(6)}</div>`;
    bus.emit("ask:ready", { slot: el.querySelector(".ask-slot") });
    if (source.askForms) bindTyped(el.querySelector(".ak"));
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

  // ---- 키 없는 질문
  function bindTyped(form) {
    const input = form.querySelector("input"), msg = form.querySelector(".ak-msg");
    form.querySelectorAll("[data-eg]").forEach(b => b.addEventListener("click", () => { input.value = b.dataset.eg; form.requestSubmit(); }));
    form.addEventListener("submit", async e => {
      e.preventDefault();
      const text = input.value.trim();
      if (!text) { msg.innerHTML = `<p class="ak-why">질문을 적어 주세요.</p>`; input.focus(); return; }
      msg.innerHTML = `<p class="ak-why">읽는 중입니다.</p>`;
      let idx;
      try { idx = await loadForms(); } catch (err) {
        msg.innerHTML = `<p class="ak-why" role="alert">질문 틀 자료를 받지 못했습니다. 잠시 후 다시 시도해 주세요.</p>`;
        return;
      }
      // 그래프에서 찾아 답하기: 장소와 묻는 것이 분명하면 틀보다 먼저, 틀이 못 알아들으면 그다음에
      const plan = await readRag(text);
      const m = AM.match(idx, text);
      const center = ctx.data && ctx.data.station && ctx.data.station.p;
      if (RAG.ragFirst(plan, m, center) && await showRag(plan, text)) { msg.innerHTML = ""; return; }
      if (m.ok) { msg.innerHTML = ""; showTyped(idx, m, text); return; }
      if (RAG.ragAfter(plan) && await showRag(plan, text)) { msg.innerHTML = ""; return; }
      // 답하지 않을 때는 앞서 보이던 답과 지도 강조를 지워, 옛 답을 이 질문의 답으로 오해하지 않게 한다
      const old = pane && pane.querySelector(".ask-ans");
      if (old && old.innerHTML) { old.innerHTML = ""; bus.emit("spread:clear"); openId = null; }
      msg.innerHTML = `<p class="ak-why">${esc(m.message)}</p>${m.similar && m.similar.length ? `<p class="ak-sim-h">비슷한 질문</p><ul class="ak-sim">${m.similar.map((s, j) =>
        `<li><button type="button" class="btn ghost mini" data-sim="${j}">${esc(s.q)}</button></li>`).join("")}</ul>` : ""}`;
      msg.querySelectorAll("[data-sim]").forEach(b => b.addEventListener("click", () => {
        const s = m.similar[Number(b.dataset.sim)];
        const key = s.item.slots.map(k => String(s.values[k])).join("|");
        msg.innerHTML = "";
        showTyped(idx, { ok: true, item: s.item, values: s.values, combo: s.item.combos[key], notes: [], picked: true }, s.q);
      }));
    });
  }

  // ---- 그래프에서 찾아 답하기 (ask-rag.js)
  async function readRag(text) {
    const res = ctx.results;
    if (!res || !res.index) return null;
    try { await Promise.race([res.ensureBusinesses(), new Promise((_, rej) => setTimeout(() => rej(new Error("늦음")), 4000))]); } catch (e) { /* 상호 없이 읽는다 */ }
    try { return RAG.read(CORE, res.index(), text, { basis: ctx.basis ? ctx.basis.get() : null }); } catch (e) { console.error(e); return null; }
  }
  async function showRag(plan, text) {
    const box = pane && pane.querySelector(".ask-ans");
    if (!box) return false;
    const get = p => source.card(p).catch(() => null);
    const cards = {};
    await Promise.all(plan.ents.map(async e => { cards[e.path] = await get(e.path); }));
    await Promise.all(Object.values(cards).flatMap(c => RAG.follow(c)).map(async p => { cards[p] = await get(p); }));
    const a = RAG.answer(plan, cards);
    if (!a.ok) return false;
    openId = null;
    const ev = a.evidence.slice(0, 20).map(x => `<li><span class="rg-k">${esc(x.label)}</span> ${x.path
      ? `<button type="button" class="linkish" data-path="${esc(x.path)}">${esc(x.value)}</button>` : `<span>${esc(x.value)}</span>`}${x.note ? ` <span class="small muted">${esc(x.note)}</span>` : ""}</li>`).join("");
    const paths = [...new Set(a.paths.filter(Boolean))];
    box.innerHTML = `<section class="ask-answer" aria-live="polite"><p class="ak-tag">입력한 질문, 그래프에서 찾은 답</p><h2 tabindex="-1">${esc(text)}</h2>
        ${a.sentences.map(t => `<p>${esc(t)}</p>`).join("")}
        <div class="ask-acts">${paths.length ? `<button type="button" class="btn ghost mini ask-show">지도에 ${nf(paths.length)}곳 표시</button>` : ""}
        <button type="button" class="btn ghost mini ask-close">답 닫기</button></div></section>
      ${ev ? `<h3 class="ask-h">근거</h3><ul class="rg-ev">${ev}</ul>` : ""}
      <details class="ask-sparql" open><summary>어떻게 찾았나요</summary>
        <p>${esc(a.how)} 질문 틀에 맞는 질문이 아니어서 이 방법으로 답했습니다. AI도 API도 쓰지 않았고, 문장은 정해 둔 틀에 근거 값을 넣은 것입니다.</p></details>`;
    box.querySelectorAll("[data-path]").forEach(b => b.addEventListener("click", () => openPath(b.dataset.path)));
    const showMap = () => {
      if (!paths.length) { bus.emit("spread:clear"); return; }
      bus.emit("spread", { paths, label: text, owner: "ask" });
      ctx.mapApi.fitPlaces(paths, { maxZoom: 17 });
    };
    const sb = box.querySelector(".ask-show");
    if (sb) sb.addEventListener("click", showMap);
    box.querySelector(".ask-close").addEventListener("click", () => { box.innerHTML = ""; bus.emit("spread:clear"); const b = pane.querySelector("#ak-q"); if (b) b.focus(); });
    showMap();
    pane.closest(".panel-body") && (pane.closest(".panel-body").scrollTop = 0);
    box.querySelector("h2").focus({ preventScroll: true });
    return true;
  }

  function showTyped(idx, m, text) {
    const box = pane && pane.querySelector(".ask-ans");
    if (!box) return;
    const fills = m.item.slots.map(k => `${AM.LABEL[k]} ${AM.valueText(k, m.values[k])}`).join(", ");
    const how = `<p>${m.picked ? `비슷한 질문 목록에서 고른 질문입니다.` : `입력한 질문을 질문 틀 '${esc(AM.fillText(m.item.q, m.item.defaults))}'에 맞춰 읽었습니다.
      가장 가까운 말투는 '${esc(AM.fillForm(m.form, m.values))}'이고 글자 조각 유사도는 ${m.raw.toFixed(2)}입니다.`}${fills ? ` 빈칸 값: ${esc(fills)}.` : ""}
      ${(m.notes || []).map(esc).join(" ")} 아래 쿼리를 빌드 때 공개 범위 그래프에서 실행한 결과입니다. AI는 쓰지 않았습니다.</p>`;
    if (!m.combo || !Array.isArray(m.combo)) {
      const big = m.combo && m.combo.big;
      box.innerHTML = `<section class="ask-answer" aria-live="polite"><h2 tabindex="-1">${esc(AM.fillText(m.item.q, m.values))}</h2>
        <p>${big ? `결과가 ${nf(big)}행으로 ${nf(idx.ask.maxRows)}행을 넘어 미리 담아 두지 않았습니다. 조건을 좁혀 다시 물어 주세요.` : "이 조합은 미리 실행해 두지 않았습니다."}</p></section>`;
      bus.emit("spread:clear");
      box.querySelector("h2").focus({ preventScroll: true });
      return;
    }
    const q = AM.answerOf(idx, m, (ctx.data && ctx.data.byIndex) || []);
    openId = null;
    show(q, { focus: true, typed: true, how });
  }

  // 질문 하나의 답 (목록에서)
  async function answer(id, { focus = true } = {}) {
    openId = id;
    let qs;
    try { qs = await load(); } catch (e) { return; }
    const q = qs.items.find(x => x.id === id);
    if (!q || !pane) return;
    show(q, { focus });
  }

  function show(q, { focus = true, typed = false, how = "" } = {}) {
    const box = pane && pane.querySelector(".ask-ans");
    if (!box) return;
    let shown = Math.min(ROWS, q.rows.length);
    const head = `<tr>${q.cols.map(c => `<th scope="col">${esc(c.label)}</th>`).join("")}</tr>`;
    const body = () => q.rows.slice(0, shown).map(r => `<tr>${r.map(v => `<td>${cell(v)}</td>`).join("")}</tr>`).join("");
    const mapN = (q.map || []).length;
    const near = /\{\{NEAR:/.test(q.sparql || "") ? " {{NEAR:장소:거리}} 자리는 그 장소를 중심으로 주어진 거리만큼의 원으로 바꿔 실행했습니다." : "";
    const from = q.from ? ` 평가 질문 ${esc(q.from)}의 정답 쿼리를 바탕으로 했습니다.` : "";
    box.innerHTML = `<section class="ask-answer" aria-live="polite">${typed ? `<p class="ak-tag">입력한 질문</p>` : ""}<h2 tabindex="-1">${esc(q.q)}</h2><p>${esc(q.answer)}</p>
        <div class="ask-acts">${mapN ? `<button type="button" class="btn ghost mini ask-show">지도에 ${nf(mapN)}곳 표시</button>` : ""}
        <button type="button" class="btn ghost mini ask-close">답 닫기</button></div></section>
      ${q.rows.length ? `<div class="ask-table-wrap"><table class="ask-table"><caption class="sr-only">${esc(q.q)} 표, ${nf(q.rows.length)}행</caption>
        <thead>${head}</thead><tbody>${body()}</tbody></table></div>
        ${q.rows.length > shown ? `<p class="small muted ask-more-wrap">${nf(q.rows.length)}행 가운데 ${nf(shown)}행을 보여 줍니다. 지도에는 모두 표시합니다.
          <button type="button" class="btn ghost mini ask-more">더 보기</button></p>` : ""}` : `<p class="muted">표로 보일 행이 없습니다.</p>`}
      <details class="ask-sparql"${typed ? " open" : ""}><summary>어떻게 찾았나요 (실행한 SPARQL 쿼리)</summary>
        ${how}<pre>${esc(q.sparql || "")}</pre>
        <p>공개 범위 그래프에서 빌드 때 실행했습니다.${near}${from}</p></details>`;
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
    const showMap = () => {
      if (!mapN) { bus.emit("spread:clear"); return; }
      bus.emit("spread", { paths: q.map, label: q.q, owner: "ask" });
      ctx.mapApi.fitPlaces(q.map, { maxZoom: 17 });
    };
    const sb = box.querySelector(".ask-show");
    if (sb) sb.addEventListener("click", showMap);
    box.querySelector(".ask-close").addEventListener("click", () => {
      openId = null;
      box.innerHTML = "";
      bus.emit("spread:clear");
      const b = typed ? pane.querySelector("#ak-q") : pane.querySelector(`.ask-q[data-id="${CSS.escape(q.id)}"]`);
      if (b) b.focus();
    });
    showMap();
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
