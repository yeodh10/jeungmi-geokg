// 찾기 (명세 3.5절, 3.12절, WP4): 한 칸 찾기, 자동완성 8개, 결과 목록, 번호 표식, 목록과 표식 연동,
// 이 지도 안에서 다시 찾기, 빈 결과 안내, 휴대폰 전체 화면 입력, 최근 찾기 5개.
// 점수와 묶기는 search-core.js가 한다. 결과 목록은 ctx.results로 칩(chips.js)과 조건 찾기(conditions.js)도 함께 쓴다.

const V = "?v=" + encodeURIComponent((window.APP_CONFIG || {}).build || "");
const app = (window.APP_CONFIG || {}).app || ".";
const core = await import(`${app}/search-core.js${V}`);

const PAGE = 15;
const KIND = { place: "건물", group: "단지", station: "역", road: "길", dong: "동네", zone: "우편구역", sensor: "센서", parcel: "필지", business: "상호" };
const TABS = [["all", "전체"], ["place", "장소"], ["addr", "주소"], ["road", "길"], ["dong", "동네"]];
// 정렬 이름. basis는 거리 기준점을 고른 뒤에만 고를 수 있고, 이름에 그 장소 이름이 들어간다
const SORTS = { score: "맞는 순", basis: "거리 기준에서 가까운 순", center: "지도 가운데서 가까운 순" };
const RECENT_KEY = "jgk-recent";
const nf = n => Number(n).toLocaleString("ko-KR");
const short = a => String(a || "").replace(/^서울특별시\s+/, "");
const distText = m => (m == null || !Number.isFinite(m) ? "" : m >= 1000 ? `${(m / 1000).toFixed(1)}km` : `${nf(m)}m`);

