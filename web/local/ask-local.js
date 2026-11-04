// 로컬 서버의 자유 질문 (명세 3.7절, 10절 WP6). 묻기 탭 맨 위의 빈 칸(ask:ready의 slot)에 입력칸을 넣는다.
// 보내기 전에 키 상태를 보여 준다. 키가 없으면 보내지 않는다 (서버도 503으로 답한다).
// 답은 묻기 탭의 미리 실행한 답과 같은 꼴로 보여 준다: 한 문장 답, 지도 보라색 번짐, 표(처음 50행과 더 보기), 접힌 쿼리.
// 답에는 AI가 만든 쿼리라고 적는다. 서버(/api/ask)가 Claude API를 부르고, 쿼리는 이 컴퓨터의 전체 그래프에서 돈다.

const ROWS = 50;
const MAX = 2000;   // 서버의 본문 상한과 맞춘 질문 글자 수
const EXAMPLES = ["증미역에서 300m 안의 학교", "지상 15층 이상인 공동주택", "등촌동의 도로명주소 없는 건물"];
const CSS = `
.al { display: grid; gap: 8px; margin: 0 0 16px; padding: 12px; border: 1px solid var(--graph-line); border-radius: var(--radius); background: var(--graph-soft); }
.al h3 { margin: 0; font-size: 14px; }
.al textarea { width: 100%; min-height: 64px; resize: vertical; font: inherit; padding: 8px 10px; border: 1px solid var(--line-2); border-radius: 8px;
  background: var(--panel); color: var(--text); }
.al .al-key { margin: 0; font-size: 12px; color: var(--text-2); }
.al .al-key.no { color: var(--warn); }
.al .al-row { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
.al .al-row .btn { min-height: 44px; }
.al .al-eg { display: flex; gap: 6px; flex-wrap: wrap; }
.al .al-eg button { min-height: 36px; padding: 0 10px; border-radius: 999px; border: 1px solid var(--line); background: var(--panel); color: var(--text-2); font-size: 12px; }
.al-out:empty { display: none; }
.al-out .al-ai { display: inline-block; font-size: 12px; color: var(--graph); border: 1px solid var(--graph-line); border-radius: 999px; padding: 1px 8px; margin: 0 0 6px; }
.al-out .al-err { color: var(--warn); }
`;

