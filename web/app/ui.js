// 공용 화면 도구: 알림, 복사, 화면 읽기 알림, 다시 시도 띠, 뼈대 표시, 아이콘, 지도 도구 단추 (명세 5.2절 ui)
// 아이콘은 24px 격자, 선 2px로 직접 그린 것이다

const P = {
  menu: '<path d="M4 6h16M4 12h16M4 18h16"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  minus: '<path d="M5 12h14"/>',
  station: '<rect x="6" y="3" width="12" height="13" rx="3"/><path d="M6 10h12"/><circle cx="9.5" cy="13" r=".6"/><circle cx="14.5" cy="13" r=".6"/><path d="M9 16l-2 4M15 16l2 4"/>',
  locate: '<circle cx="12" cy="12" r="7"/><circle cx="12" cy="12" r="2.5"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3"/>',
  back: '<path d="M15 18l-6-6 6-6"/>',
  close: '<path d="M6 6l12 12M18 6L6 18"/>',
  up: '<path d="M6 15l6-6 6 6"/>',
  down: '<path d="M6 9l6 6 6-6"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="M20 20l-4-4"/>',
  copy: '<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V6a2 2 0 0 1 2-2h9"/>',
  link: '<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1"/><path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/>',
  compass: '<circle cx="12" cy="12" r="9"/><path d="M12 5l3 7h-6l3-7z" fill="currentColor"/><path d="M12 19l-3-7h6l-3 7z"/>',
  layers: '<path d="M12 4l8 4-8 4-8-4 8-4z"/><path d="M4 12l8 4 8-4M4 16l8 4 8-4"/>',
  ruler: '<rect x="3" y="8" width="18" height="8" rx="1.5"/><path d="M7 8v3M11 8v4M15 8v3M19 8v4"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7.5v.5"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M2 12h2M20 12h2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
  moon: '<path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z"/>',
  system: '<rect x="3" y="4" width="18" height="12" rx="2"/><path d="M8 20h8M12 16v4"/>',
  refresh: '<path d="M20 11a8 8 0 1 0-2.3 5.7"/><path d="M20 5v6h-6"/>',
  orbit: '<path d="M4.5 10A8 8 0 0 1 18 6.3"/><path d="M18 2.5v4h-4"/><path d="M19.5 14A8 8 0 0 1 6 17.7"/><path d="M6 21.5v-4h4"/><path d="M12 9l3 1.6v3L12 15.2l-3-1.6v-3z"/>',
  external: '<path d="M14 4h6v6M20 4l-9 9"/><path d="M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/>',
  pin: '<path d="M12 21s-6-5.4-6-10a6 6 0 0 1 12 0c0 4.6-6 10-6 10z"/><circle cx="12" cy="11" r="2"/>',
};

export const icon = (name, size = 20) =>
  `<svg class="ic" width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">${P[name] || P.info}</svg>`;

// 도구 줄 묶음 순서. 위 줄: 지도 보기, 재기, 링크. 아래 줄: 입체 보기, 확대와 축소, 역과 내 위치
const TOP = ["view", "measure", "link", "other"];
const BOTTOM = ["d3", "zoom", "place"];

export function createUI() {
  const toastEl = document.getElementById("toast");
  const liveEl = document.getElementById("live");
  const tools = document.getElementById("map-tools");
  tools.innerHTML = '<div class="tools-top"></div><div class="tools-bottom"></div>';
  let toastTimer = 0;

  function toast(text, { ms = 3200 } = {}) {
    clearTimeout(toastTimer);
    toastEl.innerHTML = `<div class="toast-box">${esc(text)}</div>`;
    toastEl.classList.add("on");
    toastTimer = setTimeout(() => { toastEl.classList.remove("on"); toastEl.innerHTML = ""; }, ms);
  }

  function live(text) {
    liveEl.textContent = "";
    setTimeout(() => { liveEl.textContent = text; }, 30);
  }

  // 복사가 막히면 값을 선택된 입력칸으로 보여 준다 (명세 3.11절)
  function copyFallback(text) {
    clearTimeout(toastTimer);
    toastEl.innerHTML = `<div class="toast-box copy-fallback" role="group" aria-label="복사할 값">
      <label class="small" for="copy-fallback-input">자동 복사를 쓸 수 없습니다. 선택된 값을 직접 복사해 주세요.</label>
      <div class="row"><input id="copy-fallback-input" class="field" readonly value="${esc(text)}">
      <button type="button" class="btn ghost mini" data-close>닫기</button></div></div>`;
    toastEl.classList.add("on");
    const input = toastEl.querySelector("input");
    const opener = document.activeElement;
    const close = () => { toastEl.classList.remove("on"); toastEl.innerHTML = ""; if (opener && opener.focus) opener.focus(); };
    toastEl.querySelector("[data-close]").onclick = close;
    input.addEventListener("keydown", e => { if (e.key === "Escape") { e.stopPropagation(); close(); } });
    input.focus();
    input.select();
  }

  async function copy(text, doneText = "복사했습니다") {
    try {
      if (!navigator.clipboard || !window.isSecureContext) throw new Error("no clipboard");
      await navigator.clipboard.writeText(text);
      toast(doneText);
      live(doneText);
      return true;
    } catch (e) {
      copyFallback(text);
      return false;
    }
  }

  // 지도 위 다시 시도 띠. 누르면 띠를 닫고 fn을 부른다. 돌려준 close()로 닫을 수 있다
  function retryBar(text, fn) {
    const bar = document.createElement("div");
    bar.className = "retry-bar";
    bar.setAttribute("role", "alert");
    bar.innerHTML = `<span>${esc(text)}</span><button type="button" class="btn mini">${icon("refresh", 16)}다시 시도</button>`;
    document.body.appendChild(bar);
    const close = () => bar.remove();
    bar.querySelector("button").onclick = () => { close(); fn(); };
    return { close, el: bar };
  }

  const skeleton = (n = 3) => `<div class="skel" aria-busy="true"><span class="sr-only">불러오는 중입니다</span>${
    Array.from({ length: n }, (_, k) => `<div class="skel-row" style="width:${92 - (k % 3) * 14}%"></div>`).join("")}</div>`;

  // 오른쪽 도구 줄에 단추를 더한다. group: view, measure, link, other(위 줄), zoom, place(아래 줄)
  function toolButton({ id, icon: ic = "info", label, group = "other", onClick, order = 0 }) {
    const row = tools.querySelector(BOTTOM.includes(group) ? ".tools-bottom" : ".tools-top");
    const b = document.createElement("button");
    b.type = "button";
    b.className = "tool";
    if (id) b.id = id;
    b.dataset.group = group;
    b.dataset.order = String((BOTTOM.includes(group) ? BOTTOM : TOP).indexOf(group) * 100 + order);
    b.setAttribute("aria-label", label);
    b.title = label;
    b.innerHTML = icon(ic);
    if (onClick) b.addEventListener("click", onClick);
    const after = [...row.children].find(x => Number(x.dataset.order) > Number(b.dataset.order));
    row.insertBefore(b, after || null);
    return b;
  }

  return { toast, copy, live, retryBar, skeleton, esc, icon, toolButton };
}