export async function init(ctx) {
  const { bus, state, ui, panel } = ctx;
  ctx.css("search.css");
  const input = document.getElementById("q");
  const bar = document.getElementById("searchbar");
  const sb = bar.querySelector(".sb");
  const index = core.createIndex(ctx.data);
  let bizPromise = null;

  // 상호 자료는 찾기창에 처음 입력할 때 받는다 (첫 화면 300KB 기준). 받으면 장소 색인(byPath)에도 넣어 지도 표식과 주소창이 찾게 한다
  function ensureBusinesses() {
    if (index.biz) return Promise.resolve(index);
    if (!bizPromise) {
      bizPromise = (ctx.source.businesses ? ctx.source.businesses() : Promise.reject(new Error("상호 자료 입구 없음")))
        .then(biz => {
          core.addBusinesses(index, biz);
          for (const b of biz.items || []) {
            if (b.p && !ctx.data.byPath.has(b.p)) ctx.data.byPath.set(b.p, { p: b.p, n: b.n, a: b.a, pt: b.pt, b: b.b, c: b.c, f: b.f, biz: true });
          }
          bus.emit("businesses:ready", { count: (biz.items || []).length });
          return index;
        })
        .catch(e => { bizPromise = null; console.warn("상호 자료를 받지 못했습니다", e && e.message); throw e; });
    }
    return bizPromise;
  }

  // ---- 찾기창: combobox (3.12절)
  input.setAttribute("role", "combobox");
  input.setAttribute("aria-autocomplete", "list");
  input.setAttribute("aria-expanded", "false");
  input.setAttribute("aria-controls", "sug");
  input.placeholder = "장소, 상호, 주소, 지번 찾기";
  const pop = document.createElement("div");
  pop.className = "sug-pop";
  pop.hidden = true;
  pop.innerHTML = `<div class="sug-head" hidden></div><ul id="sug" class="sug" role="listbox" aria-label="찾기 후보"></ul><div class="sug-foot"></div>`;
  bar.appendChild(pop);
  const list = pop.querySelector("#sug"), head = pop.querySelector(".sug-head"), foot = pop.querySelector(".sug-foot");
  // 휴대폰 전체 화면 입력에서 빠져나오는 단추
  const exitBtn = document.createElement("button");
  exitBtn.type = "button";
  exitBtn.className = "sb-btn sb-exit";
  exitBtn.setAttribute("aria-label", "찾기 입력 닫기");
  exitBtn.innerHTML = ui.icon("back");
  sb.insertBefore(exitBtn, sb.firstChild);
  const clearBtn = document.createElement("button");
  clearBtn.type = "button";
  clearBtn.className = "sb-btn sb-clear";
  clearBtn.setAttribute("aria-label", "찾기말 지우기");
  clearBtn.innerHTML = ui.icon("close", 18);
  clearBtn.hidden = true;
  input.after(clearBtn);

  let opts = [];          // 지금 보이는 후보
  let active = -1;
  let lastTyped = "";

  const mobile = () => ctx.sheet && ctx.sheet.active();
  function setFull(on) {
    document.body.classList.toggle("search-full", !!on && mobile());
    if (on && mobile()) fitKeyboard();
  }
  // 키보드가 올라오면 후보 목록 높이를 보이는 영역에 맞춘다 (visualViewport)
  function fitKeyboard() {
    const vv = window.visualViewport;
    const h = vv ? vv.height : window.innerHeight;
    document.documentElement.style.setProperty("--vv-h", Math.round(h) + "px");
  }
  if (window.visualViewport) window.visualViewport.addEventListener("resize", fitKeyboard);

  function openPop(on) {
    pop.hidden = !on;
    input.setAttribute("aria-expanded", String(!!on));
    if (!on) { active = -1; input.removeAttribute("aria-activedescendant"); }
  }

  function readRecent() { try { const v = JSON.parse(localStorage.getItem(RECENT_KEY) || "[]"); return Array.isArray(v) ? v.filter(x => typeof x === "string").slice(0, 5) : []; } catch (e) { return []; } }
  function saveRecent(q) {
    q = String(q || "").trim();
    if (!q) return;
    try { localStorage.setItem(RECENT_KEY, JSON.stringify([q, ...readRecent().filter(x => x !== q)].slice(0, 5))); } catch (e) { /* 저장소가 막히면 기억하지 않는다 */ }
  }

  const subOf = it => {
    if (it.type === "business") return [it.cat, it.floor ? floorText(it.floor) : "", short(it.addr)].filter(Boolean).join(", ");
    if (it.type === "group") return `건물 ${nf(it.size)}동`;
    if (it.type === "road" || it.type === "dong" || it.type === "zone") return it.b != null ? `건물 ${nf(it.b)}동` : "";
    if (it.type === "parcel") return it.m && it.m.length ? `위에 선 건물 ${nf(it.m.length)}동` : "위에 건물 없음";
    return short(it.addr) || it.j || "";
  };
  const kindOf = it => (it.type === "dong" ? (it.kind === "legal" ? "법정동" : "행정동") : KIND[it.type] || "장소");
  function marked(name, q) {
    const r = core.markRange(name, q);
    if (!r) return esc(name);
    return esc(name.slice(0, r[0])) + "<mark>" + esc(name.slice(r[0], r[1])) + "</mark>" + esc(name.slice(r[1]));
  }

  function renderOptions(items, { recent = false, q = "" } = {}) {
    opts = items;
    active = -1;
    input.removeAttribute("aria-activedescendant");
    head.hidden = !recent;
    head.innerHTML = recent ? `<span>최근 찾기</span><button type="button" class="linkish sug-clear">지우기</button>` : "";
    if (recent) head.querySelector(".sug-clear").onclick = () => { try { localStorage.removeItem(RECENT_KEY); } catch (e) { /* 막힘 */ } renderOptions([], { recent: true }); openPop(false); input.focus(); };
    list.innerHTML = items.map((it, k) => recent
      ? `<li id="sug-${k}" role="option" aria-selected="false" class="sug-row" data-k="${k}">${ui.icon("search", 16)}<span class="sug-name">${esc(it.q)}</span></li>`
      : `<li id="sug-${k}" role="option" aria-selected="false" class="sug-row" data-k="${k}">
          <span class="sug-kind">${esc(kindOf(it))}</span><span class="sug-text"><span class="sug-name">${marked(it.name, q)}</span>
          <span class="sug-sub">${esc(subOf(it))}</span></span></li>`).join("");
    foot.innerHTML = !recent && q ? `<button type="button" class="sug-all" tabindex="-1">${ui.icon("search", 16)}'${esc(q)}' 결과 모두 보기 <kbd>Enter</kbd></button>` : "";
    const all = foot.querySelector(".sug-all");
    if (all) all.onclick = () => runQuery(input.value, { push: true });
    openPop(items.length > 0 || !!all);
  }

  function setActive(k) {
    const rows = list.querySelectorAll(".sug-row");
    if (!rows.length) return;
    active = (k + rows.length) % rows.length;
    rows.forEach((r, j) => r.setAttribute("aria-selected", String(j === active)));
    input.setAttribute("aria-activedescendant", rows[active].id);
    rows[active].scrollIntoView({ block: "nearest" });
  }

  let typeTimer = 0, typeGen = 0;
  function onType() {
    const q = input.value;
    clearBtn.hidden = !q;
    lastTyped = q;
    clearTimeout(typeTimer);
    if (!q.trim()) { showRecent(); return; }
    typeTimer = setTimeout(() => {
      const gen = ++typeGen;
      // 상호 자료가 늦게 와도, 그사이 찾기를 끝냈거나 다른 곳으로 갔으면 후보를 다시 열지 않는다
      const draw = () => { if (gen !== typeGen || input.value !== q || document.activeElement !== input) return; const r = core.suggest(index, q, 8); renderOptions(r.items, { q }); };
      draw();
      if (!index.biz) ensureBusinesses().then(draw).catch(() => {});
    }, 90);
  }
  function showRecent() {
    const rec = readRecent();
    if (rec.length) renderOptions(rec.map(q => ({ q })), { recent: true });
    else openPop(false);
  }

  input.addEventListener("input", onType);
  // 사람이 찾기창을 누르면 후보(또는 최근 찾기)를 열고 휴대폰은 전체 화면 입력으로 바꾼다.
  // 다른 화면을 닫으며 포커스가 돌아온 것만으로는 열지 않는다 (아래 방향키나 입력으로 연다)
  input.addEventListener("click", () => {
    setFull(true);
    if (!input.value.trim()) showRecent();
    else if (pop.hidden) onType();
  });
  input.addEventListener("focus", () => { if (!index.biz) ensureBusinesses().catch(() => {}); });
  input.addEventListener("keydown", e => {
    if (e.key === "ArrowDown") { e.preventDefault(); if (pop.hidden) onType(); else setActive(active + 1); }
    else if (e.key === "ArrowUp") { e.preventDefault(); if (!pop.hidden) setActive(active - 1); }
    else if (e.key === "Enter") {
      e.preventDefault();
      if (e.isComposing) return;
      if (active >= 0 && opts[active]) choose(opts[active]);
      else if (input.value.trim()) runQuery(input.value, { push: true });
    } else if (e.key === "Escape") {
      if (!pop.hidden) { e.preventDefault(); e.stopPropagation(); openPop(false); }
      else if (document.body.classList.contains("search-full")) { e.preventDefault(); e.stopPropagation(); leaveInput(); }
    }
  });
  list.addEventListener("mousedown", e => e.preventDefault());   // 누르는 동안 입력칸이 포커스를 잃지 않게
  list.addEventListener("click", e => {
    const row = e.target.closest(".sug-row");
    if (row) choose(opts[Number(row.dataset.k)]);
  });
  clearBtn.addEventListener("click", () => { input.value = ""; clearBtn.hidden = true; input.focus(); showRecent(); });
  exitBtn.addEventListener("click", leaveInput);
  function leaveInput() { openPop(false); setFull(false); input.blur(); }
  document.addEventListener("pointerdown", e => { if (!bar.contains(e.target)) { openPop(false); if (!document.body.classList.contains("search-full")) return; setFull(false); } });
  bar.addEventListener("focusout", e => { if (e.relatedTarget && !bar.contains(e.relatedTarget)) { openPop(false); setFull(false); } });

  // 후보 하나를 고른다: 최근 찾기면 그 말로 찾고, 장소면 바로 연다
  function choose(it) {
    if (!it) return;
    typeGen++;
    if (it.q !== undefined && !it.type) { input.value = it.q; runQuery(it.q, { push: true }); return; }
    saveRecent(input.value);
    openPop(false);
    setFull(false);
    openItem(it, { from: "search" });
  }

  // 장소 하나 열기. 필지는 그래프에 있는지 카드로 확인한다 (건물 번호 앞자리에서 만든 필지가 섞여 있다)
  async function openItem(it, { from = "list", no = null } = {}) {
    if (no != null) bus.emit("results:active", { no, from: "list" });
    markProgrammatic();
    if (it.type === "parcel") {
      try {
        const card = await ctx.source.card(it.path);
        bus.emit("select", { path: it.path, card, from, fit: true });
      } catch (e) {
        if (it.m && it.m.length) bus.emit("select", { path: ctx.data.byIndex[it.m[0]].p, from, fit: true });
        else ui.toast("이 필지를 찾지 못했습니다");
      }
      return;
    }
    if (it.type === "business" && !ctx.data.byPath.has(it.path) && it.path && it.path.startsWith("business/")) {
      try { const card = await ctx.source.card(it.path); bus.emit("select", { path: it.path, card, from, fit: true }); }
      catch (e) { ui.toast("이 상호를 찾지 못했습니다"); }
      return;
    }
    bus.emit("select", { path: it.path, group: it.group, from, fit: true });
  }

  // ---- 결과 목록 (3.5절 3~8번)
  // R: {key, title, kind:"query"|"chip"|"cond", q, items(전체), base(다시 찾기 전 전체), note, extraNote, sort, tab, page, bounds, empty}
  let R = null;
  let ownMarkers = false;
  let programmaticAt = 0;
  const markProgrammatic = () => { programmaticAt = performance.now(); };
  const research = document.createElement("button");
  research.type = "button";
  research.className = "research";
  research.hidden = true;
  research.innerHTML = `${ui.icon("refresh", 16)}이 지도 안에서 다시 찾기`;
  document.body.appendChild(research);

  function shown() {
    if (!R) return [];
    let a = R.tab === "all" ? R.items : R.items.filter(x => x.tab === R.tab);
    const c = ctx.map ? ctx.map.getCenter() : null;
    const b = ctx.basis.get();
    a = R.sort === "score" ? a : core.sortItems(a, R.sort, c ? [c.lng, c.lat] : null, b && b.pt);
    return a;
  }

  function emitMarkers() {
    const rows = shown().slice(0, R.page * PAGE);
    ownMarkers = true;
    bus.emit("results:show", { title: R.title, items: rows.map((x, k) => ({ no: k + 1, path: x.path || undefined, group: x.group, pt: x.pt })).filter(x => x.pt) });
    ownMarkers = false;
  }

  // '가까운 순'으로 부르면 기준점이 있을 때는 기준점, 없으면 지도 가운데에서 잰다 (예전 이름 station도 같다)
  const sortOf = k => (k === "near" || k === "station" || k === "basis" ? (ctx.basis.get() ? "basis" : "center") : k);
  const sortName = k => (k === "basis" && ctx.basis.get() ? `${ctx.basis.get().n}에서 가까운 순` : SORTS[k]);
  bus.on("basis:change", () => {
    if (!R) return;
    if (R.sort === "basis" && !ctx.basis.get()) R.sort = "center";
    const v = document.querySelector(".view-results .view-body");
    if (v && v.offsetParent) { renderResults(v); emitMarkers(); }   // 목록이 보일 때만 다시 그린다 (번호 표식과 순서를 맞춘다)
  });

  function show(opt) {
    R = {
      key: opt.key || opt.title, title: opt.title, kind: opt.kind || "query", q: opt.q || "", chip: opt.chip || null, cond: opt.cond || null,
      items: opt.items || [], base: opt.items || [], note: opt.note || null, extraNote: opt.extraNote || "", sort: sortOf(opt.sort || (opt.kind === "query" ? "score" : "near")),
      tab: "all", page: 1, bounds: null, empty: opt.empty || null, onClear: opt.onClear || null, loading: !!opt.loading,
    };
    research.hidden = true;
    // PC에서 패널을 접어 두었으면 결과가 있거나 찾는 중일 때 접힌 채로 둔다 (명세 3.2절). 결과가 없으면 안내를 보이게 편다
    const keepFolded = panel.isCollapsed() && !(ctx.sheet && ctx.sheet.active()) && (R.items.length > 0 || R.loading);
    panel.show({ id: "results", title: R.title, focus: !!opt.focus && !keepFolded, expand: !keepFolded,
      onBack: () => closeResults({ push: true }), render: el => renderResults(el) });
    emitMarkers();
    if (!opt.quiet) ui.live(R.items.length ? `${R.title}: ${nf(R.items.length)}곳을 찾았습니다` : `${R.title}: 찾은 곳이 없습니다`);
    if (ctx.sheet && ctx.sheet.active() && ctx.sheet.current() === "peek") ctx.sheet.snap("half");
    return R;
  }

  function closeResults({ push = false, keepState = false } = {}) {
    const had = R;
    R = null;
    research.hidden = true;
    bus.emit("results:clear");
    panel.remove("results");
    if (had && had.onClear) had.onClear();
    if (!keepState) state.set({ q: null, cat: null }, { push, silent: true });
    bus.emit("search:closed", {});
  }

  function renderResults(el) {
    if (!R) return;
    const counts = { all: R.items.length };
    for (const x of R.items) counts[x.tab] = (counts[x.tab] || 0) + 1;
    const tabs = R.kind === "query" && R.items.length ? TABS.filter(([k]) => k === "all" || counts[k]) : [];
    const rows = shown();
    const page = rows.slice(0, R.page * PAGE);
    const sortOpts = Object.keys(SORTS).filter(k => (k !== "score" || R.kind === "query") && (k !== "basis" || ctx.basis.get())).map(k => [k, sortName(k)]);
    el.innerHTML = `<div class="res">
      <h2 class="res-title" tabindex="-1">${esc(R.title)}</h2>
      <p class="res-count" aria-live="polite">${R.loading ? "불러오는 중입니다" : `${R.bounds ? "지금 지도 안 " : ""}${nf(R.items.length)}곳`}${R.bounds ? ` <button type="button" class="linkish res-all">전체 범위로</button>` : ""}</p>
      ${R.note && R.note.kind === "partial" ? `<p class="res-note">${esc(R.note.text)}</p>` : ""}
      ${R.extraNote ? `<p class="res-note">${esc(R.extraNote)}</p>` : ""}
      ${tabs.length > 2 ? `<div class="res-tabs" role="group" aria-label="결과 구분">${tabs.map(([k, label]) =>
        `<button type="button" class="res-tab" data-tab="${k}" aria-pressed="${R.tab === k}">${label} <span>${nf(counts[k] || 0)}</span></button>`).join("")}</div>` : ""}
      ${R.items.length > 1 ? `<div class="res-sort"><label for="res-sort">정렬</label><select id="res-sort">${sortOpts.map(([k, v]) =>
        `<option value="${k}"${R.sort === k ? " selected" : ""}>${v}</option>`).join("")}</select></div>` : ""}
      ${rows.length ? `<ol class="res-list">${page.map((x, k) => rowHtml(x, k + 1)).join("")}</ol>` : emptyHtml()}
      ${rows.length > page.length ? `<button type="button" class="btn ghost res-more">더 보기 (${nf(rows.length - page.length)}곳 남음)</button>` : ""}
    </div>`;
    el.querySelectorAll(".res-tab").forEach(b => b.onclick = () => { R.tab = b.dataset.tab; R.page = 1; rerender(); emitMarkers(); });
    const sel = el.querySelector("#res-sort");
    if (sel) sel.onchange = () => { R.sort = sel.value; R.page = 1; rerender(); emitMarkers(); ui.live(`${sortName(R.sort)}으로 정렬했습니다`); };
    const more = el.querySelector(".res-more");
    if (more) more.onclick = () => { R.page += 1; const k = (R.page - 1) * PAGE; rerender(); emitMarkers(); const b = el.querySelectorAll(".res-row")[k]; if (b) b.focus(); };
    const all = el.querySelector(".res-all");
    if (all) all.onclick = () => { R.items = R.base; R.bounds = null; R.page = 1; rerender(); emitMarkers(); fitArea(); };
    el.querySelectorAll(".res-row").forEach(b => b.onclick = () => {
      const no = Number(b.dataset.no);
      const x = rows[no - 1];
      if (!x) return;
      if (!ownMarkersOnMap) emitMarkers();
      setRowActive(no);
      openItem(x, { from: "list", no });
    });
    wireEmpty(el);
  }
  function rerender() { const v = document.querySelector(".view-results .view-body"); if (v) renderResults(v); }

  function rowHtml(x, no) {
    const sub = subOf(x);
    const bp = ctx.basis.get(), bd = bp && (!x.path || x.path !== bp.p) ? ctx.basis.dist(x.pt) : null;
    const dist = Number.isFinite(bd) ? `${bp.n}에서 직선 ${distText(bd)}` : "";
    const extra = x.hit && x.size ? `조건에 맞는 건물 ${nf(x.hit)}동` : "";
    return `<li><button type="button" class="res-row" data-no="${no}">
      <span class="res-no" aria-hidden="true">${no}</span>
      <span class="res-text"><span class="res-name">${esc(x.name)}</span>
      <span class="res-sub"><span class="res-kind">${esc(kindOf(x))}</span>${sub ? ` ${esc(sub)}` : ""}</span>
      ${dist || extra ? `<span class="res-dist">${esc([extra, dist].filter(Boolean).join(", "))}</span>` : ""}
      ${x.src ? `<span class="res-dist">${esc(x.src.note)}</span>` : ""}</span>
      <span class="sr-only">${no}번</span></button></li>`;
  }

  // 빈 결과 (3.5절 표, 3.11절 다시 찾기 0건). 막다른 길로 두지 않고 다음 행동을 붙인다
  function emptyHtml() {
    if (R.bounds) return `<div class="res-empty"><p>지금 보이는 지도 안에는 결과가 없습니다.</p>
      <div class="res-acts"><button type="button" class="btn res-all2">전체 범위로 다시 찾기</button></div></div>`;
    if (R.empty) return `<div class="res-empty">${R.empty}</div>`;
    const n = R.note || { kind: "none", text: "맞는 장소를 찾지 못했습니다." };
    const chips = (ctx.data.chips || []).filter(c => c.kind === "biz" || c.key === "shop").slice(0, 7);
    const ex = ["양천로 550", "등촌동 666-40", "증미역"];
    let acts = "";
    if (n.kind === "absent") acts = `<p class="small muted">대신 이렇게 찾을 수 있습니다.</p><div class="res-chips">${chips.map(c => `<button type="button" class="chip-btn" data-chip="${esc(c.key)}">${esc(c.label)}</button>`).join("")}</div>`;
    else if (n.kind === "outside") acts = `<div class="res-acts"><button type="button" class="btn res-area">증미역 반경 1km 전체 보기</button></div>`;
    acts += `<p class="small muted">이렇게 찾아보세요.</p><div class="res-chips">${ex.map(q => `<button type="button" class="chip-btn" data-q="${esc(q)}">${esc(q)}</button>`).join("")}</div>`;
    acts += `<div class="res-qs" hidden><p class="small muted">미리 실행한 질문</p><ul></ul></div>`;
    return `<div class="res-empty"><p class="res-empty-msg">${esc(n.text)}</p>${acts}</div>`;
  }
  function wireEmpty(el) {
    el.querySelectorAll("[data-q]").forEach(b => b.onclick = () => { input.value = b.dataset.q; runQuery(b.dataset.q, { push: true }); });
    el.querySelectorAll("[data-chip]").forEach(b => b.onclick = () => bus.emit("chips:pick", { key: b.dataset.chip }));
    const area = el.querySelector(".res-area");
    if (area) area.onclick = fitArea;
    const all2 = el.querySelector(".res-all2");
    if (all2) all2.onclick = () => { R.items = R.base; R.bounds = null; R.page = 1; rerender(); emitMarkers(); fitArea(); };
    const qs = el.querySelector(".res-qs");
    if (qs && ctx.features.has("ask") && (Array.isArray(ctx.data.featured) || ctx.source.questions)) {
      (Array.isArray(ctx.data.featured) ? Promise.resolve({ items: ctx.data.featured.map(x => ({ ...x, featured: true })) }) : ctx.source.questions()).then(d => {
        const items = (d && d.items || []).filter(x => x.featured).slice(0, 3);
        if (!items.length || !document.contains(qs)) return;
        qs.querySelector("ul").innerHTML = items.map(x => `<li><button type="button" class="linkish" data-ask="${esc(x.id)}">${esc(x.q || x.id)}</button></li>`).join("");
        qs.hidden = false;
        qs.querySelectorAll("[data-ask]").forEach(b => b.onclick = () => bus.emit("ask:open", { id: b.dataset.ask }));
      }).catch(() => {});
    }
  }

  function fitArea() {
    if (!ctx.map) return;
    markProgrammatic();
    const st = ctx.data.station && ctx.data.station.pt;
    ctx.map.easeTo({ center: st || MAP_CENTER, zoom: ctx.sheet && ctx.sheet.active() ? 14 : 14.6, duration: 400 });
  }

  function setRowActive(no) {
    document.querySelectorAll(".res-row.on").forEach(b => b.classList.remove("on"));
    const b = document.querySelector(`.res-row[data-no="${no}"]`);
    if (b) { b.classList.add("on"); b.scrollIntoView({ block: "nearest" }); }
  }

  let ownMarkersOnMap = true;
  bus.on("results:show", () => { ownMarkersOnMap = ownMarkers; });
  bus.on("results:active", ({ no, from } = {}) => { if (from === "map" && R) setRowActive(no); });
  bus.on("select", sel => {
    if (!R || !sel || sel.from === "list") return;
    const rows = shown().slice(0, R.page * PAGE);
    const k = rows.findIndex(x => (sel.group != null && x.group === Number(sel.group)) || (sel.group == null && x.path === sel.path && x.group == null));
    if (k >= 0) { setRowActive(k + 1); bus.emit("results:active", { no: k + 1, from: "search" }); }
  });

  // 지도를 옮기면 목록은 그대로 두고 '이 지도 안에서 다시 찾기'를 띄운다. 우리가 옮긴 것(고르기, 맞추기)은 세지 않는다
  let userMove = false;
  // 사람이 옮긴 것: 끌기, 휠, 두 손가락, 지도 키(originalEvent가 있음)와 도구 줄 단추(확대, 축소, 역, 내 위치).
  // 패널이나 시트 높이에 맞춘 여백 이동과 고르기 이동은 세지 않는다
  let toolAt = -1e9;
  const tools = document.getElementById("map-tools");
  if (tools) for (const ev of ["pointerdown", "keydown"]) tools.addEventListener(ev, () => { toolAt = performance.now(); }, true);
  if (ctx.map) {
    ctx.map.on("movestart", e => {
      if (e.originalEvent || (performance.now() - toolAt < 1500 && performance.now() - programmaticAt > 300)) userMove = true;
    });
  }
  bus.on("map:moved", () => {
    if (!R || !R.base.length) { userMove = false; return; }
    if (!userMove) return;
    userMove = false;
    if (!panel.has("results")) return;
    research.hidden = false;
  });
  research.addEventListener("click", () => {
    if (!R || !ctx.map) return;
    const b = ctx.map.getBounds();
    const bounds = [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()];
    R.items = core.within(R.base, bounds);
    R.bounds = bounds;
    R.page = 1;
    R.tab = "all";
    research.hidden = true;
    if (panel.top() !== "results") panel.remove("place");
    rerender();
    emitMarkers();
    ui.live(R.items.length ? `지금 지도 안에서 ${nf(R.items.length)}곳을 찾았습니다` : "지금 지도 안에는 맞는 곳이 없습니다");
  });
  bus.on("results:clear", () => { if (!ownMarkers && R) research.hidden = true; });

  // ---- 찾기말로 찾기
  // restore: 뒤로 가기나 첫 화면에서 주소창 값대로 목록만 다시 세운다 (주소창을 바꾸지 않고, 열려 있던 장소는 목록 위에 다시 연다)
  async function runQuery(raw, { push = false, auto = true, restore = false } = {}) {
    const q = String(raw || "").trim();
    if (!q) return;
    typeGen++;
    clearTimeout(typeTimer);
    openPop(false);
    setFull(false);
    saveRecent(q);
    input.value = q;
    clearBtn.hidden = false;
    if (!index.biz) {
      // 상호 자료(약 800KB)를 기다리는 동안 빈 화면으로 두지 않는다. 4초가 넘으면 건물과 주소만으로 찾는다
      show({ kind: "query", q, title: `'${q}' 찾는 중`, items: [], empty: ui.skeleton(4), quiet: true, loading: true });
      try { await Promise.race([ensureBusinesses(), new Promise((_, rej) => setTimeout(() => rej(new Error("늦음")), 4000))]); }
      catch (e) { /* 상호 없이 찾는다 */ }
      if (input.value.trim() !== q && document.activeElement === input) return;   // 그사이 다른 말을 입력했다
    }
    const r = core.search(index, q);
    // 새로 찾으면 열려 있던 장소는 닫는다 (뒤로 가기로 이 결과에 돌아왔을 때 옛 장소가 다시 열리지 않게)
    if (panel.has("place")) bus.emit("deselect");
    if (!restore) state.set({ q, cat: null, focus: null, unit: null }, { push, silent: true });
    bus.emit("chips:clear");
    const shownQ = r.typo ? r.typo.to : q;
    show({ kind: "query", q: shownQ, title: `'${shownQ}' 찾은 결과`, items: r.items, note: r.note, sort: "score", focus: !restore && (!auto || !r.items.length), quiet: restore && !!state.get("focus"),
      extraNote: r.typo ? `'${r.typo.from}'을 한글 자판으로 바꿔 '${r.typo.to}'(으)로 찾았습니다.` : "" });
    if (restore) { reopenFocus(); return r; }
    if (!index.biz) R.extraNote = "상호 자료를 받지 못해 건물과 주소만 찾았습니다.";
    // 패널을 접은 채 찾으면 패널을 펴지 않고 지도를 결과 범위에 맞춘다 (명세 3.2절). 목록은 패널을 펴면 그대로 있다
    if (panel.isCollapsed() && !(ctx.sheet && ctx.sheet.active()) && r.items.length) {
      fitResults();
      ui.live(`'${q}' ${nf(r.items.length)}곳을 찾아 지도를 결과 범위에 맞췄습니다. 패널을 펴면 목록이 보입니다`);
      return r;
    }
    // 찾기말 결과는 첫 결과를 자동으로 고르고 지도를 옮긴다 (칩 결과는 고르지 않는다).
    // 일부만 찾는 낱말(병원, 은행 등)은 '이름에 낱말이 든 곳만'이라는 안내가 카드에 가리지 않게 목록만 보여 준다
    if (auto && r.items.length && !(r.note && (r.note.kind === "absent" || r.note.kind === "partial"))) {
      const first = shown()[0];
      setRowActive(1);
      openItem(first, { from: "search", no: 1 });
    }
    return r;
  }

  // 결과 첫 쪽의 점이 모두 보이게 지도를 맞춘다 (접힌 패널에서 찾을 때)
  function fitResults() {
    if (!ctx.map || !R) return;
    const pts = shown().slice(0, R.page * PAGE).map(x => x.pt).filter(p => Array.isArray(p) && p.length >= 2);
    if (!pts.length) return;
    const b = new maplibregl.LngLatBounds(pts[0], pts[0]);
    pts.forEach(p => b.extend(p));
    const dur = matchMedia("(prefers-reduced-motion: reduce)").matches ? 0 : 400;
    if (b.getNorthEast().distanceTo(b.getSouthWest()) < 60) ctx.map.easeTo({ center: b.getCenter(), zoom: Math.max(ctx.map.getZoom(), 17), duration: dur });
    else ctx.map.fitBounds(b, { padding: 64, maxZoom: 17.5, duration: dur, bearing: ctx.map.getBearing(), pitch: ctx.map.getPitch() });
  }

  // 주소창의 focus를 목록 위에 다시 연다 (주소창은 그대로)
  function reopenFocus() {
    const f = state.get("focus");
    if (!f) return;
    const place = ctx.data.byPath.get(f);
    if (place) {
      const g = state.get("unit") === "group" && typeof place.i === "number" ? ctx.data.groupOf(place.i) : null;
      bus.emit("select", { path: f, group: g ? g.g : undefined, from: "link", fit: false, noPush: true });
    } else if (/^[a-z-]+\//.test(f)) {
      ctx.source.card(f).then(card => { if (state.get("focus") === f) bus.emit("select", { path: f, card, from: "link", fit: false, noPush: true }); }).catch(() => {});
    }
  }

  // 다른 모듈(칩, 조건 찾기)이 쓰는 결과 목록
  ctx.results = {
    show: opt => show(opt),
    clear: () => closeResults({ keepState: true }),
    close: closeResults,
    get current() { return R; },
    index: () => index,
    ensureBusinesses,
    markProgrammatic,
    runQuery,
    reopenFocus,
  };

  // 뒤로 가기, 앞으로 가기: q가 바뀌면 다시 찾거나 결과를 닫는다 (고르기는 하지 않는다)
  state.onChange((changed, s, source) => {
    if (source !== "pop" || !changed.includes("q")) return;
    if (s.q) runQuery(s.q, { auto: false, restore: true });
    else if (R && R.kind === "query") closeResults({ keepState: true });
  });

  // '/'로 찾기창에 오면 후보를 보여 준다. 첫 화면에 q가 있으면 고르지 않고 목록만 연다 (focus가 있으면 그 장소가 위에 열린다)
  const s0 = state.get();
  if (s0.q) runQuery(s0.q, { auto: false, restore: true });

  // ---- 상호 점: 배율 17 이상에서만 작은 점으로 그린다 (0-1절). 건물 글자가 먼저이고 누르면 그 자리 건물이 골라진다
  if (ctx.map) {
    const m = ctx.map;
    let added = false;
    const addBizLayer = () => {
      if (added || !index.biz || !m.getStyle()) return;
      added = true;
      const fc = { type: "FeatureCollection", features: index.biz.items.filter(b => b.pt && !b.src).map(b => ({ type: "Feature", geometry: { type: "Point", coordinates: b.pt }, properties: {} })) };
      if (!m.getSource("biz")) m.addSource("biz", { type: "geojson", data: fc });
      if (!m.getLayer("biz-pt")) {
        m.addLayer({ id: "biz-pt", type: "circle", source: "biz", minzoom: 17,
          paint: { "circle-radius": 2.6, "circle-color": css("--map-text-2"), "circle-opacity": 0.75, "circle-stroke-color": css("--map-halo"), "circle-stroke-width": 1 } },
        m.getLayer("bldg-no") ? "bldg-no" : undefined);
      }
    };
    ctx.mapApi.onTheme(() => { if (m.getLayer("biz-pt")) { m.setPaintProperty("biz-pt", "circle-color", css("--map-text-2")); m.setPaintProperty("biz-pt", "circle-stroke-color", css("--map-halo")); } });
    const check = () => { if (m.getZoom() >= 16.5) ensureBusinesses().then(addBizLayer).catch(() => {}); };
    m.on("zoomend", check);
    bus.on("businesses:ready", () => { if (m.getZoom() >= 16.5) addBizLayer(); });
    ctx.mapApi.whenReady(check);
  }
}

function floorText(f) {
  const s = String(f || "").trim();
  if (!s) return "";
  if (/^-?\d+$/.test(s)) return Number(s) < 0 ? `지하 ${-Number(s)}층` : `${s}층`;
  if (/^B\d+$/i.test(s)) return `지하 ${s.slice(1)}층`;
  return /층$/.test(s) ? s : `${s}층`;
}