export function init(ctx) {
  const { bus, ui, source, data } = ctx;
  if (ctx.cfg.mode !== "local") return;
  const caps = source.caps || {};
  const style = document.createElement("style");
  style.textContent = CSS;
  document.head.appendChild(style);
  const nf = n => Number(n).toLocaleString("ko-KR");
  const BASE = "https://yeodh10.github.io/jeungmi-geokg/id/";
  const pathOf = v => (typeof v === "string" && v.startsWith(BASE) && !v.endsWith("/geometry") ? v.slice(BASE.length) : null);
  let busy = false;

  bus.on("ask:ready", ({ slot } = {}) => {
    if (!slot) return;
    const key = !!caps.key;
    slot.innerHTML = `<form class="al" novalidate>
      <h3><label for="al-q">자유 질문 (로컬 서버)</label></h3>
      <p class="al-key${key ? "" : " no"}" id="al-key">${key
        ? "Claude API 키가 있습니다. 보내면 질문이 Claude API로 가고 유료 호출이 1~2번 생깁니다. 만든 쿼리는 이 컴퓨터의 전체 그래프에서 돕니다."
        : "Claude API 키가 없어 자유 질문을 보낼 수 없습니다. 저장소의 .env에 ANTHROPIC_API_KEY를 넣고 서버를 다시 켜 주세요. 아래 미리 실행한 답은 그대로 볼 수 있습니다."}</p>
      <textarea id="al-q" maxlength="${MAX}" aria-describedby="al-key" placeholder="예: 증미역에서 300m 안의 학교"${key ? "" : " disabled"}></textarea>
      <div class="al-eg" role="group" aria-label="질문 예시">${EXAMPLES.map(t => `<button type="button" data-eg="${esc(t)}"${key ? "" : " disabled"}>${esc(t)}</button>`).join("")}</div>
      <div class="al-row"><button type="submit" class="btn"${key ? "" : " disabled"}>${ui.icon("search", 16)}묻기</button>
        <span class="small muted" aria-live="polite"></span></div>
    </form><div class="al-out"></div>`;
    const form = slot.querySelector("form"), ta = slot.querySelector("textarea"), out = slot.querySelector(".al-out");
    const status = slot.querySelector(".al-row .small");
    slot.querySelectorAll("[data-eg]").forEach(b => b.addEventListener("click", () => { ta.value = b.dataset.eg; ta.focus(); }));
    ta.addEventListener("keydown", e => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); form.requestSubmit(); } });
    form.addEventListener("submit", async e => {
      e.preventDefault();
      const q = ta.value.trim();
      if (!key) { status.textContent = "키가 없어 보내지 않았습니다."; return; }
      if (!q) { status.textContent = "질문을 적어 주세요."; ta.focus(); return; }
      if (busy) return;
      busy = true;
      form.querySelector("button[type=submit]").disabled = true;
      status.textContent = "묻는 중입니다. 쿼리를 만들고 실행하는 데 수십 초 걸릴 수 있습니다.";
      out.innerHTML = ui.skeleton(4);
      try {
        const r = await source.ask(q);
        status.textContent = "";
        show(out, r);
      } catch (err) {
        status.textContent = "";
        out.innerHTML = `<p class="al-err" role="alert">${esc(err.message || "질문을 처리하지 못했습니다")}</p>`;
      } finally {
        busy = false;
        form.querySelector("button[type=submit]").disabled = false;
      }
    });
  });

  function nameOf(r, v) {
    const p = pathOf(v);
    if (p && data.byPath.has(p)) return ctx.data.nameOf(p);
    const n = r.names && r.names[v];
    return (n && n.label) || (p || v);
  }

  function cell(r, v) {
    if (v == null) return "";
    const p = pathOf(v);
    if (p) return `<button type="button" class="linkish" data-path="${esc(p)}">${esc(nameOf(r, v))}</button>`;
    const s = String(v);
    return esc(s.length > 120 ? s.slice(0, 117) + "..." : s);
  }

  // 답 하나 (미리 실행한 답과 같은 꼴)
  function show(box, r) {
    const geo = new Set(r.geometry_vars || []);
    const vars = (r.vars || []).filter(v => !geo.has(v));
    const idx = vars.map(v => r.vars.indexOf(v));
    const rows = r.rows || [];
    const paths = [];
    for (const row of rows) for (const v of row) { const p = pathOf(v); if (p && data.byPath.has(p) && !paths.includes(p)) paths.push(p); }
    let shown = Math.min(ROWS, rows.length);
    const body = () => rows.slice(0, shown).map(row => `<tr>${idx.map(k => `<td>${cell(r, row[k])}</td>`).join("")}</tr>`).join("");
    box.innerHTML = `<section class="ask-answer" aria-live="polite"><span class="al-ai">AI가 만든 쿼리로 찾은 답</span>
        <h2 tabindex="-1">${esc(r.question || "")}</h2>
        <p>${esc(r.explanation || "")} 결과 ${nf(rows.length)}행${r.truncated ? " (앞부분만)" : ""}.</p>
        <div class="ask-acts">${paths.length ? `<button type="button" class="btn ghost mini al-show">지도에 ${nf(paths.length)}곳 보라색으로</button>` : ""}
        <button type="button" class="btn ghost mini al-close">답 닫기</button></div></section>
      ${rows.length ? `<div class="ask-table-wrap"><table class="ask-table"><caption class="sr-only">${esc(r.question || "")} 표, ${nf(rows.length)}행</caption>
        <thead><tr>${vars.map(v => `<th scope="col">${esc(v)}</th>`).join("")}</tr></thead><tbody>${body()}</tbody></table></div>
        ${rows.length > shown ? `<p class="small muted ask-more-wrap">${nf(rows.length)}행 가운데 <span class="n">${nf(shown)}</span>행을 보여 줍니다. 지도에는 모두 표시합니다.
          <button type="button" class="btn ghost mini al-more">더 보기</button></p>` : ""}` : `<p class="muted">결과 행이 없습니다.</p>`}
      <details class="ask-sparql"><summary>어떻게 찾았나요 (AI가 만든 쿼리 SPARQL)</summary><pre>${esc(r.sparql || "")}</pre>
        <p>Claude가 질문을 SPARQL로 옮겼고, 이 컴퓨터의 전체 그래프에서 실행했습니다. 쿼리가 질문과 다를 수 있으니 결과를 확인해 주세요.</p></details>`;
    const bind = () => box.querySelectorAll(".ask-table [data-path]").forEach(b => b.addEventListener("click", () => {
      if (ctx.openPath) ctx.openPath(b.dataset.path, { from: "list" });
      else bus.emit("select", { path: b.dataset.path, from: "list", fit: true });
    }));
    bind();
    const more = box.querySelector(".al-more");
    if (more) more.addEventListener("click", () => {
      shown = Math.min(rows.length, shown + ROWS);
      box.querySelector(".ask-table tbody").innerHTML = body();
      bind();
      if (shown >= rows.length) box.querySelector(".ask-more-wrap").remove();
      else box.querySelector(".ask-more-wrap .n").textContent = nf(shown);
    });
    const spread = () => {
      if (!paths.length) return;
      bus.emit("spread", { paths, label: "자유 질문 답", owner: "ask" });
      ctx.mapApi.fitPlaces(paths, { maxZoom: 17 });
    };
    const sb = box.querySelector(".al-show");
    if (sb) sb.addEventListener("click", spread);
    box.querySelector(".al-close").addEventListener("click", () => { box.innerHTML = ""; bus.emit("spread:clear"); });
    spread();
    box.querySelector("h2").focus({ preventScroll: true });
  }
}
