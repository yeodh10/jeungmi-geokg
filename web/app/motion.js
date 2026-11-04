// 지도 움직임 (키보드와 끌기의 부드러움)
// 키: W A S D와 방향키는 옮기기, Q와 E는 왼쪽 오른쪽으로 돌리기(입체 보기에서만), +와 -는 확대와 축소.
// 누르고 있는 동안 천천히 빨라졌다가 같은 빠르기로 가고, 떼면 미끄러지듯 멎는다. 두 키를 같이 누르면 대각선으로 간다.
// 움직임은 짧은 easeTo를 이어 붙인다. 프레임마다 jumpTo를 부르면 moveend가 프레임마다 나서 주소창 적기와 자료 받기가 몰리기 때문이다.
// 글을 쓰는 칸에서는 키를 받지 않고, 방향키는 지도에 초점이 있을 때만 받는다(탭과 목록의 방향키 이동을 막지 않게).

const PAN = 560;      // 옮기기 빠르기, 화면 px/초
const TURN = 75;      // 돌리기 빠르기, 도/초
const ZOOM = 1.4;     // 확대 빠르기, 단계/초
const SEG = 600;      // 같은 빠르기로 가는 한 토막(ms)
const RAMP = 420;     // 멈춘 데서 빨라지는 토막(ms)
const STOP = 420;     // 떼고 멎는 토막(ms)
const KEYS = {
  KeyW: [0, -1, 0, 0], ArrowUp: [0, -1, 0, 0], KeyS: [0, 1, 0, 0], ArrowDown: [0, 1, 0, 0],
  KeyA: [-1, 0, 0, 0], ArrowLeft: [-1, 0, 0, 0], KeyD: [1, 0, 0, 0], ArrowRight: [1, 0, 0, 0],
  KeyQ: [0, 0, -1, 0], KeyE: [0, 0, 1, 0],
  Equal: [0, 0, 0, 1], NumpadAdd: [0, 0, 0, 1], Minus: [0, 0, 0, -1], NumpadSubtract: [0, 0, 0, -1],
};
const ARROWS = new Set(["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight"]);
const linear = t => t;
const easeIn = t => t * t;                     // 0에서 v까지 빨라짐: 거리 v*T/2
const easeOut = t => 1 - (1 - t) * (1 - t);    // v에서 0까지 멎음: 거리 v*T/2
const reduced = () => matchMedia("(prefers-reduced-motion: reduce)").matches;

export function init(ctx) {
  const { map, bus, ui } = ctx;
  map.keyboard.disable();   // 지도 라이브러리의 키 이동은 한 번에 툭툭 움직여서 이것으로 바꾼다
  // 끌고 놓았을 때 조금 더 길고 부드럽게 미끄러지게 (기본: linearity 0.3, deceleration 2500)
  map.dragPan.enable({ linearity: 0.25, easing: t => 1 - Math.pow(1 - t, 3), deceleration: 1900, maxSpeed: 1600 });

  const held = new Set();
  let cur = null;        // 지금 토막이 끝날 때의 빠르기 {x, y, r, z}. null이면 서 있다
  let stopping = false;  // 지금 토막이 멎는 토막인가
  let hinted = false;

  const zero = v => !v || (!v.x && !v.y && !v.r && !v.z);
  const same = (a, b) => a && b && ["x", "y", "r", "z"].every(k => Math.abs(a[k] - b[k]) < 1e-6);
  function want() {
    let x = 0, y = 0, r = 0, z = 0;
    for (const c of held) { const k = KEYS[c]; x += k[0]; y += k[1]; r += k[2]; z += k[3]; }
    const n = Math.hypot(x, y) || 1;
    if (!map.dragRotate.isEnabled()) r = 0;   // 돌리기는 입체 보기에서만 열린다 (themes.js)
    return { x: x / n * PAN, y: y / n * PAN, r: Math.sign(r) * TURN, z: Math.sign(z) * ZOOM };
  }

  // 빠르기 v로 ms 동안, 거리 배율 f(같은 빠르기 1, 빨라짐과 멎음 0.5)만큼 간다
  function step(v, ms, easing, f) {
    const s = ms / 1000 * f;
    const c = map.project(map.getCenter());
    const center = map.unproject([c.x + v.x * s, c.y + v.y * s]);
    const zoom = Math.min(map.getMaxZoom(), Math.max(map.getMinZoom(), map.getZoom() + v.z * s));
    map.easeTo({ center, zoom, bearing: map.getBearing() + v.r * s, duration: reduced() ? 0 : ms, easing, essential: true }, { drive: true });
  }

  function update() {
    const v = want();
    if (zero(v)) {
      if (cur && !stopping) { stopping = true; step(cur, STOP, easeOut, 0.5); }
      return;
    }
    if (same(v, cur) && !stopping) return;   // 같은 빠르기로 이미 가는 중
    const fromRest = !cur || stopping;
    stopping = false;
    if (fromRest) bus.emit("motion:start");
    if (fromRest) step(v, RAMP, easeIn, 0.5); else step(v, SEG, linear, 1);
    cur = v;
  }

  // 토막이 끝나면 키가 아직 눌려 있는지 보고 다음 토막을 잇는다.
  // 다른 움직임(찾은 곳으로 옮기기 등)이 이 토막을 끊을 때도 moveend가 그 easeTo 안에서 바로 나므로, 한 프레임 미루고
  // 그때 다른 움직임이 돌고 있으면 키 움직임을 그만둔다
  map.on("moveend", e => {
    if (!e.drive) return;
    requestAnimationFrame(() => {
      // 다른 움직임이 돌고 있거나, 멎는 토막이 끝났거나, 그 사이 지도를 눌러 키 움직임을 놓았으면(cur가 없다) 여기서 끝낸다
      if (map.isMoving() || stopping || !cur) { cur = null; stopping = false; return; }
      const v = want();
      if (zero(v)) { stopping = true; step(cur, STOP, easeOut, 0.5); return; }
      step(v, SEG, linear, 1);
      cur = v;
    });
  });

  const editable = el => el && (el.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName));
  const onMap = el => !el || el === document.body || el === document.documentElement || map.getContainer().contains(el);
  document.addEventListener("keydown", e => {
    const k = KEYS[e.code];
    if (!k || e.ctrlKey || e.metaKey || e.altKey || e.defaultPrevented || editable(e.target)) return;
    if (ARROWS.has(e.code) && !onMap(e.target)) return;
    if (k[2] && !map.dragRotate.isEnabled()) {
      if (!hinted) { hinted = true; ui.live("Q와 E로 돌리기는 입체 보기에서 됩니다. 아래 3D 버튼을 눌러 주세요"); }
      return;
    }
    e.preventDefault();
    if (e.repeat || held.has(e.code)) return;
    held.add(e.code);
    update();
  });
  document.addEventListener("keyup", e => { if (held.delete(e.code)) update(); });
  window.addEventListener("blur", () => { if (held.size) { held.clear(); update(); } });
  // 끌거나 휠을 쓰면 키 움직임은 그 자리에서 놓는다
  for (const t of ["pointerdown", "wheel"]) map.getContainer().addEventListener(t, () => { held.clear(); cur = null; stopping = false; }, { capture: true, passive: true });
}
