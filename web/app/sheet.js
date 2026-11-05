// 휴대폰(900px 이하)의 아래 시트 (명세 3.3절, 3.12절). 패널 요소를 그대로 시트로 쓴다.
// 세 단계: 접힘 124px(가로로 눕힌 화면 72px), 절반 50dvh, 전체 100dvh-72px.
// 손잡이는 button이고 위아래 방향키로 단계를 바꾼다. 접힘과 절반에서는 뒤의 지도를 계속 움직일 수 있다.

const ORDER = ["peek", "half", "full"];
const LABEL = { peek: "접힘", half: "절반", full: "전체" };

export function createSheet(ctx, panelEl) {
  const mobile = matchMedia("(max-width: 900px)");
  const land = matchMedia("(max-height: 500px)");
  const root = document.documentElement;
  const top = panelEl.querySelector(".sheet-top");
  top.innerHTML = `<button type="button" class="sheet-grip" aria-controls="panel" aria-expanded="false" aria-label="시트 높이 바꾸기, 현재 접힘. 위아래 방향키로 바꿀 수 있습니다"><span class="grip-bar" aria-hidden="true"></span></button>`;
  const grip = top.querySelector(".sheet-grip");
  const fns = [];
  let name = "peek";
  let dragging = null;

  const vh = () => Math.max(window.innerHeight, 1);   // 100dvh와 같은 값 (키보드가 올라와도 시트 높이는 그대로)
  const heights = () => {
    const h = vh();
    const peek = land.matches ? 72 : 124;
    return { peek, half: Math.max(peek, Math.round(h * 0.5)), full: Math.max(peek, h - 72) };
  };

  // 시트가 올라오면 아래 도구 묶음(3D, 역, 내 위치)이 위 도구 묶음과 겹칠 수 있다.
  // 세로로 들어갈 틈이 없으면 위 도구 줄 왼쪽에 가로 한 줄로 눕히고, 그래도 모자라면 감춘다.
  function fitTools(px) {
    const b = document.querySelector(".tools-bottom"), t = document.querySelector(".tools-top");
    if (!b) return;
    b.classList.remove("tools-row", "tools-none");
    document.body.classList.remove("tools-row");
    const shown = el => [...el.children].filter(c => !c.hidden).length;
    if (!mobile.matches || !t || !shown(t) || land.matches) return;
    const n = shown(b);
    if (!n) return;
    const r = t.getBoundingClientRect();
    const bottom = vh() - px - 36;                 // 아래 도구 묶음의 아래 끝 (app.css .tools-bottom)
    if (bottom - (r.bottom + 8) >= n * 44 + (n - 1) * 8) return;
    b.classList.add(bottom - 44 >= r.top ? "tools-row" : "tools-none");
    document.body.classList.toggle("tools-row", b.classList.contains("tools-row"));   // 왼쪽 아래에 뜨는 것들이 줄을 피하게
  }

  function setPx(px, animate) {
    panelEl.classList.toggle("sheet-dragging", !animate);
    panelEl.style.height = px + "px";
    root.style.setProperty("--sheet-h", px + "px");
    fitTools(px);
  }

  function apply(notify = true) {
    if (!mobile.matches) {
      panelEl.style.height = "";
      root.style.setProperty("--sheet-h", "0px");
      document.body.classList.remove("sheet-full", "sheet-peek", "sheet-half");
      grip.setAttribute("aria-expanded", "true");
      if (notify) fns.forEach(fn => fn(null, 0));
      return;
    }
    const px = heights()[name];
    setPx(px, true);
    document.body.classList.toggle("sheet-full", name === "full");
    document.body.classList.toggle("sheet-half", name === "half");
    document.body.classList.toggle("sheet-peek", name === "peek");
    grip.setAttribute("aria-expanded", String(name !== "peek"));
    grip.setAttribute("aria-label", `시트 높이 바꾸기, 현재 ${LABEL[name]}. 위아래 방향키로 바꿀 수 있습니다`);
    if (notify) fns.forEach(fn => { try { fn(name, px); } catch (e) { console.error(e); } });
  }

  function snap(next) {
    if (!ORDER.includes(next)) return;
    const changed = next !== name;
    name = next;
    apply(changed || !panelEl.style.height);
  }

  grip.addEventListener("keydown", e => {
    const k = ORDER.indexOf(name);
    if (e.key === "ArrowUp") { e.preventDefault(); snap(ORDER[Math.min(k + 1, 2)]); ctx.ui.live(`시트 ${LABEL[name]}`); }
    else if (e.key === "ArrowDown") { e.preventDefault(); snap(ORDER[Math.max(k - 1, 0)]); ctx.ui.live(`시트 ${LABEL[name]}`); }
    else if (e.key === "Escape" && name !== "peek") { e.preventDefault(); snap("peek"); }
  });
  grip.addEventListener("click", e => {
    if (grip.dataset.dragged) { delete grip.dataset.dragged; return; }
    snap(name === "peek" ? "half" : name === "half" ? "full" : "half");
    if (e.detail === 0) ctx.ui.live(`시트 ${LABEL[name]}`);
  });

  // 끌기: 손잡이와 탭 줄을 잡고 끈다. 놓으면 가까운 단계로 (빠르게 튕기면 그 방향으로 한 단계)
  const dragFrom = e => e.target.closest(".sheet-top, .panel-tabs, .view-head");
  panelEl.addEventListener("pointerdown", e => {
    if (!mobile.matches || e.button !== 0 || !dragFrom(e)) return;
    dragging = { y: e.clientY, h: panelEl.getBoundingClientRect().height, t: performance.now(), moved: false, id: e.pointerId, target: e.target };
  });
  panelEl.addEventListener("pointermove", e => {
    if (!dragging || e.pointerId !== dragging.id) return;
    const dy = e.clientY - dragging.y;
    if (!dragging.moved && Math.abs(dy) < 6) return;
    if (!dragging.moved) { dragging.moved = true; try { panelEl.setPointerCapture(e.pointerId); } catch (_) { /* 이미 놓음 */ } }
    const H = heights();
    setPx(Math.min(H.full, Math.max(H.peek, dragging.h - dy)), false);
    e.preventDefault();
  });
  const end = e => {
    if (!dragging || e.pointerId !== dragging.id) return;
    const d = dragging;
    dragging = null;
    if (!d.moved) return;
    const H = heights();
    const h = panelEl.getBoundingClientRect().height;
    const v = (d.h - h) / Math.max(1, performance.now() - d.t);   // 위로 끌면 양수 (px/ms)
    let next;
    if (Math.abs(v) > 0.6) next = ORDER[Math.max(0, Math.min(2, ORDER.indexOf(name) + (v > 0 ? 1 : -1)))];
    else next = ORDER.reduce((a, b) => (Math.abs(H[b] - h) < Math.abs(H[a] - h) ? b : a), "peek");
    if (d.target.closest(".sheet-grip")) grip.dataset.dragged = "1";
    name = next;
    apply(true);
  };
  panelEl.addEventListener("pointerup", end);
  panelEl.addEventListener("pointercancel", end);

  // 입체 보기를 켜면 위 도구가 늘어나므로 도구가 바뀔 때도 다시 맞춘다
  const toolsEl = document.querySelector(".tools-top") && document.querySelector(".tools-top").parentElement;
  if (toolsEl) new MutationObserver(() => fitTools(mobile.matches ? heights()[name] : 0))
    .observe(toolsEl, { subtree: true, childList: true, attributes: true, attributeFilter: ["hidden"] });

  const relayout = () => apply(true);
  mobile.addEventListener("change", relayout);
  land.addEventListener("change", relayout);
  let rt = 0;
  addEventListener("resize", () => { clearTimeout(rt); rt = setTimeout(() => apply(true), 100); });
  apply(false);

  return {
    snap,
    current: () => (mobile.matches ? name : null),
    active: () => mobile.matches,
    height: () => (mobile.matches ? heights()[name] : 0),
    heights,
    onChange(fn) { fns.push(fn); },
  };
}
