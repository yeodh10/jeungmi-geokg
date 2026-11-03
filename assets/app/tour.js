// 5분 둘러보기 (명세 3.10절): 작은 안내 카드가 실제 화면을 조작한다. 막는 창은 띄우지 않고 언제든 닫을 수 있다.
// 둘러보기 동안에는 주소창 기록을 쌓지 않는다. 닫으면 주소창, 지도 위치, 탭, 고른 장소를 시작 전 값으로 돌린다.

const SITE = "https://yeodh10.github.io/jeungmi-geokg";
const nf = n => Number(n).toLocaleString("ko-KR");
const sleep = ms => new Promise(r => setTimeout(r, ms));

export function init(ctx) {
  ctx.css("graph.css");
  const { bus, ui, panel, data, state, cfg } = ctx;
  const map = ctx.map;
  const st = data.station;
  const pageBase = cfg.mode === "public" ? cfg.root : SITE;
  const reduced = () => matchMedia("(prefers-reduced-motion: reduce)").matches;
  let run = null;   // {k, snap, el, opener, token}

  async function waitFor(fn, ms = 6000) {
    const t0 = performance.now();
    while (performance.now() - t0 < ms) { const v = fn(); if (v) return v; await sleep(80); }
    return null;
  }
  function mark(el) {
    document.querySelectorAll(".tour-mark").forEach(x => x.classList.remove("tour-mark"));
    if (el) { el.classList.add("tour-mark"); el.scrollIntoView({ block: "center", behavior: reduced() ? "auto" : "smooth" }); }
  }
  const cardEl = sel => panel.el.querySelector(`.view-place ${sel}`);
  const halfSheet = () => { if (ctx.sheet && ctx.sheet.active()) ctx.sheet.snap("half"); };
  const openStation = async () => {
    const h = panel.el.querySelector(".view-place .cd-name");
    if (!h || h.textContent.trim() !== st.n || !cardEl(".cd-foot")) bus.emit("select", { path: st.p, from: "tour", fit: true, noPush: true });
    halfSheet();
    return waitFor(() => cardEl(".cd-foot"));
  };

  const STEPS = [
    { title: "지도 전체", text: () => `증미역 반경 1km 안 건물 ${nf(data.count.buildings)}동이 용도별 색으로 깔려 있습니다. 확대할수록 이름 글자가 늘고, 원 밖은 어둡게 덮었습니다.`,
      async go() {
        bus.emit("deselect");
        panel.selectTab("search");
        if (map) {
          const r = 1000, c = data.center, dLat = r / 110574, dLng = r / (111320 * Math.cos(c[1] * Math.PI / 180));
          map.fitBounds([[c[0] - dLng, c[1] - dLat], [c[0] + dLng, c[1] + dLat]], { padding: 16, duration: reduced() ? 0 : 600 });
        }
        if (ctx.sheet && ctx.sheet.active()) ctx.sheet.snap("peek");
      } },
    { title: "증미역 카드", text: () => "장소마다 고유 주소와 공식 번호가 있습니다. 카드 맨 아래 보라색 칸이 고유 주소이고, 증미역은 위키데이터 Q59989와 이어져 있습니다.",
      async go(t) {
        panel.selectTab("search");
        const foot = await openStation();
        if (!alive(t)) return;
        mark(foot);
      } },
    { title: "이어진 것", text: () => "이어진 것에서 행정동 등촌제1동을 눌렀습니다. 같은 행정동에 속한 건물이 보라색으로 번집니다. 경계선은 공개 자료가 없어 그리지 않습니다.",
      async go(t) {
        panel.selectTab("search");
        await openStation();
        if (!alive(t)) return;
        const b = [...panel.el.querySelectorAll(".view-place .g-links li")].find(li => /행정동/.test(li.querySelector(".g-rel")?.textContent || ""));
        const sp = b && b.querySelector(".g-spread");
        if (sp) { sp.click(); mark(b); }
      } },
    { title: "출처와 자료 탭", text: () => {
        const src = run && run.src;
        const s = src ? ` 증미역 카드에는 이 장소의 출처(${src.object.join(", ") || "없음"})와 이 모양의 출처(${src.shape.join(", ") || "없음"})를 나눠 적었습니다.` : "";
        return `자료 탭에는 쓴 출처와 공개하지 않은 자료, 그 까닭이 있습니다.${s}`;
      },
      async go(t) {
        bus.emit("spread:clear");
        panel.selectTab("data");
        halfSheet();
        const held = await waitFor(() => panel.el.querySelector('#pane-data [data-tour="held"]'));
        if (!alive(t)) return;
        mark(held);
      } },
    { title: "묻기", text: () => "묻기 탭의 '증미역 500m 안 교육연구시설'입니다. 답 문장, 지도의 보라색 건물, 표, 실행한 쿼리가 함께 나옵니다. 미리 실행한 답입니다.",
      async go(t) {
        bus.emit("ask:open", { id: "edu-500", push: false, focus: false });
        const d = await waitFor(() => panel.el.querySelector("#pane-ask .ask-sparql"));
        if (!alive(t) || !d) return;
        d.open = true;
        mark(panel.el.querySelector("#pane-ask .ask-answer"));
      } },
    { title: "기계용 원본", text: () => "장소 페이지 주소 끝에 index.ttl을 붙이면 같은 장소를 기계가 읽는 Turtle 원본으로 받습니다. 아래는 증미역 원본의 앞부분입니다.",
      async go(t) {
        bus.emit("spread:clear");
        panel.selectTab("search");
        await openStation();
        if (!alive(t)) return;
        const act = cardEl('.cd-act[data-act="ttl"]');
        if (act) mark(act);
        const url = `${pageBase}/id/${st.p}/index.ttl`;
        const box = run.el.querySelector(".tour-extra");
        box.innerHTML = `<pre>불러오는 중입니다.</pre><p><a href="${esc(url)}" target="_blank" rel="noopener">새 탭에서 원본 열기 ${ui.icon("external", 12)}</a></p>`;
        try {
          const r = await fetch(url);
          const txt = r.ok ? await r.text() : "";
          if (!alive(t)) return;
          box.querySelector("pre").textContent = txt ? txt.split("\n").filter(l => l.trim() && !/^@prefix/.test(l)).slice(0, 10).join("\n") : "원본을 받지 못했습니다. 위 링크로 열어 보세요.";
        } catch (e) { if (alive(t)) box.querySelector("pre").textContent = "원본을 받지 못했습니다. 위 링크로 열어 보세요."; }
      } },
  ];
  const alive = t => run && run.token === t;

  function guide() {
    const el = document.createElement("section");
    el.className = "tour";
    el.setAttribute("role", "dialog");
    el.setAttribute("aria-modal", "false");
    el.setAttribute("aria-labelledby", "tour-title");
    el.tabIndex = -1;
    el.innerHTML = `<div class="tour-top"><span class="tour-step"></span>
      <button type="button" class="tour-close" aria-label="둘러보기 닫기">${ui.icon("close", 18)}</button></div>
      <h2 id="tour-title" tabindex="-1"></h2><p class="tour-text"></p><div class="tour-extra"></div>
      <div class="tour-dots" aria-hidden="true">${STEPS.map(() => "<span></span>").join("")}</div>
      <div class="tour-nav"><button type="button" class="btn ghost tour-prev">이전</button><button type="button" class="btn tour-next">다음</button></div>`;
    el.querySelector(".tour-close").addEventListener("click", () => stop());
    el.querySelector(".tour-prev").addEventListener("click", () => step(run.k - 1));
    el.querySelector(".tour-next").addEventListener("click", () => (run.k >= STEPS.length - 1 ? stop() : step(run.k + 1)));
    el.addEventListener("keydown", e => { if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); stop(); } });
    document.body.appendChild(el);
    return el;
  }

  async function step(k) {
    if (!run || k < 0 || k >= STEPS.length) return;
    run.k = k;
    const t = Symbol("step");
    run.token = t;
    const s = STEPS[k], el = run.el;
    el.querySelector(".tour-step").textContent = `${k + 1} / ${STEPS.length}`;
    el.querySelector("#tour-title").textContent = s.title;
    el.querySelector(".tour-text").textContent = s.text();
    el.querySelector(".tour-extra").innerHTML = "";
    el.querySelectorAll(".tour-dots span").forEach((d, j) => d.classList.toggle("on", j <= k));
    el.querySelector(".tour-prev").disabled = k === 0;
    el.querySelector(".tour-next").textContent = k === STEPS.length - 1 ? "마치기" : "다음";
    mark(null);
    ui.live(`둘러보기 ${k + 1}단계, ${s.title}`);
    try { await s.go(t); } catch (e) { console.error(e); }
    if (!alive(t)) return;
    // 문장에 받은 값(출처)이 들어가는 단계가 있어 한 번 더 쓴다
    el.querySelector(".tour-text").textContent = s.text();
  }

  // 증미역 카드의 출처를 미리 받아 둔다 (4단계 문장)
  async function prefetch() {
    try { const c = await ctx.source.card(st.p); if (run) run.src = c.sources; } catch (e) { /* 문장만 짧아진다 */ }
  }

  function start({ opener } = {}) {
    if (run) { run.el.focus(); return; }
    const snap = {
      url: state.get(),
      view: map ? { center: map.getCenter(), zoom: map.getZoom() } : null,
      tab: panel.tab(),
      collapsed: panel.isCollapsed(),
      sheet: ctx.sheet && ctx.sheet.current(),
    };
    run = { k: 0, snap, opener: opener || document.activeElement, token: null, src: null };
    run.el = guide();
    document.body.classList.add("touring");
    prefetch();
    step(0);
    run.el.querySelector("#tour-title").focus({ preventScroll: true });
  }

  // 닫기: 시작 전 값으로 돌린다
  async function stop() {
    if (!run) return;
    const { snap, el, opener } = run;
    run = null;
    mark(null);
    el.remove();
    document.body.classList.remove("touring");
    bus.emit("spread:clear");
    bus.emit("popup:close");
    const s = snap.url;
    if (s.focus) {
      const path = String(s.focus).replace(/^.*\/id\//, "").replace(/^\/+|\/+$/g, "");
      const place = data.byPath.get(path);
      const g = s.unit === "group" && place && typeof place.i === "number" ? data.groupOf(place.i) : null;
      if (place) bus.emit("select", { path, group: g ? g.g : undefined, from: "link", fit: false, noPush: true });
      else bus.emit("deselect");
    } else bus.emit("deselect");
    panel.selectTab(snap.tab || "search");
    if (snap.collapsed) panel.collapse(true);
    if (ctx.sheet && ctx.sheet.active() && snap.sheet) ctx.sheet.snap(snap.sheet);
    const put = () => state.set({ focus: null, unit: null, q: null, cat: null, tab: null, theme: null, z: null, c: null, ...s }, { silent: true });
    put();
    if (map && snap.view) {
      map.stop();
      map.jumpTo({ center: snap.view.center, zoom: snap.view.zoom });
      // 지도가 멎은 뒤 주소창 위치 값이 덮어써지므로 그 뒤에 한 번 더 시작 전 값으로 맞춘다
      setTimeout(put, 450);
    }
    ui.live("둘러보기를 닫았습니다. 시작 전 화면으로 돌아왔습니다");
    // 연 곳으로 포커스를 돌린다. 연 곳이 사라졌으면 패널로 (닫는 동안 다른 화면이 포커스를 가져가지 않게 한 번 더)
    const back = () => { const t = opener && document.contains(opener) && opener.getClientRects().length ? opener : panel.el; t.focus({ preventScroll: true }); };
    back();
    setTimeout(() => { if (!document.activeElement || document.activeElement === document.body) back(); }, 50);
  }

  bus.on("tour:start", start);
  document.addEventListener("keydown", e => { if (e.key === "Escape" && run && !e.defaultPrevented && !document.querySelector(".copy-fallback")) stop(); });
  ctx.tour = { start, stop, get step() { return run ? run.k : -1; } };
}
