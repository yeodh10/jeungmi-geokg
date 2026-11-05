// 지도 움직임 (키보드와 끌기의 부드러움)
// 키: W A S D와 방향키는 옮기기, Q와 E는 왼쪽 오른쪽으로 돌리기, R과 F는 더 기울이기와 덜 기울이기(입체 보기에서만),
// +와 -는 확대와 축소, Shift를 누르고 있으면 빨리 간다.
// 누르고 있는 동안 금방 빨라졌다가 같은 빠르기로 가고, 떼면 미끄러지듯 멎는다. 두 키를 같이 누르면 대각선으로 가고,
// 옮기면서 돌리기처럼 여러 키를 섞어도 된다.
// 움직임은 짧은 easeTo를 이어 붙인다. 프레임마다 jumpTo를 부르면 moveend가 프레임마다 나서 주소창 적기와 자료 받기가 몰리기 때문이다.
// 글을 쓰는 칸에서는 키를 받지 않고, 방향키는 지도에 초점이 있을 때만 받는다(탭과 목록의 방향키 이동을 막지 않게).

const PAN = 560;      // 옮기기 빠르기, 화면 px/초
const TURN = 90;      // 돌리기 빠르기, 도/초
const TILT = 40;      // 기울이기 빠르기, 도/초
const ZOOM = 1.4;     // 확대 빠르기, 단계/초
const FAST = 2.5;     // Shift를 누르면 이만큼 빨리
const SEG = 500;      // 같은 빠르기로 가는 한 토막(ms)
const RAMP = 240;     // 멈춘 데서 빨라지는 토막(ms)
const STOP = 340;     // 떼고 멎는 토막(ms)
// [옆, 위아래, 돌리기, 확대, 기울이기]
const KEYS = {
  KeyW: [0, -1, 0, 0, 0], ArrowUp: [0, -1, 0, 0, 0], KeyS: [0, 1, 0, 0, 0], ArrowDown: [0, 1, 0, 0, 0],
  KeyA: [-1, 0, 0, 0, 0], ArrowLeft: [-1, 0, 0, 0, 0], KeyD: [1, 0, 0, 0, 0], ArrowRight: [1, 0, 0, 0, 0],
  KeyQ: [0, 0, -1, 0, 0], KeyE: [0, 0, 1, 0, 0], KeyR: [0, 0, 0, 0, 1], KeyF: [0, 0, 0, 0, -1],
  Equal: [0, 0, 0, 1, 0], NumpadAdd: [0, 0, 0, 1, 0], Minus: [0, 0, 0, -1, 0], NumpadSubtract: [0, 0, 0, -1, 0],
};
const SHIFT = new Set(["ShiftLeft", "ShiftRight"]);
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
  let fast = false;
  let cur = null;        // 지금 토막이 끝날 때의 빠르기 {x, y, r, z}. null이면 서 있다
  let stopping = false;  // 지금 토막이 멎는 토막인가
  let hinted = false;
  let gen = 0;           // 토막 번호. 새 토막이 앞 토막을 끊을 때 끊긴 토막의 moveend를 가려낸다

  const AX = ["x", "y", "r", "z", "p"];
  const zero = v => !v || AX.every(k => !v[k]);
  const same = (a, b) => a && b && AX.every(k => Math.abs(a[k] - b[k]) < 1e-6);
  const threeD = () => map.dragRotate.isEnabled();   // 입체 보기에서만 열린다 (themes.js)
  function want() {
    let x = 0, y = 0, r = 0, z = 0, p = 0;
    for (const c of held) { const k = KEYS[c]; x += k[0]; y += k[1]; r += k[2]; z += k[3]; p += k[4]; }
    const n = Math.hypot(x, y) || 1;
    if (!threeD()) p = 0;   // 평면 보기는 눕히지 않는다 (돌리기는 평면에서도 된다)
    const f = fast ? FAST : 1;
    return { x: x / n * PAN * f, y: y / n * PAN * f, r: Math.sign(r) * TURN * f, z: Math.sign(z) * ZOOM * f, p: Math.sign(p) * TILT * f };
  }

  // 빠르기 v로 ms 동안, 거리 배율 f(같은 빠르기 1, 빨라짐과 멎음 0.5)만큼 간다
  function step(v, ms, easing, f) {
    const s = ms / 1000 * f;
    const c = map.project(map.getCenter());
    const center = map.unproject([c.x + v.x * s, c.y + v.y * s]);
    const zoom = Math.min(map.getMaxZoom(), Math.max(map.getMinZoom(), map.getZoom() + v.z * s));
    const pitch = Math.min(map.getMaxPitch(), Math.max(0, map.getPitch() + v.p * s));
    map.easeTo({ center, zoom, pitch, bearing: map.getBearing() + v.r * s, duration: reduced() ? 0 : ms, easing, essential: true }, { drive: ++gen });
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
  // 키를 하나 더 눌러(대각선) 토막을 바꿀 때 끊긴 앞 토막의 moveend는 번호가 달라 그냥 넘긴다
  map.on("moveend", e => {
    if (!e.drive || e.drive !== gen) return;
    const mine = gen;
    requestAnimationFrame(() => {
      if (mine !== gen) return;   // 그 사이 새 토막이 시작했다
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
    if (SHIFT.has(e.code)) { if (!fast) { fast = true; if (held.size) update(); } return; }
    const k = KEYS[e.code];
    if (!k || e.ctrlKey || e.metaKey || e.altKey || e.defaultPrevented || editable(e.target)) return;
    if (ARROWS.has(e.code) && !onMap(e.target)) return;
    if (k[4] && !threeD()) {
      if (!hinted) { hinted = true; ui.toast("R과 F로 기울이기는 입체 보기에서 됩니다. 3D 버튼을 눌러 주세요"); }
      return;
    }
    e.preventDefault();
    if (e.repeat || held.has(e.code)) return;
    held.add(e.code);
    update();
  });
  document.addEventListener("keyup", e => {
    if (SHIFT.has(e.code)) { fast = false; if (held.size) update(); return; }
    if (held.delete(e.code)) update();
  });
  window.addEventListener("blur", () => { fast = false; if (held.size) { held.clear(); update(); } });
  // 끌거나 휠을 쓰면 키 움직임은 그 자리에서 놓는다
  for (const t of ["pointerdown", "wheel"]) map.getContainer().addEventListener(t, () => { held.clear(); cur = null; stopping = false; }, { capture: true, passive: true });
}
