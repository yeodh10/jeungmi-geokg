// 떠 있는 패널 (PC)과 같은 요소를 쓰는 아래 시트 (휴대폰)의 내용 (명세 3.2절, 5.2절 panel)
// 탭: 찾기(이 파일이 만든다), 묻기와 자료(다른 묶음이 addTab으로 더한다). 탭이 하나뿐이면 탭 줄을 숨긴다.
// 찾기 탭 안은 화면 쌓기: 홈 → 결과 → 장소. show(view)로 쌓고 back()으로 하나 내린다.

export function createPanel(ctx) {
  const { ui, state } = ctx;
  const el = document.getElementById("panel");
  el.innerHTML = `<div class="sheet-top"></div>
    <div class="panel-tabs" role="tablist" aria-label="패널 탭" hidden></div>
    <div class="panel-body"></div>`;
  const tabBar = el.querySelector(".panel-tabs");
  const body = el.querySelector(".panel-body");
  const tabs = new Map();
  let current = "search";
  let placeRenderer = null;
  let homeRender = null;
  let collapsed = false;
  const collapseFns = [];

  // ---- 탭
  function addTab({ id, label, render, order = 50 }) {
    if (tabs.has(id)) return tabs.get(id).pane;
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "panel-tab";
    btn.id = `tab-${id}`;
    btn.setAttribute("role", "tab");
    btn.setAttribute("aria-controls", `pane-${id}`);
    btn.textContent = label;
    btn.dataset.order = String(order);
    const pane = document.createElement("section");
    pane.className = "tab-pane";
    pane.id = `pane-${id}`;
    pane.setAttribute("role", "tabpanel");
    pane.setAttribute("aria-labelledby", btn.id);
    pane.hidden = true;
    const t = { id, label, render, order, btn, pane, rendered: false };
    tabs.set(id, t);
    const after = [...tabBar.children].find(x => Number(x.dataset.order) > order);
    tabBar.insertBefore(btn, after || null);
    body.appendChild(pane);
    btn.addEventListener("click", () => selectTab(id, { push: true }));
    tabBar.hidden = tabs.size < 2;
    syncTabs();
    return pane;
  }
  // 왼쪽, 오른쪽 방향키로 탭을 옮긴다
  tabBar.addEventListener("keydown", e => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(e.key)) return;
    const list = [...tabBar.children];
    const k = list.indexOf(document.activeElement);
    if (k < 0) return;
    e.preventDefault();
    const n = e.key === "Home" ? 0 : e.key === "End" ? list.length - 1 : (k + (e.key === "ArrowRight" ? 1 : -1) + list.length) % list.length;
    list[n].focus();
    list[n].click();
  });

  function syncTabs() {
    for (const t of tabs.values()) {
      const on = t.id === current;
      t.btn.setAttribute("aria-selected", String(on));
      t.btn.tabIndex = on ? 0 : -1;
      t.pane.hidden = !on;
      if (on && !t.rendered && t.render) {
        t.rendered = true;
        try { t.render(t.pane, ctx); } catch (e) { console.error(e); }
      }
    }
  }

  // expand: 접힌 패널을 펴는지. 찾기 결과는 접힌 채로 볼 수 있어(명세 3.2절) 찾기 모듈이 false를 준다
  function selectTab(id, { push = false, expand = true } = {}) {
    if (!tabs.has(id)) id = "search";
    if (collapsed && expand) setCollapsed(false);
    current = id;
    syncTabs();
    body.scrollTop = 0;
    if (push) {
      // 사람이 탭을 눌렀을 때만 주소창에 쌓고, 휴대폰에서는 시트를 절반으로 올린다
      state.set({ tab: id === "search" ? null : id }, { push: true, silent: true });
      if (ctx.sheet && ctx.sheet.active() && ctx.sheet.current() === "peek") ctx.sheet.snap("half");
    }
  }

  // ---- 찾기 탭의 화면 쌓기
  const searchPane = addTab({ id: "search", label: "찾기", order: 0 });
  const stack = [];   // {id, title, el, opener, onBack}
  const home = document.createElement("div");
  home.className = "view view-home";
  searchPane.appendChild(home);
  home.innerHTML = ui.skeleton(4);

  function renderHome() {
    if (!homeRender) return;
    home.innerHTML = "";
    try { homeRender(home, ctx); } catch (e) { console.error(e); }
  }

  function syncStack() {
    home.hidden = stack.length > 0;
    stack.forEach((v, k) => { v.el.hidden = k !== stack.length - 1; });
  }

  // view: {id, title, render(el, ctx), onBack(), focus: boolean, expand: boolean}. 같은 id의 화면이 있으면 그 위를 모두 내리고 바꾼다
  function show(view) {
    selectTab("search", { expand: view.expand !== false });
    const opener = document.activeElement;
    const k = stack.findIndex(v => v.id === view.id);
    const keepOpener = k >= 0 ? stack[k].opener : opener;
    if (k >= 0) stack.splice(k).forEach(v => v.el.remove());
    const prev = stack[stack.length - 1];
    const wrap = document.createElement("div");
    wrap.className = `view view-${view.id}`;
    wrap.innerHTML = `<div class="view-head"><button type="button" class="view-back">${ui.icon("back", 18)}<span>${esc(prev ? prev.title || "뒤로" : "처음으로")}</span></button></div><div class="view-body"></div>`;
    const content = wrap.querySelector(".view-body");
    wrap.querySelector(".view-back").addEventListener("click", () => back());
    searchPane.appendChild(wrap);
    const v = { id: view.id, title: view.title || "", el: wrap, content, opener: keepOpener, onBack: view.onBack };
    stack.push(v);
    syncStack();
    body.scrollTop = 0;
    if (view.render) {
      try { view.render(content, ctx); } catch (e) { console.error(e); content.innerHTML = `<p class="muted">이 내용을 표시하지 못했습니다.</p>`; }
    }
    if (view.focus) {
      const h = content.querySelector("h2, h1, [data-focus]") || wrap.querySelector(".view-back");
      if (h) { if (!h.hasAttribute("tabindex") && !/^(BUTTON|A|INPUT)$/.test(h.tagName)) h.tabIndex = -1; h.focus({ preventScroll: true }); }
    }
    return content;
  }

  // 맨 위 화면을 하나 내린다. 화면에 onBack이 있으면 그것을 부른다 (장소 화면은 주소창도 함께 바꿔야 한다)
  function back() {
    const top = stack[stack.length - 1];
    if (!top) return false;
    if (top.onBack) { top.onBack(); return true; }
    remove(top.id);
    return true;
  }

  // id의 화면과 그 위를 내린다. 포커스는 그 화면을 연 곳으로 돌린다
  function remove(id, { restoreFocus = true } = {}) {
    const k = stack.findIndex(v => v.id === id);
    if (k < 0) return false;
    const gone = stack.splice(k);
    // 포커스가 내린 화면 안에 있었을 때만 연 곳으로 돌린다 (지도를 누르는 중이면 건드리지 않는다)
    const opener = gone[0].opener;
    const lost = !document.activeElement || document.activeElement === document.body || gone.some(v => v.el.contains(document.activeElement));
    gone.forEach(v => v.el.remove());
    syncStack();
    if (restoreFocus && lost) {
      if (opener && document.contains(opener) && opener !== document.body) opener.focus({ preventScroll: true });
      else el.focus({ preventScroll: true });
    }
    return true;
  }

  el.addEventListener("keydown", e => {
    if (e.key !== "Escape" || e.defaultPrevented) return;
    if (current !== "search" || !stack.length) return;
    if (/^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName) && e.target.value) return;
    e.preventDefault();
    back();
  });

  // ---- 접기 (PC). 접으면 찾기창만 남는다
  function setCollapsed(on) {
    on = !!on;
    if (on === collapsed) return collapsed;
    collapsed = on;
    document.body.classList.toggle("panel-collapsed", on);
    collapseFns.forEach(fn => { try { fn(on); } catch (e) { console.error(e); } });
    return collapsed;
  }

  return {
    el, body,
    addTab, selectTab,
    tab: () => current,
    show, back, remove,
    top: () => (stack.length ? stack[stack.length - 1].id : "home"),
    has: id => stack.some(v => v.id === id),
    setHome(render) { homeRender = render; if (ctx.data) renderHome(); },
    renderHome,
    hasHome: () => !!homeRender,
    hasTab: id => tabs.has(id),
    setPlaceRenderer(fn) { placeRenderer = fn; },
    get placeRenderer() { return placeRenderer; },
    collapse(on) { return setCollapsed(on === undefined ? !collapsed : on); },
    isCollapsed: () => collapsed,
    onCollapse(fn) { collapseFns.push(fn); },
  };
}
