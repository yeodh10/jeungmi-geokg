// 분류 칩 (명세 3.5절 6번, 9절, 0-1절). 칩 규칙과 건수는 places.json의 chips(config/use_groups.yaml)에서만 받는다.
// 칩을 누르면 목록과 번호 표식이 함께 나오고, 첫 결과를 고르지 않으며 지도 범위도 그대로 둔다. 주소창에 cat이 남는다.
// 첫 줄: first가 참인 칩과 상호 칩. 나머지와 조건 찾기는 더보기에 둔다.

const V = "?v=" + encodeURIComponent((window.APP_CONFIG || {}).build || "");
const app = (window.APP_CONFIG || {}).app || ".";
const core = await import(`${app}/search-core.js${V}`);
const nf = n => Number(n).toLocaleString("ko-KR");

export async function init(ctx) {
  const { bus, state, ui, data, panel } = ctx;
  if (!ctx.results) return;   // 결과 목록(search)이 없으면 칩도 두지 않는다 (누를 곳만 남지 않게)
  ctx.css("tools.css");
  const chips = (data.chips || []).filter(c => (c.n || 0) >= 5);
  const first = chips.filter(c => c.first || c.kind === "biz");
  const rest = chips.filter(c => !first.includes(c));
  const top = document.getElementById("map-top");
  const row = document.createElement("div");
  row.className = "chips";
  row.setAttribute("role", "group");
  row.setAttribute("aria-label", "분류로 찾기");
  const chipBtn = c => `<button type="button" class="chip-btn" data-key="${esc(c.key)}" aria-pressed="false">${esc(c.label)}</button>`;
  const hasCond = ctx.features.has("conditions");
  row.innerHTML = first.map(chipBtn).join("") + (rest.length || hasCond
    ? `<button type="button" class="chip-btn chip-more" aria-expanded="false" aria-controls="chip-more-pop">더보기 ${ui.icon("down", 16)}</button>` : "");
  top.appendChild(row);
  const more = row.querySelector(".chip-more");
  const pop = document.createElement("div");
  pop.id = "chip-more-pop";
  pop.className = "tool-pop chip-pop";
  pop.hidden = true;
  pop.setAttribute("role", "group");
  pop.setAttribute("aria-label", "더 많은 분류");
  pop.innerHTML = `<div class="chip-pop-list">${rest.map(c => `<button type="button" class="chip-pop-item" data-key="${esc(c.key)}" aria-pressed="false">
      <span>${esc(c.label)}</span><span class="muted">${nf(c.n)}곳</span></button>`).join("")}</div>
    ${hasCond ? `<button type="button" class="btn ghost chip-cond">${ui.icon("layers", 16)}조건 찾기 (용도, 층수, 연도, 역 거리)</button>` : ""}`;
  document.body.appendChild(pop);
  // 휴대폰에서 칩이 생기면 지도 위 여백을 다시 잰다 (app.js padding이 칩 줄 아래를 본다)
  if (ctx.sheet && ctx.sheet.active()) dispatchEvent(new Event("resize"));

  let current = null;
  const byKey = new Map(chips.map(c => [c.key, c]));
  function syncPressed() {
    document.querySelectorAll(".chip-btn[data-key], .chip-pop-item").forEach(b => b.setAttribute("aria-pressed", String(b.dataset.key === current)));
    if (more) more.classList.toggle("on", !!current && rest.some(c => c.key === current));
  }

  function openPop(on) {
    if (!more) return;
    pop.hidden = !on;
    more.setAttribute("aria-expanded", String(on));
    if (on) {
      const r = more.getBoundingClientRect();
      const w = Math.min(300, innerWidth - 16);
      pop.style.left = Math.max(8, Math.min(r.left, innerWidth - w - 8)) + "px";
      pop.style.top = r.bottom + 6 + "px";
      pop.style.width = w + "px";
      (pop.querySelector("button") || pop).focus();
    }
  }
  if (more) more.addEventListener("click", () => openPop(pop.hidden));
  pop.addEventListener("keydown", e => { if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); openPop(false); more.focus(); } });
  document.addEventListener("pointerdown", e => { if (!pop.hidden && !pop.contains(e.target) && !more.contains(e.target)) openPop(false); });
  pop.addEventListener("focusout", e => { if (e.relatedTarget && !pop.contains(e.relatedTarget) && e.relatedTarget !== more) openPop(false); });
  const cond = pop.querySelector(".chip-cond");
  if (cond) cond.addEventListener("click", () => { openPop(false); bus.emit("conditions:open", { opener: more }); });

  // restore: 뒤로 가기나 첫 화면에서 주소창 값대로 목록만 다시 세운다 (주소창은 그대로, 열려 있던 장소는 목록 위에 다시 연다)
  async function pick(key, { push = true, toggle = true, restore = false } = {}) {
    const c = byKey.get(key);
    if (!c) { if (restore && state.get("cat") === key) state.set({ cat: null }, { silent: true }); return; }   // 없는 칩은 주소창에서 지운다
    if (toggle && current === key) { ctx.results.close({ push: true }); return; }
    current = key;
    syncPressed();
    let items = null;
    if (c.kind === "biz") {
      ctx.results.show({ kind: "chip", key: "chip:" + key, title: c.label, items: [], empty: `${ui.skeleton(3)}`, quiet: true, loading: true });
      try { await ctx.results.ensureBusinesses(); }
      catch (e) {
        ctx.results.show({ kind: "chip", key: "chip:" + key, title: c.label, items: [],
          empty: `<p>상호 자료를 받지 못했습니다.</p><div class="res-acts"><button type="button" class="btn" data-retry>다시 시도</button></div>` });
        const b = document.querySelector(".view-results [data-retry]");
        if (b) b.onclick = () => pick(key, { push: false, toggle: false });
        return;
      }
      if (current !== key) return;
    }
    items = core.chipItems(ctx.results.index(), c) || [];
    if (panel.has("place")) bus.emit("deselect");
    if (!restore) state.set({ cat: key, q: null, focus: null, unit: null }, { push, silent: true });
    document.getElementById("q").value = "";
    const note = c.kind === "biz" ? "공공데이터(소상공인 상가정보 2026년 9월 기준)에 등록된 상호만 나옵니다. 영업시간, 리뷰는 없습니다." : c.note || "";
    ctx.results.show({ kind: "chip", key: "chip:" + key, title: c.label, items, extraNote: note, sort: "near", onClear: () => { current = null; syncPressed(); },
      empty: `<p>맞는 곳이 없습니다.</p>`, quiet: restore });
    if (restore) { ctx.results.reopenFocus(); return; }
    ui.live(`${c.label} ${nf(items.length)}곳을 목록과 지도에 표시했습니다`);
  }

  row.addEventListener("click", e => {
    const b = e.target.closest(".chip-btn[data-key]");
    if (b) pick(b.dataset.key);
  });
  pop.addEventListener("click", e => {
    const b = e.target.closest(".chip-pop-item");
    if (b) { openPop(false); pick(b.dataset.key); }
  });
  // 빈 결과 안내의 칩 단추
  bus.on("chips:pick", ({ key } = {}) => pick(key, { toggle: false }));
  bus.on("chips:clear", () => { current = null; syncPressed(); });

  // 뒤로 가기, 앞으로 가기
  state.onChange((changed, s, source) => {
    if (source !== "pop" || !changed.includes("cat")) return;
    if (s.cat) pick(s.cat, { push: false, toggle: false, restore: true });
    else if (current) { current = null; syncPressed(); const r = ctx.results.current; if (r && r.kind === "chip") ctx.results.clear(); }
  });
  const c0 = state.get("cat");
  if (c0 && !state.get("q")) pick(c0, { push: false, toggle: false, restore: true });
}
